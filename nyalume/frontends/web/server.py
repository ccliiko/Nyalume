"""Web 前端：python -m nyalume.frontends.web.server（或仓库根 python server.py）

启动后访问 http://127.0.0.1:8000。
"""

import json
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from nyalume.core import companionship, daily_nyalume, memory, personas, reminders, skills, tracing
from nyalume.core.agent import run_stream
from nyalume.core.vision import describe_image as _vd
from nyalume.core.vision import vision_configured
from nyalume.core.tools import (
    apply_undo,
    clean_session_workspace,
    clear_session_context,
    diff_undo,
    permission_mode,
    pet_status_snapshot,
    pet_check_motions,
    preview_undo,
    set_permission_mode,
    set_session_context,
    session_work_root,
    workspace_root,
)
from nyalume.frontends.pet.pets_registry import (
    frame_paths,
    get_pet,
    load_config,
    save_config,
)

# --- world system ---
from nyalume.core.world.manager import world as _world


def _auto_backup_on_start() -> None:
    """每次服务启动自动留一份记忆快照（保留最近 20 份）。"""
    try:
        path = memory.backup_db()
        backups = sorted(memory.list_backups(), key=lambda b: b["ts"], reverse=True)
        for old in backups[20:]:
            try:
                os.remove(os.path.join(memory.BACKUP_DIR, old["name"]))
            except OSError:
                pass
        print("auto backup:", os.path.basename(path))
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    _auto_backup_on_start()
    # --- start world system ---
    _repo_root = os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
    )
    _world.start(_repo_root)
    print(f'[world] started, root={_repo_root}')
    yield
    _world.shutdown()
    print('[world] stopped')


app = FastAPI(title="Nyalume", lifespan=lifespan)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_IMG_EXTS = _IMAGE_EXTS | {".mp4", ".webm", ".mov"}
_activity_lock = threading.Lock()
_active_runs = 0
_pet_tap_seq = 0
_pet_meow_seq = 0
_we_media_path = ""
_we_startup_available = False
_WE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class DailyNyalumeUpdateIn(BaseModel):
    liked: bool | None = None
    collected: bool | None = None
    viewed: bool | None = None


def _change_activity(delta: int) -> None:
    global _active_runs
    with _activity_lock:
        _active_runs = max(0, _active_runs + delta)


@app.get("/api/activity")
def activity_state():
    """桌宠轮询的轻量状态：有任一聊天任务运行时进入工作态。"""
    with _activity_lock:
        return {
            "working": _active_runs > 0,
            "pet_tap_seq": _pet_tap_seq,
            "pet_meow_seq": _pet_meow_seq,
        }


@app.post("/api/pet/tap")
def pet_tap(payload: dict):
    """把聊天窗的敲猫猫头动作捎给桌宠；计数仍由前端本地保存。"""
    global _pet_tap_seq, _pet_meow_seq
    with _activity_lock:
        _pet_tap_seq += 1
        if bool(payload.get("meowed")):
            _pet_meow_seq += 1
        return {"ok": True, "seq": _pet_tap_seq}


@app.get("/api/daily-nyalume")
def get_daily_nyalume():
    """只查看今天是否抽过，不会暗中代抽。"""
    return daily_nyalume.get_today() or {"drawn": False}


@app.post("/api/daily-nyalume")
def draw_daily_nyalume():
    return daily_nyalume.draw_today()


@app.get("/api/daily-nyalume/collection")
def daily_nyalume_collection():
    return daily_nyalume.collection()


@app.patch("/api/daily-nyalume/card/{day}")
def update_daily_nyalume_card(day: str, body: DailyNyalumeUpdateIn):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(status_code=400, detail="日期格式不正确")
    result = daily_nyalume.update_card(day, **body.model_dump())
    if not result:
        raise HTTPException(status_code=404, detail="没有这张今日 Nyalume")
    return result


@app.get("/api/daily-nyalume/portrait/{profile_key}")
def daily_nyalume_portrait(profile_key: str):
    profile = daily_nyalume.profile_by_key(profile_key)
    if not profile:
        raise HTTPException(status_code=404, detail="没有这个今日 Nyalume")
    card = os.path.join(STATIC_DIR, "daily_nyalume", f"{profile_key}.png")
    if os.path.isfile(card):
        return FileResponse(card, media_type="image/png")
    pet = get_pet(load_config().get("pet") or "nyalume")
    group, index = profile["portrait"]
    paths = frame_paths(pet, group)
    if not paths and pet["id"] != "nyalume":
        paths = frame_paths(get_pet("nyalume"), group)
    if not paths:
        raise HTTPException(status_code=404, detail="当前皮肤没有对应立绘")
    return FileResponse(paths[min(index, len(paths) - 1)], media_type="image/png")


@app.get("/api/workspace/file")
def workspace_file(path: str = ""):
    """网页聊天里展示工具保存的图片（截图等），只允许工作区内的图片文件。"""
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="缺少 path")
    roots = [os.path.realpath(workspace_root())]
    for proj in memory.list_projects():
        p = (proj.get("path") or "").strip()
        if p:
            roots.append(os.path.realpath(p))
    if os.path.isabs(raw):
        target = os.path.realpath(raw)
    else:
        rel = raw.replace("\\", "/").strip("/")
        if not rel:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = os.path.realpath(os.path.join(roots[0], rel))
    if not any(
        r and os.path.commonpath([r, target]) == r
        for r in roots
        if os.path.isdir(r)
    ):
        raise HTTPException(status_code=403, detail="越出工作区")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="文件不存在")
    if os.path.splitext(target)[1].lower() not in _IMG_EXTS:
        raise HTTPException(status_code=400, detail="只支持图片/视频")
    return FileResponse(target)


