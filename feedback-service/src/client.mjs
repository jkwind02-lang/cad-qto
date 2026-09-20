// This function is served as a script; keep browser dependencies inside it.
export function feedbackClient() {
  'use strict';
  const $=id=>document.getElementById(id),fields=['summary','professional','category','actual','expected','reason','steps','contact','plugin_version'];
  const key='cad-qto-problem-draft-v1';let draft={},submitted=false,pending;
  const text=m=>$('status').textContent=m;
  const collect=()=>Object.fromEntries(fields.map(k=>[k,$(k).value.trim()]));
  const store=()=>{try{localStorage.setItem(key,JSON.stringify(draft));}catch{text('本机暂存不可用，请使用“保存反馈到本机”。');}};
  function fresh() {return {report_id:crypto.randomUUID(),receipt_token:Array.from(crypto.getRandomValues(new Uint8Array(32)),n=>n.toString(16).padStart(2,'0')).join('')};}
  try{draft=JSON.parse(localStorage.getItem(key)||'{}');}catch{draft={};}
  for(const k of fields)if(typeof draft[k]==='string')$(k).value=draft[k];
  function update() {draft={...fresh(),...collect()};submitted=false;store();$('preview').hidden=true;$('check').hidden=true;}
  for(const k of fields)$(k).oninput=update;
  if(!draft.report_id)update();
  async function json(url,init) {
    const res=await fetch(url,{...init,signal:AbortSignal.timeout(20000)});let data;
    try{data=await res.json();}catch{throw Error('服务暂时不可用；内容仍在本页，请保存后重试。');}
    if(!res.ok)throw Error(data.error||'提交未完成');return data;
  }
  $('form').onsubmit=e=>{e.preventDefault();pending={...draft,...collect(),consent:true};
    $('preview-text').textContent=JSON.stringify(collect(),null,2);$('preview').hidden=false;$('consent').checked=false;};
  $('edit').onclick=()=>$('preview').hidden=true;
  $('send').onclick=async()=>{
    if(!$('consent').checked){text('请先核对发送内容并勾选确认。');return;}
    $('send').disabled=true;
    try {const result=await json('/api/reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      submitted=true;draft={...pending,receipt:result.receipt};delete draft.consent;store();text(result.receipt+'\n'+result.message);$('check').hidden=false;$('new').hidden=false;
    }catch(e){text(e.message+'\n可用相同内容重试，提交编号保持不变。');}finally{$('send').disabled=false;}
  };
  $('check').onclick=async()=>{try{const r=await json('/api/status/'+draft.report_id,{headers:{'Authorization':'Bearer '+draft.receipt_token}});text(r.receipt+'\n'+r.message);}catch(e){text(e.message);}};
  $('new').onclick=()=>{if(!submitted&&draft.actual&&!confirm('将清空当前填写内容，开始另一条反馈。'))return;
    for(const k of fields)if(!['professional','category','plugin_version'].includes(k))$(k).value='';update();$('new').hidden=true;text('');};
  $('export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(collect(),null,2)],{type:'application/json;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='插件问题反馈.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  if(draft.receipt){submitted=true;text(draft.receipt+'\n本机保留了上次收件编号。');$('check').hidden=false;$('new').hidden=false;}
  json('/api/info').then(r=>{$('notice').textContent=r.notice;if(!r.ready)text('反馈服务尚未配置完成，目前可以填写并保存到本机。');}).catch(()=>text('当前无法连接反馈服务，可以先保存到本机。'));
}
