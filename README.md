# Nodes

ProxyScrape 注册与代理导出工具。Turnstile token 可通过 2Captcha、YesCaptcha、CapMonster Cloud API 或本地浏览器获取，邮箱创建、注册、收信、验证、免费 Premium DC trial 领取与代理列表获取均通过 HTTP 完成。

项目同时提供受登录保护的 Web 控制台，可在浏览器中启动单个/批量任务、
查看实时进度、账号状态、任务日志并下载账号或代理文件。
可与这个项目做代理池 导入使用
https://github.com/Resinat/Resin

## 运行界面

![命令行运行界面](docs/images/runtime-menu.png)

启动后可设置注册数量、并发线程数以及是否隐藏浏览器窗口。

## 免责声明

本项目仅供技术研究、学习交流及合法授权的自动化测试使用，不得用于违反所在地法律法规、目标平台服务条款或损害第三方权益的活动。严禁用于批量养号、垃圾信息、欺诈、绕过访问控制或风控机制等滥用场景。

使用者应自行确认其操作已获得必要授权，并独立承担账号封禁、数据丢失、服务中断及其他直接或间接后果。项目作者与贡献者不对软件的可用性、准确性、合规性作任何明示或默示保证，也不对使用本项目产生的损失或法律责任负责。

## 项目文件

| 文件 | 说明 |
|------|------|
| `proxyscrape_register.py` | 交互式注册、邮箱验证与代理导出入口 |
| `web_app.py` | 经过登录、CSRF 和限速保护的 Web API |
| `templates/`, `static/` | 响应式仪表盘前端 |
| `proxyscrape_auth.py` | ProxyScrape 登录、注册与 Token 管理封装 |
| `启动注册.bat` | Windows 启动脚本 |
| `account/` | 本地账号与 Token 输出，不进入 Git |
| `node/` | 本地代理账号和节点输出，不进入 Git |

## 环境要求

- Python 3.10+
- Chromium/Chrome
- `turnstilePatch` 浏览器扩展（已随仓库提供，见项目内 `turnstilePatch/`，开箱即用）

安装 Python 依赖：

```bash
python -m pip install -r requirements.txt
```

## 本地隐私配置

项目不会在源码中保存邮箱 API Key、自有域名或本机绝对路径。支持 Cloudflare Temp Email、
自建云芯邮箱 API 和原有 YYDS Mail。

**方式一：配置文件（推荐，最省事）**

复制模板并填入你自己的 Key：

```bash
cp config.local.json.example config.local.json
```

然后编辑 `config.local.json`。自建云芯邮箱示例：

```json
{
  "mail_provider": "yunxin",
  "mail_api_base": "https://mail.example.com",
  "mail_api_key": "qm_你的密钥",
  "mail_type": "mail",
  "mail_suffix": "mail.com",
  "mail_domain": "",
  "captcha_provider": "2captcha",
  "captcha_api_key": "你的 2Captcha API Key",
  "captcha_api_base": "https://api.2captcha.com",
  "captcha_timeout": 180,
  "captcha_poll_interval": 5,
  "turnstile_extension_path": ""
}
```

配置字段：

| 字段 | 必需 | 说明 |
|------|------|------|
| `mail_provider` | 是 | `cfmail` 使用 Cloudflare Temp Email；`yunxin` 使用自建 HTTPS API；`yyds` 使用原接口 |
| `mail_api_base` | cfmail/yunxin 必需 | 邮箱服务地址 |
| `mail_api_key` | yunxin 必需 | `qm_` 开头的 API 密钥 |
| `mail_type` | 否 | `mail`、`cf` 或 `auto`；默认 `mail` |
| `mail_suffix` | 否 | `mail` 类型的后缀，例如 `mail.com` |
| `mail_domain` | 否 | 仅用于 `cf` 自有域名，不要填写 mail.com 后缀 |
| `yyds_api_key` | yyds 必需 | YYDS Mail API Key |
| `yyds_domain` | 否 | 已在 YYDS 验证的自有域名 |
| `captcha_provider` | 否 | API 服务可选 `2captcha`、`yescaptcha`、`capmonster`；`browser` 使用浏览器扩展 |
| `captcha_api_key` | API 模式必需 | 对应验证码服务商的 API Key，只保存在本地配置中 |
| `captcha_api_base` | 否 | 默认 `https://api.2captcha.com` |
| `captcha_timeout` | 否 | 单个 Turnstile 任务超时秒数，默认 180 |
| `captcha_poll_interval` | 否 | 查询结果间隔秒数，最小 5 秒 |
| `turnstile_extension_path` | 否 | 留空即用仓库自带的 `turnstilePatch/`；仅当想换成本机其它目录时才填 |

