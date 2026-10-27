"""Agent 编排测试夹具：真实的登录会话 + 脚本化的模拟模型（全部离线，不联网，不是真实 DeepSeek）。"""
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.api.auth import hash_password
from app.bootstrap import initialize
from app.main import create_app
from app.settings import ROOT, Settings

PW = "correct-horse-battery"


# ---------- 模型响应构造 ----------
def text(content: str, tokens: int = 40) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"total_tokens": tokens}}


def calls(*items, tokens: int = 40) -> dict:
    """items: (call_id, tool_name, args_dict)"""
    tcs = [{"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
           for i, n, a in items]
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tcs}, "finish_reason": "tool_calls"}],
            "usage": {"total_tokens": tokens}}


def delegate(cid: str, role: str, instruction: str, tokens: int = 40) -> dict:
    return calls((cid, "delegate", {"role": role, "instruction": instruction}), tokens=tokens)


class FakeClient:
    """按脚本依次返回响应；记录每次收到的 messages / tools，供断言“发给模型的内容”。"""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat_with_tools(self, messages, tools, *, max_tokens=1024):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if not self.script:
            raise AssertionError("模拟模型的脚本已用完（编排多调用了一次模型）")
        item = self.script.pop(0)
        return item(messages, tools) if callable(item) else item

    def tool_names(self, i: int) -> set[str]:
        return {t["function"]["name"] for t in self.calls[i]["tools"]}

    def sent_text(self) -> str:
        return json.dumps(self.calls, ensure_ascii=False)


def last_tool_json(messages) -> dict:
    return json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])


def find_draft_id(messages) -> str:
    blob = json.dumps(messages, ensure_ascii=False)
    return re.search(r"DRAFT-(?:VAL|SMP)-\d{14}-[0-9a-f]{6}", blob).group(0)


# ---------- 应用与登录 ----------
@pytest.fixture(scope="module")
def web_settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("agentweb")
    s = Settings(database_url=f"sqlite:///{(d / 'a.db').as_posix()}", index_dir=str(d / "index"),
                 knowledge_dir=str(ROOT / "knowledge"), audit_jsonl_dir=str(d / "audit"), reports_dir=str(d / "reports"),
                 api_token="static-token-xyz", web_dist_dir=str(d / "none"),
                 ui_users=f"alice:{hash_password(PW)};bob:{hash_password('another-password-1')}")
    initialize(s)
    return s


@pytest.fixture
def web_app(web_settings):
    app = create_app(web_settings)
    return app


class Web:
    """已登录的浏览器会话：自动带 CSRF。"""

    def __init__(self, app, user="alice", password=PW):
        self.c = TestClient(app)
        r = self.c.post("/api/auth/login", json={"username": user, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]

    def post(self, path, body=None, **kw):
        return self.c.post(path, json=body if body is not None else {}, headers={"X-CSRF-Token": self.csrf}, **kw)

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def say(self, message, task_id=None):
        body = {"message": message}
        if task_id:
            body["task_id"] = task_id
        return self.post("/api/agent/messages", body)

    def confirm(self, task_id, action_id, args_hash):
        return self.post(f"/api/agent/tasks/{task_id}/actions/{action_id}/confirm", {"args_hash": args_hash})

    def reject(self, task_id, action_id):
        return self.post(f"/api/agent/tasks/{task_id}/actions/{action_id}/reject", {})


@pytest.fixture
def alice(web_app):
    return Web(web_app)


@pytest.fixture
def agent(web_app, alice):
    """装配模拟模型：agent(script, **state_overrides) -> FakeClient"""
    def install(script, **state):
        client = FakeClient(script)
        web_app.state.agent_client = client
        for k, v in state.items():
            setattr(web_app.state, k, v)
        return client
    yield install
    for k in ("agent_client", "agent_limits", "agent_clock", "agent_monotonic"):
        if hasattr(web_app.state, k):
            delattr(web_app.state, k)


def count_rows(app, model, *where):
    from sqlalchemy import func, select
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


DEMO = "查询项目 PRJ-SM-003 及其批次，汇总规则结果，并准备报告草稿"
GEN_ARGS = {"project_id": "PRJ-SM-003", "kind": "sample_analysis"}


# ---------- 演示任务脚本（总控 → 数据 → 总控 → 报告 → 等确认 → 最终回答）----------
FINAL = lambda m, t: text(  # noqa: E731
    f"PRJ-SM-003 共 3 个分析批，其中 PRJ-SM-003-R02 的 QC 未通过。报告草稿 {find_draft_id(m)} 已生成，状态为待审核，"
    "须由人在审核页审核通过后才能导出。")


def demo_script():
    return [
        delegate("d1", "data", "查询 PRJ-SM-003 项目及各分析批的规则判定"),                    # 0 总控
        calls(("t1", "list_project_runs", {"project_id": "PRJ-SM-003"})),                      # 1 数据专家
        text("PRJ-SM-003 共 3 个分析批；PRJ-SM-003-R02 的 QC 未通过。"),                          # 2 数据专家交回
        delegate("d2", "report", "为 PRJ-SM-003 准备样品分析报告草稿"),                           # 3 总控
        calls(("t2", "generate_report_draft", GEN_ARGS)),                                      # 4 报告专家：提出写操作
        lambda m, t: text(f"草稿 {find_draft_id(m)} 已生成，状态待审核。"),                         # 5 报告专家（确认后）
        FINAL,                                                                                 # 6 总控最终回答
    ]


