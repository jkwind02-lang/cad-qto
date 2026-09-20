import test from 'node:test';
import assert from 'node:assert/strict';
import {createService,sha} from '../tencent/service.mjs';
const admin='test-admin-code-12345678901234567890';
const html='<!doctype html><script id="feedback-seed">合成意见与原因</script><script id="payload">合成图面</script>';
function fixture(){
 const rows=new Map(),blobs=new Map(),store={async get(id){return rows.get(id);},async reserve(row){if(!rows.has(row.meta.report_id))rows.set(row.meta.report_id,row);return rows.get(row.meta.report_id);},async stage(id,key){rows.get(id).stage_id=key;},async complete(id,key){const r=rows.get(id);r.file_id=key;r.status='saved';return r;},async list(){return [...rows.values()].filter(r=>r.status==='saved');}};
 const files={async prepare(key){return {file_id:key,client:{url:'https://test.cos.ap-shanghai.myqcloud.com/'+key,method:'PUT',headers:{authorization:'short-lived'}}};},async read(key){return blobs.get(key);},async put(key,bytes){blobs.set(key,bytes);return key;},async downloadURL(key){return 'https://private.example/'+key;}};
 const handle=createService({env:{FEEDBACK_ADMIN_TOKEN:admin,UPLOAD_TICKET_SECRET:'ticket-secret-1234567890123456789012345',PUBLIC_BASE_PATH:'/cad-qto-feedback'},store,files});
 const meta={schema:'cad-qto-review-upload/1.0',report_id:crypto.randomUUID(),receipt_token:'c'.repeat(64),consent:true,sha256:sha(html),filename:'合成复核.html',project_id:'合成项目',review_id:'全专业',revision:'测试版',source_sha256:'a'.repeat(64),plugin_version:'test',annotations_count:1,draft_count:1,bytes:Buffer.byteLength(html)};
 const request=(path,body,token)=>new Request('https://tencent.test/cad-qto-feedback'+path,{method:body?'POST':'GET',headers:{Origin:'null',...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 return {rows,blobs,handle,meta,request};
}
test('腾讯云无需反馈码先签发直传凭据，完整校验后才收件；状态凭据和管理员权限分离',async()=>{
 const f=fixture();const info=await (await f.handle(f.request('/api/info'))).json();assert.equal(info.ready,true);assert.equal(info.upload_access,'open');const init=await f.handle(f.request('/api/reviews/init',f.meta));assert.equal(init.status,200);assert.equal(init.headers.get('Access-Control-Allow-Origin'),'null');const body=await init.json();assert(body.ticket);assert.equal(body.accepted,undefined);
 const status=()=>f.handle(f.request('/api/status/'+f.meta.report_id,null,f.meta.receipt_token));assert.equal((await (await status()).json()).accepted,false);
 const row=f.rows.get(f.meta.report_id);f.blobs.set(row.stage_id,Buffer.from(html));const complete=await f.handle(f.request('/api/reviews/complete',{ticket:body.ticket}));assert.equal(complete.status,202);const receipt=await complete.json();assert.equal(receipt.sha256,sha(html));assert.equal(f.blobs.get(row.file_id).toString(),html);assert.equal((await (await status()).json()).accepted,true);
 assert.equal((await f.handle(f.request('/api/admin/artifacts/'+receipt.receipt,null))).status,401);
 const download=await f.handle(f.request('/api/admin/artifacts/'+receipt.receipt,null,admin));assert.equal(download.status,200);assert((await download.json()).url.includes('/reviews/'));
 const again=await f.handle(f.request('/api/reviews/init',f.meta));assert.equal((await again.json()).accepted,true);assert.equal(f.rows.size,1);
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
