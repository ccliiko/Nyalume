# mini-agent：基于 LLM API 的个人 AI 助手（学习项目）

参考 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的设计思路，从零实现的最小版
个人 AI Agent：支持多轮对话、会话记忆、工具调用（函数调用），提供命令行和网页两种入口。

> 本项目是学习/简历项目，代码全部自己写。目标：能讲清楚
> "消息进来 → 加载记忆 → 模型决策 → 调工具 → 回写记忆" 这条链路。

## 功能

- 流式输出：Web（SSE 打字机效果）与 CLI 都边生成边显示；
  底层是任意 OpenAI 兼容 API（默认 DeepSeek，可改）
- 会话记忆：SQLite 保存多轮历史
- 工具调用：当前时间 / 安全计算器 / 带标签的便签存取（可按标签筛选）/
  网页搜索（必应 RSS，免密钥，国内可直接访问）
- 人设可配置：`PERSONA=catgirl` 开启猫娘人设，好感度作为会话状态存 SQLite，
  每轮由模型输出隐藏标记、服务端校验并持久化，展示前剥离；
  好感度分 5 段温度（疏离/闹别扭/日常撒娇/心动黏人/深爱守护），只改语气不改能力
- 入口：CLI（命令行） + Web（FastAPI 单页）

## 快速开始

```bash
cd ai-agent-mini
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

## 配置（.env）

```ini
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
PERSONA=assistant   # assistant=专业助手；catgirl=猫娘人设（含好感度）
```

如果你的 key 来自其他 OpenAI 兼容平台（硅基流动、OpenRouter 等），只改
`LLM_BASE_URL` 和 `LLM_MODEL` 即可。

## 目录结构

```text
agent.py        核心 Agent 循环（记忆+模型+工具的编排）
llm.py          模型接入层（OpenAI 兼容封装）
tools.py        工具注册表（schema 自动生成/参数校验/异常兜底）+ 工具实现
                （时间/计算器/带标签便签/网页搜索）
memory.py       会话记忆（SQLite）
cli.py          命令行入口
server.py       Web 入口（FastAPI）
static/index.html  网页聊天界面
```

## 面试时可以讲的设计点

1. 为什么记忆只保留最近 N 条：控制 token 成本
2. 工具调用的循环是怎么终止的：最大轮数 + finish_reason
3. 历史消息里工具中间过程不落库，只存最终问答：避免脏数据
4. 模型层用 OpenAI 兼容接口，换模型只改配置，不换代码
5. 便签带 tag 列 + 工具描述引导：用户说“算完记下来”时直接保存完整算式与结果，
   并在回复中复述已保存内容，不反问用户
6. 猫娘好感度不是模型“嘴上说说”：模型每轮输出隐藏标记 `[affection:+N]`，
   服务端校验范围、写 SQLite、展示前剥离——状态归程序管，人设归模型演；
   分 5 段温度让“表达随状态变化”，但工具调用等能力不受影响
7. 工具用注册表管理：新增工具 = 一次 `@register` 注册（名字/描述/参数声明），
   模型可见的 schema 自动生成，不存在“描述和实现两处维护”的漂移；
   执行时按名字查表分发，缺参、未知工具、网络异常都转成字符串返回，
   由模型自行解释，不中断对话
8. 流式不是“最后一屏渲染”：`llm.chat_stream` 逐块产出正文/工具增量，
   Agent 在流上把同一 index 的 tool_calls 碎片拼回完整参数再执行；
   猫娘好感度隐藏标记靠“末尾 40 字符缓冲、流结束后剥离再补发”处理，
   既有打字机效果，又不会把内部状态流给前端
