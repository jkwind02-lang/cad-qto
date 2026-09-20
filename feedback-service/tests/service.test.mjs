import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker,{handle,digest,validate,syncOne,scheduled,issueBody} from '../src/worker.mjs';
import {Store} from '../src/store.mjs';
import {hashBytes} from '../src/reviews.mjs';

// Real SQLite executes the same SQL as D1; platform deployment is a separate check.
function database() {
 const db=new DatabaseSync(':memory:');db.exec(readFileSync(new URL('../migrations/0001_feedback.sql',import.meta.url),'utf8'));
 return {raw:db,prepare(sql){const st=db.prepare(sql);let args=[];const q={bind(...v){args=v;return q;},
  async first(){return st.get(...args)||null;},async all(){return {results:st.all(...args)};},
  async run(){const r=st.run(...args);return {meta:{changes:Number(r.changes)}};}};return q;}};
}
const code='test-team-code-123456789',repo='test-owner/private-feedback';
function environment(){return {DB:database(),FEEDBACK_ACCESS_CODE:code,IP_HASH_SALT:'test-only-salt',GITHUB_REPOSITORY:repo,GITHUB_TOKEN:'test-token-only'};}
function body(overrides={}){return {report_id:crypto.randomUUID(),receipt_token:'a'.repeat(64),consent:true,
 summary:'DN分界判断错误',professional:'给排水',category:'识图判断',actual:'转接头上方判断为DN65',expected:'应为DN50',reason:'转接头才是变径边界',steps:'查看立管转接位置',contact:'',plugin_version:'test',...overrides};}
function request(data,options={}){return new Request('https://feedback.test/api/reports',{method:'POST',headers:{
 Origin:'https://feedback.test','Content-Type':'application/json',Authorization:'Bearer '+code,...options.headers},body:JSON.stringify(data)});}