### Cloudflare Temp Email

兼容 Cloudflare Worker 版 Temp Email（例如 `/admin` 管理页面对应的服务），不需要把
Admin 密码或令牌写进 Nodes。创建邮箱使用公开的 `/api/new_address`，收信使用创建时返回的
邮箱 JWT：

```json
{
  "mail_provider": "cfmail",
  "mail_api_base": "https://temp.example.com",
  "mail_domain": "example.com",
  "mail_api_key": ""
}
```

`mail_domain` 应填写 Temp Email 的可用域名；`mail_suffix` 和 `mail_type` 对 `cfmail`
不生效。创建地址时要求服务端生成随机子域名，例如 `name@random.example.com`，并拒绝
回退到根域名地址；基础域名必须配置通配 MX，且需要列入 Temp Email 的
`randomSubdomainDomains`。若该服务启用了自己的 Turnstile 校验，需要先在服务端关闭或
另行扩展 token 配置。

收件接口返回 `raw` 原始邮件时，Nodes 会先解码 MIME（含 quoted-printable、Base64
和邮件声明的字符集），再从 HTML/纯文本正文提取验证码，不扫描附件，也不把验证码写入日志。
创建空邮箱并读取空收件箱只能验证接口连通；不能代替带验证码邮件的解析测试。

### 全量代理订阅

Resin 的 live-proxies 订阅导出每个有效账号的全部去重代理地址；账号有 100 个地址就导出
100 个，不再限制为 8 个。GPT、Clash 和 URI 导出的网关身份数也按实际导出节点数计算。
过期或余额低于阈值的账号仍会排除；节点数量不代表套餐并发或流量额度增加。
旧 `pool_slots_per_account` 设置已停用。`pool_expected_proxies_per_account` 默认 100，
仅用于估算补号数；`pool_target_slots` 是补池目标，不是订阅条数上限。

Dashboard 的「代理输出」支持按需选择文件格式：原始 HTTP URL、
`user:pass@host:port` 和 `host:port`。网关订阅还支持 HTTP、Resin 8970 SOCKS5、
Clash/Stash YAML 和 Shadowrocket URI；SOCKS5 输出使用 Resin 的独立网关身份，
不会把 HTTP 上游节点伪装成 SOCKS5。

### 复用已生成节点作为出口

在「代理输出 → 注册出口代理」勾选「使用已生成节点」，点击保存即可通过现有 Resin 池
发送 HTTP API 请求并加载注册浏览器，无需手动填代理地址或令牌。`proxy_use_pool` 默认关闭；
开启后优先于手动代理，关闭后恢复原有 `proxy_enabled/http_proxy/https_proxy` 配置，不覆盖
手动输入。代理启用时，Chromium 使用临时认证扩展向 Resin 提交凭据；认证或连接失败会终止
当前尝试，不会回退到 VPS 直连。代理关闭时 Chromium 保持默认网络行为。
手动模式只填写 HTTP 或 HTTPS 任一地址时，该地址会同时用于两种协议，避免另一类请求绕过代理。

Docker 部署通过 `NODES_RESIN_PROXY_URL=http://resin:8970` 指定内部网关；其他部署可在
服务端配置 `resin_internal_proxy_url`，默认 `http://127.0.0.1:8970`。地址不应含用户名、密码、
路径或查询参数；认证复用服务端已有 Resin 代理令牌，使用独立的 `Nodes.nodes-ops` 身份。
本机、`dashboard`、`resin` 和配置中的内部服务主机自动加入直连列表。
没有可导出节点或缺少认证时不能启用；运行中池不可用会报错，不会自动降级直连。

`config.local.json` 已被 `.gitignore` 排除，不会进入仓库。

**方式二：环境变量（会覆盖配置文件同名项）**

