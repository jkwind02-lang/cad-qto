(function(root){
'use strict';
// All navigation reads professional-owned delivery data. It never changes quantities or comparison eligibility.
function attach(api){
 const {data,core,originalHTML,storageKey}=api,d=data.delivery,$=id=>document.getElementById(id);
 let feedback=api.feedback,tab='quantity',page=0,query='',slice='all',dimension='',audit=null,selected=null,selectedRow=null;
 const panel=$('delivery'),objects=new Map(data.objects.map(o=>[o.catalog_key,o])),scenes=new Map(data.scenes.map(s=>[s.id,s]));
 const retired=new Set(d.retired_member_keys||[]),current=new Set(d.current_member_keys||[]),sources=new Map(data.sources.map(s=>[s.id,s]));
 const rowsById=new Map(d.quantity_rows.map(r=>[r.id,r])),statistics=d.comparison_statistics,coverage=d.coverage_check;
 const statsById=new Map((statistics?.items||[]).map(x=>[x.item_id,x])),coverageById=new Map((coverage?.items||[]).map(x=>[x.item_id,x]));
 const el=(tag,txt,cls)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;if(cls)e.className=cls;return e;};
 const button=(txt,fn,cls)=>{const b=el('button',txt,cls);b.type='button';b.onclick=fn;return b;};
 const line=(box,label,value)=>box.append(el('p',label+'：'+value));
 const scopeText=s=>!s?'未提供':Array.isArray(s)?(s.join('、')||'未列出'):(typeof s==='string'?s:'适用 '+(s.included||[]).join('、')+'；排除 '+((s.excluded||[]).join('、')||'无'));
 const refsText=refs=>(refs||[]).map(r=>(sources.get(r.source_id)?.title||r.source_id)+' · '+r.locator).join('；');
 const number=v=>v===null||v===undefined?'未知（null）':String(v),num=(v,u)=>number(v)+(u?' '+u:'');
 const stateText=s=>({current:'当前采用主量',reference:'参考值',historical:'历史量',retired:'已退役量',complete:'完整',partial:'局部',unknown:'未知',pass:'已核对',attention:'需关注',comparable:'可比',not_comparable:'不可比',consistent:'容差内',positive:'正偏差',negative:'负偏差',extra:'无原量新增',original_unknown:'原量未知',current_unknown:'本次量未知',unit_mismatch:'单位不同',no_original_row:'无原清单行',multiple_sources:'多来源分别比较'})[s]||s||'未提供';
 const message=s=>{$('review-status').textContent=s;};
 const items=Array.isArray(d.items)?d.items:d.quantity_rows.filter(r=>r.state==='current'||r.state==='reference').map(r=>({id:r.id,title:r.title,unit:r.unit,quantity:r.state==='current'?r.quantity:null,original_quantity:null,scope:r.scope,coverage:r.coverage,current_row_ids:r.state==='current'?[r.id]:[],reference_row_ids:r.state==='reference'?[r.id]:[],historical_row_ids:[],dimensions:{},legacy:true}));
 const itemById=new Map(items.map(x=>[x.id,x])),backgrounds=new Map(),backgroundBounds=new WeakMap(),images=new Map();
 let mapKeys=[],focusKey=null,mapRole='',mapNote='',ratio=52,fullscreen=false,mapCanvas,mapState,mapHead,mapPane,workspace,detailPane,splitter;
 let listPane,listBox,pager,counter,search,selectSlice,selectDimension,summary,body,nav,mapObserver,fullscreenButton;
 const pageSize=50,layoutKey=storageKey+':delivery-layout';
 try{const pref=JSON.parse(localStorage.getItem(layoutKey)||'null');if(Number.isFinite(pref?.map_percent))ratio=Math.max(25,Math.min(75,pref.map_percent));}catch(e){}
 function backup(){
  feedback=core.parse(feedback,data);feedback.drawing_revision=data.revision;feedback.exported_at=new Date().toISOString();
  try{localStorage.setItem(storageKey,JSON.stringify(feedback));message('已在本机保存；交接请保存HTML或导出JSON。');}
  catch(e){message('本机缓存不可用，请保存HTML或导出JSON：'+e.message);}
 }
 function download(name,type,text){const url=URL.createObjectURL(new Blob([text],{type})),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const filename=()=>data.title.replace(/[\\/:*?"<>|]/g,'_'),stamp=()=>new Date().toISOString().replace(/[:.]/g,'-');
 function saveHTML(prepared){backup();const file=prepared||{filename:filename()+'_交付_'+stamp()+'.html',html:core.embed(originalHTML,feedback,data)};download(file.filename,'text/html;charset=utf-8',file.html);return file;}
 $('save-html').onclick=()=>saveHTML();$('save-html').textContent='保存交付HTML';
 $('export-feedback').onclick=()=>{backup();download(filename()+'_交付与意见_'+stamp()+'.json','application/json;charset=utf-8',JSON.stringify(feedback,null,2));};
 $('import-feedback').onclick=()=>$('import-file').click();
 $('import-file').onchange=async()=>{const file=$('import-file').files[0];if(!file)return;try{
  const incoming=core.parse(await core.readFileTextAsync(await file.text()),data),historical=[...(feedback.historical_delivery_snapshots||[]),...(incoming.historical_delivery_snapshots||[])];
  feedback=core.mergePackages(feedback,incoming);feedback.historical_delivery_snapshots=historical.filter((x,n)=>historical.findIndex(y=>JSON.stringify(x)===JSON.stringify(y))===n);
  backup();audit?.refresh();show();message('已导入；旧意见及旧交付快照保留历史，当前采用仍以本页数据为准。');
 }catch(e){message('导入未应用：'+e.message);}finally{$('import-file').value='';}};
 root.QtoProblemFeedback?.attach({data,getFeedback:()=>feedback,saveLocal:saveHTML});
 const adoptionText=d.adoption_boundary+(d.adoption_note===d.adoption_boundary?'':' '+d.adoption_note);
 $('page-title').textContent=data.title;$('current-summary').textContent=data.project_id+' · '+data.revision+' · '+adoptionText;
 $('scope-notes').textContent=data.scope_notes.join('\n');$('summary-strip').replaceChildren();
 $('layout').hidden=true;$('audit').hidden=true;$('audit-top').hidden=true;$('mode-tabs').hidden=true;panel.hidden=false;
 const back=button('返回工程量与资料缺口',()=>show());$('action-message').replaceChildren(back);$('action-message').hidden=true;
 function setRatio(value,persist=true){
  ratio=Math.max(25,Math.min(75,value));workspace?.style.setProperty('--delivery-map-ratio',ratio+'%');
  splitter?.setAttribute('aria-valuenow',String(Math.round(ratio)));
  if(persist)try{localStorage.setItem(layoutKey,JSON.stringify({map_percent:ratio}));}catch(e){}
  drawMap();
 }
 function clearMap(note){mapKeys=[];focusKey=null;mapRole='';mapNote=note||'本项无可靠图面定位；保留数量与依据，未生成坐标。';drawMap();}
 function validKeys(keys){return Array.from(new Set(keys||[])).filter(k=>!retired.has(k)&&objects.has(k));}
 function locate(keys,role,preferred){
  mapKeys=validKeys(keys);mapRole=role;focusKey=preferred&&mapKeys.includes(preferred)?preferred:mapKeys[0]||null;
  if(!focusKey){clearMap('本条明细无可靠图面对象；数量及来源依据保留，已清除上次图面。');return false;}
  const o=objects.get(focusKey);mapNote=o.kind==='unlocated'||!scenes.has(o.scene_id)?'暂无图面几何；稳定键 '+focusKey+'；来源 '+o.source:'';
  drawMap();return !mapNote;
 }
 function objectPoints(o){return (o.parts||[o.points||[]]).flat();}
 function drawMap(){
  if(!mapCanvas)return;const rect=mapCanvas.getBoundingClientRect(),w=Math.max(160,Math.round(rect.width||800)),h=Math.max(80,Math.round(rect.height||300));
  const pixelRatio=Math.max(1,Math.min(2,root.devicePixelRatio||1));mapCanvas.width=w*pixelRatio;mapCanvas.height=h*pixelRatio;const ctx=mapCanvas.getContext('2d');ctx.setTransform(pixelRatio,0,0,pixelRatio,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);
  mapCanvas.dataset.memberKeys=JSON.stringify(mapKeys);mapCanvas.dataset.focusKey=focusKey||'';mapCanvas.dataset.role=mapRole;
  const o=objects.get(focusKey),scene=o&&scenes.get(o.scene_id);
  if(!o||retired.has(focusKey)||o.kind==='unlocated'||!scene){mapState.textContent=mapNote||'选择清单或明细查看图纸。';mapHead.textContent='图纸定位 · 无可靠图面';mapCanvas.dataset.sceneId='';return;}
  mapCanvas.dataset.sceneId=scene.id;
  const located=mapKeys.map(k=>objects.get(k)).filter(x=>x&&x.kind!=='unlocated'&&x.scene_id===scene.id),points=located.flatMap(objectPoints);
  let b=scene.bounds;
  if(points.length){const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys),span=Math.max(maxx-minx,maxy-miny,Math.max(scene.bounds[2]-scene.bounds[0],scene.bounds[3]-scene.bounds[1])*.025,1e-8),pad=span*.16;b=[minx-pad,miny-pad,maxx+pad,maxy+pad];}
  const pad=25,scale=Math.min((w-2*pad)/Math.max(b[2]-b[0],1e-9),(h-2*pad)/Math.max(b[3]-b[1],1e-9)),cx=(b[0]+b[2])/2,cy=(b[1]+b[3])/2;
  const xy=p=>[w/2+(p[0]-cx)*scale,h/2+(scene.y_axis==='down'?1:-1)*(p[1]-cy)*scale];
  const path=(ps,closed=false)=>{if(!ps?.length)return;ctx.beginPath();ps.forEach((p,i)=>{const q=xy(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});if(closed)ctx.closePath();ctx.stroke();};
  const sign=scene.y_axis==='down'?1:-1;
  if(scene.raster_data_uri){let im=images.get(scene.id);if(!im){im=new Image();images.set(scene.id,im);im.onload=()=>drawMap();im.src=scene.raster_data_uri;}if(im.complete&&im.naturalWidth){const a=xy([scene.bounds[0],scene.y_axis==='down'?scene.bounds[1]:scene.bounds[3]]),z=xy([scene.bounds[2],scene.y_axis==='down'?scene.bounds[3]:scene.bounds[1]]);ctx.drawImage(im,a[0],a[1],z[0]-a[0],z[1]-a[1]);}}
  if(!backgrounds.has(scene.id))backgrounds.set(scene.id,[...(scene.background_refs||[]).flatMap(k=>data.background_sets?.[k]||[]),...(scene.background||[])]);
  const viewBounds=[cx-w/(2*scale),cy-h/(2*scale),cx+w/(2*scale),cy+h/(2*scale)];
  const inView=r=>{let rb=backgroundBounds.get(r);if(!rb){const xs=r.points.map(p=>p[0]),ys=r.points.map(p=>p[1]);rb=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];backgroundBounds.set(r,rb);}return rb[0]<=viewBounds[2]&&rb[2]>=viewBounds[0]&&rb[1]<=viewBounds[3]&&rb[3]>=viewBounds[1];};
  for(const p of backgrounds.get(scene.id)){
   if(p.kind==='text'){
    const fs=p.height?p.height*scale*1.25:10,axes=p.text_axes,vertical=axes?Math.hypot(...axes[1]):1,stretch=axes?Math.max(vertical,Math.hypot(...axes[0])):Math.max(1,p.width_factor||1);
    if(p.height&&fs*vertical<2.5)continue;const q=xy(p.point),m=(p.height?fs*stretch*p.text.length:150*stretch)+150;if(q[0]<-m||q[0]>w+m||q[1]<-m||q[1]>h+m)continue;
    ctx.save();ctx.translate(...q);if(axes){const [[bx,by],[ux,uy]]=axes;ctx.transform(bx,sign*by,-ux,-sign*uy,0,0);}else{ctx.rotate((p.rotation||0)*Math.PI/180*sign);if(p.width_factor)ctx.scale(p.width_factor,1);}
    ctx.font=fs+'px "Microsoft YaHei",sans-serif';ctx.textAlign=p.align||'left';ctx.textBaseline=p.baseline||'alphabetic';ctx.fillStyle=p.color||'#7c909c';ctx.fillText(p.text,0,0);ctx.restore();
   }else{
    if(!inView(p))continue;ctx.strokeStyle=p.color||'#b4c1ca';ctx.lineWidth=p.width||.7;const dashed=p.dash&&p.dash.reduce((a,v)=>a+v,0)*scale>=4;if(dashed)ctx.setLineDash(p.dash.map(v=>Math.max(1,v*scale)));path(p.points,p.kind==='polygon');if(p.fill){ctx.fillStyle=p.fill;ctx.fill();}if(dashed)ctx.setLineDash([]);
   }
  }
  for(const x of located){const sourceOnly=mapRole!=='当前采用成员'||!current.has(x.catalog_key);ctx.strokeStyle=x.catalog_key===focusKey?'#ba3b2f':sourceOnly?'#a36d16':'#246a87';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=x.catalog_key===focusKey?2.5:1.5;ctx.setLineDash(sourceOnly?[5,4]:[]);
   if(x.kind==='point'){const p=xy(x.points[0]);ctx.beginPath();ctx.arc(...p,5,0,Math.PI*2);ctx.stroke();}else{for(const ps of (x.parts||[x.points]))path(ps,x.kind==='polygon');for(const hole of x.holes||[])path(hole,true);}
   if(x.points?.length){const p=xy(x.points[0]);ctx.font='12px "Microsoft YaHei",sans-serif';ctx.fillText(x.id+(sourceOnly?(mapRole==='参考定位'?'（参考定位）':'（仅证据定位）'):''),p[0]+7,p[1]-7);}
  }ctx.setLineDash([]);
  const n=mapKeys.indexOf(focusKey)+1;mapHead.textContent='图纸定位 · '+scene.title;
  mapState.textContent=mapRole+' · '+n+' / '+mapKeys.length+' 处；本图 '+located.length+' 处；稳定键 '+focusKey+(mapNote?'；'+mapNote:'');
 }
 function itemRows(item,includeHistory=false){return [...(item.current_row_ids||[]),...(item.reference_row_ids||[]),...(includeHistory?item.historical_row_ids||[]:[])].map(id=>rowsById.get(id)).filter(Boolean);}
 function locateItem(item){selectedRow=null;const keys=itemRows(item).filter(r=>r.state==='current').flatMap(r=>r.member_keys||[]);if(keys.length)locate(keys,'当前采用成员');else clearMap('本清单项暂无当前采用图元；可阅读明细或单独查看来源证据定位。');}
 function rowDetails(row){
  const card=el('article',null,'delivery-card '+row.state);card.dataset.rowId=row.id;card.append(el('h3',row.title+' · '+num(row.quantity,row.unit)));
  line(card,'角色',stateText(row.state)+(row.state==='reference'?'（不与主量默认相加）':''));line(card,'范围',scopeText(row.scope));
  line(card,'覆盖',stateText(row.coverage?.state));line(card,'已算范围',(row.coverage?.computed_scope||[]).join('、')||'无');line(card,'未算范围',(row.coverage?.uncomputed_scope||[]).join('、')||'无');line(card,'依据',row.basis);
  if(row.evidence){line(card,'证据',row.evidence.method==='adopted_record'?'来源已采用记录迁移；本次只核冻结记录身份，未重新证明几何或规则输入':row.evidence.state==='verified'?'已核实':'待核实');line(card,'来源定位',refsText(row.evidence.source_refs)||'尚缺');for(const fact of row.evidence.input_facts||[])line(card,'规则输入 '+fact.id,fact.statement+' · '+(fact.state==='verified'?'已核实':'待核实')+' · '+refsText(fact.source_refs));}
  if(row.reference){line(card,'参考前提',row.reference.prerequisites.join('；'));line(card,'与同范围主量关系',({includes_current:'包含主量',excludes_current:'不含主量',unknown:'包含关系未知'})[row.reference.relationship]+'；关联主量 '+(row.reference.current_row_ids.join('、')||'无')+'；禁止默认相加');}
  if(row.reference?.association_state==='scope_only'){line(card,'原记录关系声明',(row.reference.source_claimed_relationship==='includes_current'?'原记录声称包含同范围主量；':'')+'逐明细包含范围未证，不得相加。');line(card,'参考关联范围',row.reference.scope_statement||scopeText(row.scope));line(card,'逐行关系',row.reference.association_reason||'仅有范围关联；未提供逐行对应关系，不猜配主量明细');}
  if(row.history_reason)line(card,'历史原因',row.history_reason);
  if(row.state==='current'||row.state==='reference'){
   const members=validKeys(row.member_keys),evidence=validKeys(row.evidence_member_keys).filter(k=>!members.includes(k));
   if(!members.length&&!evidence.length)line(card,'图元关系','无图面对象；按上述来源记录定位');
   const links=el('div',null,'delivery-locations');
   for(const key of members)links.append(button(objects.get(key).title+' · '+(row.state==='current'?'采用成员':'参考定位'),()=>locate(members,row.state==='current'?'当前采用成员':'参考定位',key)));
   for(const key of evidence)links.append(button(objects.get(key).title+' · 仅来源证据定位',()=>locate(evidence,'仅来源证据定位',key)));
   card.append(links);
  }return card;
 }
 function missingCard(node,parentTitle){
  const card=el('article',null,'delivery-card');card.dataset.missingId=node.id;card.append(el('h3',(parentTitle?parentTitle+' / ':'')+node.title));line(card,'适用与排除',scopeText(node.scope));
  line(card,'已核事实',node.verified_facts.length?'如下':'暂无已核事实；没有把未知写成零');for(const fact of node.verified_facts)line(card,'事实',fact.statement+' · '+refsText(fact.source_refs));
  for(const need of node.minimum_required){line(card,'最少缺件',need.description);line(card,'缺件适用与排除',scopeText(need.scope));line(card,'提供方',need.provider);line(card,'影响',need.impact);line(card,'补齐后助手动作',need.recompute_action);}
  if(node.children?.length)line(card,'逐项范围','子项单独列示；不继承父项事实、缺件或全部适用范围');return card;
 }
 function table(headers,cls='delivery-table'){const t=el('table',null,cls),head=el('thead'),tr=el('tr'),body=el('tbody');for(const h of headers)tr.append(el('th',h));head.append(tr);t.append(head,body);return {table:t,body};}
 function rowTable(item){
  const rows=itemRows(item),wrap=el('div',null,'delivery-detail-table');const t=table(['角色 / 明细','本次量','覆盖','图元']);
  for(const r of rows){const tr=el('tr',null,'delivery-detail-row'+(r.id===selectedRow?' active':''));tr.dataset.rowId=r.id;tr.tabIndex=0;tr.setAttribute('role','button');tr.setAttribute('aria-label',r.title+'，'+stateText(r.state)+'，'+num(r.quantity,r.unit));
   tr.append(el('td',stateText(r.state)+' · '+r.title),el('td',num(r.quantity,r.unit)),el('td',stateText(r.coverage?.state)),el('td',validKeys(r.member_keys).length+' 处'));
   tr.onclick=()=>{selectedRow=r.id;for(const child of t.body.children)child.className='delivery-detail-row'+(child.dataset.rowId===r.id?' active':'');const keys=validKeys(r.member_keys);if(keys.length)locate(keys,r.state==='current'?'当前采用成员':'参考定位');else clearMap('本条明细无可靠图面对象；数量及来源依据保留，已清除上次图面。');};
   tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tr.onclick();}};t.body.append(tr);
  }wrap.append(t.table);if(!rows.length)wrap.append(el('p','本清单项暂无数量明细；清单原文及未知值完整保留。'));return wrap;
 }
 function comparisonDetails(item){
  const s=statsById.get(item.id),box=el('section',null,'delivery-facts');box.append(el('h3','数值偏差 · 仅限可比范围'));
  if(!s){line(box,'状态','未提供可比统计；不据此推断一致或无遗漏');return box;}
  line(box,'比较资格',stateText(s.comparison_state));line(box,'具体理由',s.reason||'未提供');line(box,'本次 / 原量',num(s.current_quantity,s.unit)+' / '+num(s.original_quantity,item.original_unit||s.unit));
  line(box,'可比差额',num(s.delta,s.unit));line(box,'偏差比例',s.rate===null||s.rate===undefined?'未知（null）':String(Number((s.rate*100).toFixed(4)))+'%');
  for(const segment of s.source_segments||[]){const row=el('p');row.textContent=(sources.get(segment.source_id)?.title||segment.source_id)+'：'+stateText(segment.state)+'；'+(segment.reason||'')+'；差额 '+num(segment.delta,segment.unit)+'；比例 '+(segment.rate==null?'未知（null）':String(Number((segment.rate*100).toFixed(4)))+'%');box.append(row);}return box;
 }
 function coverageDetails(item){
  const c=coverageById.get(item.id),box=el('section',null,'delivery-facts');box.append(el('h3','独立覆盖查漏 · 与数值偏差分开'));
  line(box,'核对基准',coverage?.baseline_state==='explicit'?'已提供明确基准':'缺少明确基准；结论未知，不能声称无遗漏');
  if(!c){line(box,'状态',coverage?.reason||'未提供覆盖核对');return box;}
  line(box,'核对状态',stateText(c.status));line(box,'应覆盖范围',scopeText(c.expected_scope));line(box,'已覆盖范围',scopeText(c.covered_scope));line(box,'缺失范围',scopeText(c.missing_scope));
  for(const f of c.findings||[])line(box,f.code||'查漏发现',f.reason||'未提供说明');return box;
 }
 function renderDetail(entry){
  detailPane.replaceChildren();clearMap('本项无可靠图面定位；保留完整原文和依据。');if(!entry){detailPane.append(el('p','没有符合筛选的记录。'));return;}
  const item=entry.item;
  if(item){
   const title=el('h2',item.title);detailPane.append(title,rowTable(item));
   const facts=el('div',null,'delivery-facts');line(facts,'清单编号',item.id);line(facts,'本次量',num(item.quantity,item.unit));line(facts,'原量',num(item.original_quantity,item.original_unit||item.unit));line(facts,'清单范围',scopeText(item.scope));line(facts,'覆盖状态',stateText(item.coverage?.state||item.coverage));
   for(const [k,v] of Object.entries(item.dimensions||{}))line(facts,k,Array.isArray(v)?v.join('、'):v);
   if(item.display){if(typeof item.display==='string')line(facts,'清单原文',item.display);else for(const [k,v] of Object.entries(item.display))line(facts,k,typeof v==='object'?JSON.stringify(v):v);}
   detailPane.append(facts,comparisonDetails(item),coverageDetails(item));for(const r of itemRows(item))detailPane.append(rowDetails(r));
   const focused=rowsById.get(selectedRow);if(focused&&itemRows(item).includes(focused)){const keys=validKeys(focused.member_keys);if(keys.length)locate(keys,focused.state==='current'?'当前采用成员':'参考定位');else clearMap('本条明细无可靠图面对象；数量及来源依据保留，已清除上次图面。');}else locateItem(item);return;
  }
  if(entry.finding){const f=entry.finding;detailPane.append(el('h2','独立覆盖查漏 · '+(f.code||f.id)));line(detailPane,'状态',stateText(f.status));line(detailPane,'说明',f.reason);line(detailPane,'清单项',f.item_id||(f.item_ids||[]).join('、')||'全局');line(detailPane,'范围',scopeText(f.scope));line(detailPane,'关联明细',(f.row_ids||[]).join('、')||'无');line(detailPane,'关联对象',(f.object_ids||[]).join('、')||'无');return;}
  if(entry.node){const walk=(node,parent='')=>{detailPane.append(missingCard(node,parent));for(const child of node.children||[])walk(child,node.title);};walk(entry.node);return;}
  if(entry.row){const r=entry.row;detailPane.append(rowDetails(r));if(r.state==='current'||r.state==='reference'){const keys=validKeys(r.member_keys);if(keys.length)locate(keys,r.state==='current'?'当前采用成员':'参考定位');else clearMap('本条明细无可靠图面对象；数量及来源依据保留，已清除上次图面。');}return;}
  if(entry.decision){const t=entry.decision,card=el('article',null,'delivery-card');line(card,'真实业务取舍',t.reason);card.append(button('填写此项业务意见',()=>{if(audit){panel.hidden=true;$('action-message').hidden=false;audit.apply('mode=resolve&'+(t.kind==='common'?'common':'issue')+'='+encodeURIComponent(t.id));$('mode-tabs').hidden=true;}}));detailPane.append(card);return;}
  const card=el('article',null,'delivery-card historical');card.append(el('h3',entry.title));line(card,'适用状态',entry.value?.applicability_state||'历史');line(card,'说明',entry.value?.applicability_reason||'');line(card,'意见',entry.value?.confirmed_result||entry.value?.verdict||'');line(card,'原因',entry.value?.reason||'');
  const details=el('details');details.append(el('summary','完整历史与快照'),el('pre',JSON.stringify(entry.value,null,2)));card.append(details);detailPane.append(card);
 }
 function entries(){
  if(tab==='quantity'||tab==='comparison'||tab==='coverage'){const out=items.map(item=>({id:item.id,title:item.title,item,search:{item,rows:itemRows(item),statistics:statsById.get(item.id),coverage:coverageById.get(item.id)}}));if(tab==='coverage')for(const f of coverage?.findings||[])out.push({id:'finding:'+f.id,title:'查漏发现 · '+(f.code||f.id),finding:f,search:f});return out;}
  if(tab==='rows')return d.quantity_rows.filter(r=>r.state==='current'||r.state==='reference').map(row=>({id:row.id,title:stateText(row.state)+' · '+row.title,row,search:row}));
  if(tab==='missing'){const out=[];const walk=(node,parent='')=>{out.push({id:node.id,title:(parent?parent+' / ':'')+node.title,node,search:node});for(const child of node.children||[])walk(child,node.title);};d.missing_groups.forEach(g=>walk(g));return out;}
  if(tab==='decision')return [...d.decision_targets].sort((a,b)=>(a.kind==='common'?0:1)-(b.kind==='common'?0:1)).map(t=>({id:t.kind+'|'+t.id,title:t.reason,decision:t,search:t}));
  const out=d.quantity_rows.filter(r=>r.state==='historical'||r.state==='retired').map(row=>({id:row.id,title:row.title,row,search:row}));
  for(const [key,value] of Object.entries(feedback.annotations))out.push({id:'annotation:'+key,title:key,value,search:{key,...value}});
  for(const [i,value] of (feedback.historical_delivery_snapshots||[]).entries())out.push({id:'snapshot:'+i,title:'旧交付快照（仅历史）',value,search:value});return out;
 }
 function filtered(){
  const ids=slice!=='all'?new Set((statistics?.slices||[]).find(x=>x.id===slice)?.item_ids||[]):null;
  return entries().filter(e=>(!query||JSON.stringify(e.search).toLowerCase().includes(query))&&(!e.item||!ids||ids.has(e.item.id))&&(!e.item||!dimension||JSON.stringify(e.item.dimensions||{})===dimension));
 }
 function renderRows(reset=false){
  listBox.replaceChildren();pager.replaceChildren();const all=filtered(),pages=Math.max(1,Math.ceil(all.length/pageSize));page=Math.max(0,Math.min(page,pages-1));const shown=all.slice(page*pageSize,(page+1)*pageSize);
  counter.textContent='完整记录 '+entries().length+' 项 · 筛选 '+all.length+' 项 · 第 '+(page+1)+' / '+pages+' 页';
  const headers=(tab==='quantity'||tab==='comparison'||tab==='coverage')?['清单名称 / 规格','单位','本次量','原量','可比差额']:['记录','数量 / 范围'];const t=table(headers);listBox.append(t.table);
  const active=!reset&&shown.find(e=>e.id===selected)||shown[0];if(reset||active?.id!==selected)selectedRow=null;selected=active?.id||null;
  for(const entry of shown){const tr=el('tr',null,'delivery-list-row'+(entry.id===selected?' active':''));tr.dataset.itemId=entry.id;tr.tabIndex=0;tr.setAttribute('role','button');tr.setAttribute('aria-selected',String(entry.id===selected));
   if(entry.item){const item=entry.item,s=statsById.get(item.id),name=el('td');name.append(el('strong',item.title));if(item.display?.specification)name.append(el('small',item.display.specification));name.append(el('small',item.id));if(item.legacy&&item.reference_row_ids.length)name.append(el('small','参考值；禁止默认相加'));const value=item.legacy&&item.reference_row_ids.length?'参考 '+num(rowsById.get(item.reference_row_ids[0])?.quantity,item.unit):num(item.quantity,item.unit);tr.append(name,el('td',item.unit),el('td',value),el('td',num(item.original_quantity,item.original_unit||item.unit)),el('td',s?num(s.delta,item.unit):'未提供'));}
   else tr.append(el('td',entry.title),el('td',entry.row?num(entry.row.quantity,entry.row.unit):entry.node?scopeText(entry.node.scope):entry.value?.confirmed_result||entry.value?.verdict||'只读记录'));
   tr.onclick=()=>{selected=entry.id;selectedRow=null;for(const r of t.body.children){r.className='delivery-list-row'+(r.dataset.itemId===entry.id?' active':'');r.setAttribute('aria-selected',String(r.dataset.itemId===entry.id));}renderDetail(entry);};tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tr.onclick();}};t.body.append(tr);
  }
  if(!shown.length)listBox.append(el('p',tab==='missing'?'本次没有符合筛选的外部资料缺件。':'没有符合筛选的记录。'));
  const prev=button('上一页',()=>{page--;renderRows(true);}),next=button('下一页',()=>{page++;renderRows(true);});prev.disabled=page===0;next.disabled=page===pages-1;pager.append(prev,el('span',(page+1)+' / '+pages),next);renderDetail(active);
 }
 function overview(){
  summary.replaceChildren();summary.append(el('strong','完整数量概览'));
  if(statistics){summary.append(el('span','清单 '+statistics.total_items+' 项；筛选切片可能重叠，不跨单位合计数量。'));
   for(const u of statistics.by_unit||[])summary.append(el('span',u.unit+'：'+u.total_items+' 项', 'delivery-chip'));
   for(const s of statistics.slices||[]){const b=button(s.title+' '+s.count,()=>{slice=s.id;tab='comparison';page=0;selected=null;show();},'delivery-chip'+(slice===s.id?' on':''));b.dataset.slice=s.id;summary.append(b);}
  }else{const units=new Map();for(const item of items)units.set(item.unit,(units.get(item.unit)||0)+1);summary.append(el('span','清单 '+items.length+' 项；主明细 '+d.quantity_rows.filter(r=>r.state==='current').length+' 条；参考 '+d.quantity_rows.filter(r=>r.state==='reference').length+' 条。'));
   for(const [u,n] of units)summary.append(el('span',u+'：'+n+' 项','delivery-chip'));summary.append(el('span','未提供可比统计；不推断差额或漏项。'));
  }
  const c=button('独立覆盖查漏 · '+(coverage?stateText(coverage.status):'未提供基准'),()=>{tab='coverage';slice='all';page=0;show();},'delivery-chip');c.dataset.action='coverage';summary.append(c);
 }
 function show(){
  panel.hidden=false;$('layout').hidden=true;$('audit').hidden=true;$('audit-top').hidden=true;$('mode-tabs').hidden=true;$('action-message').hidden=true;
  mapObserver?.disconnect?.();panel.replaceChildren();panel.className='delivery delivery-workbench';const boundary=el('div',adoptionText+(d.projection_provenance?.notice?'\n'+d.projection_provenance.notice:''),'delivery-boundary');panel.append(boundary);summary=el('div',null,'delivery-overview');panel.append(summary);overview();
  nav=el('div',null,'delivery-nav');for(const [key,title] of [['quantity','工程量'],['rows','全部数量明细'],['missing','资料缺口'],['comparison','可比偏差'],['coverage','覆盖查漏'],...(d.decision_targets.length?[['decision','业务取舍']]:[]),['history','历史记录']])nav.append(button(title,()=>{tab=key;page=0;query='';slice='all';dimension='';selected=null;show();},tab===key?'on':''));panel.append(nav);
  body=el('div',null,'delivery-body');listPane=el('aside',null,'delivery-list-pane');const filters=el('div',null,'delivery-filters');search=el('input');search.placeholder='搜索完整清单、明细与依据';search.setAttribute('aria-label','搜索完整交付数据');search.value=query;search.oninput=()=>{query=search.value.toLowerCase();page=0;renderRows(true);};filters.append(search);
  if(tab==='quantity'||tab==='comparison'||tab==='coverage'){
   selectSlice=el('select');selectSlice.setAttribute('aria-label','完整统计筛选');const available=statistics?.slices||[{id:'all',title:'全部清单',count:items.length}];for(const s of available){const option=el('option',s.title+'（'+s.count+'）');option.value=s.id;selectSlice.append(option);}selectSlice.value=slice;selectSlice.onchange=()=>{slice=selectSlice.value;page=0;overview();renderRows(true);};filters.append(selectSlice);
   const dimensions=Array.from(new Set(items.map(i=>JSON.stringify(i.dimensions||{})))).filter(x=>x!=='{}');if(dimensions.length){selectDimension=el('select');selectDimension.setAttribute('aria-label','楼栋系统等范围筛选');const all=el('option','全部楼栋 / 系统');all.value='';selectDimension.append(all);for(const v of dimensions){const option=el('option',Object.entries(JSON.parse(v)).map(([k,x])=>k+'：'+x).join(' · '));option.value=v;selectDimension.append(option);}selectDimension.value=dimension;selectDimension.onchange=()=>{dimension=selectDimension.value;page=0;renderRows(true);};filters.append(selectDimension);}
  }
  counter=el('div',null,'delivery-counter');listBox=el('div',null,'delivery-list-scroll');pager=el('div',null,'delivery-pager');listPane.append(filters,counter,listBox,pager);body.append(listPane);
  workspace=el('section',null,'delivery-workspace'+(fullscreen?' delivery-fullscreen':''));mapPane=el('div',null,'delivery-map-pane');const toolbar=el('div',null,'delivery-map-toolbar');mapHead=el('strong','图纸定位');
  const previous=button('上一处',()=>{if(mapKeys.length)locate(mapKeys,mapRole,mapKeys[(mapKeys.indexOf(focusKey)-1+mapKeys.length)%mapKeys.length]);});
  const next=button('下一处',()=>{if(mapKeys.length)locate(mapKeys,mapRole,mapKeys[(mapKeys.indexOf(focusKey)+1)%mapKeys.length]);});
  const all=button('整项图元',()=>{const item=itemById.get(selected);if(item)locateItem(item);else clearMap('当前记录没有当前采用图元。');});all.dataset.action='item-members';
  const reset=button('恢复默认比例',()=>setRatio(52));reset.dataset.action='reset-ratio';const expand=button(fullscreen?'退出大图':'放大图纸',()=>{fullscreen=!fullscreen;workspace.classList.toggle('delivery-fullscreen',fullscreen);expand.textContent=fullscreen?'退出大图':'放大图纸';drawMap();});expand.dataset.action='fullscreen';fullscreenButton=expand;
  const saveImage=button('保存当前图',()=>mapCanvas.toBlob(blob=>{if(!blob)return;const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=filename()+'_当前定位.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));toolbar.append(mapHead,previous,next,all,reset,expand,saveImage);
  const mapArea=el('div',null,'delivery-map-area');mapCanvas=el('canvas',null,'delivery-map-canvas');mapCanvas.setAttribute('aria-label','当前明细图元定位');mapState=el('div',null,'delivery-map-state');mapState.setAttribute('role','status');mapArea.append(mapCanvas,mapState);mapPane.append(toolbar,mapArea);
  splitter=el('div',null,'delivery-row-splitter');splitter.tabIndex=0;splitter.setAttribute('role','separator');splitter.setAttribute('aria-label','调整图纸与明细高度，方向键调节，Home恢复默认');splitter.setAttribute('aria-orientation','horizontal');splitter.setAttribute('aria-valuemin','25');splitter.setAttribute('aria-valuemax','75');splitter.title='拖动调整上下比例；方向键每次调整3%；Home或双击恢复默认';splitter.append(el('span','拖动调节图纸 / 明细'));
  splitter.onkeydown=e=>{if(['ArrowUp','ArrowDown','Home'].includes(e.key)){e.preventDefault();setRatio(e.key==='Home'?52:ratio+(e.key==='ArrowUp'?-3:3));}};splitter.ondblclick=()=>setRatio(52);
  splitter.onpointerdown=e=>{e.preventDefault();splitter.setPointerCapture(e.pointerId);splitter.classList.add('dragging');};splitter.onpointermove=e=>{if(!splitter.className.split(' ').includes('dragging'))return;const rect=workspace.getBoundingClientRect();setRatio((e.clientY-rect.top)/Math.max(1,rect.height)*100);};splitter.onpointerup=()=>splitter.classList.remove('dragging');splitter.onpointercancel=()=>splitter.classList.remove('dragging');
  detailPane=el('div',null,'delivery-details');workspace.append(mapPane,splitter,detailPane);body.append(workspace);panel.append(body);setRatio(ratio,false);renderRows();
  if(root.ResizeObserver){mapObserver=new root.ResizeObserver(()=>drawMap());mapObserver.observe(mapArea);}
 }
 root.addEventListener?.('keydown',e=>{if(e.key==='Escape'&&fullscreen){fullscreen=false;workspace.classList.remove('delivery-fullscreen');fullscreenButton.textContent='放大图纸';drawMap();}});
 if(d.mode==='decisions')audit=root.QtoReviewAudit.attach({data,core,getFeedback:()=>feedback,backup,status:message,draw(){},resize(){},locate(){return false;},review(){},selectGroup(){return false;},selectObject(){return false;},scene(){return false;}});
 show();message(d.decision_targets.length?'仅填写已声明的真实业务取舍；意见经专业回算后才更新数量。':'只读交付；没有数量审核表单。');
 return root.QtoReviewDelivery.active={show,getFeedback:()=>feedback,tab:()=>tab,state:()=>({tab,page,selected,selectedRow,mapKeys:[...mapKeys],focusKey,ratio})};
}
root.QtoReviewDelivery={attach};
})(globalThis);
