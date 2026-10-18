// Actual offline Edge interaction on the source-DXF CLI output. No quantity changes.
const fs=require('node:fs'),path=require('node:path'),{pathToFileURL}=require('node:url'),{createHash}=require('node:crypto');
const {chromium}=require(process.env.CAD_QTO_PLAYWRIGHT_MODULE||'playwright');
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],checks=[],errors=[],pageErrors=[];
const report={schema:'qto-t16-browser-evidence/1.0',checks,errors,page_errors:pageErrors,official:false,business_adoption:false};
const digest=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
(async()=>{let browser;try{
 browser=await chromium.launch({channel:'msedge',headless:true});report.browser_version=browser.version();
 const context=await browser.newContext({offline:true,acceptDownloads:true,viewport:{width:1440,height:1000}}),page=await context.newPage();page.on('pageerror',e=>pageErrors.push(e.message));
 const check=(name,pass)=>{checks.push({name,pass:!!pass});if(!pass)errors.push(name);};
 async function payload(){return page.evaluate(async()=>{const b=Uint8Array.from(atob(document.getElementById('payload').textContent.replace(/\s/g,'')),c=>c.charCodeAt(0));return await QtoReviewCore.readPayload(new Uint8Array(await new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()));});}
 await page.goto(pathToFileURL(arg('--html')).href);await page.waitForFunction(()=>!document.getElementById('loading'));
 const before=await payload();report.before_sha256=digest(before);
 check('源图与配准图页分别保留',before.scenes.length===3&&before.objects.length===8&&before.scenes.some(s=>s.title.includes('非原图量测')));
 await page.locator('#list button.item[data-key="旧编号"]').click();
 const hint=page.locator('[data-role="number-drift"]');await hint.waitFor({state:'visible'});
 check('编号漂移进入对象详情且明确未扣量',(await hint.innerText()).includes('疑似编号漂移')&&(await hint.innerText()).includes('未合并、未扣量'));
 await hint.getByRole('button',{name:'查看 新编号',exact:true}).click();
 check('候选对方编号可从图面详情切换',(await page.locator('#editor-title').innerText()).includes('新编号'));
 await page.getByText('查看完整依据、属性与原话',{exact:true}).click();
 check('完整嵌套源身份呈现于详情',(await page.locator('#detail').innerText()).includes('/DEVICE/'));
 check('提示交互保持全部对象与数量不变',digest(await payload())===report.before_sha256&&before.objects.every(o=>o.quantities.length===0));
 await page.screenshot({path:path.join(path.dirname(arg('--report')),'编号漂移_实际图面.png'),fullPage:true});
 report.after_sha256=digest(await payload());check('浏览器无脚本错误',pageErrors.length===0);
 if(args.includes('--context-html')){
  const other=await context.newPage();other.on('pageerror',e=>pageErrors.push(e.message));await other.goto(pathToFileURL(arg('--context-html')).href);await other.waitForFunction(()=>!document.getElementById('loading'));
  check('跨版本同编号保持两个列表记录',await other.locator('#list button.item[data-key="旧编号"]').count()===2);
  await other.locator('#list button.item[data-key="旧编号"]').first().click();
  check('同编号选择只激活当前catalog_key',await other.locator('#list button.item.active').count()===1);
  check('跨版本同编号不声明共用同一实物标记',!(await other.locator('#object-facts').innerText()).includes('同一实物 2 条'));
  await other.screenshot({path:path.join(path.dirname(arg('--report')),'跨版本同号_实际图面.png'),fullPage:true});
 }
 for(const [flag,same]of [['--structured-html',true],['--structured-different-html',false]])if(args.includes(flag)){
  const other=await context.newPage();other.on('pageerror',e=>pageErrors.push(e.message));await other.goto(pathToFileURL(arg(flag)).href+'#mode=review');await other.waitForFunction(()=>!document.getElementById('loading'));
  await other.locator('#mode-objects').click();
  const count=await other.locator('#list button.item[data-key="S1"]').count();
  check(same?'结构字典键序及1与1.0保持同组':'布尔与数值上下文保持不同组',count===(same?1:2));
  await other.locator('#list button.item[data-key="S1"]').first().click();
  check(same?'合法结构上下文保留两条来源记录提示':'不同JSON类型不声明同一实物',(await other.locator('#object-facts').innerText()).includes('同一实物 2 条')===same);
  check((same?'同组':'分组')+'只激活当前catalog_key',await other.locator('#list button.item.active').count()===1);
  await other.screenshot({path:path.join(path.dirname(arg('--report')),same?'结构上下文_同内容字典.png':'结构上下文_布尔数值区分.png'),fullPage:true});
 }
}catch(e){errors.push(e.stack||String(e));}finally{if(browser)await browser.close();report.pass=errors.length===0&&pageErrors.length===0&&checks.every(c=>c.pass);fs.writeFileSync(arg('--report'),JSON.stringify(report,null,2));if(!report.pass)process.exitCode=2;}})();
