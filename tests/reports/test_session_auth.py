"""浏览器会话登录：口令哈希、Cookie 属性、CSRF、操作人取自会话、限速、撤销，以及前端静态托管的安全头。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import COOKIE, hash_password, parse_users, verify_password
from app.db.models import UISession
from app.main import create_app
from app.settings import Settings
from seed.loader import seed_database

PW = "correct-horse-battery"


@pytest.fixture
def web(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>wb</title>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'w.db').as_posix()}", api_token="static-token-xyz",
                 audit_jsonl_dir=str(tmp_path / "audit"), reports_dir=str(tmp_path / "reports"),
                 ui_users=f"alice:{hash_password(PW)};bob:{hash_password('another-password-1')}",
                 web_dist_dir=str(dist))
    app = create_app(s)
    with app.state.session_factory() as db:
        seed_database(db)
    return TestClient(app)


def login(c, user="alice", pw=PW):
    return c.post("/api/auth/login", json={"username": user, "password": pw})


def csrf_of(c):
    return c.get("/api/auth/me").json()["csrf_token"]


# ---------- 哈希 ----------
def test_password_hash_roundtrip_and_user_parsing():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h and h != hash_password(PW)      # 随机盐
    assert verify_password(PW, h) and not verify_password("wrong", h) and not verify_password(PW, "garbage")
    users = parse_users(f"a:{h};broken;:x;c:plain;b:{h}")
    assert sorted(users) == ["a", "b"]


# ---------- 登录 / Cookie ----------
def test_login_sets_httponly_strict_cookie_and_never_returns_api_token(web):
    r = login(web)
    assert r.status_code == 200 and r.json()["username"] == "alice" and r.json()["csrf_token"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "static-token-xyz" not in r.text and "static-token-xyz" not in web.get("/api/auth/me").text
    assert "static-token-xyz" not in r.headers["set-cookie"]


def test_cookie_is_secure_behind_https_proxy(web):
    r = web.post("/api/auth/login", json={"username": "alice", "password": PW}, headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_session_id_is_stored_only_as_hash(web):
    login(web)
    raw = web.cookies.get(COOKIE)
    with web.app.state.session_factory() as db:
        rows = db.scalars(select(UISession)).all()
    assert len(rows) == 1 and rows[0].id_hash != raw and raw not in rows[0].id_hash and len(rows[0].id_hash) == 64


@pytest.mark.parametrize("user,pw", [("alice", "wrong-password"), ("nobody", PW), ("alice", "x")])
def test_bad_credentials_are_rejected_without_hinting_which_part(web, user, pw):
    r = login(web, user, pw)
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "invalid_credentials"
    assert COOKIE not in r.headers.get("set-cookie", "")


def test_login_is_rate_limited(web):
    for _ in range(5):
        assert login(web, pw="bad-password").status_code == 401
    r = login(web)                                         # 即使口令正确，窗口内也被限速
    assert r.status_code == 429 and r.json()["detail"]["error_code"] == "too_many_attempts"
    assert login(web, "bob", "another-password-1").status_code == 200       # 其它用户不受影响


def test_no_users_configured_means_nobody_can_log_in(tmp_path):
    c = TestClient(create_app(Settings(database_url=f"sqlite:///{(tmp_path / 'n.db').as_posix()}", api_token="t",
                                       audit_jsonl_dir=str(tmp_path / "a"), web_dist_dir=str(tmp_path / "none"))))
    assert c.post("/api/auth/login", json={"username": "alice", "password": PW}).status_code == 401


# ---------- 访问控制 ----------
def test_protected_routes_need_token_or_session(web):
    assert web.get("/api/projects").status_code == 401
    assert web.get("/api/auth/me").status_code == 401
    assert web.get("/api/projects", headers={"Authorization": "Bearer static-token-xyz"}).status_code == 200   # 原有鉴权保留
    assert web.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert web.get("/api/health").status_code == 200                                                          # 健康检查仍公开
    login(web)
    assert web.get("/api/projects").status_code == 200


def test_unsafe_requests_with_session_require_csrf(web):
    login(web)
    body = {"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": []}
    assert web.post("/api/reports/draft", json=body).status_code == 403
    assert web.post("/api/reports/draft", json=body, headers={"X-CSRF-Token": "forged"}).status_code == 403
    r = web.post("/api/reports/draft", json=body, headers={"X-CSRF-Token": csrf_of(web)})
    assert r.status_code == 200
    # Bearer 调用不需要 CSRF（不依赖 Cookie，不存在被跨站利用的问题）
    other = TestClient(web.app)
    assert other.post("/api/reports/draft", json=body, headers={"Authorization": "Bearer static-token-xyz"}).status_code == 200


def test_operator_comes_from_session_not_from_client_header(web):
    login(web)
    h = {"X-CSRF-Token": csrf_of(web), "X-Operator": "spoofed-person"}
    did = web.post("/api/reports/draft", json={"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": []},
                   headers=h).json()["draft_id"]
    assert web.post(f"/api/reports/{did}/transition", json={"action": "start_review"}, headers=h).status_code == 200
    events = web.get(f"/api/reports/{did}/events").json()
    assert {e["operator"] for e in events} == {"alice"}
    rid = web.post(f"/api/reports/{did}/comments", json={"kind": "comment", "text": "ok"}, headers=h).headers["x-request-id"]
    with web.app.state.session_factory() as db:
        from app.audit.models import AuditLog
        assert db.scalars(select(AuditLog.operator).where(AuditLog.request_id == rid)).first() == "alice"


def test_review_actions_work_for_session_user_without_any_operator_header(web):
    login(web)
    h = {"X-CSRF-Token": csrf_of(web)}
    did = web.post("/api/reports/draft", json={"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": []},
                   headers=h).json()["draft_id"]
    assert web.post(f"/api/reports/{did}/transition", json={"action": "start_review"}, headers=h).status_code == 200
    assert web.get(f"/api/reports/{did}/download").status_code == 409                    # 门禁仍然生效


# ---------- 登出 / 过期 ----------
def test_logout_revokes_session_server_side(web):
    login(web)
    raw = web.cookies.get(COOKIE)
    assert web.post("/api/auth/logout").status_code == 403                                # 登出也要 CSRF
    assert web.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_of(web)}).status_code == 200
    stale = TestClient(web.app)
    stale.cookies.set(COOKIE, raw)
    assert stale.get("/api/projects").status_code == 401                                  # 旧 Cookie 在服务端已失效


def test_expired_session_is_rejected_and_removed(web):
    login(web)
    with web.app.state.session_factory() as db:
        row = db.scalars(select(UISession)).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert web.get("/api/projects").status_code == 401
    with web.app.state.session_factory() as db:
        assert db.scalars(select(UISession)).first() is None


# ---------- 前端静态托管 ----------
def test_spa_is_served_same_origin_with_strict_csp(web):
    for path in ("/", "/reports/DRAFT-1", "/qa"):
        r = web.get(path)
        assert r.status_code == 200 and "<title>wb</title>" in r.text
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp and "http" not in csp
        assert r.headers["x-content-type-options"] == "nosniff"
    assert web.get("/assets/app-abc123.js").status_code == 200


def test_spa_fallback_does_not_swallow_api_or_leak_files(web):
    assert web.get("/api/does-not-exist").status_code in (401, 404)
    r = web.get("/api/does-not-exist", headers={"Authorization": "Bearer static-token-xyz"})
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    for evil in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/../secret.txt"):
        assert "outside" not in web.get(evil).text
    assert web.get("/docs").status_code == 200                                            # FastAPI 文档路由不被前端回落吞掉
