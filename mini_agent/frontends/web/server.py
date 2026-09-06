"""Web 前端：python -m mini_agent.frontends.web.server（或仓库根 python server.py）

启动后访问 http://127.0.0.1:8000。
"""

import json
import os
import shutil

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from mini_agent.core import memory, personas, reminders
from mini_agent.core.agent import run_stream
from mini_agent.frontends.pet.wallpaper import character_wallpaper
from mini_agent.frontends.pet.pets_registry import (
    frame_paths,
    get_pet,
    load_config,
    save_config,
)

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


class WallpaperIn(BaseModel):
    mode: str = ""  # "" / character / custom
    opacity: int = 70
    full: bool = True


def _wallpaper_settings() -> dict:
    cfg = load_config()
    custom = cfg.get("wall_custom") or ""
    return {
        "mode": cfg.get("wall_mode") or "",
        "opacity": int(cfg.get("wall_opacity", 70)),
        "full": str(cfg.get("wall_full", "1")) not in ("0", "false", "False"),
        "custom_url": f"/api/wallpaper/file/{custom}" if custom else "",
    }


def _cliko_dir() -> str:
    pet = get_pet(load_config().get("pet") or "neko-placeholder")
    return pet.get("dir") or ""


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
    """前端轮询：只读返回到期提醒；触发权（落库/删一次性）归桌宠调度器。"""
    return reminders.check_due(claim=False)


@app.get("/api/state")
def agent_state(session_id: str = "web-default"):
    """记忆/状态仪表盘数据：会话滚动摘要 + 最近便签 + 定时提醒。"""
    return {
        "session_id": session_id,
        "summary": memory.get_summary(session_id),
        "notes": memory.get_recent_notes(8),
        "reminders": reminders.list_reminders(),
        "persona": personas.resolve_persona_id(),
        "persona_name": personas.current_persona_name(),
    }


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    if not memory.note_delete(note_id):
        raise HTTPException(status_code=404, detail=f"没有便签 #{note_id}")
    return {"ok": True}


@app.get("/api/skin/current")
def current_skin_image():
    """当前桌宠皮肤的合成壁纸（渐变底+角色），供 Web 当背景用。"""
    pet_id = load_config().get("pet") or "neko-placeholder"
    pet = get_pet(pet_id)
    if not pet.get("dir") or not frame_paths(pet, "idle"):
        raise HTTPException(status_code=404, detail="当前皮肤没有可用帧")
    out = os.path.join(pet["dir"], "wallpaper_web.png")
    path = character_wallpaper(out, unique=False)
    return FileResponse(path, media_type="image/png")


@app.get("/api/wallpaper/settings")
def get_wallpaper_settings():
    """聊天窗壁纸设置（关窗后保留，由服务端持久化）。"""
    return _wallpaper_settings()


@app.post("/api/wallpaper/settings")
def save_wallpaper_settings(body: WallpaperIn):
    cfg = load_config()
    cfg["wall_mode"] = body.mode
    cfg["wall_opacity"] = max(10, min(100, int(body.opacity)))
    cfg["wall_full"] = "1" if body.full else "0"
    save_config(cfg)
    return {"ok": True}


@app.post("/api/wallpaper/custom")
async def upload_custom_wallpaper(file: UploadFile = File(...)):
    """上传自定义聊天壁纸：文件落盘 + 记录设置，关窗重开仍保留。"""
    base = _cliko_dir()
    if not base:
        raise HTTPException(status_code=404, detail="当前皮肤没有素材目录")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    target = os.path.join(base, f"chat_wallpaper_custom{ext}")
    with open(target, "wb") as f:
        shutil.copyfileobj(file.file, f)
    cfg = load_config()
    cfg["wall_custom"] = os.path.basename(target)
    cfg["wall_mode"] = "custom"
    cfg["wall_opacity"] = int(cfg.get("wall_opacity", 70))
    cfg["wall_full"] = str(cfg.get("wall_full", "1"))
    save_config(cfg)
    return {"ok": True, "url": f"/api/wallpaper/file/{os.path.basename(target)}"}


@app.get("/api/wallpaper/file/{name}")
def wallpaper_file(name: str):
    base = _cliko_dir()
    safe = os.path.basename(name)
    path = os.path.join(base, safe)
    if not base or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="壁纸文件不存在")
    return FileResponse(path)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
