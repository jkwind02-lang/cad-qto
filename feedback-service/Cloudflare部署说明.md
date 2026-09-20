# 完整HTML保存与问题反馈

本实现让同事在13专业共用的复核页点击“保存并反馈”，本地保存完整HTML，并将完全相同的文件上传至维护者的私有存储。项目身份、内嵌图面、对象明细、结论、原因、历史和草稿一并保留，供定位与复现问题。只上传意见文字的方案已经调整为整份HTML。没有生成HTML的安装、运行问题仍可通过接收站点首页填写文字反馈。

本服务定位为Cloudflare备用站，腾讯云为主站。2026-09-20已创建D1及私有R2、部署Worker并通过合成完整HTML上传和私有下载核验；没有上传真实项目，没有向GitHub发反馈。原生浏览器保存与同事网络尚未验收。最新主备状态见[主站与主备说明](腾讯云主站与主备使用说明.md)。

## 用户使用与文件完整性

1. 在右侧保存本条意见，或保留明确标识的草稿；点击顶部“保存并反馈”。面板显示项目、复核范围、版本、意见及草稿数量，说明将上传整个文件。
2. 确认本HTML中的项目资料可以提供给维护人员即可，无需反馈码。
3. 点击面板内“保存并反馈”。先请求浏览器下载文件，再上传同一份内容。服务器重算SHA-256，文件写入私有存储成功后，返回编号、校验值和字节数；网页比较本地与服务器是否一致。
4. 同一页面打开期间，未修改意见的失败重试使用同一份文件和编号。意见或草稿改变时生成新快照。刷新或关闭页面会清除内存中的待发快照，不承诺跨窗口或跨用户的语义去重。
5. 本地保存是否完成及保存位置须在实际浏览器核对。当前项目的Codex读取完整HTML后才能进行复算，上传和收件不代表问题已修复。

HTML已有的全部资料都会上传；未内嵌在HTML里的原始DWG、说明书或其他附件不会自动带上。没有悄悄扫描电脑或抓取对话的功能。草稿会保留供定位，但不能当作已确认结果。始终可选择“仅保存到本机”。

## 服务组成

| 组成 | 用途 |
|---|---|
| Cloudflare Workers | 接收文件、核对完整性、验证取件权限、后台转交问题 |
| D1 | 记录项目摘要、收件状态、文件索引和GitHub处理状态 |
| 私有R2桶 | 保存完整HTML；不开启公开域名或r2.dev公开访问 |
| 私有GitHub仓库 | 可选转交能力；备用模式禁用独立建单，现阶段未接入 |

代码仓库日后开源时，反馈仓库和项目文件仍保持私有。服务每次创建Issue前检查目标仓库确为私有。文件在接收服务中不执行、不内嵌预览；取件按附件下载，之后按待核实资料处理。

## 维护者所需配置

- Cloudflare账号下的Workers、D1和R2。R2需单独开通订阅并确认结算设置；标准存储有免费额度，不等于无条件或永久零费用。上限、CPU和访问条件须在实际账号中核对。
- 固定私有GitHub反馈仓库；fine-grained token只选择此仓库，Issues为Read and write，Metadata为Read，不需代码写入或仓库管理权限。
- `FEEDBACK_ADMIN_TOKEN`：至少32字符的独立随机维护者取件码。不能交给普通提交者，也不写入HTML。
- `IP_HASH_SALT`：独立随机盐，仅将来源地址做摘要用于限流；数据库不保存原始IP。Cloudflare自身处理请求元数据，不承诺平台零日志。
- `PRIVACY_NOTICE`：补齐维护主体、用途、保留期限及删除联系办法，覆盖完整项目HTML的收集。

账号密码、令牌和取件码不要发在聊天中，也不要写进仓库。上传已取消反馈码，保留已有大小、频率与总量限制。旧`FEEDBACK_ACCESS_CODE`配置不再读取，可以移除；目前基础限制不等于完善的公开服务防滥用机制。

## 部署步骤

在本目录执行；先完成账号与资源范围确认：

```text
npm ci
npx wrangler login
npx wrangler d1 create cad-qto-feedback
npx wrangler r2 bucket create cad-qto-private-reviews
```

把实际数据库ID填入部署用配置的 `d1_databases[0].database_id`；全零ID是未配置标识。保持 `FEEDBACK_ROLE=backup`，补齐告知文案。R2桶名与配置一致并保持私有。备用站无需GitHub令牌，不要把Secret写进配置。Wrangler OAuth部署需要`workers_scripts:write`，只给`workers:write`不足以完成新版部署。

