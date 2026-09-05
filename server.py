"""Web 入口：python server.py，然后访问 http://127.0.0.1:8000"""

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent import run

app = FastAPI(title="mini-agent")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ChatIn(BaseModel):
    session_id: str = "web-default"
    message: str


class ChatOut(BaseModel):
    reply: str


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn):
    reply = run(body.session_id, body.message)
    return ChatOut(reply=reply)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
