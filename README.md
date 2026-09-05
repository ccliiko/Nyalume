# mini-agent：基于 LLM API 的个人 AI 助手（学习项目）

参考 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的设计思路，从零实现的最小版
个人 AI Agent：支持多轮对话、会话记忆、工具调用（函数调用），提供命令行和网页两种入口。

> 本项目是学习/简历项目，代码全部自己写。目标：能讲清楚
> "消息进来 → 加载记忆 → 模型决策 → 调工具 → 回写记忆" 这条链路。

## 功能

- 流式接入任意 OpenAI 兼容 API（默认 DeepSeek，可改）
- 会话记忆：SQLite 保存多轮历史
- 工具调用：当前时间 / 安全计算器 / 便签存取
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
```

如果你的 key 来自其他 OpenAI 兼容平台（硅基流动、OpenRouter 等），只改
`LLM_BASE_URL` 和 `LLM_MODEL` 即可。

## 目录结构

```text
agent.py        核心 Agent 循环（记忆+模型+工具的编排）
llm.py          模型接入层（OpenAI 兼容封装）
tools.py        工具定义与执行（时间/计算器/便签）
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
