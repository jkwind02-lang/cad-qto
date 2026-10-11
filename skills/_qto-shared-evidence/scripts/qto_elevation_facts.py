"""Convert sourced elevation facts without confusing datum, measurement or route.

The input is a model's explicit engineering interpretation, not a recognizer.
All arithmetic is recomputed from input facts. DN is never an outer diameter.
No page coordinates are used to manufacture cross-view pipe lengths.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import deque
from pathlib import Path

SCHEMA = "qto-elevation-link/1.0"
UNITS = {"mm": .001, "cm": .01, "m": 1., "ft": .3048, "in": .0254}
LEVELS = {"confirmed": 0, "inferred": 1, "scenario": 2}
QUANTITY_LEVELS = {"confirmed": "确认量", "inferred": "规则推算量", "scenario": "方案暂估量"}
CENTER_KINDS = {"pipe_center", "equipment_port_center"}
OUTER_KINDS = {"pipe_outer_bottom": 1., "pipe_outer_top": -1.}
REFERENCE_KINDS = {"structural_floor", "finished_floor", "other_reference"}


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + "须为有限数值")
    return float(value)


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + "须为非空文字")
    return value.strip()


def record(value, name):
    if not isinstance(value, dict):
        raise ValueError(name + "须为对象")
    return value


def factor(unit):
    if not isinstance(unit, str) or unit not in UNITS:
        raise ValueError("不支持的长度单位: " + str(unit))
    return UNITS[unit]


def evidence(row, name):
    record(row, name)
    refs = row.get("source_ids")
    if not isinstance(refs, list) or not refs or any(not isinstance(v, str) or not v.strip() for v in refs):
        raise ValueError(name + "缺少定位证据source_ids")
    text(row.get("basis"), name + ".basis")
    level = row.get("level")
    if level not in LEVELS:
        raise ValueError(name + ".level须为confirmed/inferred/scenario")
    return list(dict.fromkeys(refs)), level


def quantity(row, name):
    refs, level = evidence(row, name)
    return number(row.get("value"), name + ".value") * factor(row.get("unit")), refs, level


def weakest(levels):
    return max(levels, key=LEVELS.get)


def check_sources(spec):
    """Reuse the dependency graph's sources(id, sha256), adding fact locators.

    This checks referenced records, not the model's visual interpretation. When
    a path is supplied its bytes are checked as well; embedded records carry the
    hash of the upstream source and must remain available with the evidence pack.
    """
    sources = spec.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("标高事实须带sources来源目录，不能只有孤立source_ids")
    catalog = {}
    for source in sources:
        record(source, "source")
        sid = text(source.get("id"), "source.id")
        if sid in catalog:
            raise ValueError("来源编号重复")
        digest = source.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError("来源缺少有效内容哈希")
        text(source.get("locator"), "source.locator")
        if source.get("path") is not None:
            path = Path(text(source["path"], "source.path"))
            if not path.is_absolute():
                raise ValueError("来源path须为绝对路径，避免随工作目录改变来源")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest.lower():
                raise ValueError("来源文件不存在或哈希发生变化: " + str(path))
        facts = source.get("facts", [])
        if not isinstance(facts, list):
            raise ValueError("source.facts须为数组")
        fact_ids = set()
        for fact in facts:
            record(fact, "source.fact")
            fid = text(fact.get("id"), "source.fact.id")
            if fid in fact_ids:
                raise ValueError("同一来源的事实编号重复")
            fact_ids.add(fid)
        catalog[sid] = source

    def walk(row):
        if isinstance(row, dict):
            if "source_ids" in row:
                for sid in evidence(row, "来源引用")[0]:
                    if sid not in catalog:
                        raise ValueError("引用未登记来源: " + sid)
            for key, child in row.items():
                if key != "sources":
                    walk(child)
        elif isinstance(row, list):
            for child in row:
                walk(child)
    walk(spec)

    def matching_facts(row, name, id_field, semantics):
        record(row, name)
        fid = text(row.get(id_field), name + "." + id_field)
        refs, level = evidence(row, name)
        matching = [fact for sid in refs for fact in catalog[sid].get("facts", []) if fact["id"] == fid]
        if not matching:
            raise ValueError(name + "引用的事实未登记在其来源中: " + fid)
        for fact in matching:
            for key, expected in semantics.items():
                if key not in fact or fact[key] != expected:
                    raise ValueError(name + "与来源事实的" + key + "不一致或来源未登记该解释")
            if fact.get("level") not in LEVELS or LEVELS[level] < LEVELS[fact["level"]]:
                raise ValueError(name + "不能提升来源事实的方案/推算层级")
        return fid, matching

    def bind(row, expected_kind, name, id_field="fact_id", semantics=None):
        semantics = {"measurement_kind": expected_kind, **(semantics or {})}
        fid, matching = matching_facts(row, name, id_field, semantics)
        value_m = None if row.get("value") is None else number(row["value"], name + ".value") * factor(row.get("unit"))
        factor(row.get("unit"))
        if value_m is None and (row.get("status") != "unresolved" or not text(row.get("issue"), name + ".issue")):
            raise ValueError(name + "空值须明确未决状态及原因")
        for fact in matching:
            observed_m = None if fact.get("value") is None else number(fact["value"], "source.fact.value") * factor(fact.get("unit"))
            if (observed_m is None) != (value_m is None) or (observed_m is not None and not math.isclose(observed_m, value_m, rel_tol=1e-12, abs_tol=1e-12)):
                raise ValueError(name + "数值与其来源事实不一致")
            if observed_m is None and fact.get("status") != "unresolved":
                raise ValueError(name + "来源空值未明确未决状态")
        return fid

    bound = []
    conversions = []
    for side in ("from", "to"):
        row = record(spec.get(side), side)
        bound.append(bind(row, row.get("measurement_kind"), side, semantics={
            "datum_id": row.get("datum_id"), "reference_id": row.get("reference_id"), "system": spec.get("system")}))
        conversion = row.get("centerline_conversion")
        if conversion is not None:
            record(conversion, side + ".centerline_conversion")
            cid = text(conversion.get("conversion_id"), side + ".conversion_id")
            if cid in conversions:
                raise ValueError("测点转换编号重复")
            conversions.append(cid)
            if conversion.get("method") == "outer_radius":
                operand_id = bind(conversion.get("outer_diameter"), "outer_diameter", side + ".outer_diameter")
            elif conversion.get("method") == "signed_offset":
                operand_id = bind(conversion.get("offset"), "vertical_offset", side + ".offset")
            else:
                raise ValueError("未知测点换算方法")
            bound.append(operand_id)
            matching_facts(conversion, side + ".centerline_conversion", "conversion_id", {
                "measurement_kind": "centerline_conversion", "method": conversion["method"],
                "from_fact_id": row["fact_id"], "from_reference_id": row["reference_id"],
                "target_reference_id": conversion.get("target_reference_id"), "datum_id": row["datum_id"],
                "system": spec.get("system"), "operand_fact_id": operand_id})
    transforms = spec.get("datum_transforms", [])
    if not isinstance(transforms, list):
        raise ValueError("datum_transforms须为数组")
    for row in transforms:
        bound.append(bind(row, "datum_offset", "datum_transform", "transform_id", {
            "from_datum": row.get("from_datum"), "to_datum": row.get("to_datum")}))
    route = record(spec.get("route", {}), "route")
    end_refs = {side: spec[side].get("centerline_conversion", {}).get("target_reference_id", spec[side]["reference_id"]) for side in ("from", "to")}
    route_semantics = {"from_reference_id": end_refs["from"], "to_reference_id": end_refs["to"], "system": spec.get("system")}
    if route.get("kind", "unresolved") != "unresolved":
        matching_facts(route, "route", "route_id", {"measurement_kind": "route_shape", "route_kind": route.get("kind"), **route_semantics})
    horizontal = route.get("horizontal_displacement")
    if horizontal is not None:
        bound.append(bind(horizontal, "horizontal_displacement", "horizontal_displacement", semantics=route_semantics))
    return {"source_ids": list(catalog), "bound_fact_ids": list(dict.fromkeys(bound)),
            "conversion_ids": conversions, "file_hashes_checked": [s["id"] for s in sources if s.get("path") is not None],
            "semantic_review": "model_interpretation_not_automatically_verified"}


def resolve_centerline(fact, system, name):
    """Return a centerline elevation in metres, or an explicit unresolved reason."""
    refs, level = evidence(fact, name)
    value = None if fact.get("value") is None else number(fact["value"], name + ".value") * factor(fact.get("unit"))
    levels = [level]
    fid = text(fact.get("fact_id"), name + ".fact_id")
    datum = text(fact.get("datum_id"), name + ".datum_id")
    reference = text(fact.get("reference_id"), name + ".reference_id")
    if fact.get("system") is not None and fact["system"] != system:
        raise ValueError(name + "的系统与接口系统不同")
    kind = fact.get("measurement_kind")
    if kind not in CENTER_KINDS | set(OUTER_KINDS) | REFERENCE_KINDS:
        raise ValueError(name + ".measurement_kind无效；BOP须先依据本图定义明确测点类型")
    result = {"fact_id": fid, "original_value": fact["value"], "original_unit": fact["unit"],
              "datum_id": datum, "reference_id": reference, "measurement_kind": kind,
              "centerline_value_m": None, "source_ids": refs, "level": level, "issues": []}
    if value is None:
        result["issues"].append({"type": "接口高程未给出", "fact_id": fid, "issue": fact["issue"]})
    conversion = fact.get("centerline_conversion")
    if kind in CENTER_KINDS:
        if conversion is not None:
            raise ValueError(name + "已为中心测点，不能再次叠加测点转换")
        result["centerline_value_m"] = value
        result["centerline_reference_id"] = reference
        return result
    if conversion is None:
        result["issues"].append({"type": "测点尚未换算至中心", "fact_id": fid,
                                 "required": "有依据的实际外径" if kind in OUTER_KINDS else "到管中心的带符号高差及依据"})
        return result
    conversion = record(conversion, name + ".centerline_conversion")
    c_refs, c_level = evidence(conversion, name + ".centerline_conversion")
    target = text(conversion.get("target_reference_id"), name + ".centerline_conversion.target_reference_id")
    refs.extend(c_refs)
    levels.append(c_level)
    method = conversion.get("method")
    if kind in OUTER_KINDS:
        if method != "outer_radius":
            raise ValueError(name + "管外表面到中心须使用有依据的实际外径")
        diameter = record(conversion.get("outer_diameter"), name + ".outer_diameter")
        if diameter.get("measurement_kind") != "outer_diameter" or "dn" in diameter:
            raise ValueError("实际外径必须标明outer_diameter，不能使用DN替代")
        diameter_m, d_refs, d_level = quantity(diameter, name + ".outer_diameter")
        if diameter_m <= 0:
            raise ValueError("实际外径须为正数")
        offset = OUTER_KINDS[kind] * diameter_m / 2
        refs.extend(d_refs)
        levels.append(d_level)
    else:
        if method != "signed_offset":
            raise ValueError(name + "楼面/其他参照点须提供到管中心的带符号高差")
        offset, o_refs, o_level = quantity(conversion.get("offset"), name + ".offset")
        refs.extend(o_refs)
        levels.append(o_level)
    result.update(centerline_value_m=None if value is None else value + offset, centerline_offset_m=offset,
                  centerline_reference_id=target, source_ids=list(dict.fromkeys(refs)), level=weakest(levels))
    return result


def datum_path(transforms, source, target):
    """Use explicit directed offsets: z_to = z_from + offset.

    Every reachable alternative is checked for contradictory numerical offsets.
    A missing path is unresolved, never an implicit equal-datum assumption.
    """
    if not isinstance(transforms, list):
        raise ValueError("datum_transforms须为数组")
    graph = {}
    ids = set()
    for row in transforms:
        record(row, "datum_transform")
        tid = text(row.get("transform_id"), "transform_id")
        if tid in ids:
            raise ValueError("datum_transform编号重复")
        ids.add(tid)
        a, b = text(row.get("from_datum"), "from_datum"), text(row.get("to_datum"), "to_datum")
        if a == b:
            raise ValueError("同一基准不能再次定义基准换算")
        offset, refs, level = quantity(row, tid)
        graph.setdefault(a, []).append((b, offset, tid, refs, level))
        graph.setdefault(b, []).append((a, -offset, tid, refs, level))
    values = {source: (0., [], [], [])}
    queue = deque([source])
    while queue:
        a = queue.popleft()
        value, path, refs, levels = values[a]
        for b, offset, tid, edge_refs, level in sorted(graph.get(a, []), key=lambda e: (e[0], e[2])):
            candidate = value + offset
            if b in values:
                if not math.isclose(values[b][0], candidate, rel_tol=1e-12, abs_tol=1e-9):
                    raise ValueError("基准换算链存在互相矛盾的高差")
                continue
            values[b] = (candidate, path + [tid], refs + edge_refs, levels + [level])
            queue.append(b)
    return values.get(target)


def dependency_graph(spec, result):
    """Express the same calculation in the existing qto-calculation-graph/1.0.

    Its existing recalculator handles fingerprints and stale review states. This
    function supplies facts and actual expressions; it does not create a second
    dependency engine or make the reviewed status of source facts implicit.
    """
    nodes = {}
    rule_id = "typed-elevation-conversion/1.0"

    def add_fact(row, id_field="fact_id"):
        fid = "fact:" + row[id_field]
        node = {"id": fid, "value": None if row["value"] is None else row["value"] * factor(row["unit"]), "unit": "m",
                "source_ids": row["source_ids"], "quantity_level": "未决量" if row["value"] is None else QUANTITY_LEVELS[row["level"]],
                "original_value": row["value"], "original_unit": row["unit"]}
        if fid in nodes and nodes[fid] != node:
            raise ValueError("同一数值事实编号对应了不同输入")
        nodes[fid] = node
        return fid

    def add_calculation(nid, expression, variables, row):
        nodes[nid] = {"id": nid, "expression": expression, "variables": variables, "unit": "m",
                      "source_ids": row["source_ids"], "rule_ids": [rule_id],
                      "quantity_level": QUANTITY_LEVELS[row["level"]],
                      "allow_negative": nid not in ("vertical_projection", "route_length")}
        return nid

    def unknown(nid, refs):
        nodes[nid] = {"id": nid, "value": None, "unit": "m", "source_ids": refs, "quantity_level": "未决量"}
        return nid

    transforms = {r["transform_id"]: r for r in spec.get("datum_transforms", [])}
    centers = []
    for side, endpoint in zip(("from", "to"), result["endpoints"]):
        row = spec[side]
        original = add_fact(row)
        kind = row["measurement_kind"]
        conversion = row.get("centerline_conversion")
        if kind in CENTER_KINDS:
            center = original
        elif conversion is None:
            center = unknown(side + ":center", row["source_ids"])
        elif kind in OUTER_KINDS:
            diameter = add_fact(conversion["outer_diameter"])
            center = add_calculation(side + ":center", "e + d / 2" if OUTER_KINDS[kind] > 0 else "e - d / 2",
                                     {"e": original, "d": diameter}, conversion)
        else:
            offset = add_fact(conversion["offset"])
            center = add_calculation(side + ":center", "e + h", {"e": original, "h": offset}, conversion)
        if endpoint.get("normalized_value_m") is None and "datum_transform_ids" not in endpoint:
            centers.append(unknown(side + ":normalized", row["source_ids"]))
            continue
        current_datum = row["datum_id"]
        for tid in endpoint.get("datum_transform_ids", []):
            transform = transforms[tid]
            offset = add_fact(transform, "transform_id")
            forward = current_datum == transform["from_datum"]
            center = add_calculation(side + ":datum:" + tid, "e + h" if forward else "e - h",
                                     {"e": center, "h": offset}, transform)
            current_datum = transform["to_datum"] if forward else transform["from_datum"]
        centers.append(center)
    projection_basis = {"source_ids": result["source_ids"], "level": "confirmed"}
    delta = add_calculation("vertical_difference", "t - f", {"f": centers[0], "t": centers[1]}, projection_basis)
    projection = add_calculation("vertical_projection", "abs(h)", {"h": delta}, projection_basis)
    route = spec.get("route", {"kind": "unresolved"})
    kind = route["kind"]
    if kind == "vertical":
        add_calculation("route_length", "h", {"h": projection}, route)
    elif kind in ("straight", "orthogonal") and route.get("horizontal_displacement") is not None:
        horizontal = add_fact(route["horizontal_displacement"])
        expression = "sqrt(h ** 2 + x ** 2)" if kind == "straight" else "h + x"
        add_calculation("route_length", expression, {"h": projection, "x": horizontal}, route)
    else:
        unknown("route_length", result["source_ids"])
    return {"schema": "qto-calculation-graph/1.0",
            "project_id": spec.get("calculation_id", "elevation:" + spec["system"] + ":" + spec["from"]["reference_id"] + ":" + spec["to"]["reference_id"]),
            "sources": copy.deepcopy(spec["sources"]),
            "rules": [{"id": rule_id, "sha256": hashlib.sha256(b"centre=elevation+typed offset;datum=centre+signed transform;length=evidenced route v1").hexdigest(),
                       "status": "trial", "basis": "explicit sourced measurement and datum transformations"}],
            "nodes": list(nodes.values())}


def evaluate_elevation_link(spec, output_unit="m", expected_system=None, previous_calculation=None):
    """Recompute typed elevations and an explicitly evidenced route length.

    Vertical projection is not route length. Straight and single horizontal-plus-
    vertical routes require separately sourced horizontal displacement. Other
    routing remains unresolved and should be measured as explicit geometry.
    """
    record(spec, "elevation_facts")
    if spec.get("schema") != SCHEMA:
        raise ValueError("标高事实schema无效")
    divisor = factor(output_unit)
    system = text(spec.get("system"), "system")
    if expected_system is not None and system != expected_system:
        raise ValueError("标高事实系统与跨图接口系统不同")
    source_check = check_sources(spec)
    endpoints = [resolve_centerline(spec.get(side), system, side) for side in ("from", "to")]
    if endpoints[0]["fact_id"] == endpoints[1]["fact_id"]:
        raise ValueError("接口两端不能复用同一标高事实编号")
    target = text(spec.get("target_datum", endpoints[0]["datum_id"]), "target_datum")
    refs = list(dict.fromkeys(v for e in endpoints for v in e["source_ids"]))
    levels = [e["level"] for e in endpoints]
    issues = [i for e in endpoints for i in e["issues"]]
    for endpoint in endpoints:
        path = datum_path(spec.get("datum_transforms", []), endpoint["datum_id"], target)
        if path is None:
            issues.append({"type": "不同基准缺少换算依据", "fact_id": endpoint["fact_id"],
                           "from_datum": endpoint["datum_id"], "to_datum": target})
            endpoint["normalized_value_m"] = None
            continue
        offset, transform_ids, path_refs, path_levels = path
        refs.extend(path_refs)
        levels.extend(path_levels)
        endpoint.update(datum_offset_m=offset, datum_transform_ids=transform_ids,
                        normalized_value_m=None if endpoint["centerline_value_m"] is None else endpoint["centerline_value_m"] + offset)
    route = spec.get("route", {"kind": "unresolved"})
    record(route, "route")
    kind = route.get("kind")
    if kind not in ("vertical", "straight", "orthogonal", "unresolved"):
        raise ValueError("路线形态无效")
    if kind != "unresolved":
        r_refs, r_level = evidence(route, "route")
        refs.extend(r_refs)
        levels.append(r_level)
    horizontal = route.get("horizontal_displacement")
    horizontal_m = None
    if horizontal is not None:
        horizontal_m, h_refs, h_level = quantity(horizontal, "route.horizontal_displacement")
        if horizontal_m < 0:
            raise ValueError("水平位移不能为负数")
        refs.extend(h_refs)
        levels.append(h_level)
    if kind == "vertical" and horizontal_m not in (None, 0.):
        raise ValueError("竖直路线存在非零水平位移，不能按标高差计算全长")
    result = {"schema": SCHEMA + "/result", "system": system, "status": "UNRESOLVED",
              "value": None, "unit": output_unit, "quantity_level": "未决量", "target_datum": target,
              "review_state": "model_working_unreviewed", "business_adoption_allowed": False,
              "vertical_difference": None, "vertical_projection": None, "route_kind": kind,
              "horizontal_displacement": None if horizontal_m is None else horizontal_m / divisor,
              "endpoints": endpoints, "issues": issues,
              "method": "标高事实、测点和基准换算后，按有证据的路线形态计算；图面跨图距离不参与",
              "input_sha256": hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()}
    if all(e.get("normalized_value_m") is not None for e in endpoints):
        delta = endpoints[1]["normalized_value_m"] - endpoints[0]["normalized_value_m"]
        result.update(vertical_difference=delta / divisor, vertical_projection=abs(delta) / divisor)
        length = None
        if kind == "vertical":
            length = abs(delta)
        elif kind in ("straight", "orthogonal") and horizontal_m is not None:
            length = math.hypot(delta, horizontal_m) if kind == "straight" else abs(delta) + horizontal_m
        elif kind in ("straight", "orthogonal"):
            issues.append({"type": "路线水平位移未取证，竖向投影不能充当管长"})
        else:
            issues.append({"type": "路线形态未取证，竖向投影不能充当管长"})
        if length is not None:
            result.update(status="RESOLVED", value=length / divisor, quantity_level=QUANTITY_LEVELS[weakest(levels)])
    result["source_ids"] = list(dict.fromkeys(refs))
    result["dependency_source_ids"] = copy.deepcopy(result["source_ids"])
    result["evidence_level"] = weakest(levels)
    result["source_reference_check"] = source_check
    from recompute_qto_dependencies import recalculate
    result["calculation_graph"] = dependency_graph(spec, result)
    result["calculation_run"] = recalculate(result["calculation_graph"], previous=previous_calculation)
    recalculated = next(row["value"] for row in result["calculation_run"]["nodes"] if row["id"] == "route_length")
    if (recalculated is None) != (result["value"] is None) or (recalculated is not None and not math.isclose(recalculated / divisor, result["value"], rel_tol=1e-12, abs_tol=1e-10)):
        raise ValueError("标高直接复算与已有依赖引擎结果不一致")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--unit", default="m", choices=sorted(UNITS))
    parser.add_argument("--previous", help="旧标高计算结果，用已有依赖引擎判断审阅是否失效")
    args = parser.parse_args()
    source, output = Path(args.input).resolve(), Path(args.output).resolve()
    if source == output or output.exists():
        parser.error("输出须为尚不存在的新文件，不能覆盖输入或旧成果")
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    previous = None
    if args.previous:
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8-sig"))["calculation_run"]
    result = evaluate_elevation_link(raw, args.unit, previous_calculation=previous)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["status"])


if __name__ == "__main__":
    main()
