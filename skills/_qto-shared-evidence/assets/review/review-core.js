(function(root){
'use strict';
const schema='qto-review-feedback/2.0',comparisonSchema='qto-review-feedback/2.1';
const scopes=['仅所选对象','本组勾选对象','本图同类对象（需核对范围）','供通用规则评估','本支路同类对象','本图相同画法'];
const instructions=['确认现结果','修改结果','排除对象','仍需核实','请助手复核','暂缓计算'];
const verdicts=['同意','要改','待定'];
const clone=x=>JSON.parse(JSON.stringify(x));
function ensure(ok,message){if(!ok)throw Error(message);}
function text(x){return typeof x==='string'?x:'';}
const schemaFor=view=>view.schema==='qto-review-view/1.1'?comparisonSchema:schema;
const SNAPSHOTS=[['item_snapshot','item_id','items','对照条目'],['issue_snapshot','issue_id','issues','问题'],['common_issue_snapshot','common_issue_id','common_issues','共性问题']];
const isAudit=r=>r&&typeof r==='object'&&SNAPSHOTS.some(([k])=>r[k]!=null);
const auditTarget=r=>SNAPSHOTS.find(([k])=>r[k]!=null);
const strList=x=>Array.isArray(x)&&x.every(v=>typeof v==='string');
const conclusions=['一致','多算','少算','第三方无量','图上无','缺图','归入他项'],bandClasses=['计量','计数'];
const nonneg=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0,plain=v=>v&&typeof v==='object'&&!Array.isArray(v);
// What the reviewer saw under the page tolerance (contract 9); never used for staleness.
function pageViewOk(pv){if(!plain(pv))return false;const b=pv.consistent_band,c=pv.conclusions;const bandOk=nonneg(b)||(plain(b)&&Object.keys(b).length===bandClasses.length&&bandClasses.every(k=>nonneg(b[k])));return !!bandOk&&plain(c)&&Object.values(c).every(v=>conclusions.includes(v));}
// Comparison item / issue comments (contract 9): reason always; a quick verdict may stand in for the result text.
function cleanAudit(r,key,complete){
 ensure(typeof r.confirmed_result==='string'&&typeof r.reason==='string','结果和原因须为文字');
 ensure([undefined,null,'',...verdicts].includes(r.verdict),'快捷结论只能是同意、要改、待定');
 if(complete)ensure(r.reason.trim()&&(r.confirmed_result.trim()||verdicts.includes(r.verdict)),'已保存审核意见须有原因，以及快捷结论或结论文字');
 ensure(!r.group_id&&!r.object_snapshot&&SNAPSHOTS.filter(([k])=>r[k]!=null).length===1,'审核意见只能指向一个对照条目、问题或共性问题');
 const ex=r.excluded_detail_row_ids||[];ensure(Array.isArray(ex)&&ex.every(x=>typeof x==='string')&&new Set(ex).size===ex.length,'例外明细行须为不重复的编号');
 if(r.page_view!=null)ensure(r.item_snapshot!=null&&pageViewOk(r.page_view),'page_view 须为 {consistent_band, conclusions}，只用于对照条目批注');
 const filled=x=>typeof x==='string'&&x.trim()!=='';
 ensure([undefined,null,''].includes(r.option_id)||(filled(r.option_id)&&r.item_snapshot==null),'option_id 须为做法编号，只用于问题和共性问题批注');
 // Contract 9.1: an exception says why this place does not follow the common answer.
 ensure([undefined,null,''].includes(r.exception_of)||(filled(r.exception_of)&&r.common_issue_snapshot==null),'exception_of 须为共性问题编号，只用于个别问题和条目批注');
 const mc=r.merge_conflict;ensure(mc==null||(plain(mc)&&Array.isArray(mc.candidates)&&mc.candidates.length>=2&&mc.candidates.every(plain)),'merge_conflict 须为 {candidates: [两条以上意见]}');
 const [field,ident,,label]=auditTarget(r),snap=r[field];
 ensure(plain(snap)&&typeof snap[ident]==='string'&&snap[ident]&&key===(field==='item_snapshot'?'对照条目|':label+'|')+snap[ident],label+'批注键与快照不一致');
 if(field!=='item_snapshot')ensure(!ex.length,label+'批注没有明细行例外');
 if(field==='common_issue_snapshot')ensure(plain(snap.scope)&&strList(snap.scope.issue_ids)&&strList(snap.scope.item_ids),'共性问题快照须记答复时的适用范围 scope: {issue_ids, item_ids}');
 return {...clone(r),annotation_id:key,verdict:r.verdict||'',excluded_detail_row_ids:ex,history:r.history||[]};
}
function cleanRecord(r,key,complete=true){
 ensure(r&&typeof r==='object'&&!Array.isArray(r),'批注结构无效');
 if(isAudit(r)){const q=cleanAudit(r,key,complete);ensure(Array.isArray(q.history),'历史记录无效');return q;}
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
 if(isAudit(record)){
  const [field,ident,pool,label]=auditTarget(record),snap=record[field],c=view.comparison||{};
  const current=(c[pool]||[]).find(x=>x.id===snap[ident]);
  if(!current)return {state:'unmatched',reason:label+'已不在当前成果中，须由所属专业重新对应'};
  if(record.exception_of){const g=(c.common_issues||[]).find(x=>x.id===record.exception_of),scope=g?.scope||{};
   if(!(scope[pool==='issues'?'issue_ids':'item_ids']||[]).includes(snap[ident]))return {state:'unmatched',reason:'例外所指的共性问题已不覆盖本处，例外不再适用，须重新核对'};}
  if(snap.result_fingerprint!==current.result_fingerprint||record.result_fingerprint!==current.result_fingerprint)return {state:'stale',reason:label+(pool==='common_issues'?'的问法、正文、性质、做法或处理方':'的第三方量、本次量、结论或明细')+'已变化；旧意见仅保留为历史参考'};
  ensure(!record.option_id||(current.options||[]).some(o=>o.id===record.option_id),'所选做法不在该问题的做法里：'+record.option_id);
  return {state:'current',reason:'已绑定当前成果快照；仍须所属专业复核，不自动改量'};
 }
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
 ensure(legacy||p.schema===schema||p.schema===comparisonSchema,'不是支持的复核批注文件');
 if(legacy)ensure(view.legacy_source_sha256&&p.source_sha256===view.legacy_source_sha256,'旧版批注未绑定到本图，不可跨项目导入');
 else ensure(p.project_id===view.project_id&&p.review_id===view.review_id&&p.source_sha256===view.source_sha256,'项目、复核范围或来源图纸不同，已停止导入');
 if(legacy){p.legacy_schema=p.schema;p.legacy_source_sha256=p.source_sha256;}
 p.schema=p.schema===comparisonSchema?comparisonSchema:schemaFor(view);p.project_id=view.project_id;p.review_id=view.review_id;p.source_sha256=view.source_sha256;
 const auditItems=new Map((view.comparison?.items||[]).map(i=>[i.id,i]));
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
   // A merge conflict is re-judged on this view first: candidates that went stale no longer conflict (contract 9.1).
   const q=cleanRecord(isAudit(r)&&r.merge_conflict&&Array.isArray(r.merge_conflict.candidates)&&r.merge_conflict.candidates.length>=2&&r.merge_conflict.candidates.every(plain)?settleOnView(r,view):r,k,field==='annotations');
   if(q.item_snapshot&&auditItems.has(q.item_snapshot.item_id)){const rows=new Set(auditItems.get(q.item_snapshot.item_id).detail_rows.map(x=>x.id));ensure(q.excluded_detail_row_ids.every(x=>rows.has(x)),'例外明细行不属于该条目');}
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
 const q=cleanRecord(next,key,true);q.updated_at=now;q.entry_origin='用户在HTML保存';delete q.merge_conflict;   // re-saving one opinion settles a merge conflict
 q.history=clone(previous?.history||[]);
 if(previous){const old=clone(previous);delete old.history;q.history.push(old);}
 return q;
}
// The opinion of an audit comment: quick verdict, chosen option and exception target (contract 9.1).
const opinion=r=>JSON.stringify([r.verdict||'',r.option_id||'',r.exception_of||'']);
const bare=r=>{const x=clone(r);delete x.history;delete x.merge_conflict;return x;};
const later=(a,b)=>(Date.parse(b.updated_at)||0)>=(Date.parse(a.updated_at)||0)?b:a;
// Contract 9.1: only current opinions can conflict (qto_review_revision.settle_candidates mirrors this). Stale or unmatched candidates
// become history; two or more different current opinions stay a conflict, otherwise the latest current one (or the latest) is kept.
function settleCandidates(history,cands,isLive){
 const cur=cands.filter(isLive),distinct=new Set(cur.map(opinion)).size,keep=(cur.length?cur:cands).reduce(later);
 const rest=cands.filter(x=>x!==keep&&(distinct<2||!isLive(x)));
 const out={...clone(keep),history:[...clone(history||[]),...rest.map(x=>({...bare(x),conflict_from_merge:true}))]};
 if(distinct>1)out.merge_conflict={candidates:clone(cur)};else delete out.merge_conflict;
 return out;
}
// Records not judged against a view yet count as current.
const judged=x=>!x.applicability_state||x.applicability_state==='current';
function settleOnView(r,view){
 const isLive=x=>{try{return assessRecord(x,view).state==='current';}catch(e){return false;}};
 return settleCandidates(r.history,r.merge_conflict.candidates.map(clone),isLive);
}
function mergeRecords(local,incoming){
 const annotations=Object.assign(Object.create(null),local);let conflicts=0;
 for(const [k,r] of Object.entries(incoming)){
  const old=annotations[k];if(!old){annotations[k]=r;continue;}
  if(JSON.stringify(old)===JSON.stringify(r))continue;
  // Two different current opinions are not settled by time: both are kept until the user re-saves one. A stale one never blocks or hides a current one.
  if(isAudit(old)&&isAudit(r)){
   const mine=old.merge_conflict?.candidates||[bare(old)],theirs=r.merge_conflict?.candidates||[bare(r)],cands=[...mine,...theirs];
   const distinct=cands.filter((x,n)=>cands.findIndex(y=>JSON.stringify(y)===JSON.stringify(x))===n);
   const owner=x=>mine.some(y=>JSON.stringify(y)===JSON.stringify(x))?old:r;
   const keep=(distinct.filter(judged).length?distinct.filter(judged):distinct).reduce(later);
   annotations[k]=settleCandidates(owner(keep).history,distinct,judged);conflicts++;continue;
  }
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
// Why an object has no drawing position (scene mapping contract 8); unknown codes are shown as written.
const UNLOCATED={no_geometry:'没有坐标',handle_not_found:'句柄查不到',handle_in_block:'在块内，回不到',sheet_unknown:'图页不在图页表里',outside_sheet:'落在图页范围外',estimated:'估算，没有图面路径'};
const unlocatedText=o=>o&&o.unlocated_reason?(UNLOCATED[o.unlocated_reason]||o.unlocated_reason)+(o.unlocated_note?'：'+o.unlocated_note:''):'';
// Page payload (review view contract 11.1): plain JSON, or QTOPACK1 + JSON length + JSON + background items packed as style index and delta integers.
async function readPayload(buf){
 if(String.fromCharCode(...buf.subarray(0,8))!=='QTOPACK1')return JSON.parse(await new Response(buf).text());
 const n=buf[8]*16777216+buf[9]*65536+buf[10]*256+buf[11],data=JSON.parse(await new Response(buf.subarray(12,12+n)).text()),bin=buf.subarray(12+n),pack=data.background_pack;
 let i=0;const vi=()=>{let v=0,m=1,b;do{ensure(i<bin.length,'底图打包数据不完整');b=bin[i++];v+=(b&127)*m;m*=128;}while(b&128);return v;},zz=v=>v%2?-(v+1)/2:v/2;
 for(const e of pack.collections){
  i=e.start;const k=10**e.decimals,out=[];let x=0,y=0;const pt=()=>{x+=zz(vi());y+=zz(vi());return [x/k,y/k];};
  for(let c=0;c<e.count;c++){const o={...pack.styles[vi()]},cnt=vi();if(cnt)o.points=Array.from({length:cnt},pt);else o.point=pt();out.push(o);}
  ensure(i===e.end,'底图打包数据长度不符');
  if('set' in e)data.background_sets[e.set]=out;else data.scenes[e.scene].background=out;
 }
 delete data.background_pack;return data;
}
root.QtoReviewCore={schema,comparisonSchema,schemaFor,verdicts,scopes,instructions,cleanRecord,parse,saveRecord,mergeRecords,mergePackages,embed,readFileText,receiptState,assessRecord,isAudit,opinion,unlocatedText,readPayload};
})(globalThis);
