(function(root){
'use strict';
// Comparison / resolution modes for qto-review-view/1.1 (contract docs/维护/复核页审核视图契约.md, sections 5-9 and 14).
// The page never computes engineering quantities: every number shown comes from the view as the professional wrote it.
const LEVELS=['确认量','规则推算量','方案暂估量','未决量'],HANDLERS=['decide','confirm','assumed','closed'],TAGS=['有未决量','以暂估为主','缺图'];
const MODES={resolve:'待解决',audit:'对照审核',extras:'清单外新增',review:'图面复核'},ALIASES={issues:'resolve'};
const VERDICT_LABELS={item:{同意:'同意',要改:'要改',待定:'待定'},issue:{同意:'按现做法',要改:'不按现做法',待定:'暂不定'}};
const PAGE=60;
function attach(api){
 const data=api.data,c=data.comparison;if(!c)return null;
 const byId=id=>document.getElementById(id),core=api.core;
 const el=(tag,cls,txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;};
 const btn=(txt,fn,cls)=>{const b=el('button',cls,txt);b.onclick=fn;return b;};
 const items=c.items,third=items.filter(i=>i.kind==='third_party'),extras=items.filter(i=>i.kind==='extra'),issues=c.issues;
 const itemMap=new Map(items.map(i=>[i.id,i])),issueMap=new Map(issues.map(q=>[q.id,q])),objects=new Map(data.objects.map(o=>[o.catalog_key,o]));
 const tp=c.display_terms?.third_party||'第三方',term=s=>String(s).split('第三方').join(tp);
 const dims=c.dimensions,overDim=dims.find(d=>d.role==='overview'),sectDim=dims.find(d=>d.role==='section'),filterDims=dims.filter(d=>d.role==='filter');
 const dimTitle=(d,v)=>(d.values.find(x=>x.id===v)||{}).title||v;
 const hasScenes=data.scenes.length>0,mapSection=byId('map-section'),layout=byId('layout'),audit=byId('audit');
 let mode=null,current=null,source=c.sources[0]?.id,overview='全部',mapState='auto',located=[],at=0,routeNote='';
 const open=new Set(['decide','confirm','assumed',...TAGS]),folded=new Set(),shown=Object.create(null);
 const F={conclusion:'全部',alert:false,alertRate:Math.round((c.thresholds.difference_rate_alert??0.2)*1000)/10,issue:false,estimate:false,corrected:false,disagree:false,uncertain:false,sort:'rate',dims:Object.create(null)};
 const hay=new Map();const haystack=x=>{let h=hay.get(x);if(h==null){h=JSON.stringify(x).toLowerCase();hay.set(x,h);}return h;};
 const num=v=>v==null?'—':String(Number(Number(v).toPrecision(12)));
 const pct=r=>r==null?'—':(r>0?'+':'')+(r*100).toFixed(1)+'%';
 const seg=(i,sid=source)=>i.third_parties.find(s=>s.source_id===sid);
 const nameOf=i=>i.name||i.third_parties[0]?.rows[0]?.name||i.id,featOf=i=>i.features||i.third_parties[0]?.rows[0]?.features||'';
 const handlerLabel=h=>c.handler_labels[h]||h,openIssue=q=>q.handler!=='closed';
 const inOverview=x=>{if(overview==='全部'||!overDim)return true;const v=(x.dimensions||{})[overDim.id];return Array.isArray(v)?v.includes(overview):v===overview;};
 const query=()=>byId('audit-search').value.trim().toLowerCase(),searched=x=>{const q=query();return !q||haystack(x).includes(q);};
 const isAlert=s=>s&&s.difference?.rate!=null&&Math.abs(s.difference.rate)*100>F.alertRate;
 const feedback=()=>api.getFeedback(),keyOf=x=>(issueMap.get(x.id)===x?'问题|':'对照条目|')+x.id;
 const noteOf=x=>{const f=feedback(),k=keyOf(x);return f.drafts[k]||f.annotations[k];};

 // ---------- top: mode tabs, resolve counts, band rule ----------
 const openIssues=issues.filter(openIssue),tagged=t=>items.filter(i=>(i.uncertain||[]).includes(t));
 const available={resolve:true,audit:third.length>0,extras:extras.length>0,review:data.objects.length>0||data.groups.length>0};
 function tabs(){const nav=byId('mode-tabs');nav.hidden=false;nav.replaceChildren();for(const [m,label] of Object.entries(MODES))if(available[m]){const b=btn(label,()=>{setMode(m);route();},m===mode?'on':'');nav.append(b);}}
 function top(){
  const box=byId('resolve-counts');box.replaceChildren();
  for(const h of HANDLERS.slice(0,3)){const n=openIssues.filter(q=>q.handler===h).length;if(n)box.append(chip(handlerLabel(h)+' '+n,()=>focusGroup(h),'chip warn'));}
  for(const t of TAGS){const n=tagged(t).length;if(n)box.append(chip(t+' '+n,()=>focusGroup(t),'chip'));}
  if(!box.children.length)box.append(el('span','minor','没有待解决事项'));
  const by=c.thresholds.consistent_band_by_unit||{},band=c.thresholds.consistent_band,exact=Object.entries(by).filter(([,b])=>b===0).map(([u])=>u),other=Object.entries(by).filter(([,b])=>b!==0);
  byId('band-rule').hidden=!third.length;
  byId('band-rule').textContent='一致：差额率绝对值 ≤ '+(band==null?'—':(band*100).toFixed(1).replace(/\.0$/,'')+'%')+(other.length?'（'+other.map(([u,b])=>u+' ≤ '+(b*100)+'%').join('、')+'）':'')+(exact.length?'；'+exact.join('、')+' 须相等':'')+' · 差异超阈值筛选 '+F.alertRate+'%';
  byId('route-message').hidden=!routeNote;byId('route-message').textContent=routeNote;
 }
 function chip(text,fn,cls){const b=btn(text,fn,cls);return b;}
 function focusGroup(g){open.add(g);setMode('resolve');current=null;render();}

 // ---------- filters ----------
 function select(label,options,value,fn){const w=el('label');w.append(label);const s=el('select');for(const [v,t] of options){const o=el('option',null,t);o.value=v;s.append(o);}s.value=value;s.onchange=()=>fn(s.value);w.append(s);return w;}
 function check(label,value,fn){const w=el('label'),cb=el('input');cb.type='checkbox';cb.checked=value;cb.onchange=()=>fn(cb.checked);w.append(cb,label);return w;}
 function filters(){
  const box=byId('audit-filters');box.replaceChildren();
  if(overDim)box.append(select(overDim.title,[['全部','全部'],...overDim.values.map(v=>[v.id,v.title])],overview,v=>{overview=v;refresh();}));
  if(mode==='audit'){
   if(c.sources.length>1)box.append(select('对照来源',c.sources.map(s=>[s.id,s.title]),source,v=>{source=v;refresh();}));
   const seen=Array.from(new Set(third.map(i=>seg(i)?.conclusion).filter(Boolean)));
   box.append(select('结论',[['全部','全部'],...seen.map(x=>[x,term(x)])],F.conclusion,v=>{F.conclusion=v;refresh();}));
   box.append(select('排序',[['rate','差额率（绝对值）'],['value','差额（绝对值）'],['id','编号']],F.sort,v=>{F.sort=v;refresh();}));
   const rate=el('input');rate.type='number';rate.value=F.alertRate;rate.setAttribute('aria-label','差异超阈值百分比');rate.oninput=()=>{const v=Number(rate.value);if(Number.isFinite(v)&&v>=0){F.alertRate=v;top();refresh();}};
   const alert=check('差异超阈值 %',F.alert,v=>{F.alert=v;refresh();});alert.append(rate);box.append(alert);
   box.append(check('有问题',F.issue,v=>{F.issue=v;refresh();}),check('以暂估为主',F.estimate,v=>{F.estimate=v;refresh();}),check(term('第三方条目有误已修正'),F.corrected,v=>{F.corrected=v;refresh();}),check('有不确定内容',F.uncertain,v=>{F.uncertain=v;refresh();}));
   if(c.sources.length>1)box.append(check('来源间结论不一致',F.disagree,v=>{F.disagree=v;refresh();}));
  }
  if(mode!=='resolve')for(const d of filterDims)box.append(select(d.title,[['全部','全部'],...d.values.map(v=>[v.id,v.title])],F.dims[d.id]||'全部',v=>{F.dims[d.id]=v;refresh();}));
 }
 const dimsOK=i=>filterDims.every(d=>!F.dims[d.id]||F.dims[d.id]==='全部'||i.dimensions[d.id]===F.dims[d.id]);
 function auditItems(){
  let out=third.filter(i=>{const s=seg(i);return s&&inOverview(i)&&dimsOK(i)&&searched(i)&&(F.conclusion==='全部'||s.conclusion===F.conclusion)&&(!F.alert||isAlert(s))&&(!F.issue||i.issue_ids.length)&&(!F.estimate||i.estimate_dominant)&&(!F.corrected||s.third_party_corrected)&&(!F.uncertain||(i.uncertain||[]).length)&&(!F.disagree||new Set(i.third_parties.map(x=>x.conclusion)).size>1);});
  const abs=v=>v==null?Infinity:-Math.abs(v),key={rate:i=>abs(seg(i).difference?.rate),value:i=>abs(seg(i).difference?.value),id:()=>0}[F.sort];   // no difference sorts last
  return F.sort==='id'?out:out.map((i,n)=>[key(i),n,i]).sort((a,b)=>a[0]-b[0]||a[1]-b[1]).map(x=>x[2]);
 }

 // ---------- list ----------
 function row(x,title,sub,extra){
  const b=el('button','item');b.dataset.key=keyOf(x);if(current===x)b.classList.add('active');
  const strong=el('strong',null,title);b.append(strong);if(noteOf(x))strong.append(el('span','badge','有意见'));
  for(const s of sub)b.append(s);if(extra)b.append(extra);b.onclick=()=>{show(x);route();};return b;
 }
 function bar(i,s){
  const firm=(i.ours.levels['确认量']||0)+(i.ours.levels['规则推算量']||0),est=i.ours.levels['方案暂估量']||0,q=s&&s.unit===i.ours.unit?s.quantity||0:0,max=Math.max(firm+est,q,1e-9),w=v=>Math.max(0,v/max*100).toFixed(1)+'%';
  const box=el('div','bars');const a=el('div','bar2'),t=el('span','b-tp');t.style.width=w(q);a.append(t);const b=el('div','bar2'),f=el('span','b-firm'),e=el('span','b-est');f.style.width=w(firm);e.style.width=w(est);b.append(f,e);box.append(a,b);return box;
 }
 function itemRow(i){
  const subs=[];
  if(i.kind==='extra')subs.push(el('small',null,'本次 '+num(i.ours.total)+' '+i.ours.unit+(i.ours.levels['未决量']!=null||(i.uncertain||[]).includes('有未决量')?' · 另有未决':'')));
  else for(const s of (c.sources.length>1?c.sources.map(x=>seg(i,x.id)||{source_id:x.id,missing:true}):[seg(i)||i.third_parties[0]]))
   subs.push(el('small',null,(c.sources.length>1?(c.sources.find(x=>x.id===s.source_id)?.title||s.source_id)+'：':'')+(s.missing?'无对应':tp+' '+num(s.quantity)+(s.unit!==i.ours.unit?' '+s.unit:'')+' → 本次 '+num(i.ours.total)+' '+i.ours.unit+' · '+term(s.conclusion||'—')+(s.difference?.rate!=null?' '+pct(s.difference.rate):'')+(s.third_party_corrected?' · 已修正':''))));
  const tags=el('small');for(const t of i.uncertain||[])tags.append(el('span','badge warn',t));if(i.uncertain_unlinked)tags.append(el('span','badge bad','未挂问题'));if(i.issue_ids.length)tags.append(el('span','badge','问题 '+i.issue_ids.length));if(tags.children.length)subs.push(tags);
  return row(i,i.id+' · '+nameOf(i),subs,i.kind==='third_party'&&seg(i)?bar(i,seg(i)):null);
 }
 function issueRow(q){return row(q,q.id+' · '+q.title,[el('small',null,handlerLabel(q.handler)+(q.status?' · '+q.status:'')+' · 涉及条目 '+q.item_ids.length+(q.current_practice?'':' · 未写现做法'))]);}
 // Resolve groups start open except closed issues; list sections (by section dimension) start open.
 const isOpen=k=>HANDLERS.includes(k)||TAGS.includes(k)?open.has(k):!folded.has(k);
 function section(key,label,list,render){
  const box=byId('audit-list'),head=btn((isOpen(key)?'▾ ':'▸ ')+label+' · '+list.length,()=>{const set=HANDLERS.includes(key)||TAGS.includes(key)?open:folded;set.has(key)?set.delete(key):set.add(key);refresh();},'sect');box.append(head);
  if(!isOpen(key))return;const n=shown[key]||PAGE;for(const x of list.slice(0,n))box.append(render(x));
  if(list.length>n)box.append(btn('再显示 '+Math.min(PAGE,list.length-n)+' 条（共 '+list.length+'）',()=>{shown[key]=n+PAGE;refresh();},'more'));
 }
 function bySection(list,key){
  if(!sectDim){section(key,'全部',list,itemRow);return;}
  const groups=new Map();for(const i of list){const v=i.dimensions[sectDim.id]||'（未分）';if(!groups.has(v))groups.set(v,[]);groups.get(v).push(i);}
  const order=new Map(sectDim.values.map((v,n)=>[v.id,n]));
  for(const [v,g] of [...groups].sort((a,b)=>(order.get(a[0])??1e9)-(order.get(b[0])??1e9)))section(key+'|'+v,dimTitle(sectDim,v),g,itemRow);
 }
 function list(){
  const box=byId('audit-list');box.replaceChildren();let total=0;
  if(mode==='resolve'){
   for(const h of HANDLERS){const g=issues.filter(q=>q.handler===h&&inOverview(q)&&searched(q));total+=g.length;if(g.length)section(h,handlerLabel(h),g,issueRow);}
   for(const t of TAGS){const g=tagged(t).filter(i=>inOverview(i)&&searched(i));total+=g.length;if(g.length)section(t,t,g,itemRow);}
   if(data.groups.length)box.append(btn('另有 '+data.groups.length+' 个图面问题组待复核 → 图面复核',()=>{setMode('review');route();},'more'));
   if(!total)box.append(el('div','hint','没有待解决事项'));
  }else if(mode==='audit'){const g=auditItems();total=g.length;bySection(g,'audit');}
  else{const g=extras.filter(i=>inOverview(i)&&dimsOK(i)&&searched(i));total=g.length;bySection(g,'extras');}
  byId('audit-counter').textContent='筛选 '+total+' 条'+(mode==='resolve'?'（问题与还没算实的条目）':'')+'；完整数据保留，未截取。';
 }

 // ---------- detail ----------
 function table(head,rows){const t=el('table','qty'),tr=el('tr');for(const h of head)tr.append(el('th',null,h));t.append(tr);for(const r of rows){const x=el('tr');for(const v of r){const td=el('td');if(v&&typeof v==='object')td.append(v);else td.textContent=v==null?'':String(v);x.append(td);}t.append(x);}return t;}
 function link(text,fn){const a=btn(text,fn,'link');return a;}
 const itemLink=id=>link(id,()=>{const i=itemMap.get(id);if(i){setMode(i.kind==='extra'?'extras':'audit');show(i);route();}});
 const issueLink=id=>link(id+' · '+(issueMap.get(id)?.title||''),()=>{const q=issueMap.get(id);if(q){setMode('resolve');show(q);route();}});
 function para(label,text){const p=el('p','para');p.append(el('b',null,label+'：'),el('span',null,text));return p;}
 function overviewCards(){
  const box=el('div','cards'),values=[['全部','全部'],...(overDim?overDim.values.map(v=>[v.id,v.title]):[])];
  for(const [v,t] of values){
   const keep=overview;overview=v;const its=items.filter(inOverview),qs=openIssues.filter(inOverview),th=its.filter(i=>i.kind==='third_party');overview=keep;
   const count=f=>th.filter(i=>{const s=seg(i);return s&&f(s,i);}).length,concl={};for(const i of th){const s=seg(i);if(s?.conclusion)concl[s.conclusion]=(concl[s.conclusion]||0)+1;}
   const card=btn('',()=>{overview=v;current=null;render();route();},'card'+(v===overview?' on':''));card.append(el('b',null,t));
   card.append(el('div',null,'待解决：问题 '+qs.length+' · '+TAGS.map(x=>x+' '+its.filter(i=>(i.uncertain||[]).includes(x)).length).join(' · ')));
   if(third.length)card.append(el('div','minor','对照：条目 '+th.length+' · '+Object.entries(concl).map(([k,n])=>term(k)+' '+n).join(' · ')+' · 超阈值 '+count(isAlert)+' · '+term('第三方无量但本次算出')+' '+count((s,i)=>s.conclusion==='第三方无量'&&i.ours.total!=null&&i.ours.total!==0)+' · 清单外 '+its.filter(i=>i.kind==='extra').length));
   box.append(card);
  }
  return box;
 }
 function levelsTable(i){
  const L=i.ours.levels;return table([...LEVELS.map(x=>x.replace('量','')),'合计（不含未决）','单位'],[[...LEVELS.map(x=>num(L[x])),num(i.ours.total),i.ours.unit]]);
 }
 function itemDetail(i){
  const box=byId('audit-detail');box.append(el('h2',null,i.id+' · '+nameOf(i)));
  if(featOf(i))box.append(el('div','minor',featOf(i)));
  const tags=el('div');for(const t of i.uncertain||[])tags.append(el('span','badge warn',t));if(i.uncertain_unlinked)tags.append(el('span','badge bad','未挂问题'));for(const d of dims){const v=i.dimensions[d.id];if(v!=null)tags.append(el('span','badge',d.title+' '+dimTitle(d,v)));}box.append(tags);
  if(i.third_parties.length){
   box.append(el('h3',null,tp+'数量'));
   const rows=[];for(const s of i.third_parties){const src=c.sources.find(x=>x.id===s.source_id);for(const [n,r] of s.rows.entries())rows.push([n?'':(src?.title||s.source_id),[r.seq,r.code].filter(Boolean).join(' '),r.name||'',r.features||'',r.unit,num(r.quantity),n?'':num(s.quantity)+' '+s.unit,n?'':num(s.difference?.value),n?'':pct(s.difference?.rate),n?'':term(s.conclusion||'—')+(s.third_party_corrected?'；已修正':'')]);}
   box.append(table(['来源','序号/编码','名称','特征','单位','行数量','段数量','差额','差额率','结论'],rows));
   for(const s of i.third_parties){
    const pre=c.sources.length>1?(c.sources.find(x=>x.id===s.source_id)?.title||s.source_id)+' ':'';
    for(const [k,label] of [['disposition_note','处置说明'],['correction_note','修正说明'],['missing_drawing_note','缺图说明'],['unit_mismatch_note','单位不同'],['quantity_note','部分行无量']])if(s[k]&&s[k]!==i.ours.note)box.append(para(pre+label,s[k]));
    if(s.search_evidence){const e=s.search_evidence;box.append(para(pre+'检索依据',['图纸 '+[].concat(e.drawings).join('、'),'对象 '+[].concat(e.targets).join('、'),'方法 '+e.method,'命中 '+e.hits,e.unread?'未读 '+e.unread:'',e.uncovered?'未查 '+e.uncovered:'',e.record_ref?'记录 '+e.record_ref:''].filter(Boolean).join('；')));}
    if(s.redirect_to.length){const p=el('p','para');p.append(el('b',null,pre+'去向：'));for(const t of s.redirect_to)p.append(itemLink(t));box.append(p);}
   }
  }
  box.append(el('h3',null,'本次数量'+(i.ours.quantity_name?' · '+i.ours.quantity_name:'')));box.append(levelsTable(i));
  if(i.ours.note)box.append(para('本次说明',i.ours.note));
  if(i.issue_ids.length){const p=el('p','para');p.append(el('b',null,'挂在本条目上的问题：'));for(const q of i.issue_ids)p.append(issueLink(q));box.append(p);}
  if(i.detail_rows.length){
   box.append(el('h3',null,'明细行（取消勾选的记为本条意见的例外）'));
   const note=noteOf(i),ex=new Set(note?.excluded_detail_row_ids||[]);excluded=ex;
   const rows=i.detail_rows.map(r=>{const cb=el('input');cb.type='checkbox';cb.checked=!ex.has(r.id);cb.setAttribute('aria-label','纳入 '+r.id);cb.onchange=()=>{cb.checked?ex.delete(r.id):ex.add(r.id);stash();};
    const ms=r.member_keys.map(k=>objects.get(k)).filter(Boolean),m=ms.length?btn('成员 '+ms.length,()=>members(r,ms),'link'):'';
    return [cb,r.id,r.area||'',r.level.replace('量',''),num(r.quantity)+' '+r.unit,r.basis||'',r.drawing||'',r.locator||'',r.relation||'',r.note||'',m];});
   box.append(table(['纳入','明细','楼层区域','层级','数量','依据','图纸','定位','关系','备注','成员'],rows));
   box.append(memberBox=el('div','members'));
  }
 }
 let memberBox=null,excluded=new Set();
 function members(r,ms){
  memberBox.replaceChildren();const spec=c.member_tables.find(t=>t.id===r.member_table),cols=spec?spec.columns:[{key:'id',title:'编号'},{key:'title',title:'名称'},{key:'status',title:'状态'},{key:'source',title:'来源'}];
  const val=(o,k)=>k.startsWith('attributes.')?o.attributes?.[k.slice(11)]:k.startsWith('quantities.')?(o.quantities.find(q=>q.name===k.slice(11))||{}).value:o[k];
  memberBox.append(el('h3',null,r.id+' 的成员（'+ms.length+'）'+(r.aggregation==='sum_members'?' · 本行 = 成员“'+r.member_quantity_name+'”之和':r.aggregation==='declared'?' · '+(r.aggregation_reason||''):'')));
  memberBox.append(table([...cols.map(x=>x.title),'回图'],ms.map(o=>[...cols.map(x=>{const v=val(o,x.key);return v==null?'':typeof v==='number'?num(v):String(v);}),o.kind!=='unlocated'&&hasScenes?btn('回图',()=>{at=Math.max(0,located.indexOf(o));mapState='open';map();},'link'):'未定位'])));
 }
 function issueDetail(q){
  const box=byId('audit-detail');box.append(el('h2',null,q.id+' · '+q.title));
  const tags=el('div');tags.append(el('span','badge warn',handlerLabel(q.handler)));if(q.status)tags.append(el('span','badge',q.status));for(const d of dims){const v=q.dimensions[d.id];if(v!=null)tags.append(el('span','badge',d.title+' '+[].concat(v).map(x=>dimTitle(d,x)).join('、')));}box.append(tags);
  box.append(para('问题',q.text));if(q.impact_text)box.append(para('影响',q.impact_text));if(q.impact)box.append(para('影响量',num(q.impact.value)+' '+q.impact.unit));
  box.append(para('现在的做法',q.current_practice||'未写现做法'));
  if(q.item_ids.length){
   box.append(el('h3',null,'涉及量（各条目原值，不跨条目求和）'));
   box.append(table(['条目','名称','单位',...LEVELS.map(x=>x.replace('量','')),'合计',tp+'量','结论'],q.item_ids.map(id=>{const i=itemMap.get(id),s=i.kind==='third_party'?seg(i)||i.third_parties[0]:null;return [itemLink(id),nameOf(i),i.ours.unit,...LEVELS.map(x=>num(i.ours.levels[x])),num(i.ours.total),s?num(s.quantity)+(s.unit!==i.ours.unit?' '+s.unit:''):'清单外',s?term(s.conclusion||'—'):''];})));
  }
  if(q.source_refs.length)box.append(table(['原条目','原文'],q.source_refs.map(r=>typeof r==='string'?[r,'']:[r.ref,r.text||''])));
  if(q.group_id){const p=el('p','para');p.append(el('b',null,'相关问题组：'),link(q.group_id,()=>{setMode('review');api.selectGroup(q.group_id);route({group:q.group_id});}));box.append(p);}
 }
 function detail(){
  const box=byId('audit-detail');box.replaceChildren();memberBox=null;
  if(!current){box.append(el('h2',null,'总览'),overviewCards());return;}
  issueMap.get(current.id)===current?issueDetail(current):itemDetail(current);
 }

 // ---------- map (contract 8): auto / open / close; no scenes -> never shown ----------
 function targets(x){
  if(!x)return [];const its=issueMap.get(x.id)===x?x.item_ids.map(id=>itemMap.get(id)).filter(Boolean):[x];
  return Array.from(new Set(its.flatMap(i=>i.detail_rows.flatMap(r=>r.member_keys)))).map(k=>objects.get(k)).filter(o=>o&&o.kind!=='unlocated'&&o.scene_id);
 }
 function map(){
  const slot=byId('audit-map-slot'),bar=byId('audit-mapbar');bar.hidden=!hasScenes;
  for(const [id,s] of [['map-auto','auto'],['map-open','open'],['map-close','close']])byId(id).classList.toggle('on',mapState===s);
  located=targets(current);at=Math.min(at,Math.max(0,located.length-1));
  const visible=hasScenes&&(mapState==='open'||(mapState==='auto'&&located.length>0));slot.hidden=!visible;
  byId('map-note').textContent=!hasScenes?'':located.length?'可定位成员 '+located.length+' 处 · 第 '+(at+1)+' 处':current?'当前目标没有可定位成员'+(mapState==='auto'?'，图面已自动收起':''):'';
  byId('member-prev2').disabled=byId('member-next2').disabled=located.length<2;
  if(visible){api.resize();api.locate(located,located[at]);}
 }
 function step(d){if(!located.length)return;at=(at+d+located.length)%located.length;map();}

 // ---------- editor (contract 9) ----------
 const verdictSelect=byId('audit-verdict');
 function editor(){
  const x=current,isIssue=x&&issueMap.get(x.id)===x,labels=VERDICT_LABELS[isIssue?'issue':'item'];
  verdictSelect.replaceChildren();for(const [v,t] of [['','（不选）'],...Object.entries(labels)]){const o=el('option',null,t);o.value=v;verdictSelect.append(o);}
  for(const id of ['audit-verdict','audit-result','audit-reason'])byId(id).disabled=!x;byId('audit-save').disabled=!x;
  if(!x){byId('audit-editor-title').textContent='先选问题或条目';byId('audit-result').value='';byId('audit-reason').value='';byId('audit-history').textContent='';byId('audit-entry-status').textContent='';byId('audit-excluded').textContent='';return;}
  const r=noteOf(x)||{},f=feedback(),k=keyOf(x);
  byId('audit-editor-title').textContent=(isIssue?'问题：':'条目：')+x.id+' · '+(isIssue?x.title:nameOf(x));
  verdictSelect.value=r.verdict||'';byId('audit-result').value=r.confirmed_result||'';byId('audit-reason').value=r.reason||'';
  byId('audit-result').placeholder=isIssue?'选“不按现做法”时写明应该怎么算；选了快捷结论可留空':'选了快捷结论可留空';
  byId('audit-excluded').textContent=isIssue?'':'明细例外：'+((r.excluded_detail_row_ids||[]).join('、')||'无');
  byId('audit-history').textContent=(r.history||[]).map((h,n)=>(n+1)+'. '+(h.updated_at||'')+' '+(h.verdict?labels[h.verdict]||h.verdict:'')+'\n'+(h.confirmed_result||'')+'\n原因：'+(h.reason||'')).join('\n\n')||'暂无修改历史';
  byId('audit-entry-status').textContent=(r.applicability_state==='stale'||r.applicability_state==='unmatched')?'历史意见待重新核对：'+r.applicability_reason:f.drafts[k]?'当前有草稿，未保存为意见。':f.annotations[k]?'已有保存意见；所属专业复核后才更新成果。':'尚未保存意见。';
 }
 function form(){
  const x=current,isIssue=issueMap.get(x.id)===x,k=keyOf(x),old=feedback().annotations[k]||{};
  const out={...old,result_fingerprint:x.result_fingerprint,applicability_state:'current',applicability_reason:'用户本轮重新保存意见',title:isIssue?x.title:nameOf(x),
   verdict:verdictSelect.value,confirmed_result:byId('audit-result').value,reason:byId('audit-reason').value,entry_origin:'页面草稿，未保存',updated_at:new Date().toISOString(),
   excluded_detail_row_ids:isIssue?[]:x.detail_rows.map(r=>r.id).filter(id=>excluded.has(id))};
  delete out.item_snapshot;delete out.issue_snapshot;
  out[isIssue?'issue_snapshot':'item_snapshot']=isIssue?{issue_id:x.id,result_fingerprint:x.result_fingerprint}:{item_id:x.id,result_fingerprint:x.result_fingerprint};
  return out;
 }
 function stash(){if(!current)return;const k=keyOf(current);feedback().drafts[k]=core.cleanRecord(form(),k,false);api.backup();byId('audit-entry-status').textContent='已暂存草稿；尚未保存为意见。';}
 for(const id of ['audit-result','audit-reason'])byId(id).oninput=stash;verdictSelect.onchange=stash;
 byId('audit-save').onclick=()=>{if(!current)return;try{const k=keyOf(current),f=feedback();f.annotations[k]=core.saveRecord(f.annotations[k],form(),k,new Date().toISOString());delete f.drafts[k];api.backup();editor();list();byId('audit-entry-status').textContent='意见已保存；所属专业复核后才更新成果。';}catch(e){byId('audit-entry-status').textContent=e.message;}};

 // ---------- modes, routing (contract 7) ----------
 function setMode(m){
  m=ALIASES[m]||m;if(!available[m])return false;mode=m;tabs();
  if(m==='review'){audit.hidden=true;layout.hidden=false;if(hasScenes&&mapSection.parentElement!==layout)layout.insertBefore?.(mapSection,byId('review-splitter'));api.review();return true;}
  layout.hidden=true;audit.hidden=false;if(hasScenes)byId('audit-map-slot').append(mapSection);
  if(current&&!fits(current))current=null;filters();render();return true;
 }
 const fits=x=>mode==='resolve'?issueMap.get(x.id)===x||(x.uncertain||[]).length>0:mode==='audit'?x.kind==='third_party':mode==='extras'?x.kind==='extra':false;
 function show(x){current=x;at=0;excluded=new Set(noteOf(x)?.excluded_detail_row_ids||[]);render();}
 function render(){if(mode==='review')return;top();list();detail();editor();map();}
 function refresh(){render();}
 function parseHash(h){const out=Object.create(null);for(const part of String(h||'').replace(/^#/,'').split('&')){if(!part)continue;const n=part.indexOf('='),k=n<0?part:part.slice(0,n);try{out[decodeURIComponent(k)]=n<0?'':decodeURIComponent(part.slice(n+1));}catch(e){out[k]=part.slice(n+1);}}return out;}
 function route(extra){
  const p=[];if(mode)p.push(['mode',mode]);if(current&&mode!=='review')p.push([issueMap.get(current.id)===current?'issue':'item',current.id]);if(overview!=='全部'&&overDim)p.push(['dim',overDim.id+':'+overview]);if(source&&c.sources.length>1&&source!==c.sources[0].id)p.push(['source',source]);
  for(const [k,v] of Object.entries(extra||{}))p.push([k,v]);
  try{globalThis.history?.replaceState?.(null,'','#'+p.map(([k,v])=>k+'='+encodeURIComponent(v)).join('&'));}catch(e){}
 }
 function apply(params){
  const missing=[];
  if(params.source!=null){if(c.sources.some(s=>s.id===params.source))source=params.source;else missing.push('来源 '+params.source);}
  if(params.dim!=null){const [d,...rest]=params.dim.split(':'),v=rest.join(':'),dim=dims.find(x=>x.id===d);if(dim&&dim.values.some(x=>x.id===v)){if(dim===overDim)overview=v;else F.dims[d]=v;}else missing.push('维度 '+params.dim);}
  if(params.mode!=null&&!setMode(params.mode))missing.push('模式 '+params.mode);
  if(params.item!=null){const i=itemMap.get(params.item);if(i){setMode(i.kind==='extra'?'extras':'audit');show(i);}else missing.push('条目 '+params.item);}
  if(params.issue!=null){const q=issueMap.get(params.issue);if(q){setMode('resolve');show(q);}else missing.push('问题 '+params.issue);}
  if(params.group!=null&&!(available.review&&setMode('review')&&api.selectGroup(params.group)))missing.push('问题组 '+params.group);
  if(params.object!=null&&!(available.review&&setMode('review')&&api.selectObject(params.object)))missing.push('对象 '+params.object);
  if(params.scene!=null){if(!(available.review&&setMode('review')&&api.scene(params.scene)))missing.push('图页 '+params.scene);}
  routeNote=missing.length?'未找到 '+missing.join('、')+'，已停在默认视图':'';render();top();
 }
 byId('audit-search').oninput=()=>{for(const k of Object.keys(shown))delete shown[k];refresh();};
 byId('map-auto').onclick=()=>{mapState='auto';map();};byId('map-open').onclick=()=>{mapState='open';map();};byId('map-close').onclick=()=>{mapState='close';map();};
 byId('member-prev2').onclick=()=>step(-1);byId('member-next2').onclick=()=>step(1);
 byId('audit-top').hidden=false;
 const defaultMode=openIssues.length||items.some(i=>(i.uncertain||[]).length)?'resolve':data.groups.length?'review':third.length?'audit':extras.length?'extras':'resolve';
 setMode(defaultMode);
 const hash=globalThis.location?.hash;if(hash&&hash.length>1)apply(parseHash(hash));
 globalThis.addEventListener?.('hashchange',()=>apply(parseHash(globalThis.location?.hash)));
 return root.QtoReviewAudit.active={refresh(){if(mode&&mode!=='review'){excluded=new Set(current&&noteOf(current)?.excluded_detail_row_ids||[]);render();}},setMode,show:id=>{const x=itemMap.get(id)||issueMap.get(id);if(x){setMode(issueMap.get(id)===x?'resolve':x.kind==='extra'?'extras':'audit');show(x);}return !!x;},apply:h=>apply(parseHash(h)),state:()=>({mode,current:current?.id||null,overview,source,mapState,located:located.length})};
}
root.QtoReviewAudit={attach};
})(globalThis);
