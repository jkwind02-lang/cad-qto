#!/usr/bin/env python3
"""Source-bound local CAD context and explicit model operations.

No layer, block name, proximity or drawing convention is a professional decision.
The index is an observation aid; unresolved objects remain visible. Pipe export
uses the existing working-recognition ledger and existing inference contract.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from qto_cad_geometry import (IDENTITY, LOCATION_SCHEMA, compose_matrix, entity_geometry,
    measure_part, part_extrema, summarize, transform_geometry, transform_point, transform_bounds,
    read_dxf_with_diagnostics)
from qto_cad_source_contract import canonical_scale, verify_source_file, unit_tokens, verify_unit_declaration_texts, verify_unit_corroboration, UNIT_APPLIES_TO
from model_recognition_basis import load_working_entries, recognition_basis

SCHEMA = "qto-recognition-context/1.0"
UNITS = {0: "Unitless", 1: "Inches", 2: "Feet", 4: "Millimeters",
         5: "Centimeters", 6: "Meters", 7: "Kilometers"}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空文本")
    return value.strip()


def _number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} 必须是大于等于 {minimum} 的有限数值")
    return float(value)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _unit_tokens(text, with_spans=False):
    # Shared with the pipe adapter's unit declaration; one implementation only.
    return unit_tokens(text, with_spans)


def index_digest(index):
    value={k:v for k,v in index.items() if k!="index_sha256"}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()


def _unmeasured_envelope(entity, facts=None):
    """Reuse the shared conservative-location guarantee; anchors and fit points are not envelopes."""
    if entity.dxftype() not in {'HATCH', 'SPLINE'}:
        return None
    facts = facts if facts is not None else entity_geometry(entity)
    box = facts.get('location_bbox3d')
    if (facts.get('location_schema_version') == LOCATION_SCHEMA and facts.get('location_status') == 'bounded'
            and isinstance(box, (list, tuple)) and len(box) == 6
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and all(box[i] <= box[i+3] for i in range(3))):
        return list(box)
    return None


def _annotation_dn(label,dn):
    dns={int(a or b) for a,b in re.findall(r"(?i)(?:\bDN\s*(\d+)\b|\b(\d+)\s*A\b)",label)}
    match=re.fullmatch(r"DN([1-9]\d*)",_text(dn,'dn'))
    if not match or dns!={int(match[1])}:raise ValueError("DN文字不唯一或声明DN与原图不符；须进一步定位")


def _verified_index(index):
    if index.get("schema_version") != SCHEMA or index.get("kind") != "index":
        raise ValueError("需要本工具生成的实例索引")
    verify_source_file(index["source"], "DXF来源")
    verify_source_file(index["authoritative_source"], "权威来源")
    if 'source_identity_issues' not in index:
        raise ValueError('旧索引没有原图身份诊断，须从当前DXF重建索引')
    if index.get("index_sha256") != index_digest(index):
        raise ValueError("实例索引内容已改变；须从当前DXF重新生成，不得手写坐标替换原图几何")
    return {item["object_id"]: item for item in index["objects"]}


def build_index(source, layout="Model", authoritative_source=None, max_depth=32, scope_bounds=None):
    """Read a hashed DXF; retain each nested/array instance and failed object."""
    import ezdxf
    from ezdxf.math import Vec3
    verify_source_file(source, "DXF来源")
    authoritative_source = authoritative_source or source
    verify_source_file(authoritative_source, "权威来源")
    if Path(source["path"]).suffix.lower() != ".dxf":
        raise ValueError("索引入口需要DXF；DWG须先通过现有转换与中性取证流程")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 128:
        raise ValueError("max_depth 必须在1到128之间")
    doc,read_diagnostics = read_dxf_with_diagnostics(source["path"])
    if layout not in doc.layouts:
        raise ValueError(f"图纸不存在布局 {layout}")
    result, warnings = [], []
    if any(w.get('blocks_identity_binding') is not False for w in read_diagnostics['source_read_warnings']):
        warnings.append('源文件解析产生警告；详见 source_read_diagnostics，不能仅以读取成功认定无损')
    skipped_count=0
    if scope_bounds is not None:
        if (not isinstance(scope_bounds,list) or len(scope_bounds)!=4
                or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in scope_bounds)
                or scope_bounds[0]>=scope_bounds[2] or scope_bounds[1]>=scope_bounds[3]):
            raise ValueError("局部索引范围须为 [xmin,ymin,xmax,ymax]")
    envelope_cache={}
    fact_cache={}
    def facts_of(entity):
        handle=entity.dxf.get('handle')
        # Virtual array cells have no handle and therefore never share transforms.
        if handle and handle in fact_cache:return fact_cache[handle]
        value=entity_geometry(entity)
        if handle:fact_cache[handle]=value
        return value
    def envelope(name,stack=()):
        """Cache a definition's measurable bounds; missing extents remain partial."""
        if name in envelope_cache:return envelope_cache[name]
        if name in stack or len(stack)>=max_depth:return None,False
        definition=doc.blocks.get(name)
        if definition is None:return None,False
        boxes=[];complete=True
        for e in definition:
            try:
                f=facts_of(e)
                if e.dxftype()=='INSERT':
                    child,known=envelope(e.dxf.name,stack+(name,))
                    complete &= known
                    if child:
                        # Four corner cells bound a regular insertion array.
                        for r in {0,e.dxf.row_count-1}:
                            for c in {0,e.dxf.column_count-1}:
                                mat=list(f['insert_matrix_wcs'])
                                delta=e.ocs().to_wcs(Vec3(c*e.dxf.column_spacing,r*e.dxf.row_spacing).rotate_deg(e.dxf.rotation))
                                for k in range(3):mat[4*k+3]+=delta[k]
                                boxes.append(transform_bounds(child,mat))
                elif f.get('geometry'):
                    boxes.append(f['bbox3d'])
                else:
                    location_box = _unmeasured_envelope(e, f)
                    if location_box is not None:
                        boxes.append(location_box)
                    else:
                        # Glyph anchors and unknown fill/spline data are not complete extents.
                        complete=False
            except Exception:
                complete=False
        box=([min(b[i] for b in boxes) for i in range(3)]+[max(b[i+3] for b in boxes) for i in range(3)]) if boxes else None
        envelope_cache[name]=(box,complete and bool(boxes))
        return envelope_cache[name]
    receipt = Path(source["path"] + ".export.json")
    conversion = None
    if receipt.exists():
        conversion = _json(receipt)
        if conversion.get("output_sha256") != source["sha256"]:
            raise ValueError("转换记录与当前DXF不一致")
        if authoritative_source != source and conversion.get("source_sha256") != authoritative_source["sha256"]:
            raise ValueError("转换记录与权威来源不一致")
        if conversion.get("exchange_geometry_review_required") is not False:
            warnings.append("交换几何仍需复核；索引可辅助识图，不能据此确认原DWG已完整读入")
    elif authoritative_source != source:
        warnings.append("未提供转换记录；DXF与权威来源之间的几何对应尚未验证")

    def visit(entity, parent_path, matrix, inherited_layer, ancestors, virtual=None, cell=None):
        nonlocal skipped_count
        original = entity
        entity = virtual or entity
        handle = str(original.dxf.get("handle") or "")
        if not handle:
            raise ValueError("来源图元缺少稳定句柄，请先保存可审计DXF")
        token = handle + (f"[{cell[0]},{cell[1]}]" if cell is not None else "")
        path = parent_path + [token]
        oid = "CADINST-" + layout + "/" + "/".join(path)
        kind = entity.dxftype()
        layer = str(entity.dxf.get("layer", "0"))
        effective_layer = inherited_layer if layer == "0" and inherited_layer else layer
        row = dict(object_id=oid, source_path=path, source_handle=handle,
                   parent_object_id=("CADINST-" + layout + "/" + "/".join(parent_path)) if parent_path else None,
                   entity_type=kind, layer=layer, effective_layer=effective_layer,
                   layout=layout, text=None, geometry=None, bbox=None, bbox3d=None,
                   anchor=None, raw_length=None, issues=[], review_state="unprocessed",
                   geometry_status="unresolved", block_name=None, attributes={},
                   visible_in_entity=not bool(entity.dxf.get("invisible", 0)),
                   instance_transform=list(matrix))
        try:
            facts = facts_of(entity)
            if facts.get('native_data') is not None:
                from cad_native.basic_data import transform_data
                from ezdxf.math import Matrix44
                row['native_data']=copy.deepcopy(facts['native_data'])
                # Common geometry uses column vectors; ezdxf uses row vectors.
                native_matrix=Matrix44([matrix[c*4+r] for r in range(4) for c in range(4)])
                row['native_geometry_wcs']=transform_data(facts['native_data']['basic_data'],native_matrix)
            row["issues"] = list(facts.get("geometry_issues", []))
            row["anchor"] = transform_point(facts.get("anchor"), matrix)
            if kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                row["text"] = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text)
            location_box = _unmeasured_envelope(entity, facts)
            if facts.get("geometry"):
                row["geometry"] = transform_geometry(facts["geometry"], matrix)
                summary = summarize(row["geometry"])
                row.update(bbox=summary["bbox"], bbox3d=summary["bbox3d"],
                           raw_length=summary["length"], geometry_status="measurable")
            elif location_box is not None:
                # Shared HATCH/SPLINE anchors now exist. Consume their envelope before an anchor can mask it.
                world_box = transform_bounds(location_box, matrix)
                row.update(bbox3d=world_box, bbox=[world_box[0],world_box[1],world_box[3],world_box[4]],
                           geometry_status='unsupported_measurement', location_method='conservative_source_envelope',
                           location_schema_version=LOCATION_SCHEMA, location_status='bounded',
                           location_bbox3d=world_box, location_basis=facts.get('location_basis'),
                           measurement_geometry_available=False)
                row['issues'].append('仅获得保守定位范围，量测仍须相应专业解析器或原生工具')
            elif row["anchor"] is not None:
                p = row["anchor"]
                row.update(bbox=[p[0], p[1], p[0], p[1]], bbox3d=p+p,
                           geometry_status="container" if kind == "INSERT" else "location_only")
            else:
                row["issues"].append("没有可定位的完整几何；不得解释成图上没有对象")
            if facts.get('native_data') is not None:
                # A native insertion point does not bound the whole component.
                row['bbox']=row['bbox3d']=None
                row['geometry_status']='native_fields_read' if facts['native_data']['read_status']=='SERIALIZATION_COMPLETE' else 'native_fields_partial'
                row['text']=facts['native_data']['basic_data'].get('text',{}).get('content')
            if kind == "INSERT":
                row["block_name"] = str(entity.dxf.name)
                row["attributes"] = {str(a.dxf.tag): str(a.dxf.text) for a in entity.attribs}
                row["array_cell"] = cell
                if entity.dxf.name in ancestors or len(ancestors) >= max_depth:
                    row["issues"].append("循环块或超过递归深度；子对象未展开")
                else:
                    definition = doc.blocks.get(entity.dxf.name)
                    if definition is None:
                        row["issues"].append("缺少块定义；子对象未读取")
                    else:
                        if definition.block.is_xref or definition.block.is_xref_overlay:
                            row["issues"].append("外部参照需核验载入与版本；当前仅列出实际可见定义对象")
                        if entity.dxf.name.startswith("*U") or original.has_extension_dict:
                            row["issues"].append("可能含动态块或扩展数据；可见状态需原生CAD或模型另核")
                        child_matrix = compose_matrix(matrix, facts["insert_matrix_wcs"])
                        if scope_bounds is not None:
                            envelope_box,envelope_complete=envelope(entity.dxf.name)
                            envelope_box=transform_bounds(envelope_box,child_matrix)
                            envelope_xy=[envelope_box[0],envelope_box[1],envelope_box[3],envelope_box[4]] if envelope_box else None
                            if envelope_xy is None or not _intersects(envelope_xy,scope_bounds):
                                if envelope_complete:
                                    skipped_count+=1
                                    return
                                row.update(bbox=None,bbox3d=None,bounds_complete=False,geometry_status='unresolved',
                                    expansion_state='pending_scope_uncertain')
                                row['issues'].append('块范围不完整且已知几何未进入窗口；保留待定位容器，未展开子对象')
                                result.append(row)
                                return
                        first = len(result)
                        for child in definition:
                            expand(child, path, child_matrix, effective_layer, ancestors+(entity.dxf.name,))
                        descendants = result[first:]
                        boxes = [r["bbox3d"] for r in descendants if r["bbox3d"]]
                        if boxes:
                            box = [min(b[i] for b in boxes) for i in range(3)] + [max(b[i+3] for b in boxes) for i in range(3)]
                            row.update(bbox3d=box, bbox=[box[0],box[1],box[3],box[4]])
                        row["bounds_complete"] = bool(descendants) and all(r["bbox3d"] and not r["issues"] for r in descendants)
                        if not row["bounds_complete"]:
                            row["issues"].append("块范围仅覆盖已解析内容，范围外仍可能存在未解析子对象")
                for attribute in entity.attribs:
                    # Attached attributes are already in the INSERT owner's WCS.
                    attr_original = next((a for a in original.attribs if a.dxf.tag == attribute.dxf.tag), attribute)
                    visit(attr_original, path, matrix, effective_layer, ancestors, virtual=attribute)
            elif scope_bounds is not None and row['bbox'] is not None and not _intersects(row['bbox'],scope_bounds):
                if row['geometry'] or row['text'] is not None or row.get('location_method')=='conservative_source_envelope':
                    # Text selection uses its true anchor; no quantities rely on glyph bounds.
                    skipped_count+=1
                    return
        except Exception as exc:
            row["issues"].append(f"{type(exc).__name__}: {exc}")
            row["geometry_status"] = "unresolved"
        result.append(row)

    def expand(entity, parent_path, matrix, inherited_layer, ancestors):
        if entity.dxftype() != "INSERT" or (entity.dxf.row_count == 1 and entity.dxf.column_count == 1):
            return visit(entity, parent_path, matrix, inherited_layer, ancestors)
        # Native MINSERT has one physical row/column when its source spacing is
        # exactly zero. This is local to this INSERT; no world-position merge
        # and no tolerance folding of small nonzero grid spacings is allowed.
        rows = range(entity.dxf.row_count) if entity.dxf.row_spacing != 0 else range(1)
        columns = range(entity.dxf.column_count) if entity.dxf.column_spacing != 0 else range(1)
        for r in rows:
            for c in columns:
                virtual = entity.copy()
                offset = Vec3(c*entity.dxf.column_spacing, r*entity.dxf.row_spacing)
                offset = offset.rotate_deg(entity.dxf.rotation)
                virtual.dxf.insert += offset
                virtual.dxf.row_count = virtual.dxf.column_count = 1
                for attribute in virtual.attribs:
                    attribute.dxf.insert += offset
                visit(entity, parent_path, matrix, inherited_layer, ancestors, virtual, (r,c))

    for entity in doc.layouts.get(layout):
        expand(entity, [], IDENTITY, None, ())
    result.sort(key=lambda r: r["object_id"])
    if len({r["object_id"] for r in result}) != len(result):
        raise ValueError("实例身份碰撞；不能合并或静默覆盖")
    output = dict(schema_version=SCHEMA, location_schema_version=LOCATION_SCHEMA, kind="index", source=source,
                authoritative_source=authoritative_source, layout=layout,
                source_identity_issues=read_diagnostics['source_identity_issues'],source_read_diagnostics=read_diagnostics,
                source_identity_advisories=read_diagnostics.get('source_identity_advisories', []),
                raw_unit=UNITS.get(int(doc.header.get("$INSUNITS", 0)), "Unknown"),
                index_scope=dict(bounds=scope_bounds,kind='local' if scope_bounds else 'layout',
                    proven_geometry_or_text_anchor_outside_count=skipped_count,
                    text_selection='anchor',pending_containers_do_not_imply_absent_children=True),
                conversion_warnings=warnings, objects=result,
                inventory=dict(total=len(result), by_type=dict(Counter(r["entity_type"] for r in result)),
                    unresolved_or_partial=sum(bool(r["issues"]) for r in result),
                    without_location=sum(r["bbox"] is None for r in result)),
                semantics="observations_only; no professional inclusion or quantity approval")
    output["index_sha256"]=index_digest(output)
    return output