@app.post("/api/vision/upload")
async def vision_upload(
    file: UploadFile = File(...), session_id: str = Form("")
):
    """网页里发图：存到当前对话目录，并用视觉模型转成文字。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="只支持图片文件")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 15MB")
    root = os.path.realpath(session_work_root(session_id))
    img_dir = os.path.join(root, "uploads", "images")
    os.makedirs(img_dir, exist_ok=True)
    name = f"{int(time.time())}_{os.urandom(3).hex()}{ext}"
    target = os.path.join(img_dir, name)
    with open(target, "wb") as fh:
        fh.write(data)
    rel = os.path.relpath(target, root).replace("\\", "/")
    text = _vd(target) if vision_configured() else ""
    return {"path": rel, "abs": target, "text": text}


@app.post("/api/vision/describe")
def vision_describe(payload: dict):
    """描述工作区里已存在的图片（返回文字）。"""
    root = os.path.realpath(workspace_root())
    p = str((payload or {}).get("path") or "").strip()
    target = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
    if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        raise HTTPException(status_code=400, detail="图片不在工作区内，请直接把图片拖进网页")
    return {"text": _vd(target, str((payload or {}).get("question") or ""))}


class ConfigIn(BaseModel):
    values: dict[str, str] = {}
    clear: list[str] = []


class SkillUpdateIn(BaseModel):
    enabled: bool


# ---------- Skill 管理 ----------


@app.get("/api/skills")
def get_skills():
    return skills.list_skills()


@app.post("/api/skills/import")
async def import_skill(file: UploadFile = File(...)):
    data = await file.read(12 * 1024 * 1024 + 1)
    try:
        return skills.install_bytes(data, file.filename or "SKILL.md")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdateIn):
    try:
        return skills.set_enabled(skill_id, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------- API 配置（读写仓库根 .env；密钥不回显） ----------

PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek", "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
        "vision_models": ["deepseek-v4-flash-vision-exp"],
    },
    "openai": {
        "name": "OpenAI", "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.6-luna",
        "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-4o-mini"],
        "vision_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-4o-mini"],
    },
    "openrouter": {
        "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
        "model": "~openai/gpt-latest", "models": ["~openai/gpt-latest"],
        "vision_models": ["~openai/gpt-latest"],
    },
    "siliconflow": {
        "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
        "model": "Pro/deepseek-ai/DeepSeek-R1",
        "models": ["Pro/deepseek-ai/DeepSeek-R1"],
        "vision_models": [],
    },
    "custom": {"name": "自定义兼容接口", "base_url": "", "model": "", "models": [], "vision_models": []},
}


CONFIG_FIELDS = [
    {
        "key": "LLM_PROVIDER",
        "label": "API 供应商",
        "required": False,
        "secret": False,
        "kind": "select",
        "options": list(PROVIDERS),
        "option_labels": {key: value["name"] for key, value in PROVIDERS.items()},
        "default": "deepseek",
        "hint": "选择预设会自动填写接口地址和推荐模型；自定义仍可手填。",
    },
    {
        "key": "LLM_API_KEY",
        "label": "模型 API Key",
        "required": True,
        "secret": True,
        "default": "",
        "hint": "必填：填写所选供应商签发的 API Key。",
    },
    {
        "key": "LLM_BASE_URL",
        "label": "模型接口地址",
        "required": False,
        "secret": False,
        "default": "https://api.deepseek.com",
        "hint": "选填：一般保持默认即可。",
    },
    {
        "key": "LLM_MODEL",
        "label": "自定义模型名",
        "required": False,
        "secret": False,
        "options": list(dict.fromkeys(
            model for provider in PROVIDERS.values() for model in provider["models"]
        )),
        "default": "deepseek-v4-pro",
        "hint": "仅使用自定义兼容接口时填写；预设供应商请在聊天框旁切换模型。",
    },
    {
        "key": "LLM_REASONING_EFFORT",
        "label": "模型推理等级",
        "required": False,
        "secret": False,
        "hidden": True,
        "kind": "select",
        "options": ["", "low", "high", "max"],
        "default": "",
        "hint": "选填：低/高/最高（deepseek-v4 系列生效）。",
    },
    {
        "key": "VISION_API_KEY",
        "label": "视觉模型 API Key（给 Nyalume 看图）",
        "required": False,
        "secret": True,
        "default": "",
        "hidden": True,
        "hint": "选填：不填则复用上面的 LLM_API_KEY（DeepSeek 官方视觉实验模型）。",
    },
    {
        "key": "VISION_BASE_URL",
        "label": "视觉模型接口地址",
        "required": False,
        "secret": False,
        "default": "https://api.deepseek.com/v1",
        "hidden": True,
        "hint": "选填：DeepSeek 官方默认即可。",
    },
    {
        "key": "VISION_MODEL",
        "label": "视觉模型名",
        "required": False,
        "secret": False,
        "kind": "select",
        "hidden": True,
        "options": [
            "deepseek-v4-flash-vision-exp",
        ],
        "default": "deepseek-v4-flash-vision-exp",
        "hint": "选填：DeepSeek 官方视觉实验模型（与 LLM 同一个 Key）。",
    },
]


def _env_path() -> str:
    return os.path.join(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ),
        ".env",
    )


def _env_map() -> dict[str, str]:
    """读 .env 为 {KEY: value}；不存在的 key 返回空。"""
    result: dict[str, str] = {}
    path = _env_path()
    if not os.path.isfile(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                result[key] = value
    return result


def _write_env(updates: dict[str, str], clear: list[str]) -> None:
    """更新 .env：updates 只写非空值；clear 里的 key 被删除。"""
    path = _env_path()
    env = _env_map()
    env.update({k: v for k, v in updates.items() if v and v.strip()})
    removed: set[str] = set()
    for key in clear:
        if env.pop(key, None) is not None:
            removed.add(key)
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    seen: set[str] = set()
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in removed:
                continue
            if key in env:
                value = env.pop(key)
                seen.add(key)
                out.append(f"{key}={value}")
                continue
        out.append(line)
    for key, value in env.items():
        out.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def _preview(key: str, env: dict[str, str]) -> str:
    value = env.get(key, "")
    return ("****" + value[-4:]) if len(value) > 4 else ("已配置" if value else "")


def _active_provider(env: dict[str, str]) -> str:
    selected = env.get("LLM_PROVIDER", "").lower()
    if selected in PROVIDERS:
        return selected
    base = env.get("LLM_BASE_URL", "").rstrip("/").lower()
    for key, provider in PROVIDERS.items():
        if key != "custom" and base == provider["base_url"].rstrip("/").lower():
            return key
    return "custom"


@app.get("/api/config")
def get_config():
    env = _env_map()
    provider_key = _active_provider(env)
    fields = []
    for spec in CONFIG_FIELDS:
        key = spec["key"]
        current = env.get(key, "")
        field = dict(spec)
        if key == "LLM_PROVIDER":
            current = provider_key
        elif key == "LLM_MODEL":
            field["options"] = PROVIDERS[provider_key]["models"]
            field["default"] = PROVIDERS[provider_key]["model"]
        fields.append(
            {
                **field,
                "value": "" if spec["secret"] else current,
                "configured": bool(current),
                "preview": _preview(key, env) if spec["secret"] else "",
            }
        )
    return {"fields": fields, "providers": PROVIDERS}


@app.put("/api/config")
def put_config(body: ConfigIn):
    updates = {k: (v or "").strip() for k, v in (body.values or {}).items()}
    updates = {k: v for k, v in updates.items() if v}
    _write_env(updates, list(body.clear or []))
    for key, value in updates.items():
        os.environ[key] = value  # 立即生效，不用重启
    for key in list(body.clear or []):
        os.environ.pop(key, None)
    return {"ok": True}


# ---------- 纪律开关 / 工具审计 ----------

_DISC_KEYS = {
    "loop_breaker": "disc_loop_breaker",
    "call_fuse": "disc_call_fuse",
    "failure_hint": "disc_failure_hint",
    "round_hint": "disc_round_hint",
    "audit": "disc_audit",
}


@app.get("/api/discipline")
def get_discipline():
    return {
        key: memory.get_setting(setting_key, "1") != "0"
        for key, setting_key in _DISC_KEYS.items()
    }


@app.put("/api/discipline")
def put_discipline(payload: dict):
    for key, value in (payload or {}).items():
        if key in _DISC_KEYS:
            memory.set_setting(_DISC_KEYS[key], "1" if value else "0")
    return {"ok": True}


@app.get("/api/audit")
def get_audit(limit: int = 30):
    return memory.list_tool_audit(max(1, min(100, limit)))


@app.get("/api/traces/{run_id}")
def get_trace(run_id: str):
    trace = tracing.get_run(run_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace 不存在")
    return trace


class ChatIn(BaseModel):
    session_id: str = "web-default"
    message: str
    request_id: str = ""


class PersonaIn(BaseModel):
    persona: str


class ReminderIn(BaseModel):
    content: str
    cron: str


class WallpaperIn(BaseModel):
    mode: str = ""  # "" / character / custom
    opacity: int = 70


class DocsImportIn(BaseModel):
    path: str
    session_id: str = ""


class SessionCreateIn(BaseModel):
    project: str = ""


class SessionUpdateIn(BaseModel):
    title: str | None = None
    pinned: int | None = None
    project: str | None = None


class ProjectIn(BaseModel):
    path: str
    name: str = ""


class DropProjectIn(BaseModel):
    name: str = "拖入的项目"


class OpenPathIn(BaseModel):
    path: str
    session_id: str = ""


class ProjectUpdateIn(BaseModel):
    name: str | None = None
    pinned: int | None = None


class ProjectFolderIn(BaseModel):
    path: str


class PermissionIn(BaseModel):
    mode: str


class BackupIn(BaseModel):
    name: str = ""


class ApprovalIn(BaseModel):
    decision: str  # allow / deny


class BranchIn(BaseModel):
    mode: str = "same"  # same=此工作区内 / worktree=新工作树
    message_id: int | None = None


_PENDING_APPROVALS: dict[str, dict] = {}
_APPROVAL_LOCK = threading.Lock()
_RUNS_LOCK = threading.Lock()
_SESSION_RUN_LOCKS: dict[str, threading.Lock] = {}
_RUN_CANCEL_EVENTS: dict[str, threading.Event] = {}
_WALLPAPER_CONFIG_LOCK = threading.RLock()


def _session_run_lock(session_id: str) -> threading.Lock:
    """同一会话串行，不同会话可并发。"""
    with _RUNS_LOCK:
        return _SESSION_RUN_LOCKS.setdefault(session_id or "", threading.Lock())


def _frame(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _wait_approval(
    request_id: str, timeout: int = 600, cancel_event: threading.Event | None = None
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return "deny"
        with _APPROVAL_LOCK:
            pending = _PENDING_APPROVALS.get(request_id)
            if pending and pending.get("decision"):
                return pending["decision"]
        time.sleep(0.2)
    return "deny"


@app.post("/api/runs/{request_id}/cancel")
def cancel_run(request_id: str):
    """Cooperatively stop the active model stream and cancellable tools."""
    with _RUNS_LOCK:
        cancel_event = _RUN_CANCEL_EVENTS.get(request_id)
    if cancel_event is not None:
        cancel_event.set()
    return {"ok": True, "active": cancel_event is not None}


def _wallpaper_settings() -> dict:
    global _we_media_path, _we_startup_available
    cfg = load_config()
    custom = cfg.get("wall_custom") or ""
    requested = cfg.get("wall_mode") or ""
    engine_url = ""
    engine_kind = ""

    if requested == "engine":
        _we_startup_available = bool(_wallpaper_engine_tray_exe())
        if _we_startup_available:
            # 每次打开聊天窗都重新同步当前 Wallpaper Engine 壁纸；
            # 当前壁纸不是图片/MP4（或读不到）时，回退到上次关闭前缓存的那张。
            live = ""
            try:
                live, _selected = _wallpaper_engine_selected()
            except HTTPException:
                live = ""
            cached = cfg.get("wall_engine_media") or ""
            for candidate in (live, cached):
                if not candidate:
                    continue
                try:
                    _we_media_path, kind = _wallpaper_engine_media(candidate)
                except HTTPException:
                    continue
                if candidate == live:
                    with _WALLPAPER_CONFIG_LOCK:
                        cfg = load_config()
                        _remember_wallpaper_engine(cfg, _we_media_path, kind)
                        cfg["wall_mode"] = "engine"
                        save_config(cfg)
                engine_url = _wallpaper_engine_url(_we_media_path)
                engine_kind = kind
                break

    if engine_url:
        return {
            "mode": "engine",
            "opacity": int(cfg.get("wall_opacity", 70)),
            "custom_url": f"/api/wallpaper/file/{custom}" if custom else "",
            "engine_url": engine_url,
            "engine_kind": engine_kind,
        }

    # 旧版的内置角色壁纸已移除；旧配置优先回退到用户自己的壁纸。
    fallback = "custom" if custom and requested != "none" else ""
    return {
        "mode": fallback,
        "opacity": int(cfg.get("wall_opacity", 70)),
        "custom_url": f"/api/wallpaper/file/{custom}" if custom else "",
        "engine_url": "",
        "engine_kind": "",
    }


def _nyalume_dir() -> str:
    pet = get_pet(load_config().get("pet") or "nyalume")
    return pet.get("dir") or ""


def _process_running(name: str) -> bool:
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"if (Get-Process -Name '{os.path.splitext(name)[0]}' "
                "-ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
            ],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _wallpaper_engine_exe() -> str:
    """自动寻找 Wallpaper Engine；非标准安装可用环境变量指定。"""
    configured = os.getenv("WALLPAPER_ENGINE_EXE", "").strip().strip('"')
    candidates = [configured] if configured else []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for steam in (
            "SteamLibrary",
            "Steam",
            os.path.join("Program Files (x86)", "Steam"),
            os.path.join("Program Files", "Steam"),
        ):
            base = os.path.join(
                f"{letter}:\\", steam, "steamapps", "common", "wallpaper_engine",
            )
            candidates.extend((
                os.path.join(base, "wallpaper32.exe"),
                os.path.join(base, "wallpaper64.exe"),
            ))
    installed = [p for p in candidates if p and os.path.isfile(p)]
    return next(
        (p for p in installed if _process_running(os.path.basename(p).lower())),
        installed[0] if installed else "",
    )


def _wallpaper_engine_media(path: str) -> tuple[str, str]:
    """把当前 Wallpaper Engine 项目解析成浏览器可播放的图片或 MP4。"""
    path = os.path.realpath((path or "").strip().strip('"'))
    if os.path.basename(path).lower() == "project.json" and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as file:
                relative = str(json.load(file).get("file") or "")
            root = os.path.dirname(path)
            target = os.path.realpath(os.path.join(root, relative))
            if os.path.commonpath([root, target]) != root:
                raise ValueError("项目文件越界")
            path = target
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(status_code=415, detail="无法读取当前壁纸项目")
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Wallpaper Engine 当前壁纸文件不存在")
    if ext in _WE_IMAGE_EXTS:
        return path, "image"
    if ext == ".mp4":
        return path, "video"
    raise HTTPException(status_code=415, detail="当前壁纸不是图片或 MP4，第一版暂不支持")


def _wallpaper_engine_config() -> dict:
    config_path = os.path.join(os.path.dirname(_wallpaper_engine_exe()), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8-sig") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="无法读取 Wallpaper Engine 播放列表")


def _wallpaper_engine_selected() -> tuple[str, list[dict]]:
    """只读配置，返回最近选中的显示器壁纸和全部显示器配置。"""
    selected = []
    current = ""
    for account in _wallpaper_engine_config().values():
        if isinstance(account, dict):
            general = account.get("general", {})
            screens = general.get("wallpaperconfig", {}).get("selectedwallpapers", {})
            monitor = general.get("browser", {}).get("lastselectedmonitor", "")
            slot = screens.get(monitor) or next(iter(screens.values()), {})
            if slot:
                selected.append(slot)
            selected.extend(item for item in screens.values() if item is not slot)
            current = current or str(slot.get("file") or "")
    if not current:
        raise HTTPException(status_code=404, detail="Wallpaper Engine 中没有最近壁纸")
    return current, selected


def _wallpaper_engine_playlist(
    current: str, selected: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """读取本地播放列表，不向 Wallpaper Engine 发送切换命令。"""
    if selected is None:
        _selected_path, selected = _wallpaper_engine_selected()
    current_key = os.path.normcase(os.path.realpath(current))

    def contains_current(item: dict) -> bool:
        candidates = [item.get("file")] + item.get("playlist", {}).get("items", [])
        for candidate in candidates:
            candidate = str(candidate or "")
            if os.path.normcase(os.path.realpath(candidate)) == current_key:
                return True
            try:
                if os.path.normcase(_wallpaper_engine_media(candidate)[0]) == current_key:
                    return True
            except HTTPException:
                pass
        return False

    slot = next(
        (item for item in selected if contains_current(item)),
        selected[0] if selected else {},
    )
    items = slot.get("playlist", {}).get("items", [])
    media = []
    for item in items:
        try:
            resolved = _wallpaper_engine_media(str(item))
        except HTTPException:
            continue
        if resolved not in media:
            media.append(resolved)
    return media


def _wallpaper_engine_name(path: str, title: str = "") -> str:
    if not title:
        project = os.path.join(os.path.dirname(path), "project.json")
        try:
            with open(project, "r", encoding="utf-8-sig") as file:
                title = str(json.load(file).get("title") or "")
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    title = re.sub(r"\s+\(\d+\)$", "", title).strip()
    return title or os.path.splitext(os.path.basename(path))[0] or "未命名壁纸"


def _wallpaper_engine_url(path: str) -> str:
    """让 WebView 缓存键同时跟随文件和壁纸选择变化。"""
    stat = os.stat(path)
    identity = f"{os.path.normcase(os.path.realpath(path))}|{stat.st_mtime_ns}|{stat.st_size}"
    version = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"/api/wallpaper-engine/media?v={version}"


def _wallpaper_engine_recent_media(path: str) -> tuple[str, str, bool]:
    try:
        media, kind = _wallpaper_engine_media(path)
        return media, kind, False
    except HTTPException as error:
        if error.status_code != 415:
            raise
    media, kind = _wallpaper_engine_media(os.path.join(os.path.dirname(path), "preview.jpg"))
    return media, kind, True


def _wallpaper_engine_recent_from_config() -> list[dict]:
    rows = []
    for account in _wallpaper_engine_config().values():
        if not isinstance(account, dict):
            continue
        general = account.get("general", {})
        monitor = general.get("browser", {}).get("lastselectedmonitor", "")
        for recent in reversed(general.get("wallpaperconfigrecent", [])):
            screens = recent.get("config", {}).get("selectedwallpapers", {})
            slot = screens.get(monitor) or next(reversed(screens.values()), {})
            try:
                path, kind, preview = _wallpaper_engine_recent_media(
                    str(slot.get("file") or "")
                )
            except HTTPException:
                continue
            if any(item["path"] == path for item in rows):
                continue
            rows.append({
                "name": _wallpaper_engine_name(path, str(recent.get("title") or "")),
                "path": path,
                "kind": kind,
                "preview": preview,
            })
            if len(rows) == 10:
                return rows
    return rows


def _wallpaper_engine_recent_items() -> list[dict]:
    cfg = load_config()
    if not cfg.get("wall_engine_recent_initialized"):
        try:
            cfg["wall_engine_recent"] = _wallpaper_engine_recent_from_config()
            cfg["wall_engine_recent_initialized"] = True
            save_config(cfg)
        except HTTPException:
            return []
    rows = []
    for item in cfg.get("wall_engine_recent") or []:
        try:
            path, kind = _wallpaper_engine_media(str(item.get("path") or ""))
        except (AttributeError, HTTPException):
            continue
        if any(row["path"] == path for row in rows):
            continue
        rows.append({
            "name": str(item.get("name") or _wallpaper_engine_name(path)),
            "path": path,
            "kind": kind,
            "preview": bool(item.get("preview")),
        })
        if len(rows) == 10:
            break
    return rows


def _remember_wallpaper_engine(
    cfg: dict, path: str, kind: str, name: str = "", preview: bool = False,
) -> None:
    if not cfg.get("wall_engine_recent_initialized"):
        try:
            cfg["wall_engine_recent"] = _wallpaper_engine_recent_from_config()
        except HTTPException:
            cfg["wall_engine_recent"] = []
        cfg["wall_engine_recent_initialized"] = True
    row = {
        "name": name or _wallpaper_engine_name(path), "path": path,
        "kind": kind, "preview": preview,
    }
    cfg["wall_engine_recent"] = [row] + [
        item for item in cfg["wall_engine_recent"] if item.get("path") != path
    ][:9]
    cfg["wall_engine_media"] = path


def _wallpaper_engine_tray_exe() -> str:
    """仅在 Wallpaper Engine 正在托盘运行且托盘图标未禁用时返回程序路径。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\WallpaperEngine") as key:
            if int(winreg.QueryValueEx(key, "hideTrayIcon")[0]) == 1:
                return ""
    except (ImportError, OSError, TypeError, ValueError):
        pass

    base = os.path.dirname(_wallpaper_engine_exe())
    for name in ("wallpaper32.exe", "wallpaper64.exe"):
        path = os.path.join(base, name)
        if _process_running(name) and os.path.isfile(path):
            return path
    return ""


