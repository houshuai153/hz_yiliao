import pytest
from fastapi.testclient import TestClient

from app.llm.base import DeterministicProvider
from app.main import create_app
from app.settings import Settings
from app.translation.service import TranslationService


@pytest.fixture(scope="session")
def provider():
    return DeterministicProvider()


@pytest.fixture(scope="session")
def glossary(provider):
    return provider.glossary


@pytest.fixture(scope="session")
def svc(provider, glossary):
    return TranslationService(provider, glossary)


@pytest.fixture
def api(tmp_path):
    s = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
                 audit_jsonl_dir=str(tmp_path / "audit"), api_token="test-token")
    return TestClient(create_app(s))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer test-token"}