def _intersects(box, bounds):
    return box is not None and not (box[2] < bounds[0] or box[0] > bounds[2] or box[3] < bounds[1] or box[1] > bounds[3])


def context(index, query):
    objects = _verified_index(index)
    bounds = query.get("bounds")
    if bounds is not None:
        if not isinstance(bounds,list) or len(bounds) != 4 or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in bounds) or bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            raise ValueError("bounds 需要 [xmin,ymin,xmax,ymax] 有限范围")
        indexed_bounds=index.get('index_scope',{}).get('bounds')
        if indexed_bounds and any((bounds[i]<indexed_bounds[i] if i<2 else bounds[i]>indexed_bounds[i]) for i in range(4)):
            raise ValueError("查询超出已索引局部范围，须扩大bounds重建索引；不能把未展开区域当空图")
    ids = query.get("object_ids")
    if ids is not None and (not isinstance(ids,list) or any(i not in objects for i in ids)):
        raise ValueError("object_ids 引用了不存在的真实实例")
    text_pattern = re.compile(query["text_pattern"]) if query.get("text_pattern") else None
    selected = [r for r in objects.values() if (ids is None or r["object_id"] in ids)
                and (bounds is None or _intersects(r["bbox"],bounds))
                and (text_pattern is None or text_pattern.search(r.get("text") or ""))
                and (not query.get("entity_types") or r["entity_type"] in query["entity_types"])
                and (not query.get("layers") or r["effective_layer"] in query["layers"])]
    if query.get("include_descendants"):
        parents = {r["object_id"] for r in selected}
        selected = [r for r in objects.values() if any(r["object_id"] == p or r["object_id"].startswith(p+"/") for p in parents)]
    padding = query.get("context_padding_raw")
    if padding is not None:
        padding = _number(padding, "context_padding_raw")
        windows = [[r["bbox"][0]-padding,r["bbox"][1]-padding,r["bbox"][2]+padding,r["bbox"][3]+padding] for r in selected if r["bbox"]]
        selected = [r for r in objects.values() if any(_intersects(r["bbox"], b) for b in windows)]
    selected.sort(key=lambda r:r["object_id"])
    total = len(selected)
    max_objects = query.get("max_objects", 2000)
    if not isinstance(max_objects,int) or isinstance(max_objects,bool) or max_objects < 1:
        raise ValueError("max_objects 必须是正整数")
    shown, omitted = selected[:max_objects], selected[max_objects:]
    neighbors=[]
    if query.get("neighbor_distance_raw") is not None:
        limit = _number(query["neighbor_distance_raw"], "neighbor_distance_raw")
        endpoints=[]
        for row in shown:
            parts=(row.get("geometry") or {}).get("parts",[])
            for i,p in enumerate(parts):
                if p["kind"] == "line":
                    endpoints += [(row["object_id"], i, end, p[end]) for end in ("start","end")]
        # Grid candidates preserve full 3D distance and make no connection decision.
        grid={}
        for oid,part,end,p in endpoints:
            key=tuple(math.floor(x/limit) for x in p) if limit else tuple(p)
            keys=[tuple(key[i]+d[i] for i in range(3)) for d in __import__('itertools').product((-1,0,1),repeat=3)] if limit else [key]
            for k in keys:
                for other in grid.get(k,[]):
                    if oid == other[0]: continue
                    distance=math.dist(p,other[3])
                    if distance <= limit:
                        neighbors.append(dict(a=dict(object_id=other[0],part=other[1],end=other[2]),
                            b=dict(object_id=oid,part=part,end=end),distance_raw=distance,status="candidate_only"))
            grid.setdefault(key,[]).append((oid,part,end,p))
    unlocated=[r for r in objects.values() if r['bbox'] is None]
    return dict(schema_version=SCHEMA,kind="context",source=index["source"],index_sha256=index["index_sha256"],layout=index["layout"],
        raw_unit=index["raw_unit"],query=query,objects=shown,matched_count=total,
        omitted_object_ids=[r["object_id"] for r in omitted],
        unlocated_count=len(unlocated),unlocated_object_ids=[r['object_id'] for r in unlocated],
        unlocated_objects=[{k:r.get(k) for k in ('object_id','entity_type','geometry_status','issues','expansion_state')} for r in unlocated[:50]],
        unlocated_details_reference='完整记录按 unlocated_object_ids 从同一 index_sha256 索引查询；此处最多展示50条摘要',
        neighbor_candidates=neighbors,conversion_warnings=index["conversion_warnings"],
        coverage="local_context_only; unlocated and omitted objects remain unresolved")


