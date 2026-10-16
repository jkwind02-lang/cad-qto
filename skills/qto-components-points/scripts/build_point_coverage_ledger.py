#!/usr/bin/env python3
"""Point coverage ledger: every candidate on the drawing set and every legend row gets a destination.

Contract: docs/维护/点位覆盖台账契约.md (qto-point-coverage-input/1.0). Candidates come from adapter reports
(every scoped instance with its disposition) or from a neutral layout plus project count tables (every top-level
INSERT; counted when its handle is in a count table). The script never counts a quantity, guesses a category or
drops a candidate: anything without a destination stays visible and keeps coverage incomplete.
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

SCHEMA = "qto-point-coverage-input/1.0"
LEDGER_SCHEMA = "qto-point-coverage-ledger/1.0"
VERSION = "qto-point-coverage-ledger/1.0.0"
DESTINATIONS = ("已计", "有据排除", "待识别", "版本冲突", "无去向")
DISPOSITION_DEST = {"adopted": "已计", "merged": "已计", "excluded": "有据排除", "not_applicable": "有据排除",
                    "duplicate_excluded": "有据排除", "unresolved": "待识别"}
LEGEND_COLUMNS = ("drawing", "legend_row", "legend_handle", "legend_text", "symbol", "symbol_kind", "plan_count",
                  "count_source", "variant_flag", "destination", "target_or_reason", "suspected_category", "search_record_id")
FILLED = ("legend_text", "destination", "target_or_reason", "suspected_category", "search_record_id")
LEGEND_DESTINATIONS = ("已计", "归他专业", "不计", "待识别", "")
LEDGER_COLUMNS = ("drawing", "sheet", "sheet_title", "symbol", "symbol_kind", "layer", "parent_block", "destination",
                  "count", "category_or_basis", "suspected_category", "sample_ids")
COPY_KINDS = {"图例", "详图"}
UNSHEETED = "图页未定"
COUNT_KEYS = ("drawing", "handle", "category", "sheet")
EXCLUSION_KEYS = ("drawing", "handle", "basis")
TOP_FIELDS = {"schema_version", "scope_id", "drawings", "count_tables", "handle_exclusions", "sheets", "legend_coverage",
              "version_conflicts", "excluded_classes"}
HANDLE_RE = re.compile(r'"entity_handle":\s*"([^"]*)"')


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
        self.sources: dict[str, str] = {}

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
                top = "/" not in pid
                self.add(drawing, pid, symbol=str(row.get("block_name") or ""), layer=str(row.get("layer") or ""),
                         parent_block=row.get("parent_block"), destination=dest, category=category, basis=basis,
                         x=point[0] / factor if point[0] is not None else None, y=point[1] / factor if point[1] is not None else None,
                         handle=pid[len("PT-CAD-"):].upper() if top and pid.startswith("PT-CAD-") else None, top_level=top)
            for oid in report.get("unmatched_recognition_object_ids") or []:
                self.add(drawing, f"{oid}#未承接", symbol=str(oid), kind="未承接对象", destination="无去向",
                         basis="模型识别台账列了、适配器没有承接")

    # ---------- source 2: neutral layout + project count tables ----------
    def layout(self, drawing: str, neutral_dir: Path) -> tuple[dict[str, dict[str, Any]], set[str]] | None:
        manifest_path = neutral_dir / "CAD中性取证清单.json"
        if not manifest_path.is_file():
            self.problem("C-2", str(neutral_dir), "中性取证目录里没有 CAD中性取证清单.json"); return None
        artifacts = load_json(manifest_path).get("artifacts") or {}
        paths = {}
        for key in ("layout_entities", "block_definitions"):
            artifact = artifacts.get(key) or {}
            raw = Path(str(artifact.get("path") or ""))
            paths[key] = raw if raw.is_absolute() else neutral_dir / raw
            if not paths[key].is_file() or sha256(paths[key]) != str(artifact.get("sha256") or "").lower():
                self.problem("C-2", str(manifest_path), f"中性 {key} 缺失或 SHA256 与清单不一致"); return None
            self.files.append({"role": f"neutral_{key}", "drawing": drawing, "path": str(paths[key]), "sha256": artifact.get("sha256")})
        top: dict[str, dict[str, Any]] = {}
        in_block: set[str] = set()
        with paths["block_definitions"].open(encoding="utf-8") as handle:   # handles only: a counted row may point inside a block
            for line in handle:
                match = HANDLE_RE.search(line)
                if match:
                    in_block.add(match.group(1).upper())
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
        return top, in_block

    def from_tables(self, drawing: str, neutral_dir: Path, counted: dict, excluded: dict) -> None:
        loaded = self.layout(drawing, neutral_dir)
        if loaded is None:
            return
        top, in_block = loaded
        for table, rows in (("count_tables", counted), ("handle_exclusions", excluded)):
            for handle_id, row in rows.items():
                if handle_id not in top:
                    why = "句柄是块内图元，不能作候选" if handle_id in in_block else "句柄在中性布局里查不到"
                    self.problem("C-3", row["_where"], f"{why}：{handle_id}")
        for handle_id, row in sorted(top.items()):
            is_insert = str(row.get("entity_type") or "").upper() == "INSERT"
            if not is_insert and handle_id not in counted:
                continue
            x, y = row.get("x"), row.get("y")
            if (x is None or y is None) and row.get("bbox"):
                box = row["bbox"]; x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            fields = {"symbol": str(row.get("block_name") or "") if is_insert else f"<{row.get('entity_type')}>",
                      "kind": "块" if is_insert else "非块", "layer": str(row.get("layer") or ""), "x": x, "y": y,
                      "handle": handle_id, "top_level": True}
            if handle_id in counted:
                c = counted[handle_id]
                self.add(drawing, f"PT-CAD-{handle_id}", destination="已计", category=c["category"], table_sheet=c.get("sheet"),
                         basis=c["_where"], **fields)
            elif handle_id in excluded:
                self.add(drawing, f"PT-CAD-{handle_id}", destination="有据排除", basis=excluded[handle_id]["basis"], **fields)
            else:
                self.add(drawing, f"PT-CAD-{handle_id}", destination="待识别", basis="不在计数表和排除表里", **fields)

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
                drawing, handle_id = row["drawing"], row["handle"].strip().upper()
                if drawings.get(drawing) != "tables":
                    self.problem("C-3", row["_where"], f"图纸 {drawing} 不在 drawings 里，或不是“中性清单 + 计数表”来源"); continue
                prev = out[drawing].get(handle_id)
                if prev is not None:
                    self.problem("C-3", row["_where"], f"同一句柄在{key}里出现两次：{handle_id}（另见 {prev['_where']}）"); continue
                out[drawing][handle_id] = row
        return out

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

    def place(self, sheets: dict[str, list[dict[str, Any]]]) -> None:
        for c in self.candidates:
            c["sheet"], c["sheet_title"], c["sheet_kind"] = UNSHEETED, UNSHEETED, None
            if c["x"] is None or c["y"] is None:
                continue
            hits = [s for s in sheets.get(c["drawing"], []) if s["bounds"][0] <= c["x"] <= s["bounds"][2] and s["bounds"][1] <= c["y"] <= s["bounds"][3]]
            if hits:
                s = min(hits, key=lambda item: item["area"])
                c["sheet"], c["sheet_title"], c["sheet_kind"] = s["sheet_id"], s["title"], s["kind"]
            if c.get("table_sheet") and c["table_sheet"] != c["sheet"]:
                self.warnings["sheet_mismatches"].append({"id": c["id"], "table_sheet": c["table_sheet"], "placed_sheet": c["sheet"]})

    def apply_rules(self) -> None:
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
            rules.append((ex, block, layer))
        for c in self.candidates:
            if c["kind"] not in ("块", "非块"):
                continue
            for vc, ids, pattern in compiled:
                if vc["drawing"] in ("*", c["drawing"]) and (c["id"] in ids or (pattern and pattern.search(c["symbol"]))):
                    c["destination"], c["basis"], c["category"] = "版本冲突", f"{vc['conflict_id']} {vc['basis']}", None
                    break
            if c["destination"] == "版本冲突":
                continue
            if c["sheet_kind"] in COPY_KINDS:
                if c["destination"] == "待识别":
                    c["destination"], c["basis"] = "有据排除", f"{c['sheet_kind']}页上的复制件（A10）"
                elif c["destination"] == "已计":
                    self.warnings["counted_on_legend_or_detail_sheet"].append({"id": c["id"], "sheet": c["sheet"], "symbol": c["symbol"]})
            for ex, block, layer in rules:
                if ex["drawing"] not in ("*", c["drawing"]):
                    continue
                if (block is None or block.search(c["symbol"])) and (layer is None or layer.search(c["layer"])):
                    if c["destination"] == "待识别":
                        c["destination"], c["basis"] = "有据排除", f"{ex['class_id']} {ex['basis']}"
                    elif c["destination"] == "已计":
                        self.warnings["counted_matches_excluded_class"].append({"id": c["id"], "class_id": ex["class_id"]})
                    break

    def regex(self, value: Any, where: str) -> re.Pattern[str] | None:
        if value in (None, ""):
            return None
        try:
            return re.compile(str(value))
        except re.error as exc:
            self.problem("C-5", where, f"正则写错：{exc}")
            return None

    # ---------- legend coverage ----------
    def legend(self) -> list[dict[str, str]]:
        auto: list[dict[str, str]] = []
        by_drawing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in self.candidates:
            if c["kind"] == "块" and c.get("top_level") and c["sheet_kind"] == "图例":
                by_drawing[c["drawing"]].append(c)
        for drawing in sorted(by_drawing):
            copies = sorted(by_drawing[drawing], key=lambda c: (c["sheet"], -c["y"], c["x"]))
            plan = [c for c in self.candidates if c["drawing"] == drawing and c["kind"] == "块" and c["sheet_kind"] not in COPY_KINDS]
            for n, c in enumerate(copies, start=1):
                same = [p for p in plan if p["symbol"] == c["symbol"]]
                cats = {p["category"] for p in same if p["destination"] == "已计"}
                prefill = bool(same) and all(p["destination"] == "已计" for p in same) and len(cats) == 1
                auto.append({"drawing": drawing, "legend_row": f"A{n:03d}", "legend_handle": c["handle"] or "", "legend_text": "",
                             "symbol": c["symbol"], "symbol_kind": "块", "plan_count": str(len(same)), "count_source": "自动",
                             "variant_flag": "", "destination": "已计" if prefill else "",
                             "target_or_reason": next(iter(cats)) if prefill else "", "suspected_category": "", "search_record_id": ""})
            rows_of = defaultdict(list)
            for row in auto:
                if row["drawing"] == drawing:
                    rows_of[row["symbol"]].append(row["legend_row"])
            for row in auto:
                if row["drawing"] == drawing and len(rows_of[row["symbol"]]) > 1:
                    row["variant_flag"] = "同块多变体（" + "、".join(rows_of[row["symbol"]]) + "）"
        filled = self.read_legend()
        index = {(r["drawing"], r["legend_handle"].upper()): r for r in auto}
        rows = list(auto)
        for row in filled:
            key = (row["drawing"], row["legend_handle"].strip().upper())
            if row["symbol_kind"] == "块" and key[1] and key in index:
                target = index[key]
                for field in FILLED:
                    if row[field].strip():
                        target[field] = row[field].strip()
                if row["plan_count"].strip() and row["plan_count"].strip() != target["plan_count"]:
                    self.warnings["plan_count_conflicts"].append({"drawing": row["drawing"], "legend_row": target["legend_row"],
                                                                  "filled": row["plan_count"], "automatic": target["plan_count"]})
                continue
            if row["symbol_kind"] == "块" and key[1]:
                self.warnings["legend_handle_not_on_legend_sheet"].append({"drawing": row["drawing"], "legend_row": row["legend_row"],
                                                                           "legend_handle": row["legend_handle"]})
            rows.append({**row, "count_source": "手填"})
        seen: dict[tuple[str, str], int] = Counter((r["drawing"], r["legend_row"]) for r in rows)
        for (drawing, number), times in seen.items():
            if times > 1:
                self.problem("C-6", "图例覆盖表", f"{drawing} 的图例行号重复：{number}")
        return rows

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
                if row["symbol_kind"] == "非块" and not re.fullmatch(r"\d+", row["plan_count"]):
                    self.problem("C-6", where, "非块行须手填 plan_count（非负整数）"); continue
                rows.append(row)
        return rows

    def legend_to_ledger(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Non-block rows become candidates; rows without a destination are returned."""
        pending = []
        suspected = {(r["drawing"], r["symbol"]): r["suspected_category"] for r in rows
                     if r["symbol_kind"] == "块" and r["suspected_category"]}
        for c in self.candidates:
            if c["destination"] == "待识别" and (c["drawing"], c["symbol"]) in suspected:
                c["suspected_category"] = suspected[(c["drawing"], c["symbol"])]
        for r in rows:
            if r["symbol_kind"] == "块" and not r["legend_handle"]:
                continue   # a hand-added block row only carries a suspected class; it is not a legend row
            count = int(r["plan_count"]) if re.fullmatch(r"\d+", r["plan_count"]) else 0
            unconfirmed = count == 0 and not r["search_record_id"] and r["destination"] in ("", "不计")
            if not r["destination"] or unconfirmed:
                pending.append({"drawing": r["drawing"], "legend_row": r["legend_row"], "symbol": r["symbol"],
                                "reason": "实例数为 0、没有检索记录（待确认）" if unconfirmed and r["destination"] else "去向为空"})
            if r["symbol_kind"] != "非块" or count == 0:
                continue
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
                self.from_tables(d["drawing"], self.path(str(d["neutral_dir"])), counted.get(d["drawing"], {}), excluded.get(d["drawing"], {}))
        self.place(self.load_sheets())
        self.apply_rules()
        legend_rows = self.legend()
        if self.problems:
            raise InputProblems(self.problems)
        pending = self.legend_to_ledger(legend_rows)
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
        return {"by_destination": {d: by_dest.get(d, 0) for d in DESTINATIONS}, "candidate_total": total,
                "by_drawing": by_drawing, "uncovered_drawings": uncovered, "legend_rows_without_destination": pending,
                "class_completeness": self.completeness(), "groups": group_rows, "legend_rows": legend_rows}

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
                and not result["uncovered_drawings"] and not result["legend_rows_without_destination"])
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
    print(json.dumps({"status": report["status"], "candidate_total": result["candidate_total"], **result["by_destination"],
                      "uncovered_drawings": len(result["uncovered_drawings"]),
                      "legend_rows_without_destination": len(result["legend_rows_without_destination"]),
                      "ledger": str(out / "点位覆盖台账.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
