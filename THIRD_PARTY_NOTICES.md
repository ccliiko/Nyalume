# Nyalume 第三方组件、服务与素材声明

更新日期：2026 年 9 月 21 日

Nyalume 自有代码受根目录 [LICENSE](LICENSE) 约束。下列第三方组件保留各自权利，使用和再分发以其许可证原文为准。本清单覆盖 `requirements.txt`、`cloud_service/requirements.txt` 及 Windows 打包工具中直接声明的依赖；发布者应在每次构建后按实际锁定版本复核传递依赖。

| 组件 | 用途 | 许可证 | 上游项目 |
| --- | --- | --- | --- |
| OpenAI Python | OpenAI 兼容模型客户端 | Apache-2.0 | <https://github.com/openai/openai-python> |
| FastAPI | 本地与云端 HTTP API | MIT | <https://github.com/fastapi/fastapi> |
| Uvicorn | ASGI 服务器 | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| python-dotenv | 本地配置加载 | BSD-3-Clause | <https://github.com/theskumar/python-dotenv> |
| Pillow | 图片处理 | MIT-CMU | <https://github.com/python-pillow/Pillow> |
| NumPy | 数组与图像计算 | BSD-3-Clause 及随发行包提供的第三方许可证 | <https://github.com/numpy/numpy> |
| pystray | 系统托盘 | LGPL-3.0 | <https://github.com/moses-palmer/pystray> |
| pywebview | 桌面 WebView 窗口 | BSD-3-Clause | <https://github.com/r0x0r/pywebview> |
| python-multipart | 文件上传解析 | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| pypdf | PDF 文本读取与页面编辑 | BSD-3-Clause | <https://github.com/py-pdf/pypdf> |
| pypdfium2 / PDFium | 扫描 PDF 页面渲染 | Apache-2.0 或 BSD-3-Clause；PDFium 及其传递组件许可证随 wheel 和发行包提供 | <https://github.com/pypdfium2-team/pypdfium2> |
| python-docx | Word 文档读取与编辑 | MIT | <https://github.com/python-openxml/python-docx> |
| openpyxl | Excel 文档读取与编辑 | MIT | <https://foss.heptapod.net/openpyxl/openpyxl> |
| python-pptx | PowerPoint 文档读取与编辑 | MIT | <https://github.com/scanny/python-pptx> |
| pywin32 | Windows 系统接口 | PSF-2.0 | <https://github.com/mhammond/pywin32> |
| tkinterdnd2 | 文件拖放 | MIT | <https://github.com/Eliav2/tkinterdnd2> |
| Playwright Python | 网页自动化 | Apache-2.0 | <https://github.com/microsoft/playwright-python> |
| psycopg / psycopg-binary | 可选 PostgreSQL 云同步 | LGPL-3.0-only | <https://github.com/psycopg/psycopg> |
| PyInstaller | Windows 打包工具 | GPL-2.0-or-later，含允许分发非自由程序的 bootloader 例外 | <https://github.com/pyinstaller/pyinstaller> |
| Three.js 0.171.0 | 3D 模型、动画与透明画面渲染 | MIT；许可证副本随 3D 运行文件提供 | <https://github.com/mrdoob/three.js> |
| ammo.js / Bullet Physics | 3D 布料与骨骼物理 | ammo.js 文件标注 zlib；适用的上游许可应随发行文件保留 | <https://github.com/kripken/ammo.js> |

常见传递依赖包括 Starlette、Pydantic、AnyIO、HTTPX、HTTP Core、h11、Jiter、Certifi、Click、Typing Extensions、Websockets 等；它们不会因此变为 Nyalume 自有代码。准确版本以具体发行包的构建环境为准。

## 外部服务和产品

Nyalume 可以连接 DeepSeek、OpenAI、OpenRouter、硅基流动、自定义 OpenAI 兼容 API、Bing 搜索以及普通网站，也可以读取 Wallpaper Engine 的本地状态。这些服务或产品不随 Nyalume 授权，其名称和商标归各自权利人所有。用户需要自行接受相应条款并承担费用。

## 模型和生成素材

图片生成模型、LoRA 和其输出不属于 Python 依赖清单。它们的来源、许可状态和发行结论记录在 [ASSET_PROVENANCE.md](ASSET_PROVENANCE.md)。未经明确记录为“允许商业发行”的素材不得进入付费构建。

> 该记录只随源码仓库提供（用于内部合规复核），**不进入发行包**。发行包里与素材有关的边界见下一节。

## 3D 桌宠素材与展示声明

Nyalume 的程序代码、第三方运行库、用户自行导入的 PMX 模型和 VMD 动作，分别适用各自的权利条款。获得程序使用权，不等于获得模型、动作、角色形象或音乐的再分发与商业使用权。署名和本声明也不能替代权利人的授权。

