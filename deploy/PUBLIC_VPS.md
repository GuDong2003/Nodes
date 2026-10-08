# Nodes + Resin 公网部署

部署于风尚云 Ubuntu 22.04，域名 `ps.gudong226.com`。源码基线为
`2fa1ad8`，另包含当前工作区的公网部署修补。现有业务不属于本部署项目。

## 入口与数据

- `https://ps.gudong226.com/` 跳转到 Nodes `/nodes/`，登录名以部署配置为准。
- `https://ps.gudong226.com/ui/` 为 Resin 控制台，使用独立管理员令牌登录。
- 两边侧栏分别有「打开 Resin」「打开 Nodes Ops」普通链接，在新标签页打开，不携带凭据。
- HTTP/SOCKS5 代理入口为 `ps.gudong226.com:8970`，例如用户名 `Nodes.n01`，
  密码为独立代理令牌。此端口禁止管理 API 和 URL 反向代理。
- **8970 是普通 HTTP/SOCKS5，不是 TLS 代理入口**。HTTPS 目标的内容仍由
  CONNECT 隧道里的 TLS 保护，但到代理的认证信息没有额外的传输加密。
  不要在不可信网络上裸用；需要时通过 SSH/VPN 隧道访问。TLS 面板不能替代代理入口 TLS。
- 后端管理端口 `8891`、`2260` 只绑定 VPS 的 `127.0.0.1`。
- 项目目录 `/opt/nodes`；Nodes 运行数据在 `data/account`、`data/node`、`data/web`；
  Resin 数据在 `data/resin/{state,cache,log}`。
- `data/config/config.local.json` 是 Nodes 配置，整个目录挂载以支持原子保存。
- `data/config/resin.env` 是 Resin 的独立管理员/代理令牌。
- `data/config/access.json` 是私密登录交接文件。三者均为 `0600`；不要分享、提交或输出到日志。
- 本机交接副本位于 `deploy/private/access.json`，已被 Git 和 Docker 构建上下文排除。

## 初始业务状态

服务启动不等于上游节点已经可用。初始化不创建邮箱、不注册账号、不调用打码服务。
尚需在 Nodes 配置邮箱服务、打码方式，或导入用户已有的合法代理账号数据。
初始账户/代理池为空，不能据此宣称代理出口已验证。

Resin 已创建 `Nodes` 平台及订阅，每 2 分钟从 Docker 内网
`http://dashboard:8080/nodes/api/export/live-proxies` 拉取带令牌的订阅。
订阅按有效账号的实际代理地址全量导出，保留账号内去重和过期/余额过滤；
旧 `pool_slots_per_account` 配置不再限制导出（即使仍写着 8）。
`pool_expected_proxies_per_account` 默认 100，只用于估算补号数量；
`pool_target_slots` 只用于计算补池缺口，不会截断导出列表。
API 保留 `live_slots`、`concurrent_slots` 等旧字段名作为节点数量别名，不保证同等并发连接数。
只导入此平台的 `Nodes/` 节点，绝不能把 GPT/Clash 的 Resin 网关订阅反导回 Resin，
否则会形成循环代理。

自动补号默认关闭：`pool_auto_register=false`。阈值功能部署后使用 `NODES_DISABLE_POOL_LOOP=0`，
由仪表盘「Resin / GPT 池」的开关决定是否自动注册；每轮先同步再判断节点/总流量阈值。
`NODES_DISABLE_POOL_LOOP=1` 仍是环境停用开关，面板会提示后台不可用并拒绝启用自动补号。
仅保存设置或关闭开关不会启动注册，手动检查在开关关闭时只同步账号。
「代理输出」提供 `proxy_use_pool` 开关：勾选并保存后，HTTP API 请求自动经过内网
`http://resin:8970`，凭据从现有服务端配置读取；关闭时恢复原手动代理设置。
管理/订阅等内网请求自动绕过代理，Chromium 不随开关变更。部署不自动启用此开关。
代理池为空时不要将空的 Clash 配置用作隐私保护工具（上游空配置允许 DIRECT）。
上游 GPT/Ladder 导出在空池时会返回 503，并误写为 `resin_proxy_token_missing`；
这不一定是令牌问题。此部署已经单独验证代理令牌有效，需先加入可用节点再使用这些订阅。

## 运维命令

### 2026-10-04 选择性接入上游质检

功能来源为上游 `lichao199208/Nodes` 的 `f140ea8`；上游 `a43eba9` 的后续变更主要是文档。
本分支保留本地 cfmail、全量节点导出、Resin 网关格式、任务诊断和 Xvfb 重启修复。
未接入外部代理供应商拉取、自动删除过期账号或 Adobe 专用探测。

