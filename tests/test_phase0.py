import json

import pytest
from pydantic import ValidationError

from app.audit.middleware import make_digest
from app.settings import Settings


def test_health_public_and_flags(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["offline"] is True and body["simulated_data"] is True
    assert body["llm_provider"] == "deterministic"
    assert r.headers["X-Request-ID"]


def test_audit_requires_token(client):
    assert client.get("/api/audit", params={"request_id": "x"}).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.get("/api/audit", params={"request_id": "x"}, headers=bad).status_code == 401


def test_audit_row_written_and_queryable(client, auth, settings):
    rid = client.get("/api/health", headers={"X-Operator": "qa-reviewer"}).headers["X-Request-ID"]
    rows = client.get("/api/audit", params={"request_id": rid}, headers=auth).json()
    assert len(rows) == 1
    assert rows[0]["feature"] == "/api/health"
    assert rows[0]["operator"] == "qa-reviewer"
    assert rows[0]["refused"] is False and rows[0]["status_code"] == 200
    # JSONL 同步落盘
    files = list(__import__("pathlib").Path(settings.audit_jsonl_dir).glob("audit-*.jsonl"))
    assert files and json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])["request_id"] == rid


def test_digest_truncates_and_masks():
    d = make_digest("联系 someone@lab.org 或 13812345678 " + "x" * 200)
    assert len(d) <= 80 and "someone@lab.org" not in d and "13812345678" not in d


def test_external_network_is_blocked():
    import socket

    from pytest_socket import SocketConnectBlockedError

    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_non_default_provider_requires_local_path():
    with pytest.raises(ValidationError):
        Settings(llm_provider="local")


def test_non_local_env_rejects_default_token():
    with pytest.raises(ValidationError):
        Settings(app_env="production", _env_file=None)
