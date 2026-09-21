# Nyalume：会干活的本地 AI 桌宠

当前源码版本：**0.2.0**。

Nyalume 是面向 Windows 的本地优先个人 AI Agent：以桌宠、网页和命令行三种入口
提供多轮对话、长期记忆、项目文件操作、代码执行、网页工具和主动提醒。

Agent 编排参考 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的设计思路，
核心链路为“消息进入 → 加载记忆 → 模型决策 → 工具执行 → 回写记忆”。

## 功能

- 流式输出：Web（SSE 打字机效果）与 CLI 都边生成边显示；
  底层是任意 OpenAI 兼容 API（默认 DeepSeek，可改）
- 记忆分层：工作窗口（最近 20 条原始对话）→ L2 滚动摘要
  （被挤出窗口的旧消息按批合并进 SQLite 摘要并注入上下文）→ L3 长期便签
  （攒够轮数后自动归档值得记住的内容，按内容去重，checkpoint 记录进度）
- 工具调用：当前时间 / 安全计算器 / 带标签的便签存取（可按标签筛选）/
  项目文件读写与代码执行 / 网页搜索（必应 RSS，免密钥，国内可直接访问）/
  定时提醒（标准 5 段 cron 表达式，按内容落库持久化）
- PDF 页面编辑：不改动源文件即可合并、抽取、删除或旋转页面；
  不支持直接改写页面中的现有文字
- 文档处理：扫描 PDF 可在审批后交给已配置的视觉模型做 OCR；Word/PPT 支持
  精确文字替换，Excel 支持按单元格写入，所有编辑均另存新文件
- 本地资料检索：导入文档后按段落保存到 SQLite；每轮自动用 FTS5 + BM25
  召回最多 3 段相关原文并标注来源，不需要向量数据库或额外 API
- 定时主动提醒：聊一句“每天 9 点提醒我喝水”就变成一条 cron 提醒；
  CLI / 桌宠由后台线程、Web 由前端轮询，到点主动弹出——
  **agent 会自己动**，不用你发消息它也会开口
- 人设可切换（默认 Nyalume）：`nyalume`（猫娘）/ `assistant`（标准助手）；
  Web 顶栏下拉、CLI `/persona`、桌宠右键都能切
- 日常模式：每段会话保存独立好感度，只进行纯聊天；模型不接收工具定义，
  工具入口也会二次拒绝，因此不能读写文件、运行命令、联网搜索或设置提醒
- 今日 Nyalume：每天 0:00 刷新一次 0~100 分抽取，覆盖 11 种表达人格；
  结果以内嵌卡片展示立绘和祝福，不打断任务，当天重复点击只查看同一结果。
  人格只改变回复风格，不降低事实准确性、工具纪律或安全标准
- 入口：CLI（命令行） + Web（FastAPI 单页，支持多会话：新建 / 切换 / 删除 / 历史回显）
  + 桌宠（透明置顶小窗、气泡对话、互动表情、右键换皮肤）
  （桌宠任务完成会头顶冒出彩蛋台词，默认「任务完成喵！」，皮肤 manifest 可配）
- 桌宠交互增强：拖到屏幕边缘自动“趴边只露头”；超过 30 秒没人理且
  不趴边时进入待机（idle 多帧下沉循环）；点“挂后台”收进系统托盘，
  随时从托盘呼出或退出；右键可把角色皮肤合成桌面壁纸 / 自定义壁纸 / 恢复
- 桌宠互动：拖拽时有“被拎起来”的拉伸/倾斜动画；单击按头/身/腿分区
  弹出互动台词（本地即时、按部位变化，双击才打开对话，避免误触）；
  摸摸不积累或改变关系数值；头顶小图标会提示
  “摸我/双击聊天”和趴边时的“拖出来”
- Web 增强：记忆/状态仪表盘（L2 摘要、L3 便签、定时提醒，可页内取消）；
  聊天背景壁纸支持“角色皮肤”或本地上传（记住选择，不依赖后端存储）
- 桌宠聊天框内置「📊 状态」面板：同 Web 一样查看摘要/便签/提醒
  （提醒可点 [取消]），顶部可直接挂后台
- 3D 桌宠：加载用户自备的 PMX 模型与 VMD 动作，支持按部位互动、拖拽、
  视线跟随、舞蹈、表情、主动搭话；聊天 agent 可用 `pet_status` 读取状态、
  用 `pet_perform` 指挥舞蹈、表情和气泡

## 快速开始

### Windows 便携版（推荐）

