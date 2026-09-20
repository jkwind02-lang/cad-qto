import {spawnSync} from 'node:child_process';import fs from 'node:fs';import path from 'node:path';import vm from 'node:vm';import assert from 'node:assert/strict';import {gzipSync} from 'node:zlib';
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],root=arg('--root'),report=JSON.parse(fs.readFileSync(arg('--report'),'utf8')),art=report.artifacts,A=path.join(root,'_qto-shared-evidence','assets','review');
const data=JSON.parse(fs.readFileSync(art.normalized_view,'utf8')),sourceHTML=fs.readFileSync(art.html,'utf8');
function harness(html){
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
 const ctx=vm.createContext({document,console,Blob,FormData,Response,DecompressionStream,Uint8Array,TextEncoder,crypto,AbortSignal,atob,Image,devicePixelRatio:1,ResizeObserver:class{observe(){}},setTimeout:()=>0,URL:TestURL,fetch:()=>{throw Error('Unexpected network call');},localStorage:{getItem:k=>cache.get(k)||null,setItem:(k,v)=>cache.set(k,v)}});
 return {ctx,ids,cache,downloads,async start(){vm.runInContext(fs.readFileSync(path.join(A,'review-core.js'),'utf8'),ctx);if(ids['problem-config'])vm.runInContext(fs.readFileSync(path.join(A,'problem-feedback.js'),'utf8'),ctx);await vm.runInContext(fs.readFileSync(path.join(A,'review-app.js'),'utf8'),ctx);assert.equal(ids.loading.removed,true,ids.loading.textContent);}};
}
const checks=[],h=harness(sourceHTML);await h.start();const e=h.ids;assert.equal(e.professional.options.length,14);assert.equal(e.list.children.length,13);assert.equal(e['member-list'].children.length,3);assert.equal(e['review-result'].value,'');checks.push('13专业筛选与分组初始化；建议不预填为确认');
assert.equal(e['action-message'].hidden,true);assert.match(e['review-status'].textContent,/^\d+条已保存 · \d+条草稿$/);
const headerDetails=sourceHTML.match(/<details id="header-details"([^>]*)>([\s\S]*?)<\/details>/);assert(headerDetails);assert(!/\bopen\b/.test(headerDetails[1]));for(const id of ['current-summary','summary-strip','scope-notes','export-feedback','import-feedback'])assert(headerDetails[2].includes('id="'+id+'"'));assert(sourceHTML.includes('.header-panel{position:absolute;'));checks.push('顶部默认折叠说明和摘要；展开面板脱离主布局（静态结构核对）');
e['member-next'].click();assert.equal(e.scene.value,'section');assert.match(e['editor-title'].textContent,/整组/);checks.push('跨图页下一处保持整组范围');
e['member-next'].click();assert.equal(e.unlocated.hidden,false);assert.match(e['object-facts'].textContent,/待定/);checks.push('未定位对象和null显式保留');
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
 ph.ctx.fetch=async(url,options)=>{const meta=JSON.parse(options.body.get('metadata')),file=options.body.get('html');requests.push({url,meta,file});assert(ph.downloads.length>0,'上传前必须先保存本地HTML');assert.equal(await file.text(),await ph.downloads.at(-1).blob.text(),'本地和上传必须完全同字节内容');if(fail)throw Error('模拟网络断开');return Response.json({accepted:true,receipt:'FB-000001',status:'saved',sha256:meta.sha256,bytes:file.size});};
 pe['save-html'].click();assert.equal(pe['problem-dialog'].open,true);assert.match(pe['problem-preview'].textContent,/完整HTML/);assert.equal(requests.length,0);
 await pe['problem-send'].click();assert.equal(requests.length,0);assert.match(pe['problem-message'].textContent,/确认发送完整HTML/);
 pe['problem-consent'].checked=true;pe['problem-code'].value='test-team-secret-123456789';await pe['problem-send'].click();assert.equal(requests.length,1);assert.equal(ph.downloads.length,1);assert.match(pe['problem-message'].textContent,/未完成/);
 const localCopy=await ph.downloads[0].blob.text();assert(!localCopy.includes('test-team-secret'));assert(localCopy.includes('feedback-seed'));const delivered=ph.ctx.QtoReviewCore.readFileText(localCopy);assert.equal(delivered.annotations[k].confirmed_result,note.confirmed_result);assert.equal(delivered.annotations[k].reason,note.reason);assert.equal(requests[0].meta.project_id,data.project_id);
 fail=false;pe['problem-consent'].checked=true;pe['problem-code'].value='test-team-secret-123456789';await pe['problem-send'].click();assert.equal(requests.length,2);assert.equal(requests[0].meta.report_id,requests[1].meta.report_id);assert.equal(requests[0].meta.sha256,requests[1].meta.sha256);assert.match(pe['problem-message'].textContent,/本地与服务器文件校验一致/);
 pe['review-reason'].value='增加未保存草稿';pe['review-reason'].oninput();pe['problem-code'].value='test-team-secret-123456789';await pe['problem-send'].click();assert.equal(requests.length,3);assert.notEqual(requests[0].meta.report_id,requests[2].meta.report_id);assert.equal(requests[2].meta.draft_count,1);
 checks.push('合并保存先落本地；整份HTML和上传逐字节一致，断网原文件重试，意见变化后生成新文件与编号');
 const rph=harness(localCopy);await rph.start();rph.ids['save-html'].click();assert.equal(rph.ids['problem-dialog'].open,true);
 const one=await rph.ctx.QtoProblemFeedback.hash(await requests[0].file.arrayBuffer());assert.equal(one,requests[0].meta.sha256);
 assert.throws(()=>rph.ctx.QtoProblemFeedback.endpoint('https://user:secret@feedback.example'),/HTTPS/);assert.throws(()=>rph.ctx.QtoProblemFeedback.endpoint('http://feedback.example'),/HTTPS/);
 checks.push('完整HTML保留项目、图面、结论、原因和草稿；保存后独立恢复，拒绝含凭据的接收地址');
 const dualHTML=configuredHTML.replace(/(<script id="problem-config" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,b,c)=>a+JSON.stringify({...JSON.parse(b),provider:'tencent',url:'https://tencent.example',backup_url:'https://backup.example'})+c);
 const begin=async(x)=>{await x.start();x.ids['save-html'].click();x.ids['problem-consent'].checked=true;x.ids['problem-code'].value='test-team-secret-123456789';};
 const th=harness(dualHTML);await begin(th);const tcalls=[];let tm;
 th.ctx.fetch=async(url,o)=>{tcalls.push(url);
  if(url.endsWith('/init')){tm=JSON.parse(o.body);return Response.json({ticket:'synthetic-ticket',upload:{url:'https://test-123456.cos.ap-shanghai.myqcloud.com/',fields:{key:'staging/test.html'}}});}
  if(url.includes('myqcloud.com')){assert.equal(await o.body.get('file').text(),await th.downloads.at(-1).blob.text());return new Response(null,{status:204});}
  assert(url.endsWith('/complete'));return Response.json({accepted:true,receipt:'TC-test',sha256:tm.sha256,bytes:tm.bytes});};
 await th.ids['problem-send'].click();assert.equal(tcalls.length,3);assert(!tcalls.some(u=>u.includes('backup.example')));assert.match(th.ids['problem-message'].textContent,/腾讯云/);
 th.ids['problem-code'].value='test-team-secret-123456789';await th.ids['problem-send'].click();assert.equal(tcalls.length,3,'已收件的同一快照不会重复上传');
 checks.push('腾讯云主站直传COS后确认回执；正常收件不调用备用站，已收快照不重复发送');
 const bh=harness(dualHTML);await begin(bh);const bcalls=[];let bm;
 bh.ctx.fetch=async(url,o)=>{bcalls.push(url);
  if(url.endsWith('/init')){bm=JSON.parse(o.body);throw Error('lost response');}
  if(url.includes('/api/status/'))return Response.json({error:'未找到'},{status:404});
  if(url.endsWith('/api/info'))return Response.json({provider:'cloudflare',role:'backup',ready:true});
  assert.equal(url,'https://backup.example/api/reviews');const meta=JSON.parse(o.body.get('metadata'));assert.equal(meta.report_id,bm.report_id);assert.equal(meta.sha256,bm.sha256);assert.equal(await o.body.get('html').text(),await bh.downloads.at(-1).blob.text());
  return Response.json({accepted:true,receipt:'FB-000002',sha256:meta.sha256,bytes:o.body.get('html').size});};
 await bh.ids['problem-send'].click();assert.equal(bcalls.length,4);assert.match(bh.ids['problem-message'].textContent,/Cloudflare备用/);assert.match(bh.ids['problem-message'].textContent,/主站收件状态仍待核对/);
 checks.push('主站超时先查回执，再按同一编号和完整字节备用收件；实际位置与主站不确定状态可见');
 const eh=harness(dualHTML);await begin(eh);let ecalls=0;eh.ctx.fetch=async()=>{ecalls++;return Response.json({error:'团队反馈码不正确'},{status:401});};await eh.ids['problem-send'].click();assert.equal(ecalls,1);assert.match(eh.ids['problem-message'].textContent,/反馈码不正确/);
 const ah=harness(dualHTML);await begin(ah);let am,acalls=0;ah.ctx.fetch=async(url,o)=>{acalls++;if(url.endsWith('/init')){am=JSON.parse(o.body);throw Error('timeout');}assert(url.includes('/api/status/'));return Response.json({accepted:true,receipt:'TC-accepted',sha256:am.sha256,bytes:am.bytes});};await ah.ids['problem-send'].click();assert.equal(acalls,2);assert.match(ah.ids['problem-message'].textContent,/TC-accepted/);
 checks.push('反馈码错误不转备用；主站已接收但首次回执丢失时查询恢复，不再发备用');
 const putHarness=harness(dualHTML);await begin(putHarness);let putMeta;putHarness.ctx.fetch=async(url,o)=>{
  if(url.endsWith('/init')){putMeta=JSON.parse(o.body);return Response.json({ticket:'put-ticket',upload:{url:'https://test-123456.cos.ap-shanghai.myqcloud.com/cad-qto-feedback/staging/sample.bin',method:'PUT',headers:{authorization:'scoped-temporary-signature',key:'scoped-path'}}});}
  if(url.includes('myqcloud.com')){assert.equal(o.method,'PUT');assert.equal(await o.body.text(),await putHarness.downloads.at(-1).blob.text());return new Response(null,{status:200});}
  return Response.json({accepted:true,receipt:'TC-put',sha256:putMeta.sha256,bytes:putMeta.bytes});};
 await putHarness.ids['problem-send'].click();assert.match(putHarness.ids['problem-message'].textContent,/TC-put/);assert.equal(putHarness.ctx.QtoProblemFeedback.endpoint('https://tencent.example/cad-qto-feedback/'),'https://tencent.example/cad-qto-feedback');
 checks.push('腾讯云开发实际PUT直传协议逐字节保全，支持专用子路径入口');
}
const result={status:'PASS',count:checks.length,checks,scope:'无浏览器DOM模拟，非原生浏览器视觉验收',saved_feedback:packageFile};fs.writeFileSync(path.join(path.dirname(arg('--report')),'交互核验.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
