"""Replay the frozen generic duct generator before exposing derived/curve hosts."""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_duct_network_from_neutral import build,read_ref,ref
from duct_network_extensions import checked_geometry
from infer_duct_fittings_vertical import spec_of


def generated_hosts(data,base,*,source,layout,space,coordinate_unit,factor):
    original,input_ref=read_ref(data['generated_duct_input'],base)
    binding=original.get('neutral_binding')
    if not binding:raise ValueError('合成/曲线宿主必须来自冻结通用生成器和源几何，不能手填任意中心线')
    for key in ('manifest','profile','claims'):
        if key in binding:read_ref(binding[key],Path(input_ref['path']).parent)
    def path(name):
        p=Path(binding[name]['path']);return p if p.is_absolute() else Path(input_ref['path']).parent/p
    rebuilt,_=build(path('manifest'),path('profile'),path('claims') if 'claims' in binding else None)
    if any(original.get(k)!=v for k,v in rebuilt.items()):raise ValueError('冻结风管生成输入与当前有据源几何重建不同')
    for key,value in (('source_sha256',source),('layout',layout),('space_id',space),('coordinate_unit',coordinate_unit)):
        if rebuilt.get(key)!=value:raise ValueError('生成宿主来源/布局/空间/单位与当前点位不一致:'+key)
    if factor!=1:raise ValueError('生成风管宿主目前要求点位保留同一源坐标单位，不能隐式再次缩放')
    all_rows={r['segment_id']:r for r in rebuilt['segments']+rebuilt['drawn_curve_segments']}
    coverage={r['source_key']:r for r in rebuilt['coverage']['rows']}
    requested=data.get('segments');seen=set();out=[]
    for selector in requested:
        sid=selector.get('segment_id')
        if sid in seen or sid not in all_rows or set(selector)-{'segment_id'}:raise ValueError('生成宿主只允许按唯一生成segment_id选取，不接受手改几何/分类')
        seen.add(sid);row=all_rows[sid];info=checked_geometry(row['geometry'])
        if row.get('host_attachment_eligible') is False:
            raise ValueError('数值等距中线为有位置误差界的工作几何；当前点位投影未消费此误差，不能作为无误差附件宿主')
        spec=spec_of(row)
        size_problems=['生成宿主截面未决，不能传递附件规格'] if spec=='未知规格' or '?' in spec else []
        layers=sorted({coverage[k]['layer'] for k in row['source_ids']})
        proof={'generator_input':input_ref,'generator':ref(Path(__file__).with_name('build_duct_network_from_neutral.py')),
               'geometry_verifier':ref(Path(__file__).with_name('duct_network_extensions.py')),
               'source_geometry_refs':row['source_geometry_refs'],'classification_proofs':row['classification_proofs']}
        proof_id='DUCT-GENERATED-'+hashlib.sha256(json.dumps(proof,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        out.append({'segment_id':sid,'source_record_id':row['source_record_id'],'instance_path':row['source_instance_path'],
                    'part_index':row['source_geometry_refs'][0]['part_index'],'start':info['start'],'end':info['end'],'geometry':row['geometry'],
                    'source_layer':layers[0],'source_layers':layers,'source_sha256':source,'coordinate_system':'WCS','coordinate_unit':coordinate_unit,
                    'space_id':space,'system':row['system'],'spec':spec,'classification_evidence_ids':[proof_id],
                    'classification_proofs':[proof],'classification_problems':size_problems,'generated_duct_host':True})
    return out