def _log_doc_event(session_id: str, subject: str, result: str) -> None:
    """把导入/上传动作写进会话历史：刷新或换会话后仍能看到，模型也读得到。"""
    if not session_id:
        return
    memory.save_message(session_id, "user", subject)
    memory.save_message(session_id, "assistant", result)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "nyalume.ico"))


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


@app.get("/api/permissions")
def get_permissions():
    """当前权限档：read_only / workspace / full。"""
    return {"mode": permission_mode()}


@app.put("/api/permissions")
def set_permissions(body: PermissionIn):
    mode = set_permission_mode(body.mode)
    if mode.startswith("无效"):
        raise HTTPException(status_code=400, detail=mode)
    return {"ok": True, "mode": mode}


@app.get("/api/sessions")
def list_sessions():
    """会话列表：标题取第一句用户消息，按最近活动倒序。"""
    return memory.list_sessions()


@app.get("/api/search/sessions")
def search_sessions(q: str = "", limit: int = 20):
    """按标题/消息内容搜会话，供侧边栏搜索直达。"""
    q = (q or "").strip()
    if not q:
        return []
    return memory.search_sessions(q, max(1, min(50, limit)))


@app.post("/api/sessions")
def new_session(body: SessionCreateIn | None = None):
    """新建会话（可挂到项目下），返回 {"id": ...}。"""
    project = (body.project if body else "") or ""
    return {"id": memory.create_session(project=project)}


