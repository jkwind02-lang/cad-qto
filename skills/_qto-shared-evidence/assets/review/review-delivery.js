(function(root){
'use strict';
// Opt-in contract; legacy 1.0/1.1 pages keep their existing application.
function attach(api){
 const {data,core,originalHTML,storageKey}=api,d=data.delivery,$=id=>document.getElementById(id);
 let feedback=api.feedback,tab='quantity',page=0,query='',audit=null;
 const panel=$('delivery'),objects=new Map(data.objects.map(o=>[o.catalog_key,o])),scenes=new Map(data.scenes.map(s=>[s.id,s]));
 const retired=new Set(d.retired_member_keys),current=new Set(d.current_member_keys),sources=new Map(data.sources.map(s=>[s.id,s]));
 const el=(tag,txt,cls)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;if(cls)e.className=cls;return e;};
 const button=(txt,fn)=>{const b=el('button',txt);b.onclick=fn;return b;};
 const line=(box,label,value)=>box.append(el('p',label+'：'+value));
 const scopeText=s=>'适用 '+s.included.join('、')+'；排除 '+(s.excluded.length?s.excluded.join('、'):'无');
 const refsText=refs=>(refs||[]).map(r=>(sources.get(r.source_id)?.title||r.source_id)+' · '+r.locator).join('；');
 const num=(v,u)=>(v===null?'未知（null）':String(v))+' '+u;
 const message=s=>{$('review-status').textContent=s;};
 function backup(){
  feedback=core.parse(feedback,data);feedback.drawing_revision=data.revision;feedback.exported_at=new Date().toISOString();
  try{localStorage.setItem(storageKey,JSON.stringify(feedback));message('已在本机保存；交接请保存HTML或导出JSON。');}
  catch(e){message('本机缓存不可用，请保存HTML或导出JSON：'+e.message);}
 }
 function download(name,type,text){const url=URL.createObjectURL(new Blob([text],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const filename=()=>data.title.replace(/[\\/:*?"<>|]/g,'_'),stamp=()=>new Date().toISOString().replace(/[:.]/g,'-');
 function saveHTML(prepared){backup();const file=prepared||{filename:filename()+'_交付_'+stamp()+'.html',html:core.embed(originalHTML,feedback)};download(file.filename,'text/html;charset=utf-8',file.html);return file;}
 $('save-html').onclick=()=>saveHTML();$('save-html').textContent='保存交付HTML';
 $('export-feedback').onclick=()=>{backup();download(filename()+'_交付与意见_'+stamp()+'.json','application/json;charset=utf-8',JSON.stringify(feedback,null,2));};
 $('import-feedback').onclick=()=>$('import-file').click();
 $('import-file').onchange=async()=>{const file=$('import-file').files[0];if(!file)return;try{
  const incoming=core.parse(core.readFileText(await file.text()),data);
  const historical=[...(feedback.historical_delivery_snapshots||[]),...(incoming.historical_delivery_snapshots||[])];
  feedback=core.mergePackages(feedback,incoming);feedback.historical_delivery_snapshots=historical.filter((x,n)=>historical.findIndex(y=>JSON.stringify(x)===JSON.stringify(y))===n);
  backup();audit?.refresh();show();message('已导入；旧意见及旧交付快照保留历史，当前采用仍以本页数据为准。');
 }catch(e){message('导入未应用：'+e.message);}finally{$('import-file').value='';}};
 root.QtoProblemFeedback?.attach({data,getFeedback:()=>feedback,saveLocal:saveHTML});
 const adoptionText=d.adoption_boundary+(d.adoption_note===d.adoption_boundary?'':' '+d.adoption_note);
 $('page-title').textContent=data.title;$('current-summary').textContent=data.project_id+' · '+data.revision+' · '+adoptionText;
 $('scope-notes').textContent=data.scope_notes.join('\n');$('summary-strip').replaceChildren();
 // No old object/group forms, custom markers, quantity chips or retired drawing tags are installed.
 $('layout').hidden=true;$('audit').hidden=true;$('audit-top').hidden=true;$('mode-tabs').hidden=true;panel.hidden=false;
 const back=button('返回工程量与资料缺口',()=>show());$('action-message').replaceChildren(back);$('action-message').hidden=true;

 function drawMap(canvas,keys,focus){
  const o=objects.get(focus);if(!o||retired.has(focus)||o.kind==='unlocated'||!scenes.has(o.scene_id))return false;
  const scene=scenes.get(o.scene_id),b=scene.bounds,w=900,h=460,pad=22,scale=Math.min((w-2*pad)/(b[2]-b[0]),(h-2*pad)/(b[3]-b[1]));
  canvas.width=w;canvas.height=h;canvas.className='delivery-canvas';const ctx=canvas.getContext('2d');
  ctx.clearRect(0,0,w,h);ctx.fillStyle='white';ctx.fillRect(0,0,w,h);
  const xy=p=>[pad+(p[0]-b[0])*scale,scene.y_axis==='down'?pad+(p[1]-b[1])*scale:h-pad-(p[1]-b[1])*scale];
  const path=(points,closed=false)=>{if(!points?.length)return;ctx.beginPath();points.forEach((p,i)=>{const [x,y]=xy(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});if(closed)ctx.closePath();ctx.stroke();};
  const backgrounds=[...(scene.background_refs||[]).flatMap(k=>data.background_sets[k]),...scene.background];
  ctx.strokeStyle='#b3c4cd';ctx.lineWidth=.6;ctx.fillStyle='#899ca7';ctx.font='11px Microsoft YaHei';
  for(const p of backgrounds){if(p.kind==='text'){const q=xy(p.point);ctx.fillText(p.text,...q);}else path(p.points,p.kind==='polygon');}
  for(const key of keys){const x=objects.get(key);if(!x||retired.has(key)||x.kind==='unlocated'||x.scene_id!==scene.id)continue;
   const sourceOnly=!current.has(key);ctx.strokeStyle=key===focus?'#b83e2d':sourceOnly?'#a36d16':'#246a87';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=key===focus?2.5:1.5;ctx.setLineDash(sourceOnly?[5,4]:[]);
   if(x.kind==='point'){const p=xy(x.points[0]);ctx.beginPath();ctx.arc(...p,5,0,Math.PI*2);ctx.stroke();}
   else for(const points of (x.parts||[x.points]))path(points,x.kind==='polygon');
   const p=xy(x.points[0]);ctx.font='12px Microsoft YaHei';ctx.fillText(x.id+(sourceOnly?'（仅证据定位）':''),p[0]+7,p[1]-7);
  }ctx.setLineDash([]);return true;
 }
 function quantityCard(row){
  const card=el('article',null,'delivery-card '+row.state);card.dataset.rowId=row.id;
  card.append(el('h3',row.title+' · '+num(row.quantity,row.unit)));
  line(card,'角色',({current:'当前采用主量',reference:'参考值（不与主量默认相加）',historical:'历史量',retired:'已退役量'})[row.state]);
  line(card,'范围',scopeText(row.scope));line(card,'覆盖',row.coverage.state==='complete'?'完整':'局部');
  line(card,'已算范围',row.coverage.computed_scope.join('、')||'无');line(card,'未算范围',row.coverage.uncomputed_scope.join('、')||'无');line(card,'依据',row.basis);
  if(row.evidence){line(card,'证据',row.evidence.state==='verified'?'已核实':'待核实');line(card,'来源定位',refsText(row.evidence.source_refs)||'尚缺');
   for(const fact of row.evidence.input_facts||[])line(card,'规则输入 '+fact.id,fact.statement+' · '+(fact.state==='verified'?'已核实':'待核实')+' · '+refsText(fact.source_refs));}
  if(row.reference){line(card,'参考前提',row.reference.prerequisites.join('；'));line(card,'与同范围主量关系',({includes_current:'包含主量',excludes_current:'不含主量',unknown:'包含关系未知'})[row.reference.relationship]+'；关联主量 '+(row.reference.current_row_ids.join('、')||'无')+'；禁止默认相加');}
  if(row.history_reason)line(card,'历史原因',row.history_reason);
  if(row.state==='current'||row.state==='reference'){
   const keys=Array.from(new Set([...row.member_keys,...row.evidence_member_keys])).filter(k=>!retired.has(k));
   if(!keys.length)line(card,'图元关系','无图面对象；按上述来源记录定位');
   const canvas=el('canvas');canvas.hidden=true;card.append(canvas);
   const state=el('p');card.append(state);
   for(const key of keys){const o=objects.get(key),role=current.has(key)&&row.member_keys.includes(key)?'采用成员':'仅来源证据定位';
    card.append(button(o.title+' · '+role,()=>{const ok=drawMap(canvas,keys,key);canvas.hidden=!ok;state.textContent=ok?'图页：'+scenes.get(o.scene_id).title+'；稳定键 '+key+'；'+role:'暂无图面几何；稳定键 '+key+'；来源 '+o.source;}));}
  }return card;
 }
 function missingCard(node,parentTitle){
  const card=el('article',null,'delivery-card');card.dataset.missingId=node.id;card.append(el('h3',(parentTitle?parentTitle+' / ':'')+node.title));
  line(card,'适用与排除',scopeText(node.scope));
  line(card,'已核事实',node.verified_facts.length?'如下':'暂无已核事实；没有把未知写成零');
  for(const fact of node.verified_facts)line(card,'事实',fact.statement+' · '+refsText(fact.source_refs));
  for(const need of node.minimum_required){line(card,'最少缺件',need.description);line(card,'缺件适用与排除',scopeText(need.scope));line(card,'提供方',need.provider);line(card,'影响',need.impact);line(card,'补齐后助手动作',need.recompute_action);}
  if(node.children?.length)line(card,'逐项范围','子项单独列示；不继承父项事实、缺件或全部适用范围');return card;
 }
 function entries(){
  if(tab==='quantity')return d.quantity_rows.filter(r=>r.state==='current'||r.state==='reference').map(r=>({search:r,render:()=>quantityCard(r)}));
  if(tab==='missing'){
   const out=[];const walk=(node,parent='')=>{out.push({search:node,render:()=>missingCard(node,parent)});for(const child of node.children||[])walk(child,node.title);};d.missing_groups.forEach(g=>walk(g));return out;
  }
  if(tab==='decision')return d.decision_targets.map(t=>({search:t,render:()=>{const card=el('article',null,'delivery-card');line(card,'真实业务取舍',t.reason);card.append(button('填写此项业务意见',()=>{
   if(audit){panel.hidden=true;$('action-message').hidden=false;audit.apply('mode=resolve&'+(t.kind==='common'?'common':'issue')+'='+encodeURIComponent(t.id));$('mode-tabs').hidden=true;}
  }));return card;}}));
  const out=d.quantity_rows.filter(r=>r.state==='historical'||r.state==='retired').map(r=>({search:r,render:()=>quantityCard(r)}));
  for(const [key,r] of Object.entries(feedback.annotations))out.push({search:{key,...r},render:()=>{const card=el('article',null,'delivery-card historical');card.append(el('h3',key));line(card,'适用状态',r.applicability_state||'历史');line(card,'说明',r.applicability_reason||'');line(card,'意见',r.confirmed_result||r.verdict||'');line(card,'原因',r.reason||'');const details=el('details');details.append(el('summary','完整历史与快照'),el('pre',JSON.stringify(r,null,2)));card.append(details);return card;}});
  for(const snapshot of feedback.historical_delivery_snapshots||[])out.push({search:snapshot,render:()=>{const card=el('details',null,'delivery-card historical');card.append(el('summary','旧交付快照（仅历史）'),el('pre',JSON.stringify(snapshot,null,2)));return card;}});
  return out;
 }
 function show(){
  panel.hidden=false;$('layout').hidden=true;$('audit').hidden=true;$('audit-top').hidden=true;$('mode-tabs').hidden=true;$('action-message').hidden=true;
  panel.replaceChildren();panel.append(el('p',d.adoption_boundary));if(d.adoption_note!==d.adoption_boundary)panel.append(el('p',d.adoption_note));const nav=el('div',null,'delivery-nav');
  for(const [key,title] of [['quantity','工程量'],['missing','资料缺口'],...(d.decision_targets.length?[['decision','业务取舍']]:[]),['history','历史记录']])nav.append(button(title,()=>{tab=key;page=0;show();}));panel.append(nav);
  const input=el('input');input.placeholder='搜索完整数据';input.value=query;input.oninput=()=>{query=input.value.toLowerCase();page=0;renderRows();};panel.append(input);
  const rows=el('div'),pager=el('div',null,'delivery-nav');panel.append(rows,pager);
  function renderRows(){rows.replaceChildren();pager.replaceChildren();const all=entries().filter(x=>!query||JSON.stringify(x.search).toLowerCase().includes(query)),pages=Math.max(1,Math.ceil(all.length/60));page=Math.min(page,pages-1);
   rows.append(el('p',all.length+' 项 · 第 '+(page+1)+' / '+pages+' 页；数值按单位及范围分别列示，不自动加总。'));
   for(const x of all.slice(page*60,(page+1)*60))rows.append(x.render());if(!all.length)rows.append(el('p',tab==='missing'?'本次没有外部资料缺件。':'本页暂无记录。'));
   const prev=button('上一页',()=>{page--;renderRows();}),next=button('下一页',()=>{page++;renderRows();});prev.disabled=page===0;next.disabled=page===pages-1;pager.append(prev,next);
  }renderRows();
 }
 if(d.mode==='decisions')audit=root.QtoReviewAudit.attach({data,core,getFeedback:()=>feedback,backup,status:message,draw(){},resize(){},locate(){return false;},review(){},selectGroup(){return false;},selectObject(){return false;},scene(){return false;}});
 show();message(d.decision_targets.length?'只列已声明的真实业务取舍；意见经专业回算后才更新数量。':'只读交付；没有数量审核表单。');
 return root.QtoReviewDelivery.active={show,getFeedback:()=>feedback,tab:()=>tab};
}
root.QtoReviewDelivery={attach};
})(globalThis);
