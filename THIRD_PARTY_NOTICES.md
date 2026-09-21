# Nyalume 第三方软件与服务声明

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
