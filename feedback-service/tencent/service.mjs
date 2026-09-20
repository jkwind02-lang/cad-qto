import {createHash,createHmac,timingSafeEqual} from 'node:crypto';
export const MAX_HTML=20*1024*1024;
export const sha=value=>createHash('sha256').update(value).digest('hex');
export class InputError extends Error {constructor(message,status=400){super(message);this.status=status;}}
const fail=(message,status)=>{throw new InputError(message,status);};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function validateMetadata(m){
 const keys=['schema','report_id','receipt_token','consent','sha256','filename','project_id','review_id','revision','source_sha256','plugin_version','annotations_count','draft_count','bytes'];
 if(!m||Array.isArray(m)||Object.keys(m).some(k=>!keys.includes(k))||m.schema!=='cad-qto-review-upload/1.0'||m.consent!==true||!uuid.test(m.report_id)||!/^([0-9a-f]{64})$/i.test(m.receipt_token))fail('复核上传元数据不正确');
 for(const key of ['filename','project_id','review_id','revision','plugin_version'])if(typeof m[key]!=='string'||m[key].length>300||/[\u0000-\u001f]/.test(m[key]))fail('项目摘要字段不正确');
 for(const key of ['sha256','source_sha256'])if(!/^[0-9a-f]{64}$/.test(m[key]))fail('文件校验值不正确');
 for(const key of ['annotations_count','draft_count'])if(!Number.isSafeInteger(m[key])||m[key]<0)fail('批注数量不正确');
 if(!Number.isSafeInteger(m.bytes)||m.bytes<1||m.bytes>MAX_HTML)fail('HTML为空或超过20MiB',413);
 const meta={};for(const key of keys)if(key!=='receipt_token')meta[key]=m[key];
 return {meta,receipt_hash:sha(m.receipt_token),payload_hash:sha(JSON.stringify(meta))};
}
function receipt(row){return {accepted:row.status==='saved',receipt:'TC-'+row.meta.report_id,status:row.status,
 ...(row.status==='saved'?{sha256:row.meta.sha256,bytes:row.meta.bytes}:{}),
 message:row.status==='saved'?'完整HTML已存入腾讯云主站，等待维护人员复核。':'HTML尚未核对完成，请使用原内容重试。'};}