新增「质检规则」「代理库存」页面。默认质检关闭、不排除任何国家；可选目标探测默认
为通用 HTTPS 连通性检查。质检通过节点直接访问 IP 地理信息服务，浏览页面不会发起探测。
后台检查仅检测未测或缓存过期的节点，每批最多 50 条，默认 8 个并发；试测最多 50 条，
不读写缓存、库存或审计。默认缓存有效期 600 秒，缓存过期后显示为未检测。

`/nodes/api/export/qualified-proxies?token=...` 是独立的合格订阅：开启质检时只输出当前
规则下缓存仍有效的合格节点，关闭时透传全部有效节点；未检测不等于失败。
原 `/api/export/live-proxies` 和 Resin 的现有订阅地址保持不变。
更改规则或代理凭据会使相关旧缓存失效；规则保存递增版本，启用规则同时用于合格订阅。
只有显式后台检查完成或手动快照会新增历史记录，不会因刷新页面无限写入。

新增状态位于 `data/web`：`proxy_quality_cache.json`、`inventory.json`、
`inventory_history.jsonl`、`audit.jsonl`，均以 `0600` 原子保存，内容不包含代理认证信息。
规则存入原 `data/config/config.local.json`，并与其他设置和订阅令牌共用写入锁。
后台质检状态在单个 Gunicorn worker 中共享；部署保留 `--workers=1 --threads=8`。

更新前保留配置和数据备份，构建带时间戳的新镜像，确认无活动注册任务后仅替换 dashboard。
回滚使用上一镜像及原 Compose 配置即可；不要覆盖现有账号数据、凭据或 Resin/Caddy/Uboy 服务。
镜像构建需要 `.dockerignore` 放行 `proxy_quality.py`、`platform_store.py`、`quality_rules.py`。

质检回归测试：

```bash
PYTHONDONTWRITEBYTECODE=1 NODES_DISABLE_POOL_LOOP=1 uv run --isolated --no-project --python 3.12 --with requests --with 'Flask>=3.1,<4' python -m unittest -q test_quality_engine test_quality_api
node --test tests/quality-ui.test.cjs
```

### 2026-10-08 验证码服务增量发布

