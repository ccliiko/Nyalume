"""Web 前端：python -m mini_agent.frontends.web.server（或仓库根 python server.py）

启动后访问 http://127.0.0.1:8000。
"""

import json
import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from mini_agent.core import memory
from mini_agent.core.agent import run_stream

app = FastAPI(title="mini-agent")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ChatIn(BaseModel):
    session_id: str = "web-default"
    message: str


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/sessions")
def list_sessions():
    """会话列表：标题取第一句用户消息，按最近活动倒序。"""
    return memory.list_sessions()


@app.post("/api/sessions")
def new_session():
    """新建会话，返回 {"id": ...}。"""
    return {"id": memory.create_session()}


@app.get("/api/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """切换会话时回显历史消息（正序）。"""
    return memory.session_messages(session_id)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话及其消息与摘要。"""
    memory.delete_session(session_id)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn):
    """SSE 流式聊天：每帧 data: {type: text|tool|error}，用空行分隔。"""

    def event_stream():
        for ev in run_stream(body.session_id, body.message):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