function ticket(row,secret){const body=Buffer.from(JSON.stringify({id:row.meta.report_id,hash:row.payload_hash,expires:Date.now()+600000})).toString('base64url');return body+'.'+createHmac('sha256',secret).update(body).digest('hex');}
function readTicket(value,secret){
 if(typeof value!=='string'||value.length>2000)fail('上传凭据不正确');
 const [body,sig,...extra]=value.split('.');const expected=createHmac('sha256',secret).update(body||'').digest();
 if(extra.length||!sig||!/^[0-9a-f]{64}$/.test(sig)||!timingSafeEqual(Buffer.from(sig,'hex'),expected))fail('上传凭据不正确');
 let data;try{data=JSON.parse(Buffer.from(body,'base64url').toString());}catch{fail('上传凭据不正确');}
 if(!uuid.test(data.id)||!Number.isSafeInteger(data.expires)||data.expires<Date.now())fail('上传凭据已过期，请用原文件重试');return data;
}
export function createService({env,store,files}){
 const authorized=(value,secret)=>secret&&sha(value||'')===sha('Bearer '+secret);
 return async function handle(request){
  const url=new URL(request.url),origin=request.headers.get('Origin'),headers={'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
  if(origin==='null'){headers['Access-Control-Allow-Origin']='null';headers.Vary='Origin';}
  const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers});
  if(request.method==='OPTIONS'&&origin==='null')return new Response(null,{status:204,headers:{...headers,'Access-Control-Allow-Methods':'GET, POST','Access-Control-Allow-Headers':'Content-Type, Authorization'}});
  try{
   const base=env.PUBLIC_BASE_PATH||'',path=base&&url.pathname.startsWith(base+'/')?url.pathname.slice(base.length):url.pathname;
   const ready=env.FEEDBACK_ACCESS_CODE?.length>=16&&env.UPLOAD_TICKET_SECRET?.length>=32&&env.FEEDBACK_ADMIN_TOKEN?.length>=32;
   if(request.method==='GET'&&path==='/api/info')return reply({provider:'tencent',role:'primary',ready:!!ready,max_html_bytes:MAX_HTML});
   if(!ready)fail('腾讯云收件服务尚未配置完成',503);
   if(request.method==='GET'&&/^\/api\/status\/[0-9a-f-]{36}$/i.test(path)){
    const row=await store.get(path.split('/').at(-1)),token=(request.headers.get('Authorization')||'').replace(/^Bearer /,'');
    if(!row||sha(token)!==row.receipt_hash)fail('未找到可查看的反馈',404);return reply(receipt(row));
   }
   if(request.method==='GET'&&path.startsWith('/api/admin/')){
    if(!authorized(request.headers.get('Authorization'),env.FEEDBACK_ADMIN_TOKEN))fail('需要维护者取件码',401);
    if(path==='/api/admin/reports')return reply({reports:(await store.list()).map(r=>({...receipt(r),project_id:r.meta.project_id,review_id:r.meta.review_id,filename:r.meta.filename,created_at:r.created_at}))});
    if(/^\/api\/admin\/artifacts\/TC-[0-9a-f-]{36}$/i.test(path)){
     const row=await store.get(path.split('/').at(-1).slice(3));if(!row||row.status!=='saved')fail('未找到完整HTML',404);
     return reply({...receipt(row),filename:row.meta.filename,url:await files.downloadURL(row.file_id)});
    }
    fail('没有这个入口',404);
   }
   if(request.method!=='POST'||!['/api/reviews/init','/api/reviews/complete'].includes(path))fail('没有这个入口',404);
   if(!['null',url.origin].includes(origin))fail('请从复核页面提交',403);
   if(!authorized(request.headers.get('Authorization'),env.FEEDBACK_ACCESS_CODE))fail('团队反馈码不正确',401);
   if(!/^application\/json(?:;|$)/i.test(request.headers.get('Content-Type')||''))fail('上传请求格式不正确',415);
   const raw=await bounded(request.body,16384);let body;try{body=JSON.parse(raw.toString('utf8'));}catch{fail('上传请求格式不正确');}
   if(path.endsWith('/init')){
    const value=validateMetadata(body),row=await store.reserve({...value,created_at:Date.now(),status:'uploading'});
    if(row.receipt_hash!==value.receipt_hash||row.payload_hash!==value.payload_hash)fail('提交编号已关联另一份文件',409);
    if(row.status==='saved')return reply(receipt(row));
    const upload=await files.prepare('cad-qto-feedback/staging/'+row.meta.report_id+'/'+row.meta.sha256+'.bin');
    await store.stage(row.meta.report_id,upload.file_id);
    return reply({ticket:ticket(row,env.UPLOAD_TICKET_SECRET),upload:upload.client});
   }
   if(!body||Object.keys(body).length!==1||!body.ticket)fail('上传确认格式不正确');
   const signed=readTicket(body.ticket,env.UPLOAD_TICKET_SECRET),row=await store.get(signed.id);
   if(!row||row.payload_hash!==signed.hash)fail('上传凭据与提交记录不符',409);
   if(row.status==='saved')return reply(receipt(row));
   if(!row.stage_id)fail('文件尚未上传');
   const bytes=await files.read(row.stage_id,MAX_HTML);
   if(bytes.length!==row.meta.bytes||sha(bytes)!==row.meta.sha256)fail('HTML校验不一致，请重新保存并上传');
   let html;try{html=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{fail('HTML必须为UTF-8');}
   if(!html.includes('id="feedback-seed"')||!html.includes('id="payload"'))fail('不是本插件生成的完整复核HTML');
   // Only the server can write the final path. A replay of a staging credential cannot
   // replace a reviewed artifact after the hash was verified.
   const file_id=await files.put('cad-qto-feedback/reviews/'+row.meta.report_id+'/'+row.meta.sha256+'.bin',bytes);
   const saved=await store.complete(row.meta.report_id,file_id);
   return reply(receipt(saved),202);
  }catch(e){return reply({error:e instanceof InputError?e.message:'腾讯云服务暂时不可用，请保留本地HTML后重试'},e instanceof InputError?e.status:503);}
 };
}
export async function bounded(stream,max){
 if(!stream)fail('缺少请求内容');const reader=stream.getReader(),parts=[];let count=0;
 while(true){const {done,value}=await reader.read();if(done)break;count+=value.length;if(count>max){await reader.cancel();fail('文件或请求超过接收上限',413);}parts.push(Buffer.from(value));}
 return Buffer.concat(parts);
}
