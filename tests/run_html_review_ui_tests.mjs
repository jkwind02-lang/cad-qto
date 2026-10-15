import {spawnSync} from 'node:child_process';import fs from 'node:fs';import path from 'node:path';import vm from 'node:vm';import assert from 'node:assert/strict';import {gzipSync} from 'node:zlib';
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],root=arg('--root'),report=JSON.parse(fs.readFileSync(arg('--report'),'utf8')),art=report.artifacts,A=path.join(root,'_qto-shared-evidence','assets','review');
const data=JSON.parse(fs.readFileSync(art.normalized_view,'utf8')),sourceHTML=fs.readFileSync(art.html,'utf8');
function harness(html,extra={}){
 const all=[],ids={},downloads=[],blobs=new Map(),cache=new Map();
 class El{
  constructor(tag='div',id=''){this.tagName=tag;this.id=id;this.children=[];this._text='';this.value='';this.dataset={};this.className='';this.hidden=false;this.disabled=false;this.checked=false;this.style={values:{},setProperty:(k,v)=>this.style.values[k]=v};this.classList={add:c=>{if(!this.className.split(' ').includes(c))this.className+=' '+c;},remove:c=>{this.className=this.className.split(' ').filter(x=>x!==c).join(' ');},toggle:(c,v)=>{if(v===undefined)v=!this.className.split(' ').includes(c);v?this.classList.add(c):this.classList.remove(c);}};all.push(this);}
  set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}get options(){return this.children;}
  append(...c){this.children.push(...c);}replaceChildren(...c){this.children=c;this._text='';}setAttribute(k,v){this[k]=v;}
  getBoundingClientRect(){return this.id==='layout'?{width:1450,height:760,left:0,top:0}:this.className==='nav'?{width:260}:this.className==='review-pane'?{width:490}:{width:680,height:650,left:0,top:0};}
  getContext(){return new Proxy({measureText:t=>({width:t.length*7})},{get:(t,k)=>k in t?t[k]:()=>{}});}setPointerCapture(){}remove(){this.removed=true;}
  click(){if(this.tagName==='a')downloads.push({name:this.download,blob:blobs.get(this.href)});else return this.onclick?.();}toBlob(fn){fn(new Blob(['test-canvas']));}showModal(){this.open=true;}close(){this.open=false;}
 }
 for(const match of html.split('<script id="feedback-seed"')[0].matchAll(/\bid="([^"]+)"/g))ids[match[1]]=new El('div',match[1]);
 ids.payload=new El('script','payload');ids['feedback-seed']=new El('script','feedback-seed');
 ids.payload._text=html.match(/<script id="payload" type="application\/octet-stream">([\s\S]*?)<\/script>/)[1];ids['feedback-seed']._text=html.match(/<script id="feedback-seed" type="application\/json">([\s\S]*?)<\/script>/)[1];
 if(ids['problem-config'])ids['problem-config']._text=html.match(/<script id="problem-config" type="application\/json">([\s\S]*?)<\/script>/)[1];
 const nav=new El();nav.className='nav';const rp=new El();rp.className='review-pane';ids.cad.parentElement=new El();
 const document={getElementById:id=>ids[id]||null,createElement:tag=>new El(tag),documentElement:{outerHTML:html.replace(/^<!doctype html>/i,'')},querySelector:s=>s==='.nav'?nav:rp,querySelectorAll:s=>all.filter(e=>e.className.split(' ').includes(s.slice(1)))};
 class Image{constructor(){this.complete=false;this.naturalWidth=0;}set src(v){this._src=v;this.complete=true;this.naturalWidth=64;this.onload?.();}}
 class TestURL extends URL{static createObjectURL(b){const k='blob-test-'+blobs.size;blobs.set(k,b);return k;}static revokeObjectURL(){}}
 const ctx=vm.createContext({document,console,Blob,FormData,Response,DecompressionStream,Uint8Array,TextEncoder,crypto,AbortSignal,atob,Image,devicePixelRatio:1,ResizeObserver:class{observe(){}},setTimeout:()=>0,URL:TestURL,fetch:()=>{throw Error('Unexpected network call');},localStorage:{getItem:k=>cache.get(k)||null,setItem:(k,v)=>cache.set(k,v)},...extra});
 return {ctx,ids,cache,downloads,async start(){vm.runInContext(fs.readFileSync(path.join(A,'review-core.js'),'utf8'),ctx);if(ids['problem-config'])vm.runInContext(fs.readFileSync(path.join(A,'problem-feedback.js'),'utf8'),ctx);if(html.includes('id="audit"'))vm.runInContext(fs.readFileSync(path.join(A,'review-audit.js'),'utf8'),ctx);await vm.runInContext(fs.readFileSync(path.join(A,'review-app.js'),'utf8'),ctx);assert.equal(ids.loading.removed,true,ids.loading.textContent);}};
}
const checks=[],h=harness(sourceHTML);await h.start();const e=h.ids;assert.equal(e.professional.options.length,14);assert.equal(e.list.children.length,13);assert.equal(e['member-list'].children.length,3);assert.equal(e['review-result'].value,'');checks.push('13专业筛选与分组初始化；建议不预填为确认');
assert.equal(e['action-message'].hidden,true);assert.match(e['review-status'].textContent,/^\d+条已保存 · \d+条草稿$/);
assert.equal(h.ctx.QtoReviewAudit.active,undefined);assert.equal(e['mode-tabs'].children.length,0);assert.equal(e.layout.hidden,false);for(const tag of ['<nav id="mode-tabs" class="mode-tabs" aria-label="页面模式" hidden>','<main id="audit" class="audit" hidden>','<div id="audit-top" hidden>'])assert(sourceHTML.includes(tag),tag);checks.push('1.0视图不挂载审核模式，审核区默认隐藏，页面与原来一致');
const headerDetails=sourceHTML.match(/<details id="header-details"([^>]*)>([\s\S]*?)<\/details>/);assert(headerDetails);assert(!/\bopen\b/.test(headerDetails[1]));for(const id of ['current-summary','summary-strip','scope-notes','export-feedback','import-feedback'])assert(headerDetails[2].includes('id="'+id+'"'));assert(sourceHTML.includes('.header-panel{position:absolute;'));checks.push('顶部默认折叠说明和摘要；展开面板脱离主布局（静态结构核对）');
e['member-next'].click();assert.equal(e.scene.value,'section');assert.match(e['editor-title'].textContent,/整组/);checks.push('跨图页下一处保持整组范围');
e['member-next'].click();assert.equal(e.unlocated.hidden,false);assert.match(e['object-facts'].textContent,/待定/);checks.push('未定位对象和null显式保留');
{const d=structuredClone(data);for(const o of d.objects){if(o.kind==='unlocated')o.unlocated_reason='handle_in_block';if(o.kind==='line')o.parts=[o.points,o.points.map(([x,y])=>[x+1,y+1])];}const ph=harness(withData(sourceHTML,d));await ph.start();ph.ids['member-next'].click();ph.ids['member-next'].click();assert.equal(ph.ids.unlocated.hidden,false);assert.match(ph.ids.unlocated.textContent,/在块内，回不到/);assert.equal(ph.ctx.QtoReviewCore.unlocatedText({unlocated_reason:'estimated',unlocated_note:'按分区估'}),'估算，没有图面路径：按分区估');checks.push('图面映射：未定位原因显示，多段线 parts 绘制');}
const cb=e['member-list'].children[2].children[0];cb.checked=false;cb.onchange();e['review-result'].value='本组按构造依据核对，各成员单位保留';e['review-result'].oninput();e['review-reason'].value='第三处缺图面定位，已取消勾选。';e['review-reason'].oninput();e['save-review'].click();
let pkg=JSON.parse([...h.cache.values()][0]);let k=Object.keys(pkg.annotations)[0],note=pkg.annotations[k];assert.equal(note.object_ids.length,2);assert.equal(note.excluded_object_keys.length,1);assert.equal(note.member_snapshot.length,3);assert.equal(Object.keys(pkg.drafts).length,0);assert.deepEqual(note.member_snapshot.map(s=>s.quantities),data.groups[0].member_snapshot.map(s=>s.quantities));checks.push('保存成员快照与例外；数量不改写');
e['save-html'].click();const savedHTML=await h.downloads.at(-1).blob.text();const restored=harness(savedHTML);await restored.start();assert.equal(restored.ids['member-list'].children[2].children[0].checked,false);assert.equal(restored.ids['review-result'].value,note.confirmed_result);checks.push('带批注单HTML独立保存恢复');
e.professional.value='qto-concrete-formwork';e.professional.onchange();assert.equal(e.list.children.length,1);assert.match(e['object-facts'].textContent,/m³/);assert.match(e['object-facts'].textContent,/m²/);checks.push('混凝土m³与模板m²分别展示');
e.professional.value='qto-components-points';e.professional.onchange();e['member-next'].click();assert.equal(e.scene.value,'raster');assert.match(e.mapstatus.textContent,/px/);checks.push('栅格图页及向下像素坐标支持');
e['add-marker'].click();e.cad.onpointerdown({pointerId:1,clientX:330,clientY:310});e.cad.onpointerup({pointerId:1,clientX:330,clientY:310});pkg=JSON.parse([...h.cache.values()][0]);assert.equal(pkg.custom_markers.length,1);assert.equal(pkg.custom_markers[0].scene_id,'raster');assert.equal(pkg.custom_markers[0].professional,'qto-components-points');checks.push('新增标记保存专业图页及坐标');
e['save-html'].click();const marked=harness(await h.downloads.at(-1).blob.text());await marked.start();assert.equal(marked.ctx.QtoReviewCore.parse(JSON.parse(marked.ids['feedback-seed'].textContent),data).custom_markers.length,1);checks.push('新增标记随HTML恢复');
e['widen-editor'].click();assert.equal(e.layout.style.values['--revieww'],'720px');checks.push('宽填写区操作保留');
const core=h.ctx.QtoReviewCore;assert.throws(()=>core.parse({...pkg,project_id:'其他项目'},data),/项目/);assert.throws(()=>core.cleanRecord({...note,selected_object_keys:['不属于本组']},k,true),/成员/);checks.push('跨项目和越组批注拒绝');
let history=undefined;for(let n=0;n<75;n++)history=core.saveRecord(history,{...note,confirmed_result:'修正'+n},k,new Date(1700000000000+n).toISOString());assert.equal(history.history.length,74);checks.push('超过30次修改历史不静默截断');
const conflicting={...pkg,annotations:{...pkg.annotations,[k]:{...note,confirmed_result:'不同意见',updated_at:'2099-01-01T00:00:00Z'}}};const merged=core.mergePackages(pkg,conflicting);assert.equal(merged.annotations[k].confirmed_result,'不同意见');assert(merged.annotations[k].history.some(r=>r.confirmed_result===note.confirmed_result));checks.push('冲突合并保留旧意见历史');
const changed=structuredClone(data);changed.receipts[k]={status:'已复算',assistant_response:'按所属专业重算完成，原单位保留',source_annotation_updated_at:note.updated_at,source_result_fingerprint:note.result_fingerprint,object_ids:note.object_ids};let withReceipt=savedHTML.replace(/(<script id="payload" type="application\/octet-stream">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+gzipSync(Buffer.from(JSON.stringify(changed))).toString('base64').match(/.{1,180}/g).join('\n')+b);
const rh=harness(withReceipt);await rh.start();assert.match(rh.ids['current-adoption'].textContent,/已复算/);assert.equal(core.receiptState(changed.receipts[k],note),'current');assert.equal(core.receiptState(changed.receipts[k],{...note,updated_at:'2099-02-01T00:00:00Z'}),'stale');checks.push('回执绑定实际处理的批注版本');
const malicious={...note,confirmed_result:'</script><script>globalThis.bad=true</script>',reason:'文字原样保留'};const safe=core.embed(sourceHTML,{...pkg,annotations:{[k]:malicious}});assert(safe.includes('\\u003c/script>'));assert(!safe.includes('<script>globalThis.bad=true'));checks.push('批注文字不能关闭数据块执行脚本');
function withData(html,d){return html.replace(/(<script id="payload" type="application\/octet-stream">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+gzipSync(Buffer.from(JSON.stringify(d))).toString('base64').match(/.{1,180}/g).join('\n')+b);}
const many=structuredClone(data),group0=many.groups[0];for(let n=0;n<110;n++){const o=structuredClone(many.objects[0]);o.id='分页对象-'+n;o.catalog_key='分页|'+o.id;many.objects.push(o);group0.member_keys.push(o.catalog_key);group0.member_snapshot.push({catalog_key:o.catalog_key,object_id:o.id,title:o.title,scene_id:o.scene_id,professional:o.professional,quantities:o.quantities,attributes:o.attributes});}
const mh=harness(withData(sourceHTML,many));await mh.start();assert.equal(mh.ids['member-list'].children.length,80);mh.ids['members-next'].click();assert.equal(mh.ids['member-list'].children.length,33);const lastcb=mh.ids['member-list'].children.at(-1).children[0];lastcb.checked=false;lastcb.onchange();assert.match(mh.ids['selected-count'].textContent,/112 \/ 113/);mh.ids['mode-objects'].click();assert.equal(mh.ids.list.children.length,60);mh.ids['list-next'].click();assert.equal(mh.ids.list.children.length,60);checks.push('对象与组成员分页不截断全部选择范围');
if(args.includes('--legacy-html')){
 const legacyHTML=fs.readFileSync(arg('--legacy-html'),'utf8'),lv=JSON.parse(fs.readFileSync(arg('--legacy-view'),'utf8')),legacy=harness(legacyHTML);await legacy.start();assert.equal(legacy.ids.category.value,'待补资料');assert.equal(legacy.ids.list.children.length,4);
 const originalLegacy=JSON.parse(legacy.ids['feedback-seed'].textContent);assert.equal(Object.keys(originalLegacy.annotations).length,47);assert.equal(Object.keys(originalLegacy.drafts).length,1);legacy.ids['save-html'].click();const saved=await legacy.downloads.at(-1).blob.text();const again=legacy.ctx.QtoReviewCore.parse(legacy.ctx.QtoReviewCore.readFileText(saved),lv);assert.deepEqual(JSON.parse(JSON.stringify(again.annotations)),originalLegacy.annotations);assert.deepEqual(JSON.parse(JSON.stringify(again.drafts)),originalLegacy.drafts);assert(Buffer.byteLength(saved)<2000000);checks.push('真实旧版47条意见及1空白草稿在新界面往返保全');
}
// Rebuild the changed engineering snapshot through the actual Python contract.
const changedResult=structuredClone(data);changedResult.objects[0].quantities[0].value+=10;
const normalizeResult=spawnSync('python',['-c',"import sys,json;sys.path.insert(0,sys.argv[1]);from qto_review_contract import normalize_view;print(json.dumps(normalize_view(json.load(sys.stdin)),ensure_ascii=False))",path.resolve(root,'_qto-shared-evidence/scripts')],{input:JSON.stringify(changedResult),encoding:'utf8'});
assert.equal(normalizeResult.status,0,normalizeResult.stderr);
const renewed=JSON.parse(normalizeResult.stdout),staleHarness=harness(withData(savedHTML,renewed));await staleHarness.start();
assert.match(staleHarness.ids['entry-status'].textContent,/历史意见待重新核对/);
const stalePkg=staleHarness.ctx.QtoReviewCore.parse(staleHarness.ctx.QtoReviewCore.readFileText(savedHTML),renewed);
assert.equal(stalePkg.annotations[k].applicability_state,'stale');assert.equal(stalePkg.annotations[k].confirmed_result,note.confirmed_result);
assert.equal(core.receiptState(changed.receipts[k],stalePkg.annotations[k]),'stale');
staleHarness.ids['save-review'].click();const renewedPkg=JSON.parse([...staleHarness.cache.values()][0]);
assert.equal(renewedPkg.annotations[k].result_fingerprint,renewed.groups[0].result_fingerprint);
assert(renewedPkg.annotations[k].history.some(r=>r.result_fingerprint===note.result_fingerprint));
checks.push('成果变化显式显示历史意见；用户重存后绑定新成果并保留旧历史');

const packageFile=path.join(path.dirname(art.html),'UI实际保存批注.json');fs.writeFileSync(packageFile,JSON.stringify(pkg,null,2));
const fh=harness(sourceHTML);await fh.start();fh.ctx.localStorage.setItem=()=>{throw new Error('模拟暂存不可用');};fh.ids['save-html'].click();assert.equal(fh.ids['action-message'].hidden,false);assert.match(fh.ids['action-message'].textContent,/本机暂存不可用/);assert(fh.downloads.length>0);fh.ids['import-file'].files=[{text:async()=>'{invalid'}];await fh.ids['import-file'].onchange();assert.match(fh.ids['action-message'].textContent,/导入未应用/);assert.equal(fh.ids['action-message'].hidden,false);checks.push('顶部精简后暂存失败与导入失败仍显式提示，HTML仍可下载');
if(h.ids['problem-config']){
 e['report-problem'].click();assert.equal(e['problem-dialog'].open,true);assert.match(e['problem-destination'].textContent,/尚未开通/);assert.equal(e['problem-send'].disabled,true);e['problem-close'].click();
 checks.push('反馈未配置仍可离线保存；不会自动联网');
 const configuredHTML=savedHTML.replace(/(<script id="problem-config" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,b,c)=>a+JSON.stringify({...JSON.parse(b),url:'https://feedback.example'})+c);
 const ph=harness(configuredHTML);await ph.start();const pe=ph.ids,requests=[];let fail=true;
 assert.equal(pe['problem-code'],undefined,'上传不再显示反馈码输入框');
 ph.ctx.fetch=async(url,options)=>{assert.equal(options.headers?.Authorization,undefined,'收件请求不携带团队码');const meta=JSON.parse(options.body.get('metadata')),file=options.body.get('html');requests.push({url,meta,file});assert(ph.downloads.length>0,'上传前必须先保存本地HTML');assert.equal(await file.text(),await ph.downloads.at(-1).blob.text(),'本地和上传必须完全同字节内容');if(fail)throw Error('模拟网络断开');return Response.json({accepted:true,receipt:'FB-000001',status:'saved',sha256:meta.sha256,bytes:file.size});};
 pe['save-html'].click();assert.equal(pe['problem-dialog'].open,true);assert.match(pe['problem-preview'].textContent,/完整HTML/);assert.equal(requests.length,0);
 await pe['problem-send'].click();assert.equal(requests.length,0);assert.match(pe['problem-message'].textContent,/确认发送完整HTML/);
 pe['problem-consent'].checked=true;await pe['problem-send'].click();assert.equal(requests.length,1);assert.equal(ph.downloads.length,1);assert.match(pe['problem-message'].textContent,/未完成/);
 const localCopy=await ph.downloads[0].blob.text();assert(!localCopy.includes('problem-code'));assert(localCopy.includes('feedback-seed'));const delivered=ph.ctx.QtoReviewCore.readFileText(localCopy);assert.equal(delivered.annotations[k].confirmed_result,note.confirmed_result);assert.equal(delivered.annotations[k].reason,note.reason);assert.equal(requests[0].meta.project_id,data.project_id);
 fail=false;pe['problem-consent'].checked=true;await pe['problem-send'].click();assert.equal(requests.length,2);assert.equal(requests[0].meta.report_id,requests[1].meta.report_id);assert.equal(requests[0].meta.sha256,requests[1].meta.sha256);assert.match(pe['problem-message'].textContent,/本地与服务器文件校验一致/);
 pe['review-reason'].value='增加未保存草稿';pe['review-reason'].oninput();await pe['problem-send'].click();assert.equal(requests.length,3);assert.notEqual(requests[0].meta.report_id,requests[2].meta.report_id);assert.equal(requests[2].meta.draft_count,1);
 checks.push('无需反馈码，确认后即可发送；合并保存先落本地；整份HTML和上传逐字节一致，断网原文件重试，意见变化后生成新文件与编号');
 const rph=harness(localCopy);await rph.start();rph.ids['save-html'].click();assert.equal(rph.ids['problem-dialog'].open,true);
 const one=await rph.ctx.QtoProblemFeedback.hash(await requests[0].file.arrayBuffer());assert.equal(one,requests[0].meta.sha256);
 assert.throws(()=>rph.ctx.QtoProblemFeedback.endpoint('https://user:secret@feedback.example'),/HTTPS/);assert.throws(()=>rph.ctx.QtoProblemFeedback.endpoint('http://feedback.example'),/HTTPS/);
 checks.push('完整HTML保留项目、图面、结论、原因和草稿；保存后独立恢复，拒绝含凭据的接收地址');
 const dualHTML=configuredHTML.replace(/(<script id="problem-config" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,b,c)=>a+JSON.stringify({...JSON.parse(b),provider:'tencent',url:'https://tencent.example',backup_url:'https://backup.example'})+c);
 const begin=async(x)=>{await x.start();x.ids['save-html'].click();x.ids['problem-consent'].checked=true;};
 const th=harness(dualHTML);await begin(th);const tcalls=[];let tm;
 th.ctx.fetch=async(url,o)=>{tcalls.push(url);
  if(url.endsWith('/init')){assert.equal(o.headers.Authorization,undefined);tm=JSON.parse(o.body);return Response.json({ticket:'synthetic-ticket',upload:{url:'https://test-123456.cos.ap-shanghai.myqcloud.com/',fields:{key:'staging/test.html'}}});}
  if(url.includes('myqcloud.com')){assert.equal(await o.body.get('file').text(),await th.downloads.at(-1).blob.text());return new Response(null,{status:204});}
  assert(url.endsWith('/complete'));assert.equal(o.headers.Authorization,undefined);return Response.json({accepted:true,receipt:'TC-test',sha256:tm.sha256,bytes:tm.bytes});};
 await th.ids['problem-send'].click();assert.equal(tcalls.length,3);assert(!tcalls.some(u=>u.includes('backup.example')));assert.match(th.ids['problem-message'].textContent,/腾讯云/);
 await th.ids['problem-send'].click();assert.equal(tcalls.length,3,'已收件的同一快照不会重复上传');
 checks.push('腾讯云主站直传COS后确认回执；正常收件不调用备用站，已收快照不重复发送');
 const bh=harness(dualHTML);await begin(bh);const bcalls=[];let bm;
 bh.ctx.fetch=async(url,o)=>{bcalls.push(url);
  if(url.endsWith('/init')){bm=JSON.parse(o.body);throw Error('lost response');}
  if(url.includes('/api/status/'))return Response.json({error:'未找到'},{status:404});
  if(url.endsWith('/api/info'))return Response.json({provider:'cloudflare',role:'backup',ready:true});
  assert.equal(url,'https://backup.example/api/reviews');assert.equal(o.headers.Authorization,undefined);const meta=JSON.parse(o.body.get('metadata'));assert.equal(meta.report_id,bm.report_id);assert.equal(meta.sha256,bm.sha256);assert.equal(await o.body.get('html').text(),await bh.downloads.at(-1).blob.text());
  return Response.json({accepted:true,receipt:'FB-000002',sha256:meta.sha256,bytes:o.body.get('html').size});};
 await bh.ids['problem-send'].click();assert.equal(bcalls.length,4);assert.match(bh.ids['problem-message'].textContent,/Cloudflare备用/);assert.match(bh.ids['problem-message'].textContent,/主站收件状态仍待核对/);
 checks.push('主站超时先查回执，再按同一编号和完整字节备用收件；实际位置与主站不确定状态可见');
 const eh=harness(dualHTML);await begin(eh);let ecalls=0;eh.ctx.fetch=async()=>{ecalls++;return Response.json({error:'提交较频繁'},{status:429});};await eh.ids['problem-send'].click();assert.equal(ecalls,1);assert.match(eh.ids['problem-message'].textContent,/提交较频繁/);
 const ah=harness(dualHTML);await begin(ah);let am,acalls=0;ah.ctx.fetch=async(url,o)=>{acalls++;if(url.endsWith('/init')){am=JSON.parse(o.body);throw Error('timeout');}assert(url.includes('/api/status/'));return Response.json({accepted:true,receipt:'TC-accepted',sha256:am.sha256,bytes:am.bytes});};await ah.ids['problem-send'].click();assert.equal(acalls,2);assert.match(ah.ids['problem-message'].textContent,/TC-accepted/);
 checks.push('限流不转备用；主站已接收但首次回执丢失时查询恢复，不再发备用');
 const putHarness=harness(dualHTML);await begin(putHarness);let putMeta;putHarness.ctx.fetch=async(url,o)=>{
  if(url.endsWith('/init')){putMeta=JSON.parse(o.body);return Response.json({ticket:'put-ticket',upload:{url:'https://test-123456.cos.ap-shanghai.myqcloud.com/cad-qto-feedback/staging/sample.bin',method:'PUT',headers:{authorization:'scoped-temporary-signature',key:'scoped-path'}}});}
  if(url.includes('myqcloud.com')){assert.equal(o.method,'PUT');assert.equal(await o.body.text(),await putHarness.downloads.at(-1).blob.text());return new Response(null,{status:200});}
  return Response.json({accepted:true,receipt:'TC-put',sha256:putMeta.sha256,bytes:putMeta.bytes});};
 await putHarness.ids['problem-send'].click();assert.match(putHarness.ids['problem-message'].textContent,/TC-put/);assert.equal(putHarness.ctx.QtoProblemFeedback.endpoint('https://tencent.example/cad-qto-feedback/'),'https://tencent.example/cad-qto-feedback');
 checks.push('腾讯云开发实际PUT直传协议逐字节保全，支持专用子路径入口');
}
// ---------- R1: qto-review-view/1.1 resolve / audit modes (contract docs/维护/复核页审核视图契约.md) ----------
if(art.comparison_html){
 const find=(n,p)=>{if(p(n))return n;for(const ch of n.children||[])if(ch&&typeof ch==='object'){const r=find(ch,p);if(r)return r;}return null;};
 const cview=JSON.parse(fs.readFileSync(art.comparison_normalized,'utf8')),chtml=fs.readFileSync(art.comparison_html,'utf8'),fp=id=>cview.comparison.items.find(i=>i.id===id).result_fingerprint;
 const ch=harness(chtml);await ch.start();const x=ch.ids,act=()=>ch.ctx.QtoReviewAudit.active,row=k=>find(x['audit-list'],n=>n.dataset?.key===k),tab=t=>x['mode-tabs'].children.find(b=>b.textContent===t);
 const keysOf=()=>{const keys=[];find(x['audit-list'],n=>{if(n.dataset?.key)keys.push(n.dataset.key);return false;});return keys;},cached=()=>JSON.parse([...ch.cache.values()][0]);
 const normalize=v=>{const r=spawnSync('python',['-c',"import sys,json;sys.path.insert(0,sys.argv[1]);from qto_review_contract import normalize_view;print(json.dumps(normalize_view(json.load(sys.stdin)),ensure_ascii=False))",path.resolve(root,'_qto-shared-evidence/scripts')],{input:JSON.stringify(v),encoding:'utf8'});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);};
 const head=(box,re)=>find(box,n=>n.tagName==='button'&&re.test(n.textContent||'')&&n.onclick);
 assert.equal(x.audit.hidden,false);assert.equal(x.layout.hidden,true);assert.deepEqual(x['mode-tabs'].children.map(b=>b.textContent),['成果','待解决问题','对照审核','图面复核']);assert.equal(act().state().mode,'results');
 assert.match(x['resolve-counts'].textContent,/清单的问题 4/);assert.match(x['resolve-counts'].textContent,/未分类 1/);assert.match(x['resolve-counts'].textContent,/没有依据、也没挂问题 2/);assert.match(x['band-rule'].textContent,/默认 计量 ±5%，计数 须相等/);
 assert.deepEqual(keysOf(),['对照条目|I1','对照条目|E1','对照条目|I2','对照条目|I6']);assert.match(x['audit-list'].textContent,/一号楼 · 4 条 · 含估算 2/);assert.match(x['audit-list'].textContent,/配管 · 2 条 · 含估算 2/);
 assert.match(row('对照条目|I1').textContent,/规则推算 30 · 方案暂估 5 · 合计 35 m/);assert.match(row('对照条目|I1').textContent,/对照 合成审计清单：清单 34/);assert.match(row('对照条目|E1').textContent,/清单外新增/);assert.doesNotMatch(row('对照条目|I1').textContent,/I1 ·/);
 const scope=find(x['audit-filters'],n=>n.tagName==='select'&&n.children.some(o=>o.value==='extra'));scope.value='extra';scope.onchange();assert.match(x['audit-counter'].textContent,/筛选 1 条/);scope.value='全部';scope.onchange();
 const evid=find(x['audit-filters'],n=>n.tagName==='select'&&n.children.some(o=>o.value==='pending'));evid.value='est';evid.onchange();assert.deepEqual(keysOf(),['对照条目|I1','对照条目|E1']);evid.value='firm';evid.onchange();assert.deepEqual(keysOf(),['对照条目|I2','对照条目|I6']);evid.value='全部';evid.onchange();
 row('对照条目|I1').click();{const d=x['audit-detail'].textContent;assert(d.indexOf('本次成果')>=0&&d.indexOf('本次成果')<d.indexOf('对照：'));assert.match(d,/明细组成与依据/);assert.match(d,/依据状况：有依据（确认 \+ 规则推算） 30 · 估算 5 · 待定 0 m/);assert.match(d,/技术来源/);}
 checks.push('1.1默认打开成果：只列本次有量的条目（含清单外），按楼栋、分部分级并计条数，可按范围和依据状况筛；详情先列本次成果与明细依据，后列对照');
 tab('待解决问题').click();assert.equal(act().state().mode,'resolve');
 assert(row('问题|Q1'));assert.equal(row('问题|Q2'),null);assert(row('对照条目|E1'));assert(row('对照条目|I5'));assert(row('共性问题|清单的问题|清单多计'));assert.match(x['audit-list'].textContent,/未挂问题/);assert.match(x['audit-list'].textContent,/已处理，仅留记录 · 1/);assert.match(x['audit-list'].textContent,/未分类 · 1/);assert.match(x['audit-list'].textContent,/要的答复：确认“本次依据充分/);
 checks.push('待解决问题：按问题性质分组并写明要什么样的答复，共性问题在前；没写性质的进未分类；没挂问题的条目单列；已处理收起');
 row('问题|Q1').click();assert.match(x['audit-detail'].textContent,/涉及量/);assert.match(x['audit-detail'].textContent,/现含/);assert.match(x['audit-detail'].textContent,/配管/);
 assert.deepEqual(x['audit-verdict'].children.map(o=>o.textContent),['（不选）','按现做法','不按现做法','暂不定']);assert.equal(x['audit-exception'].hidden,true);assert.equal(x['audit-option'].hidden,true);
 x['audit-verdict'].value='要改';x['audit-verdict'].onchange();x['audit-save'].click();assert.match(x['audit-entry-status'].textContent,/须有原因/);
 x['audit-reason'].value='出桥架段应单列';x['audit-reason'].oninput();x['audit-save'].click();
 let cpkg=cached();assert.equal(cpkg.schema,'qto-review-feedback/2.1');assert.equal(cpkg.annotations['问题|Q1'].verdict,'要改');assert.equal(cpkg.annotations['问题|Q1'].issue_snapshot.issue_id,'Q1');
 checks.push('问题条目显示涉及量与现做法；批注选“按现做法/不按现做法/暂不定”，无原因不能保存');
 tab('对照审核').click();assert.equal(act().state().mode,'audit');assert.deepEqual(keysOf(),['对照条目|I2','对照条目|I6','对照条目|I3','对照条目|I2']);
 assert.match(x['audit-list'].textContent,/清单的问题 · 清单多计 · 1/);assert.match(x['audit-list'].textContent,/▸ 一致 · 1/);assert.match(x['audit-list'].textContent,/▸ 缺图（在待解决问题里） · 1/);assert.match(row('对照条目|I2').textContent,/· 清单的问题/);assert.match(x['audit-counter'].textContent,/筛选 6 条/);
 checks.push('对照审核按归因分组：清单的问题按类型一组，一致、归入他项、缺图收起；同一条目进两组时只计一次');
 const rate=find(x['audit-filters'],n=>n.type==='number'),alert=find(x['audit-filters'],n=>n.tagName==='label'&&n.children.includes(rate)),acb=find(alert,n=>n.type==='checkbox');rate.value='5';rate.oninput();acb.checked=true;acb.onchange();assert.match(x['audit-counter'].textContent,/筛选 1 条/);assert(row('对照条目|I2'));acb.checked=false;acb.onchange();
 checks.push('对照审核组内按差额率排序；差异超阈值可调');
 const tolM=find(x['band-rule'],n=>n['aria-label']==='一致容差 计量 百分比');tolM.value='2';tolM.oninput();head(x['audit-list'],/^▸ 一致 · /).click();
 assert.match(row('对照条目|I1').textContent,/多算（原：一致）/);assert.match(x['band-rule'].textContent,/按页面容差显示/);assert.doesNotMatch(row('对照条目|I2').textContent,/原：/);
 const concSel=find(x['audit-filters'],n=>n.tagName==='select'&&n.children.some(o=>o.value==='多算'));concSel.value='多算';concSel.onchange();assert(row('对照条目|I1'));concSel.value='全部';concSel.onchange();
 const srcSel=find(x['audit-filters'],n=>n.tagName==='select'&&n.children.some(o=>o.value==='BID'));srcSel.value='BID';srcSel.onchange();assert.match(x['audit-list'].textContent,/本次依据不足 · Q1 配管是否含出桥架段（未分类）/);assert.match(row('对照条目|I1').textContent,/本次依据不足/);srcSel.value='AUDIT';srcSel.onchange();
 checks.push('多来源时按当前来源归因；本次依据不足的偏差按所挂问题分组，可跳到待解决问题里答复');
 row('对照条目|I1').click();const d=x['audit-detail'].textContent;assert.match(d,/合成审计清单/);assert.match(d,/合成招标清单/);assert.match(d,/I1#1/);assert.match(d,/多算/);assert.match(d,/归因：本次依据不足，差额在本次估算和待定范围内/);assert.equal(x['audit-map-slot'].hidden,false);assert.match(x['map-note'].textContent,/可回图 3 · 第 1 处/);
 assert.equal(x['audit-save'].disabled,true);assert.match(x['audit-inherit'].textContent,/对照审核里条目不逐条审/);
 find(x['audit-detail'],n=>n.textContent==='逐回路台账 3'&&n.onclick).click();assert.match(x['audit-detail'].textContent,/起点箱/);assert.match(x['audit-detail'].textContent,/AL1/);
 x['member-next2'].click();assert.match(x['map-note'].textContent,/第 2 处/);
 tab('成果').click();row('对照条目|I1').click();assert.equal(x['audit-save'].disabled,false);
 const cb2=find(x['audit-detail'],n=>n['aria-label']==='纳入 I1#2');cb2.checked=false;cb2.onchange();x['audit-verdict'].value='要改';x['audit-verdict'].onchange();x['audit-reason'].value='暂估段待定';x['audit-reason'].oninput();x['audit-save'].click();
 cpkg=cached();const inote=cpkg.annotations['对照条目|I1'];assert.deepEqual(inote.excluded_detail_row_ids,['I1#2']);assert.equal(inote.item_snapshot.result_fingerprint,fp('I1'));assert.equal(inote.result_fingerprint,fp('I1'));
 assert.deepEqual(inote.page_view,{consistent_band:{'计量':0.02,'计数':0},conclusions:{AUDIT:'多算',BID:'多算'}});
 find(x['band-rule'],n=>n.textContent==='恢复默认'&&n.onclick).click();assert.doesNotMatch(x['audit-list'].textContent,/原：/);assert.equal(tolM.value,5);
 checks.push('页面可设容差：只重新显示一致/多算/少算并标原结论，筛选按显示结论；条目自己的意见在成果里写，对照审核里条目不逐条审；批注记下当时的容差和显示结论');
 tab('对照审核').click();row('对照条目|I3').click();assert.equal(x['audit-map-slot'].hidden,true);assert.match(x['map-note'].textContent,/自动收起/);assert.match(x['audit-detail'].textContent,/检索依据/);x['map-open'].click();assert.equal(x['audit-map-slot'].hidden,false);x['map-auto'].click();
 checks.push('条目详情并排来源与归因、明细行按台账标题下钻成员、有定位时自动展开图面并可上一处下一处；无定位自动收起，可手动展开');
 head(x['audit-list'],/^整组答复 · 未答复/).click();assert.equal(act().state().current,'清单的问题|清单多计');assert.match(x['audit-detail'].textContent,/答复状态：未答复/);assert.equal(x['audit-option'].hidden,true);
 x['audit-verdict'].value='同意';x['audit-verdict'].onchange();x['audit-reason'].value='清单多列一个，交清单编制方';x['audit-reason'].oninput();x['audit-save'].click();
 cpkg=cached();assert.deepEqual(cpkg.annotations['共性问题|清单的问题|清单多计'].common_issue_snapshot.scope,{issue_ids:[],item_ids:['I2']});assert.match(x['audit-list'].textContent,/整组答复 · 已答复：按现做法/);
 row('对照条目|I2').click();assert.equal(x['audit-exception'].hidden,false);assert.deepEqual(x['audit-exception'].children.map(o=>o.value),['','清单的问题|清单多计']);assert.equal(x['audit-save'].disabled,true);assert.match(x['audit-inherit'].textContent,/已按共性答复（清单多计：按现做法）/);assert.match(x['audit-inherit'].textContent,/待共性答复（清单规格写错）/);
 x['audit-exception'].value='清单的问题|清单多计';x['audit-exception'].onchange();assert.equal(x['audit-save'].disabled,false);
 x['audit-verdict'].value='同意';x['audit-verdict'].onchange();x['audit-reason'].value='同上';x['audit-reason'].oninput();x['audit-save'].click();assert.match(x['audit-entry-status'].textContent,/与共性答复相同，不必单独写/);
 x['audit-verdict'].value='要改';x['audit-verdict'].onchange();x['audit-result'].value='此处接线盒在墙内已计';x['audit-result'].oninput();x['audit-save'].click();
 cpkg=cached();assert.equal(cpkg.annotations['对照条目|I2'].exception_of,'清单的问题|清单多计');assert.match(row('对照条目|I2').textContent,/有例外/);
 row('对照条目|I2').click();assert.match(x['audit-inherit'].textContent,/此处已设例外，不按 清单多计 的答复（本处：要改）/);assert.doesNotMatch(x['audit-inherit'].textContent,/已按共性答复（清单多计/);
 checks.push('清单的问题整组答复一次；组内条目只能设例外，与共性答复相同时不保存；还没答的共性问题提示待答');
 const pf=path.join(path.dirname(art.comparison_html),'UI对照批注.json');fs.writeFileSync(pf,JSON.stringify(cpkg));const handoff=path.join(path.dirname(art.comparison_html),'UI对照交接.json');
 const rd=spawnSync('python',[path.resolve(root,'_qto-shared-evidence/scripts/read_qto_review_feedback.py'),'--view',art.comparison_view,'--feedback',pf,'--output',handoff],{encoding:'utf8',stdio:['ignore','pipe','pipe']});assert.equal(rd.status,0,rd.stdout+rd.stderr);
 const ho=JSON.parse(fs.readFileSync(handoff,'utf8'));assert.equal(ho.item_actions.length,2);assert.equal(ho.issue_actions.length,1);assert.equal(ho.common_issue_actions.length,1);assert.deepEqual(ho.common_issue_actions[0].scope.item_ids,[]);assert.equal(ho.quantity_changes,0);
 const i1=ho.item_actions.find(a=>a.item_id==='I1');assert.deepEqual(i1.excluded_detail_row_ids,['I1#2']);assert.deepEqual(i1.page_view,inote.page_view);assert.equal(ho.item_actions.find(a=>a.item_id==='I2').exception_of,'清单的问题|清单多计');
 checks.push('页面保存的2.1批注通过Python契约读取，生成条目、问题与共性答复交接，例外从共性答复范围里除去，不改数量');
 x['save-html'].click();const csaved=await ch.downloads.at(-1).blob.text(),cr=harness(csaved);await cr.start();cr.ctx.QtoReviewAudit.active.show('I1');assert.equal(cr.ids['audit-verdict'].value,'要改');assert.equal(find(cr.ids['audit-detail'],n=>n['aria-label']==='纳入 I1#2').checked,false);
 checks.push('带审核意见的HTML独立保存恢复');
 const calls=[],listeners={},loc={hash:'#item=I4'},rh2=harness(chtml,{location:loc,history:{replaceState:(a,b,u)=>calls.push(u)},addEventListener:(t,fn)=>{listeners[t]=fn;}});await rh2.start();
 const st=()=>rh2.ctx.QtoReviewAudit.active.state();assert.deepEqual([st().mode,st().current],['results','I4']);assert.match(rh2.ids['audit-detail'].textContent,/去向/);assert.match(rh2.ids['audit-detail'].textContent,/没有明细/);
 loc.hash='#mode=audit&item=I4';listeners.hashchange();assert.deepEqual([st().mode,st().current],['audit','I4']);
 loc.hash='#mode=issues&issue=Q2';listeners.hashchange();assert.deepEqual([st().mode,st().current],['resolve','Q2']);
 loc.hash='#common='+encodeURIComponent('清单的问题|清单漏量');listeners.hashchange();assert.deepEqual([st().mode,st().current],['audit','清单的问题|清单漏量']);
 loc.hash='#common=NOPE';listeners.hashchange();assert.match(rh2.ids['route-message'].textContent,/未找到 共性问题 NOPE/);
 loc.hash='#item=NOPE';listeners.hashchange();assert.match(rh2.ids['route-message'].textContent,/未找到 条目 NOPE/);
 loc.hash='#mode=extras';listeners.hashchange();assert.equal(st().mode,'results');assert.match(rh2.ids['audit-counter'].textContent,/筛选 1 条/);
 loc.hash='#dim=building%3AB2&mode=audit';listeners.hashchange();assert.equal(st().overview,'B2');assert.equal(rh2.ids['audit-counter'].textContent.match(/筛选 (\d+)/)[1],'3');
 find(rh2.ids['audit-list'],n=>n.dataset?.key==='对照条目|I3').click();assert.match(calls.at(-1),/item=I3/);assert.match(calls.at(-1),/dim=building%3AB2/);
 loc.hash='#object=WL2';listeners.hashchange();assert.equal(st().mode,'review');assert.equal(rh2.ids.layout.hidden,false);assert.match(rh2.ids['editor-title'].textContent,/回路 WL2/);
 checks.push('网址片段定位条目、问题、共性问题（清单的问题打开对照审核）、维度、对象；hashchange 响应；找不到时提示不报错；选中后更新地址');
 const nh=harness(fs.readFileSync(art.comparison_noscene_html,'utf8'));await nh.start();assert.equal(nh.ids['audit-mapbar'].hidden,true);assert.equal(nh.ids['audit-map-slot'].hidden,true);assert.equal(nh.ids['map-section'].hidden,true);assert(nh.ids.layout.className.includes('no-map'));
 nh.ctx.QtoReviewAudit.active.show('I1');assert.equal(nh.ids['audit-map-slot'].hidden,true);assert.match(nh.ids['audit-detail'].textContent,/I1#1/);
 checks.push('无图页视图不渲染图面区，列表与详情铺满');
 const raw=JSON.parse(fs.readFileSync(art.comparison_view,'utf8')),s0=raw.comparison.items[0].third_parties[0];s0.rows[0].quantity=33;s0.quantity=33;s0.difference={value:2,rate:0.0606};
 const sh=harness(withData(csaved,normalize(raw)));await sh.start();sh.ctx.QtoReviewAudit.active.show('I1');assert.match(sh.ids['audit-entry-status'].textContent,/历史意见待重新核对/);sh.ctx.QtoReviewAudit.active.show('Q1');assert.doesNotMatch(sh.ids['audit-entry-status'].textContent,/待重新核对/);
 checks.push('条目第三方量变化后旧意见显示过期，未变的问题意见仍有效');
 const raw2=JSON.parse(fs.readFileSync(art.comparison_view,'utf8'));raw2.comparison.legacy_search_evidence={rule:'e63fe30',note:'合成'};raw2.comparison.items.find(i=>i.id==='I3').third_parties[0].search_evidence={note:'只记了查过 P1'};
 const lh=harness(withData(chtml,normalize(raw2)));await lh.start();const lx=lh.ids,la=lh.ctx.QtoReviewAudit.active,lrow=k=>find(lx['audit-list'],n=>n.dataset?.key===k);
 la.setMode('audit');assert.match(lrow('对照条目|I3').textContent,/检索记录不全/);la.setMode('resolve');assert.match(lx['audit-list'].textContent,/检索记录不全（规则生效前交付/);assert.equal(lrow('对照条目|I3'),null);assert.doesNotMatch(lx['resolve-counts'].textContent,/检索记录不全/);
 la.show('I3');assert.equal(la.state().mode,'resolve');assert.match(lx['audit-detail'].textContent,/说明 只记了查过 P1/);assert.match(lx['audit-detail'].textContent,/检索记录不全：/);
 checks.push('规则生效前交付的图上无标“检索记录不全”：对照行有标记，待解决问题末尾单列且默认收起，不计入待答计数');
 // Fifth change (contract 14, 9.1): category level, technical dimension, decided-not-counted rows, a common issue with options.
 const raw3=JSON.parse(fs.readFileSync(art.comparison_view,'utf8')),c3=raw3.comparison,it3=id=>c3.items.find(i=>i.id===id);
 c3.dimensions.find(d=>d.id==='package').role='technical';c3.dimensions.push({id:'cat',title:'品类',role:'category',values:[{id:'C1',title:'线管'},{id:'C2',title:'箱柜'}]});
 for(const [id,v] of [['I1','C1'],['E1','C1'],['I2','C2'],['I6','C2']])it3(id).dimensions.cat=v;
 c3.common_issues=[{id:'G1',nature:'multi_method',question:'同类配管是否计出桥架段',text:'合成情况',options:[{id:'O1',text:'计出桥架段',impact:null},{id:'O2',text:'不计出桥架段',impact:[{value:-5,unit:'m',note:'一号楼'}]}],current_option:'O1',handler:'decide',item_ids:['E1'],dimensions:{building:['B1']}}];
 c3.issues.find(q=>q.id==='Q1').common_issue_id='G1';
 it3('I1').detail_rows.push({id:'I1#3',level:'方案暂估量',quantity:3,unit:'m',basis:'二期段估',area:'室外',excluded:{issue_id:'Q2',note:'图标二期，本期不计'}});
 it3('I2').detail_rows.push({id:'I2#2',level:'确认量',quantity:1,unit:'个',basis:'甲供箱内',excluded:{rule_ref:'K5',note:'甲供不计'}});
 const v3=normalize(raw3),vh=harness(withData(chtml,v3));await vh.start();const vx=vh.ids,va=vh.ctx.QtoReviewAudit.active,vrow=k=>find(vx['audit-list'],n=>n.dataset?.key===k),vcached=()=>JSON.parse([...vh.cache.values()][0]);
 assert.match(vx['audit-list'].textContent,/线管 · 2 条/);assert.match(vrow('对照条目|I1').textContent,/另有已定不计 3 m/);assert.doesNotMatch(vx['audit-filters'].textContent,/分包/);assert.match(vrow('对照条目|I1').textContent,/有多种判断方式/);
 assert.doesNotMatch(vx['audit-list'].textContent,/图标二期/);head(vx['audit-list'],/^▸ 已定不计 · 2 条/).click();assert.match(vx['audit-list'].textContent,/图标二期，本期不计/);
 va.show('I1');{const d=vx['audit-detail'].textContent;assert.match(d,/已定不计：图标二期，本期不计/);assert.match(d,/Q2 · 已处理事项/);assert.match(d,/分包/);assert.match(d,/有多种判断方式/);assert.match(d,/品类 线管/);}
 assert(find(vx['audit-detail'],n=>n.textContent==='逐回路台账 3'&&n.onclick));
 checks.push('成果按楼栋、分部、品类三级分类；已定不计在行上标出、成果末尾单列并默认收起，详情里排在计入行之后并可跳到依据；技术维度只进技术来源、不进筛选');
 va.setMode('audit');assert.match(vrow('对照条目|I2').textContent,/含已定不计 1 个/);assert.match(vx['audit-list'].textContent,/其中 1 条含已定不计/);va.show('清单的问题|清单多计');assert.match(vx['audit-detail'].textContent,/含已定不计 1 条/);assert.match(vx['audit-detail'].textContent,/1 个：甲供不计（口径 K5）/);
 checks.push('清单的问题整组答复界面标出含已定不计的条目并列出依据');
 va.setMode('results');find(vrow('对照条目|I1'),n=>n.textContent==='有多种判断方式'&&n.onclick).click();assert.deepEqual([va.state().mode,va.state().current],['resolve','G1']);
 {const d=vx['audit-detail'].textContent;assert.match(d,/适用范围（个别问题 1 · 条目 2）/);assert.match(d,/O2 不计出桥架段/);assert.match(d,/-5 m（一号楼）/);}
 assert.match(vx['audit-list'].textContent,/有多种判断方式 · 1/);assert.equal(vrow('问题|Q1'),null);assert(vrow('共性问题|G1'));assert.equal(vx['audit-option'].hidden,false);assert.deepEqual(vx['audit-option'].children.map(o=>o.value),['','O1','O2']);
 va.show('Q1');assert.equal(vx['audit-save'].disabled,true);assert.match(vx['audit-inherit'].textContent,/待共性答复（G1）/);assert.match(vx['audit-inherit'].textContent,/先答共性问题/);
 va.show('G1');vx['audit-verdict'].value='要改';vx['audit-verdict'].onchange();vx['audit-option'].value='O2';vx['audit-option'].onchange();vx['audit-reason'].value='出桥架段并入桥架';vx['audit-reason'].oninput();vx['audit-save'].click();
 let vpkg=vcached();const g1=vpkg.annotations['共性问题|G1'];assert.equal(g1.option_id,'O2');assert.deepEqual(g1.common_issue_snapshot.scope,{issue_ids:['Q1'],item_ids:['I1','E1']});
 va.show('Q1');assert.match(vx['audit-inherit'].textContent,/已按共性答复（G1：不按现做法，不计出桥架段）/);vx['audit-exception'].value='G1';vx['audit-exception'].onchange();vx['audit-verdict'].value='要改';vx['audit-verdict'].onchange();vx['audit-reason'].value='同上';vx['audit-reason'].oninput();vx['audit-save'].click();assert.match(vx['audit-entry-status'].textContent,/与共性答复相同/);
 vx['audit-verdict'].value='同意';vx['audit-verdict'].onchange();vx['audit-reason'].value='此处出桥架段要单列';vx['audit-reason'].oninput();vx['audit-save'].click();vpkg=vcached();assert.equal(vpkg.annotations['问题|Q1'].exception_of,'G1');
 va.show('Q1');assert.match(vx['audit-inherit'].textContent,/此处已设例外，不按 G1 的答复（本处：按现做法）/);
 checks.push('共性问题并排列做法及影响、可选做法答复；挂在下面的个别问题显示待答或已按共性答复，只能设例外，与共性答复相同时不保存');
 vx['save-html'].click();const vsaved=await vh.downloads.at(-1).blob.text();c3.common_issues[0].item_ids.push('I6');
 const ah=harness(withData(vsaved,normalize(raw3)));await ah.start();ah.ctx.QtoReviewAudit.active.show('G1');assert.match(ah.ids['audit-detail'].textContent,/答复后新增 1 处/);assert.match(ah.ids['audit-detail'].textContent,/答复后新增（请抽查）/);
 const ck=JSON.parse(JSON.stringify(vpkg)),base=ck.annotations['共性问题|G1'];delete base.history;ck.annotations['共性问题|G1']={...base,merge_conflict:{candidates:[{...base},{...base,verdict:'同意',option_id:''}]}};
 const mh=harness(vh.ctx.QtoReviewCore.embed(withData(chtml,v3),ck));await mh.start();const ma=mh.ctx.QtoReviewAudit.active;ma.show('G1');assert.match(mh.ids['audit-entry-status'].textContent,/意见冲突/);assert.match(mh.ids['audit-detail'].textContent,/答复状态：意见冲突/);assert.match(find(mh.ids['audit-list'],n=>n.dataset?.key==='共性问题|G1').textContent,/意见冲突/);
 ma.show('E1');assert.match(mh.ids['audit-inherit'].textContent,/待共性答复（G1）/);ma.show('Q1');assert.match(mh.ids['audit-inherit'].textContent,/此处已设例外，不按 G1 的答复/);
 // Exceptions as they stand (contract 9.1): kept after reopening, a conflicting exception holds the answer back, an expired one follows it again.
 ah.ctx.QtoReviewAudit.active.show('Q1');assert.match(ah.ids['audit-inherit'].textContent,/此处已设例外，不按 G1 的答复（本处：按现做法）/);
 const ek=JSON.parse(JSON.stringify(vpkg)),q1n=ek.annotations['问题|Q1'];delete q1n.history;ek.annotations['问题|Q1']={...q1n,merge_conflict:{candidates:[{...q1n},{...q1n,verdict:'待定'}]}};
 const eh=harness(vh.ctx.QtoReviewCore.embed(withData(chtml,v3),ek));await eh.start();eh.ctx.QtoReviewAudit.active.show('Q1');assert.match(eh.ids['audit-inherit'].textContent,/例外意见冲突（G1）：两份意见不同/);assert.doesNotMatch(eh.ids['audit-inherit'].textContent,/已按共性答复/);
 c3.issues.find(q=>q.id==='Q1').text+='（下一轮改了正文）';const xh=harness(withData(vsaved,normalize(raw3)));await xh.start();xh.ctx.QtoReviewAudit.active.show('Q1');
 assert.match(xh.ids['audit-inherit'].textContent,/此处原设的例外已过期，须重新核对；现已按共性答复（G1：不按现做法/);
 checks.push('设了例外的条目和个别问题显示“此处已设例外”及本处意见，重开页面仍在；例外意见冲突时不按共性答复，例外过期后重新按共性答复并提示核对');
 checks.push('共性问题适用范围变了不过期，标“答复后新增 N 处”；合并冲突的意见标“意见冲突”，未采用前不向下继承');
}
const result={status:'PASS',count:checks.length,checks,scope:'无浏览器DOM模拟，非原生浏览器视觉验收',saved_feedback:packageFile};fs.writeFileSync(path.join(path.dirname(arg('--report')),'交互核验.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