function context(){const jobs=[];return {jobs,waitUntil(p){jobs.push(p);},async finish(){await Promise.all(jobs);}};}
function githubMock(options={}) {
 let creates=0;const issues=[];
 globalThis.fetch=async(url,init={})=>{
  assert(String(url).startsWith('https://api.github.com/repos/'+repo));
  assert.equal(init.redirect,'manual');
  if(init.method==='POST'){
   creates++;if(options.delay)await options.delay;
   if(options.timeout)throw Error('secret-never-record-this');
   const payload=JSON.parse(init.body);issues.push({number:creates,body:payload.body});
   return Response.json(options.postStatus?{message:'secret-never-record-this'}:{number:creates},{status:options.postStatus||201});
  }
  if(String(url).includes('/issues?'))return Response.json(options.recovered||issues);
  return Response.json({private:options.publicRepo!==true});
 };
 return {get creates(){return creates;},issues};
}
async function acceptOnly(env,data=body()) {
 const payload=validate(data),store=new Store(env.DB);
 return store.accept({report_id:data.report_id,receipt_hash:await digest(data.receipt_token),payload_hash:await digest(JSON.stringify(payload)),
  payload:JSON.stringify(payload),client_hash:'test-client',created_at:Date.now()});
}
test('保存后返回收件编号，后台写入私有GitHub；重复提交只建一条',async()=>{
 const env=environment(),ctx=context(),b=body(),gh=githubMock();
 const response=await handle(request(b),env,ctx);assert.equal(response.status,202);
 const result=await response.json();assert.equal(result.status,'saved');assert.match(result.receipt,/^FB-\d{6}$/);
 await ctx.finish();assert.equal(gh.creates,1);
 const again=context();await handle(request(b),env,again);await again.finish();assert.equal(gh.creates,1);
 const status=await handle(new Request('https://feedback.test/api/status/'+b.report_id,{headers:{Authorization:'Bearer '+b.receipt_token}}),env,context());
 const receipt=await status.json();assert.equal(receipt.status,'synced');assert.equal(receipt.payload,undefined);assert.equal(receipt.issue_number,undefined);
});
test('缺少授权、未确认、未知字段和超长内容均不入库',async()=>{
 const env=environment();githubMock();
 for(const b of [body({consent:false}),body({drawing_path:'D:/private.dwg'}),body({actual:'x'.repeat(2501)})]){
  assert.equal((await handle(request(b),env,context())).status,400);
 }
 assert.equal((await handle(request(body(),{headers:{Authorization:'Bearer wrong'}}),env,context())).status,401);
 assert.equal(env.DB.raw.prepare('SELECT count(*) n FROM reports').get().n,0);
});
test('本地HTML跨域只开放null来源且必须有团队码；其他站点被拒绝',async()=>{
 const env=environment(),ctx=context();githubMock();
 const pre=await worker.fetch(new Request('https://feedback.test/api/reports',{method:'OPTIONS',headers:{Origin:'null'}}),env,ctx);
 assert.equal(pre.status,204);assert.equal(pre.headers.get('Access-Control-Allow-Origin'),'null');
 const r=await worker.fetch(request(body(),{headers:{Origin:'null'}}),env,ctx);assert.equal(r.status,202);assert.equal(r.headers.get('Access-Control-Allow-Origin'),'null');await ctx.finish();
 const denied=await worker.fetch(request(body(),{headers:{Origin:'https://unrelated.test'}}),env,context());assert.equal(denied.status,403);assert.equal(denied.headers.get('Access-Control-Allow-Origin'),null);
});
test('凭据未配置关闭接收；状态查询不能枚举其他反馈',async()=>{
 const env=environment();env.FEEDBACK_ACCESS_CODE='';assert.equal((await handle(request(body()),env,context())).status,503);
 const row=await acceptOnly(env);const r=await handle(new Request('https://feedback.test/api/status/'+row.report_id,{headers:{Authorization:'Bearer wrong'}}),env,context());assert.equal(r.status,404);
});
test('丢失GitHub应答保留uncertain，自动核对已有编号，不盲目重复创建',async()=>{
 const env=environment(),row=await acceptOnly(env),gh=githubMock({timeout:true});await syncOne(env,row);
 let stored=await new Store(env.DB).get(row.report_id);assert.equal(stored.status,'uncertain');assert(!stored.last_error.includes('secret'));
 env.DB.raw.prepare('UPDATE reports SET next_attempt=0').run();
 const recovered=githubMock({recovered:[{number:42,body:issueBody(row)}]});await scheduled(env);
 stored=await new Store(env.DB).get(row.report_id);assert.equal(stored.status,'synced');assert.equal(stored.issue_number,42);assert.equal(gh.creates,1);assert.equal(recovered.creates,0);
});
test('并发处理同一反馈只允许一个GitHub POST',async()=>{
 const env=environment(),row=await acceptOnly(env);let release;const delay=new Promise(r=>release=r),gh=githubMock({delay});
 const a=syncOne(env,row),b=syncOne(env,row);release();await Promise.all([a,b]);assert.equal(gh.creates,1);
});
test('失效权限明确拒绝可自动重试；公开仓库禁止写入',async()=>{
 const env=environment(),row=await acceptOnly(env);githubMock({postStatus:403});await syncOne(env,row);assert.equal((await new Store(env.DB).get(row.report_id)).status,'pending');
 env.DB.raw.prepare('UPDATE reports SET next_attempt=0').run();const gh=githubMock({publicRepo:true});await syncOne(env,row);assert.equal(gh.creates,0);assert.equal((await new Store(env.DB).get(row.report_id)).last_error,'private_feedback_repository_required');
});
test('重启遗留sending转入核对；找不到已建问题时不重复POST',async()=>{
 const env=environment(),row=await acceptOnly(env),gh=githubMock();
 env.DB.raw.prepare("UPDATE reports SET status='sending',lease_until=1").run();await scheduled(env);
 assert.equal((await new Store(env.DB).get(row.report_id)).status,'uncertain');assert.equal(gh.creates,0);
});
test('提交编号碰撞与内容变化不覆盖原反馈',async()=>{
 const env=environment(),b=body(),ctx=context();githubMock();await handle(request(b),env,ctx);await ctx.finish();
 assert.equal((await handle(request({...b,expected:'不同内容'}),env,context())).status,409);
 assert.equal(JSON.parse((await new Store(env.DB).get(b.report_id)).payload).expected,'应为DN50');
});
test('限流使用实际SQLite约束，重复回执不占新配额',async()=>{
 const env=environment(),first=await acceptOnly(env);
 for(let n=1;n<120;n++)assert(await acceptOnly(env));
 assert.equal(await acceptOnly(env),null);
 assert.equal((await new Store(env.DB).accept(first)).report_id,first.report_id);
});
test('流式请求超限被拒绝，不依赖可伪造的Content-Length',async()=>{
 const env=environment(),data=body({actual:'字'.repeat(20000)});assert.equal((await handle(request(data),env,context())).status,413);
 assert.equal(env.DB.raw.prepare('SELECT count(*) n FROM reports').get().n,0);
});
test('HTML和Markdown中的恶意文本按文字保留，响应不携带配置秘密',async()=>{
 const env=environment(),row=await acceptOnly(env,body({actual:'<script>alert(1)</script> @all'}));
 assert(issueBody(row).includes('&lt;script&gt;'));assert(!issueBody(row).includes('@all'));
 const r=await handle(new Request('https://feedback.test/'),env,context());assert(r.headers.get('Content-Security-Policy').includes("script-src 'self'"));assert(!(await r.text()).includes(env.GITHUB_TOKEN));
});
function bucket(){const files=new Map();return {files,async put(key,bytes){files.set(key,new Uint8Array(bytes));},async get(key){const bytes=files.get(key);return bytes?{body:bytes}:null;}};}
async function reviewRequest(html,overrides={}){
 const bytes=new TextEncoder().encode(html),metadata={schema:'cad-qto-review-upload/1.0',report_id:crypto.randomUUID(),receipt_token:'c'.repeat(64),consent:true,
  sha256:await hashBytes(bytes),filename:'项目复核.html',project_id:'合成项目',review_id:'八层给排水',revision:'复核一版',source_sha256:'a'.repeat(64),plugin_version:'test',annotations_count:1,draft_count:1,...overrides};
 const form=new FormData();form.append('metadata',JSON.stringify(metadata));form.append('html',new Blob([html],{type:'text/html'}),metadata.filename);
 return {metadata,request:new Request('https://feedback.test/api/reviews',{method:'POST',headers:{Origin:'null',Authorization:'Bearer '+code},body:form})};
}
const sampleHTML='<!doctype html><html lang="zh-CN"><script id="feedback-seed" type="application/json">{"意见":"DN50","原因":"转接头边界"}</script><script id="payload" type="application/octet-stream">合成图面</script></html>';
test('完整HTML先私有入库再收件，维护者下载逐字节一致',async()=>{
 const env=environment();env.REVIEWS=bucket();env.FEEDBACK_ADMIN_TOKEN='admin-only-token-12345678901234567890';const ctx=context(),gh=githubMock(),submission=await reviewRequest(sampleHTML);
 const r=await worker.fetch(submission.request,env,ctx);assert.equal(r.status,202);const received=await r.json();assert.equal(received.sha256,submission.metadata.sha256);await ctx.finish();assert.equal(gh.creates,1);assert(gh.issues[0].body.includes('合成项目'));
 const url='https://feedback.test/api/artifacts/'+received.receipt;
 assert.equal((await worker.fetch(new Request(url),env,context())).status,401);
 assert.equal((await worker.fetch(new Request(url,{headers:{Authorization:'Bearer '+code}}),env,context())).status,401);
 const download=await worker.fetch(new Request(url,{headers:{Authorization:'Bearer '+env.FEEDBACK_ADMIN_TOKEN}}),env,context());
 assert.equal(download.status,200);assert.match(download.headers.get('Content-Disposition'),/^attachment;/);assert.equal(await download.text(),sampleHTML);assert.equal(download.headers.get('X-Content-SHA256'),submission.metadata.sha256);
});
test('文件校验失败不能收件，文件存储失败不能显示成功或发GitHub',async()=>{
 const env=environment();env.REVIEWS=bucket();const bad=await reviewRequest(sampleHTML,{sha256:'0'.repeat(64)});
 assert.equal((await worker.fetch(bad.request,env,context())).status,400);assert.equal(env.REVIEWS.files.size,0);
 env.REVIEWS.put=async()=>{throw Error('storage-failed');};const ctx=context(),gh=githubMock(),good=await reviewRequest(sampleHTML);
 assert.equal((await worker.fetch(good.request,env,ctx)).status,503);await ctx.finish();assert.equal(gh.creates,0);
 const state=await worker.fetch(new Request('https://feedback.test/api/status/'+good.metadata.report_id,{headers:{Authorization:'Bearer '+good.metadata.receipt_token}}),env,context());assert.equal((await state.json()).accepted,false);
 env.REVIEWS=bucket();const retry=await reviewRequest(sampleHTML,good.metadata),second=context();const r=await worker.fetch(retry.request,env,second);assert.equal(r.status,202);await second.finish();assert.equal(gh.creates,1);
});
test('相同完整文件同编号重复上传只收一份，大小和非HTML标记受检查',async()=>{
 const env=environment();env.REVIEWS=bucket();const first=await reviewRequest(sampleHTML),ctx=context(),gh=githubMock();await worker.fetch(first.request,env,ctx);await ctx.finish();
 const second=await reviewRequest(sampleHTML,first.metadata),ctx2=context();assert.equal((await worker.fetch(second.request,env,ctx2)).status,202);await ctx2.finish();assert.equal(gh.creates,1);assert.equal(env.REVIEWS.files.size,1);
 const wrong=await reviewRequest('<html>无复核数据</html>');assert.equal((await worker.fetch(wrong.request,env,context())).status,400);
});
test('Cloudflare备用模式保存完整HTML但不独立创建或重试GitHub问题',async()=>{
 const env=environment();env.FEEDBACK_ROLE='backup';env.REVIEWS=bucket();const gh=githubMock(),ctx=context(),submission=await reviewRequest(sampleHTML);
 const info=await worker.fetch(new Request('https://feedback.test/api/info'),env,ctx);assert.equal((await info.json()).role,'backup');
 const r=await worker.fetch(submission.request,env,ctx);assert.equal(r.status,202);const receipt=await r.json();assert.match(receipt.message,/备用站/);assert.equal(receipt.accepted,true);
 await ctx.finish();await scheduled(env);assert.equal(gh.creates,0);assert.equal(env.REVIEWS.files.size,1);
 const row=await new Store(env.DB).get(submission.metadata.report_id);assert.equal(row.attempts,0);
});
