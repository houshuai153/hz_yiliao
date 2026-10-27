"""阶段 5 验收：跨模块的端到端检查（脚本化模拟模型，离线；不代表真实 DeepSeek 已验证）。"""
import json
import re
from pathlib import Path

from sqlalchemy import text as sql

from app.settings import ROOT
from tests.agent.agent_helpers import DEMO, PW, demo_script

# 阶段 1–4 之前就存在的接口（路径 + 方法）。Agent 层不得改动或删掉其中任何一个。
LEGACY_ROUTES = {
    "/api/health": {"get"}, "/api/projects": {"get"}, "/api/projects/{project_id}": {"get"},
    "/api/projects/{project_id}/runs": {"get"}, "/api/runs/{run_id}": {"get"},
    "/api/dashboard/charts": {"get"}, "/api/dashboard/overview": {"get"}, "/api/dashboard/projects/{project_id}": {"get"},
    "/api/regulatory/ask": {"post"}, "/api/translate": {"post"}, "/api/translate/capabilities": {"get"},
    "/api/reports": {"get"}, "/api/reports/draft": {"post"}, "/api/reports/{draft_id}": {"get"},
    "/api/reports/{draft_id}/transition": {"post"}, "/api/reports/{draft_id}/download": {"get"},
    "/api/reports/{draft_id}/events": {"get"}, "/api/reports/{draft_id}/versions": {"get"},
    "/api/audit": {"get"}, "/api/auth/login": {"post"}, "/api/auth/logout": {"post"}, "/api/auth/me": {"get"},
}


def test_legacy_routes_are_still_served(web_app):
    paths = web_app.openapi()["paths"]
    for path, methods in LEGACY_ROUTES.items():
        assert path in paths and methods <= set(paths[path]), path


def test_agent_layer_has_no_orchestration_framework():
    pat = re.compile(r"^\s*(?:from|import)\s+(langchain|langgraph|autogen|crewai)", re.M)
    for f in (ROOT / "app").rglob("*.py"):
        assert not pat.search(f.read_text(encoding="utf-8")), f


def test_no_credential_or_token_lands_in_any_agent_store(web_app, web_settings, alice, agent):
    """静态令牌、登录口令、CSRF 令牌、会话 Cookie 值：全流程后不得出现在 Agent 表、审计表或 JSONL 里。"""
    agent(demo_script())
    snap = alice.say(DEMO).json()
    p = snap["pending_actions"][0]
    alice.confirm(snap["task_id"], p["action_id"], p["args_hash"])
    secrets = {"static-token-xyz", PW, alice.csrf, alice.c.cookies.get("wb_session")}
    assert all(secrets)
    blob = ""
    with web_app.state.session_factory() as s:
        tables = [t for t in s.execute(sql("select name from sqlite_master where type='table'")).scalars()
                  if t.startswith("agent_") or t == "audit_log"]
        assert {"agent_task", "agent_step", "agent_message", "audit_log"} <= set(tables)
        for t in tables:
            blob += json.dumps([list(map(str, r)) for r in s.execute(sql(f"select * from {t}"))], ensure_ascii=False)
    for f in Path(web_settings.audit_jsonl_dir).glob("*.jsonl"):
        blob += f.read_text(encoding="utf-8")
    for secret in secrets:
        assert secret not in blob
