"""Explicit adapter for the earlier two-view CAD review page, not a CAD recognizer."""
import argparse,copy,json
from pathlib import Path
from qto_review_contract import VIEW_SCHEMA,normalize_view,extract_json_file

def migrate(raw,project_id,review_id,title):
 source=raw['source_sha256'];bounds=raw['bounds'];scenes=[];common=[]
 for side,label in [('supply','给水侧原图'),('drain','排水侧原图')]:
  bg=[]
  for r in raw['background']:
   if r.get('side','both') not in ['both',side]:continue
   color='#bac4cb' if r.get('group')=='architecture' else next((raw['systems'][k] for k in sorted(raw.get('systems',{}),key=len,reverse=True) if k in r.get('layer','')),'#7e929f')
   p=dict(kind='text',point=r['points'],text=r['text'],rotation=r.get('rotation',0),height=r.get('height',0),color=color) if r['kind']=='text' else dict(kind='line',points=r['points'],color=color)
   if r.get('side','both')=='both':
    if side=='supply':common.append(p)
   else:bg.append(p)
  scenes.append(dict(id=side,title=label,source_id='legacy-dwg',coordinate_unit='mm',y_axis='up',bounds=bounds,background=bg,background_refs=['common']))
 objects=[];legacy_records={}
 for original in raw['issues']:
  r={k:copy.deepcopy(original[k]) for k in ['id','title','category','kind','points','bbox','source','status','detail','catalog_key','annotation_key','adoption_result','adoption_reason','adoption_note','system','plan_side','length','dn'] if k in original};point_owned=r['id'].startswith(('JJ-','EQ-','PT-')) or r['category'] in ['器具当前结果','供装已排除','设备本体','专业图地漏','器具类型候选','器具接口范围']
  if r['category']=='本轮批注回执' and original.get('object_ids'):point_owned=all(i.startswith(('JJ-','EQ-','PT-')) for i in original['object_ids'])
  professional='qto-components-points' if point_owned else 'qto-pipe-networks'
  r.update(catalog_key=r.get('catalog_key',r['category']+'|'+r['id']),professional=professional,scene_id='drain' if r.get('plan_side')=='drain' or r.get('system') in ['废水','污水','通气'] else 'supply',status=r.get('status') or '历史复核记录',detail=r.get('detail') or '保留原复核记录',source=r.get('source') or r['id'],attributes={k:copy.deepcopy(original[k]) for k in ['dn','length','system','status','spec_reference','valve_type','fitting_type','dn_combination'] if k in original},quantities=[])
  legacy_records[r['catalog_key']]=copy.deepcopy(original)
  if r.get('length') is not None:r['quantities']=[dict(name='原复核记录长度',value=r['length'],unit='m',level='按原记录采用状态')]
  if isinstance(r.get('bbox'),list) and len(r['bbox'])==4:
   a,b,c,d=r['bbox'];r.update(kind='polygon',points=[[a,b],[c,b],[c,d],[a,d]])
  elif not r.get('points'):r.update(kind='unlocated',scene_id=None)
  objects.append(r)
 groups=copy.deepcopy(raw['groups'])
 for g in groups:
  for k in ['status','proposal','reason']:g[k]=g.get(k) or '保留原问题组，需专业复核'
 d=dict(schema=VIEW_SCHEMA,project_id=project_id,review_id=review_id,title=title,revision=raw['revision'],sources=[dict(id='legacy-dwg',title='原CAD复核来源',sha256=source)],scenes=scenes,objects=objects,groups=groups,receipts=raw.get('processing_receipts',{}),summary=[],scope_notes=['从旧版点选复核数据显式迁移；原对象字段、批注原话和采用状态完整保留，未重新计算数量。'],legacy_source_sha256=source,legacy_scene_map={'supply':'supply','drain':'drain'},legacy_professional='qto-pipe-networks',legacy_context={k:copy.deepcopy(v) for k,v in raw.items() if k not in ['background','issues','groups','processing_receipts']})
 d['background_sets']={'common':common};d['legacy_records']=legacy_records
 if any(g['category']=='待补资料' for g in groups):d['default_category']='待补资料'
 return normalize_view(d)

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--project-id',required=True);p.add_argument('--review-id',required=True);p.add_argument('--title',required=True);a=p.parse_args()
 try:
  if a.output.exists():raise ValueError('目标视图已存在，请使用新版本名')
  d=migrate(json.loads(a.input.read_text(encoding='utf-8-sig')),a.project_id,a.review_id,a.title);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':'PASS','objects':len(d['objects']),'groups':len(d['groups']),'quantity_changes':0},ensure_ascii=False));return 0
 except (OSError,ValueError,KeyError,TypeError) as e:print(json.dumps({'status':'INPUT_REQUIRED','error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
