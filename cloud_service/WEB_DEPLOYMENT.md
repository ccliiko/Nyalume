# Nyalume 网页小窝：部署与更新

独立入口：公开注册、自带 OpenAI 兼容 API Key 聊天、按账号保存的聊天/陪伴记忆、每日卡片与收藏、互动奇幻冒险、浏览器本地壁纸和 PMX/VMD 展示。它不加载桌面 Agent，不提供文件操作、代码执行、网页自动化、后台提醒或桌面控制。原有 `python server.py` 仍是本地桌面配套网页，**不要将该入口暴露到公网**。

## 本机 Docker 预览

Windows 可直接运行 `powershell -ExecutionPolicy Bypass -File tools/start-web.ps1`。脚本定位 Docker Desktop CLI；首次自动生成独立随机密钥到 `.env.web`，已有配置不会覆盖。`-Stop` 仅停止服务，不删除数据。请先启动 Docker Desktop。

复制 `.env.web.example` 为 `.env.web`。分别生成两个随机值填入 `POSTGRES_PASSWORD` 和 `NYALUME_TOKEN_SECRET`：

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

运行两次，使用不同值。数据库密码使用 hex 避免 URI 转义问题。此文件已被 Git 忽略，不要复用桌面 `.env`。

本地保持 `SITE_ADDRESS=http://localhost`、`HTTP_PORT=8088`、`HTTPS_PORT=8443`，在项目根目录运行：

```sh
docker compose --env-file .env.web -f compose.web.yaml up -d --build --wait
```

打开 http://localhost:8088 。API 以非 root 用户运行、只读文件系统，数据库端口不对宿主机公开。

## 阿里云 Linux

准备 Docker Engine 与 Compose v2，域名 DNS 指向服务器。将 `.env.web` 的 `SITE_ADDRESS` 改为正式域名（如 `chat.example.com`），端口改成 `HTTP_PORT=80`、`HTTPS_PORT=443`。开放 TCP 80/443，Caddy 会申请并续期 HTTPS 证书。不要开放 API 8080 或数据库 5432。

2 核 2G 使用一个 API worker，最多同时 4 路模型请求，每账号同时 1 路。不在服务器运行模型推理。API 信任转发地址是因为只有 Caddy 能从外部进入；不要直接将 API 端口公开。默认容器内存上限合计 1152 MiB，不代表常驻用量，需按真实负载观察。

构建只发送 `.dockerignore` 允许的云服务、渲染器和依赖清单；个人模型、动作、设计素材、密钥、桌面数据库不进入镜像。不要上传整个个人工作目录。部署前应确定域名，避免之后迁移浏览器存储。

## 用户 Key、聊天与记忆

登录/注册可选择「记住登录」，现有账号也能在账号设置开启。勾选后登录凭证存当前浏览器 localStorage，最长沿用本次登录的 30 天有效期；不勾选只存 sessionStorage。不会存密码；主动退出、凭证失效或改密后需要重新登录。自动登录与「记住 Key」分开控制。

注册后使用自己的 Key，无平台赠送额度。Key 默认只在页面内存；勾选“记住”后明文存放当前浏览器、按账号命名的 localStorage，退出账号时删除。关闭页面不等于退出，共用设备不要勾选。请求通过 HTTPS 经后端转发给所选服务商，服务端不保存 Key，不回显请求参数或服务商错误正文。

服务端只接受 `NYALUME_LLM_BASES` 列出的**完整 HTTPS base URL**。管理员审核后可加入其他 OpenAI 兼容服务商；拒绝内网/保留 IP，不跟随重定向，不使用环境代理。不要添加不可信或 DNS 可由用户控制的域名。修改配置后用 Compose `up -d` 重建容器配置。

支持 Chat Completions SSE（需发送 `[DONE]`）。请求没有 tools/functions 定义，不执行模型回复中的命令。最近 20 条历史（合计最多 48000 字）与用户编辑的陪伴记忆一起发给服务商。冒险另带世界设定、累积摘要与背包；完整历史分页保存在账号中，不随上下文裁剪删除。网页以纯文本显示回复；异常或中断的半轮回复不写入历史。停止时若服务端刚好已完成，刷新可确认是否保存。

默认限制：用户消息 16000 字、记忆 12000 字；每账号 100 个会话、共 20000 条消息，每会话 2000 条消息，聊天正文总存储预算 128 MiB（预留下一轮回复空间）。一次对话计用户与回复两条。请求体 256 KiB；认证接口每 IP 每分钟 12 次，其余 API 每 IP 每分钟 180 次。单 worker 内存限流在重启后清零，适合当前小规模部署。

公开网页版注册必须验证邮箱；未配置 SMTP 时不能注册。配置 `SMTP_HOST`、`SMTP_FROM`、`SMTP_USER`、`SMTP_PASSWORD`、`SMTP_PORT`；网易邮箱可用 `smtp.163.com`、465、`SMTP_USE_SSL=1`（授权码填密码项）。仅在服务器 `.env.web` 配置，不写入源码或前端。