def render_svg(data):
    """Portable source-context preview. Curve sampling affects display only."""
    from qto_cad_geometry import conic_point
    rows=data["objects"]
    boxes=[r["bbox"] for r in rows if r["bbox"]]
    if not boxes:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="120"><text x="20" y="40">No located geometry; consult unresolved objects.</text></svg>'
    bounds=data.get('query',{}).get('bounds') or [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
    width,height=1200,850
    scale=min(1120/max(bounds[2]-bounds[0],1e-9),740/max(bounds[3]-bounds[1],1e-9))
    def point(p):return (40+(p[0]-bounds[0])*scale,790-(p[1]-bounds[1])*scale)
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><g font-family="Microsoft YaHei,SimHei,sans-serif">',
         '<text x="20" y="25" font-size="16">CAD 局部上下文：编号对应 JSON 实例；关系与数量需识别，未解析对象见 JSON</text>',
         f'<metadata>index_sha256={html.escape(data.get("index_sha256", ""))}</metadata>',
         '<defs><clipPath id="drawing-window"><rect x="38" y="45" width="1125" height="748"/></clipPath></defs><g clip-path="url(#drawing-window)">']
    for i,r in enumerate(rows,1):
        color="#145a83" if r["geometry"] else "#875015"
        out.append(f'<g><title>{html.escape(r["object_id"]+" | "+r["effective_layer"]+" | "+"; ".join(r["issues"]))}</title>')
        for p in (r.get("geometry") or {}).get("parts",[]):
            points=[p["start"],p["end"]] if p["kind"]=="line" else [conic_point(p,p["start_param"]+p["sweep_param"]*n/96) for n in range(97)]
            coords=" ".join(f'{x:.3f},{y:.3f}' for x,y in map(point,points))
            out.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.2"/>')
        if r["anchor"] is not None and bounds[0]<=r['anchor'][0]<=bounds[2] and bounds[1]<=r['anchor'][1]<=bounds[3]:
            x,y=point(r["anchor"])
            if r["text"]:
                for n,line in enumerate(r["text"].splitlines()):
                    out.append(f'<text x="{x:.3f}" y="{y+13*n:.3f}" font-size="11" fill="#643">{html.escape(line)}</text>')
            label_ids=data.get('query',{}).get('label_object_ids')
            if r["entity_type"]!="INSERT" and (label_ids is None or r['object_id'] in label_ids):
                out.append(f'<text x="{x+2:.3f}" y="{y-3:.3f}" font-size="9" fill="#b24">{i}</text>')
        out.append('</g>')
    out.append('</g></g></svg>')
    return "\n".join(out)


