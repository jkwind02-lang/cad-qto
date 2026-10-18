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
const find=(cls)=>all(h.ids.delivery).find(e=>e.className.split(' ').includes(cls));
const choose=id=>{const row=all(h.ids.delivery).find(e=>e.dataset.itemId===id);assert(row,'missing item '+id);row.click();};
const state=()=>JSON.parse(JSON.stringify(h.ctx.QtoReviewDelivery.active.state()));
assert.equal(h.ids.layout.hidden,true);assert(find('delivery-list-pane'));assert(find('delivery-workspace'));
assert.equal(raw.delivery.items.length,107);assert.match(h.ids.delivery.textContent,/原清单无图无量/);assert.match(h.ids.delivery.textContent,/完整记录 107 项/);
choose('I1');assert.deepEqual(state().mapKeys,['PIPE','EXTRA','PIPE3']);
let details=find('delivery-details'),row=all(details).find(e=>e.className.includes('delivery-detail-row')&&e.dataset.rowId==='M1');row.click();
assert.deepEqual(state().mapKeys,['PIPE','EXTRA']);assert.equal(state().selectedRow,'M1');assert(h.canvasCalls.some(x=>x[0]==='drawImage'));assert(h.canvasCalls.some(x=>x[0]==='rotate'&&Math.abs(x[1]+Math.PI/6)<1e-9));assert(h.canvasCalls.some(x=>x[0]==='transform'&&JSON.stringify(x.slice(1))===JSON.stringify([1,-0.2,-0.1,1,0,0])));assert(h.canvasCalls.some(x=>x[0]==='fill'));assert(h.canvasCalls.some(x=>x[0]==='setLineDash'&&x[1]?.length===2));
clickText(h,'下一处');assert.equal(state().focusKey,'EXTRA');
row=all(details).find(e=>e.className.includes('delivery-detail-row')&&e.dataset.rowId==='M3');row.click();assert.deepEqual(state().mapKeys,['PIPE3']);
clickText(h,'整项图元');assert.deepEqual(state().mapKeys,['PIPE','EXTRA','PIPE3']);
row=all(details).find(e=>e.className.includes('delivery-detail-row')&&e.dataset.rowId==='R1');row.click();assert.deepEqual(state().mapKeys,[]);assert.match(find('delivery-map-state').textContent,/已清除上次图面/);
clickText(h,'仅来源证据，旧记录量不采用 · 仅来源证据定位');assert.equal(state().focusKey,'SOURCE');assert(h.canvasCalls.some(x=>x[0]==='fillText'&&x[1].includes('SOURCE（仅证据定位）')));
choose('I3');assert.equal(find('delivery-map-canvas').dataset.sceneId,'');assert.match(find('delivery-map-state').textContent,/暂无图面几何/);
choose('I2');assert.deepEqual(state().mapKeys,[]);assert.match(find('delivery-details').textContent,/原量：6 m/);assert.match(find('delivery-details').textContent,/本清单项暂无数量明细/);
let splitter=find('delivery-row-splitter');splitter.onkeydown({key:'ArrowDown',preventDefault(){}});assert.equal(state().ratio,55);splitter.onkeydown({key:'Home',preventDefault(){}});assert.equal(state().ratio,52);
splitter.onpointerdown({pointerId:1,preventDefault(){}});splitter.onpointermove({clientY:620});splitter.onpointerup();assert.equal(state().ratio,75);splitter.ondblclick();assert.equal(state().ratio,52);
clickText(h,'放大图纸');assert(find('delivery-fullscreen'));clickText(h,'退出大图');assert(!find('delivery-fullscreen'));
const search=find('delivery-filters').children.find(e=>e.tagName==='input');search.value='末页清单103';search.oninput();assert.match(h.ids.delivery.textContent,/筛选 1 项/);assert.match(h.ids.delivery.textContent,/末页清单103/);assert.deepEqual(state().mapKeys,[]);
search.value='';search.oninput();clickText(h,'下一页');clickText(h,'下一页');assert.match(h.ids.delivery.textContent,/末页清单103/);
clickText(h,'可比偏差');assert(find('delivery-overview'));assert.match(h.ids.delivery.textContent,/不跨单位合计数量/);assert.match(find('delivery-details').textContent,/具体理由/);
clickText(h,'覆盖查漏');assert.match(find('delivery-details').textContent,/缺少明确基准/);assert.match(h.ids.delivery.textContent,/独立覆盖查漏/);
clickText(h,'资料缺口');assert(!all(h.ids.delivery).some(e=>e.tagName==='textarea'));assert.match(find('delivery-details').textContent,/补齐后助手动作/);
clickText(h,'历史记录');assert(!all(find('delivery-details')).some(e=>e.tagName==='button'&&/退役路径/.test(e.textContent)));assert.deepEqual(state().mapKeys,[]);

// Test the UI's own mandatory qualification independently of optional projection notices.
const qualified=harness(sourceHTML),display=JSON.parse(JSON.stringify(raw));delete display.delivery.projection_provenance;
display.delivery.quantity_rows.find(r=>r.id==='M1').evidence.method='adopted_record';
Object.assign(display.delivery.quantity_rows.find(r=>r.id==='R1').reference,{association_state:'scope_only',relationship:'unknown',source_claimed_relationship:'includes_current',current_row_ids:[],scope_statement:'同范围参考，逐行未给',association_reason:'仅保留原记录声明'});
vm.runInContext(fs.readFileSync(path.join(A,'review-core.js'),'utf8'),qualified.ctx);vm.runInContext(fs.readFileSync(path.join(A,'review-delivery.js'),'utf8'),qualified.ctx);
qualified.ctx.QtoReviewDelivery.attach({data:display,core:qualified.ctx.QtoReviewCore,originalHTML:sourceHTML,storageKey:'no-optional-notice',feedback:{annotations:{},historical_delivery_snapshots:[]}});
const adopted=all(qualified.ids.delivery).find(e=>e.tagName==='article'&&e.dataset.rowId==='M1');assert.match(adopted.textContent,/来源已采用记录迁移；本次只核冻结记录身份，未重新证明几何或规则输入/);assert(!adopted.textContent.includes('证据：已核实'));
const scopeOnly=all(qualified.ids.delivery).find(e=>e.tagName==='article'&&e.dataset.rowId==='R1');assert.match(scopeOnly.textContent,/逐明细包含范围未证，不得相加/);assert.match(scopeOnly.textContent,/与同范围主量关系：包含关系未知/);

console.log(JSON.stringify({status:'PASS',checks:['通用清单107项分页搜索，无图无量可读','稳定成员键一对多/缩小/逐处/整项还原','来源定位独立且退役不回流','无定位强制清旧图','拖动/键盘/Home/双击比例保护','图纸放大恢复','统计常驻、具体不可比理由及独立覆盖未知','缺资料只读范围/提供方/助手动作']},null,2));