### 本版发行边界

- 官方源码和可公开分发的程序构建不附带第三方 PMX 模型、商业用途受限的 VMD 动作或第三方音乐。3D 桌宠自带的 `idle.vmd` 是项目自制的自然站姿动作。
- 本地 `-PersonalAssets` 构建可以包含本机原有的 2D 角色帧，供制作者自行测试；该构建不作为公开、付费或宣传用发行包。`user_pets/` 下其他自行导入的素材也不会自动进入官方分发范围。
- 用户自行取得并导入模型或动作时，应保留原始说明文件，分别核对使用、改造、录屏、再分发、商业展示和署名条件。不同作者的条款可能互相不同。

### 已知限制示例

以下内容依据素材随附说明记录，仅用于说明为什么本版不统一打包第三方素材；实际使用以原作者当前完整条款为准。

| 素材 | 随附说明中的关键限制 | 本版处理 |
| --- | --- | --- |
| 锁暝 PMX（作者 1010浣） | 禁止二次配布；禁止商业使用和涉及金钱交易的使用 | 不进入仓库或公开包 |
| `IRIS OUT.vmd`（动作：Pronxy_迫奈熏） | 允许免费渠道二次配布并要求署名；禁止商业用途 | 不进入可能用于商业推广的整合包 |
| `Motion_だいあるのーと_YYB式初音ミク.vmd`（动作：若梦Romy） | 禁止动作数据二次配布；限制为视频用途且禁止商业使用，需署名 | 不进入仓库或发行包 |

其他游戏角色模型和动作也须逐项核对作者及原作品权利方的要求。素材可免费下载、允许个人使用，均不自动表示可以打进 EXE、上传 GitHub，或出现在产品广告中。

上表这几条**都不禁止拍摄和发布演示视频**：`限制为视频用途`就是"拍视频可以"的意思，常见附加条件只是**署名**和**禁止商业使用**。锁暝禁的是二次配布与商用；`IRIS OUT.vmd` 反而允许在免费渠道发布（要求署名）；`Motion_だいあるのーと` 明确限定为视频用途（要求署名、禁商用）。所以：免费发布、不接商单、不带购买引导的桌面演示视频，按各自条款署名即可；要拿视频去卖软件、带货或投广告，才需要另行取得商业授权。

### 个人版里随包附带的模型与动作

`-personal-models` 那类本机构建会把本机 `模型\` 目录下的 PMX 模型与 VMD 动作一起打进包里。这些素材**由制作者本人从官方渠道自行下载**，仅用于本机测试和个人使用，**与 Nyalume 程序及其开发者无关**：程序只是本地播放器，加载它们不代表取得任何授权、合作或背书，也不主张对模型、动作、角色形象或音乐的任何权利。

随包附带不等于可以再分发。把这类构建转给他人、上传网盘或任何公开平台，都属于二次配布；部分素材明确禁止二次配布或商用（见上表）。要**再配布这份带素材的构建**，请改用不带素材的构建，由使用者自行导入其合法取得的模型与动作。（这与拍视频是两件事：发布自己录的演示视频不属于二次配布，见下一节。）

### 截图、演示视频和宣发

分两类，别混：

- **个人演示 / 免费发布的视频与截图**（包括桌宠演示、录屏分享、不含购买引导的展示）：多数素材作者是允许的，甚至专门限定在"视频用途"。按各自条款执行即可——通常要**署名**（在简介或画面角落里写上模型作者、动作作者和原作品名），并**不得用于商业用途**。标注"非商用""仅个人使用"的素材同样按这个尺度执行：个人分享可行，商业化不行。
- **商业宣发**（卖软件或付费产品页、带货、付费推广、赞助内容、商店页、用角色形象给产品导流）：需要覆盖**商业使用与宣传展示**的明确授权（必要时含改编与署名方式）才能用；"仅视频用途""禁止商业使用"在这种情况下都不适用。授权范围不清楚，就改用自有或已获授权的素材。

署名写法（放简介，画面里可再叠一行小字）：

```text
模型：<模型名> by <作者名>（<发布页>）
动作：<动作名> by <作者名>（<发布页>）
桌面宠物程序：Nyalume
```

Nyalume 与相关游戏、角色或素材作者没有官方合作或背书关系。第三方名称只用于说明兼容性和来源。

### 程序与服务风险

3D 桌宠通过本机 WebView2、鼠标钩子和本地回环接口运行。管理员模式仅用于部分高完整性游戏的输入兼容，请在理解权限影响后自行启用。接入模型 API 时，提示词、主动搭话所需的媒体信息和用户主动提交的内容可能发往所选服务商；费用与数据处理取决于该服务商的条款。详见 [PRIVACY.md](PRIVACY.md) 和 [EULA.md](EULA.md)。软件按现状提供；重要文件请自行备份。
