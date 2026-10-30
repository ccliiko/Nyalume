# Nyalume

**v0.5.3** · 面向 Windows 的 AI 助手与二次元桌宠

Nyalume 将 AI 对话、桌面陪伴与日常工具放在一起。你可以通过桌宠、网页或命令行与它交流，使用长期记忆、文件处理、资料检索、陪伴专注等功能。

## 功能

独立的 **网页小窝预览版** 支持邮箱验证注册、自带阿里云百炼中国内地（北京地域）的千问 API Key 聊天、账号隔离的陪伴记忆，调用额度由自己的百炼账号承担，
以及浏览器本地 PMX/VMD 导入、互动与保存。公网入口不提供 Agent 工具；桌面入口维持原有能力。
Docker 部署、更新和备份见 [网页部署说明](cloud_service/WEB_DEPLOYMENT.md)。

- **桌宠陪伴**：2D 角色互动、表情与皮肤；3D 桌宠支持用户自备的 PMX、VRM 模型。PMX 可播放 VMD 动作；VRM 使用自然站姿、眨眼和互动姿态。
- **换装与放大镜**：内置 Eku 可切换私服与制服、独立设置衣物穿脱并记住选择；3D 桌宠提供可框选部位、平滑跟随的 3× 放大镜。
- **触摸与回应**：连续摸头带折耳、回弹和笑眼，柔和捏脸支持左右独立恢复；视线和情绪随互动变化，操作时及时让出控制。
- **网页与命令行**：多轮流式对话、多会话管理，可切换 Nyalume 或标准助手人设。
- **聊天交互**：生成中可编辑并重新回复；队列支持插入、编辑和删除。首字符 `/` 打开快捷菜单，工作模式可选择目标模式。
- **记忆与陪伴**：长期便签、对话摘要、每日卡片、小窝故事和专注记录。
- **记忆卡片**：按用户、主题和领域管理记忆，查看更新时间、摘要、关键词与来源，支持编辑和跨对话召回。
- **实用工具**：安全计算、网页搜索、项目文件操作，以及 PDF、Word、Excel、PPT 处理。
- **桌面模型选择**：支持 DeepSeek、OpenAI、OpenRouter、硅基流动及其他 OpenAI 兼容服务；网页小窝仅支持上述百炼千问入口。

## 快速开始

需要 Windows、Python 3.11 或更高版本，以及一个 OpenAI 兼容模型服务的 API Key。

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中配置服务商和 API Key，然后启动所需入口：

```powershell
python server.py   # 网页版：http://127.0.0.1:8000
python cli.py      # 命令行
python pet.py      # 2D 桌宠
```

3D 桌宠需要额外安装 Three.js：

```powershell
cd nyalume\frontends\pet\pet3d
npm install
cd ..\..\..\..
python -m nyalume.frontends.pet.pet3d.pet3d_win --model <模型目录或.pmx或.vrm> [--vmd <动作目录>]
```

源码目录保留三个双击入口：`启动Eku桌宠.cmd`（正式 Unity Eku）、`启动桌宠3D-管理员.cmd`（需要管理员权限的游戏内互动）、`start_pet.bat`（2D 桌宠）。材质候选、组合预览及重复的 Unity/MToon 入口已移除；开发记录中提到的旧入口仅代表当时状态。

Windows 便携版默认附带内置 Unity Eku 与野餐式 MikuQ；其他 3D 模型、动作和 2D 自定义皮肤由用户自行准备。源码运行 Unity Eku 需要本地播放器构建。模型与动作使用说明见[3D 桌宠指南](docs/3D桌宠说明.md)，配置字段见[模型配置说明](docs/model-profiles.md)。

## Windows 便携版

下载 [GitHub Releases](https://github.com/ccliiko/Nyalume/releases) 的 Windows x64 压缩包并解压完整文件夹。新构建提供 `Nyalume3D.exe`（默认 Unity Eku）、`Nyalume2D.exe`（原有 2D 桌宠）及 `启动3D桌宠-管理员.cmd`（游戏内互动）；旧版仍使用 `Nyalume.exe`。两个新入口共用运行目录，不能单独移动。首次启动时填写模型服务商、API Key 和模型。完整更新见 [CHANGELOG.md](CHANGELOG.md)。

聊天窗口侧栏的「版本更新」可直达更新页，也可从「设置与技能 → 检查更新」进入，查询 GitHub 最新正式版。Windows 便携版发现新版后可「下载并安装最新版」：下载时保持桌宠运行，校验完成后按提示退出桌宠、关闭聊天窗口，更新器会自动替换程序并重新启动。配置、记忆、自备模型与动作保留，替换失败会尝试恢复原程序；程序备份留在安装目录旁的 `.nyalume-update-*` 文件夹。源码运行支持检查版本，通过发行页或 Git 手动更新。

更新页可保存更新源：默认「自动加速」，版本查询先尝试 GitHub、失败后使用 [GH-Proxy](https://gh-proxy.com/docs/github-accelerator)，安装包优先加速、连接或校验失败后回退 GitHub；也可选「GitHub 直连」或填写自定义 HTTPS 镜像前缀。自定义源需同时支持 `前缀 + 完整 GitHub API URL` 与 `前缀 + 完整发行包 URL`。第三方加速只接收公开版本和下载请求，不转发模型 API Key；SHA-256 用于核对文件与发行信息的一致性。源设置保存在本机 `update-settings.json`，升级时保留。清华镜像只同步其收录的项目，目前未收录 Nyalume，不能作为通用下载前缀。

## 数据与许可

当前版本为本地单用户应用。对话、记忆、配置和文件保存在运行设备上；云服务组件不参与桌面版启动。请勿将包含个人数据或 API Key 的运行目录分享给他人。

Nyalume 为源码可见的商业软件，许可条款见 [LICENSE](LICENSE) 和 [EULA](EULA.md)。隐私处理见 [PRIVACY.md](PRIVACY.md)，第三方依赖及素材使用要求见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。公开源码仓库不附带个人模型与动作；Windows 便携版默认提供的 Eku、MikuQ 及其说明以随包素材声明和借物表为准，其他 PMX/VRM/VMD 素材由用户自行准备。