| 环境变量 | 必需 | 说明 |
|----------|------|------|
| `MAIL_PROVIDER` | 否 | `cfmail`、`yunxin` 或 `yyds` |
| `MAIL_API_BASE` | cfmail/yunxin 必需 | 邮箱服务地址 |
| `MAIL_API_KEY` | yunxin 必需 | `qm_` 开头的 API 密钥 |
| `MAIL_TYPE` | 否 | `mail`、`cf` 或 `auto` |
| `MAIL_SUFFIX` | 否 | mail.com 母号别名后缀 |
| `MAIL_DOMAIN` | 否 | CF 自有域名 |
| `YYDS_API_KEY` | yyds 必需 | YYDS Mail API Key |
| `YYDS_DOMAIN` | 否 | 已验证的自有域名；留空则由 YYDS 选择 |
| `CAPTCHA_PROVIDER` | 否 | `2captcha`、`yescaptcha`、`capmonster` 或 `browser` |
| `CAPTCHA_API_KEY` | API 模式必需 | 对应验证码服务商的 API Key |
| `CAPTCHA_API_BASE` | 否 | 2Captcha API 根地址 |
| `CAPTCHA_TIMEOUT` | 否 | 单任务超时秒数 |
| `CAPTCHA_POLL_INTERVAL` | 否 | 查询结果间隔秒数，最小 5 秒 |
| `TURNSTILE_EXTENSION_PATH` | 否 | 留空即用仓库自带扩展；仅覆盖为本机其它目录时才填 |
| `PYTHON_EXE` | 否 | `启动注册.bat` 使用的 Python；默认使用 PATH 中的 `python` |

PowerShell 当前窗口配置示例：

```powershell
$env:MAIL_PROVIDER = "yunxin"
$env:MAIL_API_BASE = "https://mail.example.com"
$env:MAIL_API_KEY = "qm_YOUR_API_KEY"
$env:MAIL_TYPE = "mail"
$env:MAIL_SUFFIX = "mail.com"
python .\proxyscrape_register.py
```

这些值只存在于当前 PowerShell 进程，不会写入仓库。

## 切换邮箱服务

使用原有 YYDS 时设置：

```json
{
  "mail_provider": "yyds",
  "yyds_api_key": "你的 YYDS API Key",
  "yyds_domain": ""
}
```

### 云芯 mail.com 后缀

使用 `/api/v1/suffixes` 返回的后缀：

```powershell
$env:MAIL_TYPE = "mail"
$env:MAIL_SUFFIX = "mail.com"
Remove-Item Env:MAIL_DOMAIN -ErrorAction SilentlyContinue
```

### 云芯 CF 自有域名

`mail_domain` 只用于 `/api/config` 返回的 CF 域名：

```powershell
$env:MAIL_TYPE = "cf"
$env:MAIL_SUFFIX = ""
$env:MAIL_DOMAIN = "mail.example.com"
```

`mail.example.com` 是占位符，需替换为云芯 `/api/config` 中实际启用的域名。切换配置后重新启动程序。

## 运行

PowerShell：

```powershell
python .\proxyscrape_register.py
```

或双击 `启动注册.bat`。如果 Python 不在 PATH 中，可先设置：

```powershell
$env:PYTHON_EXE = "D:\path\to\python.exe"
```

运行结果会写入：

- `account/accounts_*.jsonl`：邮箱、密码、访问 Token 和账户信息。
- `node/proxies_*.txt`：代理用户名、密码和节点地址。
- `proxyscrape_token.json`：`proxyscrape_auth.py` 的本地登录会话。

以上均包含敏感信息，已由 `.gitignore` 排除，禁止手动强制提交。

## 安全检查

上传或分享前建议执行：

```bash
git status --ignored
git grep -n -I -E "API_KEY|access_token|refresh_token|proxy_password"
```

公开的 ProxyScrape Turnstile sitekey 和 Google OAuth Client ID 来自网页前端，不是账户私钥；邮箱 API Key、登录 Token、邮箱账户和代理凭据必须始终保留在本地。

## 代理质检与库存（选择性上游合并）