@app.put("/api/sessions/{session_id}")
def update_session(session_id: str, body: SessionUpdateIn):
    """重命名 / 置顶 / 改所属项目。title 传空串表示回到“第一句消息”默认标题。"""
    memory.update_session(
        session_id,
        title=body.title,
        pinned=body.pinned,
        project=body.project,
    )
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """切换会话时回显历史消息（正序）。"""
    rows = memory.session_messages(session_id)
    runs = sorted(
        tracing.list_runs(session_id, limit=100), key=lambda item: item["started_at"]
    )
    used: set[str] = set()
    turn_started = 0.0
    for row in rows:
        if row["role"] == "user":
            turn_started = float(row.get("ts") or 0)
            continue
        candidates = [
            run for run in runs
            if run["run_id"] not in used
            and float(run.get("started_at") or 0) >= turn_started - 2
            and float(run.get("started_at") or 0) <= float(row.get("ts") or 0) + 1
        ]
        if candidates:
            run = candidates[-1]
            used.add(run["run_id"])
            row["trace"] = run
    return rows


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话及其消息与摘要。"""
    memory.delete_session(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/workspace/open")
def open_session_workspace(session_id: str):
    """打开这个会话实际使用的工作目录；纯聊天尚无目录时不创建空文件夹。"""
    root = session_work_root(session_id, create=False)
    if not root or not os.path.isdir(root):
        raise HTTPException(status_code=404, detail="这个会话还没有生成文件喵")
    os.startfile(root)  # noqa: 本地桌面应用，用户主动点击才调用
    return {"ok": True, "path": root}


@app.post("/api/sessions/{session_id}/workspace/archive")
def archive_session_workspace(session_id: str):
    """把普通会话的工作目录打成 zip 快照，原文件保持不动。"""
    if memory.session_project(session_id):
        raise HTTPException(status_code=400, detail="项目会话请直接用项目目录，不单独归档喵")
    lock = _session_run_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="这个会话还在工作，结束后再归档喵")
    try:
        root = session_work_root(session_id, create=False)
        if not root or not os.path.isdir(root):
            raise HTTPException(status_code=404, detail="这个会话还没有可归档的文件喵")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", memory.session_title(session_id))
        title = re.sub(r"\s+", " ", title).strip(" .")[:36] or "新对话"
        archive_dir = os.path.join(workspace_root(), "archives")
        os.makedirs(archive_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.join(archive_dir, f"{title}__{stamp}-{uuid.uuid4().hex[:4]}")
        path = shutil.make_archive(base, "zip", root_dir=root)
    finally:
        lock.release()
    os.startfile(archive_dir)  # noqa: 归档成功后让用户直接看到文件
    return {"ok": True, "path": path, "folder": archive_dir}


@app.post("/api/sessions/{session_id}/workspace/clean")
def clean_session_workspace_cache(session_id: str):
    """只删会话内临时脚本和常见缓存，不删正式产物。"""
    lock = _session_run_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="这个会话还在工作，结束后再清理喵")
    try:
        try:
            result = clean_session_workspace(session_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        lock.release()
    return {"ok": True, **result}


@app.post("/api/sessions/{session_id}/branch")
def branch_session(session_id: str, body: BranchIn):
    """把会话分支到截止某条消息：same=同工作区新会话 / worktree=新项目文件夹。"""
    project_id = memory.session_project(session_id)
    new_project_id = project_id
    if body.mode == "worktree":
        if not project_id:
            raise HTTPException(status_code=400, detail="当前会话不在项目里，无法建新工作树")
        project = memory.get_project(project_id)
        src = project["path"]
        stamp = time.strftime("%m%d-%H%M%S")
        dst = os.path.join(
            os.path.dirname(src),
            os.path.basename(src).rstrip("/\\") + "-branch-" + stamp,
        )
        ignore = shutil.ignore_patterns(
            ".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".webview"
        )
        shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=False)
        new_project = memory.add_project(dst, name=f"{project['name']}-分支")
        new_project_id = new_project["id"]
    target = memory.duplicate_session_until(
        session_id, body.message_id or 0, project=new_project_id
    )
    return {"id": target, "project": new_project_id}


@app.delete("/api/sessions/{session_id}/messages")
def delete_messages_after(session_id: str, message_id: int):
    """编辑重发：删除该消息之后的历史（磁盘文件不动）。"""
    memory.delete_messages_after(session_id, message_id)
    return {"ok": True}


@app.post("/api/approvals/{request_id}")
def decide_approval(request_id: str, body: ApprovalIn):
    """审批弹窗的结果：allow 放行 / deny 拒绝。"""
    decision = (
        body.decision
        if body.decision in ("allow", "deny", "allow_always")
        else "deny"
    )
    with _APPROVAL_LOCK:
        pending = _PENDING_APPROVALS.get(request_id)
        if not pending:
            raise HTTPException(status_code=404, detail="审批已过期或不存在")
        pending["decision"] = decision
    return {"ok": True}


@app.get("/api/undo")
def list_undo(session_id: str):
    """会话内最近可撤销的文件操作。"""
    return {"ops": memory.list_undo(session_id)}


@app.get("/api/undo/{op_id}/preview")
def undo_preview(op_id: str):
    return {"text": preview_undo(op_id)}


@app.get("/api/undo/{op_id}/diff")
def undo_diff(op_id: str):
    """改动统计 + 分块预览（供回复下方的文件改动区展示）。"""
    result = diff_undo(op_id)
    if result is None:
        raise HTTPException(status_code=404, detail="该操作不支持按行对比")
    return result


@app.post("/api/undo/{op_id}/undo")
def undo_action(op_id: str):
    result = apply_undo(op_id)
    ok = result.startswith("已撤销")
    return {"ok": ok, "result": result}


@app.post("/api/chat")
def chat(body: ChatIn):
    """SSE 流式聊天；遇到 approval 事件会挂起等用户点按钮再继续。"""

    run_lock = _session_run_lock(body.session_id)
    if not run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Nyalume 还在处理这个会话的上一项任务")
    run_request_id = re.sub(r"[^A-Za-z0-9_-]", "", body.request_id or "")[:80]
    run_request_id = run_request_id or uuid.uuid4().hex
    cancel_event = threading.Event()
    with _RUNS_LOCK:
        _RUN_CANCEL_EVENTS[run_request_id] = cancel_event

    def event_stream():
        gen = None
        _change_activity(1)
        try:
            gen = run_stream(body.session_id, body.message, cancel_event=cancel_event)
            while True:
                try:
                    set_session_context(body.session_id)
                    ev = next(gen)
                except StopIteration:
                    break
                if ev.get("type") == "approval":
                    approval_request_id = ev.get("request_id", "")
                    with _APPROVAL_LOCK:
                        _PENDING_APPROVALS[approval_request_id] = {"decision": None}
                    yield _frame(ev)
                    decision = _wait_approval(
                        approval_request_id, cancel_event=cancel_event
                    )
                    with _APPROVAL_LOCK:
                        _PENDING_APPROVALS.pop(approval_request_id, None)
                    try:
                        set_session_context(body.session_id)
                        ev = gen.send(decision)
                    except StopIteration:
                        break
                yield _frame(ev)
        finally:
            try:
                if gen is not None:
                    gen.close()
            except Exception:
                pass
            _change_activity(-1)
            with _RUNS_LOCK:
                _RUN_CANCEL_EVENTS.pop(run_request_id, None)
            run_lock.release()

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
    mode = permission_mode()
    return {
        "session_id": session_id,
        "summary": memory.get_summary(session_id),
        "notes": memory.get_recent_notes(8, session_id),
        "docs": memory.doc_list(session_id),
        "reminders": reminders.list_reminders(),
        "persona": personas.resolve_persona_id(),
        "persona_name": personas.current_persona_name(),
        "mode": mode,
        "affection": memory.get_daily_affection(session_id) if mode == "daily" else None,
    }


@app.get("/api/pet/state")
def pet_state():
    """按需读取桌宠概况，复用 Agent 的隐私白名单，不启动桌宠。"""
    return pet_status_snapshot()


@app.post("/api/pet/motion-check")
def check_pet_motions():
    return pet_check_motions()


class CompanionPromiseIn(BaseModel):
    content: str


class CompanionActionIn(BaseModel):
    action: str


@app.get("/api/companion")
def companion_entries(before: int = 0):
    return companionship.entries(max(0, before))


@app.post("/api/companion/promises")
def create_companion_promise(body: CompanionPromiseIn):
    try:
        return companionship.promise(body.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.patch("/api/companion/{entry_id}")
def update_companion_promise(entry_id: int, body: CompanionActionIn):
    try:
        return companionship.transition(entry_id, body.action)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/api/companion/{entry_id}")
def delete_companion_entry(entry_id: int):
    if not companionship.delete(entry_id):
        raise HTTPException(status_code=404, detail="记录不存在或已经删除")
    return {"ok": True}


@app.get("/api/docs")
def list_docs(session_id: str = ""):
    """文档库里已导入的来源（供 UI 侧栏展示）。"""
    return memory.doc_list(session_id)


# ---------- 记忆备份 ----------


@app.get("/api/backups")
def list_backups():
    return memory.list_backups()


@app.post("/api/backups")
def create_backup(body: BackupIn):
    path = memory.backup_db(body.name)
    return {"name": os.path.basename(path), "size": os.path.getsize(path)}


@app.post("/api/backups/restore")
def restore_backup(body: BackupIn):
    try:
        path = memory.restore_backup(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": path}


@app.post("/api/backups/open")
def open_backup_dir():
    os.makedirs(memory.BACKUP_DIR, exist_ok=True)
    os.startfile(memory.BACKUP_DIR)  # noqa: 单机本地应用，用户主动点击才调用
    return {"ok": True}


@app.get("/api/projects")
def list_projects():
    """项目列表（文件夹级工作区，含各自会话数）。"""
    return memory.list_projects()


@app.post("/api/projects")
def create_project(body: ProjectIn):
    """登记一个新项目文件夹（用户点击“添加项目”= 显式授权该目录内删改）。"""
    path = (body.path or "").strip().strip('"').strip("'")
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="请给绝对路径，例如 D:\\项目")
    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法创建目录：{e}")
    return memory.add_project(path, body.name)


@app.post("/api/projects/drop")
def create_drop_project(body: DropProjectIn):
    """浏览器拿不到原文件夹绝对路径时，在工作目录内接住并登记为项目。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(body.name.strip()))
    name = name.strip(" .")[:80] or "拖入的项目"
    parent = os.path.join(workspace_root(), "projects")
    os.makedirs(parent, exist_ok=True)
    target = os.path.join(parent, name)
    known = {os.path.normcase(p.get("path") or ""): p for p in memory.list_projects()}
    if os.path.normcase(target) in known:
        return known[os.path.normcase(target)]
    suffix = 2
    while os.path.exists(target):
        target = os.path.join(parent, f"{name}-{suffix}")
        suffix += 1
    return memory.add_project(target, name)


