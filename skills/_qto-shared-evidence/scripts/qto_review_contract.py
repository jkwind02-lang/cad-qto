"""Shared offline review data contract. Does not recognize objects or alter quantities."""
import base64, copy, gzip, hashlib, json, math, re
from pathlib import Path
from qto_review_delivery import normalize_delivery, bind_delivery_feedback
from qto_review_revision import bind_result_versions, classify_feedback, settle_merge_conflict
from qto_review_comparison import normalize_comparison, VERDICTS, CONCLUSIONS, BAND_CLASSES

VIEW_SCHEMA = 'qto-review-view/1.0'
VIEW_SCHEMA_COMPARISON = 'qto-review-view/1.1'
VIEW_SCHEMAS = {VIEW_SCHEMA, VIEW_SCHEMA_COMPARISON}
FEEDBACK_SCHEMA = 'qto-review-feedback/2.0'
FEEDBACK_SCHEMA_COMPARISON = 'qto-review-feedback/2.1'
FEEDBACK_SCHEMAS = {FEEDBACK_SCHEMA, FEEDBACK_SCHEMA_COMPARISON}
LEGACY_SCHEMAS = {'cad-qto-review-feedback/1.0', 'cad-qto-review-feedback/1.1'}
PROFESSIONALS = {
 'qto-pipe-networks':'管道管网', 'qto-components-points':'构件设备点位',
 'qto-duct-air-systems':'风管风系统', 'qto-electrical-routes':'电气线路',
 'qto-concrete-formwork':'混凝土模板', 'qto-rebar':'钢筋',
 'qto-structural-steel':'主体钢结构', 'qto-masonry-secondary':'砌体二次结构',
 'qto-surfaces-envelope':'面层围护', 'qto-framing-supports':'龙骨支承骨架',
 'qto-earthwork-terrain':'土方地形', 'qto-piles-groundwork':'桩基地基',
 'qto-roads-siteworks':'道路场地'}

AUDIT_SNAPSHOTS = ('item_snapshot', 'issue_snapshot', 'common_issue_snapshot')
# Scene mapping contract 8: why an object has no drawing position.
UNLOCATED_REASONS = ('no_geometry', 'handle_not_found', 'handle_in_block', 'sheet_unknown', 'outside_sheet', 'estimated')

def require(ok, message):
 if not ok: raise ValueError(message)

