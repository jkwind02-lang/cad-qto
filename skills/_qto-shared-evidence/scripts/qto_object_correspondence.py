"""Generate evidence-bound cross-view candidates, never quantities or adoption.

Observations and model interpretations are inputs, not conclusions inferred from
nearby coordinates.  Matching an explicit identifier is still only a candidate.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

from qto_cad_source_contract import verify_source_file

SCHEMA = "qto-object-correspondence/1.0"
RESULT_SCHEMA = "qto-object-correspondence-result/1.0"
FIELDS = frozenset({"building", "floor", "system", "object_tag", "identity_key",
                    "detail_index", "connection_role", "continuation_id",
                    "specification", "object_type", "variant"})
RELATIONS = {"same_physical", "floor_continuation", "detail_applicability"}
IDENTIFIERS = {"same_physical": {"object_tag", "identity_key"},
               "floor_continuation": {"object_tag", "continuation_id", "identity_key"},
               "detail_applicability": {"detail_index"}}


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " 必须为非空文字")
    return value.strip()


def indexed(rows, key, name):
    if not isinstance(rows, list):
        raise ValueError(name + " 必须为数组")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(name + " 项必须为对象")
        identity = text(row.get(key), name + "." + key)
        if identity in result:
            raise ValueError(name + " 编号重复: " + identity)
        result[identity] = copy.deepcopy(row)
    return result


def route_ref(value, name):
    if not isinstance(value, dict):
        raise ValueError(name + " 缺少既有管段定位 route_ref")
    result = {"segment_id": text(value.get("segment_id"), name + ".segment_id")}
    if value.get("end") in {"start", "end"}:
        if "part_index" in value or "parameter" in value:
            raise ValueError(name + " 不能同时声明端点和曲线参数")
        result["end"] = value["end"]
    else:
        part = value.get("part_index")
        parameter = value.get("parameter")
        if (isinstance(part, bool) or not isinstance(part, int) or part < 0
                or isinstance(parameter, bool) or not isinstance(parameter, (int, float))
                or not math.isfinite(parameter) or not 0 <= parameter <= 1):
            raise ValueError(name + " 须声明 start/end 或有效 part_index/parameter")
        result.update(part_index=part, parameter=parameter)
    return result


def route_endpoint_key(ref):
    """Canonical location, independent of observation and business link IDs."""
    if "end" in ref:
        location = (ref["end"],)
    elif ref["part_index"] == 0 and ref["parameter"] == 0:
        location = ("start",)
    else:
        location = ("parameter", ref["part_index"], float(ref["parameter"]))
    return (ref["segment_id"], *location)


def source_port_key(observation, sources):
    """A locator must identify this port, not only a shared equipment body.

    Source aliases and copied equal files have the same content identity. A
    reverse observation or renamed view does not create another physical port.
    """
    return (str(sources[observation["source_id"]]["sha256"]).lower(),
            observation["locator"], observation["building"], observation["floor"],
            observation["system"], observation["connection_role"])


def comparison(request, left, right):
    relation = request["relation"]
    matches, conflicts, missing = [], [], []
    if relation == "detail_applicability":
        if left["kind"] != "instance" or right["kind"] != "detail":
            conflicts.append({"code": "object_kind_mismatch", "expected": "instance -> detail"})
    elif relation == "floor_continuation":
        if left["kind"] != "port" or right["kind"] != "port":
            conflicts.append({"code": "object_kind_mismatch", "expected": "port -> port"})
    elif left["kind"] != right["kind"] or left["kind"] == "detail":
        conflicts.append({"code": "object_kind_mismatch", "expected": "同类 instance 或 port"})
    if (left["source_id"], left["view_id"]) == (right["source_id"], right["view_id"]):
        conflicts.append({"code": "same_source_view", "meaning": "本工具处理不同视图；同图关系另行判读"})
    fields = set(request["match_fields"])
    if not fields & IDENTIFIERS[relation]:
        missing.append({"code": "identity_constraint_missing", "fields": sorted(IDENTIFIERS[relation])})
    # Context scopes an identifier; the same tag in another building is not the
    # same physical object. Generic details may omit building/floor deliberately.
    if relation != "detail_applicability":
        fields.update({"building", "system"})
    if relation == "same_physical":
        fields.add("floor")
    if left["kind"] == "port" and right["kind"] == "port":
        fields.add("connection_role")
    # Known contrary contexts are never bypassed by leaving them out of the
    # request. Missing contexts on a generic detail are not fabricated.
    for field in ("building", "system", "connection_role"):
        if left.get(field) is not None and right.get(field) is not None:
            fields.add(field)
    if relation in {"same_physical", "detail_applicability"}:
        for field in ("object_type", "specification", "variant"):
            if left.get(field) is not None and right.get(field) is not None:
                fields.add(field)
    for field in sorted(fields):
        a, b = left.get(field), right.get(field)
        if a is None or b is None:
            missing.append({"code": "field_missing", "field": field,
                            "missing_on": [side for side, value in (("from", a), ("target", b)) if value is None]})
        elif a != b:
            conflicts.append({"code": "field_conflict", "field": field, "from": a, "target": b})
        else:
            matches.append({"field": field, "value": a})
    if relation == "floor_continuation":
        if left.get("floor") is None or right.get("floor") is None:
            missing.append({"code": "continuation_floor_missing"})
        elif left["floor"] == right["floor"]:
            conflicts.append({"code": "continuation_same_floor", "floor": left["floor"]})
        else:
            matches.append({"field": "floor_relation", "value": "different"})
    return {"target_id": right["observation_id"],
            "status": "conflict" if conflicts else ("undetermined" if missing else "compatible"),
            "matches": matches, "conflicts": conflicts, "missing_constraints": missing,
            "quantity_created": False}


def resolve_correspondence(document, base_dir=None):
    """Validate current sources, return candidates and explicit working pairings.

    No coordinate distance is used. No one-to-one allocation is imposed: a
    physical object can have multiple views and a detail can serve many objects.
    """
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
        raise ValueError("跨图对象对应输入版本无效")
    root = Path(base_dir or ".").resolve()
    sources = indexed(document.get("sources"), "source_id", "sources")
    if not sources:
        raise ValueError("缺少原始来源")
    for sid, source in sources.items():
        if source.get("kind") not in {"drawing", "technical_document"}:
            raise ValueError(sid + " 来源须为 drawing 或 technical_document；底稿不作对应证据")
        path = Path(text(source.get("path"), sid + ".path"))
        source["path"] = str((root / path).resolve())
        verify_source_file(source, sid)
    observations = indexed(document.get("observations"), "observation_id", "observations")
    for oid, observation in observations.items():
        if observation.get("source_id") not in sources:
            raise ValueError(oid + " 引用未登记来源")
        for field in ("locator", "view_id"):
            observation[field] = text(observation.get(field), oid + "." + field)
        if observation.get("kind") not in {"instance", "port", "detail"}:
            raise ValueError(oid + " kind 须为 instance、port 或 detail")
        for field in FIELDS:
            if field in observation and observation[field] is not None:
                observation[field] = text(observation[field], oid + "." + field)
    requests = indexed(document.get("requests"), "request_id", "requests")
    if not requests:
        raise ValueError("没有待对应请求")
    results = {}
    for rid, request in requests.items():
        if request.get("relation") not in RELATIONS:
            raise ValueError(rid + " relation 无效")
        left_id = request.get("from_id")
        if left_id not in observations:
            raise ValueError(rid + " from_id 不存在")
        targets = request.get("target_ids")
        if (not isinstance(targets, list) or not targets
                or any(not isinstance(t, str) or t not in observations for t in targets)
                or len(set(targets)) != len(targets) or left_id in targets):
            raise ValueError(rid + " target_ids 必须为已知、不重复且不含自身的非空数组")
        fields = request.get("match_fields")
        if (not isinstance(fields, list) or not fields
                or any(not isinstance(f, str) or f not in FIELDS for f in fields)
                or len(set(fields)) != len(fields)):
            raise ValueError(rid + " match_fields 缺少显式且有效的匹配约束")
        if request["relation"] == "floor_continuation" and "floor" in fields:
            raise ValueError(rid + " 跨层延续不能要求楼层相同")
        evaluations = [comparison(request, observations[left_id], observations[t]) for t in targets]
        candidates = [row["target_id"] for row in evaluations if row["status"] != "conflict"]
        if not candidates:
            status = "no_candidate"
        elif any(row["status"] == "undetermined" for row in evaluations):
            status = "insufficient_constraints"
        else:
            status = "unique_candidate" if len(candidates) == 1 else "multiple_candidates"
        results[rid] = {"request_id": rid, "relation": request["relation"], "from_id": left_id,
                        "status": status, "candidate_ids": candidates, "evaluations": evaluations,
                        "adoption_scope": "candidate", "review_status": "unreviewed",
                        "formal_adoption_allowed": False}
    pairings, links = [], []
    interpretations = indexed(document.get("interpretations", []), "interpretation_id", "interpretations")
    used_pairs, physical_links, link_ids = set(), set(), set()
    route_pairs, source_pairs = {}, {}
    for iid, interpretation in interpretations.items():
        rid, target_id = interpretation.get("request_id"), interpretation.get("target_id")
        if rid not in results:
            raise ValueError(iid + " request_id 不存在")
        request, result = requests[rid], results[rid]
        target = next((row for row in result["evaluations"] if row["target_id"] == target_id), None)
        if target is None or target["status"] != "compatible":
            raise ValueError(iid + " 只能显式选择约束完整且无冲突的候选；先补齐观察事实")
        left = observations[request["from_id"]]
        right = observations[target_id]
        pair_key = (request["relation"], left["observation_id"], target_id)
        if pair_key in used_pairs:
            raise ValueError(iid + " 重复解释同一关系")
        used_pairs.add(pair_key)
        if interpretation.get("decision_origin") != "model":
            raise ValueError(iid + " 须保留实际 decision_origin=model")
        if any(interpretation.get(k) is not None for k in ("confirmed_by", "confirmed_at", "confirmation_source")):
            raise ValueError(iid + " 模型解释不得冒充人工签认")
        basis = text(interpretation.get("basis"), iid + ".basis")
        refs = interpretation.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(iid + " 缺少两侧观察证据 source_refs")
        covered = set()
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("source_id") not in sources:
                raise ValueError(iid + " source_refs 来源无效")
            locator = text(ref.get("locator"), iid + ".source_refs.locator")
            oid = ref.get("observation_id")
            if oid is not None:
                if oid not in {left["observation_id"], right["observation_id"]}:
                    raise ValueError(iid + " 证据观察不属于本次配对")
                obs = observations[oid]
                if obs["source_id"] != ref["source_id"] or obs["locator"] != locator:
                    raise ValueError(iid + " 观察证据来源或定位不一致")
                covered.add(oid)
        if covered != {left["observation_id"], right["observation_id"]}:
            raise ValueError(iid + " 必须逐侧引用观察对象及其来源定位")
        pairing = {"interpretation_id": iid, "request_id": rid, "relation": request["relation"],
                   "from_id": left["observation_id"], "target_id": target_id, "basis": basis,
                   "source_refs": copy.deepcopy(refs), "decision_origin": "model",
                   "adoption_scope": "working", "review_status": "unreviewed",
                   "formal_adoption_allowed": False, "quantity_created": False}
        pairings.append(pairing)
        if interpretation.get("export_cross_view_link") is True:
            if request["relation"] != "floor_continuation":
                raise ValueError(iid + " 仅跨层端口延续可导出拓扑接口；重复视图和典型详图不能造连接")
            lid = text(interpretation.get("link_id"), iid + ".link_id")
            pid = text(interpretation.get("physical_id"), iid + ".physical_id")
            if lid in link_ids or pid in physical_links:
                raise ValueError(iid + " 同一跨层实物不能重复导出数量出口")
            a = route_ref(left.get("route_ref"), left["observation_id"])
            b = route_ref(right.get("route_ref"), right["observation_id"])
            if a["segment_id"] == b["segment_id"]:
                raise ValueError(iid + " 跨图接口两侧不能引用同一管段")
            # A <-> B is one physical connection even if all manually assigned
            # link/physical/observation IDs are renamed. Check both the actual
            # route references and original-source locators, so changing route
            # aliases or source aliases cannot manufacture a second outlet.
            route_pair = frozenset((route_endpoint_key(a), route_endpoint_key(b)))
            source_pair = frozenset((source_port_key(left, sources), source_port_key(right, sources)))
            previous = route_pairs.get(route_pair) or source_pairs.get(source_pair)
            if previous is not None:
                raise ValueError(iid + " 源端口配对重复；无向物理关系已经由 " + previous + " 导出")
            route_pairs[route_pair] = source_pairs[source_pair] = iid
            link_ids.add(lid)
            physical_links.add(pid)
            links.append({"link_id": lid, "physical_id": pid, "a": a, "b": b, "system": left["system"],
                          "source_ids": sorted({r["source_id"] for r in refs}), "basis": basis,
                          "correspondence_interpretation_id": iid, "decision_origin": "model",
                          "adoption_scope": "working", "review_status": "unreviewed",
                          "formal_adoption_allowed": False})
    return {"schema_version": RESULT_SCHEMA, "sources": list(sources.values()),
            "observations": list(observations.values()), "requests": list(results.values()),
            "pairings": pairings, "cross_view_links": links, "quantity_created": False,
            "limitations": ["输入观察与语义判断由模型提供；工具只检查文件身份及显式约束",
                            "唯一候选不等于同一实物已核定；不依据坐标距离或数量补差配对",
                            "拓扑接口须经原管网引擎校验实际管段、系统和视图；本工具不计算长度"]}


def main():
    parser = argparse.ArgumentParser(description="从源证据和显式约束生成跨图对象候选，不生成工程量")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.input.read_text(encoding="utf-8-sig"))
        output = args.output.resolve()
        result = resolve_correspondence(document, args.input.resolve().parent)
        if output == args.input.resolve() or output in {Path(s["path"]) for s in result["sources"]}:
            raise ValueError("输出不能覆盖输入或原始来源")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "request_count": len(result["requests"]),
                          "pairing_count": len(result["pairings"]), "quantity_created": False}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
