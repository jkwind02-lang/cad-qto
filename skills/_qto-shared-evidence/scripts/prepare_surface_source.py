"""Source-selected faces and model-declared finish relations to the surface manifest.

No room-name lookup, layer catalogue, default thickness or volume inference lives
here. The model selects table windows, ordered fragments, layer clauses and
applicability conditions. This adapter verifies those selections, recomputes the
selected CAD face, and carries their provenance to the existing calculator.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from model_recognition_basis import recognition_basis, texts
from qto_cad_source_contract import canonical_scale
from qto_cad_geometry import read_dxf_with_diagnostics
from qto_recognition_workbench import _text
from read_cad_scope import _select


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_record(ref, base):
    require(isinstance(ref, dict), "文件证据须有path和sha256")
    path = (Path(base) / _text(ref.get("path"), "证据path")).resolve()
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == ref.get("sha256"), "文件证据哈希不一致")
    return path, json.loads(data.decode("utf-8-sig"))


def prepare_surfaces(index, objects, operations, operations_path, ledger_path,
                     ledger, entries, refs, factor, unit_evidence, scope, scope_basis):
    require(operations_path is not None, "面层接续须绑定实际operations_path")
    operations_path = Path(operations_path).resolve()
    require(json.loads(operations_path.read_text(encoding="utf-8-sig")) == operations,
            "操作文件与面层接续内容不一致")
    require(index["layout"] == "Model", "普通源线网面层当前仅接受Model实例路径")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "qto-surfaces-envelope/scripts"))
    from extract_surface_linework import extract

    documents, source_rows, evidence_rows = {}, {}, []
    prepared, exclusions, bindings, geometry_cache = [], [], [], {}

    def source(path, role, file_role, meaning):
        path = Path(path).resolve()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        sid = "SRC-" + hashlib.sha256((str(path) + role).encode()).hexdigest()[:20]
        source_rows[sid] = dict(source_id=sid, path=str(path), sha256=sha,
            source_role=role, file_role=file_role, selection="selected",
            source_type="源对象及模型工作判读", reason=meaning, neutral_facts=meaning,
            not_used_impact="失去所述来源、几何或构造适用依据")
        return sid

    operation_sid = source(operations_path, "project_document", "json", "实际模型判读与有源构造选择")
    source(ledger_path, "project_document", "json", "本专业模型工作识别记录")
    drawing_sid = source(index["source"]["path"], "project_drawing", "dxf", "实际用于计算的CAD源")
    original = Path(index["authoritative_source"]["path"])
    source(original, "project_drawing", original.suffix.lower().lstrip("."), "原始权威图纸身份")

    def evidence(ref):
        bound = refs.evidence(ref)
        idx, obj = refs.index(ref)
        row = obj[ref["object_id"]]
        key = idx["source"]["path"]
        if key not in documents:
            documents[key] = read_dxf_with_diagnostics(key)[0]
        doc = documents[key]
        layer = doc.layers.get(row["effective_layer"])
        require(row["visible_in_entity"] and not layer.is_off() and not layer.is_frozen()
                and layer.dxf.get("plot", 1) == 1
                and row["effective_layer"].casefold() != "defpoints",
                "关闭、冻结或不出图对象不能驱动面层做法；辅助资料须单列")
        parent = row.get("parent_object_id")
        while parent:
            ancestor = obj[parent]
            parent_layer = doc.layers.get(ancestor["effective_layer"])
            require(ancestor["visible_in_entity"] and not ancestor.get("issues"),
                    "依据父实例存在可见性或几何问题")
            require(not parent_layer.is_off() and not parent_layer.is_frozen()
                    and parent_layer.dxf.get("plot", 1) == 1
                    and ancestor["effective_layer"].casefold() != "defpoints",
                    "依据父实例所在图层关闭、冻结或不出图")
            parent = ancestor.get("parent_object_id")
        source(key, "project_drawing", "dxf", "构造或条件的有源文字")
        auth = Path(idx["authoritative_source"]["path"])
        source(auth, "project_drawing", auth.suffix.lower().lstrip("."), "原文原始图纸")
        bound["effective_layer"] = row["effective_layer"]
        bound["layer_state"] = dict(off=False, frozen=False, plot=True)
        return bound

    def fragment(group):
        require(isinstance(group, dict), "分片选择须为对象")
        parts = group.get("fragments")
        require(isinstance(parts, list) and parts, "须明确有序原文分片")
        resolved = [evidence(r) for r in parts]
        require(len({(r["index_sha256"], r["object_id"], r.get("excerpt")) for r in resolved}) == len(resolved),
                "原文分片重复")
        separator = group.get("separator", "")
        require(isinstance(separator, str), "分片连接符须为文字")
        value = separator.join(r.get("excerpt") or r.get("text") or "" for r in resolved)
        require(bool(value.strip()), "源分片没有文字")
        return dict(text=value, fragments=resolved,
                    interpretation=_text(group.get("interpretation"), "分片语义由模型说明"))

    def conditions(rows, fields):
        require(isinstance(rows, list), "适用条件须为数组")
        resolved, matches = [], True
        for condition in rows:
            field = _text(condition.get("field"), "条件字段")
            values = texts(condition.get("values"), "条件values")
            require(condition.get("operator") in {"in", "not_in"}, "条件只接受in/not_in")
            require(field in fields and fields[field] is not None, "适用条件字段缺失，不得默认分支: " + field)
            proof = [evidence(r) for r in condition.get("evidence_refs", [])]
            require(proof, "适用条件缺原文")
            match = (fields[field] in values) == (condition["operator"] == "in")
            matches = matches and match
            resolved.append(dict(condition=copy.deepcopy(condition), actual_value=fields[field],
                                 matches=match, source_refs=proof))
        return matches, resolved

    def parameters(rows):
        require(isinstance(rows, list), "参数须为数组；没有参数显式传空数组")
        answer = []
        for row in rows:
            require(isinstance(row, dict), "参数须为对象")
            for key in ("name", "unit", "condition"):
                _text(row.get(key), "参数" + key)
            status = row.get("status")
            require(status in {"fixed", "minimum", "conditional", "unresolved"}, "参数状态无效")
            proof = [evidence(r) for r in row.get("evidence_refs", [])]
            require(proof, "参数缺原文")
            if status in {"conditional", "unresolved"}:
                require(row.get("value") is None, "未决或条件参数不得选择值，保留候选及原条件")
            else:
                value = row.get("value")
                require(isinstance(value, (float, int)) and not isinstance(value, bool)
                        and math.isfinite(value) and value >= 0, "固定或最小参数须为有限非负值")
                numeric = {float(x) for p in proof for x in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", p.get("excerpt") or p["text"])}
                require(float(value) in numeric, "参数值未出现在所引原文中")
            answer.append(dict(**copy.deepcopy(row), source_refs=proof))
        return answer

    definitions = {}
    for definition in operations.get("definitions", []):
        did = _text(definition.get("id"), "做法定义id")
        require(did not in definitions, "做法定义重复")
        definitions[did] = definition
    surfaces = operations.get("surfaces")
    require(definitions and isinstance(surfaces, list) and surfaces, "须明确表面与有源做法定义")
    unit_context = [evidence(r) for r in operations.get("unit_context", [])]
    if unit_evidence is not None:
        require(unit_context, "源单位未定义时须保留适用单位条款的上下文分片")
    seen, used = set(), set()
    for surface in surfaces:
        require(not set(surface) & {"area", "area_m2", "quantity", "geometry", "inputs", "volume_m3"},
                "禁止覆盖源面积、坐标或推入体积")
        oid = surface.get("object_id")
        entry = entries.get(surface.get("adjudication_id"))
        require(oid in scope and entry and entry["decision"] == "include"
                and entry["professional_owner"] == "qto-surfaces-envelope"
                and oid in entry["object_ids"], "表面须绑定本专业include识别记录")
        sid = _text(surface.get("surface_id"), "稳定surface_id")
        require(sid not in seen and oid not in used, "同一物理表面或源实例重复")
        seen.add(sid);used.add(oid)
        name = _text(surface.get("name"), "表面名称")
        require(name in (objects[oid].get("text") or ""), "表面名称未绑定本源标签原文")
        fields = surface.get("scope_fields")
        require(isinstance(fields, dict) and all(isinstance(k, str) and isinstance(v, str) and v for k,v in fields.items()),
                "scope_fields须为模型有源声明的文字字段")
        fields = copy.deepcopy(fields)
        require(fields.get("room_name", name) == name, "房间条件不能改写当前源标签身份")
        fields["room_name"] = name
        identity_evidence = [evidence(r) for r in surface.get("source_identity_evidence", [])]
        definition = definitions.get(surface.get("definition_id"))
        require(definition is not None, "所选做法定义不存在")
        assignment = surface.get("assignment", {})
        code = fragment(assignment.get("code")); definition_code = fragment(definition.get("code"))
        require(code["text"] == definition_code["text"], "单元格分片编号与所选做法原文不一致")
        require(assignment.get("column_role") == definition.get("column_role")
                and assignment.get("column_role"), "邻列用途冲突，不能只按编号匹配")
        floor = fragment(assignment.get("floor"))
        require(fields.get("floor") == floor["text"], "楼层条件与实际合并格原文不一致")
        row_binding = fragment(assignment.get("row")); column = fragment(assignment.get("column"))
        require(name in row_binding["text"], "房间或表面名称未在指定表行原文中出现；须补源身份解释")
        cp, context = file_record(assignment.get("context"), refs.base_dir)
        groups = {r["id"]:r for r in context.get("regions", [])}
        region_ids = assignment.get("regions", {})
        require(set(region_ids) == {"cell", "row", "column", "floor"}, "须绑定单元格、行、列头及合并格上下文")
        table_idx, table_objects = refs.index(assignment["code"]["fragments"][0])
        require(context.get("index_sha256") == table_idx["index_sha256"], "表格上下文与代码原文不属于同一索引快照")
        for role, binding in [("cell", code), ("row", row_binding), ("column", column), ("floor", floor)]:
            require(region_ids[role] in groups, "表格上下文区域不存在")
            group = groups[region_ids[role]]
            require(group["bounds"] == group["selection"].get("bounds"), "上下文显示范围与实际选择范围不一致")
            actual = [r["object_id"] for r in _select(table_idx, table_objects, group["selection"])]
            require(actual == group["object_ids"], "表格上下文成员与当前源不一致")
            require(all(r["index_sha256"] == table_idx["index_sha256"] and r["object_id"] in actual
                        for r in binding["fragments"]), "分片不属于声明的行列上下文")
        cell, row_window, column_window, floor_window = [groups[region_ids[k]]["bounds"] for k in ("cell", "row", "column", "floor")]
        require(row_window[1] <= cell[1] <= cell[3] <= row_window[3]
                and column_window[0] <= cell[0] <= cell[2] <= column_window[2]
                and floor_window[1] <= row_window[1] <= row_window[3] <= floor_window[3],
                "单元格与所选行、列或合并楼层格范围不一致")
        source(cp, "project_document", "json", "分片、行列及合并格上下文")
        applies, definition_conditions = conditions(definition.get("conditions", []), fields)
        require(applies, "做法适用条件与本实例冲突")

        geometry = surface.get("source_face", {})
        gp, request = file_record(geometry.get("request"), refs.base_dir)
        rp, stored = file_record(geometry.get("result"), refs.base_dir)
        require(request["drawing"]["sha256"] == index["source"]["sha256"]
                and Path(request["drawing"]["path"]).resolve() == Path(index["source"]["path"]).resolve()
                and canonical_scale(request["unit"]) == factor, "面域源图或单位与识别来源不一致")
        require(all(isinstance(r.get("path"), list) and r["path"]
                    and all(isinstance(p, str) for p in r["path"]) for r in request["segments"]),
                "面层接续目前接受普通实体/块实例路径；阵列须另行展开和核验")
        selected = {"CADINST-Model/" + "/".join(r["path"]) for r in request["segments"]}
        require(selected <= set(scope) and selected <= set(entry["object_ids"]), "源边超出实际专业审阅范围")
        used.update(selected)
        if gp not in geometry_cache:
            geometry_cache[gp] = extract(copy.deepcopy(request))
        fresh = geometry_cache[gp]
        require(not any(fresh[k] for k in ("failures", "excluded", "source_context_issues", "source_identity_issues", "invalid_rings")),
                "源面域存在未闭合来源问题，不能默认为完整几何")
        fid = geometry.get("face_id")
        faces = [f for f in fresh["faces"] if f["face_id"] == fid]
        saved_faces = [f for f in stored.get("faces", []) if f["face_id"] == fid]
        require(len(faces) == 1 and faces == saved_faces, "所选面域与原图实际复算不一致")
        require(stored.get("drawing", {}).get("sha256") == index["source"]["sha256"], "面域证据来源不一致")
        face = faces[0]
        require(any(oid == "CADINST-Model/" + "/".join(r["path"])
                    and any(f["face_id"] == fid for f in r["faces"]) for r in fresh["label_candidates"]),
                "所选面域未包含已判读的源标签实例；不得仅按名称借面")
        geom_sid = source(rp, "calculation_evidence", "json", "已从当前源线网真实复算的指定面域")
        source(gp, "calculation_evidence", "json", "逐源边、单位和解释分界选择")
        binding = dict(surface_id=sid, object_id=oid, adjudication_id=entry["adjudication_id"],
            source_identity_evidence=identity_evidence,
            scope_fields=fields, assignment=dict(code=code, row=row_binding, column=column, floor=floor,
            context_path=str(cp), regions=region_ids), definition_id=definition["id"],
            definition_code=definition_code, definition_conditions=definition_conditions,
            source_face=dict(face_id=fid, area_m2=face["area_m2"], boundary=face["boundary"],
                             request_path=str(gp), result_path=str(rp)), layers=[])

        def ev(eid, source_id, category, locator, fact, keys):
            evidence_rows.append(dict(evidence_id=eid, source_id=source_id, evidence_class=category,
                fact_status="reference", locator=locator, fact=fact,
                extraction="程序核验源选择；模型判断工程适用", quality="来源与显式约束已检查，未人工签认",
                object_keys=keys, limitation="只适用于声明表面、构造层、范围和未决条件"))
        prefix = hashlib.sha256(sid.encode()).hexdigest()[:16]
        identity_ev, geometry_ev = "EV-ID-" + prefix, "EV-GEOM-" + prefix
        ev(identity_ev, drawing_sid, "source_identity", oid, "唯一源实例及图页: " + index["layout"], [sid])
        ev(geometry_ev, geom_sid, "surface_geometry", "faces/" + fid,
           "源线网复算面积，不接受操作中填写的面积；边界判断仍属模型", [sid])
        layer_ids = set()
        for layer in definition.get("layers", []):
            selected_layer = copy.deepcopy(layer)
            variants = selected_layer.pop("variants", None)
            variant_conditions = []
            if variants is not None:
                choices = [(v, conditions(v.get("conditions", []), fields)) for v in variants]
                matched = [(v, c) for v, (ok, c) in choices if ok]
                require(len(matched) == 1, "层条件分支必须唯一匹配，不能默认楼梯或未知条件")
                variant, variant_conditions = matched[0]
                require(not set(variant) - {"conditions", "clauses", "parameters"}, "条件分支只能选择有源层条款及参数")
                selected_layer.update({k:v for k,v in variant.items() if k != "conditions"})
            lid = _text(selected_layer.get("layer_id"), "layer_id")
            require(lid not in layer_ids, "同一做法不能重复构造层")
            layer_ids.add(lid)
            clause = fragment(selected_layer.get("clauses"))
            resolved = dict(layer_id=lid, role=selected_layer.get("role"), clause=clause,
                parameters=parameters(selected_layer.get("parameters")), conditions=variant_conditions,
                coverage_basis=_text(selected_layer.get("coverage_basis"), "逐层覆盖依据"))
            binding["layers"].append(resolved)
            if resolved["role"] == "substrate":
                exclusions.append(dict(surface_id=sid, layer=resolved, reason="模型明确归为结构或既有基层，不作为本轮饰面数量"))
                continue
            require(resolved["role"] == "finish", "每层须明确finish或substrate责任")
            require(selected_layer.get("measurement") in {"horizontal_projection", "deferred"},
                    "当前接续只提供水平参考面积或未决；最薄厚度、选厚不得自动转体积")
            item_id = _text(surface.get("item_ids", {}).get(lid), "稳定item_id")
            layer_ev = "EV-LAYER-" + hashlib.sha256((sid + lid).encode()).hexdigest()[:20]
            ev(layer_ev, operation_sid, "layer_assignment", "definitions/" + definition["id"] + "/" + lid,
               json.dumps(dict(assignment=binding["assignment"], layer=resolved), ensure_ascii=False), [sid, lid])
            deferred = selected_layer["measurement"] == "deferred"
            item = dict(item_id=item_id, name=name + "—" + clause["text"] + "（水平范围参考）",
                physical_surface_id=sid, layer_id=lid, object_type=_text(selected_layer.get("object_type"), "层对象类别"),
                unit="m²", side_scope=_text(surface.get("side_scope"), "侧别"),
                measurement_basis=_text(surface.get("measurement_basis"), "计量口径"),
                result_use=_text(selected_layer.get("result_use", surface.get("result_use")), "结果用途"), scope_status="unresolved" if deferred else "partial",
                adoption_role="reference", quantity_role="physical_area", method="deferred" if deferred else "horizontal_net",
                inputs={} if deferred else dict(gross_area_m2=face["area_m2"]),
                evidence_refs=[identity_ev, geometry_ev, layer_ev], rule_refs=texts(operations.get("rule_refs"), "rule_refs"),
                unresolved_refs=texts(list(dict.fromkeys(surface.get("unresolved_refs", []) + selected_layer.get("unresolved_refs", []))), "未决范围"),
                source_bound_layer=resolved)
            if deferred:
                item["deferred_reason"] = _text(selected_layer.get("deferred_reason"), "未决说明")
            prepared.append(item)
        bindings.append(binding)
    require(prepared, "没有可交接构造层；不能输出空清单当作零量")
    return dict(manifest_version="qto-surfaces-envelope-manifest/1.0", task=copy.deepcopy(operations.get("task")),
        sources=list(source_rows.values()), evidence=evidence_rows, rules=copy.deepcopy(operations.get("rules")),
        unresolved=copy.deepcopy(operations.get("unresolved")), items=prepared,
        source_preparation=dict(schema="qto-source-specialist-preparation/1.0", target="surfaces",
            index_sha256=index["index_sha256"], recognition_basis=recognition_basis(ledger, ledger_path),
            unit="m", scale_to_m=factor, unit_evidence=unit_evidence, unit_context=unit_context, scope_basis=scope_basis,
            bindings=bindings, excluded_layers=exclusions, unprocessed_object_ids=[o for o in scope if o not in used],
            formal_adoption_allowed=False, meaning="模型有源判读的可靠输入接续，不是自动理解或正式工程采用"))