本地部署选择性接入 [上游 f140ea8](https://github.com/lichao199208/Nodes/commit/f140ea8e142688b2a4ddfb77f545320d8740f152) 的代理质检、规则与库存历史功能，保留现有 cfmail、全量节点导出、Resin 网关和浏览器修复。

- **默认关闭质检**，不排除国家；可选目标探测使用通用 HTTPS URL。
- 「质检规则」可保存多个版本化规则并选择当前规则；试测最多 50 条 HTTP/HTTPS 代理，不改动库存。
- 「代理库存」区分合格、不合格和未检测，显示后台检查进度、节点延迟/国家、快照历史与规则操作记录。页面刷新不会启动探测。
- 检查会复用有效缓存，仅探测未测或过期节点。默认缓存 600 秒，过期后需再次检查。
- 新的合格订阅 `/nodes/api/export/qualified-proxies?token=...` 在质检开启时只输出当前规则下缓存有效的合格节点，关闭时输出全部有效节点。原始订阅和 Resin 网关导出保持原有行为。

完整部署说明见 [deploy/PUBLIC_VPS.md](deploy/PUBLIC_VPS.md)。


## 2026-10-08 验证码服务增量同步

选择性接入上游 2026-10-05 的 `8ea90ee`（YesCaptcha）、`5d69706`（CapMonster Cloud）、
`3ad0b66`（前端自动填写 API 地址）和 `760a37e`（后端纠正服务商地址），每个移植提交保留上游来源。
保留本地 cfmail、全量节点导出、Resin、任务诊断和质检实现，以及示例配置原有的 2Captcha 默认值。
此前的质检集成和本次同步均为选择性移植，不能将 Git 的 ahead/behind 数量直接理解为功能缺失数量。

| `captcha_provider` | `captcha_api_base` |
|---|---|
| `2captcha` | `https://api.2captcha.com` |
| `yescaptcha` | `https://api.yescaptcha.com` |
| `capmonster` | `https://api.capmonster.cloud` |
| `browser` | 不使用 API 地址 |

Web 控制台切换服务商时会自动填写对应地址；保存时会纠正旧页面提交的其他服务商官方地址，
自定义兼容地址仍可保留。API Key 需填写当前服务商的 Key，不会自动转换。
直接编辑配置文件或使用环境变量时，应同时设置服务商、API 地址和 Key。

安装 Python 依赖后，从仓库根目录运行新增回归检查。导入 Web 应用时会读取任务状态，
因此必须先隔离配置和运行数据目录：

```bash
nodes_test_dir=$(mktemp -d)
PYTHONDONTWRITEBYTECODE=1 NODES_DISABLE_POOL_LOOP=1 \
  NODES_CONFIG_FILE="$nodes_test_dir/config.local.json" \
  NODES_ACCOUNT_DIR="$nodes_test_dir/account" \
  NODES_NODE_DIR="$nodes_test_dir/node" \
  NODES_WEB_DATA_DIR="$nodes_test_dir/web-data" \
  python -m unittest -q test_captcha_settings
node --test tests/captcha-settings.test.cjs
```

以上命令使用临时目录及模拟响应，不进行实际注册或付费打码。
2026-10-08 已推送至 [个人 fork](https://github.com/GuDong2003/Nodes) 并部署到现有服务，
发布验证与回滚位置见 [公网部署说明](deploy/PUBLIC_VPS.md)。

## 按流量和节点阈值自动补号

在仪表盘的「Resin / GPT 池」中设置最低有效节点数、最低剩余总流量（GB）、检查间隔和每轮注册上限。
两个阈值均可单独使用，设为 0 表示不检查该项；启用时至少一个阈值大于 0。
同时设置两个阈值时，任一不足便触发补号，全部满足后不再启动新一轮注册。
默认关闭自动补号，默认检查间隔 30 分钟，每轮最多 5 个账号。
每轮上限限制目标账号数，沿用现有每个目标最多 3 次尝试的规则，实际尝试次数会记录在任务日志中。

每轮先同步已有账号的到期时间、流量及节点列表，然后按新数据判断。
总流量以 GB（10 亿字节）计，按符合导出条件的有效账号汇总，一个账号的配额只计一次；
节点数是这些账号的实际导出数量，不代表探测通过数。既有单账号最低可用流量规则仍生效
（默认 100 MiB）。同步失败或流量未知时暂停本轮补号并显示原因；已明确过期的账号不占容量。
节点缺口按每账号预估 100 个节点计算，受每轮上限约束；仅流量不足时先补 1 个账号，
下一轮根据实际新增流量重新判断，避免假设每个新账号必定提供固定配额。

「立即检查」使用已保存的设置。自动补号关闭时仅同步账号并检查容量；开启时可能启动注册。
已有注册任务或同步任务时不重复执行。页面显示上次/下次检查时间、同步结果和补号数量。
检查在服务器后台执行，关掉浏览器不影响运行；下一次检查时间以 0600 权限保存到
`web-data/pool_automation.json`，重启不立即补跑未完成的轮次。保持现有单 Gunicorn worker 部署。

配置字段为 `pool_auto_register`、`pool_target_slots`、`pool_target_bandwidth_gb`、
`pool_loop_seconds`、`pool_max_register_per_round`；控制台会校验取值并保留其他配置及密钥。
部署环境使用 `NODES_DISABLE_POOL_LOOP=0` 使后台可工作，是否自动注册仍由面板开关决定；
设置为 `1` 可停用后台检查，面板会明确提示并拒绝启用自动补号。

账号管理的到期时刻统一显示为北京时间，剩余天数/小时由绝对到期时间实时计算，
不再使用注册时缓存的剩余天数；到期后仍保留具体日期。后台同步不会恢复已删除账号，
也不会覆盖同步期间保存的编辑。新增回归模块为 `test_pool_automation`、
`test_automation_runner`、`test_pool_automation_api`；运行时沿用上面的临时目录隔离命令。
