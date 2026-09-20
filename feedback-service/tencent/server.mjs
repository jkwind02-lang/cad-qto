import http from 'node:http';
import {Readable} from 'node:stream';
import cloudbase from '@cloudbase/node-sdk';
import {createService} from './service.mjs';
import {CloudBaseFiles,CloudBaseStore} from './cloudbase.mjs';
// HTTP functions use a named server API key, never a copied CLI login credential.
if(!process.env.CLOUDBASE_APIKEY)throw Error('Dedicated server API key is required');
const app=cloudbase.init({env:process.env.TCB_ENV,accessKey:process.env.CLOUDBASE_APIKEY,timeout:60000});
const handle=createService({env:process.env,store:new CloudBaseStore(app.database()),files:new CloudBaseFiles(app)});
http.createServer(async(req,res)=>{
 try{
  const url='https://'+(req.headers.host||'feedback.local')+req.url;
  const request=new Request(url,{method:req.method,headers:req.headers,...(['GET','HEAD'].includes(req.method)?{}:{body:Readable.toWeb(req),duplex:'half'})});
  const response=await handle(request);res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(503,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end('{"error":"收件服务暂不可用"}');}
}).listen(9000,'0.0.0.0');
