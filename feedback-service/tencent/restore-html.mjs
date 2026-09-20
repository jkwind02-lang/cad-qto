// Maintenance only: copy saved legacy artifacts, verify bytes, then update the index.
// Originals stay private and are retained. No HTML is executed or written locally.
import cloudbase from '@cloudbase/node-sdk';
import {CloudBaseFiles,CloudBaseStore} from './cloudbase.mjs';
import {ensureHtmlArtifact} from './service.mjs';
if(!process.env.TCB_ENV||!process.env.CLOUDBASE_APIKEY)throw Error('Dedicated environment credentials are required');
const app=cloudbase.init({env:process.env.TCB_ENV,accessKey:process.env.CLOUDBASE_APIKEY,timeout:60000});
const store=new CloudBaseStore(app.database()),files=new CloudBaseFiles(app);
const apply=process.argv.includes('--apply');
const results=[];
for(let offset=0;;offset+=100){
 const rows=await store.migrationPage(offset);
 for(const row of rows){
  if(/\.html$/i.test(row.file_id))continue;
  if(!apply){results.push({report_id:row.meta.report_id,status:'planned',bytes:row.meta.bytes,sha256:row.meta.sha256});continue;}
  try{
   const restored=await ensureHtmlArtifact(row,store,files);
   results.push({report_id:row.meta.report_id,status:'restored',bytes:row.meta.bytes,sha256:row.meta.sha256,original_retained:restored.legacy_file_id===row.file_id});
  }catch(error){results.push({report_id:row.meta.report_id,status:'failed',error_code:error.status||'storage_error'});}
 }
 if(rows.length<100)break;
}
const failed=results.filter(r=>r.status==='failed').length;
console.log(JSON.stringify({status:apply?(failed?'INCOMPLETE':'PASS'):'PLAN',restored:apply?results.length-failed:0,planned:apply?0:results.length,failed,results}));
if(failed)process.exitCode=1;
