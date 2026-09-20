import {Store} from './store.mjs';
import {PAGE} from './page.mjs';
import {feedbackClient} from './client.mjs';
import {uploadReview,downloadReview,ADMIN_PAGE,adminClient} from './reviews.mjs';

const MAX_BYTES=24576;
const FIELDS={summary:120,professional:80,category:40,actual:2500,expected:2500,reason:2500,steps:1500,contact:120,plugin_version:80};
const CATEGORIES=['识图判断','数量或计算','复核页面','安装或运行','改进建议','其他'];
export class InputError extends Error { constructor(message,status=400) { super(message);this.status=status; } }
export async function digest(text) {
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));
  return [...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');
}
export function validate(body) {
  if(!body||typeof body!=='object'||Array.isArray(body))throw new InputError('反馈格式不正确');
  const allowed=new Set(['report_id','receipt_token','consent',...Object.keys(FIELDS)]);
  if(Object.keys(body).some(k=>!allowed.has(k)))throw new InputError('含有未允许发送的字段');
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.report_id||''))throw new InputError('提交编号不正确');
  if(!/^[0-9a-f]{64}$/i.test(body.receipt_token||''))throw new InputError('回执凭据不正确');
  if(body.consent!==true)throw new InputError('请先核对并确认发送内容');
  const payload={};
  for(const [key,max] of Object.entries(FIELDS)) {
    const value=body[key]??'';
    if(typeof value!=='string'||value.length>max||/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value))throw new InputError('字段格式或长度不正确：'+key);
    payload[key]=value.trim();
  }
  if(!payload.summary||!payload.actual)throw new InputError('请填写问题概述和实际遇到的情况');
  if(!CATEGORIES.includes(payload.category))throw new InputError('请选择问题类型');
  return payload;
}
export function response(data,status=200,type='application/json; charset=utf-8') {
  return new Response(typeof data==='string'?data:JSON.stringify(data),{status,headers:{
    'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
    'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
  }});
}
export function receipt(row,role='primary') {
  if(row.status==='uploading')return {accepted:false,receipt:'FB-'+String(row.number).padStart(6,'0'),status:'incomplete',message:'文件尚未收完整，请用原内容重新上传。'};
  const synced=row.status==='synced';
  const artifact=JSON.parse(row.payload).artifact;
  return {accepted:true,receipt:'FB-'+String(row.number).padStart(6,'0'),status:synced?'synced':'saved',
    ...(artifact?{sha256:artifact.sha256,bytes:artifact.bytes}:{}),
    message:role==='backup'?'完整反馈已保存于Cloudflare备用站；主备按提交编号核对，备用站不重复创建GitHub问题。':synced?'反馈已收到，已转交维护人员。':'反馈已保存，正在转交维护人员；无需重复提交。'};
}
async function readBody(request) {
  if(!/^application\/json(?:;|$)/i.test(request.headers.get('Content-Type')||''))throw new InputError('仅接受JSON反馈',415);
  if(Number(request.headers.get('Content-Length'))>MAX_BYTES)throw new InputError('反馈过长',413);
  const reader=request.body?.getReader();if(!reader)throw new InputError('缺少反馈内容');
  let size=0;const chunks=[];
  while(true) { const {done,value}=await reader.read();if(done)break;size+=value.byteLength;
    if(size>MAX_BYTES) { await reader.cancel();throw new InputError('反馈过长',413); }chunks.push(value); }
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  try{return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{throw new InputError('反馈内容无法解析');}
}
function repository(env) {
  if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.GITHUB_REPOSITORY||''))throw Error('repository_not_configured');
  if(!env.GITHUB_TOKEN)throw Error('github_not_configured');
  return env.GITHUB_REPOSITORY;
}
async function github(env,path,method='GET',body) {
  return fetch('https://api.github.com'+path,{method,redirect:'manual',signal:AbortSignal.timeout(12000),headers:{
    'Authorization':'Bearer '+env.GITHUB_TOKEN,'Accept':'application/vnd.github+json',
    'X-GitHub-Api-Version':'2026-03-10','User-Agent':'cad-qto-feedback','Content-Type':'application/json'
  },...(body?{body:JSON.stringify(body)}:{})});
}
// Keep user text literal in GitHub Markdown and neutralize accidental mentions.
function quote(text) {return '<pre>'+text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/@/g,'&#64;')+'</pre>';}
export function issueBody(row) {
  const p=JSON.parse(row.payload),names={summary:'问题概述',professional:'专业',category:'问题类型',actual:'实际情况',expected:'用户确认的结果',reason:'判断原因',steps:'复现步骤',contact:'联系信息',plugin_version:'插件版本'};
  const artifact=p.artifact?'\n\n### 完整复核HTML（私有存储）\n'+quote(JSON.stringify({receipt:'FB-'+String(row.number).padStart(6,'0'),...p.artifact},null,2))+'\n维护人员在反馈服务的 /admin 页面输入收件编号取件。文件只能经管理员验证后下载，不能公开预览。':'';
  return '<!-- cad-qto-report:'+row.report_id+' -->\n'+Object.entries(names).map(([k,n])=>'### '+n+'\n'+quote(p[k]||'未填写')).join('\n\n')+artifact+'\n\n用户反馈，尚未验证；不能直接作为通用计算规则或正式工程量。';
}
export async function syncOne(env,row,store=new Store(env.DB),now=Date.now()) {
  if(env.FEEDBACK_ROLE==='backup')return;
  if(!await store.claim(row.report_id,now))return;
  let postStarted=false;
  try {
    const repo=repository(env),meta=await github(env,'/repos/'+repo);
    if(!meta.ok)throw Error('repository_http_'+meta.status);
    if((await meta.json()).private!==true)throw Error('private_feedback_repository_required');
    postStarted=true;
    const r=await github(env,'/repos/'+repo+'/issues','POST',{
      title:('[反馈] '+JSON.parse(row.payload).summary).replace(/@/g,'＠'),body:issueBody(row)
    });
    if(r.status===201) {
      const issue=await r.json();if(!Number.isSafeInteger(issue.number)||issue.number<1)throw Error('invalid_github_receipt');
      await store.done(row.report_id,issue.number);return;
    }
    // Explicit authentication/validation/rate failures did not create an issue.
    if([400,401,403,404,410,422,429].includes(r.status)) {
      postStarted=false;throw Error('github_http_'+r.status);
    }
    throw Error('github_delivery_uncertain');
  } catch(error) {
    // Never store upstream response bodies: they can contain credentials or private data.
    const code=/^(repository_|github_|private_|invalid_)/.test(error.message)?error.message:'network_or_storage_error';
    if(postStarted)await store.uncertain(row.report_id,code,now);
    else await store.retry(row.report_id,code,now,row.attempts+1);
  }
}
export async function reconcile(env,row,store,now) {
  // Only recover a known issue. A timed-out POST must not create a second one.
  try {
    const repo=repository(env),since=new Date(row.created_at-60000).toISOString();
    for(let page=1;page<=3;page++) {
      const r=await github(env,'/repos/'+repo+'/issues?state=all&sort=created&direction=desc&per_page=100&page='+page+'&since='+encodeURIComponent(since));
      if(!r.ok)break;const items=await r.json();if(!Array.isArray(items))break;
      const found=items.find(i=>!i.pull_request&&String(i.body).includes('<!-- cad-qto-report:'+row.report_id+' -->'));
      if(found){await store.done(row.report_id,found.number);return;}
      if(items.length<100)break;
    }
  } catch { /* Keep accepted feedback durable for maintenance inspection. */ }
  await store.uncertain(row.report_id,'delivery_needs_reconciliation',now+3000000);
}
export async function scheduled(env) {
  if(env.FEEDBACK_ROLE==='backup')return;
  const store=new Store(env.DB),now=Date.now();
  for(const row of await store.due(now)) {
    if(row.status==='uncertain')await reconcile(env,row,store,now);
    else await syncOne(env,row,store,now);
  }
}
export async function handle(request,env,ctx) {
  const url=new URL(request.url);
  // file:// review pages have Origin:null. A separate team code is still required.
  if(request.method==='OPTIONS'&&request.headers.get('Origin')==='null') {
    return new Response(null,{status:204,headers:{'Access-Control-Allow-Origin':'null',
      'Access-Control-Allow-Methods':'POST, GET','Access-Control-Allow-Headers':'Authorization, Content-Type','Vary':'Origin'}});
  }
  if(request.method==='GET'&&url.pathname==='/')return response(PAGE,200,'text/html; charset=utf-8');
  if(request.method==='GET'&&url.pathname==='/client.js')return response('('+feedbackClient.toString()+')();',200,'text/javascript; charset=utf-8');
  if(request.method==='GET'&&url.pathname==='/admin')return response(ADMIN_PAGE,200,'text/html; charset=utf-8');
  if(request.method==='GET'&&url.pathname==='/admin.js')return response('('+adminClient.toString()+')();',200,'text/javascript; charset=utf-8');
  if(request.method==='GET'&&url.pathname==='/api/info')return response({provider:'cloudflare',role:env.FEEDBACK_ROLE||'primary',notice:env.PRIVACY_NOTICE||'',ready:!!env.DB&&!!env.REVIEWS&&!!env.FEEDBACK_ACCESS_CODE&&!!env.IP_HASH_SALT});
  try {
    if(request.method==='POST'&&url.pathname==='/api/reviews')return await uploadReview(request,env,ctx);
    if(request.method==='GET'&&/^\/api\/artifacts\/FB-\d{6,}$/.test(url.pathname))return await downloadReview(request,env);
    if(request.method==='POST'&&url.pathname==='/api/reports') {
      if(![url.origin,'null'].includes(request.headers.get('Origin')))throw new InputError('请从反馈页面提交',403);
      if(!env.DB||!env.FEEDBACK_ACCESS_CODE||env.FEEDBACK_ACCESS_CODE.length<16||!env.IP_HASH_SALT)throw new InputError('服务尚未配置完成，请联系维护人员',503);
      const auth=request.headers.get('Authorization')||'';
      if(await digest(auth)!==await digest('Bearer '+env.FEEDBACK_ACCESS_CODE))throw new InputError('团队反馈码不正确',401);
      const body=await readBody(request),payload=validate(body),store=new Store(env.DB);
      const value={report_id:body.report_id,receipt_hash:await digest(body.receipt_token),payload_hash:await digest(JSON.stringify(payload)),
        payload:JSON.stringify(payload),client_hash:await digest(env.IP_HASH_SALT+'|'+(request.headers.get('CF-Connecting-IP')||'unknown')),created_at:Date.now()};
      const row=await store.accept(value);
      if(!row)throw new InputError('提交较频繁，请稍后再试；内容仍保留在本页',429);
      if(row.receipt_hash!==value.receipt_hash||row.payload_hash!==value.payload_hash)throw new InputError('这次提交编号已被使用，请新建一条反馈',409);
      ctx.waitUntil(syncOne(env,row,store));
      return response(receipt(row,env.FEEDBACK_ROLE),202);
    }
    if(request.method==='GET'&&/^\/api\/status\/[0-9a-f-]{36}$/i.test(url.pathname)) {
      if(!env.DB)throw new InputError('服务暂不可用',503);
      const row=await new Store(env.DB).get(url.pathname.split('/').at(-1));
      const token=(request.headers.get('Authorization')||'').replace(/^Bearer /,'');
      if(!row||!token||await digest(token)!==row.receipt_hash)throw new InputError('未找到可查看的反馈',404);
      return response(receipt(row,env.FEEDBACK_ROLE));
    }
    return response({error:'没有这个入口'},404);
  } catch(error) {
    return response({error:error instanceof InputError?error.message:'服务暂时不可用，请保留内容后重试'},error instanceof InputError?error.status:503);
  }
}
export default {
  async fetch(request,env,ctx) {
    const result=await handle(request,env,ctx);
    if(request.headers.get('Origin')==='null') {
      result.headers.set('Access-Control-Allow-Origin','null');result.headers.set('Vary','Origin');
    }
    return result;
  },
  scheduled:(_event,env,ctx)=>ctx.waitUntil(scheduled(env))
};
