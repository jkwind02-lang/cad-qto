(function(){
'use strict';
const $=id=>document.getElementById(id),MAX_HTML=20*1024*1024;
function endpoint(value){
 const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash||!/^\/[a-zA-Z0-9_/-]*$/.test(u.pathname))throw Error('反馈地址必须是没有账号信息的HTTPS服务地址');return u.origin+u.pathname.replace(/\/$/,'');
}
async function hash(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');}
function attach({data,getFeedback,saveLocal}){
 const config=JSON.parse($('problem-config').textContent),dlg=$('problem-dialog');
 let busy=false,server='',backup='',pending=null;
 const primaryName=config.provider==='tencent'?'腾讯云':'主站';
 const message=text=>$('problem-message').textContent=text;
 const signature=()=>{const value={...getFeedback()};delete value.exported_at;return JSON.stringify(value);};
 const close=()=>{if(!busy)dlg.close();};
 $('problem-close').onclick=close;dlg.oncancel=e=>{if(busy)e.preventDefault();};
 $('problem-local').onclick=()=>{saveLocal();message('已请求浏览器保存完整带批注HTML，请在下载位置核对文件。');};
 function open(){
  const f=getFeedback();$('problem-consent').checked=false;$('problem-check').hidden=!pending;
  server='';backup='';try{if(config.url)server=endpoint(config.url);if(config.backup_url)backup=endpoint(config.backup_url);}catch(e){message(e.message);}
  $('problem-destination').textContent=server?primaryName+'主站：'+server+(backup?'\nCloudflare备用：'+backup+'（仅主站网络或服务故障时使用）':''):'在线反馈尚未开通，当前可保存到本机。';
  $('problem-preview').textContent='项目：'+data.project_id+'\n复核范围：'+data.review_id+'\n成果版本：'+data.revision+
   '\n已保存意见：'+Object.keys(f.annotations||{}).length+' 条\n草稿：'+Object.keys(f.drafts||{}).length+' 条'+
   '\n上传内容：与本次本地保存完全一致的完整HTML，包括内嵌图面、对象明细、结论、原因和历史。'+
   '\n未内嵌在HTML中的原始DWG或其他附件不会自动上传。\n插件版本：'+config.plugin_version;
  $('problem-send').disabled=!server;message('完整项目资料会随HTML进入私有存储；草稿一并保留供定位，不作为已确认结论。');dlg.showModal();
 }
 $('report-problem').onclick=open;
 if(config.url){$('save-html').textContent='保存并反馈';$('save-html').onclick=open;}
 const error=(text,retryable=false)=>Object.assign(Error(text),{retryable});
 async function json(url,options={}){
  let res;try{res=await fetch(url,{credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(90000),...options});}
  catch{throw error('网络中断或等待超时',true);}
  let result;try{result=await res.json();}catch{throw error('未获得有效收件回执',res.ok||res.status>=500);}
  if(!res.ok)throw error(result.error||result.message||'服务未能收件',res.status>=500||res.status===408);
  return result;
 }
 function verify(result){
  if(result.accepted!==true)throw error('文件尚未完整收件',true);
  if(result.sha256!==pending.metadata.sha256||result.bytes!==pending.blob.size)throw error('服务器回执与本地文件不一致，请保留文件并联系维护人员');
  return result;
 }
 async function uploadTo(url,provider){
  const headers={};
  if(provider==='tencent'){
   const init=await json(url+'/api/reviews/init',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({...pending.metadata,bytes:pending.blob.size})});
   if(init.accepted)return verify(init);
   const target=new URL(init.upload?.url);
   if(target.protocol!=='https:'||!/^[-a-z0-9]+\.cos\.[-a-z0-9]+\.myqcloud\.com$/.test(target.hostname)||target.username||target.password||target.hash)throw error('主站返回的COS地址不正确');
   if(!init.ticket)throw error('主站上传凭据不完整');
   let body,method='POST',uploadHeaders={};
   if(init.upload.method==='PUT'){
    method='PUT';body=pending.blob;const allowed=['signature','authorization','x-cos-security-token','x-cos-meta-fileid','key'];
    if(!init.upload.headers||Object.keys(init.upload.headers).some(k=>!allowed.includes(k.toLowerCase())))throw error('主站上传请求头不正确');uploadHeaders=init.upload.headers;
   }else{
    if(!init.upload.fields||typeof init.upload.fields!=='object')throw error('主站上传凭据不完整');
    body=new FormData();for(const [k,v]of Object.entries(init.upload.fields))body.append(k,v);body.append('file',pending.blob,pending.file.filename);
   }
   let res;try{res=await fetch(target.href,{method,headers:uploadHeaders,credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(90000),body});}catch{throw error('COS上传网络中断或等待超时',true);}
   if(!res.ok)throw error('COS未接收文件（'+res.status+'）',res.status>=500||res.status===408);
   return verify(await json(url+'/api/reviews/complete',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({ticket:init.ticket})}));
  }
  const form=new FormData();form.append('metadata',JSON.stringify(pending.metadata));form.append('html',pending.blob,pending.file.filename);
  return verify(await json(url+'/api/reviews',{method:'POST',headers,body:form}));
 }
 async function deliver(){
  if(pending.result)return pending.result;
  try{const result=await uploadTo(server,config.provider||'cloudflare');pending.destination=server;pending.destinationName=primaryName;return result;}
  catch(e){
   if(!backup||!e.retryable)throw e;
   // A lost acknowledgement can follow a successful write. Query first, then retain the
   // same report ID at the backup. Backup services must disable independent issue creation.
   try{const result=await json(server+'/api/status/'+pending.metadata.report_id,{signal:AbortSignal.timeout(15000),headers:{Authorization:'Bearer '+pending.metadata.receipt_token}});
    if(result.accepted){verify(result);pending.destination=server;pending.destinationName=primaryName;return result;}
   }catch(statusError){if(statusError.message.includes('回执与本地文件不一致'))throw statusError;}
   message(primaryName+'暂时未能确认收件，正在将同一份HTML转交Cloudflare备用站…');
   const info=await json(backup+'/api/info',{signal:AbortSignal.timeout(15000)});
   if(info.provider!=='cloudflare'||info.role!=='backup'||!info.ready)throw error('Cloudflare备用站尚未配置为备用接收模式');
   const result=await uploadTo(backup,'cloudflare');pending.destination=backup;pending.destinationName='Cloudflare备用';pending.mainUncertain=true;return result;
  }
 }
 $('problem-send').onclick=async()=>{
  if(busy)return;
  if(!$('problem-consent').checked){message('请先确认发送完整HTML。');return;}
  busy=true;$('problem-send').disabled=true;$('problem-local').disabled=true;
  try {
   const current=signature();
   if(!pending||pending.signature!==current){
    const file=saveLocal(),blob=new Blob([file.html],{type:'text/html;charset=utf-8'});
    if(blob.size>MAX_HTML)throw Error('HTML超过20MiB接收上限；本地保存已发起，请联系维护人员');
    const f=getFeedback(),sha256=await hash(await blob.arrayBuffer());
    const metadata={schema:'cad-qto-review-upload/1.0',report_id:crypto.randomUUID(),
     receipt_token:Array.from(crypto.getRandomValues(new Uint8Array(32)),n=>n.toString(16).padStart(2,'0')).join(''),
     consent:true,sha256,filename:file.filename,project_id:data.project_id,review_id:data.review_id,revision:data.revision,
     source_sha256:data.source_sha256,plugin_version:config.plugin_version,annotations_count:Object.keys(f.annotations||{}).length,draft_count:Object.keys(f.drafts||{}).length};
    pending={file,blob,metadata,signature:signature()};
   }else saveLocal(pending.file);
   message('本地保存已发起，正在同步同一份完整HTML…');
   const result=await deliver();
   pending.receipt=result.receipt;pending.result=result;
   message('完整HTML已收件：'+result.receipt+'（'+pending.destinationName+'）。本地与服务器文件校验一致。'+
    (pending.mainUncertain?'\n主站收件状态仍待核对；主备使用同一提交编号，备用站不另建GitHub问题。':'')+
    '\n请在浏览器下载位置核对本地文件。收件不代表当前项目已复算或问题已修正。');
  }catch(e){message('同步尚未完成：'+e.message+'。\n本地保存已发起；未修改意见时重试会使用同一份文件和提交编号。');}
  finally{busy=false;$('problem-local').disabled=false;$('problem-send').disabled=!server;$('problem-check').hidden=!pending;}
 };
 $('problem-check').onclick=async()=>{
  if(!pending)return;
  try{const result=await json((pending.destination||server)+'/api/status/'+pending.metadata.report_id,{signal:AbortSignal.timeout(20000),headers:{Authorization:'Bearer '+pending.metadata.receipt_token}});
   if(result.accepted)verify(result);message((pending.destinationName||primaryName)+' · '+result.receipt+' · '+result.message);
  }catch(e){message('暂时无法确认：'+e.message+'。请保留本地HTML。');}
 };
}
globalThis.QtoProblemFeedback={attach,hash,endpoint};
})();