已将上游的 YesCaptcha、CapMonster Cloud、前端 API 地址自动填写和后端地址校正接入现有部署。
运行代码为 `36ad331`，已推送至 [个人 fork](https://github.com/GuDong2003/Nodes)；
本地 `origin` 指向个人 fork，`upstream` 指向 `lichao199208/Nodes`。

发布镜像内 145 项 Python 测试在禁网容器中通过，本地 20 项 JS 测试通过；
实际 Chromium/Xvfb 启动、公网桌面/手机界面、服务商切换地址、登录/CSRF、
质检/库存及各类订阅导出均验证通过。仍为 4 个账号、400 条有效节点，
Resin 订阅保持 400 条；验证码方式保留为 `browser`，质检和自动注册仍关闭。
配置、访问凭据和 Resin 配置文件的哈希保持不变，未执行实际注册或付费打码。

本次回滚备份位于 `/opt/nodes/backups/pre-captcha-20261008045721`（目录 `0700`，归档 `0600`），
包含源码、运行数据、Compose 和旧镜像引用。上一镜像 `nodes:public-quality-0d574d217176` 保留。
需要回滚时，将当前 Compose 的 dashboard image 恢复为该旧标签，并仅重建 dashboard；
备份中的 Compose 供对照，避免覆盖之后新增的配置。此次发布仅重建 Nodes 面板，
Resin 与 Caddy 的启动时间保持不变。

在 VPS `/opt/nodes` 下执行：

```bash
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml ps
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml logs --tail 80 dashboard
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml logs --tail 80 resin
```

不要把未脱敏的日志贴出；订阅 URL 自身也包含访问凭据。

Resin 固定为 `1.2.0` 并锁定镜像摘要。当前 Nodes 镜像为
`nodes:public-captcha-36ad331fdd10-20261008045249`，运行代码对应提交 `36ad331`。
更新时构建新的时间戳标签，将 `deploy/compose.public.yml` 的 dashboard image 改为该标签，
确认没有活动注册任务后仅重新部署 dashboard：

```bash
NODES_NEXT_IMAGE="nodes:public-$(date -u +%Y%m%d%H%M%S)"
docker build -t "$NODES_NEXT_IMAGE" .
# 将 dashboard image 设置为上面的新标签，再执行：
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml up -d --no-build --no-deps dashboard
```

初次初始化（幂等，拒绝覆盖不完整的已有配置）：

```bash
docker run --rm --network none --entrypoint python -v /opt/nodes:/deployment nodes:public-captcha-36ad331fdd10-20261008045249 /deployment/deploy/initialize.py --root /deployment --domain ps.gudong226.com
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml exec -T dashboard python deploy/configure_resin.py
```

`configure_resin.py` 会协调名称为 Nodes 的平台/订阅及 8970 端点；不要在手动修改这些
对象后无意执行它，因为它会恢复本部署策略。它不处理其他名称的订阅或平台。

## 验证

本地测试：

```bash
PYTHONDONTWRITEBYTECODE=1 NODES_DISABLE_POOL_LOOP=1 NODES_CONFIG_FILE=/tmp/nodes-unconfigured.json uv run --isolated --no-project --python 3.12 --with requests --with 'Flask>=3.1,<4' python -m unittest -q test_turnstile_solver test_web_app tests.test_mail_provider test_public_deployment test_deploy_resin test_proxy_reuse test_panel_links
node --test tests/panel-links.test.cjs
```

线上 smoke test 使用现有登录凭据，验证 TLS、登录、Cookie、CSRF、无变化配置保存、
Resin 内网订阅和公网代理鉴权，不启动注册任务：

```bash
docker cp deploy/verify_public.py nodes-dashboard-1:/tmp/verify_public.py
docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml exec -T dashboard python /tmp/verify_public.py
```

## Caddy 与回滚

2026-10-04 质检更新前的代码、Nodes 配置/账号/节点/任务数据及 Compose 已保存到
`/opt/nodes/backups/pre-quality-20261004154010`（目录 `0700`）。上一镜像
`nodes:public-20261004061910` 仍保留。当前登录凭据、Resin 令牌、邮箱设置与订阅配置
经过哈希及接口检查保持不变；线上仍为 3 个有效账号、300 条节点，质检默认关闭。
发布镜像内 138 项 Python 测试、本地 17 项 JS 测试及桌面/手机页面验证通过；
以不写入缓存的方式抽测 3 条现有代理，地理信息及 HTTPS 目标检查均通过。
Resin 与 Uboy 服务保持运行，未启动注册任务。

需要撤回本次面板更新时，恢复上述备份中的 `compose.public.yml`，再执行
`docker compose --project-directory /opt/nodes -p nodes -f deploy/compose.public.yml up -d --no-build --no-deps dashboard`。
这个回滚步骤只替换面板镜像，不覆盖现有业务数据。备份中的源码与数据压缩包供单独恢复使用。

Resin 仍使用官方二进制/前端资源，不需要重新编译。Caddy 为 `/ui/` 页面提供
`deploy/resin-nav/index.html` 入口副本，仅比官方入口多加载一个 `panel-links.js`，
用来在侧栏添加普通链接。JS/CSS 等资源和管理 API 仍请求原 Resin 服务。
入口文件部署在 `/opt/new-api/assets/nodes-resin-nav/index.html`，利用 Caddy 已有的只读
`/srv/assets` 挂载。升级 Resin 时需同步入口中的资源文件名；当前对应固定的 1.2.0 镜像。
不包含登录凭据，不读取 Cookie/localStorage；撤回该页面路由即恢复原 Resin 页面。

首次部署链接时，先复制入口文件再校验并 reload Caddy；仅写入本项目专用子目录：

```bash
install -D -m 644 /opt/nodes/deploy/resin-nav/index.html /opt/new-api/assets/nodes-resin-nav/index.html
```

现有 Caddy 容器 `new-api-caddy` 使用 host 网络。仅在
`/opt/new-api/Caddyfile` 末尾新增 `deploy/ps.Caddyfile` 中的站点。
原文件备份为 `/opt/nodes/backups/Caddyfile.pre-nodes-20260922`。
原有 `api`、`cpa`、`sub`、`t2i` 站点配置保留，配置通过验证后只 reload、不 restart。

若未来回滚，先比较当前 Caddyfile 与备份，保留部署之后新增的其他业务配置，
再仅移除 ps 站点并 validate/reload；不要盲目用旧备份覆盖后续变更。
停用 Nodes/Resin 使用本项目的 `docker compose ... stop`，不要删除 data 目录或卷。

变更前基线：`api` 根路径 200、`cpa` 根路径 200、`sub` 根路径 502、`t2i` 根路径 404。
其中 sub 的既有 502 不在此次修复范围内。