日志出现 `SMTPAuthenticationError smtp_code=535` 表示发信邮箱认证被拒绝，此时接口返回 503 并提示管理员检查授权，重试不能修复认证配置。网易邮箱请确认 SMTP 服务已开启，在“设置 → POP3/SMTP/IMAP”新增客户端授权密码并更新服务器 `.env.web` 的 `SMTP_PASSWORD`，再运行 `docker compose --env-file .env.web -f compose.web.yaml up -d --no-deps --no-build --wait api` 使配置生效。不要使用邮箱登录密码。发送失败不会保存新验证码，也不会使之前仍有效的验证码失效。

验证码 10 分钟有效、最多输错 5 次、60 秒后可重发，每邮箱每日最多 10 封、全站默认 200 封（`SMTP_DAILY_LIMIT`）。数据库仅保存验证码摘要。注册、验证旧账号、找回密码的验证码用途隔离。重设或修改密码会退出所有设备；用户名可在账号设置修改，邮箱暂不支持修改。旧账号仍可登录并补验证，完成后继续使用原记录。

以上容量可通过 `.env.web.example` 中的 `WEB_*` 配置调整，再运行 Compose `up -d`。维持 API 单 worker；若扩大用户规模，需先改用共享限流与并发控制。

## 今日卡片、壁纸与互动冒险

今日卡片每日北京时间 0 点刷新，每账号每天固定一张，抽取不调用模型；收藏按账号保存。卡面与文案复用程序素材。自定义壁纸支持 PNG/JPEG/WebP，最多 10 MB，保存前缩至最长边 1920；只存当前浏览器 IndexedDB，可调透明度或恢复默认，同站点不同账号共享本机壁纸。

奇幻冒险创建独立会话，支持输入世界设定和最多 4000 字的同行角色人设；已有冒险可通过「角色人设」修改，按账号及会话隔离保存。留空使用默认 Nyalume，修改影响后续表演，已发生的情节沿用原存档。默认提示词强调个性化对话、动作、情绪与少量角色内心独白；冒险不再套用今日卡片的日常聊天语气。程序端的默认冒险提示词也同步调整。每轮通常 450–900 字，交代场景、线索与上一行动的结果，停在一个关键决定上；三个选项在对应回复下方显示完整的第一人称回答，点击直接发送并收起；也可在输入框自由写行动、对话或追问。摘要、背包和正文在完成一轮后一起保存，冒险正文校验成功后完整展示；无效存档或中断不推进。失败时不自动回填，使用「恢复上次输入」取回本页暂存的文字。DeepSeek 冒险使用官方 JSON Output，将正文与选项字段分开；若服务商正常结束却只返回空白，自动关闭强制 JSON 模式并额外重试一次（使用同一用户 Key，最多两次请求），页面显示重试状态。连续空白、截断或无效存档不会写入消息；其他服务商使用相同 JSON 提示格式，是否稳定支持取决于所选模型。没有文件、代码或浏览器工具。选项与文字质量依赖用户选择的模型。

程序端沿用原世界底稿、固定设定和账本，生成单次互动后等待用户；待回答时不自动推进。新导出的手记以「回合」命名，旧章节文件保留。剧情查证每回合最多 6 次模型请求、4 分钟，避免一轮无限准备；初次世界底稿仍沿用原独立准备流程。

新用户默认使用千问兼容地址 `https://dashscope.aliyuncs.com/compatible-mode/v1` 与模型 `qwen3.7-plus`；Key 默认为空。DeepSeek 请求显式关闭 thinking，页面展示服务商实际返回的模型名称；其他 OpenAI 兼容服务商不附带此设置。

## 3D 模型与本地保存

- 选择含 PMX 与配套贴图的文件夹；多个 PMX 会出现在选择列表。VMD 支持多选导入。
- 单次最多 2000 个文件、合计 256 MiB，单个模型资源 128 MiB，单个动作 32 MiB。
- 素材只保存在 IndexedDB，不上传、不随账号同步；同一浏览器同一站点的账号共享该本机素材库。退出账号不删除模型。
- 视角/物理开关按模型保存，默认关闭物理减轻性能负担。VMD 骨骼与模型不匹配时不保证效果，相机/灯光动作不属于角色动作。
- 中文、空格、子目录、反斜杠贴图路径映射到本地 Blob URL。外部 URL 不发出请求，缺贴图会提示并使用占位纹理。
- 浏览器清理、配额、隐私模式可能影响保存，请保留原始文件。服务端升级不会主动清除本地模型；更换域名、协议、端口属于不同 origin，需要重新导入。
- 首版验证 Chrome/Edge 桌面浏览器；手机支持触控/布局适配，但目录选择与大型 PMX 性能取决于浏览器/设备。暂不支持 ZIP、VRM 或 Live2D。

