import test from 'node:test';
import assert from 'node:assert/strict';
import {createService,sha,htmlFilename,attachmentHeader} from '../tencent/service.mjs';
import {CloudBaseFiles} from '../tencent/cloudbase.mjs';
const admin='test-admin-code-12345678901234567890';
const html='<!doctype html><script id="feedback-seed">合成意见与原因</script><script id="payload">合成图面</script>';
function fixture(){
 const rows=new Map(),blobs=new Map(),store={async get(id){return rows.get(id);},async reserve(row){if(!rows.has(row.meta.report_id))rows.set(row.meta.report_id,row);return rows.get(row.meta.report_id);},async stage(id,key){rows.get(id).stage_id=key;},async complete(id,key){const r=rows.get(id);r.file_id=key;r.status='saved';return r;},async list(){return [...rows.values()].filter(r=>r.status==='saved');}};
 const files={async prepare(key){return {file_id:key,client:{url:'https://test.cos.ap-shanghai.myqcloud.com/'+key,method:'PUT',headers:{authorization:'short-lived'}}};},async read(key){return blobs.get(key);},async put(key,bytes){blobs.set(key,bytes);return key;},async downloadURL(key){return 'https://private.example/'+key;}};
 store.relocate=async(id,previous,file_id)=>{const row=rows.get(id);assert.equal(row.file_id,previous);row.legacy_file_id=previous;row.file_id=file_id;return row;};
 const handle=createService({env:{FEEDBACK_ADMIN_TOKEN:admin,UPLOAD_TICKET_SECRET:'ticket-secret-1234567890123456789012345',PUBLIC_BASE_PATH:'/cad-qto-feedback'},store,files});
 const meta={schema:'cad-qto-review-upload/1.0',report_id:crypto.randomUUID(),receipt_token:'c'.repeat(64),consent:true,sha256:sha(html),filename:'合成复核.html',project_id:'合成项目',review_id:'全专业',revision:'测试版',source_sha256:'a'.repeat(64),plugin_version:'test',annotations_count:1,draft_count:1,bytes:Buffer.byteLength(html)};
 const request=(path,body,token)=>new Request('https://tencent.test/cad-qto-feedback'+path,{method:body?'POST':'GET',headers:{Origin:'null',...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 return {rows,blobs,handle,meta,request,files};
}
test('腾讯云无需反馈码先签发直传凭据，完整校验后才收件；状态凭据和管理员权限分离',async()=>{
 const f=fixture();const info=await (await f.handle(f.request('/api/info'))).json();assert.equal(info.ready,true);assert.equal(info.upload_access,'open');const init=await f.handle(f.request('/api/reviews/init',f.meta));assert.equal(init.status,200);assert.equal(init.headers.get('Access-Control-Allow-Origin'),'null');const body=await init.json();assert(body.ticket);assert.equal(body.accepted,undefined);
 const status=()=>f.handle(f.request('/api/status/'+f.meta.report_id,null,f.meta.receipt_token));assert.equal((await (await status()).json()).accepted,false);
 const row=f.rows.get(f.meta.report_id);f.blobs.set(row.stage_id,Buffer.from(html));const complete=await f.handle(f.request('/api/reviews/complete',{ticket:body.ticket}));assert.equal(complete.status,202);const receipt=await complete.json();assert.equal(receipt.sha256,sha(html));assert.equal(f.blobs.get(row.file_id).toString(),html);assert.equal((await (await status()).json()).accepted,true);
 assert(row.file_id.endsWith('/合成复核.html'),'控制台中的正式文件保留HTML名称');assert(row.stage_id.endsWith('.bin'),'暂存上传仍隔离');
 assert.equal((await f.handle(f.request('/api/admin/artifacts/'+receipt.receipt,null))).status,401);
 const download=await f.handle(f.request('/api/admin/artifacts/'+receipt.receipt,null,admin));assert.equal(download.status,200);assert((await download.json()).url.includes('/reviews/'));
 const again=await f.handle(f.request('/api/reviews/init',f.meta));assert.equal((await again.json()).accepted,true);assert.equal(f.rows.size,1);
});
test('腾讯云旧bin取件自动恢复原HTML名称，保留原文件且内容和收件身份不变',async()=>{
 const f=fixture(),old='cad-qto-feedback/reviews/'+f.meta.report_id+'/'+f.meta.sha256+'.bin';
 const row={meta:f.meta,file_id:old,status:'saved'};f.rows.set(f.meta.report_id,row);f.blobs.set(old,Buffer.from(html));
 const path='/api/admin/artifacts/TC-'+f.meta.report_id;
 assert.equal((await f.handle(f.request(path,null))).status,401);assert.equal(f.blobs.size,1);
 const r=await f.handle(f.request(path,null,admin));assert.equal(r.status,200);const body=await r.json();
 assert.equal(body.filename,'合成复核.html');assert(body.url.endsWith('/合成复核.html'));assert.equal(body.sha256,f.meta.sha256);
 assert.equal(row.legacy_file_id,old);assert.equal(f.blobs.get(old).toString(),html);assert.equal(f.blobs.get(row.file_id).toString(),html);assert.equal(row.meta,f.meta);
 await f.handle(f.request(path,null,admin));assert.equal(f.blobs.size,2,'重复取件不新增副本');
});
test('旧bin损坏或新副本校验失败时不替换原文件索引',async()=>{
 for(const corruptCopy of [false,true]){
  const f=fixture(),old='cad-qto-feedback/reviews/'+f.meta.report_id+'/old.bin',row={meta:f.meta,file_id:old,status:'saved'};
  f.rows.set(f.meta.report_id,row);f.blobs.set(old,Buffer.from(corruptCopy?html:'damaged'));
  if(corruptCopy)f.files.put=async(key)=>{f.blobs.set(key,Buffer.from('damaged-copy'));return key;};
  const r=await f.handle(f.request('/api/admin/artifacts/TC-'+f.meta.report_id,null,admin));assert.equal(r.status,corruptCopy?503:409);
  assert.equal(row.file_id,old);assert.equal(row.legacy_file_id,undefined);assert(f.blobs.has(old));
 }
});
test('下载保留中文HTML名称，路径、保留名称和请求头注入字符不会进入文件名',()=>{
 assert.equal(htmlFilename('八层给排水_已批注.html'),'八层给排水_已批注.html');
 assert.equal(htmlFilename('CON.html'),'复核_CON.html');assert.equal(htmlFilename(''),'复核成果.html');
 for(const value of ['../a\\b\r\n.html','图'.repeat(300)+'.html','测试\ud800.html']){
  const name=htmlFilename(value);assert(name.endsWith('.html'));assert(!/[\\/\r\n]/.test(name));assert(Buffer.byteLength(name)<=185);assert(!name.startsWith('.'));
  const header=attachmentHeader(value);assert(header.startsWith('attachment;'));assert(!/[\r\n]/.test(header));assert.equal(decodeURIComponent(header.split("UTF-8''")[1]),name);
 }
});
test('腾讯云正式上传设置HTML附件名及二进制下载类型，拒绝重定向与错误收件',async()=>{
 const original=globalThis.fetch,calls=[];
 const files=new CloudBaseFiles({async getUploadMetadata(){return {data:{fileId:'cloud://test/review.html',url:'https://test.cos.ap-shanghai.myqcloud.com/review.html',authorization:'scoped-signature',token:'scoped-token',cosFileId:'meta-id'}};}});
 try{
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(null,{status:200});};
  assert.equal(await files.put('review.html',Buffer.from(html),'给排水复核.html'),'cloud://test/review.html');
  const {options}=calls[0];assert.equal(options.headers['Content-Type'],'application/octet-stream');assert.equal(options.headers['Content-Disposition'],attachmentHeader('给排水复核.html'));assert.equal(options.redirect,'error');assert.equal(options.body.toString(),html);
  globalThis.fetch=async()=>new Response(null,{status:403});await assert.rejects(()=>files.put('review.html',Buffer.from(html),'给排水复核.html'),/file_not_stored/);
 }finally{globalThis.fetch=original;}
});
test('腾讯云未确认发送、伪造票据、内容冲突和文件校验错误均不能生成成功回执',async()=>{
 const f=fixture();assert.equal((await f.handle(f.request('/api/reviews/init',{...f.meta,consent:false}))).status,400);assert.equal(f.rows.size,0);
 const init=await (await f.handle(f.request('/api/reviews/init',f.meta))).json();const row=f.rows.get(f.meta.report_id);f.blobs.set(row.stage_id,Buffer.from('wrong'));
 assert.equal((await f.handle(f.request('/api/reviews/complete',{ticket:init.ticket+'a'}))).status,400);
 assert.equal((await f.handle(f.request('/api/reviews/complete',{ticket:init.ticket}))).status,400);assert.equal(row.status,'uploading');
 assert.equal((await f.handle(f.request('/api/reviews/init',{...f.meta,revision:'different'}))).status,409);
});
test('腾讯云接受超过6MB的HTML直传元数据，但拒绝超过20MiB及额外字段',async()=>{
 const f=fixture();assert.equal((await f.handle(f.request('/api/reviews/init',{...f.meta,bytes:7*1024*1024}))).status,200);
 assert.equal((await f.handle(f.request('/api/reviews/init',{...f.meta,bytes:21*1024*1024}))).status,413);
 assert.equal((await f.handle(f.request('/api/reviews/init',{...f.meta,secret:'unexpected'}))).status,400);
});