若 [GitHub Releases](https://github.com/ccliiko/Nyalume/releases) 提供与你要使用的版本对应的
Windows x64 压缩包，可下载并解压整个 `Nyalume` 文件夹后双击
`Nyalume.exe`，无需安装 Python；首次启动在设置中填写 API 服务商、
API Key 和模型。3D 启动与操作见 [Windows 便携版与 3D 桌宠教程](docs/Windows-便携版与3D桌宠教程.md)；
第三方模型和舞蹈需用户自行取得，本仓库与公开程序包不附带。
0.2.0 的个人素材构建仅保存在制作者本机，不作为 GitHub 公共下载包。

### 从源码运行

```powershell
cd Nyalume
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env    # 然后编辑 .env 填入你的 API Key
```

命令行聊天：

```bash
python cli.py
```

网页聊天：

```bash
python server.py
# 浏览器打开 http://127.0.0.1:8000
```

两个入口都只是根目录薄封装，等价于：
`python -m nyalume.frontends.cli` / `python -m nyalume.frontends.web.server`

桌宠：

```bash
python pet.py          # 无控制台双击 start_pet.bat
```

桌宠默认使用 Nyalume；右键可换自定义皮肤。皮肤放 `user_pets/`，
格式与版权说明见 `user_pets/README.md`（该目录已 gitignore，不随仓库分发）。

## 测试

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q
```

## 当前发行范围

当前公开版本是纯本地单用户版，不提供注册、登录和云同步。对话、记忆、卡片、
壁纸设置与模型 API Key 都保存在用户电脑上；`cloud_service/` 是独立部署组件，
默认不参与桌面版启动。

测试不依赖 API Key，也不碰真实 `agent.db`（conftest 会把 `MEMORY_DB`
指到临时文件）；覆盖 cron 解析、提醒去重、计算器安全、便签/会话/摘要/
每日 Nyalume 唯一性、人设注册表等核心逻辑。

## 许可、隐私与素材

Nyalume 是**源码可见的商业软件，不是开源软件**。个人非商业评估以
[LICENSE](LICENSE) 为准；官方发行版的安装和使用同时受 [EULA](EULA.md) 约束。

- 本地数据、API 调用和删除方式：[PRIVACY.md](PRIVACY.md)
- Python 依赖与外部服务声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- 角色图片、生成模型和商业发行状态：[ASSET_PROVENANCE.md](ASSET_PROVENANCE.md)
- 3D 模型、动作及宣传展示边界：[3D_ASSET_NOTICE.md](3D_ASSET_NOTICE.md)

当前 Nyalume 角色帧与每日卡片的旧生成链许可记录不完整，**不得直接用于付费发行**。
仓库中的新工作流已切换到许可清晰的模型，但商业包仍需用新工作流从文字设定重新生成素材。
游戏角色模型、非商用或禁止二配的 VMD 动作不得因为出现在本机 3D 桌宠中就进入
公开 EXE 或产品宣发；署名和免责声明不能代替相应权利人的授权。

## 本地运行追踪（Trace）

每轮对话会在同一个 SQLite 数据库中保存 `agent_traces` 记录，无需额外服务。
聊天事件中的 `run_id` 可关联该次运行；记录包含会话 ID、总耗时、主循环模型调用次数
（`spans` 中的 `llm` 条目）、工具名称与耗时、审批等待和最终状态。

```bash
python -m nyalume.core.tracing --limit 10
python -m nyalume.core.tracing --session pet --limit 5
```

状态包含 `completed`、`error`、`cancelled`、`max_rounds`；进程突然退出时可能留下
`running`。`completed` 表示程序正常结束，不代表任务答案正确。工具的 `returned`
表示正常返回（包括工具返回错误字符串），异常才标记 `error`。
耗时是本地经过时间，模型流包含消费者等待时间，审批等待单独计时。
记录不保存对话正文、工具参数、结果正文或密钥；暂不统计 token 和费用，
摘要与长期便签维护调用暂未独立计时。
记录暂不自动清理，长期使用时需自行管理数据库大小。

## 角色素材生成管线（可选）

Nyalume 表情帧的本地 ComfyUI 出图管线（提示词/工作流/脚本）见
[tools/nyalume_pipeline/README.md](tools/nyalume_pipeline/README.md)。
生成的 PNG 素材只落在 gitignore 的 `user_pets/`，仓库只分发管线代码与提示词。

## 配置（.env）

```ini
LLM_PROVIDER=deepseek
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
PERSONA=nyalume       # nyalume=猫娘（默认）/ assistant=标准助手
```

设置页可直接选择 DeepSeek、OpenAI、OpenRouter、硅基流动或自定义兼容接口；
预设会自动填写 `LLM_BASE_URL` 与推荐模型，API Key 仍填写对应供应商签发的 Key。

## 目录结构

```text
nyalume/                     项目包
├── core/                  内核层：与具体前端无关
│   ├── agent.py           Agent 编排（多轮/工具循环/流式/记忆分层）
│   ├── llm.py             模型接入（OpenAI 兼容，流式/非流式）
│   ├── vision.py          图片理解模型接入
│   ├── memory.py          三层记忆 + 会话管理（SQLite）
│   ├── reminders.py       定时主动提醒（cron 解析/落库/后台调度）
│   ├── personas.py        人设注册表（nyalume/标准助手）
│   ├── daily_nyalume.py     每日抽取、11 种风格与祝福记录
│   └── tools.py           工具注册表（时间/计算器/便签/搜索/提醒）
└── frontends/             前端层：只消费内核的 run_stream 事件
    ├── cli.py             命令行（流式打字机）
    ├── web/               FastAPI + SSE + 单页聊天
    │   ├── server.py      会话/消息 REST + SSE 聊天接口
    │   └── static/index.html
    └── pet/               桌宠前端（第四前端）
        ├── pet.py         主程序（皮肤菜单/触摸互动）
        ├── renderer.py    透明小窗 + 帧动画（无素材时程序画占位猫）
        ├── web_chat.py    气泡对话（线程消费 run_stream）
        └── web_chat_win.py 独立聊天窗口
        └── pets_registry.py  皮肤注册表（manifest/动画帧）
cli.py / server.py / pet.py  仓库根启动入口（薄封装）
start_pet.bat              双击启动桌宠（pythonw 无控制台）
user_pets/                 用户自备皮肤（gitignore，不随源码仓库分发）
agent.db                   SQLite 数据（默认放在仓库根，可用 MEMORY_DB 覆盖）
```

约定：新功能进 `core/`，新入口在 `frontends/` 里加一个目录
只消费 `agent.run_stream` 产出的事件，
所以 CLI / Web / 桌宠不会互相重复业务逻辑。

## 说明

1. 为什么记忆只保留最近 N 条：控制 token 成本
2. 工具调用的循环是怎么终止的：最大轮数 + finish_reason
3. 历史消息里工具中间过程不落库，只存最终问答：避免脏数据
4. 模型层用 OpenAI 兼容接口，换模型只改配置，不换代码
5. 便签带 tag 列 + 工具描述引导：用户说“算完记下来”时直接保存完整算式与结果，
   并在回复中复述已保存内容，不反问用户
6. “今日 Nyalume”由日期主键保证每天只有一份结果：0~99 每 10 分一档，
   100 分单独成档；抽取结果存 SQLite，并同步追加到根目录的可读祝福记录。
   每档的执行规则单独定义，但只控制措辞，不允许削弱正确性和工具纪律
7. 工具用注册表管理：新增工具 = 一次 `@register` 注册（名字/描述/参数声明），
   模型可见的 schema 自动生成，不存在“描述和实现两处维护”的漂移；
   执行时按名字查表分发，缺参、未知工具、网络异常都转成字符串返回，
   由模型自行解释，不中断对话
8. 流式不是“最后一屏渲染”：`llm.chat_stream` 逐块产出正文/工具增量，
   Agent 在流上把同一 index 的 tool_calls 碎片拼回完整参数再执行；
   正文块到达即转发，工具结果则作为独立事件逐项呈现
9. 记忆分三层而不是一味加长上下文：窗口只放最近 20 条控 token；
   被挤出的旧消息按批（攒够 6 条）交给模型滚成摘要，下轮以 system 上下文注入；
   长期记忆是带标签的便签，每攒够 8 轮让模型输出 JSON 归档，服务端解析、
   规范化去重后入库并推进 checkpoint——维护调用失败不阻塞主对话，
   触发频率受批大小约束，避免长会话每轮都付出额外模型成本
10. 会话列表不另建冗余表：标题直接用 SQL 取“该会话第一句用户消息”，
    排序按最近活动时间，删除时级联清理 messages/summaries（全局便签保留）；
    前端切换会话时才拉历史，SSE 流式回复期间不刷新列表，避免打断打字机效果
11. 代码按“内核 / 前端”分层：`core/` 不 import 任何前端，所有入口只消费
    `run_stream` 的统一事件（text/tool/error）；因此新增工具或记忆功能
    不会改动任何界面，加一个新前端（桌宠）也只等于多写一个事件消费者
12. 桌宠皮肤是“纯资源”：manifest 里的 idle/working/情绪帧由渲染器按场景选取；
    素材只进 gitignore 的 user_pets/，仓库本身不分发角色图片
13. 人设也是注册表而不是 if-else：每个角色 = id + prompt；
    今日人格作为独立风格层追加。切换只写一份 persona_config.json，解析优先级为
    运行时配置 > PERSONA 环境变量 > 默认 Nyalume，所以 Web/CLI/桌宠
    三个入口共用同一份选择，互不冲突
14. “主动提醒”不是前端各写一套定时器：cron 解析、去重、持久化都在
    reminders.py；到期判定 check_due 同分钟只触发一次（last_fired 落库），
    CLI/桌宠起一个后台线程消费、Web 轮询一个 due 接口——加新前端
    仍然只是多写一个事件消费者，这就是 agent 会自己动的最小骨架