def family_candidates(index, object_id, relative_tolerance=1e-7):
    objects=_verified_index(index)
    if index['source_identity_issues']:
        raise ValueError('原图有重复句柄等身份冲突；须修复来源身份或原生取证后再绑定算量，不能沿用解析器重分配身份')
    if object_id not in objects:raise ValueError("同族种子实例不存在")
    tolerance=_number(relative_tolerance,"relative_tolerance")
    def signature(row):
        leaves=[r for r in objects.values() if r["object_id"].startswith(row["object_id"]+"/") and r.get("geometry")] if row["entity_type"]=="INSERT" else [row]
        # Length/part-count descriptors are candidate filters, not shape equivalence.
        return sorted((p["kind"],measure_part(p)[0]) for r in leaves for p in (r.get("geometry") or {}).get("parts",[]))
    seed=objects[object_id]; target=signature(seed)
    candidates=[];exceptions=[]
    for row in objects.values():
        if row["object_id"]==object_id or row["entity_type"]!=seed["entity_type"]:continue
        other=signature(row)
        same=bool(target) and len(target)==len(other) and all(a[0]==b[0] and math.isclose(a[1],b[1],rel_tol=tolerance,abs_tol=0) for a,b in zip(target,other))
        differences=[key for key in ("block_name","attributes","text","effective_layer","visible_in_entity") if row[key]!=seed[key]]
        record=dict(object_id=row["object_id"],differences=differences,issues=row["issues"],status="candidate_only")
        if same:candidates.append(record)
        elif seed["block_name"] and row["block_name"]==seed["block_name"]:
            exceptions.append(dict(record,reason="同块名但当前实例几何描述不同"))
    return dict(seed_object_id=object_id,candidates=candidates,exceptions=exceptions,
        index_sha256=index["index_sha256"],
        semantic_equivalence_confirmed=False,method="相同类型及长度分布仅缩小候选；仍须比较构形、属性、接口与工程用途")


