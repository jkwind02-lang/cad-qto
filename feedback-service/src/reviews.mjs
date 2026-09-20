import {Store} from './store.mjs';
import {digest,validate,InputError,response,receipt,syncOne} from './worker.mjs';
const MAX_HTML=20*1024*1024;
async function bounded(request) {
 const max=MAX_HTML+32768;
 if(Number(request.headers.get('Content-Length'))>max)throw new InputError('HTML超过20MiB接收上限；本地文件仍保留',413);
 const reader=request.body?.getReader();if(!reader)throw new InputError('缺少HTML');
 const chunks=[];let length=0;
 while(true){const {done,value}=await reader.read();if(done)break;length+=value.length;
  if(length>max){await reader.cancel();throw new InputError('HTML超过接收上限',413);}chunks.push(value);}
 const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
 return bytes;
}
export async function hashBytes(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');}
export async function uploadReview(request,env,ctx) {
 const origin=new URL(request.url).origin;
 if(![origin,'null'].includes(request.headers.get('Origin')))throw new InputError('请从复核页面提交',403);
 if(!env.DB||!env.REVIEWS||!env.FEEDBACK_ACCESS_CODE||env.FEEDBACK_ACCESS_CODE.length<16||!env.IP_HASH_SALT)throw new InputError('完整HTML接收服务尚未配置完成',503);
 if(await digest(request.headers.get('Authorization')||'')!==await digest('Bearer '+env.FEEDBACK_ACCESS_CODE))throw new InputError('团队反馈码不正确',401);
 const type=request.headers.get('Content-Type')||'';
 if(!type.startsWith('multipart/form-data;'))throw new InputError('需要上传HTML文件',415);
 let form,meta;
 try{form=await new Response(await bounded(request),{headers:{'Content-Type':type}}).formData();
  const text=form.get('metadata');if(typeof text!=='string'||text.length>12000)throw Error();meta=JSON.parse(text);
 }catch(e){if(e instanceof InputError)throw e;throw new InputError('上传内容格式不正确');}
 if([...form.keys()].some(k=>!['html','metadata'].includes(k))||form.getAll('html').length!==1||form.getAll('metadata').length!==1)throw new InputError('上传字段不正确');
 const file=form.get('html');if(!file||typeof file==='string'||file.size===0||file.size>MAX_HTML)throw new InputError('HTML为空或超过20MiB',413);
 const keys=['schema','report_id','receipt_token','consent','sha256','filename','project_id','review_id','revision','source_sha256','plugin_version','annotations_count','draft_count'];
 if(!meta||typeof meta!=='object'||Object.keys(meta).some(k=>!keys.includes(k))||meta.schema!=='cad-qto-review-upload/1.0')throw new InputError('复核上传元数据不正确');
 for(const field of ['filename','project_id','review_id','revision','plugin_version'])if(typeof meta[field]!=='string'||meta[field].length>300||/[\u0000-\u001f]/.test(meta[field]))throw new InputError('项目摘要字段不正确');
 if(!/^[0-9a-f]{64}$/i.test(meta.sha256)||!/^([0-9a-f]{64})$/i.test(meta.source_sha256)||!Number.isSafeInteger(meta.annotations_count)||meta.annotations_count<0||!Number.isSafeInteger(meta.draft_count)||meta.draft_count<0)throw new InputError('文件校验或批注数量不正确');
 const bytes=await file.arrayBuffer(),sha=await hashBytes(bytes);if(sha!==meta.sha256.toLowerCase())throw new InputError('HTML校验不一致，请重新保存并上传');
 let html;try{html=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{throw new InputError('HTML必须为UTF-8');}
 if(!html.includes('id="feedback-seed"')||!html.includes('id="payload"'))throw new InputError('不是本插件生成的完整复核HTML');
 const standard=validate({report_id:meta.report_id,receipt_token:meta.receipt_token,consent:meta.consent,summary:'完整复核HTML反馈',category:'识图判断',
  professional:'见HTML专业筛选',actual:'项目：'+meta.project_id+'\n复核范围：'+meta.review_id+'\n版本：'+meta.revision+'\n已保存意见：'+meta.annotations_count+'；草稿：'+meta.draft_count,
  expected:'查看完整HTML中的用户结论；草稿不是已确认意见。',reason:'查看对应对象、图页、分组、用户原因和修改历史。',plugin_version:meta.plugin_version});
 const artifact={key:'reviews/'+sha+'.html',sha256:sha,bytes:file.size,filename:meta.filename.replace(/[\\/<>:"|?*]/g,'_'),
  project_id:meta.project_id,review_id:meta.review_id,revision:meta.revision,source_sha256:meta.source_sha256,annotations_count:meta.annotations_count,draft_count:meta.draft_count};
 const payload=JSON.stringify({...standard,artifact}),store=new Store(env.DB),entry={report_id:meta.report_id,receipt_hash:await digest(meta.receipt_token),payload_hash:await digest(payload),payload,
  client_hash:await digest(env.IP_HASH_SALT+'|'+(request.headers.get('CF-Connecting-IP')||'unknown')),created_at:Date.now(),status:'uploading'};
 let row=await store.accept(entry);if(!row)throw new InputError('提交较频繁，请稍后重试；本地HTML仍保留',429);
 if(row.receipt_hash!==entry.receipt_hash||row.payload_hash!==entry.payload_hash)throw new InputError('提交编号已关联另一份文件，请重新保存',409);
 if(row.status==='uploading') {
  await env.REVIEWS.put(artifact.key,bytes,{httpMetadata:{contentType:'application/octet-stream'},customMetadata:{sha256:sha}});
  await store.ready(row.report_id);row=await store.get(row.report_id);
 }
 ctx.waitUntil(syncOne(env,row,store));return response(receipt(row,env.FEEDBACK_ROLE),202);
}
export async function downloadReview(request,env) {
 if(!env.FEEDBACK_ADMIN_TOKEN||env.FEEDBACK_ADMIN_TOKEN.length<32||await digest(request.headers.get('Authorization')||'')!==await digest('Bearer '+env.FEEDBACK_ADMIN_TOKEN))throw new InputError('需要维护者取件码',401);
 const number=Number(new URL(request.url).pathname.split('/').at(-1).slice(3)),row=await new Store(env.DB).getNumber(number);
 const artifact=row&&JSON.parse(row.payload).artifact;
 if(!artifact||row.status==='uploading')throw new InputError('未找到完整文件',404);
 const object=await env.REVIEWS.get(artifact.key);if(!object)throw new InputError('文件暂不可用，请联系维护人员',404);
 return new Response(object.body,{headers:{'Content-Type':'application/octet-stream',
  'Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent(artifact.filename),
  'Content-Length':String(artifact.bytes),'X-Content-SHA256':artifact.sha256,'Cache-Control':'no-store',
  'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox",'Referrer-Policy':'no-referrer'}});
}
export const ADMIN_PAGE=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>维护者取件</title>
<style>body{max-width:660px;margin:40px auto;padding:20px;font:16px/1.7 "Microsoft YaHei",sans-serif;color:#20364b}input,button{font:inherit;padding:10px;display:block;width:100%;box-sizing:border-box;margin:8px 0 20px}#status{white-space:pre-wrap}</style>
<h1>下载完整复核HTML</h1><p>仅限维护人员。文件按附件下载，不在接收服务网页中执行。</p>
<form id="admin-form"><label for="receipt">收件编号</label><input id="receipt" placeholder="FB-000001" required><label for="token">维护者取件码</label><input id="token" type="password" autocomplete="off" required><button>下载并核对文件</button></form><p id="status" role="status"></p><script src="/admin.js"></script></html>`;
export function adminClient(){
 const $=id=>document.getElementById(id);
 $('admin-form').onsubmit=async e=>{e.preventDefault();const receipt=$('receipt').value.trim();if(!/^FB-\d{6,}$/.test(receipt)){$('status').textContent='请输入完整收件编号';return;}
  try{const r=await fetch('/api/artifacts/'+receipt,{headers:{Authorization:'Bearer '+$('token').value},signal:AbortSignal.timeout(60000)});
   if(!r.ok)throw Error((await r.json()).error||'下载失败');const blob=await r.blob(),hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))].map(b=>b.toString(16).padStart(2,'0')).join('');
   if(hash!==r.headers.get('X-Content-SHA256'))throw Error('文件校验不一致，未保存');const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;
   const header=r.headers.get('Content-Disposition')||'',name=header.match(/filename\*=UTF-8''(.+)$/);a.download=name?decodeURIComponent(name[1]):receipt+'.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   $('status').textContent=receipt+' 文件校验通过，已请求浏览器保存。';
  }catch(e){$('status').textContent=e.message;}finally{$('token').value='';}};
}