@app.put("/api/projects/{project_id}")
def rename_project(project_id: str, body: ProjectUpdateIn):
    if not memory.update_project(project_id, name=body.name, pinned=body.pinned):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    """删除项目登记（磁盘文件夹不动，会话退回“最近”）。"""
    memory.delete_project(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/folders")
def add_project_folder(project_id: str, body: ProjectFolderIn):
    """把另一个已存在目录并入项目授权范围。"""
    try:
        ok = memory.add_project_folder(project_id, body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True}


@app.delete("/api/projects/{project_id}/folders")
def remove_project_folder(project_id: str, folder: str):
    """把某目录移出授权范围（主目录不可移出，只能删整个项目）。"""
    try:
        ok = memory.remove_project_folder(project_id, folder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="项目或目录不在授权范围")
    return {"ok": True}


@app.post("/api/projects/{project_id}/open")
def open_project_folder(project_id: str):
    """在资源管理器里打开项目文件夹。"""
    project = memory.get_project(project_id)
    if not project.get("path") or not os.path.isdir(project["path"]):
        raise HTTPException(status_code=404, detail="项目文件夹不存在")
    os.startfile(project["path"])  # noqa: 单机本地应用，用户主动点击才调用
    return {"ok": True}


@app.post("/api/local/open")
def open_local_path(body: OpenPathIn):
    """用户点击工具路径后，用系统默认程序打开；仅允许当前授权目录。"""
    roots = [os.path.realpath(session_work_root(body.session_id))]
    project_id = memory.session_project(body.session_id) if body.session_id else ""
    if project_id:
        project = memory.get_project(project_id)
        project_roots = [os.path.realpath(p) for p in project.get("folders") or []]
        if project_roots:
            roots = project_roots
    raw = (body.path or "").strip().strip('"').strip("'")
    target = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(roots[0], raw))
    try:
        allowed = permission_mode() == "full" or any(
            os.path.commonpath([root, target]) == root for root in roots
        )
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(status_code=403, detail="只能打开当前工作目录或项目里的路径")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="文件或文件夹已经不存在")
    os.startfile(target)  # noqa: 本地桌面应用，且只响应用户主动点击
    return {"ok": True}


