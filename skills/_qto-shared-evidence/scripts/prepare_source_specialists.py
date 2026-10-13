#!/usr/bin/env python3
"""Carry reviewed source selections into existing component/electrical inputs.

This adapter never recognizes equipment, chooses a cable or connects drawings.
Text hits are observations; model decisions, ambiguities and exclusions remain
explicit. Geometry and source identity are read from verified CAD indices.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from model_recognition_basis import load_working_entries, recognition_basis, texts
from qto_cad_source_contract import canonical_scale
from qto_cad_geometry import summarize, transform_geometry
from qto_recognition_workbench import _verified_index, context, _text, _unit_tokens


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_ref(index, row):
    return dict(source=copy.deepcopy(index["source"]),
                authoritative_source=copy.deepcopy(index["authoritative_source"]),
                index_sha256=index["index_sha256"], layout=index["layout"],
                object_id=row["object_id"], source_path=row["source_path"],
                raw_unit=index["raw_unit"])


class References:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.cache = {}

    def index(self, ref):
        path = (self.base_dir / _text(ref.get("index"), "index")).resolve()
        if path not in self.cache:
            idx = read(path)
            objects = _verified_index(idx)
            if idx["source_identity_issues"]:
                raise ValueError("原图身份冲突，不能绑定专业输入")
            self.cache[path] = idx, objects
        idx, objects = self.cache[path]
        if ref.get("index_sha256") != idx["index_sha256"]:
            raise ValueError("引用索引快照已改变")
        return idx, objects

    def evidence(self, ref):
        idx, objects = self.index(ref)
        oid = ref.get("object_id")
        if oid not in objects:
            raise ValueError("依据引用不存在的源对象")
        row = objects[oid]
        excerpt = ref.get("excerpt")
        if excerpt is not None:
            excerpt = _text(excerpt, "excerpt")
            if excerpt not in (row.get("text") or ""):
                raise ValueError("依据原文不属于引用对象")
        return dict(**source_ref(idx, row), text=row.get("text"), excerpt=excerpt,
                    applicability=_text(ref.get("applicability"), "依据适用范围"),
                    interpretation=_text(ref.get("interpretation"), "依据解释"))


def search(request, base_dir="."):
    """Literal/exact text candidates across explicitly bounded source scopes."""
    refs = References(base_dir)
    terms = texts(request.get("terms"), "terms")
    mode = request.get("match", "literal")
    if mode not in {"literal", "exact"}:
        raise ValueError("match 须为 literal 或 exact")
    limit = request.get("max_hits", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("max_hits 须为正整数")
    scopes = request.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("须声明实际检索范围")
    hits, searched = [], []
    for scope in scopes:
        idx, _ = refs.index(scope)
        interpretation_scope = _text(scope.get("applicability"), "检索适用范围")
        # Existing context validation protects local index boundaries and IDs.
        query = copy.deepcopy(scope.get("query", {}))
        if set(query) - {"bounds", "object_ids", "entity_types", "layers"}:
            raise ValueError("检索 query 仅支持范围、对象、类型和图层过滤")
        query["max_objects"] = max(1, len(idx["objects"]))
        ctx = context(idx, query)
        matched = []
        for row in ctx["objects"]:
            value = row.get("text")
            if value is None:
                continue
            found = [t for t in terms if (t == value if mode == "exact" else t in value)]
            if found:
                matched.append(dict(**source_ref(idx, row), text=value,
                                    bbox=row["bbox"], matched_terms=found,
                                    applicability=interpretation_scope,
                                    status="candidate_only"))
        hits.extend(matched)
        searched.append(dict(source=idx["authoritative_source"], layout=idx["layout"],
                             index_sha256=idx["index_sha256"], index_scope=idx["index_scope"],
                             query=query, applicability=interpretation_scope,
                             observed_objects=len(ctx["objects"]), matches=len(matched),
                             unlocated_count=ctx["unlocated_count"],
                             conversion_warnings=idx["conversion_warnings"]))
    return dict(schema="qto-source-evidence-search/1.0", terms=terms, match=mode,
                searched=searched, hits=hits[:limit], matched_count=len(hits),
                omitted_hits=hits[limit:], automatic_identity_merge=False,
                meaning="仅本次已查范围；未命中不证明缺资料，同号及跨专业对象不自动合并")


def unit_scale(index, operations, refs):
    raw = index["raw_unit"]
    declaration = operations.get("unit_declaration")
    bound = None
    if declaration is not None:
        bound = refs.evidence(declaration)
        excerpt = _text(bound.get("excerpt"), "单位适用条款原文")
        value = bound["text"]
        starts = [i for i in range(len(value)) if value.startswith(excerpt, i)]
        if not any(not any(a < i < b or a < i + len(excerpt) < b
                           for a, b, _ in _unit_tokens(value, with_spans=True)) for i in starts):
            raise ValueError("单位原文截断了单位词")
        scale = canonical_scale(_text(declaration.get("raw_unit"), "raw_unit"))
        if _unit_tokens(excerpt) != {scale}:
            raise ValueError("单位声明和唯一适用原文不一致")
        if raw not in {"Unknown", "Unitless"} and canonical_scale(raw) != scale:
            raise ValueError("单位声明与图纸单位冲突")
        raw = declaration["raw_unit"]
    if raw in {"Unknown", "Unitless"}:
        raise ValueError("必须用有源 unit_declaration 明确图面单位")
    return canonical_scale(raw), bound


def prepare(index, operations, ledger_path, base_dir="."):
    objects = _verified_index(index)
    if index["source_identity_issues"]:
        raise ValueError("源身份冲突")
    if operations.get("index_sha256") != index["index_sha256"]:
        raise ValueError("操作与当前索引快照不一致")
    target = operations.get("target")
    owners = {"components": "qto-components-points", "electrical": "qto-electrical-routes"}
    if target not in owners:
        raise ValueError("target 须为 components 或 electrical")
    scope = texts(operations.get("scope_object_ids"), "scope_object_ids")
    if not set(scope) <= objects.keys():
        raise ValueError("范围引用不存在的源对象")
    scope_basis = _text(operations.get("scope_basis"), "scope_basis")
    ledger_path = Path(ledger_path).resolve()
    ledger, entries = load_working_entries(read(ledger_path), ledger_path,
                                          index["authoritative_source"]["sha256"])
    refs = References(base_dir)
    factor, unit_evidence = unit_scale(index, operations, refs)
    matrix = (factor, 0., 0., 0., 0., factor, 0., 0., 0., 0., factor, 0., 0., 0., 0., 1.)
    used, bindings, unresolved = set(), [], []
    rows = operations.get("instances" if target == "components" else "paths")
    if not isinstance(rows, list) or not rows:
        raise ValueError("需非空实例或路径选择")
    prepared = []
    ids = set()
    node_points = {}
    tolerance = operations.get("node_tolerance_m", 0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("node_tolerance_m 无效")
    if tolerance:
        _text(operations.get("node_tolerance_basis"), "节点容差依据")
    for op in rows:
        oid = op.get("object_id")
        entry = entries.get(op.get("adjudication_id"))
        if oid not in scope or entry is None or entry["decision"] != "include" or entry["professional_owner"] != owners[target] or oid not in entry["object_ids"]:
            raise ValueError("对象须绑定本专业实际审阅范围及 include 记录")
        selected = texts(op.get("member_object_ids", [oid]), "member_object_ids")
        if oid not in selected or (target == "electrical" and selected != [oid]):
            raise ValueError("代表对象须在成员内；电气路径每项只选择一条完整曲线")
        if not set(selected) <= set(scope) or not set(selected) <= set(entry["object_ids"]):
            raise ValueError("成员超出本轮实际审阅范围")
        if any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
               for a in selected for b in used):
            raise ValueError("重复或父子重叠源对象")
        if any(a != b and a.startswith(b + "/") for a in selected for b in selected):
            raise ValueError("同组成员不能重复包含父子对象")
        used.update(selected)
        row = objects[oid]
        evidence_refs = [refs.evidence(r) for r in op.get("evidence_refs", [])]
        observation = _text(op.get("observation"), "observation")
        # Raw source coordinates are never accepted from semantic operations.
        if set(op) & {"position", "geometry", "length", "length_m", "physical_id", "source_ref"}:
            raise ValueError("禁止在操作中覆盖源几何、长度或身份")
        source = source_ref(index, row)
        identity = index["authoritative_source"]["sha256"] + ":" + oid
        binding = dict(source_ref=source, evidence_refs=evidence_refs,
                       adjudication_id=entry["adjudication_id"], observation=observation)
        members = [objects[x] for x in selected]
        binding["member_sources"] = [source_ref(index, member) for member in members]
        ancestors = set()
        for member in members:
            parent = member.get("parent_object_id")
            while parent and parent in objects:
                ancestors.add(parent)
                parent = objects[parent].get("parent_object_id")
        binding["ancestor_issues"] = [dict(object_id=x, issues=objects[x]["issues"])
                                      for x in sorted(ancestors) if objects[x]["issues"]]
        if any(not x["visible_in_entity"] or x["issues"] for x in members):
            unresolved.append(dict(object_id=oid, reason="源可见性或几何问题", issues=[x["issues"] for x in members], **binding))
            continue
        if target == "components":
            if any(x["entity_type"] in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION"} or not x.get("bbox") for x in members):
                unresolved.append(dict(object_id=oid, reason="文字或无可定位实物几何", **binding))
                continue
            fields = {k: _text(op.get(k), k) for k in ("category", "system", "spec", "unit")}
            box = [min(x["bbox"][i] for x in members) for i in (0, 1)] + [max(x["bbox"][i] for x in members) for i in (2, 3)]
            prepared.append(dict(physical_id=identity, **fields, quantity_level="方案暂估量",
                                 evidence=observation, source_binding=binding,
                                 source_geometry=[dict(object_id=x["object_id"], geometry=copy.deepcopy(x.get("geometry")), bbox=x["bbox"]) for x in members],
                                 location_m=[(box[0]+box[2])*factor/2, (box[1]+box[3])*factor/2],
                                 location_meaning="源对象图面中心，不是实物端口",
                                 object_tag=op.get("object_tag"), uncertainties=entry["uncertainties"]))
        else:
            pid = _text(op.get("id"), "path.id")
            if pid in ids:
                raise ValueError("路径编号重复")
            ids.add(pid)
            if row["geometry_status"] != "measurable" or not row.get("geometry"):
                unresolved.append(dict(object_id=oid, reason="无完整可测路径", **binding))
                continue
            geo = transform_geometry(row["geometry"], matrix)
            measured = summarize(geo)
            if not measured["length"] or measured["start"] == measured["end"]:
                raise ValueError("零长或闭合路径须先按实际节点分段")
            start, end = (_text(op.get(k), k) for k in ("from_node", "to_node"))
            for node, point in [(start, measured["start"]), (end, measured["end"])]:
                if node in node_points and math.dist(node_points[node], point) > tolerance:
                    raise ValueError("同节点源坐标不连续；不能用节点名称接通未知距离")
                node_points[node] = point
            role = _text(op.get("role"), "role")
            prepared.append(dict(id=pid, from_node=start, to_node=end, role=role,
                                 length_m=measured["length"], source_ids=[identity, *entry["evidence_ids"]],
                                 source_binding=binding, geometry=geo))
        bindings.append(binding)
    remaining = [oid for oid in scope if oid not in used]
    state = dict(schema="qto-source-specialist-preparation/1.0", index_sha256=index["index_sha256"],
                 operations_sha256=digest(operations), recognition_basis=recognition_basis(ledger, ledger_path),
                 unit="m", scale_to_m=factor, unit_evidence=unit_evidence, scope_basis=scope_basis,
                 bindings=bindings, unresolved=unresolved, unprocessed_object_ids=remaining,
                 formal_adoption_allowed=False)
    if target == "components":
        return dict(reconciled_instances=prepared, recognition_basis=state["recognition_basis"], source_preparation=state)
    channels = copy.deepcopy(operations.get("channels", []))
    circuits = copy.deepcopy(operations.get("circuits", []))
    out = dict(schema="qto-electrical-path-ledger/1.0", unit="m",
               project_id=_text(operations.get("project_id"), "project_id"), paths=prepared,
               channels=channels, circuits=circuits, source_preparation=state)
    # The existing professional calculator enforces path order, channel ownership,
    # evidence, parallel counts and extras. It is not duplicated in this adapter.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qto-electrical-routes/scripts"))
    from calculate_electrical_paths import calculate
    calculate(out, base_dir)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("search")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("prepare")
    for field in ("index", "operations", "ledger", "output"):
        p.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("输出已存在，请用新版本，不能覆盖证据或原图")
    if args.action == "search":
        out = search(read(args.input), args.input.resolve().parent)
    else:
        out = prepare(read(args.index), read(args.operations), args.ledger, args.operations.resolve().parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