def prepare_pipe(index, operations, ledger_path):
    """Convert explicit source-object decisions to the existing pipe input.

    Geometries, endpoint locations and measured lengths cannot be supplied by the
    operations file. Semantic observations still require model review; this
    validator checks their provenance and object scope, not their truth.
    """
    from qto_cad_geometry import conic_point
    objects=_verified_index(index)
    if index['source_identity_issues']:
        raise ValueError('原图有重复句柄等身份冲突；须修复来源身份或原生取证后再绑定算量，不能沿用解析器重分配身份')
    if operations.get("index_sha256")!=index["index_sha256"]:
        raise ValueError("操作与当前识图索引不一致；旧局部视图不能复用")
    ledger_path=Path(ledger_path).resolve()
    ledger,entries=load_working_entries(_json(ledger_path),ledger_path,index["authoritative_source"]["sha256"])
    network_id=_text(operations.get("network_id"),"network_id")
    scope=operations.get("scope_object_ids")
    if not isinstance(scope,list) or not scope or len(set(scope))!=len(scope) or any(i not in objects for i in scope):
        raise ValueError("scope_object_ids 必须是不重复的真实实例列表")
    scope=set(scope)
    scope_basis=_text(operations.get("scope_basis"),"scope_basis")
    unit=_text(operations.get("unit"),"unit")
    raw_unit=index["raw_unit"]
    unit_declaration=None
    geometry_fields={}
    canonical_source_ids={}
    workbench_producer_dependencies=None
    if operations.get('geometric_unit_evidence') is not None:
        from qto_geometric_unit_evidence import verify_geometric_unit_consumption, runtime_python_dependencies
        workbench_producer_dependencies=runtime_python_dependencies([Path(__file__)],[Path(__file__).parent])
        credential=operations['geometric_unit_evidence']
        if operations.get('unit_declaration'):
            raise ValueError('局部工作台不能混用几何单位与声明路径')
        manifest=_json(credential['cad_manifest']['path'])
        measurement_scope=operations.get('measurement_scope')
        # The selected objects must belong to the exact source and local index,
        # not merely carry equal-looking handles or a matching authority hash.
        for actual,expected_ref in ((index['source'],manifest.get('exchange_dxf')),
                                    (index['authoritative_source'],manifest.get('authoritative_source'))):
            verify_source_file(actual,'局部工作台实际源位置')
            verify_source_file(expected_ref,'局部工作台单位凭据源位置')
            if actual.get('sha256')!=expected_ref.get('sha256'):
                raise ValueError('局部工作台索引与单位凭据实际交换来源内容不一致')
        b=(measurement_scope or {}).get('bounds',{})
        expected=[b.get(k) for k in ('xmin','ymin','xmax_exclusive','ymax_exclusive')]
        if index['layout']!=(measurement_scope or {}).get('layout') or index['index_scope']['bounds']!=expected:
            raise ValueError('局部工作台须从同一 measurement_scope 重建局部索引')
        replay=build_index(index['source'],layout=index['layout'],authoritative_source=index['authoritative_source'],
                           scope_bounds=expected)
        if replay['index_sha256']!=index['index_sha256']:
            raise ValueError('局部工作台索引与当前原图实际重放不一致')
        factor=operations.get('factor_to_output')
        consumption=verify_geometric_unit_consumption(credential,manifest,measurement_scope,factor,unit)
        raw_unit=consumption['checked_evidence']['raw_unit']
        for oid in scope:
            box=objects[oid].get('bbox')
            if (not isinstance(box,list) or len(box)!=4 or box[0]<expected[0] or box[1]<expected[1]
                    or box[2]>expected[2] or box[3]>expected[3]):
                raise ValueError('局部工作台实际消费对象超出单位凭据 scope 或空间未明')
        source_doc,_=read_dxf_with_diagnostics(index['source']['path'])
        for oid in scope:
            row=objects[oid]
            path=row['source_path']
            if not path:
                raise ValueError('局部工作台原实例链为空')
            import re
            tokens=[]
            owner=source_doc.modelspace().block_record_handle
            for handle in path[:-1]:
                matched=re.fullmatch(r'([0-9A-Fa-f]+)(?:\[(\d+),(\d+)\])?',handle)
                if matched is None:
                    raise ValueError('局部工作台原实例链句柄/阵列单元格式无效')
                insert=source_doc.entitydb.get(matched[1].upper())
                if insert is None or insert.dxftype()!='INSERT' or insert.dxf.owner!=owner:
                    raise ValueError('局部工作台原实例链不能回读完整父链')
                is_array=insert.dxf.row_count!=1 or insert.dxf.column_count!=1
                if is_array != (matched[2] is not None):
                    raise ValueError('局部工作台阵列须有逐单元身份，普通块不能虚构单元')
                if is_array:
                    r,c=int(matched[2]),int(matched[3])
                    if (matched[2]!=str(r) or matched[3]!=str(c)
                            or r>=insert.dxf.row_count or c>=insert.dxf.column_count):
                        raise ValueError('局部工作台阵列单元越界或身份不是规范行列整数')
                    if (r != 0 and insert.dxf.row_spacing == 0) or (c != 0 and insert.dxf.column_spacing == 0):
                        raise ValueError('局部工作台阵列零间距cell不是独立原生实例，须使用规范0行/0列')
                tokens.extend(['CAD-'+handle.upper(),insert.dxf.name])
                owner=source_doc.blocks[insert.dxf.name].block_record_handle
            leaf=source_doc.entitydb.get(path[-1])
            if leaf is None or leaf.dxf.owner!=owner:
                raise ValueError('局部工作台原实例叶对象与父链不一致')
            tokens.append('CAD-'+path[-1])
            canonical_source_ids[oid]=('CADINST-' if len(path)>1 else '')+'/'.join(tokens)
        geometry_fields=dict(geometric_unit_evidence=credential,measurement_scope=measurement_scope,
                             unit_evidence_level='inferred',geometric_unit_consumption=consumption)
    elif raw_unit in {"Unitless","Unknown"}:
        unit_declaration=operations.get("unit_declaration")
        if not isinstance(unit_declaration,dict):raise ValueError("图纸单位未设，必须由当前原图单位说明形成 unit_declaration")
        unit_entry=entries.get(unit_declaration.get("adjudication_id"))
        unit_ids=unit_declaration.get("annotation_object_ids")
        if (unit_entry is None or unit_entry["decision"]!="include" or not isinstance(unit_ids,list) or not unit_ids
                or not set(unit_ids)<=set(unit_entry["object_ids"]) or any(i not in objects for i in unit_ids)):
            raise ValueError("单位裁决必须引用模型已审阅的真实单位文字")
        _text(unit_declaration.get("observation"),"单位说明与适用范围")
        raw_unit=_text(unit_declaration.get("raw_unit"),"unit_declaration.raw_unit")
        scale=canonical_scale(raw_unit)
        source_texts=[objects[i].get("text") or "" for i in unit_ids]
        excerpt=unit_declaration.get('source_excerpt')
        if excerpt is not None:excerpt=_text(excerpt,'单位适用条款原文 source_excerpt')
        # Same rule as the pipe adapter (registration 20261015-0155): say what the clause is
        # about; a clause on annotation values or a text naming several units needs geometry.
        applies=unit_declaration.get("applies_to")
        if applies not in UNIT_APPLIES_TO:
            raise ValueError("unit_declaration.applies_to 必须写明所引条款适用的对象：coordinates、annotation_values 或 pipe_length_elevation")
        corroboration=unit_declaration.get("geometric_corroboration")
        if corroboration is None and (applies!="coordinates" or len(unit_tokens("\n".join(source_texts)))>1):
            raise ValueError("所引单位条款不是只写图纸坐标的单一单位说明；须提供 geometric_corroboration（图框幅面×出图比例或已知构件尺寸）")
        verify_unit_declaration_texts(source_texts,raw_unit,excerpt)
        if corroboration is not None:
            cited=[i for item in corroboration if isinstance(item,dict) for i in (item.get("object_ids") or [])] if isinstance(corroboration,list) else []
            if any(i not in objects for i in cited) or not set(cited)<=set(unit_entry["object_ids"]):
                raise ValueError("单位佐证对象必须是当前索引中的真实对象，并被单位识别记录覆盖")
            unit_declaration={**unit_declaration,"geometric_corroboration":verify_unit_corroboration(raw_unit,corroboration,{i:objects[i].get("bbox") for i in cited})}
    if operations.get("raw_unit") is not None and canonical_scale(operations["raw_unit"]) != canonical_scale(raw_unit):
        raise ValueError("操作单位与图纸INSUNITS不一致；单位不明须先核定来源")
    factor=canonical_scale(raw_unit)/canonical_scale(unit)
    params=copy.deepcopy(operations.get("parameters",{}))
    snap=_number(params.get("snap_tolerance"),"snap_tolerance")
    gap=_number(params.get("gap_max"),"gap_max")
    if gap<=snap:raise ValueError("gap_max 必须大于 snap_tolerance")
    _text(operations.get("parameter_basis"),"parameter_basis")
    evidence={r["evidence_id"]:r for r in ledger["evidence"]}
    # Bind semantic decisions to the exact rendered/index evidence as well as source.
    def entry_for(operation, ids):
        entry=entries.get(operation.get("adjudication_id"))
        if entry is None or entry["decision"]!="include" or entry["professional_owner"]!="qto-pipe-networks":
            raise ValueError("管网操作须引用本专业 include 模型识别记录")
        if not set(ids)<=set(entry["object_ids"]):raise ValueError("操作对象超出识别记录实际审阅范围")
        if not set(ids)<=scope:raise ValueError("操作对象超出当前范围")
        _text(operation.get("observation"),"本次对象或关系的图面 observation")
        return entry

    excluded={}; containers={}; unresolved={}
    for entry in entries.values():
        if entry['professional_owner']!='qto-pipe-networks':continue
        for oid in set(entry["object_ids"]) & scope:
            if entry["decision"]=="exclude":excluded[oid]=entry["adjudication_id"]
            elif entry["decision"]=="container":containers[oid]=entry["adjudication_id"]
    used=set(unit_declaration["annotation_object_ids"])&scope if unit_declaration else set()
    segments=[];by_object={};bindings=[]
    scale_matrix=(factor,0.,0.,0.,0.,factor,0.,0.,0.,0.,factor,0.,0.,0.,0.,1.)
    for operation in operations.get("assignments",[]):
        ids=operation.get("object_ids")
        if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):raise ValueError("assignment.object_ids 必须是唯一对象数组")
        entry=entry_for(operation,ids)
        if operation.get("role")!="pipe_centerline":raise ValueError("当前导出支持明确识别为 pipe_centerline 的路径")
        system=_text(operation.get("system"),"system")
        for oid in ids:
            if oid in used or oid in excluded or oid in containers:raise ValueError("同一实物存在重复或冲突处置: "+oid)
            row=objects[oid]
            if not row.get("geometry") or row["geometry_status"]!="measurable":
                unresolved[oid]="被纳入的对象没有可计算完整几何";continue
            if row["issues"]:
                unresolved[oid]="几何存在未解决问题: "+"; ".join(row["issues"]);continue
            if not row["visible_in_entity"]:
                unresolved[oid]="图元声明不可见，须核验当前可见状态后才能计量";continue
            geometry=transform_geometry(row["geometry"],scale_matrix)
            summary=summarize(geometry)
            if not summary["length"] or summary["length"]<=0:
                unresolved[oid]="零长或无有效曲线";continue
            sid="SEG-"+oid
            segment=dict(segment_id=sid,system=system,start=summary["start"],end=summary["end"],
                length=summary["length"],geometry=geometry,source_ids=[oid,*entry["evidence_ids"]],
                physical_id=index["authoritative_source"]["sha256"]+":"+oid,
                space_id=_text(operation.get("space_id",index["layout"]),"space_id"),dn_seeds=[])
            if geometry_fields:
                segment['source_ids']=[canonical_source_ids[oid],*entry['evidence_ids']]
                segment['source_record_id']='CAD-'+row['source_handle']
                segment['source_instance_path']=canonical_source_ids[oid]
                segment['workbench_object_ids']=[oid]
            segments.append(segment);by_object[oid]=segment;used.add(oid)
            bindings.append(dict(object_id=oid,segment_id=sid,adjudication_id=entry["adjudication_id"],
                source_path=row["source_path"],observation=operation["observation"]))

    roots=[];terminals=[];boundaries=[];junctions=[];valves=[];gaps=[];changes=[];relations=[];relation_ids=set()
    def endpoint(ref):
        oid=ref.get("object_id")
        if oid not in by_object:raise ValueError("接口引用未纳入的可测量管路")
        segment=by_object[oid];parts=segment["geometry"]["parts"]
        if ref.get("end") in {"start","end"}:
            end=ref["end"];i=0 if end=="start" else len(parts)-1;t=0. if end=="start" else 1.
            located=dict(segment_id=segment["segment_id"],end=end)
        else:
            i=ref.get("part_index");t=ref.get("parameter")
            if isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(parts):raise ValueError("接口 part_index 无效")
            t=_number(t,"parameter")
            if t>1:raise ValueError("接口 parameter 须在0到1之间")
            located=dict(segment_id=segment["segment_id"],part_index=i,parameter=t)
        p=parts[i]
        point=[a+(b-a)*t for a,b in zip(p["start"],p["end"])] if p["kind"]=="line" else conic_point(p,p["start_param"]+p["sweep_param"]*t)
        return dict(located,point=point,system=segment["system"],space_id=segment["space_id"])
    for relation in operations.get("relations",[]):
        rid=_text(relation.get("relation_id"),"relation_id")
        if rid in relation_ids:raise ValueError("关系编号重复")
        relation_ids.add(rid)
        kind=relation.get("kind")
        if kind=="dn_annotation":
            target=relation.get("target_object_id");annotation=relation.get("annotation_object_id")
            entry=entry_for(relation,[target,annotation])
            if target not in by_object:raise ValueError("DN关系目标没有已纳入路径")
            label=objects[annotation].get("text") or ""
            observed=_text(relation.get("annotation_text"),"annotation_text")
            if label!=observed:raise ValueError("DN文字与当前原图不一致")
            dn=_text(relation.get("dn"),"dn")
            _annotation_dn(label,dn)
            by_object[target]["dn_seeds"].append(dict(dn=dn,evidence_level="inferred",source_id=annotation,method="model_explicit_source_annotation"))
            used.add(annotation)
        elif kind=="valve":
            oid=relation.get("object_id");connections=relation.get("connected_object_ids")
            if not isinstance(connections,list) or not connections or len(set(connections))!=len(connections):raise ValueError("在线附件需提供逐一识别的 connected_object_ids")
            entry=entry_for(relation,[oid,*connections])
            if oid in used or oid in excluded or oid in containers:raise ValueError("在线附件存在重复或冲突计数")
            if any(i not in by_object for i in connections):raise ValueError("在线附件接管对象未纳入")
            systems={by_object[i]['system'] for i in connections};spaces={by_object[i]['space_id'] for i in connections}
            if len(systems)!=1 or len(spaces)!=1:raise ValueError("在线附件不能跨系统或跨图空间连接")
            position=objects[oid].get('anchor')
            if position is None:raise ValueError("在线附件来源没有可定位锚点，不能手写坐标冒充原图")
            valves.append(dict(valve_id=oid,point=[n*factor for n in position],system=next(iter(systems)),space_id=next(iter(spaces)),
                valve_type=relation.get('valve_type'),candidate_types=relation.get('candidate_types',[]),
                connected_segment_ids=[by_object[i]['segment_id'] for i in connections],
                connection_source_id=entry['evidence_ids'][0],source_id=oid,type_source_id=entry['evidence_ids'][0],
                instance_level='inferred',block_family=objects[oid].get('block_name'),
                candidate_type_source_id=entry['evidence_ids'][0],type_source_ids=list(entry['evidence_ids']),
                evidence_level='inferred',type_evidence_level='inferred'))
            used.add(oid)
        elif kind=="gap_classification":
            a=relation.get('a',{});b=relation.get('b',{})
            entry=entry_for(relation,[a.get('object_id'),b.get('object_id'),*relation.get('support_object_ids',[])])
            if a.get('end') not in {'start','end'} or b.get('end') not in {'start','end'}:raise ValueError("断口必须引用实际源路径端点")
            aa=endpoint(a);bb=endpoint(b)
            if aa['system']!=bb['system'] or aa['space_id']!=bb['space_id']:raise ValueError("局部断口不能跨系统或图纸空间")
            if relation.get('quantity_action') not in {'scenario','exclude','restore_omission'}:raise ValueError("断口须明确 quantity_action")
            gaps.append(dict(a_segment=aa['segment_id'],a_end=a['end'],b_segment=bb['segment_id'],b_end=b['end'],
                classification=_text(relation.get('classification'),'classification'),quantity_decision=relation['observation'],
                quantity_action=relation['quantity_action'],quantity_evidence_level='inferred',
                scenario_include=relation.get('scenario_include',False),source_id=entry['evidence_ids'][0]))
            used.update(relation.get('support_object_ids',[]))
        elif kind in {"root","terminal","scope_boundary","junction","change_node"}:
            refs=relation.get("members") if kind=="junction" else [relation.get("target",{})]
            if not isinstance(refs,list) or not refs:raise ValueError("关系需要真实路径接口")
            ids=[r.get("object_id") for r in refs]
            ids+=relation.get("support_object_ids",[])
            port_ids=relation.get('port_dn_annotation_object_ids',[])
            if kind=='terminal' and (relation.get('port_dn') is not None or port_ids):
                if not isinstance(port_ids,list) or not port_ids:raise ValueError('末端端口DN须引用本图真实标注，不能按设备类型补默认值')
                ids+=port_ids
            entry=entry_for(relation,ids)
            located=[endpoint(ref) for ref in refs]
            common=dict(source_ids=entry["evidence_ids"],source_id=entry["evidence_ids"][0],
                evidence_level="inferred",basis=relation["observation"])
            if kind=="junction":
                decision=relation.get("decision")
                if decision not in {"connect","separate","unresolved"} or len(refs)<2:raise ValueError("junction 需至少两成员及有效decision")
                junctions.append(dict(junction_id=rid,members=located,decision=decision,**common))
            else:
                record=dict(located[0],**common)
                if kind=="root":roots.append(dict(record,root_id=rid,root_role=relation.get("root_role","network_root")))
                elif kind=="terminal":
                    terminal=dict(record,terminal_id=rid,terminal_type=_text(relation.get("terminal_type"),"terminal_type"))
                    if port_ids:
                        for oid in port_ids:_annotation_dn(objects[oid].get('text') or '',relation.get('port_dn'))
                        terminal.update(port_dn=relation['port_dn'],port_evidence_level='inferred',port_source_id=port_ids[0])
                        used.update(port_ids)
                    terminals.append(terminal)
                elif kind=="change_node":changes.append(dict(record,change_id=rid))
                else:boundaries.append(dict(record,boundary_id=rid,connections_complete=False,fitting_quantity_owner="unresolved"))
            used.update(relation.get("support_object_ids",[]))
        else:raise ValueError("不支持的关系类型；不能静默忽略: "+str(kind))
        relations.append(copy.deepcopy(relation))
    if used & (set(excluded)|set(containers)):raise ValueError("同一对象的纳入/排除/容器处置相冲突")
    for oid in scope-used-set(excluded)-set(containers):unresolved.setdefault(oid,"尚未完成对象处置或尚未转换为当前专业输入")
    # Objects without a location cannot be safely classified as outside a window.
    for oid,row in objects.items():
        if row["bbox"] is None and oid not in excluded and oid not in used:
            unresolved.setdefault(oid,"缺少位置，不能证明位于所选范围外")
    if not segments:raise ValueError("没有已识别且可测量的管道路径；不生成零工程量")
    basis=recognition_basis(ledger,ledger_path)
    endpoint_review=operations.get("endpoint_review",{})
    complete=endpoint_review.get("complete")
    if not isinstance(complete,bool):raise ValueError("endpoint_review.complete 必须显式声明")
    reason=_text(endpoint_review.get("basis"),"endpoint_review.basis")
    unknown_ports=endpoint_review.get("unresolved_object_ids",[])
    if not isinstance(unknown_ports,list) or len(set(unknown_ports))!=len(unknown_ports) or any(i not in scope for i in unknown_ports):raise ValueError("未决末端必须引用范围内真实对象")
    if complete and (unknown_ports or unresolved):raise ValueError("存在未处置对象或末端时，不能声明工作包完整")
    issues=[dict(object_id=i,reason=r) for i,r in sorted(unresolved.items())]
    status="REVIEW_REQUIRED" if issues or index["conversion_warnings"] else "READY"
    result=dict(schema_version="qto-pipe-network-inference-input/1.2",network_id=network_id,unit=unit,
        **geometry_fields,
        parameters=params,recognition_basis=basis,segments=segments,roots=roots,terminals=terminals,
        junctions=junctions,scope_boundary_nodes=boundaries,valves=valves,gap_classifications=gaps,change_nodes=changes,
        object_selection=dict(audit_id="WORKBENCH-"+network_id,status=status,
            candidate_count=len(segments)+len(excluded)+len(issues),included_count=len(segments),
            excluded_count=len(excluded),unresolved_count=len(issues),included_segment_ids=[r["segment_id"] for r in segments],
            source_ids=[index["authoritative_source"]["sha256"]],anomaly_groups=issues),
        endpoint_handoff=dict(handoff_id="WORKBENCH-ENDPOINTS-"+network_id,required=bool(terminals or unknown_ports),
            source_instance_count=len(terminals)+len(unknown_ports),source_endpoint_port_count=len(terminals)+len(unknown_ports),
            source_physical_instance_count=len(terminals)+len(unknown_ports),delivered_count=len(terminals),
            not_applicable_count=0,unresolved_count=len(unknown_ports),source_workpackage_complete=complete,
            recognition_basis=basis,source_id=index["authoritative_source"]["sha256"],reason=reason),
        workbench=dict(index_sha256=index["index_sha256"],source=index["source"],scope_basis=scope_basis,
            source_declared_unit=index["raw_unit"],raw_unit_used=raw_unit,unit_declaration=unit_declaration,
            parameter_basis=operations["parameter_basis"],scope_object_ids=sorted(scope),bindings=bindings,relations=relations,
            excluded_objects=excluded,container_objects=containers,unprocessed_objects=issues,
            total_index_objects=len(objects),outside_selected_scope_count=len(objects)-len(scope),
            conversion_warnings=index["conversion_warnings"],formal_adoption_allowed=False,
            adoption_scope="working",review_status="unreviewed"))
    if geometry_fields:
        if workbench_producer_dependencies!=runtime_python_dependencies([Path(__file__)],[Path(__file__).parent]):
            raise ValueError('局部工作台计算期间源码字节变化，须重新实际生产')
        current=verify_geometric_unit_consumption(operations['geometric_unit_evidence'],manifest,
                                                measurement_scope,factor,unit)
        if current['dependency_fingerprints']!=consumption['dependency_fingerprints']:
            raise ValueError('局部工作台计算期间单位或原源依赖变化')
        result['geometric_unit_consumption']['workbench_production']=dict(
            operations=copy.deepcopy(operations),source=index['source'],authoritative_source=index['authoritative_source'],
            layout=index['layout'],scope_bounds=expected,index_sha256=index['index_sha256'],
            recognition_ledger=dict(path=str(ledger_path),sha256=hashlib.sha256(ledger_path.read_bytes()).hexdigest()),
            producer_dependencies=workbench_producer_dependencies)
    return result


