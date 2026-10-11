"""Evidence-bound dimensional arithmetic; no drawing recognition or default design rules."""
from __future__ import annotations
import copy
import math


def derive_dimensions(item, evidence, error_type=ValueError, section_catalog=None):
    prepared = copy.deepcopy(item)
    audit = {}
    used = set()
    label = item.get('item_id', 'item')

    def fail(message):
        raise error_type(f'{label}尺寸推导: {message}')

    def number(value, name, zero=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            fail(f'{name}必须是有限数值')
        if value < 0 or (value == 0 and not zero):
            fail(f'{name}必须为' + ('非负数' if zero else '正数'))
        return float(value)

    def refs(spec, name):
        if not isinstance(spec, dict):
            fail(f'{name}必须是对象')
        ids = spec.get('evidence_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or x not in evidence for x in ids):
            fail(f'{name}必须引用存在的尺寸或节点证据')
        for eid in ids:
            if evidence[eid].get('evidence_class') not in {'object_geometry', 'object_mapping', 'visual_geometry', 'rule_clause', 'requirement'}:
                fail(f'{name}证据类别不能说明尺寸或节点')
        used.update(ids)

    def set_value(field, value, spec, formula):
        if not math.isfinite(value) or value < 0:
            fail(f'{field}推导结果不是有限非负数')
        if field in prepared:
            declared = number(prepared[field], field, zero=True)
            if not math.isclose(declared, value, rel_tol=1e-9, abs_tol=1e-8):
                fail(f'{field}声明值{declared:.12g}与尺寸复算{value:.12g}不一致')
        prepared[field] = value
        audit[field] = {'value': value, 'formula': formula, 'basis': copy.deepcopy(spec)}

    if 'section_reference' in item:
        ref = item['section_reference']
        fields = ('schedule_id', 'building', 'level', 'code')
        if not isinstance(ref, dict) or any(not isinstance(ref.get(k), str) or not ref[k].strip() for k in fields):
            fail('section_reference必须明确表版本、楼栋、楼层及构件代码')
        if not isinstance(section_catalog, list): fail('section_reference缺少section_catalog')
        matches = [row for row in section_catalog if isinstance(row, dict) and all(row.get(k)==ref[k] for k in fields)]
        if len(matches) != 1: fail(f'截面表按版本/楼栋/楼层/构件代码须唯一匹配，实际{len(matches)}条')
        row = matches[0]; geometry = row.get('section_geometry');refs(geometry, 'section_catalog.section_geometry')
        if 'section_geometry' in item and item['section_geometry'] != geometry:
            fail('项目截面表与构件直接截面声明冲突')
        prepared['section_geometry'] = copy.deepcopy(geometry)
        audit['section_binding'] = {'reference': copy.deepcopy(ref), 'row': copy.deepcopy(row)}

    if 'section_geometry' in prepared:
        if item.get('method') != 'built_up_section_length':
            fail('section_geometry只用于组焊净截面计算，热轧型钢须用有依据的表重')
        s = prepared['section_geometry']; refs(s, 'section_geometry')
        extra = set(s)-{'kind','height_mm','width_mm','web_mm','flange_mm','evidence_ids'}
        if extra: fail('section_geometry包含未支持尺寸字段，不能静默忽略: '+', '.join(sorted(extra)))
        kind = s.get('kind')
        if kind not in {'welded_h', 'welded_box'}:
            fail('仅支持等截面welded_h/welded_box；变截面须按有依据的分板或积分另算')
        h = number(s.get('height_mm'), 'height_mm'); b = number(s.get('width_mm'), 'width_mm')
        tw = number(s.get('web_mm'), 'web_mm'); tf = number(s.get('flange_mm'), 'flange_mm')
        if h <= 2*tf or b <= (2*tw if kind == 'welded_box' else tw):
            fail('截面内净尺寸必须大于0')
        n = 2 if kind == 'welded_box' else 1
        area = 2*b*tf + n*(h-2*tf)*tw
        set_value('cross_section_area_mm2', area, s, f'2×{b}×{tf}+{n}×({h}-2×{tf})×{tw}')

    if 'length_derivation' in item:
        if item.get('method') not in {'profile_length', 'built_up_section_length'}:
            fail('length_derivation只用于线性构件')
        s = item['length_derivation']; refs(s, 'length_derivation')
        if not isinstance(s.get('basis'), str) or not s['basis'].strip():
            fail('必须说明中心距、柱面、牛腿或净长的起算基准')
        if 'axis_geometry' in s:
            g = s['axis_geometry']; refs(g, 'axis_geometry')
            if set(g)-{'start','end','unit','evidence_ids'}:
                fail('axis_geometry含未支持字段')
            scales = {'mm': .001, 'cm': .01, 'm': 1., 'in': .0254, 'ft': .3048}
            if not isinstance(g.get('unit'),str) or g['unit'] not in scales: fail('axis_geometry.unit必须显式声明mm/cm/m/in/ft')
            points = []
            for key in ('start','end'):
                p = g.get(key)
                if not isinstance(p, (list,tuple)) or len(p) != 3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in p):
                    fail('axis_geometry端点必须是三个有限坐标')
                points.append(p)
            span = math.dist(*points)*scales[g['unit']]
            number(span, 'axis_geometry空间跨度')
            if 'span_m' in s and not math.isclose(number(s['span_m'],'span_m'),span,rel_tol=1e-9,abs_tol=1e-8):
                fail('span_m与三维端点复算不一致')
        else:
            span = number(s.get('span_m'), 'span_m')
        if 'end_conditions' in s:
            if s.get('deductions') not in (None, []): fail('end_conditions不能与自由扣减同时使用')
            span_basis = s.get('span_basis')
            if not isinstance(span_basis,str) or span_basis not in {'support_centres','support_faces','cut_faces'}:
                fail('end_conditions必须明确span_basis为support_centres/support_faces/cut_faces')
            ends = s['end_conditions']
            if not isinstance(ends,list) or len(ends)!=2 or any(not isinstance(e,dict) or not isinstance(e.get('end_id'),str) for e in ends) or {e['end_id'] for e in ends}!={'start','end'}:
                fail('end_conditions必须分别列出start和end，不能遗漏或重复')
            deductions=[]
            for e in ends:
                refs(e, 'end_conditions')
                if set(e)-{'end_id','axis_to_face_m','face_to_cut_m','evidence_ids','note'}:
                    fail('end_conditions含未支持字段')
                centre = number(e.get('axis_to_face_m'),'axis_to_face_m',zero=True)
                cut = number(e.get('face_to_cut_m'),'face_to_cut_m',zero=True)
                if span_basis != 'support_centres' and centre != 0:
                    fail('支承面或切口起算跨度不得重复扣中心至支承面距离')
                if span_basis == 'cut_faces' and cut != 0:
                    fail('切口起算跨度不得再次扣支承面至切口距离')
                deductions.append({'label':e['end_id']+'中心至支承面及支承面至切口','value_m':centre+cut})
        else:
            if 'span_basis' in s: fail('span_basis必须配套两端end_conditions')
            deductions = s.get('deductions')
        if not isinstance(deductions, list):
            fail('deductions必须显式列出，无扣减时使用空数组')
        values=[]
        for d in deductions:
            if not isinstance(d, dict) or not isinstance(d.get('label'), str) or not d['label'].strip():
                fail('每项扣减必须说明对象和基准')
            values.append(number(d.get('value_m'), 'deduction.value_m', zero=True))
        net = span-math.fsum(values)
        if net <= 0: fail('扣减后净长必须大于0')
        count = s.get('quantity', 1)
        q = number(count, 'length.quantity')
        if not q.is_integer(): fail('length.quantity必须为整数')
        set_value('length_total_m', net*q, s, f'({span}-sum({values}))×{int(q)}')

    if 'plate_geometry' in item:
        if item.get('method') != 'plate': fail('plate_geometry只用于板件')
        s = item['plate_geometry']; refs(s, 'plate_geometry')
        if s.get('kind') != 'rectangle': fail('当前只支持矩形板，异形净面积需独立几何依据')
        width=number(s.get('width_mm'),'plate.width_mm');height=number(s.get('height_mm'),'plate.height_mm')
        deduction=number(s.get('deduction_area_mm2'),'deduction_area_mm2',zero=True)
        area=width*height-deduction
        if area <= 0: fail('板件扣减后面积必须大于0')
        set_value('net_area_mm2',area,s,f'{width}×{height}-{deduction}')

    if 'quantity_derivation' in item:
        if item.get('method') not in {'plate','count'}: fail('quantity_derivation只用于板件或连接件')
        s=item['quantity_derivation'];refs(s,'quantity_derivation');groups=s.get('groups')
        if not isinstance(groups,list) or not groups: fail('数量推导必须列出节点或位置组')
        group_ids=set();instances=set();total=0
        for g in groups:
            if not isinstance(g,dict): fail('节点组必须是对象')
            gid=g.get('group_id')
            if not isinstance(gid,str) or not gid.strip() or gid in group_ids: fail('节点组编号缺失或重复')
            group_ids.add(gid);ids=g.get('instance_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x.strip() for x in ids): fail('节点组必须明确列出实例编号')
            if len(ids)!=len(set(ids)) or instances.intersection(ids): fail('节点实例在数量推导中重复')
            instances.update(ids);n=number(g.get('units_per_instance'),'units_per_instance',zero=True)
            if not n.is_integer(): fail('每处数量必须为整数')
            total+=len(ids)*int(n)
        set_value('quantity',total,s,'Σ(去重节点实例数×每处件数)')
    return prepared,audit,used
