import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const code='runtime-test-team-code-123456789';let posts=0;
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:'.codex_work/bundle/worker.js',compatibilityDate:'2026-09-20',
 host:'127.0.0.1',port:0,d1Databases:{DB:'runtime-test'},r2Buckets:{REVIEWS:'runtime-reviews'},
 bindings:{FEEDBACK_ACCESS_CODE:code,IP_HASH_SALT:'runtime-test-salt',GITHUB_TOKEN:'fake-token',GITHUB_REPOSITORY:'tests/private-feedback',FEEDBACK_ADMIN_TOKEN:'runtime-admin-token-12345678901234567890'},
 outboundService:async request=>{
  assert(request.url.startsWith('https://api.github.com/repos/tests/private-feedback'));
  if(request.method==='POST'){posts++;return Response.json({number:17},{status:201});}
  return Response.json({private:true});
 }}));
try {
 const db=await mf.getD1Database('DB');
 for(const sql of readFileSync('migrations/0001_feedback.sql','utf8').split(';').filter(s=>s.trim()))await db.prepare(sql).run();
 const body={report_id:crypto.randomUUID(),receipt_token:'b'.repeat(64),consent:true,summary:'本地运行时验证',professional:'给排水',category:'识图判断',actual:'虚构测试问题',expected:'虚构正确结果',reason:'虚构依据'};
 const submit=()=>mf.dispatchFetch('https://feedback.test/api/reports',{method:'POST',headers:{Origin:'null','Content-Type':'application/json',Authorization:'Bearer '+code},body:JSON.stringify(body)});
 const r=await submit();assert.equal(r.status,202);assert.equal(r.headers.get('Access-Control-Allow-Origin'),'null');assert.equal((await r.json()).accepted,true);
 let row;for(let n=0;n<100;n++){row=await db.prepare('SELECT * FROM reports').first();if(row?.status==='synced')break;await new Promise(resolve=>setTimeout(resolve,50));}
 assert.equal(row.status,'synced',JSON.stringify({status:row?.status,error:row?.last_error}));assert.equal(row.issue_number,17);
 assert.equal((await submit()).status,202);assert.equal(posts,1);
 const status=await mf.dispatchFetch('https://feedback.test/api/status/'+body.report_id,{headers:{Origin:'null',Authorization:'Bearer '+body.receipt_token}});
 assert.equal((await status.json()).status,'synced');
 const html='<!doctype html><html><script id="feedback-seed" type="application/json">{"reason":"转接头边界"}</script><script id="payload">合成图面</script></html>';
 const metadata={schema:'cad-qto-review-upload/1.0',report_id:crypto.randomUUID(),receipt_token:'c'.repeat(64),consent:true,sha256:createHash('sha256').update(html).digest('hex'),filename:'完整复核.html',project_id:'合成项目',review_id:'八层给排水',revision:'试验版',source_sha256:'a'.repeat(64),plugin_version:'test',annotations_count:1,draft_count:0};
 const form=new FormData();form.append('metadata',JSON.stringify(metadata));form.append('html',new Blob([html]),metadata.filename);
 const encoded=new Request('https://feedback.test/api/reviews',{method:'POST',body:form});
 const uploaded=await mf.dispatchFetch('https://feedback.test/api/reviews',{method:'POST',headers:{Origin:'null',Authorization:'Bearer '+code,'Content-Type':encoded.headers.get('Content-Type')},body:await encoded.arrayBuffer()});
 assert.equal(uploaded.status,202,await uploaded.clone().text());const receipt=await uploaded.json();assert.equal(receipt.sha256,metadata.sha256);
 const denied=await mf.dispatchFetch('https://feedback.test/api/artifacts/'+receipt.receipt);assert.equal(denied.status,401);
 const downloaded=await mf.dispatchFetch('https://feedback.test/api/artifacts/'+receipt.receipt,{headers:{Authorization:'Bearer runtime-admin-token-12345678901234567890'}});assert.equal(downloaded.status,200);assert.equal(await downloaded.text(),html);
 console.log(JSON.stringify({status:'PASS',scope:'Miniflare Workers、D1及R2运行时；GitHub模拟，不是线上部署',checks:7,posts}));
} finally {await mf.dispose();}
