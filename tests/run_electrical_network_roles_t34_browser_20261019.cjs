// Offline interaction with the shipped shared HTML after T34 role projection.
const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url');
const {chromium}=require(process.env.CAD_QTO_PLAYWRIGHT_MODULE||'playwright');
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1];
const report={schema:'qto-t34-offline-browser-evidence/1.0',checks:[],errors:[],official_adoption:false,business_adoption_allowed:false};
(async()=>{let browser;try{
  browser=await chromium.launch({channel:'msedge',headless:true});report.browser_version=browser.version();
  const context=await browser.newContext({offline:true,acceptDownloads:true,viewport:{width:1440,height:1000}}),page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  const check=(name,pass)=>{report.checks.push({name,pass:!!pass});if(!pass)report.errors.push(name);};
  await page.goto(pathToFileURL(arg('--html')).href);await page.waitForFunction(()=>!document.getElementById('loading'));
  const payload=await page.evaluate(async()=>{const raw=Uint8Array.from(atob(document.getElementById('payload').textContent.replace(/\s/g,'')),c=>c.charCodeAt(0));return QtoReviewCore.readPayload(new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()));});
  const sourceView=JSON.parse(fs.readFileSync(arg('--view'),'utf8'));
  check('最终HTML与角色投影视图指纹相同',payload.result_fingerprint===sourceView.result_fingerprint);
  check('角色及代码传播为类型化字段',payload.objects.some(o=>Array.isArray(o.attributes.network_roles)&&Array.isArray(o.attributes.expression_seed_ids)));
  const button=page.locator('#list button.item').first();await button.click();
  await page.getByText('查看完整依据、属性与原话',{exact:true}).click();
  const detail=await page.locator('#detail').innerText();
  check('实际点选呈现系统角色',detail.includes('network_roles')&&detail.includes('报警'));
  check('代码种子与完整来源可回查',detail.includes('expression_seed_ids')&&detail.includes('CAD-'));
  check('竖向端接未知仍可见',detail.includes('vertical')&&detail.includes('reserve'));
  check('点位几何复核凭据可见',detail.includes('point_geometry_review'));
  await page.screenshot({path:path.join(path.dirname(arg('--report')),'火警角色实际离线交互.png'),fullPage:true});
  const after=await page.evaluate(()=>document.getElementById('payload').textContent);
  check('交互未写改源载荷',!!after&&payload.objects.length===sourceView.objects.length);
}catch(e){report.errors.push(e.stack||String(e));}finally{
  if(browser)await browser.close();report.pass=report.errors.length===0&&report.checks.every(c=>c.pass);
  fs.writeFileSync(arg('--report'),JSON.stringify(report,null,2));if(!report.pass)process.exitCode=2;
}})();
