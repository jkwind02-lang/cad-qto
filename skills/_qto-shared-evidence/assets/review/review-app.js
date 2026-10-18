(async()=>{
'use strict';
const $=id=>document.getElementById(id),originalHTML='<!doctype html>\n'+document.documentElement.outerHTML;
try{
 if(!('DecompressionStream' in globalThis))throw Error('请用支持本地解压的新版Edge或Chrome打开。');
 const bytes=Uint8Array.from(atob($('payload').textContent.replace(/\s/g,'')),c=>c.charCodeAt(0));
 const core=globalThis.QtoReviewCore;
 const data=await core.readPayload(new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()));
 let feedback=core.parse(JSON.parse($('feedback-seed').textContent),data),storageOK=true,cacheProblem='';
 const storageKey='qto-review-2-'+data.source_sha256;
 try{const cached=localStorage.getItem(storageKey);if(cached)feedback=core.mergePackages(feedback,core.parse(cached,data));}catch(e){storageOK=false;cacheProblem=e.message;}
 if(data.delivery){globalThis.QtoReviewDelivery.attach({data,core,feedback,originalHTML,storageKey});$('loading').remove();return;}
 const scenes=new Map(data.scenes.map(s=>[s.id,s])),objects=new Map(data.objects.map(o=>[o.catalog_key,o]));
 const backgrounds=new Map(data.scenes.map(s=>[s.id,[...(s.background_refs||[]).flatMap(k=>data.background_sets[k]),...s.background]])),boundsCache=new WeakMap();
 function pointBounds(points){const b=[Infinity,Infinity,-Infinity,-Infinity];for(const p of points){b[0]=Math.min(b[0],p[0]);b[1]=Math.min(b[1],p[1]);b[2]=Math.max(b[2],p[0]);b[3]=Math.max(b[3],p[1]);}return b;}
 // A line may carry several parts (scene mapping contract 13); points stays its first part for older pages.
 const partsOf=o=>o.kind==='line'&&o.parts?.length?o.parts:[o.points],allPts=o=>partsOf(o).flat();
 function within(r,view){let b=boundsCache.get(r);if(!b){b=pointBounds(allPts(r));boundsCache.set(r,b);}return b[0]<=view[2]&&b[2]>=view[0]&&b[1]<=view[3]&&b[3]>=view[1];}
 function installMarkers(){for(const m of feedback.custom_markers){if(!objects.has(m.catalog_key)){data.objects.push(m);objects.set(m.catalog_key,m);}}}
 installMarkers();
 // One physical object carried by several packages (scene mapping contract 6): point objects with the same scene and id whose first
 // points lie within the unit tolerance share one marker; same id farther apart stays separate and is flagged in the details.
 const TWIN_TOL={mm:1,cm:.1,m:.001};
 function contextValue(v){
  if(v===null||v===undefined)return ['null'];
  if(Array.isArray(v))return ['array',v.map(contextValue)];
  if(typeof v==='object')return ['object',Object.keys(v).sort().map(k=>[k,contextValue(v[k])])];
  if(typeof v==='number'){const b=new ArrayBuffer(8);new DataView(b).setFloat64(0,v===0?0:v,false);return ['number',Array.from(new Uint8Array(b),x=>x.toString(16).padStart(2,'0')).join('')];}
  return [typeof v,v];
 }
 const objectContext=o=>['object_type','version','floor','scope_id'].map(k=>contextValue(o.attributes?.[k]??null));
 function twinIndex(){
  const byId=new Map(),out=new Map();
  for(const o of data.objects){if(o.kind!=='point'||!o.scene_id||!o.points?.length)continue;const k=JSON.stringify([o.scene_id,o.id,...objectContext(o)]);if(!byId.has(k))byId.set(k,[]);byId.get(k).push(o);}
  for(const os of byId.values()){
   if(os.length<2)continue;const tol=TWIN_TOL[scenes.get(os[0].scene_id)?.coordinate_unit]??0,clusters=[];
   for(const o of os){const p=o.points[0],c=clusters.find(c=>Math.hypot(c[0].points[0][0]-p[0],c[0].points[0][1]-p[1])<=tol);c?c.push(o):clusters.push([o]);}
   for(const c of clusters)for(const o of c)out.set(o.catalog_key,{same:c,apart:clusters.filter(x=>x!==c).flat()});
  }
  return out;
 }
 const twins=twinIndex();
 let mode=data.groups.length?'groups':'objects',selected=null,focused=null,checked=new Set(),items=[],page=0,memberPage=0;
 let scene=data.scenes[0],cx=0,cy=0,scale=1,cw=0,ch=0,drag=null,moved=0,placing=false,wide=false,external=null,marks=[],measuring=false,mpts=[];
 const PAGE=60,MEMBER_PAGE=80,cv=$('cad'),ctx=cv.getContext('2d'),images=new Map();
 const key=i=>i.member_keys?'问题组|'+i.id:i.annotation_key||i.catalog_key;
 const members=g=>(g?.member_keys||[]).map(k=>objects.get(k)).filter(Boolean);
 const groupProfessional=g=>Array.from(new Set(members(g).map(o=>o.professional)));
 const qtyText=o=>(o.quantities||[]).map(q=>q.name+' '+(q.value===null?'待定':q.value)+' '+q.unit+'（'+q.level+'）').join('；');
 const midpoint=o=>o.points?.length?o.points.reduce((a,p)=>[a[0]+p[0]/o.points.length,a[1]+p[1]/o.points.length],[0,0]):[(scene.bounds[0]+scene.bounds[2])/2,(scene.bounds[1]+scene.bounds[3])/2];
 const sign=()=>scene.y_axis==='down'?1:-1;
 const screen=p=>[(p[0]-cx)*scale+cw/2,(p[1]-cy)*scale*sign()+ch/2];
 const world=p=>[(p[0]-cw/2)/scale+cx,(p[1]-ch/2)/scale*sign()+cy];
 const visible=o=>!!scene&&o.kind!=='unlocated'&&o.scene_id===scene.id;
 function catalogue(){return Array.from(new Map(data.objects.map(o=>[JSON.stringify([o.professional,o.category,o.id,...objectContext(o)]),o])).values());}
 function source(){return mode==='groups'?data.groups:catalogue();}
 function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
 function populateCategories(preferred){
  const prof=$('professional').value,counts=Object.create(null);
  for(const o of source())if(prof==='全部'||(o.member_keys?groupProfessional(o).includes(prof):o.professional===prof))counts[o.category]=(counts[o.category]||0)+1;
  $('category').replaceChildren();option($('category'),'全部','全部状态');
  Object.entries(counts).forEach(([c,n])=>option($('category'),c,c+' · '+n));
  $('category').value=preferred&&counts[preferred]?preferred:'全部';
 }
 function list(reset=true){
  if(reset){page=0;selected=null;focused=null;syncUnlocated();checked.clear();$('members-panel').hidden=true;$('current-adoption').hidden=true;$('save-review').disabled=true;}
  const prof=$('professional').value,cat=$('category').value,q=$('search').value.trim().toLowerCase();
  items=source().filter(o=>(prof==='全部'||(o.member_keys?groupProfessional(o).includes(prof):o.professional===prof))&&(cat==='全部'||o.category===cat)&&(!q||JSON.stringify(o).toLowerCase().includes(q)));
  page=Math.min(page,Math.max(0,Math.ceil(items.length/PAGE)-1));$('list').replaceChildren();
  for(const i of items.slice(page*PAGE,(page+1)*PAGE)){
   const b=document.createElement('button');b.className='item';b.dataset.key=i.id;b.dataset.selectionKey=key(i);
   const strong=document.createElement('strong');strong.textContent=i.title;
   const sub=document.createElement('small');sub.textContent=i.category+' · '+i.status+(i.member_keys?' · '+i.member_keys.length+'处':qtyText(i)?' · '+qtyText(i):'');
   b.append(strong,sub);b.onclick=()=>select(i);$('list').append(b);
  }
  $('counter').textContent='筛选 '+items.length+(mode==='groups'?' 个问题组':' 条对象记录')+'；完整数据保留，分专业和单位查看。';
  $('list-page').textContent=(page+1)+' / '+Math.max(1,Math.ceil(items.length/PAGE));$('list-prev').disabled=page===0;$('list-next').disabled=(page+1)*PAGE>=items.length;draw();
 }
 function select(i){
  selected=i;focused=null;syncUnlocated();memberPage=0;document.querySelectorAll('.item').forEach(b=>b.classList.toggle('active',b.dataset.selectionKey===key(i)));
  const note=feedback.drafts[key(i)]||feedback.annotations[key(i)];
  checked=new Set(i.member_keys?(note?.selected_object_keys||i.member_keys).filter(k=>i.member_keys.includes(k)):[]);
  $('members-panel').hidden=!i.member_keys;if(i.member_keys)renderMembers();reviewShow(i);
  if(i.member_keys){if(members(i).length)focus(members(i)[0]);}else focus(i);
 }
 function renderMembers(){
  const ms=members(selected);memberPage=Math.min(memberPage,Math.max(0,Math.ceil(ms.length/MEMBER_PAGE)-1));$('member-list').replaceChildren();
  for(const i of ms.slice(memberPage*MEMBER_PAGE,(memberPage+1)*MEMBER_PAGE)){
   const row=document.createElement('div');row.className='member-row';row.dataset.key=i.catalog_key;
   const cb=document.createElement('input');cb.type='checkbox';cb.disabled=!!data.review_workflow?.enabled;cb.checked=checked.has(i.catalog_key);cb.setAttribute('aria-label','纳入 '+i.title);
   cb.onchange=()=>{cb.checked?checked.add(i.catalog_key):checked.delete(i.catalog_key);groupState();stash();draw();};
   const b=document.createElement('button');b.textContent=i.id+' · '+i.title;
   const small=document.createElement('small');small.textContent=(scenes.get(i.scene_id)?.title||'未定位')+' · '+qtyText(i);b.append(small);b.onclick=()=>focus(i);row.append(cb,b);$('member-list').append(row);
  }
  $('members-page').textContent=(memberPage+1)+' / '+Math.max(1,Math.ceil(ms.length/MEMBER_PAGE));$('members-prev').disabled=memberPage===0;$('members-next').disabled=(memberPage+1)*MEMBER_PAGE>=ms.length;groupState();
 }
 function groupState(){
  if(!selected?.member_keys)return;
  $('selected-count').textContent=checked.size+' / '+selected.member_keys.length+'处';$('group-guidance').hidden=false;
  const count=new Set(members(selected).map(o=>o.scene_id).filter(Boolean)).size;
  $('group-guidance').textContent='已勾选 '+checked.size+' / '+selected.member_keys.length+'处，涉及 '+count+' 张图页。\n'+selected.reason+'\n'+(selected.caution||'各成员规格、单位和原值分别保留；此处保存意见不会自动覆盖数量。');
  $('save-review').textContent='保存本组 '+checked.size+' 处批注';$('save-review').disabled=!checked.size;
 }
 function reviewShow(i){
  if(globalThis.QtoReviewIdentity?.active){globalThis.QtoReviewIdentity.active.focusTarget(i.member_keys?'group':'object',i.member_keys?i.id:i.catalog_key);return;}
  const r=feedback.drafts[key(i)]||feedback.annotations[key(i)]||{};
  $('editor-title').textContent=(i.member_keys?'整组复核：':'对象复核：')+i.title;
  $('review-result').value=r.confirmed_result||'';$('review-result').placeholder=i.member_keys?'待判断建议：'+i.proposal:'填写修正结果，或说明需要继续核对的问题';
  $('review-reason').value=r.reason||'';$('review-evidence').value=r.evidence_note||'';
  $('review-instruction').value=r.instruction||'请助手复核';$('review-scope').disabled=!!i.member_keys;$('review-scope').value=i.member_keys?'本组勾选对象':r.scope||'仅所选对象';
  $('original').hidden=!r.original_message;$('original').textContent='原话：\n'+(r.original_message||'');
  $('history-content').textContent=(r.history||[]).map((h,n)=>(n+1)+'. '+(h.updated_at||'')+'\n'+(h.confirmed_result||'')+'\n原因：'+(h.reason||'')).join('\n\n')||'暂无修改历史';
  $('entry-status').textContent=r.applicability_state==='conflict'?'跨入口意见冲突，动作暂挂：'+r.applicability_reason:(r.applicability_state==='stale'||r.applicability_state==='unmatched')?'历史意见待重新核对：'+r.applicability_reason:feedback.drafts[key(i)]?'当前有草稿，未保存为结论。':feedback.annotations[key(i)]?'已有保存意见；实际应用状态见回执。':'尚未保存意见；建议不自动当作你的确认。';
  if(i.member_keys)groupState();else{$('group-guidance').hidden=true;$('save-review').textContent='保存本对象批注';$('save-review').disabled=false;}
 }
 function showFacts(i){
  $('object-facts').replaceChildren();const heading=document.createElement('strong');heading.textContent=i.title;$('object-facts').append(heading);
  if(i.quantities?.length){const table=document.createElement('table');table.className='qty';
   const head=document.createElement('tr');for(const label of ['数量项','数值','单位','层级']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
   for(const q of i.quantities){const tr=document.createElement('tr');for(const v of [q.name,q.value===null?'待定':q.value,q.unit,q.level]){const td=document.createElement('td');td.textContent=String(v);tr.append(td);}table.append(tr);}$('object-facts').append(table);
  }
  $('detail').textContent='对象：'+i.id+'\n专业：'+data.professional_labels[i.professional]+'\n图页：'+(scenes.get(i.scene_id)?.title||'未定位')+'\n状态：'+i.status+'\n'+i.detail+'\n来源：'+i.source+'\n属性：'+JSON.stringify(i.attributes||{},null,2);
  if(data.legacy_records?.[i.catalog_key])$('detail').textContent+='\n兼容迁移的完整原记录：'+JSON.stringify(data.legacy_records[i.catalog_key],null,2);
  const tw=twins.get(i.catalog_key);if(tw)$('object-facts').append(twinBox(i,tw));
  const drifts=i.attributes?.suspected_number_drifts||[],cross=i.attributes?.cross_drawing_candidates||[];
  if(drifts.length||cross.length){
   const box=document.createElement('div');box.className='twins';box.dataset.role='number-drift';
   const heading=document.createElement('strong');heading.textContent='跨图/编号候选：仅提示，未合并、未扣量';box.append(heading);
   for(const c of drifts){
    const line=document.createElement('div');line.textContent='疑似编号漂移：'+c.object_ids.join(' ↔ ')+(c.context_conflicts?.length?'；上下文冲突：'+c.context_conflicts.join('、'):'')+(c.missing_context?.length?'；待补：'+c.missing_context.join('、'):'');
    for(const k of c.records||[]){const o=objects.get(k);if(!o||o.catalog_key===i.catalog_key)continue;const b=document.createElement('button');b.className='link';b.textContent='查看 '+o.id;b.onclick=()=>selected?.member_keys||external?focus(o):select(o);line.append(b);}box.append(line);
   }
   for(const c of cross){const line=document.createElement('div');line.textContent=(c.suspected_number_drift?'疑似编号漂移：':'几何对应候选：')+c.object_ids.join(' ↔ ')+'；距离 '+c.distance_m+' m'+(c.context_conflicts?.length?'；上下文冲突：'+c.context_conflicts.join('、'):'')+(c.missing_context?.length?'；待补：'+c.missing_context.join('、'):'');box.append(line);}
   $('object-facts').append(box);
  }
  const rc=data.receipts[key(selected)],saved=feedback.annotations[key(selected)],state=core.receiptState(rc,saved),box=$('current-adoption');box.hidden=!rc&&!i.adoption_result;
  box.textContent=(i.adoption_result?'当前采用：'+i.adoption_result+'\n'+(i.adoption_reason||'')+'\n'+(i.adoption_note||''):'')+(rc?'\n处理回执：'+rc.status+'\n'+rc.assistant_response+(state==='stale'?'\n意见或成果版本已变化，旧回执不能证明当前结果已复核。':''):'');
  if(rc?.member_results){const row=rc.member_results.find(r=>r.object_id===i.id||r['对象编号']===i.id);if(row)box.textContent+='\n本成员处理：'+JSON.stringify(row,null,2);}
 }
 // Records of the same object: which carry quantities and which are references; two with quantities may count it twice.
 function twinBox(i,tw){
  const box=document.createElement('div'),span=t=>{const s=document.createElement('span');s.textContent=t;return s;};box.className='twins';
  const rows=(list,lead)=>{box.append(span(lead));list.forEach((o,n)=>{if(n)box.append(span('；'));const t=(data.professional_labels[o.professional]||o.professional)+' · '+o.catalog_key+' · '+(o.quantities?.length?'带数量 '+qtyText(o):'参照');
   if(o===i){box.append(span(t+'（当前）'));return;}const b=document.createElement('button');b.className='link';b.textContent=t;b.title='切换到这条记录';b.onclick=()=>selected?.member_keys||external?focus(o):select(o);box.append(b);});};
  const counted=tw.same.filter(o=>o.quantities?.length).length;
  if(tw.same.length>1)rows(tw.same,'同一实物 '+tw.same.length+' 条记录，图上只画一个点'+(counted>1?'；其中 '+counted+' 条带数量，可能跨包重复计量，请核对':'')+'：');
  if(tw.apart.length){if(tw.same.length>1)box.append(document.createElement('br'));rows(tw.apart,'注意：同编号另有 '+tw.apart.length+' 条记录坐标不一致，各画各的点，请核对坐标：');}
  return box;
 }
 function changeScene(id,fitPage=true){if(!scenes.has(id))return;const swap=scene!==scenes.get(id);scene=scenes.get(id);$('scene').value=id;if(swap){syncUnlocated(null);marks=[];mpts=[];renderSymbols();if(measuring)status(measureText());}if(fitPage)fit();draw();}
 // What this page cannot show is counted per scene; the reviewer must go back to the original for those (scene mapping contract 7.4).
 const NOT_SHOWN={other_layers:'大块里未画的图层',pattern_hatch:'填充图案',image:'图片',ole:'OLE 对象',proxy:'代理对象',xref_missing:'缺外部参照'};
 function restorationText(s){const r=s?.restoration;if(!r)return '';if(r.source==='neutral')return ' · '+(r.note||'底图只有模型空间顶层图元');if(!r.not_shown_total)return ' · 底图已按原图还原';const top=(r.other_layers_top||[]).slice(0,3).map(e=>e[0]).join('、');return ' · 本页看不出 '+r.not_shown_total+' 处，须回原图（'+Object.entries(r.not_shown||{}).filter(e=>e[1]).map(e=>(NOT_SHOWN[e[0]]||e[0])+' '+e[1]+(e[0]==='other_layers'&&top?'，多为 '+top:'')).join('；')+'）';}
 // Symbol reconciliation (contract 7.5): one row per block; "未计" and "同位置" ring the points on the map. Never changes quantities.
 // The panel folds away so it does not cover the rings; the toolbar button opens it again (closing it there clears the rings).
 function showMarks(pts,what){marks=pts||[];if(!marks.length){draw();return;}$('symbols-panel').hidden=true;$('symbols').classList.remove('on');const b=pointBounds(marks),pad=Math.max((scene.bounds[2]-scene.bounds[0])*.03,1);fitBounds([b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad]);status(what+'：'+marks.length+' 处，已在图上标红圈。');}
 function renderSymbols(){
  const rows=scene?.symbol_reconciliation||[],ib=scene?.symbol_in_block,panel=$('symbols-panel');$('symbols').hidden=!rows.length&&!ib;panel.replaceChildren();
  if(!rows.length&&!ib){panel.hidden=true;$('symbols').classList.remove('on');return;}
  const total=k=>rows.reduce((a,r)=>a+(r[k]||0),0),head=document.createElement('div');
  // Explained symbols (scene mapping contract 7.5.1) get their own columns and never count as uncounted or same position.
  const NOTES=[['removed_overlap','已剔除'],['counted_elsewhere','他图计入'],['same_item_elsewhere','同一实物另一图']],explained=rows.some(r=>'removed_overlap' in r);
  // Symbols on hidden layers (off, frozen, not plotted) are not drawn and stay out of the sheet count (contract 7.5).
  const hid=rows.some(r=>r.hidden),hc=total('hidden_counted');
  head.textContent='符号对账（只对账，不改数量）：本页 '+total('on_sheet')+' 个，已计 '+total('counted')+'，未计 '+total('uncounted')+'，同位置 '+total('duplicates')+(explained?'；已说明：'+NOTES.map(([k,t])=>t+' '+total(k)).join('、'):'')+(hid?'；隐藏层符号 '+total('hidden')+' 个不计入本页'+(hc?'，其中 '+hc+' 个是计数对象，图上看不到，须回原图':''):'');
  const table=document.createElement('table'),tr=document.createElement('tr');
  for(const h of ['符号','图层','本页','已计','未计','同位置',...(explained?NOTES.map(n=>n[1]):[]),...(hid?['隐藏层']:[])]){const th=document.createElement('th');th.textContent=h;tr.append(th);}table.append(tr);
  for(const r of rows){
   const row=document.createElement('tr'),name=(r.label||r.block)+(r.sheet?'（'+r.sheet+'）':'');
   for(const [v,pts,what,why] of [[name],[r.layer||''],[r.on_sheet],[r.counted],[r.uncounted,r.uncounted_points,'未计'],[r.duplicates,r.duplicate_points,'同位置'],...(explained?NOTES.map(([k,t])=>[r[k],r[k+'_points'],t,(r.notes?.[k]||[]).join('；')]):[]),...(hid?[[r.hidden||0,r.hidden_points,'隐藏层符号',r.hidden_counted?'其中 '+r.hidden_counted+' 个是计数对象':'']]:[])]){
    const td=document.createElement('td');
    if(pts&&v){const b=document.createElement('button');b.className='link';b.textContent=v;b.title='在图上标出'+what+'的位置'+(why?'。说明：'+why:'');b.onclick=()=>showMarks(pts,name+' '+what+(why?'（'+why+'）':''));td.append(b);}else td.textContent=v??'';
    row.append(td);
   }
   row.children[0].title='块名 '+r.block;table.append(row);
  }
  if(rows.length)panel.append(head,table);
  if(ib)panel.append(inBlock(ib));
 }
 // Symbols drawn inside floor blocks and the like are not reconciled yet (contract 7.5): show how many, so the panel never reads all zero.
 function inBlock(ib){
  const d=document.createElement('div'),t=s=>{const x=document.createElement('span');x.textContent=s;return x;};d.append(t('块内 '+ib.total+' 个符号未参与对账（画在楼层块等块里）：'));
  ib.blocks.forEach((b,i)=>{const btn=document.createElement('button');btn.className='link';btn.textContent=b.block+(b.sheet?'（'+b.sheet+'）':'')+' '+b.count;btn.title='在图上标出块内 '+b.block+' 的位置';btn.onclick=()=>showMarks(b.points,'块内未对账 '+b.block);if(i)d.append(t('、'));d.append(btn);});
  d.append(t('。已计、未计要回原图或看覆盖台账。'));return d;
 }
 $('symbols').onclick=()=>{const p=$('symbols-panel');p.hidden=!p.hidden;$('symbols').classList.toggle('on',!p.hidden);if(p.hidden){marks=[];draw();}};
 function syncUnlocated(obj=focused){
  const note=$('unlocated'),show=obj?.kind==='unlocated';note.hidden=!show;
  const why=show?core.unlocatedText(obj):'';note.textContent=show?'本对象尚无可靠图面定位'+(why?'（'+why+'）':'')+'。保留数量、来源及文字批注，不用虚构坐标代替回图。':'';
 }
 function focus(i){
  focused=i;if(i.scene_id)changeScene(i.scene_id,false);showFacts(i);
  syncUnlocated();
  if(i.kind==='unlocated')fit();else{const pts=[...allPts(i),...(i.holes||[]).flat()],xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);const min=(scene.bounds[2]-scene.bounds[0])*.04;fitBounds([Math.min(...xs)-min,Math.min(...ys)-min,Math.max(...xs)+min,Math.max(...ys)+min]);}
  const ms=selected?.member_keys?members(selected):[i],index=ms.indexOf(i);
  $('member-position').textContent=(selected?.member_keys?'第 '+(index+1)+' / '+ms.length+'处 · ':'')+i.id+' · '+(scenes.get(i.scene_id)?.title||'未定位');
  if(selected?.member_keys&&Math.floor(index/MEMBER_PAGE)!==memberPage){memberPage=Math.floor(index/MEMBER_PAGE);renderMembers();}
  document.querySelectorAll('.member-row').forEach(row=>row.classList.toggle('focus',row.dataset.key===i.catalog_key));draw();
 }
 function fitBounds(b){cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2;scale=Math.min(Math.max(1,cw-55)/Math.max(1e-9,b[2]-b[0]),Math.max(1,ch-55)/Math.max(1e-9,b[3]-b[1]));draw();}
 function fit(){if(scene)fitBounds(scene.bounds);}
 function groupFit(){const pts=members(selected).filter(visible).flatMap(allPts);if(!pts.length){fit();return;}const pad=(scene.bounds[2]-scene.bounds[0])*.015;fitBounds([Math.min(...pts.map(p=>p[0]))-pad,Math.min(...pts.map(p=>p[1]))-pad,Math.max(...pts.map(p=>p[0]))+pad,Math.max(...pts.map(p=>p[1]))+pad]);}
 function resize(){const b=cv.getBoundingClientRect();cw=b.width;ch=b.height;cv.width=Math.round(cw*devicePixelRatio);cv.height=Math.round(ch*devicePixelRatio);draw();}
 function mapItems(){return external?external:selected?.member_keys?members(selected):mode==='objects'?items:selected?[selected]:[];}
 // Comparison modes highlight an item's member objects (contract 5.1/8); returns false when the object has no drawing position.
 function locate(list,obj){external=list;focused=obj||null;syncUnlocated();if(!obj||obj.kind==='unlocated'||!scenes.has(obj.scene_id)){draw();return false;}changeScene(obj.scene_id,false);const pts=allPts(obj),min=(scene.bounds[2]-scene.bounds[0])*.04;fitBounds([Math.min(...pts.map(p=>p[0]))-min,Math.min(...pts.map(p=>p[1]))-min,Math.max(...pts.map(p=>p[0]))+min,Math.max(...pts.map(p=>p[1]))+min]);return true;}
 function path(points,close=false){ctx.beginPath();points.forEach((p,n)=>n?ctx.lineTo(...screen(p)):ctx.moveTo(...screen(p)));if(close)ctx.closePath();}
 function draw(){
  if(!cw||!ch||!scene)return;ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);ctx.fillStyle='#fffefb';ctx.fillRect(0,0,cw,ch);
  if(scene.raster_data_uri){let im=images.get(scene.id);if(!im){im=new Image();images.set(scene.id,im);im.onload=draw;im.src=scene.raster_data_uri;}if(im.complete&&im.naturalWidth){const a=screen([scene.bounds[0],scene.y_axis==='down'?scene.bounds[1]:scene.bounds[3]]),b=screen([scene.bounds[2],scene.y_axis==='down'?scene.bounds[3]:scene.bounds[1]]);ctx.drawImage(im,a[0],a[1],b[0]-a[0],b[1]-a[1]);}}
  const view=pointBounds([world([0,0]),world([cw,ch])]);
  for(const r of backgrounds.get(scene.id)){
   // Text with a drawing height scales with zoom like the original (scene mapping contract 7.3); too small to read is skipped.
   if(r.kind==='text'){
    const fs=r.height?r.height*scale*1.25:10,axes=r.text_axes;
    const vertical=axes?Math.hypot(...axes[1]):1,stretch=axes?Math.max(vertical,Math.hypot(...axes[0])):Math.max(1,r.width_factor||1);
    if(r.height&&fs*vertical<2.5)continue;
    const p=screen(r.point),m=(r.height?fs*stretch*(r.text||'').length:150*stretch)+150;
    if(p[0]<-m||p[0]>cw+m||p[1]<-m||p[1]>ch+m)continue;
    ctx.save();ctx.translate(...p);
    if(axes){const [[bx,by],[ux,uy]]=axes;ctx.transform(bx,sign()*by,-ux,-sign()*uy,0,0);}
    else{ctx.rotate((r.rotation||0)*Math.PI/180*sign());if(r.width_factor)ctx.scale(r.width_factor,1);}
    ctx.font=fs+'px Microsoft YaHei';ctx.textAlign=r.align||'left';ctx.textBaseline=r.baseline||'alphabetic';ctx.fillStyle=r.color||'#7c909c';ctx.fillText(r.text,0,0);ctx.restore();
   }
   else{if(!within(r,view))continue;path(r.points,r.kind==='polygon');if(r.fill){ctx.fillStyle=r.fill;ctx.fill();}ctx.strokeStyle=r.color||'#b4c1ca';ctx.lineWidth=r.width||.7;const dl=r.dash?r.dash.reduce((a,b)=>a+b,0)*scale:0;if(dl>=4)ctx.setLineDash(r.dash.map(v=>Math.max(1,v*scale)));ctx.stroke();if(dl>=4)ctx.setLineDash([]);}
  }
  const drawn=new Set();
  for(const i of mapItems()){
   if(!visible(i)||!within(i,view))continue;const tw=twins.get(i.catalog_key)?.same;if(tw?.length>1){if(drawn.has(tw))continue;drawn.add(tw);}
   const same=tw?.length>1?tw:[i],active=same.includes(focused),allowed=!selected?.member_keys||same.some(o=>checked.has(o.catalog_key)),color=!allowed?'#b6bec5':i.color||(/排除|暂缓|不计/.test(i.status)?'#8f9ca5':active?'#d36b17':'#237ba1');
   ctx.strokeStyle=color;ctx.lineWidth=active?4:2;
   if(i.kind==='polygon'){path(i.points,true);for(const h of i.holes||[]){ctx.moveTo(...screen(h[0]));h.slice(1).forEach(p=>ctx.lineTo(...screen(p)));ctx.closePath();}ctx.fillStyle=allowed?'#2d91b92a':'#bac2c72a';ctx.fill('evenodd');ctx.stroke();}
   else if(i.kind==='line'){for(const q of partsOf(i)){path(q);ctx.stroke();}}
   const p=screen(midpoint(i));if(p[0]<-30||p[0]>cw+30||p[1]<-30||p[1]>ch+30)continue;ctx.beginPath();ctx.arc(...p,active?7:4,0,2*Math.PI);ctx.fillStyle='white';ctx.fill();ctx.stroke();
   if(active){ctx.font='bold 12px Microsoft YaHei';const label=i.id+(same.length>1?' · '+same.length+' 条记录':''),width=ctx.measureText(label).width,x=Math.max(5,Math.min(cw-width-10,p[0]+12)),y=Math.max(20,p[1]-12);ctx.fillStyle='white';ctx.fillRect(x-3,y-14,width+8,20);ctx.fillStyle=color;ctx.fillText(label,x,y);}
  }
  for(const q of marks){const p=screen(q);if(p[0]<-20||p[0]>cw+20||p[1]<-20||p[1]>ch+20)continue;ctx.beginPath();ctx.arc(...p,11,0,2*Math.PI);ctx.strokeStyle='#d21f1f';ctx.lineWidth=2.5;ctx.stroke();}
  if(mpts.length){const f=lengthFactor();ctx.strokeStyle='#c2187a';ctx.lineWidth=2;ctx.setLineDash([6,4]);path(mpts);ctx.stroke();ctx.setLineDash([]);ctx.font='bold 12px Microsoft YaHei';mpts.forEach((q,n)=>{const s=screen(q);ctx.fillStyle='#c2187a';ctx.fillRect(s[0]-3,s[1]-3,6,6);if(!n)return;const a=screen(mpts[n-1]),d=Math.hypot(q[0]-mpts[n-1][0],q[1]-mpts[n-1][1]),t=f?(d*f).toFixed(3)+' m':num(d)+' '+scene.coordinate_unit,w=ctx.measureText(t).width,x=(a[0]+s[0])/2+6,y=(a[1]+s[1])/2-6;ctx.fillStyle='white';ctx.fillRect(x-2,y-13,w+4,17);ctx.fillStyle='#c2187a';ctx.fillText(t,x,y);});}
  const target=100/scale,power=10**Math.floor(Math.log10(Math.max(target,1e-12))),L=[1,2,5,10].map(n=>n*power).find(n=>n>=target)||power*10;
  ctx.strokeStyle='#35556b';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(cw-20-L*scale,ch-22);ctx.lineTo(cw-20,ch-22);ctx.stroke();ctx.font='11px Microsoft YaHei';ctx.fillStyle='#35556b';ctx.fillText(Number(L.toPrecision(4))+' '+scene.coordinate_unit,cw-20-L*scale,ch-30);
  $('mapstatus').textContent=scene.title+' · 坐标单位 '+scene.coordinate_unit+(selected?.member_keys?' · 勾选 '+checked.size+'/'+selected.member_keys.length+'处':'')+restorationText(scene);
 }
 function form(){
  const old=feedback.annotations[key(selected)]||{},isGroup=!!selected.member_keys;
  const out={...old,result_fingerprint:selected.result_fingerprint,applicability_state:'current',applicability_reason:'用户本轮重新保存意见',object_id:selected.id,object_ids:isGroup?Array.from(new Set(selected.member_keys.filter(k=>checked.has(k)).map(k=>objects.get(k).id))):[selected.id],category:selected.category,title:selected.title,
   confirmed_result:$('review-result').value,reason:$('review-reason').value,instruction:$('review-instruction').value,scope:isGroup?'本组勾选对象':$('review-scope').value,evidence_note:$('review-evidence').value,original_message:old.original_message||'',entry_origin:'页面草稿，未保存',updated_at:new Date().toISOString()};
  if(isGroup)Object.assign(out,{group_id:selected.id,selected_object_keys:selected.member_keys.filter(k=>checked.has(k)),excluded_object_keys:selected.member_keys.filter(k=>!checked.has(k)),member_snapshot:selected.member_snapshot,group_revision:data.revision});
  else out.object_snapshot={catalog_key:selected.catalog_key,object_id:selected.id,professional:selected.professional,scene_id:selected.scene_id,quantities:selected.quantities,attributes:selected.attributes,result_fingerprint:selected.result_fingerprint};
  return out;
 }
 function status(extra=''){
  $('review-status').textContent=Object.keys(feedback.annotations).length+'条已保存 · '+Object.keys(feedback.drafts).length+'条草稿';
  const message=[extra,storageOK?'':'本机暂存不可用，请及时保存带批注HTML或导出JSON。',cacheProblem?'暂存提示：'+cacheProblem:''].filter(Boolean).join(' ');
  $('action-message').textContent=message;$('action-message').hidden=!message;
 }
 function backup(){if(data.review_workflow?.enabled)feedback=core.parse(feedback,data);core.holdDecisionConflicts(feedback,data);feedback.drawing_revision=data.revision;feedback.current_result_fingerprint=data.result_fingerprint;feedback.exported_at=new Date().toISOString();try{localStorage.setItem(storageKey,JSON.stringify(feedback));storageOK=true;}catch(e){storageOK=false;}status(feedback.decision_conflicts?.length?'跨入口意见冲突，相关动作已暂挂；请所属专业核对决策身份。':'');}
 function stash(){if(!selected)return;feedback.drafts[key(selected)]=core.cleanRecord(form(),key(selected),false);backup();$('entry-status').textContent='已暂存草稿；尚未保存为意见。';}
 for(const id of ['review-result','review-reason','review-evidence','review-scope','review-instruction'])$(id).oninput=stash;
 $('save-review').onclick=()=>{if(!selected)return;try{const k=key(selected);feedback.annotations[k]=core.saveRecord(feedback.annotations[k],form(),k,new Date().toISOString());delete feedback.drafts[k];backup();reviewShow(selected);if(focused)showFacts(focused);$('entry-status').textContent='意见已保存；专业复算后才更新实际数量。';}catch(e){$('entry-status').textContent=e.message;}};
 function download(name,type,content){const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const stamp=()=>new Date().toISOString().slice(0,19).replace(/[:T]/g,'-'),filename=()=>data.title.replace(/[<>:"/\\|?*]/g,'_');
 const saveLocalHTML=prepared=>{let file=prepared;if(!file){backup();file={filename:filename()+'_带批注_'+stamp()+'.html',html:core.embed(originalHTML,feedback,data)};}download(file.filename,'text/html;charset=utf-8',file.html);return file;};
 $('save-html').onclick=()=>saveLocalHTML();
 globalThis.QtoProblemFeedback?.attach({data,getFeedback:()=>feedback,saveLocal:saveLocalHTML});
 $('export-feedback').onclick=()=>{backup();download(filename()+'_批注_'+stamp()+'.json','application/json;charset=utf-8',JSON.stringify(feedback,null,2));};
 $('import-feedback').onclick=()=>$('import-file').click();$('import-file').onchange=async()=>{const f=$('import-file').files[0];if(!f)return;try{const incoming=core.parse(await core.readFileTextAsync(await f.text()),data);feedback=core.mergePackages(feedback,incoming);installMarkers();backup();populateCategories();list();status('导入完成；重复意见保留修改历史。');}catch(e){status('导入未应用：'+e.message);}finally{$('import-file').value='';}};
 function setMode(m){mode=m;$('mode-groups').classList.toggle('on',m==='groups');$('mode-objects').classList.toggle('on',m==='objects');populateCategories(m==='groups'?data.default_category:null);list();if(items.length)select(items[0]);}
 $('mode-groups').onclick=()=>setMode('groups');$('mode-objects').onclick=()=>setMode('objects');$('professional').onchange=()=>{populateCategories();list();if(items.length)select(items[0]);};$('category').onchange=()=>{list();if(items.length)select(items[0]);};$('search').oninput=()=>list();
 $('list-prev').onclick=()=>{page--;list(false);};$('list-next').onclick=()=>{page++;list(false);};$('members-prev').onclick=()=>{memberPage--;renderMembers();};$('members-next').onclick=()=>{memberPage++;renderMembers();};
 $('member-all').onclick=()=>{if(selected?.member_keys){checked=new Set(selected.member_keys);renderMembers();stash();draw();}};$('member-none').onclick=()=>{if(selected?.member_keys){checked.clear();renderMembers();stash();draw();}};
 function step(delta){if(!selected?.member_keys)return;const ms=members(selected);focus(ms[(Math.max(0,ms.indexOf(focused))+delta+ms.length)%ms.length]);}
 $('member-prev').onclick=()=>step(-1);$('member-next').onclick=()=>step(1);$('member-single').onclick=()=>{if(!focused)return;const obj=focused;$('search').value='';setMode('objects');select(obj);};
 $('scene').onchange=()=>changeScene($('scene').value);$('fit').onclick=fit;$('group-fit').onclick=groupFit;$('plus').onclick=()=>{scale*=1.4;draw();};$('minus').onclick=()=>{scale/=1.4;draw();};
 function segmentDistance(p,a,b){const u=[b[0]-a[0],b[1]-a[1]],den=u[0]**2+u[1]**2,t=den?Math.max(0,Math.min(1,((p[0]-a[0])*u[0]+(p[1]-a[1])*u[1])/den)):0;return Math.hypot(p[0]-a[0]-t*u[0],p[1]-a[1]-t*u[1]);}
 function inside(p,points){let yes=false;for(let i=0,j=points.length-1;i<points.length;j=i++){const a=points[i],b=points[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;}return yes;}
 function hitDistance(o,p){const ps=o.points.map(screen);if(o.kind==='polygon'&&inside(p,ps)&&!(o.holes||[]).some(h=>inside(p,h.map(screen))))return 0;let best=Infinity;for(const q of partsOf(o)){const s=q.map(screen);best=Math.min(best,Math.hypot(s[0][0]-p[0],s[0][1]-p[1]));for(let i=1;i<s.length;i++)best=Math.min(best,segmentDistance(p,s[i-1],s[i]));}if(o.kind==='polygon')best=Math.min(best,segmentDistance(p,ps.at(-1),ps[0]));return best;}
 cv.onwheel=e=>{e.preventDefault();if(!scene)return;const r=cv.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top],before=world(p);scale*=Math.exp(-Math.max(-500,Math.min(500,e.deltaY))*.0014);const after=world(p);cx+=before[0]-after[0];cy+=before[1]-after[1];draw();};
 cv.onpointerdown=e=>{cv.setPointerCapture(e.pointerId);drag=[e.clientX,e.clientY,cx,cy];moved=0;};
 cv.onpointermove=e=>{if(!drag||!scene)return;moved=Math.max(moved,Math.hypot(e.clientX-drag[0],e.clientY-drag[1]));cx=drag[2]-(e.clientX-drag[0])/scale;cy=drag[3]-(e.clientY-drag[1])/scale*sign();draw();};
 function createMarker(p){const prof=$('professional').value==='全部'?focused?.professional:$('professional').value;if(!prof){status('请先在左侧选择标记所属专业。');return;}let n=1;while(data.objects.some(o=>o.id==='标记-'+String(n).padStart(3,'0')))n++;const id='标记-'+String(n).padStart(3,'0'),m={id,catalog_key:'我的新增标记|'+id,professional:prof,scene_id:scene.id,category:'我的新增标记',kind:'point',points:[p],title:'新增复核位置',detail:'用户在当前图页添加的位置；专业判断及数量待复核',source:scene.title+'；坐标 '+p.map(v=>Number(v.toPrecision(10))).join(',')+' '+scene.coordinate_unit,status:'用户新增，待处理',quantities:[],attributes:{}};feedback.custom_markers.push(m);installMarkers();placing=false;$('add-marker').classList.remove('on');$('search').value='';setMode('objects');select(m);backup();}
 // Measuring (scene mapping contract 4 and 13):clicks snap to the nearest drawn vertex within 10 px; real length = drawing length x length_factor, or 1:1 for mm/cm/m drawings.
 const UNIT_M={mm:.001,cm:.01,m:1},num=v=>Number(v.toPrecision(6)),lengthFactor=()=>scene.length_factor||UNIT_M[scene.coordinate_unit]||null;
 function snap(p){const view=pointBounds([world([0,0]),world([cw,ch])]);let best=null,gap=10;const test=q=>{const s=screen(q),g=Math.hypot(s[0]-p[0],s[1]-p[1]);if(g<gap){gap=g;best=q;}};for(const r of backgrounds.get(scene.id))if(r.kind!=='text'&&within(r,view))allPts(r).forEach(test);for(const o of mapItems().filter(visible))if(within(o,view))allPts(o).forEach(test);return best?[best[0],best[1]]:world(p);}
 function measureText(){if(mpts.length<2)return '量距：已取 '+mpts.length+' 点，请在图上依次点击，靠近端点会自动吸附；再点“量距”结束。';let d=0;for(let n=1;n<mpts.length;n++)d+=Math.hypot(mpts[n][0]-mpts[n-1][0],mpts[n][1]-mpts[n-1][1]);const f=lengthFactor(),u=scene.coordinate_unit,on='量距 '+(mpts.length-1)+' 段：图上合计 '+num(d)+' '+u;return f?on+'，实长 '+(d*f).toFixed(3)+' m（'+(scene.length_factor?'按本图页换算系数，每 1 '+u+' 折 '+num(f)+' m':'按 1:1，图上 1 '+u+' 即实长 1 '+u)+'）':on+'；本图页没有比例，不换算实长，请对照尺寸标注。';}
 cv.onpointerup=e=>{if(moved<5&&scene&&measuring){const r=cv.getBoundingClientRect();mpts.push(snap([e.clientX-r.left,e.clientY-r.top]));draw();status(measureText());drag=null;return;}if(moved<5&&scene&&!external){const r=cv.getBoundingClientRect(),p=[e.clientX-r.left,e.clientY-r.top];if(placing)createMarker(world(p));else{let best=null,distance=16;for(const o of mapItems().filter(visible)){const gap=hitDistance(o,p);if(gap<distance){distance=gap;best=o;}}if(best){const shown=mapItems();best=twins.get(best.catalog_key)?.same.find(o=>o.quantities?.length&&shown.includes(o))||best;selected?.member_keys?focus(best):select(best);}}}drag=null;};
 $('add-marker').onclick=()=>{if(!scene){status('没有图页，不能新增标记。');return;}placing=!placing;$('add-marker').classList.toggle('on',placing);if(placing&&measuring){measuring=false;mpts=[];$('measure').classList.remove('on');draw();}status(placing?'请点击当前图页添加复核标记。':'');};
 $('measure').onclick=()=>{if(!scene){status('没有图页，不能量距。');return;}measuring=!measuring;mpts=[];$('measure').classList.toggle('on',measuring);if(measuring&&placing){placing=false;$('add-marker').classList.remove('on');}draw();status(measuring?measureText():'');};
 function divider(id,isNav){let state=null;const el=$(id);el.onpointerdown=e=>{el.setPointerCapture(e.pointerId);state={x:e.clientX,nav:document.querySelector('.nav').getBoundingClientRect().width,review:document.querySelector('.review-pane').getBoundingClientRect().width};};el.onpointermove=e=>{if(!state)return;const width=$('layout').getBoundingClientRect().width;$('layout').style.setProperty(isNav?'--navw':'--revieww',(isNav?Math.max(200,Math.min(380,state.nav+e.clientX-state.x)):Math.max(340,Math.min(width-state.nav-300,state.review+state.x-e.clientX)))+'px');};el.onpointerup=()=>{state=null;};}
 divider('nav-splitter',true);divider('review-splitter',false);$('widen-editor').onclick=()=>{wide=!wide;$('layout').style.setProperty('--revieww',wide?Math.max(390,Math.min(720,$('layout').getBoundingClientRect().width-580))+'px':'clamp(390px,34vw,620px)');$('widen-editor').textContent=wide?'恢复常用宽度':'加宽填写区';};
 $('export-image').onclick=()=>{if(!scene)return;const out=document.createElement('canvas');out.width=cv.width;out.height=cv.height+85*devicePixelRatio;const c=out.getContext('2d');c.fillStyle='white';c.fillRect(0,0,out.width,out.height);c.drawImage(cv,0,85*devicePixelRatio);c.scale(devicePixelRatio,devicePixelRatio);c.fillStyle='#21485f';c.font='bold 16px Microsoft YaHei';c.fillText(data.title+' · '+scene.title,14,25);c.font='12px Microsoft YaHei';c.fillText(focused?.title||'对象复核图',14,48);c.fillText('图面定位与业务数量分别记录；完整批注保存在HTML/JSON。',14,68);out.toBlob(b=>{if(b)download(filename()+'_回图_'+stamp()+'.png','image/png',b);});};
 $('page-title').textContent=data.title;$('page-title').title=data.title;$('current-summary').textContent='项目：'+data.project_id+' · '+data.revision+' · '+data.objects.length+'条复核记录，'+data.groups.length+'个问题组。';
 $('scope-notes').textContent=data.scope_notes.join('\n');
 for(const q of data.summary){const chip=document.createElement('span');chip.className='summary-chip';chip.textContent=q.scope+'｜'+q.label+' '+(q.value===null?'待定':q.value)+' '+q.unit;$('summary-strip').append(chip);}
 $('professional').replaceChildren();option($('professional'),'全部','全部专业');for(const p of Array.from(new Set(data.objects.map(o=>o.professional))))option($('professional'),p,data.professional_labels[p]);$('professional').value='全部';
 $('scene').replaceChildren();for(const s of data.scenes)option($('scene'),s.id,s.title);$('scene').value=scene?.id||'';$('symbols-panel').hidden=true;renderSymbols();
 if(!scene){$('map-section').hidden=true;$('review-splitter').hidden=true;$('layout').classList.add('no-map');}
 new ResizeObserver(resize).observe(cv.parentElement);resize();fit();setMode(mode);status();
 const identity=globalThis.QtoReviewIdentity?.attach({data,core,getFeedback:()=>feedback,backup,showQuantity(){globalThis.QtoReviewAudit?.active?.setMode('results');},showEvidence(id){globalThis.QtoReviewAudit?.active?.setMode('review');const d=data.review_workflow.decisions.find(x=>x.id===id),list=(d?.scope.member_keys||[]).map(k=>objects.get(k)).filter(Boolean);locate(list,list.find(o=>o.kind!=='unlocated'));}});
 if(selected)identity?.focusTarget(selected.member_keys?'group':'object',selected.member_keys?selected.id:selected.catalog_key);
 const audit=globalThis.QtoReviewAudit?.attach({data,core,getFeedback:()=>feedback,backup,status,draw,resize,locate,
  review(){external=null;resize();draw();},
  selectGroup(id){const g=data.groups.find(x=>x.id===id);if(!g)return false;external=null;$('search').value='';setMode('groups');$('professional').value='全部';$('category').value='全部';list();select(g);return true;},
  selectObject(ref){const o=objects.get(ref)||data.objects.find(x=>x.id===ref);if(!o)return false;external=null;$('search').value='';setMode('objects');$('professional').value='全部';$('category').value='全部';list();select(o);return true;},
  scene(id){if(!scenes.has(id))return false;changeScene(id);return true;}});
 const importChange=$('import-file').onchange;$('import-file').onchange=async()=>{await importChange();audit?.refresh();identity?.refresh();};
 $('loading').remove();
}catch(e){const node=$('loading');if(node)node.textContent='复核页未能载入：'+e.message;}
})();
