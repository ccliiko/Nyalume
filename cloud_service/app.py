"""Central FastAPI + PostgreSQL service for Nyalume accounts and sync."""

from __future__ import annotations

import json
import os
import re
import secrets
import smtplib
import uuid
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .security import (
    TokenError,
    create_token,
    hash_password,
    read_token,
    reset_code_digest,
    verify_password,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SYNC_TYPES = {"session", "message", "summary", "note", "daily_nyalume", "preference"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]{2,24}$")
bearer = HTTPBearer(auto_error=False)


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://nyalume:nyalume@127.0.0.1:5432/nyalume")


def _token_secret() -> str:
    return os.getenv("NYALUME_TOKEN_SECRET", "")


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _init_schema() -> None:
    secret = _token_secret()
    if len(secret) < 32:
        raise RuntimeError("NYALUME_TOKEN_SECRET must contain at least 32 characters")
    with _connect() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_schema()
    yield


app = FastAPI(title="Nyalume Cloud", version="0.1.0", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("NYALUME_CORS_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class DeviceIn(BaseModel):
    device_id: uuid.UUID | None = None
    device_name: str = Field(default="Nyalume desktop", min_length=1, max_length=80)


class RegisterIn(DeviceIn):
    username: str = Field(min_length=2, max_length=24)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(DeviceIn):
    account: str = Field(min_length=2, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class ResetRequestIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class ResetConfirmIn(ResetRequestIn):
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)


class SyncItem(BaseModel):
    mutation_id: uuid.UUID
    record_type: str = Field(min_length=1, max_length=40)
    record_id: str = Field(min_length=1, max_length=200)
    payload: dict = Field(default_factory=dict)
    deleted: bool = False


class SyncPushIn(BaseModel):
    records: list[SyncItem] = Field(min_length=1, max_length=200)


class FeedbackIn(BaseModel):
    category: Literal["bug", "suggestion", "other"] = "suggestion"
    title: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=2, max_length=8000)


def _normal_email(value: str) -> str:
    email = value.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    return email


def _normal_username(value: str) -> tuple[str, str]:
    username = value.strip()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=422, detail="用户名需为 2～24 位中文、字母、数字、_ 或 -")
    return username, username.casefold()


def _issue_session(
    conn, user_id: uuid.UUID, email: str, username: str, body: DeviceIn
) -> dict:
    device_id = body.device_id or uuid.uuid4()
    row = conn.execute("SELECT user_id FROM devices WHERE id = %s", (device_id,)).fetchone()
    if row and row["user_id"] != user_id:
        raise HTTPException(status_code=409, detail="设备标识已被占用")
    conn.execute(
        """INSERT INTO devices (id, user_id, name, last_seen_at, revoked_at)
           VALUES (%s, %s, %s, now(), NULL)
           ON CONFLICT (id) DO UPDATE
           SET name = EXCLUDED.name, last_seen_at = now(), revoked_at = NULL""",
        (device_id, user_id, body.device_name.strip()),
    )
    return {
        "access_token": create_token(str(user_id), str(device_id), _token_secret()),
        "token_type": "bearer",
        "user": {"id": str(user_id), "username": username, "email": email},
        "device_id": str(device_id),
    }


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def _send_reset_code(email: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    message = EmailMessage()
    message["Subject"] = "Nyalume 找回密码验证码"
    message["From"] = os.getenv("SMTP_FROM", "")
    message["To"] = email
    message.set_content(f"你的 Nyalume 验证码是：{code}\n\n10 分钟内有效。若不是你本人操作，请忽略此邮件。")
    smtp_cls = smtplib.SMTP_SSL if os.getenv("SMTP_USE_SSL") == "1" else smtplib.SMTP
    with smtp_cls(host, port, timeout=10) as smtp:
        if smtp_cls is smtplib.SMTP and os.getenv("SMTP_STARTTLS", "1") == "1":
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        claims = read_token(credentials.credentials, _token_secret())
    except TokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    try:
        user_id, device_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["device"])
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    with _connect() as conn:
        row = conn.execute(
            """SELECT u.id, u.username, u.email, d.id AS device_id
               FROM users u JOIN devices d ON d.user_id = u.id
               WHERE u.id = %s AND d.id = %s AND d.revoked_at IS NULL""",
            (user_id, device_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
        conn.execute("UPDATE devices SET last_seen_at = now() WHERE id = %s", (device_id,))
    return row


@app.get("/health")
def health():
    with _connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: RegisterIn):
    email = _normal_email(body.email)
    username, username_key = _normal_username(body.username)
    user_id = uuid.uuid4()
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO users (id, username, username_key, email, password_hash)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, username, username_key, email, hash_password(body.password)),
            )
            return _issue_session(conn, user_id, email, username, body)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="用户名或邮箱已经注册过啦")


@app.post("/api/v1/auth/login")
def login(body: LoginIn):
    account = body.account.strip()
    with _connect() as conn:
        user = conn.execute(
            """SELECT id, username, email, password_hash FROM users
               WHERE email = %s OR username_key = %s""",
            (account.lower(), account.casefold()),
        ).fetchone()
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名、邮箱或密码不对喵")
        return _issue_session(
            conn, user["id"], user["email"], user["username"], body
        )