def verify_workbench_unit_consumption(data, *, required=False):
    """Rebuild the actual local index and production operations at the infer inlet."""
    if not required and (not data.get('workbench') or data.get('geometric_unit_evidence') is None):
        return None
    from qto_geometric_unit_evidence import runtime_python_dependencies,verify_runtime_python_dependencies
    receipt=data.get('geometric_unit_consumption')
    recipe=receipt.get('workbench_production') if isinstance(receipt,dict) else None
    if not isinstance(recipe,dict):
        raise ValueError('局部工作台缺实际几何单位生产凭据，须重新生产')
    current=runtime_python_dependencies([Path(__file__)],[Path(__file__).parent])
    verify_runtime_python_dependencies(recipe.get('producer_dependencies'),current)
    ledger=recipe.get('recognition_ledger')
    verify_source_file(ledger,'局部工作台实际生产识别台账')
    index=build_index(recipe['source'],layout=recipe['layout'],authoritative_source=recipe['authoritative_source'],
                      scope_bounds=recipe['scope_bounds'])
    if index['index_sha256']!=recipe['index_sha256']:
        raise ValueError('局部工作台原源索引已变化，须重新生产')
    replay=prepare_pipe(index,recipe['operations'],ledger['path'])
    replay['geometric_unit_consumption']['workbench_production']['producer_dependencies']=recipe['producer_dependencies']
    normalized=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if normalized(replay)!=normalized(data):
        raise ValueError('局部工作台实际生产结果与完整回源重放不一致')
    return dict(producer_dependencies=current,recognition_ledger=ledger,index_sha256=index['index_sha256'])


