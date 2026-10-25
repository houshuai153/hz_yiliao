import pytest
from fastapi.testclient import TestClient

from app.bootstrap import initialize
from app.kb.indexer import load_index
from app.main import create_app
from app.regulatory.config import load_regulatory_config
from app.regulatory.service import RegulatoryAssistant
from app.settings import ROOT, Settings


@pytest.fixture(scope="session")
def kb_settings(tmp_path_factory) -> Settings:
    d = tmp_path_factory.mktemp("reg")
    s = Settings(
        database_url=f"sqlite:///{(d / 'reg.db').as_posix()}",
        index_dir=str(d / "index"),
        knowledge_dir=str(ROOT / "knowledge"),
        audit_jsonl_dir=str(d / "audit"),
        api_token="test-token",
    )
    initialize(s)
    return s


@pytest.fixture(scope="session")
def index(kb_settings):
    return load_index(kb_settings.index_dir)


@pytest.fixture(scope="session")
def cfg():
    return load_regulatory_config()


@pytest.fixture
def assistant(index, cfg):
    return RegulatoryAssistant(index, cfg)


@pytest.fixture
def api(kb_settings):
    return TestClient(create_app(kb_settings))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}
