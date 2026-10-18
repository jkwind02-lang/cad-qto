(function(root){
'use strict';
// Optional single editor. All facts, scopes, options and references are supplied by the professional.
const STATE_LABELS={conflict:'意见冲突',migration_required:'待迁移核对',stale:'待重新核对',unmatched:'身份待核'};
const PREFIX='审核事项|',ACTIONS=['采用建议','调整处理','退回核查','待资料确认','暂不处理'];
const clone=x=>JSON.parse(JSON.stringify(x)),ensure=(ok,msg)=>{if(!ok)throw Error(msg);};
const stable=x=>JSON.stringify(x,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const same=(a,b)=>stable(a)===stable(b),enabled=v=>v.review_workflow?.enabled===true;
const decisions=v=>enabled(v)?v.review_workflow.decisions:[];
const isRecord=r=>!!r&&r.decision_snapshot!=null;
const snapshot=d=>Object.fromEntries(['id','revision','title','result_fingerprint','scope','exception_of'].map(k=>[k,clone(d[k]??null)]));
const pools=v=>({object:v.objects,group:v.groups,item:v.comparison?.items||[],issue:v.comparison?.issues||[],common:v.comparison?.common_issues||[],delivery_item:v.delivery?.items||v.delivery?.quantity_rows?.filter(r=>['current','reference'].includes(r.state))||[]});
function resolveRef(view,kind,id){const t=(pools(view)[kind]||[]).find(x=>(kind==='object'?x.catalog_key:x.id)===id)||{};return decisions(view).filter(d=>d.id===t.decision_id||(d.refs||[]).some(r=>r.kind===kind&&r.id===id)).map(d=>d.id);}
function recordRef(r){for(const [f,id,k] of [['item_snapshot','item_id','item'],['issue_snapshot','issue_id','issue'],['common_issue_snapshot','common_issue_id','common']])if(r[f])return [k,r[f][id]];return r.group_id?['group',r.group_id]:['object',r.object_snapshot?.catalog_key];}
function cleanRecord(r,key,complete=true){
 if(r?.decision_conflict?.candidates?.length>=2)r=mergeRecord(r,r);
 ensure(isRecord(r)&&r.decision_snapshot&&typeof r.decision_snapshot==='object','审核事项记录缺少身份快照');const s=r.decision_snapshot;
 ensure(typeof s.id==='string'&&key===PREFIX+s.id,'审核事项键与稳定身份不一致');
 ensure(!['group_id','object_snapshot','item_snapshot','issue_snapshot','common_issue_snapshot'].some(k=>r[k]!=null),'统一事项不可兼带另一通道身份');
 ensure(s.scope&&['member_keys','excluded_member_keys'].every(k=>Array.isArray(s.scope[k])&&s.scope[k].every(x=>typeof x==='string')),'审核事项须保留答复时范围快照');
 ensure(typeof (r.confirmed_result??'')==='string'&&typeof(r.reason??'')==='string','结论及依据须为文字');ensure(Array.isArray(r.history||[]),'事项历史须为列表');
 const ex=r.excluded_member_keys||[];ensure(Array.isArray(ex)&&new Set(ex).size===ex.length&&ex.every(k=>s.scope.member_keys.includes(k)),'用户排除范围不得越出答复时成员');
 ensure(!r.decision_conflict||(Array.isArray(r.decision_conflict.candidates)&&r.decision_conflict.candidates.length>=2&&r.decision_conflict.candidates.every(x=>x&&typeof x==='object')),'事项冲突须完整保留候选');
 if(complete&&!r.migration_state){ensure(ACTIONS.includes(r.action),'请选择审核动作');if(['调整处理','退回核查'].includes(r.action))ensure(r.user_note?.trim(),'调整或退回须补充一项必要说明');if(r.action==='采用建议')ensure(r.recommendation_receipt?.snapshot&&r.recommendation_receipt.user_choice==='采用建议'&&r.recommendation_receipt.reason_origin==='助手建议引用','采用建议须保存建议快照、助手来源及用户选择');}
 return {...clone(r),annotation_id:key,history:clone(r.history||[])};
}
function recommendationProblem(d,prior=null,now=Date.now()){
 if(d.kind!=='business_choice')return '资料缺口和分类只读，不接受整组认可';const r=d.recommendation||{};
 if(!['id','version','text','reason','assistant_source'].every(k=>typeof r[k]==='string'&&r[k].trim()))return '建议版本、内容、依据或助手来源不足';
 if(!Array.isArray(r.evidence_ids)||!r.evidence_ids.length||!r.evidence_ids.every(id=>d.evidence.some(e=>e.id===id)))return '建议缺少可追溯依据';
 if(d.evidence.filter(e=>r.evidence_ids.includes(e.id)).some(e=>e.state!=='verified'))return '建议所引依据尚未核实，不可快捷采用';
 if(!d.business_options.some(o=>o.id===r.business_option_id))return '建议缺少本事项的具体业务做法';
 if((r.status||'current')!=='current')return '参考建议已过期或待核';
 if(r.expires_at){const n=Date.parse(r.expires_at);if(!/(Z|[+-]\d\d:\d\d)$/.test(r.expires_at)||!Number.isFinite(n))return '建议有效期无效';if(n<=now)return '参考建议已过期';}
 const scope=d.scope,active=scope.member_keys.filter(k=>!scope.excluded_member_keys.includes(k)).sort();
 if(!Array.isArray(r.scope_member_keys)||!same([...new Set(r.scope_member_keys)].sort(),active))return '建议适用范围与当前事项不一致；新增范围须另核';
 if(prior){const old=prior.decision_snapshot?.scope||{};if(!same([...(old.member_keys||[])].sort(),[...scope.member_keys].sort())||!same([...(old.excluded_member_keys||[])].sort(),[...scope.excluded_member_keys].sort()))return '答复后适用或排除范围变化，须调整处理并核对新增成员';if(prior.migration_state||prior.decision_conflict||prior.identity_hold)return '旧意见迁移或冲突须先人工核对，不可快捷覆盖';}
 return null;
}
function assessRecord(r,view){
 const d=decisions(view).find(d=>d.id===r.decision_snapshot?.id);if(!d)return {state:'unmatched',reason:'审核事项身份不存在或工作流未启用'};
 if(r.decision_conflict)return {state:'conflict',reason:'同一事项意见冲突，完整候选保留；不按时间择一'};
 if(r.migration_state)return {state:'migration_required',reason:'旧意见已对应事项，尚未重新审核；原话、草稿、历史和来源保留'};
 if(r.identity_hold)return {state:'migration_required',reason:r.identity_hold};
 if(!same(r.decision_snapshot,snapshot(d)))return {state:'stale',reason:'事项事实、建议、成果或适用排除范围变化，须重新核对'};
 if(d.kind!=='business_choice')return {state:'unmatched',reason:'资料缺口或分类只读，不是业务取舍事项'};
 if((r.exception_of??null)!==(d.exception_of??null))return {state:'unmatched',reason:'特例关系与当前事项不一致'};
 if(r.business_option_id&&!d.business_options.some(o=>o.id===r.business_option_id))return {state:'unmatched',reason:'业务做法不属于当前事项'};
 if(r.action==='采用建议'){const problem=recommendationProblem(d),receipt=r.recommendation_receipt||{},expected={snapshot:d.recommendation,evidence:d.evidence.filter(e=>d.recommendation.evidence_ids?.includes(e.id)),user_choice:'采用建议',reason_origin:'助手建议引用'};
  if(problem||Object.keys(expected).some(k=>!same(receipt[k],expected[k]))||r.business_option_id!==d.recommendation.business_option_id||r.confirmed_result!==d.recommendation.text||r.reason!==d.recommendation.reason)return {state:'stale',reason:problem||'建议快照、来源或依据与当前建议不一致'};}
 return {state:'current',reason:'已审核，待所属专业复算；方法选择不等于数量核准'};
}
function migratePackage(p,view){
 if(!enabled(view))return p;const archive=p.legacy_review_records=p.legacy_review_records||[];ensure(Array.isArray(archive),'旧意见迁移档案无效');const pending=[];
 for(const field of ['annotations','drafts'])for(const r of Object.values(p[field]||{}))if(isRecord(r))delete r.identity_hold;
 const sameOrigin=(a,b)=>['field','annotation_key','record'].every(k=>same(a[k],b[k]));
 if(view.review_workflow.migrate_legacy)for(const a of [...archive]){if(a.candidate_decision_ids?.length===1||!a.record)continue;const matches=resolveRef(view,...recordRef(a.record)),linked=archive.some(b=>sameOrigin(a,b)&&same(b.candidate_decision_ids,matches));if(matches.length===1&&!linked&&['annotations','drafts'].includes(a.field)&&!p[a.field]?.[a.annotation_key]){p[a.field]=p[a.field]||{};p[a.field][a.annotation_key]=clone(a.record);}}
 for(const field of ['annotations','drafts'])for(const [key,r] of Object.entries(p[field]||{})){
  if(isRecord(r))continue;ensure(r&&typeof r==='object'&&!Array.isArray(r),'旧意见须为对象');const matches=resolveRef(view,...recordRef(r));
  if(!view.review_workflow.migrate_legacy){pending.push({field,annotation_key:key,candidate_decision_ids:matches,reason:'未启用旧意见迁移'});continue;}
  const a={field,annotation_key:key,record:clone(r),source_schema:p.legacy_schema||p.schema,candidate_decision_ids:matches};if(!archive.some(x=>same(a,x)))archive.push(a);delete p[field][key];
  if(matches.length!==1){pending.push({field,annotation_key:key,candidate_decision_ids:matches,reason:'显式事项映射缺失、歧义或旧映射变化，须迁移核对，不按同号或成员重叠合并'});continue;}
  const d=decisions(view).find(d=>d.id===matches[0]),dest=PREFIX+d.id,q={annotation_id:dest,decision_snapshot:snapshot(d),confirmed_result:r.confirmed_result||'',reason:r.reason||'',history:[],migration_state:'review_required',entry_origin:'旧意见显式迁移，未重审',updated_at:r.updated_at??null,legacy_sources:[clone(a)],exception_of:d.exception_of??null},old=p[field][dest];
  if(old){const sources=old.legacy_sources=old.legacy_sources||[];if(!sources.some(x=>same(a,x)))sources.push(clone(a));if(field==='annotations'){const candidates=old.decision_conflict?.candidates||[clone(old)];candidates.push(q);old.decision_conflict={candidates};}else{old.history=old.history||[];old.history.push(q);}}else p[field][dest]=q;
 }
 for(const a of archive){const matches=resolveRef(view,...recordRef(a.record||{})),linked=matches.length===1&&archive.some(b=>sameOrigin(a,b)&&same(b.candidate_decision_ids,matches));if(!linked){const x={field:a.field,annotation_key:a.annotation_key,candidate_decision_ids:matches,reason:'显式事项映射缺失、歧义或旧映射变化，须迁移核对，不按同号或成员重叠合并'};if(!pending.some(y=>same(y,x)))pending.push(x);}}
 p.decision_migration_pending=pending;return p;
}
function mergeRecord(a,b){
 if(same(a,b)&&!a.decision_conflict)return clone(a);
 // Metadata can differ on duplicate imports. The decision, evidence version and scope determine whether opinions conflict.
 const unique=xs=>xs.map(clone).filter((r,n,all)=>all.findIndex(x=>same(x,r))===n);
 const bare=r=>{const q=clone(r);delete q.history;delete q.decision_conflict;return q;};
 const semantic=r=>{const q=bare(r);for(const k of ['annotation_id','updated_at','entry_origin','applicability_state','applicability_reason','identity_hold','legacy_sources'])delete q[k];if(q.recommendation_receipt&&typeof q.recommendation_receipt==='object')delete q.recommendation_receipt.chosen_at;if(Array.isArray(q.excluded_member_keys))q.excluded_member_keys.sort();return stable(q);};
 const flatten=r=>r.decision_conflict?.candidates?.length>=2?r.decision_conflict.candidates.flatMap(flatten):[r];
 const records=[...flatten(a),...flatten(b)];ensure([a,b,...records].every(r=>Array.isArray(r.history||[])),'事项历史须为列表');
 const groups=new Map();for(const r of records){const k=semantic(r);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r);}
 const candidates=[...groups.values()].map(records=>{const head=bare(records[0]);head.history=unique([...records.flatMap(r=>r.history||[]),...records.slice(1).filter(r=>!same(bare(r),head)).map(bare)]);return head;});
 // Keep the first local representative, never choose an opinion by newest timestamp. Other times/origins remain in history.
 const out=clone(candidates[0]);out.history=unique([...(a.history||[]),...(b.history||[]),...candidates.flatMap(r=>r.history)]);
 const sources=unique([...(a.legacy_sources||[]),...(b.legacy_sources||[]),...candidates.flatMap(r=>r.legacy_sources||[])]);if(sources.length)out.legacy_sources=sources;
 if(candidates.length>1)out.decision_conflict={candidates};else delete out.decision_conflict;return out;
}
function adopt(d,prior,now=new Date().toISOString()){
 const problem=recommendationProblem(d,prior);ensure(!problem,problem);const r=d.recommendation;
 return {decision_snapshot:snapshot(d),action:'采用建议',business_option_id:r.business_option_id,confirmed_result:r.text,reason:r.reason,user_note:'',exception_of:d.exception_of??null,excluded_member_keys:clone(prior?.excluded_member_keys||[]),recommendation_receipt:{snapshot:clone(r),evidence:clone(d.evidence.filter(e=>r.evidence_ids.includes(e.id))),user_choice:'采用建议',reason_origin:'助手建议引用',chosen_at:now},entry_origin:'用户选择采用，结论及依据引用助手建议',updated_at:now,history:[]};
}
function attach(api){
 const {data,core}=api;if(!enabled(data))return null;const $=id=>document.getElementById(id),el=(tag,text)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;return e;},button=(text,fn)=>{const b=el('button',text);b.type='button';b.onclick=fn;return b;};
 document.body.classList.add('single-review-workflow');
 const style=el('style');style.textContent='.single-review-workflow #audit-form,.single-review-workflow #review-fields,.single-review-workflow #mode-tabs{display:none!important}.workflow-open #layout,.workflow-open #audit,.workflow-open #delivery{display:none!important}#workflow-nav{padding:8px 16px;display:flex;gap:8px;background:#e9f2f7}#workflow-panel{overflow:auto;padding:16px;background:#f6f9fc;max-height:calc(100vh - 140px)}#workflow-panel[hidden]{display:none}#workflow-body{display:grid;grid-template-columns:minmax(220px,30%) minmax(0,1fr);gap:16px}#workflow-list{max-height:74vh;overflow:auto}#workflow-list button{display:block;width:100%;text-align:left;margin:5px 0}#workflow-editor{background:white;border:1px solid #cedde6;padding:16px;border-radius:8px}#workflow-editor p,#workflow-editor pre{white-space:pre-wrap;overflow-wrap:anywhere}#workflow-editor pre{max-height:230px;overflow:auto}#workflow-editor label{display:block;margin:8px 0}#workflow-note{display:block;width:100%;min-height:80px}#workflow-editor input[type=checkbox]{width:auto}#workflow-editor select{max-width:100%}#workflow-status{color:#704700}#workflow-history{margin-top:15px}@media(max-width:700px){#workflow-body{grid-template-columns:1fr}#workflow-list{max-height:180px}}';document.head.append(style);
 // Keep drawing facts visible and hide only the old editing controls.
 style.textContent+=' .single-review-workflow #editor>:not(#editor-title):not(#object-facts):not(#source-details):not(#current-adoption):not(#workflow-context-link){display:none!important}';
 const legacy=$('review-result')?.closest('aside');if(legacy){legacy.querySelector('.review-titlebar span').textContent='图面证据与关联事项';legacy.querySelector('.footer-note').textContent='图面用于核对证据；处理意见统一在关联审核事项填写。';}
 const map=$('map-section');if(map&&!data.delivery){style.textContent+=' .workflow-map-fullscreen{position:fixed!important;inset:10px!important;width:auto!important;height:auto!important;z-index:100!important;background:white;box-shadow:0 10px 40px #20364b66}';const full=button('图面全屏',()=>{const on=map.classList.toggle('workflow-map-fullscreen');full.textContent=on?'退出图面全屏':'图面全屏';});full.id='workflow-map-fullscreen';map.querySelector('.bar').append(full);root.addEventListener('keydown',e=>{if(e.key==='Escape'){map.classList.remove('workflow-map-fullscreen');full.textContent='图面全屏';}});}
 const nav=el('nav');nav.id='workflow-nav';const panel=el('section');panel.id='workflow-panel';panel.hidden=true;
 const h=el('h2','审核事项'),body=el('div');body.id='workflow-body';const list=el('div');list.id='workflow-list';const editor=el('article');editor.id='workflow-editor';body.append(list,editor);panel.append(h,body);
 const header=document.querySelector('header');(header||document.body).after(nav);nav.after(panel);
 let current=null,showDone=false,draftAction='调整处理',contextRef=null;
 const fb=()=>api.getFeedback(),show=()=>{panel.hidden=false;document.body.classList.add('workflow-open');},hide=()=>{panel.hidden=true;document.body.classList.remove('workflow-open');};
 const firstPending=()=>decisions(data).find(d=>{const r=fb().annotations[PREFIX+d.id];return !!fb().drafts[PREFIX+d.id]||!r||assessRecord(r,data).state!=='current'||['待资料确认','暂不处理'].includes(r.action);});
 nav.append(button('工程量',()=>{hide();api.showQuantity?.();}),button('审核事项',()=>{if(contextRef){openTarget(contextRef.kind,contextRef.id);return;}show();renderList();if(!current)open(firstPending()?.id);}),button('查看图面证据',()=>{hide();api.showEvidence?.(current);}));
 const done=el('input');done.type='checkbox';done.id='workflow-show-done';done.onchange=()=>{showDone=done.checked;renderList();};const dl=el('label');dl.append(done,document.createTextNode('显示已处理事项'));panel.insertBefore(dl,body);
 function renderList(){list.replaceChildren();const ordered=[...decisions(data)].sort((a,b)=>Number(!!a.exception_of)-Number(!!b.exception_of)||Number(!a.is_common)-Number(!b.is_common));for(const d of ordered){const r=fb().annotations[PREFIX+d.id],valid=r?assessRecord(r,data):null;if(!showDone&&!fb().drafts[PREFIX+d.id]&&valid?.state==='current'&&!['待资料确认','暂不处理'].includes(r.action)&&current!==d.id)continue;const b=button((d.group_label?d.group_label+' · ':'')+(d.exception_of?'特例 · ':'')+d.title+(valid?' · '+(valid.state==='current'?(['待资料确认','暂不处理'].includes(r.action)?r.action:'已审核待复算'):(STATE_LABELS[valid.state]||'待核')):''),()=>open(d.id));b.dataset.decisionId=d.id;list.append(b);}const pending=fb().decision_migration_pending||[];if(pending.length){const det=el('details');det.id='workflow-pending';det.append(el('summary','旧意见待迁移 '+pending.length+' 项'),el('pre',JSON.stringify(pending,null,2)));list.append(det);}}
 function textBlock(label,value){editor.append(el('h3',label),el('p',value||'未提供；须由所属专业补充'));}
 function open(id){const d=decisions(data).find(d=>d.id===id);show();current=d?.id||null;editor.replaceChildren();if(!d){delete editor.dataset.decisionId;editor.append(el('p','暂无待处理事项；可勾选显示已处理事项。未绑定事项的图面仅作证据。'));return false;}renderList();editor.dataset.decisionId=d.id;editor.append(el('h2',d.title));textBlock('需要决定',d.question);textBlock('已核事实',d.facts.join('\n'));textBlock('资料缺口',d.gaps.length?d.gaps.join('\n'):'未列出');textBlock('适用和排除范围',d.scope.basis+'\n适用：'+d.scope.member_keys.join('、')+'\n排除：'+(d.scope.excluded_member_keys.join('、')||'无')+(d.exception_of?'\n特例对应事项：'+d.exception_of:''));const children=decisions(data).filter(x=>x.exception_of===d.id);if(children.length)textBlock('单独处理的特例',children.map(x=>x.title+'：'+x.scope.member_keys.join('、')+'；这些成员不随本共性答复采用').join('\n'));textBlock('按单位影响',d.impacts.map(i=>(i.value==null?'未知':i.value)+' '+i.unit+' · '+i.level+' · '+i.scope).join('\n'));textBlock('参考建议及理由',d.recommendation?(d.recommendation.text||'内容待补')+'\n'+(d.recommendation.reason||'依据待补')+'\n来源：'+(d.recommendation.assistant_source||'未提供')+' · 版本 '+(d.recommendation.version||'未提供'):'无建议');textBlock('证据来源',d.evidence.map(e=>e.id+' · '+e.source+'\n'+e.detail).join('\n'));
 const r=fb().drafts[PREFIX+d.id]||fb().annotations[PREFIX+d.id],saved=fb().annotations[PREFIX+d.id],userExcluded=r?.excluded_member_keys??saved?.excluded_member_keys??[],pendingForMatter=(fb().decision_migration_pending||[]).some(x=>x.candidate_decision_ids?.includes(d.id)),priorForAdoption=pendingForMatter?{decision_snapshot:snapshot(d),migration_state:'review_required'}:saved||r;if(userExcluded.length)textBlock('本次用户排除',userExcluded.join('、')+'；这些成员不随本事项处理');
 const status=el('p',saved?assessRecord(saved,data).reason:'尚未审核；方法选择不等于数量核准');status.id='workflow-status';status.setAttribute('role','status');editor.append(status);
 if(d.kind==='business_choice'){
  const quick=button('采用建议',()=>{try{const next=adopt(d,priorForAdoption);const m=document.getElementById('workflow-members');if(m)next.excluded_member_keys=[...m.querySelectorAll('input')].filter(e=>!e.checked).map(e=>e.dataset.memberKey);save(next);}catch(e){status.textContent=e.message;}});quick.id='workflow-adopt';const problem=recommendationProblem(d,priorForAdoption);quick.disabled=!!problem;quick.title=problem||'自动引用建议、版本、依据及助手来源';editor.append(quick);if(problem)editor.append(el('p',problem));
  const action=el('select');action.id='workflow-action';for(const a of ACTIONS.filter(x=>x!=='采用建议')){const o=el('option',a);o.value=a;action.append(o);}action.value=ACTIONS.includes(r?.action)&&r.action!=='采用建议'?r.action:draftAction;const al=el('label','审核动作 ');al.append(action);editor.append(al);
  const option=el('select');option.id='workflow-business-option';const empty=el('option','（选择本事项的具体做法）');empty.value='';option.append(empty);for(const opt of d.business_options){const o=el('option',opt.title);o.value=opt.id;option.append(o);}option.value=r?.business_option_id||'';const ol=el('label','具体业务做法 ');ol.append(option);editor.append(ol);
  const note=el('textarea');note.id='workflow-note';note.placeholder='调整或退回时只补充这一项必要说明';note.value=r?.user_note||'';editor.append(note);
  const members=el('details');members.id='workflow-members';members.append(el('summary','适用成员与个别排除'));for(const k of d.scope.member_keys){const cb=el('input');cb.type='checkbox';cb.dataset.memberKey=k;cb.checked=!userExcluded.includes(k)&&!d.scope.excluded_member_keys.includes(k);cb.disabled=d.scope.excluded_member_keys.includes(k);const label=el('label');label.append(cb,document.createTextNode(k));members.append(label);}editor.append(members);
  const manual=()=>{const a=action.value,n=note.value,excluded=[...members.querySelectorAll('input')].filter(e=>!e.checked).map(e=>e.dataset.memberKey);return {decision_snapshot:snapshot(d),action:a,business_option_id:option.value||null,confirmed_result:a==='待资料确认'?'待资料确认：'+(d.gaps.join('；')||'未列缺项，须补核'):a==='暂不处理'?'暂不处理':n,reason:a==='待资料确认'?d.gaps.join('；'):n,user_note:n,exception_of:d.exception_of??null,excluded_member_keys:excluded,entry_origin:'用户在统一事项编辑器保存',history:[]};};
  const stash=()=>{const key=PREFIX+d.id;fb().drafts[key]=cleanRecord({...manual(),updated_at:new Date().toISOString()},key,false);api.backup();status.textContent='已暂存草稿；尚未审核。';};action.onchange=()=>{draftAction=action.value;stash();};option.onchange=stash;note.oninput=stash;members.onchange=stash;
  const saveButton=button('保存处理',()=>{try{const next=manual();if(['调整处理','退回核查'].includes(next.action))ensure(next.user_note.trim(),'调整或退回须补充一项必要说明');save(next);}catch(e){status.textContent=e.message;}});saveButton.id='workflow-save';editor.append(saveButton);
 }else editor.append(el('p','本卡只读；资料缺口和分类须补齐，不能代替业务决定。'));
 const history=el('details');history.id='workflow-history';history.append(el('summary','历史、迁移来源与冲突候选'),el('pre',JSON.stringify({saved:saved||null,draft:fb().drafts[PREFIX+d.id]||null,legacy_sources:(fb().legacy_review_records||[]).filter(a=>a.candidate_decision_ids?.includes(d.id))},null,2)));editor.append(history);return true;
 }
 function save(next){const key=PREFIX+current;fb().annotations[key]=core.saveRecord(fb().annotations[key],next,key,new Date().toISOString());delete fb().drafts[key];api.backup();open(current);}
 function openTarget(kind,id){const matches=resolveRef(data,kind,id);if(matches.length===1)return open(matches[0]);show();current=null;delete editor.dataset.decisionId;editor.replaceChildren(el('p','没有唯一审核事项映射；待所属专业明确身份，不按同号或成员重叠合并。'));renderList();return false;}
 function focusTarget(kind,id){contextRef={kind,id};const drawing=['object','group'].includes(kind),holder=drawing?$('editor'):$('audit-form')?.parentElement;if(!holder)return;const ident=drawing?'workflow-context-link':'workflow-audit-link';let link=$(ident);if(!link){link=button('查看关联审核事项',()=>{});link.id=ident;holder.append(link);}const ids=resolveRef(data,kind,id);link.textContent=ids.length===1?'查看关联审核事项':'事项身份待核';link.onclick=()=>openTarget(kind,id);}
 const active={open,openTarget,focusTarget,hide,refresh(){renderList();if(current)open(current);},state:()=>({decision_id:current,visible:!panel.hidden}),getFeedback:fb};root.QtoReviewIdentity.active=active;
 const route=()=>{const p=new URLSearchParams(location.hash.replace(/^#/,''));if(p.has('decision'))open(p.get('decision'));};root.addEventListener('hashchange',route);renderList();route();if(!current&&data.review_workflow.default_tab==='decisions')open(firstPending()?.id);return active;
}
root.QtoReviewIdentity={PREFIX,ACTIONS,enabled,decisions,isRecord,snapshot,cleanRecord,assessRecord,recommendationProblem,resolveRef,migratePackage,mergeRecord,adopt,attach,same};
})(globalThis);