def main():
    parser=argparse.ArgumentParser(description="实例索引、局部上下文和模型识别操作")
    sub=parser.add_subparsers(dest="action",required=True)
    p=sub.add_parser("index");p.add_argument("--source",required=True,type=Path);p.add_argument("--layout",default="Model");p.add_argument("--bounds",nargs=4,type=float);p.add_argument("--output",required=True,type=Path)
    p=sub.add_parser("context");p.add_argument("--index",required=True,type=Path);p.add_argument("--query",required=True,type=Path);p.add_argument("--output",required=True,type=Path);p.add_argument("--svg",type=Path)
    p=sub.add_parser("family");p.add_argument("--index",required=True,type=Path);p.add_argument("--object-id",required=True);p.add_argument("--output",required=True,type=Path)
    p=sub.add_parser("apply");p.add_argument("--index",required=True,type=Path);p.add_argument("--operations",required=True,type=Path);p.add_argument("--ledger",required=True,type=Path);p.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    try:
        if args.action=="index":
            source=_json(args.source);out=build_index(source,layout=args.layout,authoritative_source=source.pop("authoritative_source",None),scope_bounds=args.bounds)
        elif args.action=="context":out=context(_json(args.index),_json(args.query))
        elif args.action=="family":out=family_candidates(_json(args.index),args.object_id)
        else:out=prepare_pipe(_json(args.index),_json(args.operations),args.ledger)
        # Never overwrite inputs or source evidence, including an index's original drawing.
        protected={Path(v).resolve() for k,v in vars(args).items() if k in {"source","index","query","operations","ledger"} and v}
        if args.action!="index":
            idx=_json(args.index);protected.update(Path(idx[k]["path"]).resolve() for k in ("source","authoritative_source"))
        else:protected.update(Path(out[k]["path"]).resolve() for k in ("source","authoritative_source"))
        if args.action=='apply':
            protected.update((args.ledger.parent/Path(item['path'])).resolve() for item in _json(args.ledger).get('evidence',[]))
        protected.update(Path(str(p)+'.export.json') for p in list(protected) if p.suffix.lower()=='.dxf')
        if args.output.resolve() in protected or (getattr(args,"svg",None) and (args.svg.resolve() in protected or args.svg.resolve()==args.output.resolve())):
            raise ValueError("输出不能覆盖输入或原图")
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        if getattr(args,"svg",None):args.svg.write_text(render_svg(out),encoding="utf-8")
        print(json.dumps({"status":"PASS","output":str(args.output)},ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
