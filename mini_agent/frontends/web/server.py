"""Web 前端：python -m mini_agent.frontends.web.server（或仓库根 python server.py）

启动后访问 http://127.0.0.1:8000。
"""

import json
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from mini_agent.core import memory, personas, reminders
from mini_agent.core.agent import run_stream

app = FastAPI(title="mini-agent")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ChatIn(BaseModel):
    session_id: str = "web-default"
    message: str


class PersonaIn(BaseModel):
    persona: str


class ReminderIn(BaseModel):
    content: str
    cron: str


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/personas")
def list_personas():
    """人设列表（供前端下拉/菜单展示）。"""
    return personas.list_personas()


@app.get("/api/persona")
def current_persona():
    return {"persona": personas.resolve_persona_id()}


@app.put("/api/persona")
def switch_persona(body: PersonaIn):
    """切换人设并持久化，下一条消息生效。"""
    persona_id = (body.persona or "").strip().lower()
    if not personas.set_persona(persona_id):
        raise HTTPException(status_code=400, detail=f"未知人设：{body.persona}")
    return {"ok": True, "persona": persona_id}


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


@app.get("/api/reminders")
def list_reminders():
    """列出全部定时提醒。"""
    return reminders.list_reminders()


@app.post("/api/reminders")
def create_reminder(body: ReminderIn):
    """创建定时提醒（cron 5 段表达式）。"""
    try:
        rid = reminders.add_reminder(body.content, body.cron)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": rid}


@app.delete("/api/reminders/{reminder_id}")
def cancel_reminder(reminder_id: int):
    ok = reminders.delete_reminder(reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"没有提醒 #{reminder_id}")
    return {"ok": True}


@app.get("/api/reminders/due")
def due_reminders():
    """前端每 15 秒轮询：到期即返回并把 last_fired 落库（同分钟不重复）。"""
    return reminders.check_due()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