@app.post("/api/docs/import")
def import_docs(body: DocsImportIn):
    """把本地文件夹/文档/PDF 导入文档库，返回导入结果文本。"""
    from nyalume.core.tools import execute_tool

    set_session_context(body.session_id)
    try:
        result = execute_tool("add_documents", {"path": body.path})
    finally:
        clear_session_context()
    if result.startswith("已导入"):
        _log_doc_event(body.session_id, f"（导入资料）{body.path}", result)
    else:
        _log_doc_event(body.session_id, f"（尝试导入资料）{body.path}", result)
    return {"ok": result.startswith("已导入"), "result": result}


@app.post("/api/docs/upload")
async def upload_docs(
    files: list[UploadFile] = File(...),
    session_id: str = Form(""),
    project_id: str = Form(""),
    project_root: bool = Form(False),
):
    """拖入的文件/文件夹：默认存到工作目录 uploads/；选了项目则存入项目并挂靠会话。"""
    from nyalume.core.tools import execute_tool

    if project_id:
        project = memory.get_project(project_id)
        if not project.get("path"):
            raise HTTPException(status_code=404, detail="项目不存在")
        upload_root = project["path"] if project_root else os.path.join(project["path"], "uploads")
        if session_id:
            memory.update_session(session_id, project=project_id)
    else:
        upload_root = os.path.join(session_work_root(session_id), "uploads")
    saved: list[str] = []
    for upload in files:
        rel = (upload.filename or "").replace("\\", "/").strip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        target = os.path.join(upload_root, *parts)
        if os.path.commonpath([os.path.realpath(upload_root), os.path.realpath(target)]) != os.path.realpath(upload_root):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved.append("/".join(parts))
    if not saved:
        raise HTTPException(status_code=400, detail="没有收到可保存的文件")
    set_session_context(session_id)
    try:
        import_result = execute_tool("add_documents", {"path": upload_root})
    finally:
        clear_session_context()
    reply = f"已保存 {len(saved)} 个文件到工作目录 uploads/：\n- " + "\n- ".join(saved)
    reply += "\n" + import_result
    _log_doc_event(session_id, f"（拖入 {len(saved)} 个文件/文件夹内容）", reply)
    return {"ok": True, "result": reply}


