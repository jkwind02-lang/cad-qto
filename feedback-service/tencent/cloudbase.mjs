import {InputError,bounded,attachmentHeader} from './service.mjs';
const REPORTS='cad_qto_feedback_reports',CONTROL='cad_qto_feedback_control';
const ok=r=>{if(r.code)throw Error('cloudbase_operation_failed');return r;};
const first=r=>{ok(r);return Array.isArray(r.data)?r.data[0]:r.data;};
export class CloudBaseStore {
 constructor(db){this.db=db;}
 async get(id){return first(await this.db.collection(REPORTS).doc(id).get());}
 async reserve(row){return this.db.runTransaction(async tx=>{
  const doc=tx.collection(REPORTS).doc(row.meta.report_id),existing=first(await doc.get());if(existing)return existing;
  const day=new Date(row.created_at).toISOString().slice(0,10),limit=tx.collection(CONTROL).doc('daily-'+day),counter=first(await limit.get());
  if((counter?.count||0)>=2000)throw new InputError('今日收件较多，请稍后重试；本地HTML仍保留',429);
  ok(await limit.set({count:(counter?.count||0)+1}));ok(await doc.set(row));return row;
 });}
 async stage(id,file_id){ok(await this.db.collection(REPORTS).doc(id).update({stage_id:file_id}));}
 async complete(id,file_id){ok(await this.db.collection(REPORTS).doc(id).update({file_id,status:'saved'}));return this.get(id);}
 async relocate(id,previous,file_id){return this.db.runTransaction(async tx=>{
  const doc=tx.collection(REPORTS).doc(id),row=first(await doc.get());
  if(!row||row.status!=='saved')throw new InputError('收件记录已变化，未更新',409);
  if(row.file_id!==previous){if(row.file_id===file_id)return row;throw new InputError('文件记录已变化，未覆盖',409);}
  const update={file_id,legacy_file_id:row.legacy_file_id||previous,filename_restored_at:Date.now()};
  ok(await doc.update(update));return {...row,...update};
 });}
 async migrationPage(offset=0){return ok(await this.db.collection(REPORTS).where({status:'saved'}).orderBy('_id','asc').skip(offset).limit(100).get()).data;}
 async list(){return ok(await this.db.collection(REPORTS).where({status:'saved'}).orderBy('created_at','desc').limit(100).get()).data;}
}
export class CloudBaseFiles {
 constructor(app){this.app=app;}
 async prepare(cloudPath){
  const r=await this.app.getUploadMetadata({cloudPath});if(r.code||!r.data?.fileId)throw Error('upload_metadata_unavailable');const d=r.data;
  return {file_id:d.fileId,client:{url:d.url,method:'PUT',headers:{Signature:d.authorization,'x-cos-security-token':d.token,'x-cos-meta-fileid':d.cosFileId,authorization:d.authorization,key:encodeURIComponent(cloudPath)}}};
 }
 async downloadURL(fileID){const r=await this.app.getTempFileURL({fileList:[{fileID,maxAge:120}]});const file=r.fileList?.[0];if(file?.code!=='SUCCESS'||!file.tempFileURL)throw Error('file_unavailable');return file.tempFileURL;}
 async read(fileID,max){const r=await fetch(await this.downloadURL(fileID),{redirect:'error',signal:AbortSignal.timeout(60000)});if(!r.ok)throw Error('file_unavailable');if(Number(r.headers.get('Content-Length'))>max){await r.body.cancel();throw new InputError('HTML超过20MiB接收上限',413);}return bounded(r.body,max);}
 async put(cloudPath,bytes,filename){
  // Use the SDK's scoped credential, adding download metadata to the normal COS PUT.
  const upload=await this.prepare(cloudPath),target=new URL(upload.client.url);
  if(target.protocol!=='https:'||!/^[-a-z0-9]+\.cos\.[-a-z0-9]+\.myqcloud\.com$/.test(target.hostname)||target.username||target.password||target.hash)throw Error('invalid_storage_destination');
  const r=await fetch(target,{method:'PUT',headers:{...upload.client.headers,'Content-Type':'application/octet-stream','Content-Disposition':attachmentHeader(filename)},body:bytes,redirect:'error',signal:AbortSignal.timeout(60000)});
  if(!r.ok){await r.body?.cancel();throw Error('file_not_stored');}
  await r.body?.cancel();return upload.file_id;
 }
}
