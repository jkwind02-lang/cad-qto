(function(root){
'use strict';
const schema='qto-review-feedback/2.0';
const scopes=['仅所选对象','本组勾选对象','本图同类对象（需核对范围）','供通用规则评估','本支路同类对象','本图相同画法'];
const instructions=['确认现结果','修改结果','排除对象','仍需核实','请助手复核','暂缓计算'];
const clone=x=>JSON.parse(JSON.stringify(x));
function ensure(ok,message){if(!ok)throw Error(message);}
function text(x){return typeof x==='string'?x:'';}
function cleanRecord(r,key,complete=true){
 ensure(r&&typeof r==='object'&&!Array.isArray(r),'批注结构无效');
 ensure(typeof r.confirmed_result==='string'&&typeof r.reason==='string','结果和原因须为文字');
 if(complete)ensure(r.confirmed_result.trim()&&r.reason.trim(),'请填写结论和判断原因；需要助手判断时也请说明问题');
 const q={...clone(r),annotation_id:key,object_id:text(r.object_id),object_ids:r.object_ids||[],
 confirmed_result:r.confirmed_result,reason:r.reason,scope:scopes.includes(r.scope)?r.scope:scopes[0],
 instruction:instructions.includes(r.instruction)?r.instruction:'请助手复核',history:r.history||[]};
 ensure(Array.isArray(q.object_ids)&&q.object_ids.every(x=>typeof x==='string'),'对象清单无效');
 ensure(Array.isArray(q.history),'历史记录无效');
 if(q.group_id){
  const sel=q.selected_object_keys||[],ex=q.excluded_object_keys||[],snap=q.member_snapshot||[];
  const sm=new Map(snap.map(m=>[m.catalog_key,m]));
  ensure(sm.size===snap.length&&new Set(sel).size===sel.length&&new Set(ex).size===ex.length,'成员快照或选择重复');
  ensure(sel.every(k=>sm.has(k))&&ex.every(k=>sm.has(k))&&!sel.some(k=>ex.includes(k))&&sel.length+ex.length===sm.size,'勾选成员与例外须完整覆盖本组，不能越组或同时选中排除');
  if(complete)ensure(sel.length,'请至少选择一个成员');
  const actual=new Set(sel.map(k=>sm.get(k).object_id));
  ensure(actual.size===new Set(q.object_ids).size&&q.object_ids.every(id=>actual.has(id)),'对象清单与勾选成员不一致');
 }
 else if(q.object_snapshot)ensure(q.object_id===q.object_snapshot.object_id&&q.object_ids.length===1&&q.object_ids[0]===q.object_snapshot.object_id,'单对象批注和对象快照不一致');
 return q;
}
function assessRecord(record,view){
 const objects=new Map(view.objects.map(o=>[o.catalog_key,o])),groups=new Map(view.groups.map(g=>[g.id,g]));
 const target=record.group_id?groups.get(record.group_id):objects.get(record.object_snapshot?.catalog_key);
 if(!target)return {state:'unmatched',reason:'历史对象或新增标记须由所属专业重新对应，不自动沿用确认'};
 const snaps=record.group_id?(record.member_snapshot||[]):[record.object_snapshot||{}];
 for(const snap of snaps){
  const current=objects.get(snap.catalog_key);
  if(!current)return {state:'unmatched',reason:'批注成员不在当前成果中，须先核对迁移'};
  ensure(current.id===snap.object_id,'批注成员快照与当前对象身份不一致');
  if(!snap.result_fingerprint||snap.result_fingerprint!==current.result_fingerprint)return {state:'stale',reason:'对象数量、规格、几何或采用依据已变化；旧意见仅保留为历史参考'};
 }
 if(!record.result_fingerprint||record.result_fingerprint!==target.result_fingerprint)return {state:'stale',reason:'意见未绑定当前成果版本，须重新确认；不得自动继承旧数字确认'};
 return {state:'current',reason:'已绑定当前成果快照；仍须所属专业复核，不自动改量'};
}

function parse(input,view){
 const raw=typeof input==='string'?JSON.parse(input):input,p=clone(raw);
 const legacy=['cad-qto-review-feedback/1.0','cad-qto-review-feedback/1.1'].includes(p.schema);
 ensure(legacy||p.schema===schema,'不是支持的复核批注文件');
 if(legacy)ensure(view.legacy_source_sha256&&p.source_sha256===view.legacy_source_sha256,'旧版批注未绑定到本图，不可跨项目导入');
 else ensure(p.project_id===view.project_id&&p.review_id===view.review_id&&p.source_sha256===view.source_sha256,'项目、复核范围或来源图纸不同，已停止导入');
 if(legacy){p.legacy_schema=p.schema;p.legacy_source_sha256=p.source_sha256;}
 p.schema=schema;p.project_id=view.project_id;p.review_id=view.review_id;p.source_sha256=view.source_sha256;
 ensure(p.annotations&&typeof p.annotations==='object'&&!Array.isArray(p.annotations),'缺少已保存批注');
 const keys=new Set(view.objects.map(o=>o.catalog_key)),groups=new Map(view.groups.map(g=>[g.id,g])),sceneIds=new Set(view.scenes.map(s=>s.id));
 p.custom_markers=p.custom_markers||[];ensure(Array.isArray(p.custom_markers),'新增标记无效');
 for(const m of p.custom_markers){
  if(legacy&&!m.scene_id){m.scene_id=view.legacy_scene_map?.[m.plan_side||'supply'];m.professional=view.legacy_professional;m.catalog_key=m.catalog_key||'我的新增标记|'+m.id;m.quantities=m.quantities||[];m.attributes=m.attributes||{};}
  ensure(sceneIds.has(m.scene_id)&&view.professional_labels[m.professional],'新增标记缺少有效图页或专业');
  ensure(m.id&&Array.isArray(m.points)&&m.points.length===1&&m.points[0].length===2&&m.points[0].every(Number.isFinite),'新增标记坐标无效');
  m.catalog_key=m.catalog_key||'我的新增标记|'+m.id;keys.add(m.catalog_key);
 }
 for(const field of ['annotations','drafts']){
  const records=p[field]||{},result=Object.create(null);ensure(typeof records==='object'&&!Array.isArray(records),'草稿或批注无效');
  for(const [k,r] of Object.entries(records)){
   ensure(!['__proto__','constructor','prototype'].includes(k),'批注键无效');
   const q=cleanRecord(r,k,field==='annotations');
   if(q.object_snapshot){const obj=view.objects.find(o=>o.catalog_key===q.object_snapshot.catalog_key);ensure(!obj||obj.id===q.object_snapshot.object_id,'单对象快照与当前对象身份不一致');}
   if(q.group_id&&groups.has(q.group_id)){
    const expected=groups.get(q.group_id).member_keys;
    ensure(q.member_snapshot.length===expected.length&&q.member_snapshot.every(m=>expected.includes(m.catalog_key)),'当前组成员已变化，请先核对历史批注适用范围');
   }
   const validity=assessRecord(q,view);
   q.applicability_state=validity.state;q.applicability_reason=validity.reason;
   result[k]=q;
  }
  p[field]=result;
 }
 p.current_result_fingerprint=view.result_fingerprint;
 return p;
}
function saveRecord(previous,next,key,now){
 const q=cleanRecord(next,key,true);q.updated_at=now;q.entry_origin='用户在HTML保存';
 q.history=clone(previous?.history||[]);
 if(previous){const old=clone(previous);delete old.history;q.history.push(old);}
 return q;
}
function mergeRecords(local,incoming){
 const annotations=Object.assign(Object.create(null),local);let conflicts=0;
 for(const [k,r] of Object.entries(incoming)){
  const old=annotations[k];if(!old){annotations[k]=r;continue;}
  if(JSON.stringify(old)===JSON.stringify(r))continue;
  const keep=(Date.parse(r.updated_at)||0)>=(Date.parse(old.updated_at)||0)?r:old;
  const other=clone(keep===r?old:r);delete other.history;
  annotations[k]={...keep,history:[...(keep.history||[]),{...other,conflict_from_merge:true}]};conflicts++;
 }
 return {annotations,conflicts};
}
function mergePackages(local,incoming){
 ensure(local.project_id===incoming.project_id&&local.review_id===incoming.review_id&&local.source_sha256===incoming.source_sha256,'不能合并异项目批注');
 const a=mergeRecords(local.annotations,incoming.annotations),d=mergeRecords(local.drafts,incoming.drafts),markers=clone(local.custom_markers);
 for(const m of incoming.custom_markers){const old=markers.find(x=>x.id===m.id);ensure(!old||JSON.stringify(old)===JSON.stringify(m),'新增标记编号冲突，已保留当前批注并停止导入');if(!old)markers.push(m);}
 for(const [k,r] of Object.entries(d.annotations))if((Date.parse(a.annotations[k]?.updated_at)||0)>=(Date.parse(r.updated_at)||0))delete d.annotations[k];
 return {...local,annotations:a.annotations,drafts:d.annotations,custom_markers:markers,merge_conflicts:a.conflicts+d.conflicts};
}
function embed(html,pkg){const json=JSON.stringify(pkg,null,1).replace(/^[ \t]+/gm,'').replace(/</g,'\\u003c');ensure(/id="feedback-seed"/.test(html),'页面缺少批注嵌入块');return html.replace(/(<script id="feedback-seed" type="application\/json">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+json+b);}
function readFileText(text){if(text.trimStart().startsWith('<')){const m=text.match(/<script id="feedback-seed" type="application\/json">([\s\S]*?)<\/script>/);ensure(m,'HTML没有批注数据块');return JSON.parse(m[1]);}return JSON.parse(text);}
function receiptState(receipt,annotation){return receipt&&annotation&&annotation.result_fingerprint&&annotation.applicability_state!=='stale'&&annotation.applicability_state!=='unmatched'&&receipt.source_annotation_updated_at===annotation.updated_at&&receipt.source_result_fingerprint===annotation.result_fingerprint?'current':receipt?'stale':'none';}
root.QtoReviewCore={schema,scopes,instructions,cleanRecord,parse,saveRecord,mergeRecords,mergePackages,embed,readFileText,receiptState,assessRecord};
})(globalThis);