@app.delete("/api/docs")
def delete_docs(source: str, session_id: str = ""):
    """按来源路径从文档库删除（磁盘文件保留）。"""
    if not memory.doc_delete(source, session_id):
        raise HTTPException(status_code=404, detail="文档不在文档库里")
    return {"ok": True}


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int, session_id: str = ""):
    if not memory.note_delete(note_id, session_id):
        raise HTTPException(status_code=404, detail=f"没有便签 #{note_id}")
    return {"ok": True}


@app.get("/api/wallpaper/settings")
def get_wallpaper_settings():
    """聊天窗壁纸设置（关窗后保留，由服务端持久化）。"""
    return _wallpaper_settings()


@app.get("/api/wallpaper-engine/recent")
def wallpaper_engine_recent():
    return [{
        "index": index, "name": item["name"], "kind": item["kind"],
        "preview": item["preview"],
    }
            for index, item in enumerate(_wallpaper_engine_recent_items())]


@app.delete("/api/wallpaper-engine/recent")
def clear_wallpaper_engine_recent():
    with _WALLPAPER_CONFIG_LOCK:
        cfg = load_config()
        cfg["wall_engine_recent"] = []
        cfg["wall_engine_recent_initialized"] = True
        save_config(cfg)
    return {"ok": True}


@app.post("/api/wallpaper-engine/{action}")
def wallpaper_engine_action(action: str, index: int = -1):
    """同步当前壁纸、切换下一张，或打开 Wallpaper Engine。"""
    global _we_media_path, _we_startup_available
    if action == "open":
        tray_exe = _wallpaper_engine_tray_exe()
        if not tray_exe:
            raise HTTPException(status_code=404, detail="托盘中不存在")
        try:
            os.startfile(tray_exe)
        except OSError as error:
            raise HTTPException(status_code=503, detail=f"无法打开 Wallpaper Engine：{error}")
        _we_startup_available = True
        return {"ok": True}
    if action not in ("current", "next", "recent"):
        raise HTTPException(status_code=404, detail="未知 Wallpaper Engine 操作")
    if action == "current":
        current, _selected = _wallpaper_engine_selected()
        _we_media_path, kind = _wallpaper_engine_media(current)
        name = _wallpaper_engine_name(_we_media_path)
        preview = False
    elif action == "next":
        selected_path, selected = _wallpaper_engine_selected()
        current = _we_media_path or selected_path
        playlist = _wallpaper_engine_playlist(current, selected)
        if not playlist:
            playlist = [(item["path"], item["kind"])
                        for item in _wallpaper_engine_recent_items()]
        if not playlist:
            raise HTTPException(status_code=404, detail="没有可切换的 JPG、其他图片或 MP4")
        paths = [item[0] for item in playlist]
        try:
            index = paths.index(os.path.realpath(current)) + 1
        except ValueError:
            index = 0
        _we_media_path, kind = playlist[index % len(playlist)]
        name = _wallpaper_engine_name(_we_media_path)
        preview = False
    else:
        recent = _wallpaper_engine_recent_items()
        if index < 0 or index >= len(recent):
            raise HTTPException(status_code=404, detail="这条最近壁纸已不存在")
        chosen = recent[index]
        _we_media_path, kind, name = chosen["path"], chosen["kind"], chosen["name"]
        preview = chosen["preview"]
    _we_startup_available = True
    with _WALLPAPER_CONFIG_LOCK:
        cfg = load_config()
        _remember_wallpaper_engine(cfg, _we_media_path, kind, name, preview)
        cfg["wall_mode"] = "engine"
        save_config(cfg)
    return {
        "ok": True,
        "kind": kind,
        "url": _wallpaper_engine_url(_we_media_path),
        "name": name,
    }


