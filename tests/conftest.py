import os

# 测试永远离线：无论开发者的 .env 里配置了什么（例如 DeepSeek），环境变量优先于 .env，这里强制回到确定性实现。
# 需要在线 provider 的用例自己构造 Settings(llm_provider="deepseek", ...) 并注入伪造的 HTTP 会话，不联网。
os.environ["LLM_PROVIDER"] = "deterministic"
os.environ["LLM_API_KEY"] = ""
os.environ["LOCAL_MODEL_PATH"] = ""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        api_token="test-token",
        audit_jsonl_dir=str(tmp_path / "audit"),
    )


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def auth() -> dict:
    return {"Authorization": "Bearer test-token"}