def string(v): return isinstance(v,str) and bool(v.strip())
def nonneg(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0
def page_view_ok(pv):
 # What the reviewer saw under the page tolerance (contract 9); never used for staleness.
 if not isinstance(pv,dict):return False
 band,conc=pv.get('consistent_band'),pv.get('conclusions')
 band_ok=nonneg(band) or (isinstance(band,dict) and set(band)==set(BAND_CLASSES) and all(nonneg(x) for x in band.values()))
 return band_ok and isinstance(conc,dict) and all(isinstance(k,str) and x in CONCLUSIONS for k,x in conc.items())
def number(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def xy(v): return isinstance(v,list) and len(v)==2 and all(number(n) for n in v)

def identity(data):
 canonical={'project_id':data['project_id'],'review_id':data['review_id'],
            'sources':sorted([{'id':s['id'],'sha256':s['sha256'].lower()} for s in data['sources']],key=lambda s:s['id'])}
 return hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def normalize_view(raw, base_dir=None):
 d=copy.deepcopy(raw);require(d.get('schema') in VIEW_SCHEMAS,'复核视图schema无效')
 v11=d['schema']==VIEW_SCHEMA_COMPARISON
 require(v11 or 'comparison' not in d,'comparison扩展须使用qto-review-view/1.1')
 for k in ['project_id','review_id','title','revision']: require(string(d.get(k)),k+'必须填写')
 sources=d.get('sources');require(isinstance(sources,list) and sources,'缺少来源文件身份')
 require(len({s.get('id') for s in sources})==len(sources),'来源编号重复')
 for s in sources:
  require(string(s.get('id')) and string(s.get('title')),'来源须有id和标题')
  require(isinstance(s.get('sha256'),str) and re.fullmatch('[0-9a-fA-F]{64}',s['sha256']),'来源sha256无效')
 fingerprint=identity(d)
 require(not d.get('source_sha256') or d['source_sha256']==fingerprint,'项目/复核范围或来源文件身份不一致')
 d['source_sha256']=fingerprint
 if d.get('legacy_source_sha256'):
  require(len(sources)==1 and d['legacy_source_sha256']==sources[0]['sha256'],'旧版导入只能显式绑定同一单来源')
 scenes=d.get('scenes');require(isinstance(scenes,list) and (scenes or v11 or 'delivery' in d),'至少提供一张图页/视图')
 d.setdefault('background_sets',{});require(isinstance(d['background_sets'],dict),'共用底图集无效')
 scene_ids=set();source_ids={s['id'] for s in sources}
 for s in scenes:
  require(string(s.get('id')) and s['id'] not in scene_ids,'图页编号缺失或重复');scene_ids.add(s['id'])
  require(string(s.get('title')) and string(s.get('coordinate_unit')),'图页标题或坐标单位缺失')
  require(s.get('source_id') in source_ids,'图页来源编号无效')
  require(s.get('y_axis','up') in ['up','down'],'y_axis须为up/down')
  b=s.get('bounds');require(isinstance(b,list) and len(b)==4 and all(number(x) for x in b) and b[2]>b[0] and b[3]>b[1],'图页范围无效')
  s.setdefault('background',[])
  refs=s.get('background_refs',[]);require(isinstance(refs,list) and all(k in d['background_sets'] for k in refs),'共用底图引用不存在')
  for p in [*s['background'],*[p for k in refs for p in d['background_sets'][k]]]:
   require(p.get('kind') in ['line','polygon','text'],'底图图元kind无效')
   if p['kind']=='text':require(xy(p.get('point')) and isinstance(p.get('text'),str),'底图文字定位无效')
   else:require(isinstance(p.get('points'),list) and len(p['points'])>=2 and all(xy(x) for x in p['points']),'底图几何无效')
   # Display fields restored from the original (scene mapping contract 7.3); all optional.
   if p.get('height') is not None:require(number(p['height']) and p['height']>0,'底图文字字高无效')
   if p.get('align') is not None:require(p['align'] in ['left','center','right'],'底图文字对齐无效')
   if p.get('baseline') is not None:require(p['baseline'] in ['alphabetic','middle','top','bottom'],'底图文字基线无效')
   if p.get('width_factor') is not None:require(number(p['width_factor']) and p['width_factor']>0,'底图文字宽度系数无效')
   if 'text_axes' in p:
    axes=p['text_axes']
    require(p['kind']=='text' and isinstance(axes,list) and len(axes)==2 and all(xy(v) for v in axes),'底图文字变换须为两个有限二维向量')
    (bx,by),(ux,uy)=axes
    size=math.hypot(bx,by)*math.hypot(ux,uy)
    require(math.isfinite(size) and size>0 and abs(bx*uy-by*ux)>1e-12*size,'底图文字变换不能退化')
   if p.get('fill') is not None:require(p['kind']=='polygon' and isinstance(p['fill'],str) and re.fullmatch('#[0-9a-fA-F]{6}',p['fill']) is not None,'底图填充只用于面，颜色写 #rrggbb')
   if p.get('dash') is not None:require(p['kind']!='text' and isinstance(p['dash'],list) and bool(p['dash']) and all(number(v) and v>=0 for v in p['dash']),'底图虚线无效')
  if s.get('length_factor') is not None:require(number(s['length_factor']) and s['length_factor']>0,'图页实长换算系数须为正数')
  # What the page cannot show (contract 7.4) and the symbol reconciliation (7.5): counts must add up.
  rs=s.get('restoration')
  if rs is not None:
   require(isinstance(rs,dict) and rs.get('source') in ['dxf','neutral'],'图页还原说明无效')
   if rs['source']=='dxf':ns=rs.get('not_shown');require(isinstance(ns,dict) and all(isinstance(v,int) and v>=0 for v in ns.values()) and rs.get('not_shown_total')==sum(ns.values()),'须回原图计数无效')
  sym=s.get('symbol_reconciliation')
  if sym is not None:
   require(isinstance(sym,list),'符号对账须为列表')
   for r in sym:
    require(isinstance(r,dict) and string(r.get('block')) and all(isinstance(r.get(k),int) and r[k]>=0 for k in ('on_sheet','counted','uncounted','duplicates')),'符号对账行无效')
    up,dp=r.get('uncounted_points') or [],r.get('duplicate_points') or []
    # Explained symbols (scene mapping contract 7.5.1): removed overlaps, counted on another sheet, same item drawn elsewhere.
    ex={k:r.get(k,0) for k in ('removed_overlap','counted_elsewhere','same_item_elsewhere')}
    require(all(isinstance(v,int) and v>=0 and len(r.get(k+'_points') or [])==v and all(xy(p) for p in r.get(k+'_points') or []) for k,v in ex.items()),'符号对账说明类计数与点位不符')
    require(isinstance(r.get('notes',{}),dict) and all(isinstance(v,list) and all(string(n) for n in v) for v in r.get('notes',{}).values()),'符号对账说明无效')
    hd=r.get('hidden',0);require(isinstance(hd,int) and hd>=0 and len(r.get('hidden_points') or [])==hd and all(xy(p) for p in r.get('hidden_points') or []) and isinstance(r.get('hidden_counted',0),int) and 0<=r.get('hidden_counted',0)<=hd,'隐藏层符号计数与点位不符')
    require(r['counted']+r['uncounted']+sum(ex.values())==r['on_sheet'] and len(up)==r['uncounted'] and len(dp)==r['duplicates'] and all(xy(p) for p in [*up,*dp]),'符号对账行计数与点位不符')
  # Symbols inside blocks that the reconciliation does not reach yet (contract 7.5): only counted and located.
  ib=s.get('symbol_in_block')
  if ib is not None:
   bl=ib.get('blocks') if isinstance(ib,dict) else None
   require(isinstance(bl,list) and bool(bl) and all(isinstance(b,dict) and string(b.get('block')) and isinstance(b.get('count'),int) and not isinstance(b['count'],bool) and b['count']>0
                                                     and isinstance(b.get('points'),list) and len(b['points'])==b['count'] and all(xy(p) for p in b['points']) and (b.get('sheet') is None or string(b['sheet'])) for b in bl)
           and ib.get('total')==sum(b['count'] for b in bl),'块内未对账符号计数与点位不符')
  if s.get('raster_path'):
   require(base_dir is not None,'raster_path需要输入文件目录')
   path=(Path(base_dir)/s['raster_path']).resolve();raw_image=path.read_bytes()
   mime='image/png' if raw_image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw_image.startswith(b'\xff\xd8\xff') else None
   require(mime is not None,'底图图片仅支持PNG/JPEG，SVG须转为可信几何或栅格')
   s['raster_data_uri']='data:'+mime+';base64,'+base64.b64encode(raw_image).decode();s.pop('raster_path')
  if s.get('raster_data_uri'):require(re.fullmatch(r'data:image/(png|jpeg);base64,[A-Za-z0-9+/=\s]+',s['raster_data_uri']) is not None,'不允许外部或可执行底图图片')
 objects=d.get('objects');require(isinstance(objects,list) and (objects or 'comparison' in d or 'delivery' in d),'缺少可复核对象')
 keys=set()
 for o in objects:
  for k in ['id','catalog_key','title','category','detail','source','status']:require(string(o.get(k)),'对象'+str(o.get('id'))+'缺少'+k)
  require(o['catalog_key'] not in keys,'对象catalog_key重复');keys.add(o['catalog_key'])
  require(o.get('professional') in PROFESSIONALS,'对象专业不属于13个专业Skill')
  require(o.get('kind') in ['point','line','polygon','unlocated'],'对象几何类型无效')
  if o['kind']!='unlocated':
   require(o.get('scene_id') in scene_ids,'对象图页缺失' if scene_ids else '没有图页时全部对象须为unlocated、scene_id为null')
   pts=o.get('points');minimum={'point':1,'line':2,'polygon':3}[o['kind']]
   require(isinstance(pts,list) and len(pts)>=minimum and all(xy(p) for p in pts),'对象几何无效')
   for ring in o.get('holes',[]):require(isinstance(ring,list) and len(ring)>=3 and all(xy(p) for p in ring),'面域孔洞无效')
  else:require(o.get('scene_id') in scene_ids or o.get('scene_id') is None,'未定位对象图页无效')
  if 'parts' in o:
   parts=o['parts'];require(o['kind']=='line' and isinstance(parts,list) and bool(parts) and all(isinstance(q,list) and len(q)>=2 and all(xy(p) for p in q) for q in parts) and parts[0]==o['points'],'对象'+str(o['id'])+'的parts须为line的折线列表（每段至少两点），points为其第一段')
  if o.get('unlocated_reason') is not None:require(o['kind']=='unlocated' and o['unlocated_reason'] in UNLOCATED_REASONS,'对象'+str(o['id'])+'的未定位原因无效或不是unlocated对象')
  o.setdefault('quantities',[])
  for q in o['quantities']:
   require(string(q.get('name')) and string(q.get('unit')) and string(q.get('level')),'数量须含名称、单位和证据层级')
   require(q.get('value') is None or number(q.get('value')),'数量必须为有限数字或null；不接受NaN/布尔值')
  o.setdefault('attributes',{});require(isinstance(o['attributes'],dict),'对象属性须为对象')
 groups=d.setdefault('groups',[]);gids=set();bykey={o['catalog_key']:o for o in objects}
 for g in groups:
  require(string(g.get('id')) and g['id'] not in gids,'问题组编号重复或为空');gids.add(g['id'])
  for k in ['title','category','proposal','reason','status']:require(string(g.get(k)),'问题组缺少'+k)
  members=g.get('member_keys');require(isinstance(members,list) and members and len(set(members))==len(members) and all(k in keys for k in members),'问题组成员不存在或重复')
  g['member_snapshot']=[{'catalog_key':k,'object_id':bykey[k]['id'],'title':bykey[k]['title'],'scene_id':bykey[k].get('scene_id'),'professional':bykey[k]['professional'],'quantities':copy.deepcopy(bykey[k]['quantities']),'attributes':copy.deepcopy(bykey[k]['attributes'])} for k in members]
  g['count']=len(members);g['object_ids']=list(dict.fromkeys(bykey[k]['id'] for k in members))
 if 'comparison' in d:d['comparison']['generation_check']=normalize_comparison(d,keys)
 for pool in [objects,groups,*[(d.get('comparison') or {}).get(k,[]) for k in ('items','issues','common_issues')]]:
  for target in pool:
   require(target.get('decision_id') is None or string(target['decision_id']),'decision_id须为非空文字；不同事项不可仅按裸编号关联')
 d.setdefault('receipts',{});require(isinstance(d['receipts'],dict),'receipts须为对象')
 for key,rc in d['receipts'].items():
  for k in ['status','assistant_response','source_annotation_updated_at']:require(string(rc.get(k)),'处理回执缺少'+k)
  require(isinstance(rc.get('object_ids'),list),'回执缺少处理对象范围')
 d.setdefault('summary',[])
 for q in d['summary']:
  require(string(q.get('label')) and string(q.get('unit')) and string(q.get('scope')),'汇总须含范围、名称和单位')
  require(q.get('value') is None or number(q['value']),'汇总数值无效')
 d['professional_labels']=PROFESSIONALS;d.setdefault('scope_notes',[])
 normalize_delivery(d)
 bind_result_versions(d)
 json.dumps(d,ensure_ascii=False,allow_nan=False)
 return d

def extract_json_file(path):
 text=Path(path).read_text(encoding='utf-8-sig')
 if Path(path).suffix.lower() in ['.html','.htm']:
  m=re.search(r'<script\s+id="feedback-seed"\s+type="application/json">([\s\S]*?)</script>',text)
  require(m is not None,'HTML没有批注数据块；不执行其中脚本');return json.loads(m.group(1))
 return json.loads(text)

def validate_feedback(raw, view):
 p=copy.deepcopy(raw);legacy=p.get('schema') in LEGACY_SCHEMAS
 require(legacy or p.get('schema') in FEEDBACK_SCHEMAS,'批注schema无效')
 out_schema=FEEDBACK_SCHEMA_COMPARISON if p.get('schema')==FEEDBACK_SCHEMA_COMPARISON else feedback_schema(view)
 if legacy:
  require(view.get('legacy_source_sha256') and p.get('source_sha256')==view['legacy_source_sha256'],'旧版批注来源未获当前视图显式绑定')
 else:
  require(p.get('source_sha256')==view['source_sha256'] and p.get('project_id')==view['project_id'] and p.get('review_id')==view['review_id'],'批注属于其他项目、复核范围或图纸版本')
 p.update(schema=out_schema,project_id=view['project_id'],review_id=view['review_id'],source_sha256=view['source_sha256'])
 if legacy:p['legacy_schema']=raw['schema'];p['legacy_source_sha256']=raw['source_sha256']
 require(isinstance(p.get('annotations'),dict),'缺少已保存批注')
 p.setdefault('drafts',{});p.setdefault('custom_markers',[])
 require(isinstance(p['drafts'],dict) and isinstance(p['custom_markers'],list),'草稿或新增标记无效')
 scene_ids={s['id'] for s in view['scenes']};keys={o['catalog_key'] for o in view['objects']};groups={g['id']:g for g in view['groups']};unmatched=[]
 comparison=view.get('comparison') or {};items={i['id']:i for i in comparison.get('items',[])};issues={q['id']:q for q in comparison.get('issues',[])};commons={g['id']:g for g in comparison.get('common_issues',[])}
 for m in p['custom_markers']:
  if 'delivery' in view:
   require(isinstance(m,dict),'历史新增标记须为对象');continue
  if legacy and not m.get('scene_id'):
   m['scene_id']=view.get('legacy_scene_map',{}).get(m.get('plan_side','supply'));m['professional']=view.get('legacy_professional')
   m.setdefault('catalog_key','我的新增标记|'+m['id']);m.setdefault('quantities',[]);m.setdefault('attributes',{})
  require(string(m.get('id')) and m.get('scene_id') in scene_ids,'新增标记编号/图页无效')
  require(isinstance(m.get('points'),list) and len(m['points'])==1 and xy(m['points'][0]),'新增标记坐标无效')
  require(m.get('professional') in PROFESSIONALS,'新增标记专业无效');keys.add(m.get('catalog_key','我的新增标记|'+m['id']))
 for field in ['annotations','drafts']:
  for key,r in p[field].items():
   require(key not in ['__proto__','constructor','prototype'] and isinstance(r,dict),'批注键或结构无效')
   # A merge conflict is re-judged on this view first: candidates that went stale no longer conflict (contract 9.1).
   mc=r.get('merge_conflict')
   if any(r.get(k) is not None for k in AUDIT_SNAPSHOTS) and isinstance(mc,dict) and isinstance(mc.get('candidates'),list) and len(mc['candidates'])>=2 and all(isinstance(x,dict) for x in mc['candidates']):
    r=p[field][key]=settle_merge_conflict(r,view)
   require(isinstance(r.get('confirmed_result',''),str) and isinstance(r.get('reason',''),str),'批注结果和原因必须是文字')
   audit=any(r.get(k) is not None for k in AUDIT_SNAPSHOTS)
   if audit:require(r.get('verdict') in (None,'',*VERDICTS),'快捷结论只能是同意、要改、待定')
   if field=='annotations':
    if audit:require(string(r.get('reason')) and (string(r.get('confirmed_result')) or r.get('verdict') in VERDICTS),'已保存审核意见须有原因，以及快捷结论或结论文字')
    else:require(string(r.get('confirmed_result')) and string(r.get('reason')),'已保存批注须有结果及原因')
   if audit:
    require(not r.get('group_id') and not r.get('object_snapshot') and sum(r.get(k) is not None for k in AUDIT_SNAPSHOTS)==1,'审核意见只能指向一个对照条目、问题或共性问题')
    ex=r.get('excluded_detail_row_ids',[]);require(isinstance(ex,list) and all(isinstance(x,str) for x in ex) and len(set(ex))==len(ex),'例外明细行须为不重复的编号')
    if r.get('page_view') is not None:require(r.get('item_snapshot') is not None and page_view_ok(r['page_view']),'page_view 须为 {consistent_band, conclusions}，只用于对照条目批注')
    require(r.get('option_id') in (None,'') or (string(r['option_id']) and r.get('item_snapshot') is None),'option_id 须为做法编号，只用于问题和共性问题批注')
    # Contract 9.1: an exception says why this place does not follow the common answer.
    require(r.get('exception_of') in (None,'') or (string(r['exception_of']) and r.get('common_issue_snapshot') is None),'exception_of 须为共性问题编号，只用于个别问题和条目批注')
    mc=r.get('merge_conflict')
    require(mc is None or (isinstance(mc,dict) and isinstance(mc.get('candidates'),list) and len(mc['candidates'])>=2 and all(isinstance(x,dict) for x in mc['candidates'])),'merge_conflict 须为 {candidates: [两条以上意见]}')
    if r.get('item_snapshot') is not None:
     snap=r['item_snapshot'];require(isinstance(snap,dict) and string(snap.get('item_id')) and key=='对照条目|'+snap['item_id'],'对照条目批注键与快照不一致')
     item=items.get(snap['item_id'])
     if item is None:unmatched.append({'annotation_key':key,'reason':'对照条目已不在当前视图，不自动应用'})
     else:require(set(ex)<={x['id'] for x in item.get('detail_rows',[])},'例外明细行不属于该条目')
    elif r.get('issue_snapshot') is not None:
     require(not ex,'问题批注没有明细行例外')
     snap=r['issue_snapshot'];require(isinstance(snap,dict) and string(snap.get('issue_id')) and key=='问题|'+snap['issue_id'],'问题批注键与快照不一致')
     if snap['issue_id'] not in issues:unmatched.append({'annotation_key':key,'reason':'问题已不在当前视图，不自动应用'})
    else:
     require(not ex,'共性问题批注没有明细行例外')
     snap=r['common_issue_snapshot'];sc=snap.get('scope') if isinstance(snap,dict) else None
     require(isinstance(snap,dict) and string(snap.get('common_issue_id')) and key=='共性问题|'+snap['common_issue_id'],'共性问题批注键与快照不一致')
     require(isinstance(sc,dict) and all(isinstance(sc.get(k),list) and all(isinstance(x,str) for x in sc[k]) for k in ('issue_ids','item_ids')),'共性问题快照须记答复时的适用范围 scope: {issue_ids, item_ids}')
     if snap['common_issue_id'] not in commons:unmatched.append({'annotation_key':key,'reason':'共性问题已不在当前视图，不自动应用'})
   elif r.get('group_id'):
    snaps=r.get('member_snapshot',[]);sm={s['catalog_key']:s for s in snaps};sel=r.get('selected_object_keys',[]);ex=r.get('excluded_object_keys',[])
    require(len(sm)==len(snaps) and len(set(sel))==len(sel) and len(set(ex))==len(ex),'批注成员重复')
    require(set(sel).isdisjoint(ex) and set(sel)|set(ex)==set(sm),'勾选与例外须完整覆盖本组快照')
    require(set(r.get('object_ids',[]))=={sm[k]['object_id'] for k in sel},'对象清单和成员快照不一致')
    if field=='annotations':require(bool(sel),'整组未选择成员')
    g=groups.get(r['group_id'])
    if g and 'delivery' not in view:require(set(sm)==set(g['member_keys']),'批注组成员与当前视图不同，须先核对版本及成员迁移')
    missing=[k for k in sel if k not in keys]
    if not g or missing:unmatched.append({'annotation_key':key,'missing_member_keys':missing,'reason':'历史组或对象需重新对应，不自动应用'})
   elif r.get('object_snapshot'):
    snap=r['object_snapshot'];require(isinstance(snap,dict),'对象快照无效')
    require(r.get('object_id')==snap.get('object_id') and r.get('object_ids')==[snap.get('object_id')],'单对象批注与快照对象不一致')
    if snap.get('catalog_key') not in keys:unmatched.append({'annotation_key':key,'reason':'所选对象已不在当前视图，不自动应用'})
    else:
     obj=next((o for o in view['objects'] if o['catalog_key']==snap['catalog_key']),None)
     if obj:require(obj['id']==snap['object_id'],'单对象快照与当前对象身份不一致')
 return classify_feedback(bind_delivery_feedback(p,view),view,unmatched)

def feedback_schema(view):
 return FEEDBACK_SCHEMA_COMPARISON if view.get('schema')==VIEW_SCHEMA_COMPARISON else FEEDBACK_SCHEMA

def empty_feedback(view):
 return bind_delivery_feedback({'schema':feedback_schema(view),'project_id':view['project_id'],'review_id':view['review_id'],'source_sha256':view['source_sha256'],'drawing_revision':view['revision'],'result_fingerprint':view['result_fingerprint'],'annotations':{},'drafts':{},'custom_markers':[]},view)

# Page payload (review view contract 11.1): transport only, the view itself is unchanged.
# Background items become a shared style table plus delta-coded integer coordinates; a set whose
# coordinates are not exact at <= 6 decimals, or whose items are not plain point/points, stays JSON.
PACK_MAGIC=b'QTOPACK1'
def _varint(n,out):
 while True:
  b=n&127;n>>=7
  if n:out.append(b|128)
  else:out.append(b);return
def _packable(items):
 pts=[]
 for it in items:
  if not isinstance(it,dict) or ('point' in it)==('points' in it):return None
  ps=[it['point']] if 'point' in it else it['points']
  if not isinstance(ps,list) or not ps or not all(isinstance(p,list) and len(p)==2 and all(type(c) in (int,float) and math.isfinite(c) for c in p) for p in ps):return None
  pts+=ps
 for nd in range(7):
  k=10**nd
  if all(round(c*k)/k==c for p in pts for c in p):return nd
 return None
# The page decodes with JavaScript numbers: every coordinate, delta and zigzag value must stay within 2^53-1 to be exact there.
JS_SAFE=2**53-1
def _pack_set(items,nd,index,styles):
 """Varint stream of one background set, or None when a value would leave the page decoder's exact range (the set then stays JSON)."""
 k=10**nd;buf=bytearray();x=y=0;new={}
 for it in items:
  st={f:val for f,val in it.items() if f not in ('point','points')};sk=json.dumps(st,ensure_ascii=False,sort_keys=True)
  if sk not in index and sk not in new:new[sk]=(len(styles)+len(new),st)
  ps=[it['point']] if 'point' in it else it['points']
  _varint(index[sk] if sk in index else new[sk][0],buf);_varint(0 if 'point' in it else len(ps),buf)
  for px,py in ps:
   X,Y=round(px*k),round(py*k)
   if abs(X)>JS_SAFE or abs(Y)>JS_SAFE:return None
   for d in (X-x,Y-y):
    z=d*2 if d>=0 else -d*2-1
    if z>JS_SAFE:return None
    _varint(z,buf)
   x,y=X,Y
 for sk,(n,st) in new.items():index[sk]=n;styles.append(st)
 return buf
def pack_payload(view):
 v=dict(view);v['scenes']=[dict(s) for s in view['scenes']]
 if isinstance(view.get('background_sets'),dict):v['background_sets']=dict(view['background_sets'])
 styles,index,cols,binary=[],{},[],bytearray()
 targets=[('set',k,items) for k,items in (v.get('background_sets') or {}).items()]+[('scene',n,s.get('background') or []) for n,s in enumerate(v['scenes'])]
 for where,key,items in targets:
  nd=_packable(items) if items else None
  if nd is None:continue
  buf=_pack_set(items,nd,index,styles)
  if buf is None:continue
  start=len(binary);binary+=buf
  cols.append({where:key,'count':len(items),'decimals':nd,'start':start,'end':len(binary)})
  if where=='set':v['background_sets'][key]=[]
  else:v['scenes'][key]['background']=[]
 if not cols:return json.dumps(view,ensure_ascii=False,separators=(',',':')).encode()
 v['background_pack']={'format':'qto-background-pack/1','styles':styles,'collections':cols}
 raw=json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
 return PACK_MAGIC+len(raw).to_bytes(4,'big')+raw+bytes(binary)
def page_decode(raw,core_js):
 """Decode a packed payload with the page's own readPayload under node (review view contract 11.1); None when node is missing."""
 import os,shutil,subprocess,tempfile
 node=shutil.which('node')
 if not node or not raw.startswith(PACK_MAGIC):return None
 code=("const fs=require('fs'),vm=require('vm'),ctx=vm.createContext({Response,Uint8Array,console});"
       "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
       "ctx.QtoReviewCore.readPayload(new Uint8Array(fs.readFileSync(process.argv[2]))).then(d=>process.stdout.write(JSON.stringify(d)),e=>{process.stderr.write(String(e&&e.message||e));process.exit(3);});")
 with tempfile.TemporaryDirectory() as d:
  p=os.path.join(d,'payload.bin');Path(p).write_bytes(raw)
  r=subprocess.run([node,'-e',code,str(core_js),p],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8')
 require(r.returncode==0,'页面解码器读不出载荷：'+r.stderr[-300:])
 return json.loads(r.stdout)
def payload_backgrounds(v):return [v.get('background_sets'),[s.get('background') for s in v['scenes']]]
def unpack_payload(raw):
 if not raw.startswith(PACK_MAGIC):return json.loads(raw)
 n=int.from_bytes(raw[8:12],'big');v=json.loads(raw[12:12+n]);b=raw[12+n:];pack=v.pop('background_pack');pos=0
 def vi():
  nonlocal pos
  val=shift=0
  while True:
   c=b[pos];pos+=1;val|=(c&127)<<shift;shift+=7
   if not c&128:return val
 for e in pack['collections']:
  pos=e['start'];k=10**e['decimals'];x=y=0;out=[]
  for _ in range(e['count']):
   it=dict(pack['styles'][vi()]);cnt=vi();ps=[]
   for _ in range(cnt or 1):
    dx,dy=vi(),vi();x+=dx//2 if dx%2==0 else -(dx+1)//2;y+=dy//2 if dy%2==0 else -(dy+1)//2
    ps.append([x,y] if k==1 else [x/k,y/k])
   if cnt:it['points']=ps
   else:it['point']=ps[0]
   out.append(it)
  require(pos==e['end'],'底图打包数据长度不符')
  if 'set' in e:v['background_sets'][e['set']]=out
  else:v['scenes'][e['scene']]['background']=out
 return v
