import fs from 'node:fs';import path from 'node:path';import vm from 'node:vm';import assert from 'node:assert/strict';import {fileURLToPath} from 'node:url';
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),A=path.join(root,'skills/_qto-shared-evidence/assets/review');
const sourceHTML=fs.readFileSync(arg('--html'),'utf8'),raw=JSON.parse(fs.readFileSync(arg('--view'),'utf8'));
function harness(html,extra={}){
 const all=[],ids={},downloads=[],blobs=new Map(),cache=new Map(),canvasCalls=[];
 class El{
  constructor(tag='div',id=''){this.tagName=tag;this.id=id;this.children=[];this._text='';this.value='';this.dataset={};this.className='';this.hidden=false;this.disabled=false;this.checked=false;this.style={values:{},setProperty:(k,v)=>this.style.values[k]=v};this.classList={add:c=>{if(!this.className.split(' ').includes(c))this.className+=' '+c;},remove:c=>{this.className=this.className.split(' ').filter(x=>x!==c).join(' ');},toggle:(c,v)=>{if(v===undefined)v=!this.className.split(' ').includes(c);v?this.classList.add(c):this.classList.remove(c);}};all.push(this);}
  set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}get options(){return this.children;}
  append(...c){this.children.push(...c);}replaceChildren(...c){this.children=c;this._text='';}setAttribute(k,v){this[k]=v;}
  getBoundingClientRect(){return this.id==='layout'?{width:1450,height:760,left:0,top:0}:this.className==='nav'?{width:260}:this.className==='review-pane'?{width:490}:{width:680,height:650,left:0,top:0};}
  getContext(){return new Proxy({measureText:t=>({width:t.length*7})},{get:(t,k)=>k in t?t[k]:(...args)=>canvasCalls.push([k,...args])});}setPointerCapture(){}remove(){this.removed=true;}
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
 return {ctx,ids,cache,downloads,canvasCalls,async start(){vm.runInContext(fs.readFileSync(path.join(A,'review-core.js'),'utf8'),ctx);if(ids['problem-config'])vm.runInContext(fs.readFileSync(path.join(A,'problem-feedback.js'),'utf8'),ctx);if(html.includes('id="audit"'))vm.runInContext(fs.readFileSync(path.join(A,'review-audit.js'),'utf8'),ctx);vm.runInContext(fs.readFileSync(path.join(A,'review-delivery.js'),'utf8'),ctx);await vm.runInContext(fs.readFileSync(path.join(A,'review-app.js'),'utf8'),ctx);assert.equal(ids.loading.removed,true,ids.loading.textContent);}};
}

