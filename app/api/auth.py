"""鉴权：Bearer 静态令牌（脚本 / 测试 / 服务端调用）或浏览器会话 Cookie（React 前端）。

浏览器端绝不持有 API_TOKEN：登录后只得到一个 HttpOnly + SameSite=Strict 的随机会话 ID，
会话存在库里（只存哈希），写操作还必须带与会话绑定的 CSRF 令牌（X-CSRF-Token）。
会话请求的“操作人”取自会话里的用户名，客户端不能自行声明。
"""
import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.db.models import UISession

COOKIE = "wb_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_bearer = HTTPBearer(auto_error=False)
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
_DUMMY_HASH = "scrypt$" + base64.b64encode(b"0" * 16).decode() + "$" + base64.b64encode(b"0" * 32).decode()
MAX_FAILS, FAIL_WINDOW_S = 5, 300


# ---------- 口令哈希（标准库 scrypt）----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split("$")
        salt, want = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    except ValueError:
        return False
    got = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return scheme == "scrypt" and hmac.compare_digest(got, want)


def parse_users(raw: str) -> dict[str, str]:
    """UI_USERS="alice:scrypt$..$..;bob:scrypt$..$.." -> {name: hash}。格式错误的条目直接忽略。"""
    users: dict[str, str] = {}
    for item in raw.split(";"):
        name, sep, h = item.strip().partition(":")
        if sep and name.strip() and h.startswith("scrypt$"):
            users[name.strip()] = h.strip()
    return users


# ---------- 会话 ----------
@dataclass
class SessionInfo:
    username: str
    csrf_token: str


def _hash_id(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def current_session(request: Request) -> SessionInfo | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    with request.app.state.session_factory() as db:
        row = db.get(UISession, _hash_id(raw))
        if row is None:
            return None
        if _utc(row.expires_at) <= datetime.now(timezone.utc):
            db.delete(row)
            db.commit()
            return None
        return SessionInfo(row.username, row.csrf_token)


def session_key_of(request: Request) -> str | None:
    """当前登录会话的标识（Cookie 的 SHA-256）；Bearer 调用返回 None。用于把待确认动作绑定到会话。"""
    return getattr(request.state, "session_key", None)


def operator_name(request: Request) -> str | None:
    """已登录会话的用户名（客户端无法伪造）；Bearer 调用返回 None。"""
    return getattr(request.state, "user", None)


async def require_token(request: Request) -> None:
    creds: HTTPAuthorizationCredentials | None = await _bearer(request)
    if creds is not None and hmac.compare_digest(creds.credentials, request.app.state.settings.api_token):
        return
    sess = current_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="invalid or missing token")
    if request.method not in SAFE_METHODS and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", ""), sess.csrf_token):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
    request.state.user = sess.username
    request.state.session_key = _hash_id(request.cookies[COOKIE])


async def require_agent_session(request: Request) -> None:
    """Agent 只接受浏览器登录会话：静态 Bearer 令牌即使有效也没有操作人，不允许使用 Agent。（须排在 require_token 之后）"""
    if not operator_name(request) or not session_key_of(request):
        raise HTTPException(status_code=403, detail={"error_code": "session_required",
                                                      "message": "Agent 只接受浏览器登录会话，不接受静态令牌"})


# ---------- 路由 ----------
router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _client_key(request: Request, username: str) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return f"{username.lower()}|{fwd or (request.client.host if request.client else '-')}"


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    state = request.app.state
    users = parse_users(state.settings.ui_users)
    fails: dict[str, list[float]] = state.__dict__.setdefault("login_fails", {})
    key, now = _client_key(request, body.username), time.time()
    recent = [t for t in fails.get(key, []) if now - t < FAIL_WINDOW_S]
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, detail={"error_code": "too_many_attempts", "message": "尝试次数过多，请 5 分钟后再试"})
    stored = users.get(body.username)
    ok = verify_password(body.password, stored or _DUMMY_HASH) and stored is not None   # 用户不存在时也做一次哈希，耗时一致
    if not ok:
        fails[key] = recent + [now]
        raise HTTPException(401, detail={"error_code": "invalid_credentials", "message": "用户名或口令不正确"})
    fails.pop(key, None)
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    ttl = timedelta(hours=state.settings.session_ttl_hours)
    with state.session_factory() as db:
        db.add(UISession(id_hash=_hash_id(raw), username=body.username, csrf_token=csrf,
                         expires_at=datetime.now(timezone.utc) + ttl))
        db.commit()
    response.set_cookie(COOKIE, raw, max_age=int(ttl.total_seconds()), httponly=True, samesite="strict",
                        secure=_secure(request), path="/")
    request.state.user = body.username
    return {"username": body.username, "csrf_token": csrf}


@router.get("/me")
def me(request: Request) -> dict:
    sess = current_session(request)
    if sess is None:
        raise HTTPException(401, detail="not signed in")
    s = request.app.state.settings
    return {"username": sess.username, "csrf_token": sess.csrf_token,
            "llm_enabled": s.llm_provider != "deterministic",
            "llm_model": s.llm_model if s.llm_provider == "deepseek" else None}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    raw = request.cookies.get(COOKIE)
    if raw:
        sess = current_session(request)
        if sess is not None and not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), sess.csrf_token):
            raise HTTPException(403, detail="missing or invalid CSRF token")
        with request.app.state.session_factory() as db:
            row = db.get(UISession, _hash_id(raw))
            if row is not None:
                db.delete(row)
                db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
