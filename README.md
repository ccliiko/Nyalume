# mini-agent：基于 LLM API 的个人 AI 助手（学习项目）

参考 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的设计思路，从零实现的最小版
个人 AI Agent：支持多轮对话、会话记忆、工具调用（函数调用），提供命令行和网页两种入口。

> 本项目是学习/简历项目，代码全部自己写。目标：能讲清楚
> "消息进来 → 加载记忆 → 模型决策 → 调工具 → 回写记忆" 这条链路。

## 功能

- 流式输出：Web（SSE 打字机效果）与 CLI 都边生成边显示；
  底层是任意 OpenAI 兼容 API（默认 DeepSeek，可改）
- 记忆分层：工作窗口（最近 20 条原始对话）→ L2 滚动摘要
  （被挤出窗口的旧消息按批合并进 SQLite 摘要并注入上下文）→ L3 长期便签
  （攒够轮数后自动归档值得记住的内容，按内容去重，checkpoint 记录进度）
- 工具调用：当前时间 / 安全计算器 / 带标签的便签存取（可按标签筛选）/
  网页搜索（必应 RSS，免密钥，国内可直接访问）/
  定时提醒（标准 5 段 cron 表达式，按内容落库持久化）
- 定时主动提醒：聊一句“每天 9 点提醒我喝水”就变成一条 cron 提醒；
  CLI / 桌宠由后台线程、Web 由前端轮询，到点主动弹出——
  **agent 会自己动**，不用你发消息它也会开口
- 人设可切换（默认 cliko）：`cliko`（猫娘）/ `assistant`（标准助手）；
  Web 顶栏下拉、CLI `/persona`、桌宠右键都能切
- 温度状态机：带人设的角色把好感度/信赖度作为会话状态存 SQLite，
  每轮由模型输出隐藏标记、服务端校验并持久化、展示前剥离；
  分 5 段温度（疏离/闹别扭/日常撒娇/心动黏人/深爱守护），只改语气不改能力
- 入口：CLI（命令行） + Web（FastAPI 单页，支持多会话：新建 / 切换 / 删除 / 历史回显）
  + 桌宠（透明置顶小窗、气泡对话、好感度切表情、右键换皮肤）
  （桌宠任务完成会头顶冒出彩蛋台词，默认「任务完成喵！」，皮肤 manifest 可配）

## 快速开始

```bash
cd nanobot
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 然后编辑 .env 填入你的 API Key
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
`python -m mini_agent.frontends.cli` / `python -m mini_agent.frontends.web.server`

桌宠：

```bash
python pet.py          # 无控制台双击 start_pet.bat
```

桌宠内置一只程序绘制的占位猫；右键可换皮肤。外置皮肤放 `user_pets/`，
格式与版权说明见 `user_pets/README.md`（该目录已 gitignore，不随仓库分发）。

## 配置（.env）

```ini
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
PERSONA=cliko       # cliko=猫娘（默认）/ assistant=标准助手
```

如果你的 key 来自其他 OpenAI 兼容平台（硅基流动、OpenRouter 等），只改
`LLM_BASE_URL` 和 `LLM_MODEL` 即可。

## 目录结构

```text
mini_agent/                项目包
├── core/                  内核层：与具体前端无关
│   ├── agent.py           Agent 编排（多轮/工具循环/流式/记忆分层）
│   ├── llm.py             模型接入（OpenAI 兼容，流式/非流式）
│   ├── memory.py          三层记忆 + 会话管理（SQLite）
│   ├── reminders.py       定时主动提醒（cron 解析/落库/后台调度）
│   ├── personas.py        人设注册表（cliko/助手 + 温度状态注入）
│   └── tools.py           工具注册表（时间/计算器/便签/搜索/提醒）
└── frontends/             前端层：只消费内核的 run_stream 事件
    ├── cli.py             命令行（流式打字机）
    ├── web/               FastAPI + SSE + 单页聊天
    │   ├── server.py      会话/消息 REST + SSE 聊天接口
    │   └── static/index.html
    └── pet/               桌宠前端（第四前端）
        ├── pet.py         主程序（皮肤菜单/心情联动）
        ├── renderer.py    透明小窗 + 帧动画（无素材时程序画占位猫）
        ├── chat_panel.py  气泡对话（线程消费 run_stream）
        └── pets_registry.py  皮肤注册表（manifest/好感度分档）
cli.py / server.py / pet.py  仓库根启动入口（薄封装）
start_pet.bat              双击启动桌宠（pythonw 无控制台）
user_pets/                 用户自备皮肤（gitignore，仅本地演示）
agent.db                   SQLite 数据（默认放在仓库根，可用 MEMORY_DB 覆盖）
```

约定：新功能进 `core/`，新入口在 `frontends/` 里加一个目录
只消费 `agent.run_stream` 产出的事件，
所以 CLI / Web / 桌宠不会互相重复业务逻辑。

## 面试时可以讲的设计点

1. 为什么记忆只保留最近 N 条：控制 token 成本
2. 工具调用的循环是怎么终止的：最大轮数 + finish_reason
3. 历史消息里工具中间过程不落库，只存最终问答：避免脏数据
4. 模型层用 OpenAI 兼容接口，换模型只改配置，不换代码
5. 便签带 tag 列 + 工具描述引导：用户说“算完记下来”时直接保存完整算式与结果，
   并在回复中复述已保存内容，不反问用户
6. cliko 的好感度不是模型“嘴上说说”：模型每轮输出隐藏标记 `[affection:+N]`，
   服务端校验范围、写 SQLite、展示前剥离——状态归程序管，人设归模型演；
   分 5 段温度让“表达随状态变化”，但工具调用等能力不受影响
7. 工具用注册表管理：新增工具 = 一次 `@register` 注册（名字/描述/参数声明），
   模型可见的 schema 自动生成，不存在“描述和实现两处维护”的漂移；
   执行时按名字查表分发，缺参、未知工具、网络异常都转成字符串返回，
   由模型自行解释，不中断对话
8. 流式不是“最后一屏渲染”：`llm.chat_stream` 逐块产出正文/工具增量，
   Agent 在流上把同一 index 的 tool_calls 碎片拼回完整参数再执行；
   cliko 的好感度隐藏标记靠“末尾 40 字符缓冲、流结束后剥离再补发”处理，
   既有打字机效果，又不会把内部状态流给前端
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
12. 桌宠皮肤是“纯资源”：manifest 里的帧图按好感度五档分组
    （distant/grumpy/neutral/happy/love + working），渲染器只做映射；
    内置占位猫由 Canvas 程序绘制、零版权负担，第三方素材只进
    gitignore 的 user_pets/，仓库本身不含任何角色图片
13. 人设也是注册表而不是 if-else：每个角色 = id + prompt + 是否启用
    “温度状态”；切换只写一份 persona_config.json，解析优先级为
    运行时配置 > PERSONA 环境变量 > 默认 cliko，所以 Web/CLI/桌宠
    三个入口共用同一份选择，互不冲突
14. “主动提醒”不是前端各写一套定时器：cron 解析、去重、持久化都在
    reminders.py；到期判定 check_due 同分钟只触发一次（last_fired 落库），
    CLI/桌宠起一个后台线程消费、Web 轮询一个 due 接口——加新前端
    仍然只是多写一个事件消费者，这就是 agent 会自己动的最小骨架
