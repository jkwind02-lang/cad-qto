#!/usr/bin/env python3
"""Point coverage ledger: every candidate on the drawing set and every legend row gets a destination.

Contract: docs/维护/点位覆盖台账契约.md (qto-point-coverage-input/1.0). Candidates come from adapter reports
(every scoped instance with its disposition) or from a neutral layout plus project count tables (every top-level
INSERT, plus the INSERTs inside container blocks the drawing declares in expand_blocks; counted when its handle or
path id is in a count table). Sheets are found from the transformed geometry extent, not the insertion point.
The script never counts a quantity, guesses a category or drops a candidate: anything without a destination stays
visible and keeps coverage incomplete.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from build_review_scenes_from_tables import read_rows, SHEET_KEYS, SHEET_KINDS  # noqa: E402
import qto_cad_geometry as cad_geometry  # noqa: E402
from search_negative_evidence import OUTPUT_SCHEMA as SEARCH_SCHEMA, record_problems  # noqa: E402

SCHEMA = "qto-point-coverage-input/1.0"
LEDGER_SCHEMA = "qto-point-coverage-ledger/1.0"
VERSION = "qto-point-coverage-ledger/1.5.0"
DESTINATIONS = ("已计", "有据排除", "待识别", "版本冲突", "无去向")
DISPOSITION_DEST = {"adopted": "已计", "merged": "已计", "excluded": "有据排除", "not_applicable": "有据排除",
                    "duplicate_excluded": "有据排除", "unresolved": "待识别"}
LEGEND_COLUMNS = ("drawing", "legend_row", "legend_handle", "legend_text", "symbol", "symbol_kind", "matched_symbols",
                  "plan_count", "count_source", "variant_flag", "destination", "target_or_reason", "suspected_category",
                  "search_record_id")
FILLED = ("legend_text", "matched_symbols", "destination", "target_or_reason", "suspected_category", "search_record_id")
LEGEND_DESTINATIONS = ("已计", "归他专业", "不计", "待识别", "")
LEDGER_COLUMNS = ("drawing", "sheet", "sheet_title", "symbol", "symbol_kind", "layer", "parent_block", "destination",
                  "count", "category_or_basis", "suspected_category", "sample_ids")
LEGEND_KIND, DETAIL_KIND, SYSTEM_KIND, PLAN_KIND = "图例", "详图", "系统图", "平面"
# Sheet kinds whose pending candidates may be excluded as a whole (registration 20261016-1728, 1800). Detail sheets are
# not here: a detail drawing can be the only place an object is shown, so each one is confirmed in detail_copy_sheets.
AUTO_EXCLUDABLE = (LEGEND_KIND, SYSTEM_KIND)
DEFAULT_AUTO_EXCLUDE = [LEGEND_KIND]
AUTO_BASIS = {LEGEND_KIND: "图例页上的复制件（A10）", SYSTEM_KIND: "系统图页上的示意，不单独计数（auto_exclude_sheet_kinds）"}
UNSHEETED = "图页未定"
# K3 (registration 20261017-0031): the extent rules are cad_geometry.definition_extent, shared with the adapters and,
# with these labels, with the negative-evidence search (registration 20261017-0012)
GEOMETRY_BASIS, PARTIAL_BASIS, INSERT_BASIS = (cad_geometry.PLACEMENT_GEOMETRY, cad_geometry.PLACEMENT_PARTIAL,
                                               cad_geometry.PLACEMENT_INSERT)
DYNAMIC_BASIS = "插入点（动态块图形范围不参与定位）"
GUARD_BASIS = "插入点（范围不完整且落在包外，按插入点保留）"
GEOMETRY_BASES = (GEOMETRY_BASIS, PARTIAL_BASIS, cad_geometry.PLACEMENT_ESTIMATED)
COUNT_KEYS = ("drawing", "handle", "category", "sheet")
EXCLUSION_KEYS = ("drawing", "handle", "basis")
TOP_FIELDS = {"schema_version", "scope_id", "drawings", "count_tables", "handle_exclusions", "sheets", "legend_coverage",
              "version_conflicts", "excluded_classes", "auto_exclude_sheet_kinds", "detail_copy_sheets", "search_records",
              "frame_blocks", "frame_dispositions", "count_overrides"}
FRAME_DISPOSITIONS = ("封面", "目录", "空白", "说明或图例", "旧版副本", "非本专业底图", "已补进图页表", "其他")
FRAME_TODO_COLUMNS = ("todo_id", "drawing", "kind", "id", "handle", "block_name", "insert_x", "insert_y", "x1", "y1", "x2", "y2",
                      "placement_basis", "hint")
HANDLE_RE = re.compile(r'"entity_handle":\s*"([^"]*)"')
# Same path id as the adapter: PT-CAD-<top handle>/<block>/CAD-<child handle>, one /<block>/CAD-<handle> per level.
# AutoCAD block names cannot contain / or \, so the separators are unambiguous.
PATH_RE = re.compile(r"PT-CAD-([0-9A-Fa-f]+)((?:/[^/\\]+/CAD-[0-9A-Fa-f]+)+)")
SEGMENT_RE = re.compile(r"/([^/\\]+)/CAD-([0-9A-Fa-f]+)")
XREF_PREFIX_RE = re.compile(r"^(?:[^$]*\$\d+\$)+")   # bound xref names: XREF$0$GPB, A$0$B$0$C
LIST_SPLIT_RE = re.compile(r"[；;，,]")
CLAIM_SEP = "::"   # matched_symbols "block::category"; AutoCAD block names cannot contain a colon (checkpoint 1, 3.4)
INSERT_KEYS = ("record_id", "entity_handle", "block_name", "layer", "x", "y", "z", "rotation", "scale_factors",
               "extrusion", "insert_matrix_wcs", "attributes", "geometry_status", "geometry_issues", "geometry_issue_codes")
MAX_DEPTH = 16


def base_symbol(name: str) -> str:
    """Block name without bound-xref prefixes, so a legend XREF$0$GPB matches a plan GPB (registration 20261016-1800)."""
    return XREF_PREFIX_RE.sub("", name) or name


def claim_parts(entry: str) -> tuple[str, str | None]:
    """A matched_symbols entry: a block name, or block::category claiming only that category's instances (1945)."""
    name, sep, category = entry.partition(CLAIM_SEP)
    return name.strip(), (category.strip() if sep else None)


def normalize_id(value: str) -> tuple[str | None, str | None]:
    """Count-table key: a top-level handle (upper case) or a normalized path id; (None, message) when malformed."""
    raw = value.strip()
    if "/" in raw or "\\" in raw:
        match = PATH_RE.fullmatch(raw)
        if not match:
            return None, (f"路径编号格式不对：{raw}；应为 PT-CAD-<顶层句柄>/<块名>/CAD-<块内句柄>，"
                          "多层时逐级重复 /<块名>/CAD-<句柄>，前缀 PT-CAD-、CAD- 大写")
        return "PT-CAD-" + match.group(1).upper() + "".join(f"/{block}/CAD-{h.upper()}" for block, h in
                                                            SEGMENT_RE.findall(match.group(2))), None
    if raw.startswith("PT-CAD-"):
        raw = raw[len("PT-CAD-"):]
    return raw.upper(), None


