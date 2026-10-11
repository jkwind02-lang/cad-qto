"""Discover the project files that registered CLIs actually open indirectly.

This is an explicit adapter for the CLI contracts, not a recursive search for
strings named ``path``. Paths used only as locators, historical metadata, output
destinations or JSON selectors are not file inputs. Runtime resources belong in
tool fingerprints instead. Adding a registered CLI requires declaring its read
contract here, including an explicit no-indirect-reads entry when appropriate.

The caller owns task state and file hashes. This module never completes tasks,
adopts quantities, updates manifests or follows unregistered nested documents.
Missing future inputs are returned as pending so independent preparation work
can continue. Existing stale inputs may be inspected for dependencies only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


NO_INDIRECT_TOOLS = frozenset({
    "pipe_infer", "component_count", "endpoint_handoff", "duct_reference",
    "electrical_paths", "masonry_reference", "framing_reference",
    "piles_reference", "roads_reference", "dependency_recalculate",
})
INDIRECT_TOOLS = frozenset({
    "concrete_formwork", "rebar", "structural_steel", "surfaces_envelope",
    "earthwork_terrain", "cad_index", "cad_view", "cad_scope", "pipe_prepare",
    "correspondence", "instance_requirements", "review_html", "review_feedback",
})
SUPPORTED_TOOLS = NO_INDIRECT_TOOLS | INDIRECT_TOOLS


class _Dependencies:
    def __init__(self, task, artifacts, root):
        self.task = task
        self.root = Path(root).resolve()
        self.inputs = set(task.get("input_ids", []))
        self.artifacts = (artifacts if isinstance(artifacts, dict) else
                          {row["id"]: row for row in artifacts})
        self.paths, self.by_path = {}, {}
        self.required, self.pending = set(), set()
        self.errors, self.references = [], []
        self.cache = {}
        for aid, row in self.artifacts.items():
            try:
                path = Path(row["path"])
                path = (self.root / path).resolve()
            except (KeyError, TypeError, ValueError, OSError, RuntimeError):
                continue  # Diagnose malformed paths if this task uses them.
            self.paths[aid] = path
            self.by_path.setdefault(os.path.normcase(str(path)), []).append(aid)

    def error(self, message):
        message = "任务 " + str(self.task.get("id", "?")) + "：" + message
        if message not in self.errors:
            self.errors.append(message)

    def artifact(self, aid, label):
        if not isinstance(aid, str) or aid not in self.artifacts:
            self.error(label + " 引用了未登记产物 " + str(aid))
            return None
        self.required.add(aid)
        if aid not in self.inputs:
            self.error(label + " 的文件依赖必须加入本任务 input_ids：" + aid +
                       " (" + str(self.paths.get(aid, "无效路径")) + ")")
            return None
        if aid not in self.paths:
            self.error(label + " 的产物路径无效：" + aid)
            return None
        path = self.paths[aid]
        try:
            if not path.exists():
                self.pending.add(aid)
                return None
            if not path.is_file():
                self.error(label + " 需要实际文件，不能使用目录：" + str(path))
                return None
        except OSError as exc:
            self.error(label + " 无法检查文件 " + str(path) + ": " + str(exc))
            return None
        if self.artifacts[aid].get("file_status") == "invalid":
            self.error(label + " 文件身份当前无效：" + aid)
            return None
        return aid

    def resolve(self, value, label, base, *, expand=False, strip=True, absolute=False):
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            self.error(label + " 缺少有效文件路径")
            return None
        try:
            raw = Path(value.strip() if strip else value)
            raw = raw.expanduser() if expand else raw
            if absolute and not raw.is_absolute():
                self.error(label + " 按该工具契约必须使用绝对文件路径：" + value)
                return None
            return (base / raw).resolve()
        except (ValueError, OSError, RuntimeError) as exc:
            self.error(label + " 文件路径无效：" + str(exc))
            return None

    def reference(self, value, label, base, **options):
        path = self.resolve(value, label, base, **options)
        if path is None:
            return None
        matches = self.by_path.get(os.path.normcase(str(path)), [])
        ref = {"field": label, "path": str(path),
               "artifact_id": matches[0] if len(matches) == 1 else None}
        if ref not in self.references:
            self.references.append(ref)
        if not matches:
            self.error("未登记间接文件依赖：" + label + " → " + str(path) +
                       "；请登记 artifacts 并加入本任务 input_ids")
            return None
        if len(matches) != 1:
            self.error("间接文件路径对应多个产物：" + str(path))
            return None
        return self.artifact(matches[0], label)

    def json(self, aid, label):
        aid = self.artifact(aid, label)
        if aid is None:
            return None
        if aid in self.cache:
            return self.cache[aid]
        try:
            raw = self.paths[aid].read_bytes()
            expected = self.artifacts[aid].get("sha256")
            if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
                self.error(label + " 在依赖检查期间已改变；请重新读取当前文件身份：" + aid)
                return None
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                self.error(label + " 须为 JSON 对象：" + str(self.paths[aid]))
                return None
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            self.error(label + " 无法读取 JSON " + str(self.paths[aid]) + ": " + str(exc))
            return None
        self.cache[aid] = data
        return data

    def rows(self, data, field, label, *, optional=False):
        value = data.get(field, [] if optional else None)
        if not isinstance(value, list):
            self.error(label + "." + field + " 必须为对象数组")
            return []
        out = []
        for number, row in enumerate(value):
            name = label + "." + field + "[" + str(number) + "]"
            if not isinstance(row, dict):
                self.error(name + " 必须为对象")
            else:
                out.append((row, name))
        return out

    def sources(self, data, aid, *, base=None, expand=False, absolute=False):
        base = self.paths[aid].parent if base is None else base
        for row, label in self.rows(data, "sources", aid):
            self.reference(row.get("path"), label + ".path", base,
                           expand=expand, absolute=absolute)

    def ledger(self, aid):
        data = self.json(aid, "识别证据账本")
        if data is not None:
            for row, label in self.rows(data, "evidence", aid, optional=True):
                # load_working_entries hashes all evidence, including rows that
                # no active recognition entry happens to reference.
                self.reference(row.get("path"), label + ".path", self.paths[aid].parent)

    def cad_sources(self, data, aid):
        for key in ("source", "authoritative_source"):
            row = data.get(key)
            if not isinstance(row, dict):
                self.error(aid + "." + key + " 必须提供来源文件对象")
            else:
                self.reference(row.get("path"), aid + "." + key + ".path",
                               self.root, strip=False)

    def export_sidecar(self, source_path, label):
        # build_index appends to the original path before resolving it. This
        # matters when a descriptor uses a symlink to the registered drawing.
        if not isinstance(source_path, str):
            return
        value = source_path + ".export.json"
        path = self.resolve(value, label, self.root, strip=False)
        if path is not None:
            try:
                if path.exists():
                    self.reference(value, label, self.root, strip=False)
            except OSError as exc:
                self.error(label + " 无法检查转换记录：" + str(exc))

    def inspect_tool(self, tool_id, args):
        if tool_id in NO_INDIRECT_TOOLS:
            return
        if tool_id in {"cad_index", "cad_view"}:
            aid = args.get("source")
            if aid not in self.paths or self.artifact(aid, "source") is None:
                return
            if self.paths[aid].suffix.lower() == ".dxf":
                # The runner makes a temporary descriptor from this declared
                # DXF. Its temporary JSON is an output, not a project input.
                self.export_sidecar(str(self.paths[aid]), aid + ".export_sidecar")
                return
            data = self.json(aid, "source")
            if data is None:
                return
            self.reference(data.get("path"), aid + ".path", self.root, strip=False)
            authoritative = data.get("authoritative_source")
            if authoritative:
                if not isinstance(authoritative, dict):
                    self.error(aid + ".authoritative_source 必须为对象")
                else:
                    self.reference(authoritative.get("path"), aid + ".authoritative_source.path",
                                   self.root, strip=False)
            self.export_sidecar(data.get("path"), aid + ".export_sidecar")
            return
        if tool_id in {"cad_scope", "pipe_prepare"}:
            aid = args.get("index")
            data = self.json(aid, "index")
            if data is not None:
                self.cad_sources(data, aid)
            if tool_id == "pipe_prepare":
                self.ledger(args.get("ledger"))
            return
        if tool_id in {"review_html", "review_feedback"}:
            aid = args.get("input" if tool_id == "review_html" else "view")
            data = self.json(aid, "复核视图")
            if data is not None:
                for row, label in self.rows(data, "scenes", aid):
                    if row.get("raster_path"):
                        self.reference(row["raster_path"], label + ".raster_path",
                                       self.paths[aid].parent, strip=False)
            return
        aid = args.get("manifest" if tool_id in {
            "concrete_formwork", "rebar", "structural_steel",
            "surfaces_envelope", "earthwork_terrain"} else "input")
        data = self.json(aid, "工具输入")
        if data is None:
            return
        if tool_id == "concrete_formwork":
            source = data.get("source")
            if not isinstance(source, dict):
                self.error(aid + ".source 必须为对象")
                return
            for field in ("dwg_path", "entity_stream_path", "metadata_path"):
                self.reference(source.get(field), aid + ".source." + field,
                               self.root, expand=True, strip=False)
            for row, label in self.rows(source, "excluded_drifted_files", aid + ".source", optional=True):
                self.reference(row.get("path"), label + ".path", self.root, expand=True, strip=False)
        elif tool_id == "rebar":
            self.sources(data, aid, absolute=True)
        elif tool_id == "structural_steel":
            self.sources(data, aid, base=self.root, expand=True)
        elif tool_id in {"surfaces_envelope", "earthwork_terrain"}:
            self.sources(data, aid, expand=True)
        elif tool_id in {"correspondence", "instance_requirements"}:
            self.sources(data, aid)
            if tool_id == "instance_requirements":
                for row, label in self.rows(data, "recognition_ledgers", aid):
                    ledger_id = self.reference(row.get("path"), label + ".path", self.paths[aid].parent)
                    if ledger_id is not None:
                        self.ledger(ledger_id)

    def result(self):
        return {"required_artifact_ids": sorted(self.required),
                "errors": self.errors,
                "pending_artifact_ids": sorted(self.pending),
                "references": self.references}


def validate_dependencies(task, artifacts, root):
    """Return actual direct/indirect file requirements and task-local errors.

    ``artifacts`` accepts the inspection's artifact map (preferred, with actual
    paths/SHA-256) or declarations keyed by id. Paths in declarations are relative
    to ``root``. Each discovered file must be registered and explicitly included
    in *this task's* input_ids; scope basis metadata is not a substitute.

    A missing registered input is pending, without treating old caches as its
    replacement. The state engine still decides producer/dependency readiness.
    Even when errors is empty this function certifies no engineering semantics.
    """
    check = _Dependencies(task, artifacts, root)
    action = task.get("action")
    if not action:
        return check.result()
    if not isinstance(action, dict):
        check.error("action 必须为对象")
        return check.result()
    # Imported lazily to keep state inspection independent of the subprocess
    # runner and to make new registry entries fail closed until audited here.
    from qto_workflow_tools import TOOLS
    tool_id = action.get("tool")
    if not isinstance(tool_id, str) or tool_id not in TOOLS or tool_id not in SUPPORTED_TOOLS:
        check.error("工具尚未登记文件读取契约：" + str(tool_id))
        return check.result()
    args = action.get("arguments")
    if not isinstance(args, dict):
        check.error("action.arguments 必须为对象")
        return check.result()
    tool = TOOLS[tool_id]
    for field in tool["inputs"]:
        if field in args:
            check.artifact(args[field], field)
        elif field not in tool["optional"]:
            check.error("缺少工具输入参数：" + field)
    check.inspect_tool(tool_id, args)
    return check.result()
