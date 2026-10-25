"""PostgreSQL 兼容性（可选）：需要 `pip install pgserver`（内嵌真实 PostgreSQL，仅测试用），否则整体跳过。

默认的测试、评测和演示都用 SQLite；这里验证“数据库访问层预留的 PostgreSQL 配置”确实可用：
初始化幂等、旧库补列迁移、以及 API / 看板 / 报告 / 问答在 PostgreSQL 上端到端工作。
"""
import re

import pytest

pgserver = pytest.importorskip("pgserver")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from app.bootstrap import initialize  # noqa: E402
from app.db import models  # noqa: E402,F401
from app.db.base import Base, add_missing_columns, make_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import ROOT, Settings  # noqa: E402


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield server
    server.cleanup()


def sa_url(server, dbname: str) -> str:
    uri = server.get_uri()                                     # postgresql://postgres:@127.0.0.1:port/postgres
    return re.sub(r"^postgresql://", "postgresql+psycopg://", uri.rsplit("/", 1)[0] + f"/{dbname}")


def make_db(server, name: str) -> str:
    server.psql(f"CREATE DATABASE {name};")
    return sa_url(server, name)


@pytest.fixture(scope="module")
def pg_settings(pg, tmp_path_factory):
    d = tmp_path_factory.mktemp("pgapp")
    return Settings(database_url=make_db(pg, "bioapp"), index_dir=str(d / "index"), knowledge_dir=str(ROOT / "knowledge"),
                    audit_jsonl_dir=str(d / "audit"), reports_dir=str(d / "reports"), api_token="test-token")


def test_initialize_is_idempotent_on_postgres(pg_settings):
    first = initialize(pg_settings)
    second = initialize(pg_settings)
    assert first["seed"] == second["seed"] == {
        "project": 3, "analytical_run": 10, "calibration_point": 71, "qc_sample": 120, "study_sample": 413,
        "isr_record": 127, "deviation": 5, "report_milestone": 4}
    assert first["knowledge"] == second["knowledge"] == {"documents": 7, "sections": 43}
    assert second["added_columns"] == []


def test_old_postgres_schema_gets_missing_columns(pg):
    engine = make_engine(make_db(pg, "bioold"))
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE project (
            id VARCHAR(32) PRIMARY KEY, title VARCHAR(200) NOT NULL, modality VARCHAR(20) NOT NULL,
            analyte VARCHAR(100) NOT NULL, matrix VARCHAR(50) NOT NULL, method_type VARCHAR(20) NOT NULL,
            unit VARCHAR(20) NOT NULL, lloq DOUBLE PRECISION NOT NULL, uloq DOUBLE PRECISION NOT NULL,
            phase VARCHAR(30) NOT NULL, planned_run_count INTEGER NOT NULL, is_simulated BOOLEAN)"""))
    Base.metadata.create_all(engine)
    added = add_missing_columns(engine)
    assert {a.split(".")[1] for a in added if a.startswith("project.")} == {
        "internal_standard", "sample_prep", "instrument_setup", "reagent_kit"}
    assert add_missing_columns(engine) == []
    assert "reagent_kit" in {c["name"] for c in inspect(engine).get_columns("project")}
    engine.dispose()


def test_api_end_to_end_on_postgres(pg_settings):
    initialize(pg_settings)
    api = TestClient(create_app(pg_settings))
    h = {"Authorization": "Bearer test-token"}

    ov = api.get("/api/dashboard/overview", params={"as_of": "2026-10-23"}, headers=h).json()["totals"]
    assert ov["runs"] == {"planned": 12, "existing": 10, "completed": 7, "under_review": 2, "in_progress": 1}
    assert ov["risks_by_level"] == {"high": 5, "medium": 2, "low": 2}                 # 与 SQLite 上的结果一致

    run = api.get("/api/runs/PRJ-SM-003-R02", headers=h).json()
    assert run["assessment"]["qc"]["verdict"] == "fail"

    r = api.post("/api/reports/draft", headers=h, json={"project_id": "PRJ-SM-003", "kind": "sample_analysis"})
    assert r.status_code == 200 and r.json()["unsourced_sentences"] == 0
    did = r.json()["draft_id"]
    ho = {**h, "X-Operator": "pg-tester"}
    assert api.get(f"/api/reports/{did}/download", headers=ho).status_code == 409        # 未审核不可导出
    for act, body in [("start_review", {}), ("approve", {
            "comment": "已逐项核对", "confirmations": ["facts_checked", "verdicts_untouched", "not_formal_approval"],
            "missing_ack": {f: "保留待补充，待业务提供" for f in r.json()["missing_fields"]}})]:
        t = api.post(f"/api/reports/{did}/transition", headers=ho, json={"action": act, **body})
        assert t.status_code == 200, t.text                                            # 审核表（JSON 列、外键）在 PostgreSQL 上可用
    assert api.get(f"/api/reports/{did}/download", headers=ho).status_code == 200

    a = api.post("/api/regulatory/ask", headers=h, json={"question": "偏差在什么条件下可以关闭"}).json()
    assert a["refused"] is False and "DEMO-SOP-DEV-004" in [c["section_id"] for c in a["citations"]]

    audit = api.get("/api/audit", params={"request_id": a["request_id"]}, headers=h).json()
    assert audit and audit[0]["section_ids"]                                          # JSON 列在 PostgreSQL 上读写正常