@app.get("/api/wallpaper-engine/media")
def wallpaper_engine_media_file():
    path = _we_media_path or load_config().get("wall_engine_media") or ""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="尚未同步 Wallpaper Engine 壁纸")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.post("/api/wallpaper/settings")
def save_wallpaper_settings(body: WallpaperIn):
    with _WALLPAPER_CONFIG_LOCK:
        cfg = load_config()
        cfg["wall_mode"] = body.mode
        cfg["wall_opacity"] = max(10, min(100, int(body.opacity)))
        save_config(cfg)
    return {"ok": True}


@app.post("/api/wallpaper/custom")
async def upload_custom_wallpaper(file: UploadFile = File(...)):
    """上传自定义聊天壁纸：文件落盘 + 记录设置，关窗重开仍保留。"""
    base = _nyalume_dir()
    if not base:
        raise HTTPException(status_code=404, detail="当前皮肤没有素材目录")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    target = os.path.join(base, f"chat_wallpaper_custom{ext}")
    with open(target, "wb") as f:
        shutil.copyfileobj(file.file, f)
    with _WALLPAPER_CONFIG_LOCK:
        cfg = load_config()
        cfg["wall_custom"] = os.path.basename(target)
        cfg["wall_mode"] = "custom"
        cfg["wall_opacity"] = int(cfg.get("wall_opacity", 70))
        save_config(cfg)
    return {"ok": True, "url": f"/api/wallpaper/file/{os.path.basename(target)}"}


@app.get("/api/wallpaper/file/{name}")
def wallpaper_file(name: str):
    base = _nyalume_dir()
    safe = os.path.basename(name)
    path = os.path.join(base, safe)
    if not base or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="壁纸文件不存在")
    return FileResponse(path)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