class InputProblems(Exception):
    def __init__(self, problems: list[dict[str, str]]):
        super().__init__(f"{len(problems)} 处输入问题")
        self.problems = problems


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class Ledger:
    def __init__(self, data: dict[str, Any], base: Path):
        self.data, self.base = data, base
        self.problems: list[dict[str, str]] = []
        self.files: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []
        self.warnings: dict[str, list[Any]] = defaultdict(list)
        self.search_records: dict[str, dict[str, Any]] | None = None
        self.sources: dict[str, str] = {}
        self.auto_kinds: list[str] = list(DEFAULT_AUTO_EXCLUDE)
        self.class_rules: list[tuple[dict[str, Any], Any, Any, str | None]] = []
        self.frames: list[tuple[str, Any, Any, str]] = []
        self.frame_coverage: dict[str, Any] = {"checked": False, "conclusion": "没写 frame_blocks，没核图框是否都在图页表里"}
        self.legend_frames: Counter[str] = Counter()
        self.legend_frame_rows_dropped: list[dict[str, str]] = []
        self.frame_dispositions: dict[tuple[str, str], dict[str, Any]] = {}
        self.frame_todo: list[dict[str, Any]] = []
        self.frame_disposed: list[dict[str, Any]] = []
        self.auto_excluded: Counter[str] = Counter()
        self.unconfirmed_detail: Counter[tuple[str, str]] = Counter()
        self.nested: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}   # drawing -> (block index, unexpanded tops)
        self.expanded: dict[str, Counter[str]] = {}
        self.neutral_sources: dict[str, Any] = {}
        self.neutral_source_identities: dict[str, str] = {}
        self.source_coverage_gaps: list[dict[str, str]] = []
        self.legend_inventory_gaps: list[dict[str, str]] = []
        self.source_inventory: dict[tuple[str, str], dict[str, Any]] = {}
        self.project_corrections: list[dict[str, Any]] = []

    def problem(self, check: str, where: str, message: str) -> None:
        self.problems.append({"check": check, "where": where, "message": message})

    def path(self, value: str) -> Path:
        p = Path(value)
        return (p if p.is_absolute() else self.base / p).resolve()

    def add(self, drawing: str, cid: str, **fields: Any) -> None:
        self.candidates.append({"drawing": drawing, "id": cid, "count": 1, "kind": "块", "parent_block": None,
                                "layer": "", "x": None, "y": None, "handle": None, "category": None, "basis": "",
                                "suspected_category": None, **fields})

    # ---------- source 1: adapter reports ----------
    def from_reports(self, drawing: str, reports: list[Any]) -> None:
        seen: set[str] = set()
        for value in reports:
            if not nonempty(value):
                self.problem("C-1", drawing, "reports 里的每一项须是报告路径"); continue
            report_path = self.path(value)
            if not report_path.is_file():
                self.problem("C-1", str(value), "适配器报告不存在"); continue
            self.files.append({"role": "report", "drawing": drawing, "path": str(report_path), "sha256": sha256(report_path)})
            report = load_json(report_path)
            source = report.get("neutral_source") or {}
            manifest_value = source.get("manifest") or report.get("cad_manifest")
            source_checked = False
            if manifest_value:
                manifest_path = Path(str(manifest_value))
                if not manifest_path.is_absolute():
                    manifest_path = report_path.parent / manifest_path
                if manifest_path.is_file():
                    manifest = load_json(manifest_path)
                    expected = source.get("source_sha256")
                    actual = (manifest.get("authoritative_source") or {}).get("sha256")
                    if expected and expected != actual:
                        self.problem("C-1", str(report_path), "报告与中性清单来源 SHA256 不一致")
                    elif not isinstance(actual, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", actual):
                        pass  # readable files without an original drawing identity do not establish source coverage
                    elif drawing in self.neutral_source_identities and self.neutral_source_identities[drawing] != actual:
                        self.problem("C-1", str(report_path), "同图纸的报告引用不同原图身份，不能合并为全覆盖")
                    else:
                        loaded = self.neutral_sources.get(drawing) or self.layout(drawing, manifest_path.parent)
                        if loaded is not None:
                            self.neutral_sources[drawing] = loaded
                            self.neutral_source_identities[drawing] = actual
                            self.files.append({"role": "neutral_manifest", "drawing": drawing,
                                "path": str(manifest_path), "sha256": sha256(manifest_path)})
                            source_checked = True
            if not source_checked:
                self.source_coverage_gaps.append({"drawing": drawing, "report": str(report_path),
                    "reason": "该适配器报告缺少可核验的中性来源，未核全图图框及图例行"})
            artifacts = report.get("artifacts") or {}
            disp_path = Path(str(artifacts.get("neutral_instance_dispositions") or ""))
            if not disp_path.is_file() or sha256(disp_path) != artifacts.get("neutral_instance_dispositions_sha256"):
                self.problem("C-1", str(report_path), "处置清单缺失或 SHA256 与报告不一致"); continue
            profile = report.get("profile") or {}
            profile_path = Path(str(profile.get("path") or ""))
            if not profile_path.is_file() or sha256(profile_path) != profile.get("sha256"):
                self.problem("C-1", str(report_path), "识别配置缺失或 SHA256 与报告不一致，换不回原图坐标"); continue
            factor = float((load_json(profile_path).get("unit") or {}).get("factor_to_output") or 0)
            if factor <= 0:
                self.problem("C-1", str(profile_path), "unit.factor_to_output 须为正数"); continue
            categories: dict[str, str] = {}
            count_input = Path(str(artifacts.get("count_input") or ""))
            if count_input.is_file():
                for item in load_json(count_input).get("reconciled_instances") or []:
                    categories[str(item.get("physical_id"))] = str(item.get("category") or "")
            with disp_path.open(encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            for row in rows:
                pid = str(row.get("physical_id") or "")
                dest = DISPOSITION_DEST.get(str(row.get("disposition")))
                if not pid or dest is None:
                    self.problem("C-1", str(disp_path), f"处置记录缺编号或处置取值未知：{pid or '无编号'} {row.get('disposition')}"); continue
                if pid in seen:
                    self.problem("C-1", str(disp_path), f"同一图纸的几份报告里实例编号重复：{pid}"); continue
                seen.add(pid)
                category, basis = None, str(row.get("basis") or "")
                if row["disposition"] == "adopted":
                    category = categories.get(pid)
                    if not category:
                        self.problem("C-1", str(count_input), f"已采用实例在计数输入里没有类别：{pid}"); continue
                elif row["disposition"] == "merged":
                    primary = str(row.get("merged_into") or "")
                    category, basis = categories.get(primary) or "（主实例类别未知）", f"并入 {primary}"
                point = row.get("point") or [None, None]
                ix = point[0] / factor if point[0] is not None else None
                iy = point[1] / factor if point[1] is not None else None
                # scope_geometry_bbox is the adapter's transformed definition extent, in drawing units (1713)
                box = row.get("scope_geometry_bbox")
                if row.get("scope_location_basis") == "insertion_anchor_incomplete_extent_guard":
                    x, y, placed = ix, iy, GUARD_BASIS
                elif isinstance(box, list) and len(box) >= 4 and all(isinstance(v, (int, float)) for v in box[:4]):
                    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    placed = PARTIAL_BASIS if row.get("scope_extent_notes") else GEOMETRY_BASIS
                else:
                    x, y = ix, iy
                    placed = DYNAMIC_BASIS if row.get("scope_location_basis") == "insertion_anchor_dynamic_state_adjudicated" else INSERT_BASIS
                top = "/" not in pid
                entity_type = str(row.get("entity_type") or "INSERT").upper()
                self.add(drawing, pid, symbol=(str(row.get("block_name") or "") if entity_type == "INSERT" else f"<{entity_type}>"),
                         kind="块" if entity_type == "INSERT" else "非块", layer=str(row.get("layer") or ""),
                         parent_block=row.get("parent_block"), destination=dest, category=category, basis=basis,
                         x=x, y=y, insert_x=ix, insert_y=iy, placement_basis=placed,
                         box=[float(v) for v in box[:4]] if placed in (GEOMETRY_BASIS, PARTIAL_BASIS) else None,
                         adjudication_id=row.get("adjudication_id"),
                         original_adapter_disposition={k: row.get(k) for k in
                             ("disposition", "basis", "rule_ids", "adjudication_id", "merged_into")},
                         handle=pid[len("PT-CAD-"):].upper() if top and pid.startswith("PT-CAD-") else None, top_level=top)
            for oid in report.get("unmatched_recognition_object_ids") or []:
                self.add(drawing, f"{oid}#未承接", symbol=str(oid), kind="未承接对象", destination="无去向",
                         basis="模型识别台账列了、适配器没有承接")

    # ---------- source 2: neutral layout + project count tables ----------
    def layout(self, drawing: str, neutral_dir: Path) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any], dict[str, Any]] | None:
        manifest_path = neutral_dir / "CAD中性取证清单.json"
        if not manifest_path.is_file():
            self.problem("C-2", str(neutral_dir), "中性取证目录里没有 CAD中性取证清单.json"); return None
        manifest = load_json(manifest_path)
        artifacts = manifest.get("artifacts") or {}
        paths = {}
        for key in ("layout_entities", "block_definitions"):
            artifact = artifacts.get(key) or {}
            raw = Path(str(artifact.get("path") or ""))
            paths[key] = raw if raw.is_absolute() else neutral_dir / raw
            if not paths[key].is_file() or sha256(paths[key]) != str(artifact.get("sha256") or "").lower():
                self.problem("C-2", str(manifest_path), f"中性 {key} 缺失或 SHA256 与清单不一致"); return None
            self.files.append({"role": f"neutral_{key}", "drawing": drawing, "path": str(paths[key]), "sha256": artifact.get("sha256")})
        meta = {str(b.get("block_name")): b for b in manifest.get("blocks") or [] if isinstance(b, dict) and b.get("block_name")}
        empty = frozenset(n for n, b in meta.items() if cad_geometry.is_empty_definition(b))
        top: dict[str, dict[str, Any]] = {}
        in_block: set[str] = set()
        # Streamed once: per block only its folded extent rows and its child INSERTs are kept (D006 definitions are
        # 734 MB); the folder keeps what cad_geometry.definition_extent needs, so placement follows the adapters' rules.
        blocks: dict[str, dict[str, Any]] = {}
        with paths["block_definitions"].open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                parent = row.get("parent_block")
                handle_id = str(row.get("entity_handle") or "").upper()
                if handle_id:
                    in_block.add(handle_id)   # a counted row may point inside a block
                if parent is None or row.get("row_type", "entity") != "entity":
                    continue
                entry = blocks.get(str(parent))
                if entry is None:
                    entry = blocks[str(parent)] = {"inserts": [], "texts": [], "folder": cad_geometry.DefinitionExtentFolder(
                        empty, cad_geometry.DYNAMIC_STATE_CODES)}
                entry["folder"].add(row)
                if str(row.get("entity_type") or "").upper() == "INSERT":
                    entry["inserts"].append({k: row.get(k) for k in INSERT_KEYS})
                elif str(row.get("entity_type") or "").upper() in {"TEXT", "MTEXT"} and row.get("text"):
                    entry["texts"].append({k: row.get(k) for k in ("text", "x", "y", "z", "raw_geometry")})
        with paths["layout_entities"].open(encoding="utf-8") as handle:
            for line in handle:
                if '"parent_block":null' not in line and '"parent_block": null' not in line:   # block children: handle only
                    match = HANDLE_RE.search(line)
                    if match:
                        in_block.add(match.group(1).upper())
                    continue
                row = json.loads(line)
                if row.get("parent_block") is not None or row.get("layout") != "Model":
                    continue
                handle_id = str(row.get("entity_handle") or "").upper()
                if handle_id:
                    top.setdefault(handle_id, row)
        return top, in_block, blocks, meta

    @staticmethod
    def matrix(row: dict[str, Any], meta: dict[str, Any]) -> tuple[float, ...] | None:
        origin = (meta.get(str(row.get("block_name") or "")) or {}).get("origin") or [0.0, 0.0, 0.0]
        try:
            return cad_geometry.matrix_for_insert(row, origin)
        except (ValueError, TypeError, IndexError):
            return None

    def extent(self, blocks: dict[str, Any], meta: dict[str, Any], name: str, memo: dict[Any, Any]):
        """(local box, notes) of a block definition for sheet placement (cad_geometry.definition_extent with
        skip_unknown, K3 of registration 20261017-0031 and decision 10-17): fills skipped, labels at their insertion
        point, rows without any box and unreadable children skipped as in 0.3.10 (an estimate, noted); only a block
        with nothing measurable left is placed by its insertion point."""
        if ("definitions",) not in memo:   # tuple keys never meet a block name
            memo[("definitions",)] = {n: entry["folder"].rows() for n, entry in blocks.items()}
            memo[("origins",)] = {n: (m.get("origin") or [0.0, 0.0, 0.0]) for n, m in meta.items()}
            memo[("cache",)] = {}
            memo[("empty",)] = next((entry["folder"].empty for entry in blocks.values()), frozenset())
        try:
            return cad_geometry.definition_extent(name, memo[("definitions",)], memo[("origins",)], MAX_DEPTH, memo[("cache",)],
                                                  (), memo[("empty",)], cad_geometry.DYNAMIC_STATE_CODES, skip_unknown=True)
        except (ValueError, TypeError, IndexError):
            return None, frozenset()

    def placed(self, blocks, meta, memo, name: str, matrix, insert_point) -> tuple[Any, Any, str, list[float] | None]:
        """(x, y, basis, plan box [x1, y1, x2, y2] or None); the box is kept for the frame check (fifth batch G)."""
        box, notes = self.extent(blocks, meta, name, memo) if matrix is not None else (None, frozenset())
        world = cad_geometry.transform_bounds(box, matrix) if box is not None else None
        (x, y), basis = cad_geometry.placement_point(world, notes, insert_point)
        return x, y, basis, ([float(world[0]), float(world[1]), float(world[3]), float(world[4])] if world is not None else None)

    def expand(self, drawing: str, path_id: str, block: str, matrix, layer: str, blocks, meta, memo, expand_set: set[str],
               out: dict[str, dict[str, Any]], stack: tuple[str, ...]) -> None:
        """Every INSERT inside a declared container, through the accumulated matrix; nested declared containers recurse."""
        for child in blocks.get(block, {}).get("inserts", []):
            name = str(child.get("block_name") or "")
            child_handle = str(child.get("entity_handle") or "").upper()
            cid = f"{path_id}/{block}/CAD-{child_handle}"
            child_matrix = self.matrix(child, meta)
            if child_matrix is None or not child_handle:
                self.problem("C-2", f"{drawing} {cid}", "块内插入缺句柄或变换矩阵，展不开；用新版中性提取器重提")
                continue
            world = cad_geometry.compose_matrix(matrix, child_matrix)
            local = [float(child.get(k) or 0.0) for k in ("x", "y", "z")]
            point = cad_geometry.transform_point(local, matrix)
            own_layer = str(child.get("layer") or "")
            effective = layer if own_layer in {"", "0"} and layer else own_layer
            x, y, basis, box = self.placed(blocks, meta, memo, name, world, point)
            out[cid] = {"symbol": name, "kind": "块", "layer": effective, "parent_block": block, "x": x, "y": y,
                        "insert_x": point[0], "insert_y": point[1], "placement_basis": basis, "box": box, "handle": None,
                        "top_level": False, "container": name in expand_set}
            if name in expand_set:
                if name in stack or len(stack) >= MAX_DEPTH:
                    self.problem("C-2", f"{drawing} {cid}", f"展开的块循环引用或超过 {MAX_DEPTH} 层：{name}")
                    continue
                self.expand(drawing, cid, name, world, effective, blocks, meta, memo, expand_set, out, (*stack, name))

    def path_problem(self, key: str, top: dict[str, Any], expand_set: set[str], expanded: dict[str, Any]) -> str:
        top_handle, segments = key[len("PT-CAD-"):].split("/", 1)[0], SEGMENT_RE.findall(key)
        row = top.get(top_handle)
        if row is None:
            return "路径编号的顶层句柄在中性布局里查不到"
        first = str(row.get("block_name") or "")
        if first not in expand_set:
            return f"路径编号指向未在 expand_blocks 声明展开的块 {first}；先在 drawings[].expand_blocks 声明"
        folded = {k.casefold(): k for k in expanded}
        if key.casefold() in folded:
            return f"路径编号的块名大小写与图中不符（图中是 {folded[key.casefold()]}）"
        if segments and segments[0][0] != first:
            return f"路径编号第一级块名 {segments[0][0]} 与顶层块 {first} 不符"
        return "路径编号在展开结果里查不到（层级、块名或块内句柄不对）"

    def from_tables(self, drawing: str, neutral_dir: Path, counted: dict, excluded: dict, expand_set: set[str]) -> None:
        loaded = self.layout(drawing, neutral_dir)
        if loaded is None:
            return
        top, in_block, blocks, meta = loaded
        self.neutral_sources[drawing] = loaded
        for name in sorted(expand_set):
            if name not in blocks:
                state = meta.get(name)
                why = ("块定义没有序列化，中性取证用 --all-blocks 重提" if state is not None and state.get("serialized") is False
                       else "块定义里没有这个块（块名区分大小写）")
                self.problem("C-2", f"drawings[{drawing}].expand_blocks", f"{why}：{name}")
        memo: dict[str, Any] = {}
        tops: dict[str, dict[str, Any]] = {}
        expanded: dict[str, dict[str, Any]] = {}
        unexpanded: list[str] = []
        for handle_id, row in sorted(top.items()):
            is_insert = str(row.get("entity_type") or "").upper() == "INSERT"
            if not is_insert and handle_id not in counted:
                continue
            x, y = row.get("x"), row.get("y")
            if (x is None or y is None) and row.get("bbox"):
                box = row["bbox"]; x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            symbol = str(row.get("block_name") or "") if is_insert else f"<{row.get('entity_type')}>"
            fields = {"symbol": symbol, "kind": "块" if is_insert else "非块", "layer": str(row.get("layer") or ""),
                      "x": x, "y": y, "insert_x": x, "insert_y": y, "placement_basis": INSERT_BASIS, "box": None,
                      "handle": handle_id, "top_level": True, "container": is_insert and symbol in expand_set}
            matrix = self.matrix(row, meta) if is_insert else None
            if is_insert and x is not None and y is not None:
                fields["x"], fields["y"], fields["placement_basis"], fields["box"] = self.placed(blocks, meta, memo, symbol, matrix, (x, y))
            tops[handle_id] = fields
            if fields["container"]:
                if matrix is None:
                    self.problem("C-2", f"{drawing} PT-CAD-{handle_id}", "顶层容器块缺变换矩阵，展不开；用新版中性提取器重提")
                    continue
                self.expand(drawing, f"PT-CAD-{handle_id}", symbol, matrix, fields["layer"], blocks, meta, memo, expand_set,
                            expanded, (symbol,))
            elif is_insert and blocks.get(symbol, {}).get("inserts"):
                unexpanded.append(handle_id)
        used = {f["symbol"] for f in (*tops.values(), *expanded.values()) if f.get("container")}
        for name in sorted(expand_set - used):
            if name in blocks:
                self.warnings["expand_blocks_unused"].append({"drawing": drawing, "block_name": name})
        for table, rows in (("count_tables", counted), ("handle_exclusions", excluded)):
            for key, row in rows.items():
                if key.startswith("PT-CAD-"):
                    if key not in expanded:
                        self.problem("C-3", row["_where"], f"{self.path_problem(key, top, expand_set, expanded)}：{key}")
                    elif table == "count_tables" and expanded[key]["container"]:
                        self.problem("C-3", row["_where"], f"块已在 expand_blocks 里展开，块内候选另列；容器本身不能再进计数表：{key}")
                elif key not in top:
                    why = ("句柄是块内图元，不能作候选；它在已声明展开的容器块里时，写路径编号 PT-CAD-<顶层句柄>/<块名>/CAD-<块内句柄>"
                           if key in in_block else "句柄在中性布局里查不到")
                    self.problem("C-3", row["_where"], f"{why}：{key}")
                elif table == "count_tables" and tops.get(key, {}).get("container"):
                    # the same physical thing would be counted once at the top and again under its path ids
                    self.problem("C-3", row["_where"], f"块已在 expand_blocks 里展开，块内候选另列；容器本身不能再进计数表：{key}")
        container_basis = "容器块，已按 expand_blocks 展开，块内候选另列"
        for cid, fields in [(f"PT-CAD-{h}", f) for h, f in tops.items()] + sorted(expanded.items()):
            key = fields["handle"] if fields["top_level"] else cid
            if key in counted and not fields["container"]:
                c = counted[key]
                self.add(drawing, cid, destination="已计", category=c["category"], table_sheet=c.get("sheet"), basis=c["_where"], **fields)
            elif key in excluded:
                self.add(drawing, cid, destination="有据排除", basis=excluded[key]["basis"], **fields)
            elif fields["container"]:
                self.add(drawing, cid, destination="有据排除", basis=container_basis, **fields)
            else:
                self.add(drawing, cid, destination="待识别", basis="不在计数表和排除表里", **fields)
        self.expanded[drawing] = Counter(f["parent_block"] for f in expanded.values())
        holders = set(unexpanded)
        self.nested[drawing] = (blocks, [c for c in self.candidates if c["drawing"] == drawing and c.get("top_level")
                                         and c["handle"] in holders])

    def unexpanded_warnings(self) -> None:
        """Top-level blocks that hold countable INSERTs nobody expanded: the ledger would otherwise look complete
        while the objects inside are invisible (registration 20261016-1709). Countable = a block name that is counted,
        or that appears on a legend sheet, in the same drawing."""
        for drawing, (blocks, holders) in sorted(self.nested.items()):
            countable = {base_symbol(c["symbol"]) for c in self.candidates if c["drawing"] == drawing and c["kind"] == "块"
                         and (c["destination"] == "已计" or c["sheet_kind"] == LEGEND_KIND)}
            if not countable:
                continue
            memo: dict[str, Counter[str]] = {}

            def nested(name: str, stack: tuple[str, ...] = ()) -> Counter[str]:
                if name in memo:
                    return memo[name]
                total: Counter[str] = Counter()
                if name in stack or len(stack) >= MAX_DEPTH:
                    return total
                for child in blocks.get(name, {}).get("inserts", []):
                    child_name = str(child.get("block_name") or "")
                    if base_symbol(child_name) in countable:
                        total[child_name] += 1
                    total.update(nested(child_name, (*stack, name)))
                memo[name] = total
                return total

            found: dict[str, dict[str, Any]] = {}
            for c in holders:
                if c["destination"] == "已计" or c["sheet_kind"] == LEGEND_KIND:
                    continue
                inside = nested(c["symbol"])
                if inside:
                    entry = found.setdefault(c["symbol"], {"top_level_insert_count": 0, "inside": inside, "sample_ids": []})
                    entry["top_level_insert_count"] += 1
                    if len(entry["sample_ids"]) < 3:
                        entry["sample_ids"].append(c["id"])
            for name, entry in sorted(found.items()):
                per = sum(entry["inside"].values())
                self.warnings["unexpanded_container_blocks"].append({
                    "drawing": drawing, "block_name": name, "top_level_insert_count": entry["top_level_insert_count"],
                    "countable_inserts_per_instance": per, "countable_inserts_total": per * entry["top_level_insert_count"],
                    "symbols": dict(entry["inside"].most_common(8)), "sample_ids": entry["sample_ids"],
                    "hint": "块里有计数类图元却没展开，台账看不到它们；在 drawings[].expand_blocks 声明，或改用适配器报告"})

    def table_rows(self, key: str, keys: tuple[str, ...], required: tuple[str, ...], drawings: dict[str, str]) -> dict[str, dict]:
        out: dict[str, dict[str, dict]] = defaultdict(dict)
        for spec in self.data.get(key) or []:
            if isinstance(spec, dict) and nonempty(spec.get("path")):
                spec = {**spec, "path": str(self.path(spec["path"]))}
            rows, _ = read_rows(spec, keys, self.base, self.problems, self.files, key)
            for row in rows:
                missing = [k for k in required if not nonempty(row.get(k))]
                if missing:
                    self.problem("C-3", row["_where"], "缺少 " + "、".join(missing)); continue
                drawing = row["drawing"]
                handle_id, error = normalize_id(row["handle"])
                if error:
                    self.problem("C-3", row["_where"], error); continue
                if drawings.get(drawing) != "tables" and not (key == "handle_exclusions" and drawings.get(drawing) == "reports"):
                    self.problem("C-3", row["_where"], f"图纸 {drawing} 不在 drawings 里，或不是“中性清单 + 计数表”来源"); continue
                prev = out[drawing].get(handle_id)
                if prev is not None:
                    self.problem("C-3", row["_where"], f"同一句柄在{key}里出现两次：{handle_id}（另见 {prev['_where']}）"); continue
                out[drawing][handle_id] = row
        return out

    def apply_project_corrections(self, excluded):
        """Coverage destinations only: retain adapter evidence; never create or approve a quantity instance."""
        by_id = {}
        for c in self.candidates:
            normalized, error = normalize_id(c["id"])
            if not error:
                by_id[(c["drawing"], normalized)] = c
        seen = set()
        specs = self.data.get("count_overrides") or []
        if not isinstance(specs, list):
            self.problem("C-3", "count_overrides", "须为数组，每项写 drawing、physical_id、category、basis")
            return
        for i, spec in enumerate(specs):
            where = f"count_overrides[{i}]"
            if not isinstance(spec, dict) or not all(nonempty(spec.get(k)) for k in ("drawing", "physical_id", "category", "basis")):
                self.problem("C-3", where, "drawing、physical_id、category、basis 必填"); continue
            pid, error = normalize_id(spec["physical_id"])
            key = (spec["drawing"], pid)
            c = by_id.get(key)
            if error or self.sources.get(spec["drawing"]) != "reports" or c is None:
                self.problem("C-3", where, error or "项目补收只能引用 reports 来源中实际存在的唯一实例"); continue
            if key in seen or pid in excluded.get(spec["drawing"], {}):
                self.problem("C-3", where, "同一实例重复补收或同时写了项目排除"); continue
            seen.add(key)
            original = c.get("original_adapter_disposition") or {}
            if original.get("disposition") not in {"excluded", "not_applicable", "unresolved"}:
                self.problem("C-3", where, "只更正适配器排除或未决实例；已计、合并、重复剔除须回适配器核对唯一身份"); continue
            if c["destination"] == "版本冲突":
                self.problem("C-3", where, "实例尚有版本冲突，不能用项目补收绕过版本核对"); continue
            self.project_corrections.append({"drawing": c["drawing"], "physical_id": c["id"],
                "original_adapter": original, "previous_coverage_destination": c["destination"],
                "project_destination": "已计", "category": spec["category"], "basis": spec["basis"],
                "quantity_validation_performed": False})
            c.update(destination="已计", category=spec["category"], basis=f"项目补收：{spec['basis']}；原处置：{original}")
        for drawing, rows in excluded.items():
            if self.sources.get(drawing) != "reports":
                continue
            for pid, spec in rows.items():
                c = by_id.get((drawing, pid))
                if c is None:
                    self.problem("C-3", spec["_where"], f"排除编号不在报告候选里：{pid}"); continue
                if c["destination"] in {"已计", "版本冲突"}:
                    self.problem("C-3", spec["_where"], "不能用覆盖台账排除已计或版本冲突实例；回适配器核对计数及版本"); continue
                original = c.get("original_adapter_disposition") or {}
                self.project_corrections.append({"drawing": drawing, "physical_id": c["id"], "original_adapter": original,
                    "previous_coverage_destination": c["destination"], "project_destination": "有据排除",
                    "basis": spec["basis"], "quantity_validation_performed": False})
                c.update(destination="有据排除", basis=f"项目排除：{spec['basis']}；原处置：{original}")

    # ---------- common rules ----------
    def load_sheets(self) -> dict[str, list[dict[str, Any]]]:
        sheets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for spec in self.data.get("sheets") or []:
            if isinstance(spec, dict) and nonempty(spec.get("path")):
                spec = {**spec, "path": str(self.path(spec["path"]))}
            rows, _ = read_rows(spec, SHEET_KEYS, self.base, self.problems, self.files, "sheets")
            for row in rows:
                kind = row.get("sheet_kind") or None
                if kind is not None and kind not in SHEET_KINDS:
                    self.problem("C-4", row["_where"], "sheet_kind 只能是 " + "、".join(SHEET_KINDS)); continue
                box = [row.get(k) for k in ("x1", "y1", "x2", "y2")]
                if not nonempty(row.get("sheet_id")) or not nonempty(row.get("drawing")) or any(v is None for v in box):
                    self.problem("C-4", row["_where"], "图页须有 sheet_id、drawing 和 x1、y1、x2、y2"); continue
                x1, x2 = sorted((box[0], box[2])); y1, y2 = sorted((box[1], box[3]))
                sheets[row["drawing"]].append({"sheet_id": row["sheet_id"], "title": row.get("title") or row["sheet_id"],
                                               "kind": kind, "bounds": (x1, y1, x2, y2), "area": (x2 - x1) * (y2 - y1)})
        return sheets

    @staticmethod
    def locate(sheets: dict[str, list[dict[str, Any]]], drawing: str, x: Any, y: Any) -> dict[str, Any] | None:
        if x is None or y is None:
            return None
        hits = [s for s in sheets.get(drawing, []) if s["bounds"][0] <= x <= s["bounds"][2] and s["bounds"][1] <= y <= s["bounds"][3]]
        return min(hits, key=lambda item: item["area"]) if hits else None

    def place(self, sheets: dict[str, list[dict[str, Any]]]) -> None:
        """Sheet from the geometry centre when known (registration 20261016-1713), else the insertion point."""
        for c in self.candidates:
            c["sheet"], c["sheet_title"], c["sheet_kind"] = UNSHEETED, UNSHEETED, None
            s = self.locate(sheets, c["drawing"], c["x"], c["y"])
            if s:
                c["sheet"], c["sheet_title"], c["sheet_kind"] = s["sheet_id"], s["title"], s["kind"]
            if c.get("placement_basis") in GEOMETRY_BASES and c.get("insert_x") is not None:
                at_insert = self.locate(sheets, c["drawing"], c["insert_x"], c["insert_y"])
                insert_sheet = at_insert["sheet_id"] if at_insert else UNSHEETED
                if insert_sheet != c["sheet"]:
                    self.warnings["insert_geometry_sheet_mismatch"].append({"drawing": c["drawing"], "id": c["id"], "symbol": c["symbol"],
                                                                            "insert_sheet": insert_sheet, "geometry_sheet": c["sheet"]})
            if c.get("table_sheet") and c["table_sheet"] != c["sheet"]:
                self.warnings["sheet_mismatches"].append({"id": c["id"], "table_sheet": c["table_sheet"], "placed_sheet": c["sheet"]})

    def sheet_kind_rules(self, sheets: dict[str, list[dict[str, Any]]], drawings: dict[str, str]) -> dict[tuple[str, str], str]:
        """auto_exclude_sheet_kinds (legend and system sheets only) and detail_copy_sheets (each detail sheet confirmed)."""
        raw = self.data.get("auto_exclude_sheet_kinds")
        raw = DEFAULT_AUTO_EXCLUDE if raw is None else raw
        kinds: list[str] = []
        if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
            self.problem("C-5", "auto_exclude_sheet_kinds", "须是图页类型的数组，例如 [\"图例\", \"系统图\"]")
        else:
            for value in raw:
                if value == DETAIL_KIND:
                    self.problem("C-5", "auto_exclude_sheet_kinds", "详图页不能整类自动排除：详图可能是唯一表达；确认是复制件的详图页逐页写进 detail_copy_sheets")
                elif value not in AUTO_EXCLUDABLE:
                    self.problem("C-5", "auto_exclude_sheet_kinds", f"只能是 {'、'.join(AUTO_EXCLUDABLE)}：{value}")
                elif value not in kinds:
                    kinds.append(value)
        self.auto_kinds = kinds
        confirmed: dict[tuple[str, str], str] = {}
        specs = self.data.get("detail_copy_sheets") or []
        if not isinstance(specs, list):
            self.problem("C-5", "detail_copy_sheets", "须是数组"); return confirmed
        for i, spec in enumerate(specs):
            where = f"detail_copy_sheets[{i}]"
            if not isinstance(spec, dict) or not all(nonempty(spec.get(k)) for k in ("drawing", "sheet_id", "basis")):
                self.problem("C-5", where, "确认的详图页须写 drawing、sheet_id、basis"); continue
            drawing, sheet_id = spec["drawing"].strip(), spec["sheet_id"].strip()
            if drawing not in drawings:
                self.problem("C-5", where, f"图纸 {drawing} 不在 drawings 里"); continue
            found = [s for s in sheets.get(drawing, []) if s["sheet_id"] == sheet_id]
            if not found:
                self.problem("C-5", where, f"图页表里没有 {drawing} 的图页 {sheet_id}"); continue
            if any(s["kind"] != DETAIL_KIND for s in found):
                self.problem("C-5", where, f"图页 {sheet_id} 的类型是 {found[0]['kind'] or '未写'}，不是详图；只有详图页能这样确认复制件"); continue
            if (drawing, sheet_id) in confirmed:
                self.problem("C-5", where, f"图页 {drawing} {sheet_id} 重复确认"); continue
            confirmed[(drawing, sheet_id)] = spec["basis"].strip()
        return confirmed

    def apply_rules(self, confirmed: dict[tuple[str, str], str]) -> None:
        conflicts = self.data.get("version_conflicts") or []
        classes = self.data.get("excluded_classes") or []
        compiled = []
        for i, vc in enumerate(conflicts):
            where = f"version_conflicts[{i}]"
            if not isinstance(vc, dict) or not nonempty(vc.get("conflict_id")) or not nonempty(vc.get("basis")) or not nonempty(vc.get("drawing")):
                self.problem("C-5", where, "版本冲突须有 conflict_id、drawing、basis"); continue
            ids = set(vc.get("object_ids") or [])
            pattern = self.regex(vc.get("block_pattern"), where)
            if not ids and pattern is None:
                self.problem("C-5", where, "版本冲突须写 object_ids 或 block_pattern"); continue
            compiled.append((vc, ids, pattern))
        rules = []
        for i, ex in enumerate(classes):
            where = f"excluded_classes[{i}]"
            if not isinstance(ex, dict) or not nonempty(ex.get("class_id")) or not nonempty(ex.get("basis")) or not nonempty(ex.get("drawing")):
                self.problem("C-5", where, "整类排除须有 class_id、drawing、basis"); continue
            block, layer = self.regex(ex.get("block_pattern"), where), self.regex(ex.get("layer_pattern"), where)
            if block is None and layer is None:
                self.problem("C-5", where, "整类排除须写 block_pattern 或 layer_pattern"); continue
            kind = ex.get("sheet_kind")
            if kind in (None, ""):
                kind = None
            elif kind == DETAIL_KIND:
                self.problem("C-5", where, "整类排除不能按详图页限定：详图页复制件逐页写进 detail_copy_sheets"); continue
            elif kind not in SHEET_KINDS:
                self.problem("C-5", where, "sheet_kind 只能是 " + "、".join(SHEET_KINDS)); continue
            rules.append((ex, block, layer, kind))
        self.class_rules = rules
        for c in self.candidates:
            if c["kind"] not in ("块", "非块"):
                continue
            for vc, ids, pattern in compiled:
                if vc["drawing"] in ("*", c["drawing"]) and (c["id"] in ids or (pattern and pattern.search(c["symbol"]))):
                    c["destination"], c["basis"], c["category"] = "版本冲突", f"{vc['conflict_id']} {vc['basis']}", None
                    break
            if c["destination"] == "版本冲突":
                continue
            kind, detail = c["sheet_kind"], confirmed.get((c["drawing"], c["sheet"]))
            if kind in self.auto_kinds or detail is not None:
                if c["destination"] == "待识别":
                    label = kind if kind in self.auto_kinds else "详图（已确认）"
                    basis = AUTO_BASIS[kind] if kind in self.auto_kinds else f"详图页上的复制件（已确认：{detail}）"
                    c["destination"], c["basis"] = "有据排除", basis
                    self.auto_excluded[label] += c["count"]
                elif c["destination"] == "已计":
                    key = "counted_on_system_sheet" if kind == SYSTEM_KIND else "counted_on_legend_or_detail_sheet"
                    self.warnings[key].append({"id": c["id"], "sheet": c["sheet"], "symbol": c["symbol"]})
            elif kind == DETAIL_KIND:
                # not confirmed: may be the only drawing of the object, so it stays pending with a hint (1800)
                if c["destination"] == "待识别":
                    c["basis"] = f"{c['basis']}；详图页，可能是复制件，确认后写 detail_copy_sheets"
                elif c["destination"] == "已计":
                    self.warnings["counted_on_legend_or_detail_sheet"].append({"id": c["id"], "sheet": c["sheet"], "symbol": c["symbol"]})
            for ex, block, layer, rule_kind in rules:
                if self.class_hits(ex, block, layer, rule_kind, c):
                    if c["destination"] == "待识别":
                        c["destination"], c["basis"] = "有据排除", f"{ex['class_id']} {ex['basis']}"
                    elif c["destination"] == "已计":
                        self.warnings["counted_matches_excluded_class"].append({"id": c["id"], "class_id": ex["class_id"]})
                    break
            # counted after the class rules: a frame block they exclude is no longer pending (checkpoint 1 fix)
            if kind == DETAIL_KIND and detail is None and c["destination"] == "待识别":
                self.unconfirmed_detail[(c["drawing"], c["sheet"])] += c["count"]

    def frame_rules(self) -> None:
        """frame_blocks (fifth batch G, 1859): frame block names as a regex, or an excluded_classes rule id (its block and
        layer patterns, any sheet kind - a frame outside every sheet has none)."""
        specs = self.data.get("frame_blocks")
        if specs is None:
            return
        if not isinstance(specs, list):
            self.problem("C-5", "frame_blocks", "须是数组：每项写 block_pattern（图框块名正则）或 class_id（excluded_classes 的规则号）"); return
        by_class = {ex["class_id"]: (ex, block, layer) for ex, block, layer, _ in self.class_rules}
        for i, spec in enumerate(specs):
            where = f"frame_blocks[{i}]"
            if (not isinstance(spec, dict) or
                    (nonempty(spec.get("class_id")) ==
                     (nonempty(spec.get("block_pattern")) or nonempty(spec.get("layer_pattern"))))):
                self.problem("C-5", where, "每项写 class_id，或 block_pattern / layer_pattern（两种正则可同时写，须同时命中）"); continue
            drawing = str(spec.get("drawing") or "").strip()
            if nonempty(spec.get("class_id")):
                hit = by_class.get(str(spec["class_id"]).strip())
                if hit is None:
                    self.problem("C-5", where, f"引用的规则号 {spec['class_id']} 不在 excluded_classes 里"); continue
                ex, block, layer = hit
                self.frames.append((drawing or ex["drawing"], block, layer, str(spec["class_id"]).strip()))
            else:
                block = self.regex(spec.get("block_pattern"), where)
                layer = self.regex(spec.get("layer_pattern"), where)
                if block is not None or layer is not None:
                    self.frames.append((drawing or "*", block, layer, " & ".join(p.pattern for p in (block, layer) if p is not None)))

    def frame_disposition_rules(self) -> None:
        """frame_dispositions (checkpoint 3): the project's answer to one reported frame - a cover, a catalogue, a blank
        frame, a sheet now added to the table, or other - always with its basis; a disposed frame is no longer reported."""
        items = self.data.get("frame_dispositions")
        if items is None:
            return
        if self.data.get("frame_blocks") is None:
            self.problem("C-5", "frame_dispositions", "写了 frame_dispositions 却没写 frame_blocks：不核图框就没有可处置的图框"); return
        if not isinstance(items, list):
            self.problem("C-5", "frame_dispositions", "须是数组：每项写 drawing、handle 或 id、disposition、basis"); return
        for i, item in enumerate(items):
            where = f"frame_dispositions[{i}]"
            if not isinstance(item, dict) or not nonempty(item.get("drawing")) or nonempty(item.get("handle")) == nonempty(item.get("id")):
                self.problem("C-5", where, "每项写 drawing，再写 handle（顶层图框句柄）或 id（候选编号）之一"); continue
            if item.get("disposition") not in FRAME_DISPOSITIONS:
                self.problem("C-5", where, f"disposition 只能是 {'、'.join(FRAME_DISPOSITIONS)}"); continue
            if not nonempty(item.get("basis")):
                self.problem("C-5", where, "basis 必填：写清这张图框为什么不用进图页表，或已补进哪张图页"); continue
            frame = str(item["handle"]).strip().upper() if nonempty(item.get("handle")) else str(item["id"]).strip()
            key = (str(item["drawing"]).strip(), frame)
            if key in self.frame_dispositions:
                self.problem("C-5", where, f"同一图框写了两条处置：{key[0]} {frame}"); continue
            self.frame_dispositions[key] = {"disposition": item["disposition"], "basis": str(item["basis"]).strip(), "where": where}

    def is_frame(self, c: dict[str, Any]) -> bool:
        return c["kind"] == "块" and any(drawing in ("*", c["drawing"]) and (block is None or block.search(c["symbol"]))
                                         and (layer is None or layer.search(c["layer"])) for drawing, block, layer, _ in self.frames)

    @staticmethod
    def frame_on_sheet(box: list[float], sheet_list: list[dict[str, Any]]) -> bool:
        """A sheet box may be the whole frame or only its drawing area (D003's table boxes the 310 x 320 plan inside an
        840 x 594 frame, so the frame centre falls outside): the frame is on the table when it and a sheet share at least
        half of the smaller of the two. Frames side by side only touch, so a neighbour's sheet never covers a frame."""
        area = (box[2] - box[0]) * (box[3] - box[1])
        for s in sheet_list:
            x1, y1, x2, y2 = s["bounds"]
            w, h = min(box[2], x2) - max(box[0], x1), min(box[3], y2) - max(box[1], y1)
            if w > 0 and h > 0 and w * h >= 0.5 * min(area, s["area"]):
                return True
        return False

    def check_frames(self, sheets: dict[str, list[dict[str, Any]]], drawings: dict[str, str]) -> None:
        """Every frame instance must be on a sheet of the table: one that is not is a sheet the table may have left out
        (D003 sheet 005, 552.78 m of earthing never counted). On the table = geometry centre on a sheet (as any candidate)
        or the frame extent sharing most of a sheet box (frame_on_sheet). A frame inside another frame (the title block
        nested in A0) goes with its outer frame. A frame known only by its insertion point (a corner) and off every sheet
        cannot be checked: listed, not counted as a missing sheet. Report only."""
        if self.data.get("frame_blocks") is None:
            return
        inventory = { (c["drawing"], c["id"]): c for c in self.source_inventory.values() }
        inventory.update({(c["drawing"], c["id"]): c for c in self.candidates})
        found = [c for c in inventory.values() if self.is_frame(c)]
        ids = {c["id"] for c in found}

        def inside_frame(cid: str) -> bool:
            while "/" in cid:
                cid = cid.rsplit("/", 2)[0]
                if cid in ids:
                    return True
            return False

        frames = [c for c in found if not inside_frame(c["id"])]
        reported: list[tuple[str, dict[str, Any], str]] = []
        for c in sorted(frames, key=lambda f: (f["drawing"], f["id"])):
            sheet_list = sheets.get(c["drawing"])
            if not sheet_list:
                if drawings.get(c["drawing"]) != "legend_only":
                    reported.append(("整图没有图页表", c, "这张图没有图页表，图框和图页都没核：写图页表后重算"))
                continue
            if c["sheet"] != UNSHEETED:
                continue
            box = c.get("box")
            if box is not None and box[2] > box[0] and box[3] > box[1]:
                if not self.frame_on_sheet(box, sheet_list):
                    reported.append(("可能漏页", c, "图框不在图页表的任何图页里，可能漏了这张图页：补页后重算，或在 frame_dispositions 写处置"))
            else:
                reported.append(("定不出范围", c, "图框定不出图形范围，插入点（图框角点）不在任何图页里，没法核：核图后补图页表、"
                                               "补全图框范围，或在 frame_dispositions 写处置"))
        # every reported frame is one to-do row (checkpoint 3), unless the project wrote its disposition
        warning_key = {"可能漏页": "frame_outside_sheet_table", "定不出范围": "frame_extent_unknown"}
        used: set[tuple[str, str]] = set()
        counts: Counter[str] = Counter()
        for kind, c, hint in reported:
            key = next((k for k in ((c["drawing"], c["handle"] or ""), (c["drawing"], c["id"])) if k in self.frame_dispositions), None)
            row = {"drawing": c["drawing"], "id": c["id"], "handle": c["handle"], "block_name": c["symbol"],
                   "insert_point": [c.get("insert_x"), c.get("insert_y")], "geometry_centre": [c["x"], c["y"]],
                   "geometry_box": c.get("box"), "placement_basis": c.get("placement_basis")}
            if key is not None:
                used.add(key)
                given = self.frame_dispositions[key]
                self.frame_disposed.append({**row, "kind": kind, "disposition": given["disposition"], "basis": given["basis"]})
                continue
            counts[kind] += 1
            if kind in warning_key:
                self.warnings[warning_key[kind]].append({**row, "hint": hint})
            box = c.get("box") or [None, None, None, None]
            self.frame_todo.append({"todo_id": f"FR-{len(self.frame_todo) + 1:04d}", "drawing": c["drawing"], "kind": kind, "id": c["id"],
                                    "handle": c["handle"], "block_name": c["symbol"], "insert_x": c.get("insert_x"),
                                    "insert_y": c.get("insert_y"), "x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3],
                                    "placement_basis": c.get("placement_basis"), "hint": hint})
        for drawing in sorted(d for d, kind in drawings.items() if kind != "legend_only" and not sheets.get(d)):
            own = [t for t in self.frame_todo if t["drawing"] == drawing]
            if not own and any(c["drawing"] == drawing for c in self.frame_disposed):
                continue
            self.warnings["frames_without_sheet_table"].append({"drawing": drawing, "frame_count": len(own), "sample_ids": [t["id"] for t in own[:3]],
                                                                "hint": "这张图没有图页表，图框和图页都没核：写图页表后重算"})
        for key, given in sorted(self.frame_dispositions.items()):
            if key not in used:
                self.warnings["frame_dispositions_unused"].append({"drawing": key[0], "frame": key[1], "where": given["where"],
                                                                   "hint": "这条处置没有对上报出的图框（句柄或编号写错，或图框已在图页表里）：核对后删掉"})
        uncovered, unchecked = counts["可能漏页"] + counts["整图没有图页表"], counts["定不出范围"]
        if uncovered:
            conclusion = f"可能漏页：{uncovered} 个图框不在图页表里" + (f"；另有 {unchecked} 个图框定不出范围，没核" if unchecked else "")
        else:
            conclusion = f"{unchecked} 个图框定不出范围，没核；其余图框都在图页表里" if unchecked else "图框都在图页表里"
        if self.frame_disposed:
            conclusion += f"；{len(self.frame_disposed)} 个已写处置，不再报"
        self.frame_coverage = {"checked": True, "rules": [label for *_, label in self.frames], "frame_count": len(frames),
                               "nested_frames_skipped": len(found) - len(frames), "uncovered_frame_count": uncovered,
                               "unchecked_frame_count": unchecked, "todo_count": len(self.frame_todo),
                               "disposed": self.frame_disposed, "source_coverage_gaps": self.source_coverage_gaps,
                               "conclusion": conclusion if not self.source_coverage_gaps else conclusion + "；部分来源未核，不能证明全图覆盖"}

    @staticmethod
    def class_hits(ex, block, layer, kind, c) -> bool:
        return (ex["drawing"] in ("*", c["drawing"]) and (block is None or block.search(c["symbol"]))
                and (layer is None or layer.search(c["layer"])) and (kind is None or c["sheet_kind"] == kind))

    def class_legend_warnings(self, legend_rows: list[dict[str, Any]]) -> None:
        """An excluded class whose block name is also shown in the drawing's legend: anonymous names (A$C…, *U…) say
        nothing about use, so a name pattern may sweep real objects away (registration 20261016-1800). Hint only.
        Only hits off the legend sheets count: a rule that excludes just the legend sample sweeps nothing away. A legend row
        marked 不计 still gets the hint, since 不计 is often filled because the rule already excluded the plan blocks."""
        rows_of: dict[tuple[str, str], list[str]] = defaultdict(list)
        for r in legend_rows:
            names = set()
            if r["symbol_kind"] == "块" and r["legend_handle"]:
                names.add(base_symbol(r["symbol"]))
            names |= {base_symbol(claim_parts(e)[0]) for e in r.get("_claims") or [] if not e.startswith("@")}
            for name in names:
                rows_of[(r["drawing"], name)].append(r["legend_row"])
        for ex, block, layer, kind in self.class_rules:
            hit: dict[str, set[str]] = defaultdict(set)
            for c in self.candidates:
                if c["kind"] == "块" and c["sheet_kind"] != LEGEND_KIND and self.class_hits(ex, block, layer, kind, c):
                    hit[c["drawing"]].add(c["symbol"])
            for drawing in sorted(hit):
                for symbol in sorted(hit[drawing]):
                    rows = rows_of.get((drawing, base_symbol(symbol)))
                    if rows:
                        self.warnings["excluded_class_hits_legend_symbol"].append(
                            {"class_id": ex["class_id"], "drawing": drawing, "symbol": symbol, "legend_rows": sorted(set(rows)),
                             "hint": "图例里列了这个块，整类排除可能把实物排掉；核对后改写规则，去向不变"})

    def class_counted_warnings(self) -> None:
        """A class rule excluded plan-sheet objects whose name (xref prefix stripped) is counted elsewhere in this input:
        the rule may have swept real objects away (D014 8C5 push button box, checkpoint 1). Hint only, destination kept."""
        counted: dict[str, set[str]] = defaultdict(set)
        for c in self.candidates:
            if c["destination"] == "已计":
                counted[base_symbol(c["symbol"])].add(c["drawing"])
        class_ids = {ex["class_id"] for ex, *_ in self.class_rules}
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for c in self.candidates:
            class_id = (c["basis"] or "").split(" ", 1)[0]
            if (c["destination"] == "有据排除" and c["sheet_kind"] == PLAN_KIND and class_id in class_ids
                    and base_symbol(c["symbol"]) in counted):
                groups[(class_id, c["drawing"], c["sheet"], c["symbol"])].append(c)
        for (class_id, drawing, sheet, symbol), rows in sorted(groups.items()):
            self.warnings["excluded_class_hits_counted_symbol"].append(
                {"class_id": class_id, "drawing": drawing, "sheet": sheet, "symbol": symbol, "excluded_count": sum(c["count"] for c in rows),
                 "counted_on": sorted(counted[base_symbol(symbol)]), "sample_ids": [c["id"] for c in rows[:3]],
                 "hint": "整类排除把平面页上的这个块排掉了，同名块在本输入别处已计；核对是不是实物，去向不变"})

    def regex(self, value: Any, where: str) -> re.Pattern[str] | None:
        if value in (None, ""):
            return None
        try:
            return re.compile(str(value))
        except re.error as exc:
            self.problem("C-5", where, f"正则写错：{exc}")
            return None

    # ---------- legend coverage ----------
    def prepare_source_inventory(self, sheets):
        """Raw neutral top-level INSERT inventory supplements scoped reports for frames and legend evidence only."""
        existing = {(c["drawing"], c["id"]): c for c in self.candidates}
        for drawing, (top, _, blocks, meta) in self.neutral_sources.items():
            memo = {}
            for handle, row in top.items():
                if row.get("entity_type") != "INSERT":
                    continue
                name = str(row.get("block_name") or "")
                if "array_instances_not_expanded" in (row.get("geometry_issue_codes") or []) and (
                        self.data.get("frame_blocks") is not None or any(s["kind"] == LEGEND_KIND for s in sheets.get(drawing, []))):
                    self.source_coverage_gaps.append({"drawing": drawing,
                        "reason": f"来源块 PT-CAD-{handle} 是未展开阵列，不能用首个单元证明图框/图例全覆盖"})
                point = (row.get("x"), row.get("y"))
                x, y, basis, box = self.placed(blocks, meta, memo, name, self.matrix(row, meta), point)
                c = {"drawing": drawing, "id": f"PT-CAD-{handle}", "symbol": name, "kind": "块",
                     "layer": str(row.get("layer") or ""), "parent_block": None, "handle": handle, "top_level": True,
                     "x": x, "y": y, "insert_x": point[0], "insert_y": point[1], "placement_basis": basis, "box": box,
                     "sheet": UNSHEETED, "sheet_title": UNSHEETED, "sheet_kind": None,
                     "destination": "有据排除", "category": None, "basis": "仅供图框/图例清点的来源记录"}
                s = self.locate(sheets, drawing, x, y)
                if s:
                    c.update(sheet=s["sheet_id"], sheet_title=s["title"], sheet_kind=s["kind"])
                self.source_inventory[(drawing, c["id"])] = c
                target = existing.get((drawing, c["id"]))
                if target and self.sources.get(drawing) == "reports" and not target.get("box") and target.get("placement_basis") != GUARD_BASIS:
                    target.update({k: c[k] for k in ("x", "y", "box", "placement_basis")})

    @staticmethod
    def legend_label(child, entry):
        attrs = child.get("attributes") or {}
        if isinstance(attrs, dict):
            if nonempty(attrs.get("A")):
                return attrs["A"].strip()
            values = [v.strip() for v in attrs.values() if nonempty(v)]
            if len(values) == 1:
                return values[0]
        x, y = child.get("x"), child.get("y")
        matches = []
        if x is not None and y is not None:
            for text in entry.get("texts", []):
                if text.get("x") is None or text.get("y") is None:
                    continue
                height = float((text.get("raw_geometry") or {}).get("height") or 0)
                if abs(text["y"]-y) <= max(height*1.5, 1e-6):
                    matches.append((abs(text["x"]-x), str(text["text"])))
        matches.sort()
        if matches and (len(matches) == 1 or matches[0][0] < matches[1][0]):
            return matches[0][1]
        return ""

    def legend_source_copies(self):
        copies = []
        inventory = dict(self.source_inventory)
        # Sources lacking a verifiable neutral inventory remain visible but cannot establish completeness.
        for c in self.candidates:
            inventory.setdefault((c["drawing"], c["id"]), c)
        for c in inventory.values():
            if c["kind"] != "块" or not c.get("top_level"):
                continue
            legend_sheets = [s for s in self.sheets.get(c["drawing"], []) if s["kind"] == LEGEND_KIND]
            if not legend_sheets:
                continue
            if self.is_frame(c):
                if c["sheet_kind"] == LEGEND_KIND:
                    copies.append(c)
                continue
            loaded = self.neutral_sources.get(c["drawing"])
            if not loaded:
                if c["sheet_kind"] == LEGEND_KIND:
                    copies.append(c)
                continue
            top, _, blocks, meta = loaded
            row = top.get(c["handle"])
            entry = blocks.get(c["symbol"], {})
            children = entry.get("inserts", [])
            if not children or (len(children) == 1 and not entry.get("texts") and not children[0].get("attributes")):
                descendant = str(children[0].get("block_name") or "") if children else ""
                seen = set()
                while descendant and descendant not in seen:
                    seen.add(descendant)
                    nested = blocks.get(descendant, {}).get("inserts", [])
                    if len(nested) > 1:
                        box = c.get("box")
                        possible = [s for s in legend_sheets if box is None or not (
                            box[2] < s["bounds"][0] or box[0] > s["bounds"][2] or
                            box[3] < s["bounds"][1] or box[1] > s["bounds"][3])]
                        for possible_sheet in possible:
                            self.legend_inventory_gaps.append({"drawing": c["drawing"], "sheet": possible_sheet["sheet_id"],
                                "reason": f"图例 {c['id']} 的多个符号藏在更深容器中，第一层不能证明逐行覆盖"})
                        break
                    descendant = str(nested[0].get("block_name") or "") if nested else ""
                if c["sheet_kind"] == LEGEND_KIND:
                    copies.append({**c, "label_text": self.legend_label(row or {}, {})})
                continue
            matrix = self.matrix(row, meta) if row else None
            if matrix is None:
                self.legend_inventory_gaps.append({"drawing": c["drawing"], "sheet": c["sheet"],
                    "reason": f"图例容器 {c['id']} 缺少有效变换，第一层图例行未完整读取"})
                continue
            memo = {}
            for child in children:
                handle, name = str(child.get("entity_handle") or ""), str(child.get("block_name") or "")
                child_matrix = self.matrix(child, meta)
                if not handle or child_matrix is None or child.get("x") is None or child.get("y") is None:
                    self.legend_inventory_gaps.append({"drawing": c["drawing"], "sheet": c["sheet"],
                        "reason": f"图例容器 {c['id']} 内有缺少身份或变换的子块"})
                    continue
                world = cad_geometry.compose_matrix(matrix, child_matrix)
                point = cad_geometry.transform_point([child["x"], child["y"], child.get("z") or 0.], matrix)
                x, y, basis, box = self.placed(blocks, meta, memo, name, world, point)
                child_sheet = self.locate(self.sheets, c["drawing"], x, y)
                if child_sheet is None or child_sheet["kind"] != LEGEND_KIND:
                    continue
                if "array_instances_not_expanded" in (child.get("geometry_issue_codes") or []):
                    self.legend_inventory_gaps.append({"drawing": c["drawing"], "sheet": child_sheet["sheet_id"],
                        "reason": f"图例容器 {c['id']} 内有未逐实例展开的阵列，图例行数未核全"})
                copies.append({**c, "id": f"{c['id']}/{c['symbol']}/CAD-{handle}",
                    "handle": f"{c['id']}/{c['symbol']}/CAD-{handle}", "symbol": name,
                    "x": x, "y": y, "insert_x": point[0], "insert_y": point[1], "box": box,
                    "placement_basis": basis, "label_text": self.legend_label(child, entry), "top_level": False,
                    "sheet": child_sheet["sheet_id"], "sheet_title": child_sheet["title"], "sheet_kind": LEGEND_KIND})
        return copies

    def plan_candidates(self) -> dict[str, list[dict[str, Any]]]:
        """Block candidates that count as plan instances: not on legend or detail sheets, nor on auto-excluded kinds."""
        skip = {LEGEND_KIND, DETAIL_KIND, *self.auto_kinds}
        plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in self.candidates:
            if c["kind"] in {"块", "非块"} and c["sheet_kind"] not in skip:
                plan[c["drawing"]].append(c)
        return plan

    def legend(self) -> list[dict[str, str]]:
        auto: list[dict[str, str]] = []
        by_drawing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        frames_here: set[tuple[str, str]] = set()
        for c in self.legend_source_copies():
            if c["kind"] == "块" and c["sheet_kind"] == LEGEND_KIND:
                if self.is_frame(c):            # a frame or title block on the legend sheet is no legend row (1945)
                    self.legend_frames[c["drawing"]] += 1
                    frames_here.add((c["drawing"], (c["handle"] or "").upper()))
                    continue
                by_drawing[c["drawing"]].append(c)
        plan_of = self.plan_candidates()
        filled = self.read_legend()
        # a handle already in the filled table keeps its row number, new ones take numbers that table does not use: a
        # block that leaves or joins a legend sheet (a placement change, a re-extraction) renumbers nothing else
        kept = {(r["drawing"], r["legend_handle"].strip().upper()): r["legend_row"] for r in filled
                if r["symbol_kind"] == "块" and r["legend_handle"].strip()}
        used: dict[str, set[str]] = defaultdict(set)
        for r in filled:
            used[r["drawing"]].add(r["legend_row"])
        for drawing in sorted(by_drawing):
            # numbered by insertion point, as in 1.0, so existing filled tables keep their row numbers
            copies = sorted(by_drawing[drawing], key=lambda c: (c["sheet"], -(c["insert_y"] if c.get("insert_y") is not None else c["y"]),
                                                                c["insert_x"] if c.get("insert_x") is not None else c["x"]))
            n = 0
            for c in copies:
                number = kept.get((drawing, (c["handle"] or "").upper()))
                if number is None:
                    n += 1
                    while f"A{n:03d}" in used[drawing]:
                        n += 1
                    number = f"A{n:03d}"
                same = [p for p in plan_of[drawing] if base_symbol(p["symbol"]) == base_symbol(c["symbol"])]
                cats = {p["category"] for p in same if p["destination"] == "已计"}
                prefill = bool(same) and all(p["destination"] == "已计" for p in same) and len(cats) == 1
                auto.append({"drawing": drawing, "legend_row": number, "legend_handle": c["handle"] or "", "legend_text": c.get("label_text") or "",
                             "symbol": c["symbol"], "symbol_kind": "块", "matched_symbols": "", "plan_count": str(len(same)),
                             "count_source": "自动", "variant_flag": "", "destination": "已计" if prefill else "",
                             "target_or_reason": next(iter(cats)) if prefill else "", "suspected_category": "", "search_record_id": ""})
            rows_of = defaultdict(list)
            for row in auto:
                if row["drawing"] == drawing:
                    rows_of[base_symbol(row["symbol"])].append(row["legend_row"])
            for row in auto:
                if row["drawing"] == drawing and len(rows_of[base_symbol(row["symbol"])]) > 1:
                    row["variant_flag"] = "同块多变体（" + "、".join(rows_of[base_symbol(row["symbol"])]) + "）"
        index ={(r["drawing"], r["legend_handle"].upper()): r for r in auto}
        rows = list(auto)
        for row in filled:
            key = (row["drawing"], row["legend_handle"].strip().upper())
            if row["symbol_kind"] == "块" and key[1] and key in frames_here:
                self.legend_frame_rows_dropped.append({"drawing": row["drawing"], "legend_row": row["legend_row"], "legend_handle": row["legend_handle"]})
                continue
            if row["symbol_kind"] == "块" and key[1] and key in index:
                target = index[key]
                for field in FILLED:
                    if row[field].strip():
                        target[field] = row[field].strip()
                target["_filled_count"] = row["plan_count"].strip()
                continue
            if row["symbol_kind"] == "块" and key[1]:
                self.warnings["legend_handle_not_on_legend_sheet"].append({"drawing": row["drawing"], "legend_row": row["legend_row"],
                                                                           "legend_handle": row["legend_handle"]})
            rows.append({**row, "count_source": "手填"})
        seen: dict[tuple[str, str], int] = Counter((r["drawing"], r["legend_row"]) for r in rows)
        for (drawing, number), times in seen.items():
            if times > 1:
                self.problem("C-6", "图例覆盖表", f"{drawing} 的图例行号重复：{number}")
        self.match_legend(rows, plan_of)
        for row in rows:
            filled_count = row.pop("_filled_count", "")
            if filled_count and filled_count != row["plan_count"]:
                self.warnings["plan_count_conflicts"].append({"drawing": row["drawing"], "legend_row": row["legend_row"],
                                                              "filled": filled_count, "automatic": row["plan_count"]})
        for drawing in self.data.get("drawings") or []:
            if not isinstance(drawing, dict):
                continue
            name = drawing.get("drawing")
            legend_sheets = [s for s in getattr(self, "sheets", {}).get(name, []) if s["kind"] == LEGEND_KIND]
            for sheet in legend_sheets:
                hand_rows = len(legend_sheets) == 1 and any(r["drawing"] == name for r in filled)
                if not hand_rows and not any(c["sheet"] == sheet["sheet_id"] for c in by_drawing.get(name, [])):
                    gap = {"drawing": name, "sheet": sheet["sheet_id"],
                           "reason": "图例页没有可列的图例行（可能在容器块内或缺少来源），图例逐行覆盖未核"}
                    self.legend_inventory_gaps.append(gap)
                    self.warnings["legend_sheets_without_rows"].append(gap)
        return rows

    def match_legend(self, rows: list[dict[str, Any]], plan_of: dict[str, list[dict[str, Any]]]) -> None:
        """matched_symbols: block names (bound-xref prefixes ignored) or @recognition-record ids the model writes for a
        legend row whose plan blocks have another name (registration 20261016-1800). A 已计 block row whose category
        is the target of no other 已计 row gets the counted block names of that category as default (one-to-one only).
        A block claimed by two rows, explicitly on at least one side, is a conflict and keeps both rows pending."""
        names_of: dict[str, set[str]] = defaultdict(set)
        records_of: dict[str, set[str]] = defaultdict(set)
        categories_of: dict[tuple[str, str], set[str]] = defaultdict(set)
        for c in self.candidates:
            if c["kind"] in {"块", "非块"}:
                names_of[c["drawing"]] |= {c["symbol"], base_symbol(c["symbol"])}
                if c.get("adjudication_id"):
                    records_of[c["drawing"]].add(str(c["adjudication_id"]))
                if c.get("category"):
                    categories_of[(c["drawing"], base_symbol(c["symbol"]))].add(c["category"])
        for r in rows:
            r["_claims"], r["_explicit"] = None, False
            entries = [e.strip() for e in LIST_SPLIT_RE.split(r.get("matched_symbols") or "") if e.strip()]
            if not entries:
                continue
            bad, wrong = [], []
            for e in entries:
                if e.startswith("@"):
                    if e[1:] not in records_of[r["drawing"]]:
                        bad.append(e)
                    continue
                name, category = claim_parts(e)
                if name not in names_of[r["drawing"]] and base_symbol(name) not in names_of[r["drawing"]]:
                    bad.append(e)
                elif category is not None and category not in categories_of[(r["drawing"], base_symbol(name))]:
                    wrong.append(e)
            where = f"图例覆盖表 {r['drawing']} {r['legend_row']}"
            if bad:
                self.problem("C-6", where, "matched_symbols 写了本图没有的块名或识别记录编号（识别记录写 @编号）：" + "、".join(bad))
                continue
            if wrong:
                self.problem("C-6", where, "matched_symbols 按类别认领（块名::类别），这个块名下没有该类别的实例（类别取计数表或识别记录）："
                             + "、".join(wrong)); continue
            r["_claims"], r["_explicit"], r["count_source"] = sorted(set(entries)), True, "matched_symbols"
        targets = Counter((r["drawing"], r["target_or_reason"]) for r in rows if r["destination"] == "已计" and r["target_or_reason"])
        for r in rows:
            if (r["_claims"] is None and r["symbol_kind"] == "块" and r["legend_handle"] and r["destination"] == "已计"
                    and r["target_or_reason"] and targets[(r["drawing"], r["target_or_reason"])] == 1):
                names = sorted({p["symbol"] for p in plan_of[r["drawing"]]
                                if p["kind"] == "块" and p["destination"] == "已计" and
                                p["category"] == r["target_or_reason"]} - {r["symbol"]})
                if names:
                    r["_claims"], r["matched_symbols"], r["count_source"] = names, "；".join(names), "类别一对一默认"
        # block::category claims split one block name's instances between rows by category (1945); a row claiming its
        # own legend block by category no longer claims all of that block. Plain and by-category claims of one block
        # name on different rows (the row's own legend block counts as plain) are a conflict, as two plain explicit ones.
        owners: dict[tuple[str, str], list[tuple[str, bool]]] = defaultdict(list)
        plain: dict[tuple[str, str], set[str]] = defaultdict(set)
        by_category: dict[tuple[str, str], set[str]] = defaultdict(set)
        for r in rows:
            d, keys = r["drawing"], set()
            claims = [(e, None) if e.startswith("@") else claim_parts(e) for e in r["_claims"] or []]
            split = {base_symbol(name) for name, category in claims if category is not None}
            own = base_symbol(r["symbol"]) if r["symbol_kind"] == "块" and r["legend_handle"] else None
            if own is not None and own not in split:
                owners[(d, own)].append((r["legend_row"], False))
                keys.add(own); plain[(d, own)].add(r["legend_row"])
            for name, category in claims:
                if name.startswith("@"):
                    key = name
                else:
                    key = base_symbol(name) if category is None else f"{base_symbol(name)}{CLAIM_SEP}{category}"
                    (plain if category is None else by_category)[(d, base_symbol(name))].add(r["legend_row"])
                if key not in keys:
                    owners[(d, key)].append((r["legend_row"], True))
                    keys.add(key)
            if r["_claims"] is not None:
                wanted = {base_symbol(name) for name, category in claims if category is None and not name.startswith("@")}
                if own is not None and own not in split:
                    wanted.add(own)
                wanted_category = {(base_symbol(name), category) for name, category in claims if category is not None}
                records = {name[1:] for name, _ in claims if name.startswith("@")}
                hits = [p for p in plan_of[d] if base_symbol(p["symbol"]) in wanted or (base_symbol(p["symbol"]), p["category"]) in wanted_category
                        or str(p.get("adjudication_id") or "") in records]
                r["plan_count"] = str(len(hits))
        by_row = {(r["drawing"], r["legend_row"]): r for r in rows}
        for (drawing, key), claimants in sorted(owners.items()):
            if len({row for row, _ in claimants}) > 1 and any(explicit for _, explicit in claimants):
                numbers = sorted({row for row, _ in claimants})
                self.warnings["matched_symbol_conflicts"].append({"drawing": drawing, "symbol": key, "legend_rows": numbers})
                for number in numbers:
                    by_row[(drawing, number)]["_conflict"] = f"matched_symbols 冲突：{key} 同时对应 {'、'.join(numbers)}"
        for (drawing, key), split_rows in sorted(by_category.items()):
            numbers = sorted(split_rows | plain.get((drawing, key), set()))
            if plain.get((drawing, key)) and len(numbers) > 1:
                self.warnings["matched_symbol_conflicts"].append({"drawing": drawing, "symbol": key, "legend_rows": numbers,
                                                                  "detail": "一行按类别认领、另一行没写类别"})
                for number in numbers:
                    by_row[(drawing, number)]["_conflict"] = (f"matched_symbols 冲突：{key} 在 {'、'.join(numbers)} 里有的按类别认领、"
                                                              "有的没写类别；同名块分给几行时每行都写 块名::类别")

    def read_search_records(self) -> dict[str, dict[str, Any]] | None:
        """search_records: search_negative_evidence.py outputs (one path or a list). None when not given."""
        value = self.data.get("search_records")
        if value in (None, "", []):
            return None
        paths = value if isinstance(value, list) else [value]
        records: dict[str, dict[str, Any]] = {}
        for item in paths:
            path = self.path(str(item))
            if not path.is_file():
                self.problem("C-7", str(item), "检索记录文件不存在"); continue
            try:
                data = load_json(path)
            except ValueError as exc:
                self.problem("C-7", str(path), f"检索记录文件读不了：{exc}"); continue
            if not isinstance(data, dict) or data.get("schema") != SEARCH_SCHEMA or not isinstance(data.get("records"), list):
                self.problem("C-7", str(path), f"须是 {SEARCH_SCHEMA}（search_negative_evidence.py 的输出），records 是数组"); continue
            self.files.append({"role": "search_records", "path": str(path), "sha256": sha256(path)})
            for n, rec in enumerate(data["records"]):
                rid = rec.get("record_id") if isinstance(rec, dict) else None
                if not nonempty(rid):
                    self.problem("C-7", f"{path.name} records[{n}]", "缺少 record_id"); continue
                if rid in records:
                    self.problem("C-7", f"{path.name} records[{n}]", f"检索记录编号重复：{rid}"); continue
                records[rid] = rec
        return records

    def search_issue(self, r: dict[str, str], records: dict[str, dict[str, Any]] | None) -> str | None:
        """Why a zero-instance row's search records do not confirm the zero; None when they do."""
        ids = [s for s in re.split(r"[；;,，\s]+", r["search_record_id"]) if s]
        if records is None:
            self.warnings["search_record_ids_not_checked"].append(
                {"drawing": r["drawing"], "legend_row": r["legend_row"], "search_record_id": r["search_record_id"]})
            return None
        for rid in ids:
            rec = records.get(rid)
            if rec is None:
                return f"检索记录 {rid} 不在 search_records 里"
            problems = record_problems(rec)
            if problems:
                return f"检索记录 {rid}{problems[0]}"
            if rec["status"] == "hit":
                return f"检索记录 {rid} 有命中 {rec['result']['hits']} 处，实例数为 0 不成立"
            if rec["status"] == "no_hit_partial":
                return f"检索记录 {rid} 为待确认（有未覆盖的图或可能含图面内容的未读对象），实例数为 0 未确认"
        return None

    def read_legend(self) -> list[dict[str, str]]:
        value = self.data.get("legend_coverage")
        if value in (None, ""):
            return []
        path = self.path(str(value))
        if not path.is_file():
            self.problem("C-6", str(value), "图例覆盖表不存在"); return []
        self.files.append({"role": "legend_coverage", "path": str(path), "sha256": sha256(path)})
        rows = []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [k for k in ("drawing", "legend_row", "symbol_kind") if k not in (reader.fieldnames or [])]
            if missing:
                self.problem("C-6", str(path), "图例覆盖表缺列：" + "、".join(missing)); return []
            for n, raw in enumerate(reader, start=2):
                row = {k: (raw.get(k) or "").strip() for k in LEGEND_COLUMNS}
                where = f"{path.name} 第 {n} 行"
                if row["symbol_kind"] not in ("块", "非块") or not row["drawing"] or not row["legend_row"]:
                    self.problem("C-6", where, "须写 drawing、legend_row，symbol_kind 只能是 块 或 非块"); continue
                if row["destination"] not in LEGEND_DESTINATIONS:
                    self.problem("C-6", where, "destination 只能是 " + "、".join(d for d in LEGEND_DESTINATIONS if d) + "，或留空"); continue
                if row["symbol_kind"] == "非块" and not row["matched_symbols"] and not re.fullmatch(r"\d+", row["plan_count"]):
                    self.problem("C-6", where, "非块行须手填 plan_count（非负整数），或写 matched_symbols 由脚本按块数"); continue
                rows.append(row)
        return rows

    def legend_to_ledger(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Non-block rows become candidates; rows without a destination are returned."""
        pending = []
        suspected: dict[tuple[str, str], str] = {}
        for r in rows:
            if r["suspected_category"] and (r["symbol_kind"] == "块" or r.get("_claims")):
                names = {base_symbol(claim_parts(e)[0]) for e in r.get("_claims") or [] if not e.startswith("@")}
                if r["symbol_kind"] == "块":
                    names.add(base_symbol(r["symbol"]))
                for name in names:
                    suspected[(r["drawing"], name)] = r["suspected_category"]
        for c in self.candidates:
            if c["destination"] == "待识别" and (c["drawing"], base_symbol(c["symbol"])) in suspected:
                c["suspected_category"] = suspected[(c["drawing"], base_symbol(c["symbol"]))]
        for r in rows:
            if r["symbol_kind"] == "块" and not r["legend_handle"]:
                continue   # a hand-added block row only carries a suspected class; it is not a legend row
            count = int(r["plan_count"]) if re.fullmatch(r"\d+", r["plan_count"]) else 0
            unconfirmed = count == 0 and not r["search_record_id"]   # every destination, not only 不计 (cross-check B4A-B1-01)
            issue = self.search_issue(r, self.search_records) if count == 0 and r["search_record_id"] else None
            physical_nonblocks = [c for c in self.candidates if c["drawing"] == r["drawing"] and c["kind"] == "非块"
                and (c["symbol"] == r["symbol"] or (c["category"] and c["category"] == r["target_or_reason"]))]
            if r["symbol_kind"] == "非块" and count > 0 and not r.get("_claims") and physical_nonblocks:
                r["_conflict"] = "本图已有同类非块唯一实例，须用 matched_symbols 或 @识别记录认领，避免图例汇总重复列候选"
            if not r["destination"] or unconfirmed or issue or r.get("_conflict"):
                reason = (r["_conflict"] if r.get("_conflict") else
                          "实例数为 0、没有检索记录（待确认）" if unconfirmed and r["destination"] else
                          issue if issue and r["destination"] else "去向为空")
                pending.append({"drawing": r["drawing"], "legend_row": r["legend_row"], "symbol": r["symbol"], "reason": reason})
            if r["symbol_kind"] != "非块" or count == 0 or r.get("_claims") or physical_nonblocks:
                continue   # a non-block row matched to blocks: those blocks are already candidates
            dest = {"已计": "已计", "归他专业": "有据排除", "不计": "有据排除", "待识别": "待识别", "": "无去向"}[r["destination"]]
            basis = {"已计": "", "归他专业": f"归他专业：{r['target_or_reason']}", "不计": f"不计：{r['target_or_reason']}",
                     "待识别": "图例行待识别", "": f"图例行 {r['legend_row']} 有实例、无去向"}[r["destination"]]
            self.add(r["drawing"], f"LEGEND-{r['drawing']}-{r['legend_row']}", count=count, symbol=r["symbol"], kind="非块",
                     destination=dest, category=r["target_or_reason"] if dest == "已计" else None, basis=basis,
                     suspected_category=r["suspected_category"] or None)
            self.candidates[-1].update({"sheet": UNSHEETED, "sheet_title": f"图例行 {r['legend_row']}", "sheet_kind": None})
        return pending

    # ---------- run ----------
    def run(self) -> dict[str, Any]:
        data = self.data
        if data.get("schema_version") != SCHEMA:
            raise InputProblems([{"check": "C-0", "where": "schema_version", "message": f"须为 {SCHEMA}"}])
        unknown = sorted(set(data) - TOP_FIELDS)
        if unknown:
            self.problem("C-0", "顶层", "不认识的字段：" + "、".join(unknown))
        drawings: dict[str, str] = {}
        specs = data.get("drawings")
        if not isinstance(specs, list) or not specs:
            raise InputProblems([{"check": "C-0", "where": "drawings", "message": "drawings 须是非空数组"}])
        for i, d in enumerate(specs):
            where = f"drawings[{i}]"
            if not isinstance(d, dict) or not nonempty(d.get("drawing")):
                self.problem("C-0", where, "每张图纸须写 drawing"); continue
            if d["drawing"] in drawings:
                self.problem("C-0", where, f"图纸重复：{d['drawing']}"); continue
            if d.get("reports") and d.get("neutral_dir"):
                self.problem("C-0", where, f"{d['drawing']} 同时写了 reports 和 neutral_dir，先定以哪份为准"); continue
            expand = d.get("expand_blocks")
            if expand is not None:
                if not d.get("neutral_dir"):
                    self.problem("C-0", where, "expand_blocks 只用于“中性清单 + 计数表”来源；适配器报告已逐级展开"); continue
                if not isinstance(expand, list) or not expand or any(not nonempty(v) for v in expand):
                    self.problem("C-0", where, "expand_blocks 须是非空的精确块名数组"); continue
            drawings[d["drawing"]] = "reports" if d.get("reports") else ("tables" if d.get("neutral_dir") else "legend_only")
        counted = self.table_rows("count_tables", COUNT_KEYS, ("drawing", "handle", "category"), drawings)
        excluded = self.table_rows("handle_exclusions", EXCLUSION_KEYS, ("drawing", "handle", "basis"), drawings)
        for drawing, rows in excluded.items():
            for handle_id, row in rows.items():
                if handle_id in counted.get(drawing, {}):
                    self.problem("C-3", row["_where"], f"同一句柄既在计数表里又在排除表里：{handle_id}")
        for d in specs:
            if not isinstance(d, dict) or drawings.get(d.get("drawing")) is None:
                continue
            kind = drawings[d["drawing"]]
            self.sources[d["drawing"]] = kind
            if kind == "reports":
                self.from_reports(d["drawing"], d["reports"] if isinstance(d["reports"], list) else [d["reports"]])
            elif kind == "tables":
                self.from_tables(d["drawing"], self.path(str(d["neutral_dir"])), counted.get(d["drawing"], {}),
                                 excluded.get(d["drawing"], {}), {str(v).strip() for v in d.get("expand_blocks") or []})
        sheets = self.load_sheets()
        self.sheets = sheets
        self.prepare_source_inventory(sheets)
        self.place(sheets)
        self.apply_rules(self.sheet_kind_rules(sheets, drawings))
        self.apply_project_corrections(excluded)
        self.frame_rules()
        self.frame_disposition_rules()
        self.check_frames(sheets, drawings)
        self.unexpanded_warnings()
        self.search_records = self.read_search_records()
        legend_rows = self.legend()
        if self.problems:
            raise InputProblems(self.problems)
        self.class_legend_warnings(legend_rows)
        pending = self.legend_to_ledger(legend_rows)
        self.class_counted_warnings()   # after legend rows: non-block rows counted from the legend count too
        for row in legend_rows:
            for key in ("_claims", "_explicit", "_conflict"):
                row.pop(key, None)
        legend_drawings = {r["drawing"] for r in legend_rows}
        uncovered = sorted(d for d, k in drawings.items() if k == "legend_only" and d not in legend_drawings)
        return self.summarize(drawings, legend_rows, pending, uncovered)

    def summarize(self, drawings, legend_rows, pending, uncovered) -> dict[str, Any]:
        by_dest = Counter()
        for c in self.candidates:
            by_dest[c["destination"]] += c["count"]
        total = sum(c["count"] for c in self.candidates)
        if sum(by_dest.values()) != total or set(by_dest) - set(DESTINATIONS):
            raise RuntimeError("覆盖台账去向不守恒")
        ids = [(c["drawing"], c["id"]) for c in self.candidates]   # handles repeat across drawings
        if len(ids) != len(set(ids)):
            raise RuntimeError("覆盖台账里有候选出现两次")
        by_drawing = {}
        for drawing, kind in drawings.items():
            counts = Counter()
            for c in self.candidates:
                if c["drawing"] == drawing:
                    counts[c["destination"]] += c["count"]
            by_drawing[drawing] = {"source": kind, **{d: counts.get(d, 0) for d in DESTINATIONS}}
        groups = defaultdict(lambda: {"count": 0, "sample_ids": []})
        for c in self.candidates:
            label = c["category"] if c["destination"] == "已计" else c["basis"]
            key = (c["drawing"], c["sheet"], c["sheet_title"], c["symbol"], c["kind"], c["layer"], c.get("parent_block") or "",
                   c["destination"], label or "", c.get("suspected_category") or "")
            g = groups[key]; g["count"] += c["count"]
            if len(g["sample_ids"]) < 3:
                g["sample_ids"].append(c["id"])
        group_rows = [dict(zip(LEDGER_COLUMNS[:8], key[:8]), count=g["count"], category_or_basis=key[8],
                           suspected_category=key[9], sample_ids=g["sample_ids"]) for key, g in groups.items()]
        order = {d: i for i, d in enumerate(("无去向", "待识别", "版本冲突", "有据排除", "已计"))}
        group_rows.sort(key=lambda r: (r["drawing"], order[r["destination"]], r["sheet"], -r["count"], r["symbol"]))
        placement = Counter(c.get("placement_basis") or "无坐标" for c in self.candidates if c["kind"] in ("块", "非块"))
        return {"by_destination": {d: by_dest.get(d, 0) for d in DESTINATIONS}, "candidate_total": total,
                "by_drawing": by_drawing, "uncovered_drawings": uncovered, "legend_rows_without_destination": pending,
                "source_coverage_gaps": self.source_coverage_gaps, "legend_inventory_gaps": self.legend_inventory_gaps,
                "project_corrections": self.project_corrections,
                "adapter_excluded_project_counted": sum(r["project_destination"] == "已计" and
                    r["original_adapter"].get("disposition") in {"excluded", "not_applicable"} for r in self.project_corrections),
                "class_completeness": self.completeness(),
                "auto_exclude_sheet_kinds": self.auto_kinds,
                "auto_excluded_by_sheet_kind": dict(sorted(self.auto_excluded.items())),
                "unconfirmed_detail_sheets": [{"drawing": d, "sheet": s, "pending_count": n}
                                              for (d, s), n in sorted(self.unconfirmed_detail.items())],
                "expanded_containers": {d: dict(sorted(n.items())) for d, n in sorted(self.expanded.items()) if n},
                "placement_basis_counts": dict(sorted(placement.items())),
                "search_records": {"given": self.search_records is not None, "records": len(self.search_records or {})},
                "frame_coverage": {**self.frame_coverage, "legend_frames_skipped": dict(sorted(self.legend_frames.items())),
                                   "legend_frame_rows_dropped": self.legend_frame_rows_dropped},
                "frame_todo": self.frame_todo, "groups": group_rows, "legend_rows": legend_rows}

    def completeness(self) -> dict[str, Any]:
        counted_on = defaultdict(set)
        for c in self.candidates:
            if c["destination"] == "已计" and c["category"]:
                counted_on[c["category"]].add(c["drawing"])
        unknown = Counter()
        suspected = defaultdict(Counter)
        for c in self.candidates:
            if c["destination"] in ("待识别", "无去向"):
                if c.get("suspected_category"):
                    suspected[c["suspected_category"]][(c["drawing"], c["destination"])] += c["count"]
                else:
                    unknown[c["drawing"]] += c["count"]
        out = {}
        for category in sorted(set(counted_on) | set(suspected)):
            reasons = [f"{d} {dest} {n} 个（疑似本类）" for (d, dest), n in sorted(suspected[category].items())]
            reasons += [f"{d} 类别未知的待识别或无去向 {unknown[d]} 个" for d in sorted(counted_on[category]) if unknown[d]]
            out[category] = {"complete": not reasons, "reasons": reasons}
        return out


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("；".join(v) if isinstance(v, list) else v) for k, v in row.items()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="点位覆盖台账：每个候选、每行图例都要有去向")
    parser.add_argument("--input", required=True, type=Path, help="qto-point-coverage-input/1.0")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        input_path = args.input.resolve()
        ledger = Ledger(load_json(input_path), input_path.parent)
        result = ledger.run()
    except InputProblems as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "problems": exc.problems}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "problems": [{"check": "C-0", "where": "输入", "message": str(exc)}]},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    complete = (not result["by_destination"]["待识别"] and not result["by_destination"]["无去向"]
                and not result["uncovered_drawings"] and not result["legend_rows_without_destination"]
                and not result["source_coverage_gaps"] and not result["legend_inventory_gaps"] and not result["frame_todo"])
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    legend_rows = result.pop("legend_rows")
    report = {"schema_version": LEDGER_SCHEMA, "script_version": VERSION, "scope_id": ledger.data.get("scope_id"),
              "input": {"path": str(input_path), "sha256": sha256(input_path)}, "status": "COMPLETE" if complete else "INCOMPLETE",
              "coverage_complete": complete, **{k: v for k, v in result.items() if k != "groups"},
              "warnings": dict(ledger.warnings), "groups": result["groups"], "files": ledger.files,
              "meaning": "台账只看每个候选和图例行有没有去向；不计算工程量，不判断类别是否正确，完整不等于人工复核完成"}
    (out / "点位覆盖台账.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out / "点位覆盖台账.csv", LEDGER_COLUMNS, result["groups"])
    write_csv(out / "图例覆盖表_待填.csv", LEGEND_COLUMNS, legend_rows)
    write_csv(out / "图框待办.csv", FRAME_TODO_COLUMNS, result["frame_todo"])
    print(json.dumps({"status": report["status"], "candidate_total": result["candidate_total"], **result["by_destination"],
                      "uncovered_drawings": len(result["uncovered_drawings"]),
                      "legend_rows_without_destination": len(result["legend_rows_without_destination"]),
                      "frames": result["frame_coverage"]["conclusion"], "ledger": str(out / "点位覆盖台账.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
