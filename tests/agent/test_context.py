"""AgentContext：操作人与会话只来自浏览器登录会话；依赖复用 app.state。"""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.agent.context import AgentAuthError, AgentContext
from app.api.auth import COOKIE, hash_password, require_token
from app.main import create_app
from app.settings import Settings


def fake_request(user=None, key=None):
    st = SimpleNamespace()
    if user is not None:
        st.user = user
    if key is not None:
        st.session_key = key
    return SimpleNamespace(state=st, app=SimpleNamespace(state=SimpleNamespace(settings="S")))


def test_context_has_no_default_operator_and_no_grant(app):
    c = AgentContext(state=app.state, operator="")
    assert c.operator == "" and c.grant is None and c.session_key is None


@pytest.mark.parametrize("user,key", [(None, None), (None, "k"), ("alice", None), ("anonymous", "k"), ("", "k")])
def test_from_request_rejects_anything_but_a_logged_in_session(user, key):
    with pytest.raises(AgentAuthError):
        AgentContext.from_request(fake_request(user, key))


def test_from_request_takes_operator_and_session_from_the_trusted_state():
    c = AgentContext.from_request(fake_request("alice", "hash-1"))
    assert (c.operator, c.session_key, c.grant) == ("alice", "hash-1", None)


def test_dependencies_are_the_ones_on_app_state(app, ctx):
    assert ctx.settings is app.state.settings and ctx.session_factory is app.state.session_factory
    assert ctx.rule_sets is app.state.rule_sets and ctx.assistant is app.state.assistant
    assert ctx.translation_service is app.state.translation_service


@pytest.fixture
def probe(tmp_path):
    """真实的登录会话 + 一个探测路由：验证 operator 只来自会话，静态 Bearer 与 X-Operator 都不行。"""
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}", api_token="static-token-xyz",
                 audit_jsonl_dir=str(tmp_path / "a"), reports_dir=str(tmp_path / "r"), web_dist_dir=str(tmp_path / "none"),
                 ui_users=f"alice:{hash_password('correct-horse-battery')}")
    app: FastAPI = create_app(s)

    @app.get("/_probe", dependencies=[Depends(require_token)])
    def _probe(request: Request):
        try:
            c = AgentContext.from_request(request)
        except AgentAuthError:
            return {"ok": False}
        return {"ok": True, "operator": c.operator, "session_key": c.session_key}
    return TestClient(app)


def test_static_bearer_token_has_no_agent_identity(probe):
    r = probe.get("/_probe", headers={"Authorization": "Bearer static-token-xyz", "X-Operator": "mallory"})
    assert r.status_code == 200 and r.json() == {"ok": False}


def test_session_operator_ignores_spoofed_header_and_binds_to_the_cookie(probe):
    assert probe.post("/api/auth/login", json={"username": "alice", "password": "correct-horse-battery"}).status_code == 200
    r = probe.get("/_probe", headers={"X-Operator": "mallory"}).json()
    assert r["ok"] is True and r["operator"] == "alice"
    assert r["session_key"] == hashlib.sha256(probe.cookies.get(COOKIE).encode()).hexdigest()
