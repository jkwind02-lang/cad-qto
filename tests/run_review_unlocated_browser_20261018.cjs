// Real Chromium DOM integration, no project data and no quantity/adoption writes.
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const args=process.argv.slice(2), arg=k=>args[args.indexOf(k)+1];
const {chromium}=require(process.env.CAD_QTO_PLAYWRIGHT_MODULE || 'playwright');
const checks=[],errors=[];
const report={schema:'qto-unlocated-browser-check/1.0',checks,errors,quantity_changes:0};
const digest=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
(async()=>{
 let browser;
 try{
  browser=await chromium.launch({channel:process.env.CAD_QTO_BROWSER_CHANNEL || 'msedge',headless:true});
  report.browser_version=browser.version();
  const context=await browser.newContext({offline:true,viewport:{width:1460,height:950}});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(pathToFileURL(arg('--html')).href+'#mode=review');
  await page.waitForFunction(()=>!document.getElementById('loading'));
  async function viewData(){return await page.evaluate(async()=>{
   const bytes=Uint8Array.from(atob(document.getElementById('payload').textContent.replace(/\s/g,'')),c=>c.charCodeAt(0));
   return await QtoReviewCore.readPayload(new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()));
  });}
  const before=await viewData();report.business_data_before_sha256=digest(before);
  async function state(){return await page.locator('#unlocated').evaluate(e=>({hidden:e.hidden,text:e.textContent,visible:!!e.getClientRects().length}));}
  async function check(name,condition){const actual=await state();const pass=condition(actual);checks.push({name,pass,actual});if(!pass)errors.push(name);}
  async function route(hash){await page.evaluate(h=>{location.hash=h;},hash);await page.waitForTimeout(60);}
  async function seedU1(){await route('mode=review&object=U1');}
  await seedU1();await check('图面单对象合法未定位：U1自己的原因',s=>!s.hidden&&s.text.includes('U1独有原因'));
  await route('mode=resolve&issue=Q1');
  await check('混合多成员问题定位已定位成员清除U1旧黄条',s=>s.hidden&&s.text==='');
  await page.locator('#member-next2').click();
  await check('多成员切下一处保持无旧黄条',s=>s.hidden&&s.text==='');
  await page.locator('#map-close').click();
  await check('关闭图面清空定位提示',s=>s.hidden&&s.text==='');
  await page.locator('#map-open').click();
  await check('重新打开已定位问题仍无旧黄条',s=>s.hidden&&s.text==='');
  await route('mode=results&item=I2');
  await check('纯未定位目标手动开图只显示U2原因',s=>!s.hidden&&s.text.includes('U2独有原因')&&!s.text.includes('U1独有原因'));
  await page.locator('#map-auto').click();
  await check('纯未定位目标自动收起时清空提示',s=>s.hidden&&s.text==='');
  await page.locator('#map-open').click();
  await check('纯未定位目标重开时恢复U2原因',s=>!s.hidden&&s.text.includes('U2独有原因'));
  await route('mode=results&item=I3');
  await check('空focus目标清空上次提示',s=>s.hidden&&s.text==='');
  await seedU1();await page.locator('#scene').selectOption('P2');
  await check('切换到其他图页清空旧对象提示',s=>s.hidden&&s.text==='');
  await seedU1();await page.locator('#search').fill('没有此对象的筛选');
  await check('筛选无对象清空focused提示',s=>s.hidden&&s.text==='');
  report.business_data_after_sha256=digest(await viewData());
  const same=report.business_data_before_sha256===report.business_data_after_sha256;
  checks.push({name:'全部业务视图数据未变（含数量、对象、审核映射和结果指纹）',pass:same});
  if(!same)errors.push('业务视图被改写');
  report.status=errors.length?'FAIL':'PASS';
 }catch(e){errors.push(e.stack||e.message);report.status='FAIL';}
 finally{if(browser)await browser.close();fs.writeFileSync(arg('--report'),JSON.stringify(report,null,2),'utf8');console.log(JSON.stringify(report,null,2));process.exitCode=errors.length?1:0;}
})();
