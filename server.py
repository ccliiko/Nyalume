"""Web 入口：python server.py，然后访问 http://127.0.0.1:8000"""

import json
import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent import run_stream

app = FastAPI(title="mini-agent")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ChatIn(BaseModel):
    session_id: str = "web-default"
    message: str


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


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
