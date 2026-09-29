# Nyalume

**V0.4.1** · 面向 Windows 的 AI 助手与二次元桌宠

Nyalume 将 AI 对话、桌面陪伴与日常工具放在一起。你可以通过桌宠、网页或命令行与它交流，使用长期记忆、文件处理、资料检索、定时提醒和陪伴专注等功能。

## 功能

- **桌宠陪伴**：2D 角色互动、表情与皮肤；3D 桌宠支持用户自备的 PMX 模型和 VMD 动作。
- **网页与命令行**：多轮流式对话、多会话管理，可切换 Nyalume 或标准助手人设。
- **记忆与陪伴**：长期便签、对话摘要、每日卡片、小窝故事和专注记录。
- **实用工具**：安全计算、网页搜索、定时提醒、项目文件操作，以及 PDF、Word、Excel、PPT 处理。
- **模型选择**：支持 DeepSeek、OpenAI、OpenRouter、硅基流动及其他 OpenAI 兼容服务。

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
python -m nyalume.frontends.pet.pet3d.pet3d_win --model <模型目录或.pmx> [--vmd <动作目录>]
```

3D 模型、动作和 2D 自定义皮肤由用户自行准备。模型与动作使用说明见[3D 桌宠指南](docs/3D桌宠说明.md)，配置字段见[模型配置说明](docs/model-profiles.md)。

## Windows 便携版

下载 [GitHub Releases](https://github.com/ccliiko/Nyalume/releases) 的 Windows x64 压缩包，解压完整文件夹后运行 `Nyalume.exe`。V0.4.1 已恢复原有的 2D 角色、待机动作和表情。首次启动时填写模型服务商、API Key 和模型。

## 数据与许可

当前版本为本地单用户应用。对话、记忆、配置和文件保存在运行设备上；云服务组件不参与桌面版启动。请勿将包含个人数据或 API Key 的运行目录分享给他人。

Nyalume 为源码可见的商业软件，许可条款见 [LICENSE](LICENSE) 和 [EULA](EULA.md)。隐私处理见 [PRIVACY.md](PRIVACY.md)，第三方依赖及素材使用要求见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。公开仓库和发行包不附带第三方 PMX/VMD 模型或动作；使用素材前请确认其授权范围。