## 更新

### 公网白屏与手机布局

先从服务器之外直连域名检查响应。若 HTTP 返回 `403`、`Server: Beaver` 和 `Non-compliance ICP Filing`，或 HTTPS 在握手时被重置，而服务器内部仍正常，需在[阿里云备案控制台](https://beian.aliyun.com/)办理首次备案或补全接入备案。页面代码更新无法解除这层接入拦截；备案状态以控制台审核结果为准。参见[阿里云公网访问排查](https://help.aliyun.com/zh/ecs/support/what-do-i-do-if-i-cannot-access-a-website-hosted-on-an-instance)。

手机和平板默认显示聊天，3D 角色通过“展开角色”打开，并可“返回聊天”。页面高度跟随可视区域变化，模型与壁纸存储恢复不会阻塞聊天初始化。

使用现有 Playwright 环境运行 `python tools/check_web_mobile.py` 检查本地静态页面，或加 `--url https://nyalume.chat/` 检查线上。`--browser webkit` 使用已安装的 WebKit 引擎。检查覆盖小屏、平板、横屏、可视区域缩小，以及本地模型存储挂起或不可用；不会登录账号或调用收费模型。

保持服务器 `.env.web`、Compose 项目名和数据卷不变。

上传干净的新版源码后运行：

```sh
bash tools/update-web.sh build
```

脚本备份现有数据库到 `web-backups`，构建镜像、启动、等待健康检查。备份目录权限 700、文件权限 600；失败时返回非零，不删除卷。更新可能短暂中断聊天。

更推荐在本机/CI 构建版本镜像：

```sh
docker build -f cloud_service/Dockerfile -t YOUR_REGISTRY/nyalume-web:0.4.1 .
docker push YOUR_REGISTRY/nyalume-web:0.4.1
```

服务器将 `NYALUME_IMAGE` 改为该版本，再运行：

```sh
bash tools/update-web.sh pull
```

后续可在正式发布版本后由 CI 调用同一脚本。此次未连接镜像仓库、服务器或配置远程自动部署。避免自动追踪 `latest` 并跳过备份。

不能访问仓库时，也可以本机 `docker save -o nyalume-web.tar YOUR_IMAGE:VERSION`，上传后 `docker load -i nyalume-web.tar`；手动备份数据库后执行 `docker compose --env-file .env.web -f compose.web.yaml up -d --no-build --wait`。

程序回退：保留旧镜像标签，修改 `NYALUME_IMAGE` 后执行 `up -d --no-build --wait`。当前新增数据库表可重复初始化、不删除旧数据；未来若迁移不向后兼容，需要恢复配套数据库备份，不能只切镜像。

## 备份与恢复（Linux）

```sh
mkdir -p web-backups
chmod 700 web-backups
umask 077
docker compose --env-file .env.web -f compose.web.yaml exec -T postgres pg_dump -U nyalume -d nyalume -Fc > web-backups/manual.dump
```

恢复会覆盖云端账号与聊天，先确认备份和目标实例，再执行：

```sh
docker compose --env-file .env.web -f compose.web.yaml stop api
docker compose --env-file .env.web -f compose.web.yaml exec -T postgres pg_restore -U nyalume -d nyalume --clean --if-exists --no-owner < web-backups/manual.dump
docker compose --env-file .env.web -f compose.web.yaml up -d --wait
```

不要执行 `docker compose down -v`，会删除数据库卷。定期将备份复制到服务器之外；浏览器模型不在服务器备份里。

## 检查

```sh
python -m pytest tests/test_cloud_security.py tests/test_web_boundaries.py -q
node tests/test_pet_assets.mjs
docker compose --env-file .env.web -f compose.web.yaml exec -T api python < tools/check_web.py
```

PowerShell 的最后一条写成：

```powershell
Get-Content tools/check_web.py -Raw -Encoding utf8 | docker compose --env-file .env.web -f compose.web.yaml exec -T api python -
```

集成检查使用真实 PostgreSQL 和随机账号，结束时仅删除它创建的账号；模型服务模拟，不使用真实 Key、不产生费用。验证邮箱注册、验证码用途/重用保护、账号隔离、流式聊天、Key 不回显/持久化、工具入口关闭、每日卡片、冒险存档与错误回滚、分页、改名、改密和退出。邮件发送与模型服务均模拟，不向测试邮箱发信。

安装 Playwright 且本机有 Chrome 时，可对本地 Docker 预览做 UI 检查：

```sh
python tools/check_web_browser.py --model "你的模型文件夹"
```

此检查只访问 localhost:8088，模拟聊天响应，不消耗 API 额度；使用临时账号并在结束时删除该账号，截图保存在忽略的 `output/web-qa` 中。只用个人素材本地验收，不把模型或截图打包发布。
