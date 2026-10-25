from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import initialize
from app.main import create_app
from app.settings import ROOT, Settings

AS_OF = date(2026, 10, 23)   # 种子数据按此日期设计里程碑 / 偏差账龄


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("dash")
    s = Settings(
        database_url=f"sqlite:///{(d / 'dash.db').as_posix()}", index_dir=str(d / "index"),
        knowledge_dir=str(ROOT / "knowledge"), audit_jsonl_dir=str(d / "audit"),
        reports_dir=str(d / "reports"), api_token="test-token",
    )
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