@app.post("/api/v1/auth/password-reset/request", status_code=202)
def request_password_reset(body: ResetRequestIn):
    if not _smtp_configured():
        raise HTTPException(status_code=503, detail="邮件服务尚未配置")
    email = _normal_email(body.email)
    with _connect() as conn:
        user = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        if not user:
            return {"ok": True, "message": "如果邮箱已注册，验证码会很快到达"}
        recent = conn.execute(
            """SELECT 1 FROM password_reset_codes
               WHERE user_id = %s AND created_at > now() - interval '60 seconds'
               LIMIT 1""",
            (user["id"],),
        ).fetchone()
        if recent:
            return {"ok": True, "message": "验证码已经发送，请稍后再试"}
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            _send_reset_code(email, code)
        except (OSError, smtplib.SMTPException):
            raise HTTPException(status_code=502, detail="验证码没发出去，请稍后再试")
        conn.execute(
            """INSERT INTO password_reset_codes (id, user_id, code_hash, expires_at)
               VALUES (%s, %s, %s, now() + interval '10 minutes')""",
            (uuid.uuid4(), user["id"], reset_code_digest(str(user["id"]), code, _token_secret())),
        )
    return {"ok": True, "message": "如果邮箱已注册，验证码会很快到达"}


@app.post("/api/v1/auth/password-reset/confirm")
def confirm_password_reset(body: ResetConfirmIn):
    email = _normal_email(body.email)
    with _connect() as conn:
        row = conn.execute(
            """SELECT r.id, r.user_id, r.code_hash FROM password_reset_codes r
               JOIN users u ON u.id = r.user_id
               WHERE u.email = %s AND r.used_at IS NULL AND r.expires_at > now()
                 AND r.attempts < 5
               ORDER BY r.created_at DESC LIMIT 1""",
            (email,),
        ).fetchone()
        if not row or not secrets.compare_digest(
            row["code_hash"], reset_code_digest(str(row["user_id"]), body.code, _token_secret())
        ):
            if row:
                conn.execute(
                    "UPDATE password_reset_codes SET attempts = attempts + 1 WHERE id = %s",
                    (row["id"],),
                )
            raise HTTPException(status_code=400, detail="验证码不正确或已经过期")
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(body.new_password), row["user_id"]),
        )
        conn.execute("UPDATE password_reset_codes SET used_at = now() WHERE id = %s", (row["id"],))
        conn.execute("UPDATE devices SET revoked_at = now() WHERE user_id = %s", (row["user_id"],))
    return {"ok": True}


@app.get("/api/v1/me")
def me(account: dict = Depends(current_account)):
    return {
        "id": str(account["id"]),
        "username": account["username"],
        "email": account["email"],
        "device_id": str(account["device_id"]),
    }


@app.delete("/api/v1/auth/logout", status_code=204)
def logout(account: dict = Depends(current_account)):
    with _connect() as conn:
        conn.execute(
            "UPDATE devices SET revoked_at = now() WHERE id = %s", (account["device_id"],)
        )


@app.post("/api/v1/sync/push")
def sync_push(body: SyncPushIn, account: dict = Depends(current_account)):
    user_id = account["id"]
    applied = []
    with _connect() as conn:
        for item in body.records:
            if item.record_type not in SYNC_TYPES:
                raise HTTPException(status_code=422, detail=f"不支持同步 {item.record_type}")
            if len(json.dumps(item.payload, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
                raise HTTPException(status_code=413, detail="单条同步记录不能超过 256KB")
            mutation = conn.execute(
                """INSERT INTO sync_mutations
                       (user_id, mutation_id, record_type, record_id, revision)
                   VALUES (%s, %s, %s, %s, nextval('sync_revision_seq'))
                   ON CONFLICT (user_id, mutation_id) DO NOTHING
                   RETURNING record_type, record_id, revision""",
                (user_id, item.mutation_id, item.record_type, item.record_id),
            ).fetchone()
            if not mutation:
                mutation = conn.execute(
                    """SELECT record_type, record_id, revision FROM sync_mutations
                       WHERE user_id = %s AND mutation_id = %s""",
                    (user_id, item.mutation_id),
                ).fetchone()
                if (
                    mutation["record_type"] != item.record_type
                    or mutation["record_id"] != item.record_id
                ):
                    raise HTTPException(status_code=409, detail="mutation_id 已用于其他记录")
                applied.append({"mutation_id": str(item.mutation_id), "revision": mutation["revision"]})
                continue
            conn.execute(
                """INSERT INTO sync_records
                       (user_id, record_type, record_id, payload, deleted, revision)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, record_type, record_id) DO UPDATE
                   SET payload = EXCLUDED.payload, deleted = EXCLUDED.deleted,
                       revision = EXCLUDED.revision, updated_at = now()""",
                (
                    user_id,
                    item.record_type,
                    item.record_id,
                    Jsonb(item.payload),
                    item.deleted,
                    mutation["revision"],
                ),
            )
            applied.append({"mutation_id": str(item.mutation_id), "revision": mutation["revision"]})
    return {"applied": applied}


@app.get("/api/v1/sync/pull")
def sync_pull(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    account: dict = Depends(current_account),
):
    with _connect() as conn:
        rows = conn.execute(
            """SELECT record_type, record_id, payload, deleted, revision, updated_at
               FROM sync_records WHERE user_id = %s AND revision > %s
               ORDER BY revision ASC LIMIT %s""",
            (account["id"], since, limit + 1),
        ).fetchall()
    page = rows[:limit]
    return {
        "records": page,
        "next_cursor": page[-1]["revision"] if page else since,
        "has_more": len(rows) > limit,
    }


@app.post("/api/v1/feedback", status_code=201)
def create_feedback(body: FeedbackIn, account: dict = Depends(current_account)):
    with _connect() as conn:
        row = conn.execute(
            """INSERT INTO feedback (user_id, category, title, body)
               VALUES (%s, %s, %s, %s)
               RETURNING id, status, created_at""",
            (account["id"], body.category, body.title.strip(), body.body.strip()),
        ).fetchone()
    return row
