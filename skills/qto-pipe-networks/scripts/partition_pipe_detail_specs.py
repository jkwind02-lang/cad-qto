"""Partition measured detail routes at source-bound points, preserving mixed DN.

This audits a model's drawing interpretation. It does not decide template
applicability, infer nominal diameters from block names, or produce net pipe bills.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import ezdxf
from measure_pipe_detail_routes import Drawing, measure, require, bounds_contain
from qto_cad_geometry import transform_point, entity_geometry, read_dxf_with_diagnostics
from qto_cad_source_contract import verify_source_file

VERSION = 'qto-pipe-detail-spec-partition/1.0'


def station(ref, route, drawing, tolerance):
    """Measure along a route; never use an unverified user-provided chainage."""
    segments = route['segments']
    index = ref['segment']
    require(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(segments), '区段索引无效')
    s = segments[index]
    if ref.get('end') in ('start', 'end'):
        fraction = 0 if ref['end'] == 'start' else 1
    else:
        point = drawing.point(ref['point'])
        a, b = s['start'], s['end']
        # Mid-route cuts support straight axes only. Curves can be retained whole.
        if s['kind'] == 'drawn':
            require(drawing.part(s['source_evidence']['source'])['kind'] == 'line', '曲线中途分界尚不支持；须保留完整源曲线')
        vector = [y-x for x, y in zip(a,b)]
        length2 = sum(x*x for x in vector)
        fraction = sum((p-x)*v for p,x,v in zip(point,a,vector))/length2
        projected = [x+fraction*v for x,v in zip(a,vector)]
        require(math.dist(projected,point) <= tolerance and -tolerance/math.sqrt(length2) <= fraction <= 1+tolerance/math.sqrt(length2),
                '规格分界点不在当前轴段内')
        fraction = min(1.,max(0.,fraction))
    return math.fsum(x['dimension_length_m'] for x in segments[:index])+fraction*s['dimension_length_m']


def validate_spec(spec, drawing, view, tolerance):
    require(spec.get('kind') in ('constant_dn', 'transition', 'unresolved'), '规格类别无效')
    require(isinstance(spec.get('review_note'),str) and spec['review_note'].strip(), '须保留规格判读说明')
    if spec['kind'] == 'unresolved':
        require(spec.get('issue'), '未决规格须说明具体缺口')
        return
    expected = [spec['dn']] if spec['kind'] == 'constant_dn' else [spec['dn_from'],spec['dn_to']]
    require(all(isinstance(n,int) and not isinstance(n,bool) and n>0 for n in expected), 'DN必须为正整数')
    if len(expected)==2:require(expected[0]!=expected[1], '同规格不能伪装为异径段')
    evidence = spec.get('annotations',[])
    require(len(evidence)==len(expected), '每个规格须有原图文字证据')
    for dn,ref in zip(expected,evidence):
        e,matrix = drawing.resolve(ref['path'])
        require(e.dxftype() in ('TEXT','MTEXT'), '规格必须引用原图文字，不能从块名直接套用')
        text = e.plain_text()
        require(text == ref['text'], '规格原文与源图不一致')
        numbers = re.findall(r'(?i)(?<![A-Z0-9])DN\s*(\d+)(?![\d.])|(?<![\d.])(\d+)\s*A(?![A-Z0-9])',text)
        require(dn in {int(a or b) for a,b in numbers}, 'DN不在所引用原文中')
        require(bounds_contain(view['bounds3d'],[transform_point(entity_geometry(e)['anchor'],matrix)],tolerance), '规格文字不属于该主视图')


def partition(data):
    require(data.get('schema_version')==VERSION, '规格分段版本不支持')
    verify_source_file(data.get('detail_input'), '原图路径输入')
    raw = json.loads(Path(data['detail_input']['path']).read_text(encoding='utf-8-sig'))
    measured = measure(raw)  # Reread source; do not trust cached/user-entered lengths.
    document, read_diagnostics = read_dxf_with_diagnostics(raw['drawing']['path'])
    require(not read_diagnostics['source_identity_issues'], '源图实体句柄重复，规格源对象身份未闭合')
    drawing = Drawing(document)
    tolerance = raw.get('join_tolerance_raw',.01)
    views = {v['view_id']:v for v in raw['views']}
    routes = {(r['physical_group_id'],r['physical_route_id']):r for r in measured['routes']}
    plans = data.get('partitions',[])
    require(isinstance(plans,list) and plans, '规格分段清单为空')
    seen=set();outputs=[]
    for plan in plans:
        key=(plan['physical_group_id'],plan['physical_route_id'])
        require(key in routes and key not in seen, '路径不存在或重复分段')
        seen.add(key);route=routes[key];total=route['axis_including_components_m']
        zones=plan.get('zones',[]);require(zones,'没有规格区间')
        previous=0.;pieces=[];by_spec={}
        for zone in zones:
            spec=zone['specification'];validate_spec(spec,drawing,views[route['view_id']],tolerance)
            begin=station(zone['from'],route,drawing,tolerance);end=station(zone['to'],route,drawing,tolerance)
            require(math.isclose(begin,previous,abs_tol=1e-8,rel_tol=0) and end>begin+1e-10,
                    '规格区间存在遗漏、重叠、逆序或零长度')
            previous=end
            name=(f"DN{spec['dn']}" if spec['kind']=='constant_dn' else
                  f"DN{spec['dn_from']}→DN{spec['dn_to']}异径占位" if spec['kind']=='transition' else '规格未决')
            by_spec[name]=by_spec.get(name,0.)+end-begin
            pieces.append(dict(start_chainage_m=begin,end_chainage_m=end,axis_length_m=end-begin,
                               specification=spec,boundary_evidence=dict(start=zone['from'],end=zone['to'])))
        require(math.isclose(previous,total,abs_tol=1e-8,rel_tol=0),'规格分段未覆盖到路径终点')
        outputs.append(dict(physical_group_id=key[0],physical_route_id=key[1],zones=pieces,
                            axis_length_by_spec_m=by_spec,axis_including_components_m=total,
                            specification_coverage='complete' if all(z['specification']['kind']!='unresolved' for z in zones) else 'partial',
                            billable_pipe_length_m=None,status='MODEL_INTERPRETED_SPEC_PARTITION'))
    require(seen==set(routes),'存在未处置路径，不能报告整批规格分段完成')
    return dict(schema_version=VERSION,detail_input=data['detail_input'],drawing=raw['drawing'],routes=outputs,
                status='SOURCE_BOUND_PARTITION_MEASURED',review_state='working_unreviewed',
                limitations=['分段只核验原图证据及长度守恒，不代替模型对标注适用范围的识图判断',
                             '异径占位单列，不整段套主管DN；含阀件轴长不是净管下料量',
                             '典型图与安装实例对应、示意比例与真实制作尺寸须另行核对'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();raw=Path(args.input).read_bytes();result=partition(json.loads(raw.decode('utf-8-sig')))
    result['input_sha256']=hashlib.sha256(raw).hexdigest()
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(status=result['status'],routes=len(result['routes'])),ensure_ascii=False))


if __name__=='__main__':main()