```text
npx wrangler d1 migrations apply cad-qto-feedback --remote
npx wrangler secret put FEEDBACK_ADMIN_TOKEN
npx wrangler secret put IP_HASH_SALT
npm run deploy
```

初次使用按控制台或Wrangler提示创建Worker再配置Secret；配置完成前服务拒绝收件。没有自动开通付费计划、执行账号结算或替用户确认条款的操作。

## 接入所有专业

```text
python skills/_qto-shared-evidence/scripts/build_qto_review_html.py --input 本轮复核视图.json --output 本轮图面复核.html --feedback 用户已保存批注.html --feedback-url https://实际反馈域名
```

只有已部署、已验证的HTTPS根地址才能给同事正式使用。配置后原顶部保存按钮变为“保存并反馈”，不增加长横幅。未配置时保持原本地保存行为。13个专业共用一套模板，不分别复制实现。既有HTML不会被插件更新自动改写，应保留用户意见后重新生成。

`https://feedback.example`仅可作演示占位，不能收件。默认workers.dev地址仍须由同事实测可达性，腾讯云始终为主入口。主站正常收件时不自动复制到R2；备用成功后也不自动回迁。

## 取回文件与处理问题

维护人员打开已部署站点的 `/admin`，输入 `FB-000001` 形式的编号和维护者取件码，即可下载并核对完整HTML。无需反馈码即可提交，下载别人的项目仍须管理员权限。随机回执凭据只能查询这一条的收件状态和校验值。

收件后先读取HTML的数据块定位对象、图页和意见，程序不执行HTML脚本。再复现问题、核对专业适用条件；可推广的规则加匿名回归案例后发布。一份用户意见不会自动修改全局公式。

`backup`模式只持久收件，不独立创建或重试GitHub问题。主站超时不等于没收到，主备记录须按全局`report_id`核对。仅在另行采用单站主服务并配置专用GitHub权限时，代码中的转交与退避流程才运行；不应把两个站同时设置成独立自动建单主站。文件上传失败维持`uploading`，不能显示已收件。

```text
npx wrangler d1 execute cad-qto-feedback --remote --command "SELECT number,status,attempts,last_error FROM reports WHERE status != 'synced' ORDER BY number LIMIT 100"
```

重新处理`uncertain`前先按`cad-qto-report:<report_id>`在GitHub核对。D1记录、R2文件及Issue按实际保留政策一起管理；不因插件更新静默删除。若删除文件，需要同步标明收件索引已过期。

## 大小、限额与验证

- 完整HTML最大20MiB；超限显式提示，本地文件完整保留，不截断图面或意见。部署后须用接近实际最大文件的样例验证CPU、内存和上传时间，不能以小样例证明上限文件已通过。
- 独立文字反馈最大24KiB；不影响整份HTML中的长意见。
- 同一来源摘要最多120条/分钟、1000条/小时，全服务2000条/24小时；共同出口共用限额。相同编号重试不计新记录。这是团队试用限制，不替代公开服务防滥用。
- 本地`file://`页面的跨域来源为`null`，已显式支持，上传无需反馈码。其他托管网站不能直接跨域提交，需另外验证配置。

```text
npm test
npm run check
npm run test:runtime
```

单元测试使用真实SQLite执行D1的SQL，GitHub和R2为模拟。运行时集成测试使用Miniflare的Workers、D1、R2，GitHub仍模拟。DOM测试验证本地下载内容和上传内容逐字节一致、项目和意见保留、失败重试；它不等于原生浏览器视觉或下载验收。

上线使用一份合成HTML验证：本地文件 → 私有D1/R2 → 维护者下载，核对文件SHA-256和字节数相同，再从同事实际网络验证访问。当前GitHub未接入，不能把私有存储收件描述成已经建单。

官方参考：[R2开通](https://developers.cloudflare.com/r2/get-started/)、[R2费用与免费额度](https://developers.cloudflare.com/r2/pricing/)、[Workers费用](https://developers.cloudflare.com/workers/platform/pricing/)、[服务端Secrets](https://developers.cloudflare.com/workers/configuration/secrets/)、[GitHub创建Issue](https://docs.github.com/en/rest/issues/issues#create-an-issue)。