function all(node){return [node,...(node.children||[]).flatMap(all)];}
function clickText(h,text){const b=all(h.ids.delivery).find(x=>x.tagName==='button'&&x.textContent===text);assert(b,'missing button '+text);b.click();}
const h=harness(sourceHTML);await h.start();
assert.equal(h.ids.delivery.hidden,false);assert.equal(h.ids.layout.hidden,true);
assert.match(h.ids.delivery.textContent,/12 m/);assert.match(h.ids.delivery.textContent,/2 台/);assert.match(h.ids.delivery.textContent,/20 m/);assert.match(h.ids.delivery.textContent,/未知（null）/);
assert.match(h.ids.delivery.textContent,/禁止默认相加/);assert(!h.ids.delivery.textContent.includes('888'));assert(!h.ids.delivery.textContent.includes('999'));
if(raw.delivery.quantity_rows.length>60){clickText(h,'下一页');clickText(h,'下一页');assert.match(h.ids.delivery.textContent,/批量参考124/);clickText(h,'工程量');}
if(raw.scenes.length){clickText(h,'仅来源证据，旧记录量不采用 · 仅来源证据定位');assert(h.canvasCalls.some(x=>x[0]==='fillText'&&x[1].includes('SOURCE（仅证据定位）')));assert(!h.canvasCalls.some(x=>x[0]==='fillText'&&/OLD|888|999/.test(x[1])));}
clickText(h,'资料缺口');const child=all(h.ids.delivery).find(x=>x.dataset?.missingId==='G-WL1');assert(child);assert.match(child.textContent,/适用 WL1/);assert(!child.textContent.includes('WL2'));assert.match(child.textContent,/设计单位/);assert.match(child.textContent,/回算该回路/);
assert(!all(h.ids.delivery).some(x=>['textarea','select'].includes(x.tagName)));
if(raw.delivery.mode==='decisions'){
 clickText(h,'业务取舍');clickText(h,'填写此项业务意见');assert.equal(h.ids.audit.hidden,false);assert.equal(h.ids['audit-save'].disabled,false);assert.match(h.ids['audit-editor-title'].textContent,/问题：施工方案选择/);
 h.ids['audit-verdict'].value='同意';h.ids['audit-verdict'].onchange?.();h.ids['audit-reason'].value='合成业务选择方案甲';h.ids['audit-reason'].oninput?.();h.ids['audit-save'].click();
 const pkg=h.ctx.QtoReviewDelivery.active.getFeedback();assert.equal(pkg.annotations['问题|D1'].applicability_state,'current');assert.equal(pkg.annotations['问题|D1'].verdict,'同意');
 all(h.ids['action-message']).find(x=>x.tagName==='button').click();assert.equal(h.ids.audit.hidden,true);
}else{assert.equal(h.ctx.QtoReviewAudit.active,undefined);assert.equal(h.ids.audit.hidden,true);assert(!all(h.ids.delivery).some(x=>/填写.*意见/.test(x.textContent)&&x.tagName==='button'));}
h.ids['save-html'].click();const saved=await h.downloads.at(-1).blob.text(),restored=harness(saved);await restored.start();
assert.equal(restored.ids.delivery.hidden,false);assert(!restored.ids.delivery.textContent.includes('888'));assert(!restored.ids.delivery.textContent.includes('999'));
const pkg=restored.ctx.QtoReviewDelivery.active.getFeedback();assert.equal(pkg.delivery_snapshot.delivery.quantity_rows[3].quantity,null);assert.deepEqual(JSON.parse(JSON.stringify(pkg.delivery_snapshot.delivery.current_member_keys)),['PIPE','PUMP']);
restored.ids['export-feedback'].click();const exported=JSON.parse(await restored.downloads.at(-1).blob.text());assert.equal(exported.delivery_snapshot.delivery.quantity_rows[2].quantity,20);assert.equal(exported.delivery_snapshot.delivery.quantity_rows[0].quantity,12);
clickText(restored,'历史记录');assert.match(restored.ids.delivery.textContent,/888 m/);assert(!all(restored.ids.delivery).some(x=>x.tagName==='button'&&/退役路径/.test(x.textContent)));
const old=JSON.parse(JSON.stringify(pkg));old.annotations['旧意见|OLD']={object_id:'OLD',object_ids:['OLD'],object_snapshot:{catalog_key:'OLD',object_id:'OLD'},confirmed_result:'历史888 m',reason:'旧方案已退役',result_fingerprint:'f'.repeat(64),history:[]};old.custom_markers=[{id:'旧标记',catalog_key:'历史标记|1',scene_id:'已移除图页',points:[[1,2]],professional:'qto-pipe-networks'}];
const history=harness(restored.ctx.QtoReviewCore.embed(saved,old));await history.start();assert(!history.ids.delivery.textContent.includes('888'));assert.equal(history.ctx.QtoReviewDelivery.active.getFeedback().annotations['旧意见|OLD'].applicability_state,'unmatched');clickText(history,'历史记录');assert.match(history.ids.delivery.textContent,/历史888 m/);
history.ids['save-html'].click();const historyAgain=harness(await history.downloads.at(-1).blob.text());await historyAgain.start();assert(!historyAgain.ids.delivery.textContent.includes('888'));assert.equal(historyAgain.ctx.QtoReviewDelivery.active.getFeedback().custom_markers.length,1);
console.log(JSON.stringify({status:'PASS',checks:['主量/参考/单位/null/覆盖','证据定位无量及退役不回流','缺件逐项范围与回算','只读或真实业务审核','单HTML保存复读及导出','历史只读']},null,2));
