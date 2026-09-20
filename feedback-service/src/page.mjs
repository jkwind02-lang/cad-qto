export const PAGE=`<!doctype html>
<html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CAD 算量 · 问题反馈</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#20364b;font:15px/1.65 "Microsoft YaHei",sans-serif}
main{max-width:840px;margin:24px auto;padding:24px;background:white;border:1px solid #d5e0e8;border-radius:10px}
h1{font-size:23px;margin:0 0 8px}.muted{color:#617587;font-size:13px}label{display:block;margin:16px 0 5px;font-weight:bold}
input,select,textarea,button{font:inherit}input,select,textarea{width:100%;padding:9px;border:1px solid #a8bdca;border-radius:5px}
textarea{min-height:100px;resize:vertical}button{padding:9px 15px;border:1px solid #235c7a;border-radius:5px;background:white;color:#235c7a;cursor:pointer}
button.primary{background:#235c7a;color:white}button:disabled{opacity:.5;cursor:default}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f6fa;padding:15px;max-height:55vh;overflow:auto;font:inherit}
#status{white-space:pre-wrap;padding:12px 0}input[type=checkbox]{width:auto}#preview[hidden]{display:none}@media(max-width:600px){main{margin:0;border-radius:0;padding:16px}.row{display:block}}
</style><main>
<h1>反馈插件问题</h1><p class="muted">无需 GitHub 账号。填写后先查看发送内容，再提交。复核页中的批注可直接用“保存并反馈”，无需在此重复填写。</p>
<p id="notice" class="muted"></p>
<form id="form"><label for="summary">问题概述 *</label><input id="summary" maxlength="120" required>
<div class="row"><div><label for="professional">专业</label><input id="professional" maxlength="80" placeholder="如：给排水、混凝土、电气"></div>
<div><label for="category">问题类型</label><select id="category"><option>识图判断</option><option>数量或计算</option><option>复核页面</option><option>安装或运行</option><option>改进建议</option><option>其他</option></select></div></div>
<label for="actual">实际遇到的情况 *</label><textarea id="actual" maxlength="2500" required></textarea>
<label for="expected">你确认的结果（可选）</label><textarea id="expected" maxlength="2500"></textarea>
<label for="reason">判断原因／依据（可选）</label><textarea id="reason" maxlength="2500"></textarea>
<label for="steps">如何出现这个问题（可选）</label><textarea id="steps" maxlength="1500"></textarea>
<div class="row"><div><label for="plugin_version">插件版本（可选）</label><input id="plugin_version" maxlength="80"></div><div><label for="contact">方便联系你的方式（可选）</label><input id="contact" maxlength="120"></div></div>
<label for="code">团队反馈码</label><input id="code" type="password" autocomplete="off" placeholder="由插件维护人员提供；不是 GitHub 密码">
<div class="actions"><button type="submit" class="primary">查看发送内容</button><button type="button" id="export">保存反馈到本机</button></div></form>
<section id="preview" hidden><h2>将发送的内容</h2><pre id="preview-text"></pre>
<label><input type="checkbox" id="consent">我已核对以上内容，并同意发送给插件维护人员。</label>
<div class="actions"><button id="send" class="primary">提交反馈</button><button id="edit">返回修改</button></div></section>
<div id="status" role="status" aria-live="polite"></div>
<div class="actions"><button id="check" hidden>查询收件状态</button><button id="new" hidden>填写另一条</button></div>
</main><script src="/client.js"></script></html>`;
