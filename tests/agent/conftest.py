"""Agent 工具层测试夹具：真实的种子库 + 本地索引（initialize），全部离线；不使用任何模型。"""
import pytest
from fastapi.testclient import TestClient

from app.agent.context import AgentContext, issue_grant
from app.agent.tools import prepare_write
from app.bootstrap import initialize
from app.main import create_app
from app.settings import ROOT, Settings
from tests.agent.agent_helpers import agent, alice, web_app, web_settings  # noqa: F401  (夹具)


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("agent")
    s = Settings(database_url=f"sqlite:///{(d / 'agent.db').as_posix()}", index_dir=str(d / "index"),
                 knowledge_dir=str(ROOT / "knowledge"), audit_jsonl_dir=str(d / "audit"),
                 reports_dir=str(d / "reports"), api_token="test-token")
    initialize(s)
    return s


@pytest.fixture(scope="module")
def app(settings):
    return create_app(settings)


@pytest.fixture
def api(app):
    return TestClient(app)


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def ctx(app) -> AgentContext:
    return AgentContext(state=app.state, operator="alice", session_key="sess-A")


@pytest.fixture
def grant_for():
    """服务端侧：为“工具 + 参数”签发确认（模拟阶段 2 用户独立确认之后）。可覆盖任意字段以构造越权场景。"""
    def make(ctx, name, raw_args, **over):
        pending = prepare_write(name, raw_args)
        fields = dict(tool=name, args_hash=pending.args_hash, operator=ctx.operator,
                      session_key=ctx.session_key, user_message_id="msg-1")
        fields.update(over)
        return issue_grant(**fields)
    return make


def count(app, model, *where):
    from sqlalchemy import func, select
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))
