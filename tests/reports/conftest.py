import pytest
import yaml
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401
from app.db.base import Base, make_engine, make_session_factory
from app.main import create_app
from app.rules.engine import load_rule_sets
from app.settings import ROOT, Settings
from seed.loader import seed_database


def all_text(doc: Document) -> list[str]:
    out = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            out.extend(c.text for c in row.cells)
    return out


@pytest.fixture
def db(tmp_path):
    """每个测试一份全新的种子库（部分测试会改动数据）。"""
    engine = make_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        seed_database(s)
    yield factory
    engine.dispose()


@pytest.fixture
def session(db) -> Session:
    with db() as s:
        yield s


@pytest.fixture(scope="session")
def rule_sets():
    return load_rule_sets()


@pytest.fixture(scope="session")
def raw_rules():
    """直接读 YAML，供测试独立重算阈值（不经过规则引擎）。"""
    return yaml.safe_load((ROOT / "config" / "rules" / "demo_rules.yaml").read_text(encoding="utf-8"))["rule_sets"]


@pytest.fixture
def reports_dir(tmp_path):
    return tmp_path / "reports"


@pytest.fixture
def api(tmp_path, reports_dir):
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}", api_token="test-token",
                 audit_jsonl_dir=str(tmp_path / "audit"), reports_dir=str(reports_dir))
    app = create_app(s)
    with app.state.session_factory() as sess:
        seed_database(sess)
    return TestClient(app)


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}
