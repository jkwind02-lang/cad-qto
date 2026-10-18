(function(root){
'use strict';
// Results / resolve / audit modes for qto-review-view/1.1 (contract docs/维护/复核页审核视图契约.md, sections 5-9 and 14).
// The page never computes engineering quantities: every number shown comes from the view as the professional wrote it.
const LEVELS=['确认量','规则推算量','方案暂估量','未决量'],INCOMPLETE='检索记录不全';
// Results first (user 2026-10-15: the page is the final result page; third-party quantities are only for comparison).
const MODES={results:'成果',resolve:'待解决问题',audit:'对照审核',review:'图面复核'},ALIASES={issues:'resolve',extras:'results'};
const VERDICT_LABELS={item:{同意:'同意',要改:'要改',待定:'待定'},issue:{同意:'按现做法',要改:'不按现做法',待定:'暂不定'}};
// What kind of answer each nature asks for (contract 6.1); the group heading tells the reviewer what to give.
const NATURE_ASK={rule_gap:'定一条计算规则，覆盖全部同类内容',multi_method:'在列出的做法里选一种，或另写',missing_info:'给资料；或定“缺这类资料时怎么处理”的兜底口径',drawing_conflict:'定取舍顺序（以哪种图为准、标注还是量取为准），或请设计确认',scope:'定计、不计或列清单外',assumption:'接受该估算方法（作方案暂估），或要求补资料',tool_limit:'接受近似，或等插件改进',list_issue:'确认“本次依据充分，按本次计，差异交清单编制方”'};
const LIST_TYPES=['清单少计','清单多计','清单漏量','清单列了图上没有的','清单规格写错'],LIST_PREFIX='清单的问题|',DEVIATIONS=['多算','少算','第三方无量','图上无'];
const deviation=s=>DEVIATIONS.includes(s.conclusion)||(s.conclusion==='原量明确零'&&s.difference?.value>0);
const UNSORTED='未分类',UNLINKED='没有依据、也没挂问题',KEY_PREFIX={item:'对照条目|',issue:'问题|',common:'共性问题|'};
const ATTRIBUTION={list:'清单的问题',ours:'本次依据不足',both:'部分是清单的问题'};
const PAGE=60;
function attach(api){
 const data=api.data,c=data.comparison;if(!c)return null;
 const byId=id=>document.getElementById(id),core=api.core;
 const el=(tag,cls,txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;};
 const btn=(txt,fn,cls)=>{const b=el('button',cls,txt);b.onclick=fn;return b;};
 const items=c.items,third=items.filter(i=>i.kind==='third_party'),extras=items.filter(i=>i.kind==='extra'),issues=c.issues,commons=c.common_issues||[];
 const itemMap=new Map(items.map(i=>[i.id,i])),issueMap=new Map(issues.map(q=>[q.id,q])),commonMap=new Map(commons.map(g=>[g.id,g])),objects=new Map(data.objects.map(o=>[o.catalog_key,o]));
 const kindOf=x=>itemMap.get(x.id)===x?'item':issueMap.get(x.id)===x?'issue':'common';
 const tp=c.display_terms?.third_party||'第三方',term=s=>String(s).split('第三方').join(tp);
 // 没有任何对照来源时 extra 只表示还没建立对照，不能说成清单外新增（登记 20261016-0131）
 const EXTRA=c.sources.length?'清单外新增':'独立算量条目（未建立'+tp+'对照）',EXTRA_SHORT=c.sources.length?'清单外':'独立算量';
 const natureLabels=c.nature_labels||{},natures=Object.keys(natureLabels),natureLabel=n=>n?natureLabels[n]||n:UNSORTED;
 const dims=c.dimensions,overDim=dims.find(d=>d.role==='overview'),sectDim=dims.find(d=>d.role==='section'),catDim=dims.find(d=>d.role==='category');
 const filterDims=dims.filter(d=>d.role==='filter'),techDims=dims.filter(d=>d.role==='technical');   // technical values only show under 技术来源
 const dimTitle=(d,v)=>(d.values.find(x=>x.id===v)||{}).title||v;
 const hasScenes=data.scenes.length>0,mapSection=byId('map-section'),layout=byId('layout'),audit=byId('audit');
 let mode=null,current=null,source=c.sources[0]?.id,overview='全部',mapState='auto',located=[],at=0,routeNote='';
 const folded=new Set(['closed',INCOMPLETE,'excluded','audit|none','audit|缺图','audit|归入他项','audit|一致']),shown=Object.create(null);
 const F={scope:'全部',evidence:'全部',nature:'全部',conclusion:'全部',alert:false,alertRate:Math.round((c.thresholds.difference_rate_alert??0.2)*1000)/10,corrected:false,disagree:false,incomplete:false,sort:'rate',dims:Object.create(null)};
 // Page tolerance (contract 5.4): only segments the generator judged by band (band_check) are re-shown; numbers never change.
 const defaultBand=c.thresholds.consistent_band,split=defaultBand!=null&&typeof defaultBand==='object',countUnits=new Set(c.thresholds.count_units||[]);
 let pageBand=split?{...defaultBand}:defaultBand;
 const bandFor=unit=>split?(countUnits.has(unit)?pageBand['计数']:pageBand['计量']):pageBand;
 const bandChanged=()=>split?Object.keys(defaultBand).some(k=>pageBand[k]!==defaultBand[k]):pageBand!==defaultBand;
 const shownOf=s=>{if(!s||!s.band_check||!bandChanged())return s?.conclusion;const b=bandFor(s.unit),{rate,equal}=s.band_check;if(b==null)return s.conclusion;return (b===0?equal:equal||Math.abs(rate)<=b+1e-12)?'一致':rate>0?'多算':'少算';};
 const conclusionText=s=>{const v=shownOf(s);return term(v||'—')+(v!==s.conclusion?'（原：'+term(s.conclusion||'—')+'）':'');};
 const pctIn=b=>Math.round(b*1e6)/1e4,bandText=b=>split?'计量 '+(b['计量']?'±'+pctIn(b['计量'])+'%':'须相等')+'，计数 '+(b['计数']?'±'+pctIn(b['计数'])+'%':'须相等'):b?'±'+pctIn(b)+'%':'须相等';
 const hay=new Map();const haystack=x=>{let h=hay.get(x);if(h==null){h=JSON.stringify(x).toLowerCase();hay.set(x,h);}return h;};
 const num=v=>v==null?'—':String(Number(Number(v).toPrecision(12)));
 const pct=r=>r==null?'—':(r>0?'+':'')+(r*100).toFixed(1)+'%';
 const seg=(i,sid=source)=>i.third_parties.find(s=>s.source_id===sid);
 const nameOf=i=>i.name||i.third_parties[0]?.rows[0]?.name||i.id,featOf=i=>i.features||i.third_parties[0]?.rows[0]?.features||'';
 const titleOf=x=>({item:()=>nameOf(x),issue:()=>x.title,common:()=>x.implied?'清单的问题 · '+x.list_type:x.question})[kindOf(x)]();
 const shortOf=g=>g.implied?g.list_type:g.id;
 const handlerLabel=h=>c.handler_labels[h]||h,isOpen=x=>x.handler!=='closed';
 const ownVals=(x,d)=>[].concat((x.dimensions||{})[d.id]??[]);
 // A common issue without its own value takes the values of its scope (contract 6.2).
 const vals=(x,d)=>{const v=ownVals(x,d);if(v.length||kindOf(x)!=='common')return v;return Array.from(new Set([...x.scope.item_ids.map(id=>itemMap.get(id)),...x.scope.issue_ids.map(id=>issueMap.get(id))].filter(Boolean).flatMap(y=>ownVals(y,d))));};
 const inOverview=x=>overview==='全部'||!overDim||vals(x,overDim).includes(overview);
 const dimsOK=x=>filterDims.every(d=>!F.dims[d.id]||F.dims[d.id]==='全部'||vals(x,d).includes(F.dims[d.id]));
 const query=()=>byId('audit-search').value.trim().toLowerCase(),searched=x=>{const q=query();return !q||haystack(x).includes(q);};
 const isAlert=s=>s&&s.comparison_state==='comparable'&&s.difference?.rate!=null&&Math.abs(s.difference.rate)*100>F.alertRate;
 const incomplete=i=>i.third_parties.some(s=>s.search_incomplete);

 // ---------- opinions: own note, common answers, inheritance (contract 9.1) ----------
 const feedback=()=>api.getFeedback(),keyOf=x=>KEY_PREFIX[kindOf(x)]+x.id;
 const noteOf=x=>{const f=feedback(),k=keyOf(x);return f.drafts[k]||f.annotations[k];};
 const savedOf=x=>feedback().annotations[keyOf(x)];
 const answerOf=x=>{const r=savedOf(x);return r&&!['stale','unmatched','conflict'].includes(r.applicability_state)&&!r.merge_conflict?r:null;};
 const optionText=(x,id)=>(x.options||[]).find(o=>o.id===id)?.text||id;
 const answerText=(x,a)=>(a.verdict?VERDICT_LABELS[kindOf(x)==='item'?'item':'issue'][a.verdict]:'已答复')+(a.option_id?'，'+optionText(x,a.option_id):'')+(!a.verdict&&a.confirmed_result?'：'+a.confirmed_result:'');
 // Commons covering a target: an issue's own common; for an item every common whose scope holds it, including list-issue groups.
 const covering=x=>{const k=kindOf(x);if(k==='common')return [];if(k==='issue')return x.common_issue_id&&commonMap.has(x.common_issue_id)?[commonMap.get(x.common_issue_id)]:[];return [...x.common_issue_ids,...x.list_issue_ids].map(id=>commonMap.get(id)).filter(Boolean);};
 // How a covered place stands towards one common answer (contract 9.1): its exception is in conflict (held), it has its own exception,
 // its exception expired, or it takes the answer (inherited) / waits for it. Only inherited and expired places follow the common answer.
 const standing=(x,g)=>{const r=savedOf(x);if(r?.merge_conflict&&r.merge_conflict.candidates.some(c=>c.exception_of===g.id))return 'held';
  const a=answerOf(g),old=a?.common_issue_snapshot?.scope;if(a&&!(old?.[kindOf(x)==='issue'?'issue_ids':'item_ids']||[]).includes(x.id))return 'new';
  if(answerOf(x)?.exception_of===g.id)return 'exception';if(r?.exception_of===g.id)return 'expired';return answerOf(g)?'inherited':'waiting';};
 const follows=(x,g)=>['inherited','expired'].includes(standing(x,g))&&!!answerOf(g);
 const inheritText=x=>covering(x).map(g=>{const s=standing(x,g),a=answerOf(g),take=a?'已按共性答复（'+shortOf(g)+'：'+answerText(g,a)+'）':'待共性答复（'+shortOf(g)+'）';
  return s==='new'?'答复后新增范围，须重新核对；本处未继承旧答复':s==='held'?'例外意见冲突（'+shortOf(g)+'）：两份意见不同，重新保存一条前这里不按共性答复':s==='exception'?'此处已设例外，不按 '+shortOf(g)+' 的答复（本处：'+answerText(x,answerOf(x))+'）':
   s==='expired'?'此处原设的例外已过期，须重新核对；现'+take:take;}).join('；');
 const answered=x=>!!answerOf(x)||covering(x).some(g=>follows(x,g));
 const addedSince=(g,a)=>{const old=a?.common_issue_snapshot?.scope||{};return {issue_ids:g.scope.issue_ids.filter(x=>!(old.issue_ids||[]).includes(x)),item_ids:g.scope.item_ids.filter(x=>!(old.item_ids||[]).includes(x))};};
 function commonState(g){
  const r=savedOf(g);if(r?.merge_conflict||r?.applicability_state==='conflict')return '意见冲突，动作暂挂';if(r&&(r.applicability_state==='stale'||r.applicability_state==='unmatched'))return '答复已过期，须重答';
  const a=answerOf(g);if(!a)return '未答复';const n=addedSince(g,a);const added=n.issue_ids.length+n.item_ids.length;
  return '已答复：'+answerText(g,a)+(added?' · 答复后新增 '+added+' 处':'');
 }
 // Two answered commons over one item, one keeping and one changing the practice, are only flagged (contract 9.1 overlaps).
 const overlap=i=>{const v=new Set(covering(i).filter(g=>follows(i,g)).map(g=>answerOf(g).verdict));return v.has('同意')&&v.has('要改');};

 // ---------- what still lacks a basis ----------
 const hasPending=i=>(i.uncertain||[]).includes('有未决量'),estOf=i=>i.ours.levels['方案暂估量']||0;
 const inResults=i=>(i.ours.total!=null&&i.ours.total!==0)||hasPending(i);
 const evidenceOf=i=>hasPending(i)?'pending':estOf(i)>0?'est':'firm';
 // Linked = an open individual issue or open common (direct or through its issues), as the generator's C3-9 and 5.5.
 const linkedTargets=i=>{const gs=i.common_issue_ids.map(id=>commonMap.get(id)).filter(g=>g&&isOpen(g));return [...gs,...i.issue_ids.map(id=>issueMap.get(id)).filter(q=>q&&isOpen(q)&&!gs.some(g=>g.id===q.common_issue_id))];};
 const weakSeg=(i,s)=>s&&(s.attribution==='ours'||s.attribution==='both')&&!linkedTargets(i).length;
 const unlinked=i=>i.uncertain_unlinked||i.third_parties.some(s=>weakSeg(i,s));
 const unlinkedItems=items.filter(unlinked),incompleteItems=items.filter(incomplete);
 // Unanswered open targets on an item, for the nature marks on result rows.
 const pendingTargets=i=>[...linkedTargets(i),...i.list_issue_ids.map(id=>commonMap.get(id)).filter(Boolean)].filter(x=>!answered(x));
 const natureOf=x=>x.nature||null;
 // Things that wait for an answer: open commons and open stand-alone issues, by nature.
 const toAnswer=()=>[...commons.filter(isOpen),...issues.filter(q=>isOpen(q)&&!q.common_issue_id)].filter(x=>!answerOf(x));

 // ---------- top: mode tabs, resolve counts, band rule ----------
 const available={results:items.length>0,resolve:true,audit:third.length>0,review:data.objects.length>0||data.groups.length>0};
 function tabs(){const nav=byId('mode-tabs');nav.hidden=false;nav.replaceChildren();for(const [m,label] of Object.entries(MODES))if(available[m]){const b=btn(label,()=>{setMode(m);route();},m===mode?'on':'');nav.append(b);}}
 function top(){
  const box=byId('resolve-counts');box.replaceChildren();const waiting=toAnswer();
  for(const n of [...natures,null]){const k=waiting.filter(x=>natureOf(x)===n).length;if(k)box.append(btn(natureLabel(n)+' '+k,()=>focusGroup('nature|'+(n||UNSORTED)),'chip warn'));}
  if(unlinkedItems.length)box.append(btn(UNLINKED+' '+unlinkedItems.length,()=>focusGroup('unlinked'),'chip'));
  if(!box.children.length)box.append(el('span','minor','没有待解决的问题'));
  byId('band-rule').hidden=!third.length;
  bandState.textContent=(defaultBand==null?'':bandChanged()?'按页面容差显示，默认 '+bandText(defaultBand)+'；与默认不同的结论标“原：”':'默认 '+bandText(defaultBand))+' · 差异超阈值筛选 '+F.alertRate+'%';
  byId('route-message').hidden=!routeNote;byId('route-message').textContent=routeNote;
 }
 // Band controls are built once so typing keeps focus; top() only updates the state text.
 const bandState=el('span','minor'),bandInputs=[];
 (function bandControls(){
  const box=byId('band-rule');box.replaceChildren();
  if(defaultBand!=null){
   box.append(el('span',null,'一致容差：'));
   const input=(label,get,set)=>{const w=el('label',null);w.append(el('span',null,label+' ±'));const i=el('input');i.type='number';i.min='0';i.step='0.5';i.value=pctIn(get());i.setAttribute('aria-label','一致容差 '+label+' 百分比');
    i.oninput=()=>{const v=Number(i.value);if(i.value!==''&&Number.isFinite(v)&&v>=0){set(v/100);filters();refresh();}};w.append(i,el('span',null,'%'));box.append(w);bandInputs.push(()=>{i.value=pctIn(get());});};
   if(split){input('计量',()=>pageBand['计量'],v=>{pageBand['计量']=v;});input('计数',()=>pageBand['计数'],v=>{pageBand['计数']=v;});}
   else input('全部单位',()=>pageBand,v=>{pageBand=v;});
   box.append(btn('恢复默认',()=>{pageBand=split?{...defaultBand}:defaultBand;for(const f of bandInputs)f();filters();refresh();},'link'));
  }
  box.append(bandState);
 })();
 function focusGroup(k){folded.delete(k);setMode('resolve');current=null;render();}

 // ---------- filters ----------
 function select(label,options,value,fn){const w=el('label');w.append(label);const s=el('select');for(const [v,t] of options){const o=el('option',null,t);o.value=v;s.append(o);}s.value=value;s.onchange=()=>fn(s.value);w.append(s);return w;}
 function check(label,value,fn){const w=el('label'),cb=el('input');cb.type='checkbox';cb.checked=value;cb.onchange=()=>fn(cb.checked);w.append(cb,label);return w;}
 function filters(){
  const box=byId('audit-filters');box.replaceChildren();
  if(overDim)box.append(select(overDim.title,[['全部','全部'],...overDim.values.map(v=>[v.id,v.title])],overview,v=>{overview=v;refresh();}));
  if(mode==='results'){
   if(third.length&&extras.length)box.append(select('范围',[['全部','全部'],['third_party','有'+tp+'对应'],['extra',EXTRA]],F.scope,v=>{F.scope=v;refresh();}));
   box.append(select('依据状况',[['全部','全部'],['firm','全部有依据'],['est','含估算'],['pending','有待定']],F.evidence,v=>{F.evidence=v;refresh();}));
   const seen=[...natures,null].filter(n=>items.some(i=>pendingTargets(i).some(x=>natureOf(x)===n)));
   if(seen.length)box.append(select('问题性质',[['全部','全部'],...seen.map(n=>[n||UNSORTED,natureLabel(n)])],F.nature,v=>{F.nature=v;refresh();}));
  }
  if(mode==='audit'){
   if(c.sources.length>1)box.append(select('对照来源',c.sources.map(s=>[s.id,s.title]),source,v=>{source=v;refresh();}));
   const seen=Array.from(new Set(third.map(i=>shownOf(seg(i))).filter(Boolean)));if(F.conclusion!=='全部'&&!seen.includes(F.conclusion))F.conclusion='全部';
   box.append(select('结论',[['全部','全部'],...seen.map(x=>[x,term(x)])],F.conclusion,v=>{F.conclusion=v;refresh();}));
   box.append(select('排序',[['rate','差额率（绝对值）'],['value','差额（绝对值）'],['id','编号']],F.sort,v=>{F.sort=v;refresh();}));
   const rate=el('input');rate.type='number';rate.value=F.alertRate;rate.setAttribute('aria-label','差异超阈值百分比');rate.oninput=()=>{const v=Number(rate.value);if(Number.isFinite(v)&&v>=0){F.alertRate=v;top();refresh();}};
   const alert=check('差异超阈值 %',F.alert,v=>{F.alert=v;refresh();});alert.append(rate);box.append(alert);
   box.append(check(term('第三方条目有误已修正'),F.corrected,v=>{F.corrected=v;refresh();}));
   if(incompleteItems.length)box.append(check(INCOMPLETE,F.incomplete,v=>{F.incomplete=v;refresh();}));
   if(c.sources.length>1)box.append(check('来源间结论不一致',F.disagree,v=>{F.disagree=v;refresh();}));
  }
  // 筛选跨模式沿用，问题页也显示，看得见、能清除（登记 20261016-0342）
  for(const d of filterDims)box.append(select(d.title,[['全部','全部'],...d.values.map(v=>[v.id,v.title])],F.dims[d.id]||'全部',v=>{F.dims[d.id]=v;refresh();}));
 }
 const common=x=>inOverview(x)&&dimsOK(x)&&searched(x);
 const resultOK=i=>common(i)&&(F.scope==='全部'||i.kind===F.scope)&&(F.evidence==='全部'||(F.evidence==='firm'?evidenceOf(i)==='firm':F.evidence==='est'?estOf(i)>0:hasPending(i)))&&(F.nature==='全部'||pendingTargets(i).some(x=>(natureOf(x)||UNSORTED)===F.nature));
 const auditOK=i=>{const s=seg(i);return s&&common(i)&&(F.conclusion==='全部'||shownOf(s)===F.conclusion)&&(!F.alert||isAlert(s))&&(!F.corrected||s.third_party_corrected)&&(!F.incomplete||incomplete(i))&&(!F.disagree||new Set(i.third_parties.map(shownOf)).size>1);};
 function sorted(list){
  const abs=v=>v==null?Infinity:-Math.abs(v),key={rate:i=>abs(seg(i)?.difference?.rate),value:i=>abs(seg(i)?.difference?.value),id:()=>0}[F.sort];   // no difference sorts last
  return F.sort==='id'?list:list.map((i,n)=>[key(i),n,i]).sort((a,b)=>a[0]-b[0]||a[1]-b[1]).map(x=>x[2]);
 }

 // ---------- list ----------
 const listBox=()=>byId('audit-list'),opened=k=>!folded.has(k);
 function head(key,label,depth,extra){const b=btn((opened(key)?'▾ ':'▸ ')+label,()=>{folded.has(key)?folded.delete(key):folded.add(key);refresh();},'sect'+(depth?' d'+depth:''));listBox().append(b);if(extra&&opened(key))listBox().append(extra);return opened(key);}
 function rows(key,list,render){const n=shown[key]||PAGE;for(const x of list.slice(0,n))listBox().append(render(x));if(list.length>n)listBox().append(btn('再显示 '+Math.min(PAGE,list.length-n)+' 条（共 '+list.length+'）',()=>{shown[key]=n+PAGE;refresh();},'more'));}
 function section(key,label,list,render,extra){if(head(key,label+' · '+list.length,0,extra))rows(key,list,render);}
 // Results: building, division and category levels (contract 14); headings count items, never sum quantities across items.
 function tree(key,list,render,levels,depth=0,label=g=>' · '+g.length){
  if(!levels.length){rows(key,list,render);return;}
  const [d,...rest]=levels,groups=new Map();for(const i of list){const v=vals(i,d)[0]??'（未分）';if(!groups.has(v))groups.set(v,[]);groups.get(v).push(i);}
  const order=new Map(d.values.map((v,n)=>[v.id,n]));
  for(const [v,g] of [...groups].sort((a,b)=>(order.get(a[0])??1e9)-(order.get(b[0])??1e9))){const k=key+'|'+v;if(head(k,dimTitle(d,v)+label(g),depth))tree(k,g,render,rest,depth+1,label);}
 }
 function row(x,title,sub,extra){
  const b=el('button','item');b.dataset.key=keyOf(x);if(current===x)b.classList.add('active');
  const strong=el('strong',null,title);b.append(strong);if(savedOf(x)?.merge_conflict)strong.append(el('span','badge bad','意见冲突'));else if(noteOf(x))strong.append(el('span','badge',savedOf(x)?.exception_of?'有例外':'有意见'));
  for(const s of sub)b.append(s);if(extra)b.append(extra);b.onclick=()=>{show(x);route();};return b;
 }
 // A clickable mark inside a row: jumps without also selecting the row.
 const jump=(text,cls,fn)=>{const s=el('span',cls,text);s.onclick=e=>{e?.stopPropagation?.();fn();};return s;};
 function evidenceBar(i){
  const L=i.ours.levels,firm=(L['确认量']||0)+(L['规则推算量']||0),est=L['方案暂估量']||0,pend=L['未决量']||0,stub=hasPending(i)&&!pend,max=Math.max(firm+est+pend,1e-9),w=v=>Math.max(0,v/max*100).toFixed(1)+'%';
  const box=el('div','bars'),b=el('div','bar2'),f=el('span','b-firm'),e=el('span','b-est'),p=el('span','b-pend');f.style.width=w(firm);e.style.width=w(est);p.style.width=stub?'6%':w(pend);b.append(f,e,p);
  b.setAttribute('title','有依据 '+num(firm)+' · 估算 '+num(est)+' · 待定 '+(stub?'未写数':num(pend)));box.append(b);return box;
 }
 function compareBar(i,s){
  const firm=(i.ours.levels['确认量']||0)+(i.ours.levels['规则推算量']||0),est=i.ours.levels['方案暂估量']||0,q=s&&s.unit===i.ours.unit?s.quantity||0:0,max=Math.max(firm+est,q,1e-9),w=v=>Math.max(0,v/max*100).toFixed(1)+'%';
  const box=el('div','bars');const a=el('div','bar2'),t=el('span','b-tp');t.style.width=w(q);a.append(t);const b=el('div','bar2'),f=el('span','b-firm'),e=el('span','b-est');f.style.width=w(firm);e.style.width=w(est);b.append(f,e);box.append(a,b);return box;
 }
 const levelsText=i=>{const L=i.ours.levels;return LEVELS.filter(l=>L[l]!=null).map(l=>l.replace('量','')+' '+num(L[l])).join(' · ')+' · 合计 '+num(i.ours.total)+' '+i.ours.unit+(hasPending(i)&&L['未决量']==null?' · 另有待定':'');};
 const srcTitle=s=>c.sources.length>1?(c.sources.find(x=>x.id===s.source_id)?.title||s.source_id)+'：':'';
 const segsOf=i=>i.kind==='extra'?[]:c.sources.length>1?c.sources.map(x=>seg(i,x.id)||{source_id:x.id,missing:true}):[seg(i)||i.third_parties[0]];
 const tail=s=>' · '+conclusionText(s)+(s.difference?.rate!=null?' '+pct(s.difference.rate):'')+(s.third_party_corrected?' · 已修正':'');
 const excludedText=i=>i.ours.excluded_quantity!=null?num(i.ours.excluded_quantity)+' '+i.ours.unit:'';
 function resultRow(i){
  const subs=[el('small',null,levelsText(i))],tags=el('small');
  if(i.kind==='extra')tags.append(el('span','badge',EXTRA));
  if(excludedText(i))tags.append(el('span','badge','另有已定不计 '+excludedText(i)));
  if(unlinked(i))tags.append(el('span','badge bad','未挂问题'));
  const byNature=new Map();for(const x of pendingTargets(i)){const n=natureOf(x)||UNSORTED;if(!byNature.has(n))byNature.set(n,[]);byNature.get(n).push(x);}
  for(const [n,xs] of byNature)tags.append(jump(natureLabel(n===UNSORTED?null:n)+(xs.length>1?' '+xs.length:''),'badge warn',()=>{const x=xs.find(y=>kindOf(y)==='common')||xs[0];setMode(x.implied&&available.audit?'audit':'resolve');show(x);route();}));
  if(tags.children.length)subs.push(tags);
  for(const s of segsOf(i))subs.push(el('small','minor','对照 '+srcTitle(s)+(s.missing?'无对应':tp+' '+num(s.quantity)+(s.unit!==i.ours.unit?' '+s.unit:'')+tail(s))));
  return row(i,nameOf(i)+(featOf(i)?' · '+featOf(i):''),subs,evidenceBar(i));
 }
 function auditRow(i){
  const subs=[];for(const s of segsOf(i))subs.push(el('small',null,srcTitle(s)+(s.missing?'无对应':tp+' '+num(s.quantity)+(s.unit!==i.ours.unit?' '+s.unit:'')+' → 本次 '+num(i.ours.total)+' '+i.ours.unit+' · 差额 '+num(s.difference?.value)+tail(s)+(s.attribution?' · '+ATTRIBUTION[s.attribution]:deviation(s)?' · 无法归因':''))));
  const tags=el('small'),s=seg(i);
  if(s?.attribution==='both')tags.append(el('span','badge warn','部分是清单的问题 超出 '+num(s.excess)+' '+i.ours.unit));
  if(excludedText(i))tags.append(el('span','badge','含已定不计 '+excludedText(i)));
  if(incomplete(i))tags.append(el('span','badge','检索记录不全'));if(weakSeg(i,s))tags.append(el('span','badge bad','未挂问题'));
  if(tags.children.length)subs.push(tags);
  return row(i,nameOf(i)+(featOf(i)?' · '+featOf(i):''),subs,s?compareBar(i,s):null);
 }
 function issueRow(q){
  const subs=[el('small',null,handlerLabel(q.handler)+(q.status?' · '+q.status:'')+' · 涉及条目 '+q.item_ids.length+(q.current_practice||q.current_option?'':' · 未写现做法')+(q.rule_ref?' · 已按口径 '+q.rule_ref+' 处理':''))];
  if(q.common_issue_id)subs.push(el('small','minor',inheritText(q)));
  return row(q,q.id+' · '+q.title,subs);
 }
 function commonRow(g){
  const blds=overDim?vals(g,overDim).map(v=>dimTitle(overDim,v)):[];
  const subs=[el('small',null,(g.implied?g.question:handlerLabel(g.handler))+(g.current_option?' · 现做法 '+optionText(g,g.current_option):'')+' · 个别问题 '+g.scope.issue_ids.length+' · 条目 '+g.scope.item_ids.length+(blds.length?' · '+blds.join('、'):'')),el('small','minor',commonState(g))];
  return row(g,g.implied?'清单的问题 · '+g.list_type:g.id+' · '+g.question,subs);
 }
 const targetRow=x=>kindOf(x)==='common'?commonRow(x):issueRow(x);
 function excludedRow(i){
  const rs=i.detail_rows.filter(r=>r.excluded);
  return row(i,nameOf(i)+(featOf(i)?' · '+featOf(i):''),[el('small',null,'不计 '+excludedText(i)+' · '+rs.length+' 行'+(i.ours.total===0?' · 本条全部不计':'')),el('small','minor',rs.map(r=>r.excluded.note+(r.excluded.rule_ref?'（口径 '+r.excluded.rule_ref+'）':'')).join('；'))]);
 }
 function list(){
  const box=listBox();box.replaceChildren();let total=0;
  if(mode==='resolve'){
   // The page's main job (contract 0, 14): what has no basis or cannot be judged, grouped by the kind of answer it needs.
   box.append(el('div','hint','成果中没有依据或无法判断的内容。按问题性质分组，先答共性问题：答一条，适用范围里的个别问题和条目一起生效。'));
   for(const n of [...natures,null]){
    const gs=commons.filter(g=>isOpen(g)&&natureOf(g)===n&&common(g)),qs=issues.filter(q=>isOpen(q)&&!q.common_issue_id&&natureOf(q)===n&&common(q));
    total+=gs.length+qs.length;if(gs.length+qs.length)section('nature|'+(n||UNSORTED),natureLabel(n),[...gs,...qs],targetRow,el('div','hint','要的答复：'+(NATURE_ASK[n]||'先由专业补上问题性质；可照问题本身答复')));
   }
   const un=unlinkedItems.filter(common);total+=un.length;if(un.length)section('unlinked',UNLINKED+'（提醒专业补挂问题）',un,resultRow);
   const closed=[...commons.filter(g=>!isOpen(g)),...issues.filter(q=>!isOpen(q))].filter(common);total+=closed.length;if(closed.length)section('closed',handlerLabel('closed'),closed,targetRow);
   const inc=incompleteItems.filter(common);if(inc.length)section(INCOMPLETE,INCOMPLETE+'（规则生效前交付，不回头补，仅提示）',inc,auditRow);
   if(data.groups.length)box.append(btn('另有 '+data.groups.length+' 个图面问题组待复核 → 图面复核',()=>{setMode('review');route();},'more'));
   if(!total)box.append(el('div','hint','没有待解决的问题'));
  }else if(mode==='audit'){
   // Deviations by attribution (contract 5.5): list issues answered once per group; our weak basis goes to its issue.
   const seen=new Set();   // an item may sit in two list-issue groups; the counter counts it once
   for(const t of LIST_TYPES){const g=commonMap.get(LIST_PREFIX+t);if(!g)continue;const its=sorted(third.filter(i=>auditOK(i)&&(seg(i).list_issue_types||[]).includes(t)));if(!its.length)continue;for(const i of its)seen.add(i);
    const ex=its.filter(excludedText).length,extra=el('div','groupbar');extra.append(btn('整组答复 · '+commonState(g),()=>{show(g);route();},'more'));if(ex)extra.append(el('div','hint','其中 '+ex+' 条含已定不计：少于清单的部分有按口径不计的，不是清单编制方的错，答复前请看依据'));
    section('list|'+t,'清单的问题 · '+t,its,auditRow,extra);}
   const weak=new Map();for(const i of third){const s=seg(i);if(!auditOK(i)||!(s.attribution==='ours'||s.attribution==='both'))continue;const ts=linkedTargets(i);for(const x of ts.length?ts:[null]){const k=x?keyOf(x):'none';if(!weak.has(k))weak.set(k,{x,list:[]});weak.get(k).list.push(i);}}
   for(const [k,{x,list:its}] of weak){for(const i of its)seen.add(i);
    const extra=x?btn('到待解决问题里答复 · '+(kindOf(x)==='common'?commonState(x):answered(x)?'已答复':'未答复'),()=>{setMode('resolve');show(x);route();},'more'):el('div','hint','依据不足的偏差未挂问题：提醒专业补挂');
    section('weak|'+k,'本次依据不足 · '+(x?(kindOf(x)==='common'?shortOf(x)+' '+x.question:x.id+' '+x.title)+'（'+natureLabel(natureOf(x))+'）':'依据不足的偏差未挂问题'),sorted(its),auditRow,extra);}
   for(const [k,label,f] of [['audit|none','无法归因（单位不同或没有差额）',s=>deviation(s)&&!s.attribution],...['原量未填','原量明确零','不可比','待核可比性','本次量未形成','本期不计'].map(t=>['audit|'+t,t,s=>s.conclusion===t]),['audit|缺图','缺图（在待解决问题里）',s=>s.conclusion==='缺图'],['audit|归入他项',term('归入他项'),s=>s.conclusion==='归入他项'],['audit|一致','一致',s=>s.conclusion==='一致']]){   // groups follow the data conclusion, as attribution does
    const its=third.filter(i=>auditOK(i)&&f(seg(i)));for(const i of its)seen.add(i);if(its.length)section(k,label,sorted(its),auditRow);}
   total=seen.size;
  }else{
   const g=items.filter(i=>inResults(i)&&resultOK(i));total=g.length;
   const est=l=>l.filter(i=>estOf(i)>0).length,pend=l=>l.filter(hasPending).length;
   tree('results',g,resultRow,[overview==='全部'?overDim:null,sectDim,catDim].filter(Boolean),0,l=>' · '+l.length+' 条'+(est(l)?' · 含估算 '+est(l):'')+(pend(l)?' · 有待定 '+pend(l):''));
   const ex=items.filter(i=>i.ours.excluded_rows&&common(i));if(ex.length){if(head('excluded','已定不计 · '+ex.length+' 条（不计入成果，不进待解决问题）',0)){if(overDim&&overview==='全部')tree('excluded',ex,excludedRow,[overDim],1);else rows('excluded',ex,excludedRow);}}
  }
  byId('audit-counter').textContent='筛选 '+total+' 条'+(mode==='resolve'?'（待答复的问题与还没挂问题的条目）':mode==='results'?'（本次有量的成果条目）':'（按归因分组）')+'；完整数据保留，未截取。';
 }

 // ---------- detail ----------
 function table(head,rows){const t=el('table','qty'),tr=el('tr');for(const h of head)tr.append(el('th',null,h));t.append(tr);for(const r of rows){const x=el('tr');for(const v of r){const td=el('td');if(v&&typeof v==='object')td.append(v);else td.textContent=v==null?'':String(v);x.append(td);}t.append(x);}return t;}
 function link(text,fn){return btn(text,fn,'link');}
 const go=x=>{const k=kindOf(x);setMode(k==='item'?(mode==='audit'&&x.kind==='third_party'?'audit':'results'):x.implied&&mode==='audit'?'audit':'resolve');show(x);route();};
 const itemLink=id=>{const i=itemMap.get(id);return link(i?nameOf(i)+(featOf(i)?'（'+featOf(i)+'）':''):id,()=>{if(i)go(i);});};
 const targetLink=x=>link(kindOf(x)==='common'?(x.implied?'清单的问题 · '+x.list_type:x.id+' · '+x.question):x.id+' · '+x.title,()=>go(x));
 const issueLink=id=>{const q=issueMap.get(id)||commonMap.get(id);return q?targetLink(q):el('span',null,id);};
 function para(label,text){const p=el('p','para');p.append(el('b',null,label+'：'));if(text!=null&&typeof text==='object')p.append(text);else p.append(el('span',null,text));return p;}
 function linksPara(label,nodes){const p=el('p','para');p.append(el('b',null,label+'：'));for(const n of nodes)p.append(n);return p;}
 const impactText=imp=>[].concat(imp??[]).map(p=>(p.value!=null?num(p.value)+' '+(p.unit||''):'')+(p.note?(p.value!=null?'（'+p.note+'）':p.note):'')).join('；');
 function overviewCards(){
  const box=el('div','cards'),values=[['全部','全部'],...(overDim?overDim.values.map(v=>[v.id,v.title]):[])];
  for(const [v,t] of values){
   const keep=overview;overview=v;const its=items.filter(i=>inOverview(i)&&inResults(i)),wait=toAnswer().filter(inOverview),th=third.filter(inOverview),un=unlinkedItems.filter(inOverview);overview=keep;
   const concl={};for(const i of th){const x=shownOf(seg(i));if(x)concl[x]=(concl[x]||0)+1;}
   const att=k=>th.filter(i=>k.includes(seg(i)?.attribution)).length;
   const card=btn('',()=>{overview=v;current=null;render();route();},'card'+(v===overview?' on':''));card.append(el('b',null,t));
   card.append(el('div',null,'成果：条目 '+its.length+(extras.length?'（'+EXTRA_SHORT+' '+its.filter(i=>i.kind==='extra').length+'）':'')+' · 含估算 '+its.filter(i=>estOf(i)>0).length+' · 有待定 '+its.filter(hasPending).length));
   const byN=[...natures,null].map(n=>[n,wait.filter(x=>natureOf(x)===n).length]).filter(([,k])=>k);
   card.append(el('div',null,'待解决：'+(byN.length?byN.map(([n,k])=>natureLabel(n)+' '+k).join(' · '):'无')+(un.length?' · 未挂问题 '+un.length:'')));
   if(third.length)card.append(el('div','minor','对照：清单的问题 '+att(['list','both'])+' · 本次依据不足 '+att(['ours','both'])+' · '+Object.entries(concl).map(([k,n])=>term(k)+' '+n).join(' · ')));
   box.append(card);
  }
  return box;
 }
 function levelsTable(i){const L=i.ours.levels;return table([...LEVELS.map(x=>x.replace('量','')),'合计（不含未决）','单位'],[[...LEVELS.map(x=>num(L[x])),num(i.ours.total),i.ours.unit]]);}
 const evidenceText=i=>{const L=i.ours.levels;return '有依据（确认 + 规则推算） '+num((L['确认量']||0)+(L['规则推算量']||0))+' · 估算 '+num(L['方案暂估量']||0)+' · 待定 '+(L['未决量']!=null?num(L['未决量']):hasPending(i)?'有，未写数':'0')+' '+i.ours.unit;};
 const searchText=s=>{const e=s.search_evidence||{};return [e.drawings!=null?'图纸 '+[].concat(e.drawings).join('、'):'',e.targets!=null?'对象 '+[].concat(e.targets).join('、'):'',e.method?'方法 '+e.method:'',e.hits!=null?'命中 '+e.hits:'',e.unread?'未读 '+e.unread:'',e.uncovered?'未查 '+e.uncovered:'',e.record_ref?'记录 '+e.record_ref:'',e.note?'说明 '+e.note:''].filter(Boolean).join('；');};
 const attributionText=(i,s)=>s.attribution==='list'?'本次依据充分，偏差是清单的问题（'+(s.list_issue_types||[]).join('、')+'）':s.attribution==='ours'?'本次依据不足，差额在本次估算和待定范围内，归到所挂问题':s.attribution==='both'?'本次依据不足，差额超出估算和待定 '+num(s.excess)+' '+i.ours.unit+'，超出部分是清单的问题':deviation(s)?'无法归因（单位不同或没有差额）':(s.list_issue_types||[]).length?(s.list_issue_types||[]).join('、'):'';
 function itemDetail(i){
  const box=byId('audit-detail');box.append(el('h2',null,nameOf(i)));
  if(featOf(i))box.append(el('div','minor',featOf(i)));
  const tags=el('div');for(const d of [overDim,sectDim,catDim,...filterDims].filter(Boolean)){const v=vals(i,d);if(v.length)tags.append(el('span','badge',d.title+' '+v.map(x=>dimTitle(d,x)).join('、')));}
  if(i.kind==='extra')tags.append(el('span','badge',EXTRA));for(const t of i.uncertain||[])tags.append(el('span','badge warn',t));if(unlinked(i))tags.append(el('span','badge bad','未挂问题'));box.append(tags);
  if(covering(i).length)box.append(para('共性答复',inheritText(i)));
  if(overlap(i))box.append(el('div','hint warn','同一条目有两处答复，请核对：一处按现做法、一处不按现做法'));
  // Our result first, third party as comparison; the comparison audit mode keeps the third-party table on top.
  const comparisonPart=()=>{if(!i.third_parties.length)return;
   box.append(el('h3',null,(mode==='audit'?'':'对照：')+tp+'数量'));
   const rows=[];for(const s of i.third_parties){const src=c.sources.find(x=>x.id===s.source_id);for(const [n,r] of s.rows.entries())rows.push([n?'':(src?.title||s.source_id),[r.seq,r.code].filter(Boolean).join(' '),r.name||'',r.features||'',r.unit,num(r.quantity),n?'':num(s.quantity)+' '+s.unit,n?'':num(s.difference?.value),n?'':pct(s.difference?.rate),n?'':conclusionText(s)+(s.third_party_corrected?'；已修正':''),n?'':s.attribution?ATTRIBUTION[s.attribution]:deviation(s)?'无法归因':''])}
   box.append(table(['来源','序号/编码','名称','特征','单位','行数量','段数量','差额','差额率','结论','归因'],rows));
   for(const s of i.third_parties){
    const pre=c.sources.length>1?(c.sources.find(x=>x.id===s.source_id)?.title||s.source_id)+' ':'';
    const at=attributionText(i,s);if(at)box.append(para(pre+'归因',at));
    if(s.comparison_state!=='comparable')box.append(para(pre+'比较边界',s.comparability?.reason||s.conclusion));
    if(s.scope_basis)box.append(para(pre+'不计依据',s.scope_basis));
    if(excludedText(i)&&deviation(s))box.append(para(pre+'含已定不计','本次另有 '+excludedText(i)+' 按口径不计，见明细里的已定不计行'));
    for(const [k,label] of [['disposition_note','处置说明'],['correction_note','修正说明'],['missing_drawing_note','缺图说明'],['unit_mismatch_note','单位不同'],['quantity_note','部分行无量']])if(s[k]&&s[k]!==i.ours.note)box.append(para(pre+label,s[k]));
    const ev=searchText(s);if(ev)box.append(para(pre+'检索依据',ev));
    if(s.search_incomplete)box.append(para(pre+'检索记录',term('检索记录不全：成果在检索记录规则生效前交付，未回头补；已有的说明照上显示')));
    if(s.redirect_to.length)box.append(linksPara(pre+'去向',s.redirect_to.map(itemLink)));
   }
  };
  const oursPart=()=>{
   box.append(el('h3',null,'本次成果'+(i.ours.quantity_name?' · '+i.ours.quantity_name:'')));box.append(levelsTable(i));box.append(para('依据状况',evidenceText(i)));
   if(i.ours.note)box.append(para('本次说明',i.ours.note));
   const targets=[...i.common_issue_ids.map(id=>commonMap.get(id)),...i.list_issue_ids.map(id=>commonMap.get(id)),...i.issue_ids.map(id=>issueMap.get(id))].filter(Boolean);
   if(targets.length){box.append(el('h3',null,'挂在本条目上的问题'));for(const n of [...natures,null]){const xs=targets.filter(x=>natureOf(x)===n);if(xs.length)box.append(linksPara(natureLabel(n),xs.map(x=>{const s=el('span');s.append(targetLink(x),el('span','minor',isOpen(x)?(answered(x)?'已答复 ':'未答复 '):'已处理 '));return s;})));}}
   if(i.detail_rows.length){
    box.append(el('h3',null,'明细组成与依据（取消勾选的记为本条意见的例外）'));
    const note=noteOf(i),ex=new Set(note?.excluded_detail_row_ids||[]);excluded=ex;
    const spec=r=>c.member_tables.find(t=>t.id===r.member_table);
    const basis=r=>{const s=el('span',null,(r.basis||'')+(r.drawing?'；图纸 '+r.drawing:''));if(r.relation&&r.relation!=='对应')s.append(el('span','badge',r.relation));return s;};
    const counted=i.detail_rows.filter(r=>!r.excluded),dropped=i.detail_rows.filter(r=>r.excluded);
    const rows=counted.map(r=>{const cb=el('input');cb.type='checkbox';cb.checked=!ex.has(r.id);cb.setAttribute('aria-label','纳入 '+r.id);cb.onchange=()=>{cb.checked?ex.delete(r.id):ex.add(r.id);stash();};
     const ms=r.member_keys.map(k=>objects.get(k)).filter(Boolean),m=ms.length?btn((spec(r)?.title||'成员')+' '+ms.length,()=>members(r,ms),'link'):'';
     return [cb,r.area||'',r.level.replace('量',''),num(r.quantity)+' '+r.unit,basis(r),m];});
    // Decided-not-counted rows come after the counted ones and stay out of every level total (contract 5.1).
    for(const r of dropped){const why=el('span',null,(r.basis?r.basis+'；':'')+'已定不计：'+r.excluded.note+(r.excluded.rule_ref?'（口径 '+r.excluded.rule_ref+'）':''));if(r.excluded.issue_id)why.append(issueLink(r.excluded.issue_id));const tag=el('span','badge','已定不计');rows.push([tag,r.area||'',r.level.replace('量',''),num(r.quantity)+' '+r.unit,why,'']);}
    box.append(table(['纳入','楼层区域','层级','数量','依据','下钻'],rows));
    const tech=el('details'),sum=el('summary',null,'技术来源（明细编号、定位、备注'+(techDims.length?'、'+techDims.map(d=>d.title).join('、'):'')+'）');tech.append(sum);
    tech.append(table(['明细','定位','备注','关系',...techDims.map(d=>d.title)],i.detail_rows.map(r=>[r.id,r.locator||'',r.note||'',r.relation||'',...techDims.map(d=>vals(i,d).map(v=>dimTitle(d,v)).join('、'))])));box.append(tech);
    box.append(memberBox=el('div','members'));
   }else{
    // No empty table: say why there are no rows (contract 14).
    const s=i.third_parties.find(x=>['图上无','归入他项','缺图'].includes(x.conclusion));
    if(s?.conclusion==='图上无')box.append(para('没有明细','图上无'+(s.search_incomplete?'；检索记录不全':'')+(searchText(s)?'；检索依据：'+searchText(s):'')));
    else if(s?.conclusion==='归入他项')box.append(linksPara('没有明细：'+term('归入他项')+'，去向',s.redirect_to.map(itemLink)));
    else if(s?.conclusion==='缺图')box.append(para('没有明细','缺图：'+(s.missing_drawing_note||'')));
    else box.append(para('没有明细','本条目没有明细行'));
   }
  };
  for(const part of mode==='audit'?[comparisonPart,oursPart]:[oursPart,comparisonPart])part();
 }
 let memberBox=null,excluded=new Set();
 function members(r,ms){
  memberBox.replaceChildren();const spec=c.member_tables.find(t=>t.id===r.member_table),cols=spec?spec.columns:[{key:'id',title:'编号'},{key:'title',title:'名称'},{key:'status',title:'状态'},{key:'source',title:'来源'}];
  const val=(o,k)=>k.startsWith('attributes.')?o.attributes?.[k.slice(11)]:k.startsWith('quantities.')?(o.quantities.find(q=>q.name===k.slice(11))||{}).value:o[k];
  memberBox.append(el('h3',null,(spec?.title||'成员')+'（'+ms.length+'）'+(r.aggregation==='sum_members'?' · 本行 = 成员“'+r.member_quantity_name+'”之和':r.aggregation==='declared'?' · '+(r.aggregation_reason||''):'')));
  memberBox.append(table([...cols.map(x=>x.title),'回图'],ms.map(o=>[...cols.map(x=>{const v=val(o,x.key);return v==null?'':typeof v==='number'?num(v):String(v);}),o.kind!=='unlocated'&&hasScenes?btn('回图',()=>{at=Math.max(0,located.indexOf(o));mapState='open';map();},'link'):'未定位'+(core.unlocatedText(o)?'：'+core.unlocatedText(o):'')])));
 }
 const involved=ids=>table(['条目',...(overDim?[overDim.title]:[]),'单位',...LEVELS.map(x=>x.replace('量','')),'合计',tp+'量','结论'],ids.map(id=>itemMap.get(id)).filter(Boolean).map(i=>{const s=i.kind==='third_party'?seg(i)||i.third_parties[0]:null;return [itemLink(i.id),...(overDim?[vals(i,overDim).map(v=>dimTitle(overDim,v)).join('、')]:[]),i.ours.unit,...LEVELS.map(x=>num(i.ours.levels[x])),num(i.ours.total),s?num(s.quantity)+(s.unit!==i.ours.unit?' '+s.unit:''):EXTRA_SHORT,s?conclusionText(s):''];}));
 function optionsTable(x){return table(['做法','影响（专业算好的，与现做法差多少；不求和）',''],x.options.map(o=>[o.id+' '+o.text,impactText(o.impact)||'—',o.id===x.current_option?'现做法':'']));}
 function issueDetail(q){
  const box=byId('audit-detail');box.append(el('h2',null,q.id+' · '+q.title));
  const tags=el('div');tags.append(el('span','badge warn',natureLabel(natureOf(q))),el('span','badge warn',handlerLabel(q.handler)));if(q.status)tags.append(el('span','badge',q.status));for(const d of dims.filter(d=>d.role!=='technical')){const v=vals(q,d);if(v.length)tags.append(el('span','badge',d.title+' '+v.map(x=>dimTitle(d,x)).join('、')));}box.append(tags);
  if(q.common_issue_id){const g=commonMap.get(q.common_issue_id);if(g)box.append(linksPara('所属共性问题',[targetLink(g),el('span','minor',inheritText(q))]));}
  box.append(para('问题',q.text));if(q.impact_text)box.append(para('影响',q.impact_text));
  if(q.impact!=null)box.append(para('影响量',impactText(q.impact)));   // one or several segments; value may be null with a note (contract 6)
  box.append(para('现在的做法',q.current_practice||(q.current_option?optionText(q,q.current_option):'未写现做法')));
  if(q.options?.length)box.append(optionsTable(q));
  if(q.rule_ref)box.append(para('口径','已按口径 '+q.rule_ref+' 处理'));if(q.plugin_issue_ref)box.append(para('插件问题登记',q.plugin_issue_ref));
  if(q.item_ids.length){box.append(el('h3',null,'涉及量（各条目原值，不跨条目求和）'));box.append(involved(q.item_ids));}
  if(q.source_refs.length)box.append(table(['原条目','原文'],q.source_refs.map(r=>typeof r==='string'?[r,'']:[r.ref,r.text||''])));
  if(q.group_id)box.append(linksPara('相关问题组',[link(q.group_id,()=>{setMode('review');api.selectGroup(q.group_id);route({group:q.group_id});})]));
 }
 function commonDetail(g){
  const box=byId('audit-detail');box.append(el('h2',null,g.implied?'清单的问题 · '+g.list_type:g.id+' · '+g.question));
  const tags=el('div');tags.append(el('span','badge warn',natureLabel(natureOf(g))),el('span','badge warn',handlerLabel(g.handler)));if(g.rule_ref)tags.append(el('span','badge','口径 '+g.rule_ref));if(g.status)tags.append(el('span','badge',g.status));if(g.implied)tags.append(el('span','badge','按偏差归因归出'));box.append(tags);
  if(g.implied)box.append(para('问法',g.question));
  box.append(para('情况',g.text||''));
  if(g.options?.length&&!g.implied)box.append(optionsTable(g));else if(g.current_option)box.append(para('现做法',optionText(g,g.current_option)));
  if(g.plugin_issue_ref)box.append(para('插件问题登记',g.plugin_issue_ref));
  const a=answerOf(g);box.append(para('答复状态',commonState(g)));
  if(a){const n=addedSince(g,a);if(n.issue_ids.length||n.item_ids.length)box.append(linksPara('答复后新增（请抽查）',[...n.issue_ids.map(issueLink),...n.item_ids.map(itemLink)]));}
  box.append(el('h3',null,'适用范围（个别问题 '+g.scope.issue_ids.length+' · 条目 '+g.scope.item_ids.length+'）'));
  if(g.scope.issue_ids.length)box.append(table(['个别问题',...(overDim?[overDim.title]:[]),'处理方','状态'],g.scope.issue_ids.map(id=>issueMap.get(id)).filter(Boolean).map(q=>[targetLink(q),...(overDim?[vals(q,overDim).map(v=>dimTitle(overDim,v)).join('、')]:[]),handlerLabel(q.handler),answerOf(q)?.exception_of?'例外':inheritText(q)])));
  if(g.implied){
   // The group answer must show which rows were decided not to count (contract 5.5), or the list maker gets the blame.
   const segs=g.segments.map(s=>[itemMap.get(s.item_id),s.source_id]).filter(([i])=>i).map(([i,sid])=>[i,seg(i,sid)]);
   const ex=segs.filter(([i])=>excludedText(i));if(ex.length)box.append(el('div','hint warn','含已定不计 '+ex.length+' 条：本次少于清单的部分有按口径不计的，不是清单编制方的错，答复前请看下表“已定不计”一列的依据'));
   box.append(table(['条目',...(c.sources.length>1?['来源']:[]),'单位',tp+'量','本次合计','差额','差额率','结论','归因','已定不计'],segs.map(([i,s])=>[itemLink(i.id),...(c.sources.length>1?[c.sources.find(x=>x.id===s.source_id)?.title||s.source_id]:[]),i.ours.unit,num(s.quantity),num(i.ours.total),num(s.difference?.value),pct(s.difference?.rate),conclusionText(s),s.attribution==='both'?'部分是清单的问题，超出 '+num(s.excess):ATTRIBUTION[s.attribution]||'',excludedText(i)?excludedText(i)+'：'+i.detail_rows.filter(r=>r.excluded).map(r=>r.excluded.note+(r.excluded.rule_ref?'（口径 '+r.excluded.rule_ref+'）':'')+(r.excluded.issue_id?'（问题 '+r.excluded.issue_id+'）':'')).join('；'):''])));
  }else if(g.scope.item_ids.length)box.append(involved(g.scope.item_ids));
 }
 function detail(){
  const box=byId('audit-detail');box.replaceChildren();memberBox=null;
  if(!current){box.append(el('h2',null,'总览'),overviewCards());return;}
  ({item:itemDetail,issue:issueDetail,common:commonDetail})[kindOf(current)](current);
 }

 // ---------- map (contract 8): auto / open / close; no scenes -> never shown ----------
 function memberObjects(x){
  if(!x)return [];const k=kindOf(x),ids=k==='item'?[x.id]:k==='issue'?x.item_ids:x.scope.item_ids;
  return Array.from(new Set(ids.map(id=>itemMap.get(id)).filter(Boolean).flatMap(i=>i.detail_rows.flatMap(r=>r.member_keys)))).map(k=>objects.get(k)).filter(Boolean);
 }
 const targets=x=>memberObjects(x).filter(o=>o.kind!=='unlocated'&&o.scene_id);
 function map(){
  const slot=byId('audit-map-slot'),bar=byId('audit-mapbar');bar.hidden=!hasScenes;
  for(const [id,s] of [['map-auto','auto'],['map-open','open'],['map-close','close']])byId(id).classList.toggle('on',mapState===s);
  located=targets(current);at=Math.min(at,Math.max(0,located.length-1));
  const visible=hasScenes&&(mapState==='open'||(mapState==='auto'&&located.length>0));slot.hidden=!visible;
  // Scene mapping contract 8: say how many members can be shown on the drawing and how many cannot.
  const lost=current?memberObjects(current).length-located.length:0,lostText=lost?' · 未定位 '+lost:'';
  byId('map-note').textContent=!hasScenes?'':located.length?'可回图 '+located.length+lostText+' · 第 '+(at+1)+' 处':current?'当前目标没有可定位成员'+lostText+(mapState==='auto'?'，图面已自动收起':''):'';
  byId('member-prev2').disabled=byId('member-next2').disabled=located.length<2;
  if(visible){api.resize();api.locate(located,located[at]||memberObjects(current).find(o=>o.kind==='unlocated'));}
  else api.locate([],null);
 }
 function step(d){if(!located.length)return;at=(at+d+located.length)%located.length;map();}

 // ---------- editor (contract 9, 9.1): commons answer a whole scope; what they cover may only take exceptions ----------
 const verdictSelect=byId('audit-verdict'),optionSelect=byId('audit-option'),exceptionSelect=byId('audit-exception');let excChoice=null;
 const fill=(s,options,value)=>{s.replaceChildren();for(const [v,t] of options){const o=el('option',null,t);o.value=v;s.append(o);}s.value=value;};
 const show2=(id,on)=>{byId(id).hidden=!on;const l=byId(id+'-label');if(l)l.hidden=!on;};
 // How the current target may be answered: own answer, exception to an answered common, or not here at all.
 function editRule(x){
  const k=kindOf(x),cov=covering(x).filter(isOpen),done=cov.filter(g=>answerOf(g));   // a closed common is a settled rule, not an answer to wait for
  if(k==='common')return {how:'own'};
  if(cov.length)return done.length?{how:'exception',commons:done,waiting:cov.filter(g=>!answerOf(g))}:{how:'blocked',why:'本处按共性问题 '+cov.map(shortOf).join('、')+' 答复；共性问题还没答复，先答共性问题，再决定这里要不要设为例外'};
  if(k==='item'&&mode==='audit')return {how:'blocked',why:'对照审核里条目不逐条审：清单的问题在分组上整组答复，本次依据不足的到所挂问题答复；条目自己的数据问题在成果里写意见'};
  return {how:'own'};
 }
 function editor(){
  const x=current,fields=['audit-verdict','audit-option','audit-exception','audit-result','audit-reason'];
  if(!x){for(const id of fields)byId(id).disabled=true;byId('audit-save').disabled=true;byId('audit-editor-title').textContent='先选问题或条目';for(const id of ['audit-result','audit-reason'])byId(id).value='';for(const id of ['audit-history','audit-entry-status','audit-excluded','audit-inherit'])byId(id).textContent='';show2('audit-option',false);show2('audit-exception',false);fill(verdictSelect,[['','（不选）']],'');return;}
  if(root.QtoReviewIdentity?.active){byId('audit-form').hidden=true;root.QtoReviewIdentity.active.focusTarget(kindOf(x),x.id);return;}
  const allowed=!data.delivery||core.deliveryAllowsTarget(data,kindOf(x),x.id);if(data.delivery)byId('audit-form').hidden=!allowed;if(!allowed)return;
  const k=kindOf(x),r=noteOf(x)||{},f=feedback(),key=keyOf(x),rule=editRule(x),own=savedOf(x);
  byId('audit-editor-title').textContent=({item:'条目：',issue:'问题：',common:'共性问题：'})[k]+titleOf(x);
  byId('audit-inherit').textContent=[k==='common'?'答复对适用范围内的个别问题 '+x.scope.issue_ids.length+' 条、条目 '+x.scope.item_ids.length+' 条一起生效。':'',inheritText(x),rule.how==='blocked'?rule.why:'',k==='item'&&overlap(x)?'同一条目有两处答复，请核对。':''].filter(Boolean).join('\n');
  const exc=rule.how==='exception';show2('audit-exception',exc);
  const want=excChoice??r.exception_of;if(exc)fill(exceptionSelect,[['','（不设例外，按共性答复）'],...rule.commons.map(g=>[g.id,'设为例外：不按 '+shortOf(g)+' 的答复'])],rule.commons.some(g=>g.id===want)?want:'');
  const editable=rule.how==='own'||(exc&&exceptionSelect.value!=='');
  const labels=VERDICT_LABELS[k==='item'&&!exc?'item':'issue'];fill(verdictSelect,[['','（不选）'],...Object.entries(labels)],r.verdict||'');
  const opts=k!=='item'&&!x.implied&&x.options?.length?x.options:[];show2('audit-option',opts.length>0);
  if(opts.length)fill(optionSelect,[['','（不选做法）'],...opts.map(o=>[o.id,o.id+' '+o.text+(o.id===x.current_option?'（现做法）':'')])],r.option_id||'');
  for(const id of ['audit-verdict','audit-option','audit-result','audit-reason'])byId(id).disabled=!editable;exceptionSelect.disabled=!exc;byId('audit-save').disabled=!editable;
  byId('audit-result').value=r.confirmed_result||'';byId('audit-reason').value=r.reason||'';
  byId('audit-result').placeholder=exc?'写明这一处为什么不按共性答复、应该怎么算':k==='item'?'选了快捷结论可留空':'选“不按现做法”时，选另一个做法或写明应该怎么算；选了快捷结论可留空';
  byId('audit-excluded').textContent=k==='item'?'明细例外：'+((r.excluded_detail_row_ids||[]).join('、')||'无'):'';
  byId('audit-history').textContent=(r.history||[]).map((h,n)=>(n+1)+'. '+(h.updated_at||'')+' '+(h.verdict?labels[h.verdict]||h.verdict:'')+(h.option_id?' '+h.option_id:'')+(h.exception_of?' 例外：'+h.exception_of:'')+'\n'+(h.confirmed_result||'')+'\n原因：'+(h.reason||'')).join('\n\n')||'暂无修改历史';
  byId('audit-entry-status').textContent=own?.merge_conflict?'意见冲突：合并时两份意见不同（'+own.merge_conflict.candidates.map(y=>(labels[y.verdict]||y.verdict||'未选')+(y.option_id?' '+y.option_id:'')+(y.exception_of?' 例外':'')).join(' / ')+'），未采用任何一份；重新保存其中一条才消除。'
   :r.applicability_state==='conflict'?'跨入口意见冲突，动作暂挂：'+r.applicability_reason:(r.applicability_state==='stale'||r.applicability_state==='unmatched')?'历史意见待重新核对：'+r.applicability_reason:f.drafts[key]?'当前有草稿，未保存为意见。':f.annotations[key]?'已有保存意见；所属专业复核后才更新成果。':'尚未保存意见。';
 }
 function form(){
  const x=current,k=kindOf(x),key=keyOf(x),old=feedback().annotations[key]||{};
  const out={...old,result_fingerprint:x.result_fingerprint,applicability_state:'current',applicability_reason:'用户本轮重新保存意见',title:titleOf(x),
   verdict:verdictSelect.value,confirmed_result:byId('audit-result').value,reason:byId('audit-reason').value,entry_origin:'页面草稿，未保存',updated_at:new Date().toISOString(),
   excluded_detail_row_ids:k==='item'?x.detail_rows.map(r=>r.id).filter(id=>excluded.has(id)):[]};
  for(const f of ['item_snapshot','issue_snapshot','common_issue_snapshot','page_view','option_id','exception_of','merge_conflict'])delete out[f];
  if(k!=='item'&&!byId('audit-option').hidden&&optionSelect.value)out.option_id=optionSelect.value;
  if(k!=='common'&&!byId('audit-exception').hidden&&exceptionSelect.value)out.exception_of=exceptionSelect.value;
  // Record what the reviewer saw under the page tolerance (contract 9); staleness still binds to the data conclusion.
  if(k==='item'&&pageBand!=null&&x.third_parties.length)out.page_view={consistent_band:split?{...pageBand}:pageBand,conclusions:Object.fromEntries(x.third_parties.filter(s=>shownOf(s)).map(s=>[s.source_id,shownOf(s)]))};
  if(k==='common')out.common_issue_snapshot={common_issue_id:x.id,result_fingerprint:x.result_fingerprint,scope:{issue_ids:[...x.scope.issue_ids],item_ids:[...x.scope.item_ids]}};
  else out[k==='issue'?'issue_snapshot':'item_snapshot']=k==='issue'?{issue_id:x.id,result_fingerprint:x.result_fingerprint}:{item_id:x.id,result_fingerprint:x.result_fingerprint};
  return out;
 }
 function stash(){if(!current||byId('audit-save').disabled)return;const k=keyOf(current);feedback().drafts[k]=core.cleanRecord(form(),k,false);api.backup();byId('audit-entry-status').textContent='已暂存草稿；尚未保存为意见。';}
 for(const id of ['audit-result','audit-reason'])byId(id).oninput=stash;verdictSelect.onchange=stash;optionSelect.onchange=stash;exceptionSelect.onchange=()=>{excChoice=exceptionSelect.value;editor();stash();};
 byId('audit-save').onclick=()=>{if(!current||byId('audit-save').disabled)return;try{const k=keyOf(current),f=feedback(),next=form();
  // An exception that says the same as the common answer is not written (contract 9.1).
  // A target without options of its own (items, most issues) compares the verdict and wording only.
  if(next.exception_of){const a=answerOf(commonMap.get(next.exception_of)),own=kindOf(current)!=='item'&&(current.options||[]).length;if(a&&(next.verdict||'')===(a.verdict||'')&&(!own||(next.option_id||'')===(a.option_id||''))&&(!next.confirmed_result.trim()||next.confirmed_result.trim()===(a.confirmed_result||'').trim())){byId('audit-entry-status').textContent='与共性答复相同，不必单独写；未保存。';return;}}
  f.annotations[k]=core.saveRecord(f.annotations[k],next,k,new Date().toISOString());delete f.drafts[k];api.backup();editor();list();top();byId('audit-entry-status').textContent='意见已保存；所属专业复核后才更新成果。';}catch(e){byId('audit-entry-status').textContent=e.message;}};

 // ---------- modes, routing (contract 7) ----------
 function setMode(m){
  if(m==='extras'&&extras.length)F.scope='extra';   // old #mode=extras opens the results filtered to extra items
  m=ALIASES[m]||m;if(!available[m])return false;mode=m;tabs();
  if(m==='review'){audit.hidden=true;layout.hidden=false;if(hasScenes&&mapSection.parentElement!==layout)layout.insertBefore?.(mapSection,byId('review-splitter'));api.review();return true;}
  layout.hidden=true;audit.hidden=false;if(hasScenes)byId('audit-map-slot').append(mapSection);
  if(current&&!fits(current))current=null;filters();render();return true;
 }
 const fits=x=>{const k=kindOf(x);return mode==='resolve'?k!=='item'||unlinked(x)||incomplete(x):mode==='audit'?(k==='item'&&x.kind==='third_party')||(k==='common'&&!!x.implied):mode==='results'?k==='item':false;};
 function show(x){current=x;at=0;excChoice=null;excluded=new Set(noteOf(x)?.excluded_detail_row_ids||[]);render();}
 function render(){if(mode==='review')return;top();list();detail();editor();map();}
 function refresh(){render();}
 function parseHash(h){const out=Object.create(null);for(const part of String(h||'').replace(/^#/,'').split('&')){if(!part)continue;const n=part.indexOf('='),k=n<0?part:part.slice(0,n);try{out[decodeURIComponent(k)]=n<0?'':decodeURIComponent(part.slice(n+1));}catch(e){out[k]=part.slice(n+1);}}return out;}
 function route(extra){
  const p=[];if(mode)p.push(['mode',mode]);if(current&&mode!=='review')p.push([kindOf(current),current.id]);if(overview!=='全部'&&overDim)p.push(['dim',overDim.id+':'+overview]);if(source&&c.sources.length>1&&source!==c.sources[0].id)p.push(['source',source]);
  for(const [k,v] of Object.entries(extra||{}))p.push([k,v]);
  try{globalThis.history?.replaceState?.(null,'','#'+p.map(([k,v])=>k+'='+encodeURIComponent(v)).join('&'));}catch(e){}
 }
 function apply(params){
  const missing=[];
  if(params.source!=null){if(c.sources.some(s=>s.id===params.source))source=params.source;else missing.push('来源 '+params.source);}
  if(params.dim!=null){const [d,...rest]=params.dim.split(':'),v=rest.join(':'),dim=dims.find(x=>x.id===d);if(dim&&dim.values.some(x=>x.id===v)){if(dim===overDim)overview=v;else F.dims[d]=v;}else missing.push('维度 '+params.dim);}
  if(params.mode!=null&&!setMode(params.mode))missing.push('模式 '+params.mode);
  if(params.item!=null){const i=itemMap.get(params.item);if(i){if(!(params.mode!=null&&fits(i)))setMode('results');show(i);}else missing.push('条目 '+params.item);}
  if(params.issue!=null){const q=issueMap.get(params.issue);if(q){setMode('resolve');show(q);}else missing.push('问题 '+params.issue);}
  if(params.common!=null){const g=commonMap.get(params.common);if(g){setMode(g.implied&&available.audit?'audit':'resolve');show(g);}else missing.push('共性问题 '+params.common);}
  if(params.group!=null&&!(available.review&&setMode('review')&&api.selectGroup(params.group)))missing.push('问题组 '+params.group);
  if(params.object!=null&&!(available.review&&setMode('review')&&api.selectObject(params.object)))missing.push('对象 '+params.object);
  if(params.scene!=null){if(!(available.review&&setMode('review')&&api.scene(params.scene)))missing.push('图页 '+params.scene);}
  routeNote=missing.length?'未找到 '+missing.join('、')+'，已停在默认视图':'';render();top();
 }
 byId('audit-search').oninput=()=>{for(const k of Object.keys(shown))delete shown[k];refresh();};
 byId('map-auto').onclick=()=>{mapState='auto';map();};byId('map-open').onclick=()=>{mapState='open';map();};byId('map-close').onclick=()=>{mapState='close';map();};
 byId('member-prev2').onclick=()=>step(-1);byId('member-next2').onclick=()=>step(1);
 byId('audit-top').hidden=false;
 const defaultMode=items.length?'results':toAnswer().length?'resolve':data.groups.length?'review':'resolve';
 setMode(defaultMode);
 const hash=globalThis.location?.hash;if(hash&&hash.length>1)apply(parseHash(hash));
 globalThis.addEventListener?.('hashchange',()=>apply(parseHash(globalThis.location?.hash)));
 const find=id=>itemMap.get(id)||issueMap.get(id)||commonMap.get(id);
 return root.QtoReviewAudit.active={refresh(){if(mode&&mode!=='review'){excluded=new Set(current&&noteOf(current)?.excluded_detail_row_ids||[]);render();}},setMode,show:id=>{const x=find(id);if(x){if(!(mode&&mode!=='review'&&fits(x)))setMode(kindOf(x)==='item'?'results':x.implied&&available.audit?'audit':'resolve');show(x);}return !!x;},apply:h=>apply(parseHash(h)),state:()=>({mode,current:current?.id||null,overview,source,mapState,located:located.length})};
}
root.QtoReviewAudit={attach};
})(globalThis);
