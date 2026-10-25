"""阶段 5：项目 / 分析批 API、看板指标口径、预置风险案例与边界情形。"""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AnalyticalRun, Deviation, Project, ReportMilestone, StudySample,
)
from tests.dashboard.conftest import AS_OF


def dash(api, auth, pid, as_of=AS_OF):
    r = api.get(f"/api/dashboard/projects/{pid}", params={"as_of": as_of.isoformat()}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 鉴权与字段契约 ----------
def test_all_endpoints_require_token(api):
    for path in ("/api/projects", "/api/projects/PRJ-SM-001", "/api/projects/PRJ-SM-001/runs",
                 "/api/runs/PRJ-SM-001-R01", "/api/dashboard/overview", "/api/dashboard/projects/PRJ-SM-001"):
        assert api.get(path).status_code == 401, path


def test_project_and_run_api_fields(api, auth):
    ps = api.get("/api/projects", headers=auth).json()
    assert [p["id"] for p in ps] == ["PRJ-BIO-002", "PRJ-SM-001", "PRJ-SM-003"]
    assert {"id", "title", "modality", "method_type", "analyte", "matrix", "unit", "phase",
            "planned_run_count", "run_count", "is_simulated"} <= set(ps[0])
    assert {p["id"]: p["run_count"] for p in ps} == {"PRJ-BIO-002": 4, "PRJ-SM-001": 3, "PRJ-SM-003": 3}
    assert all(p["is_simulated"] for p in ps)

    d = api.get("/api/projects/PRJ-SM-001", headers=auth).json()
    assert {"lloq", "uloq", "internal_standard", "sample_prep", "instrument_setup", "reagent_kit"} <= set(d)

    runs = api.get("/api/projects/PRJ-SM-003/runs", headers=auth).json()
    assert [r["id"] for r in runs] == ["PRJ-SM-003-R01", "PRJ-SM-003-R02", "PRJ-SM-003-R03"]
    assert {"id", "run_date", "status", "rule_set_id", "n_samples", "n_isr",
            "calibration", "qc", "accepted", "isr"} <= set(runs[0])
    by = {r["id"]: r for r in runs}
    assert by["PRJ-SM-003-R02"]["qc"] == "fail" and by["PRJ-SM-003-R03"]["calibration"] == "pending_review"


def test_run_detail_fields_and_statuses(api, auth):
    r = api.get("/api/runs/PRJ-SM-003-R03", headers=auth).json()
    assert {"run", "rule_status", "assessment", "calibration_points", "qc_samples", "isr_records",
            "sample_counts"} <= set(r)
    assert r["rule_status"] == "demo_unconfirmed" and r["assessment"]["accepted"] == "pending_review"
    pts = r["calibration_points"]
    missing = [p for p in pts if p["back_calc_conc"] is None]
    assert len(missing) == 2 and all(p["status"] == "unknown" and p["bias_pct"] is None for p in missing)
    assert any(p["status"] == "fail" for p in pts)
    assert r["assessment"]["calibration"]["rule_ids"] == ["CAL-ACC", "CAL-MIN"]

    r4 = api.get("/api/runs/PRJ-BIO-002-R04", headers=auth).json()
    unknown = [i for i in r4["isr_records"] if i["repeat_conc"] is None]
    assert unknown and all(i["status"] == "unknown" and i["diff_pct"] is None for i in unknown)
    assert r4["assessment"]["isr"]["verdict"] == "pending_review"

    r2 = api.get("/api/runs/PRJ-SM-003-R02", headers=auth).json()
    assert sum(q["status"] == "fail" for q in r2["qc_samples"] if q["level"] == "HQC") == 2


def test_not_found_and_bad_params(api, auth):
    assert api.get("/api/projects/NOPE", headers=auth).status_code == 404
    assert api.get("/api/projects/NOPE/runs", headers=auth).status_code == 404
    assert api.get("/api/runs/NOPE", headers=auth).status_code == 404
    assert api.get("/api/dashboard/projects/NOPE", headers=auth).status_code == 404
    assert api.get("/api/dashboard/overview", params={"as_of": "not-a-date"}, headers=auth).status_code == 422
    assert api.get("/api/dashboard/overview", headers=auth).status_code == 200      # as_of 缺省为今天


def test_dashboard_field_contract(api, auth):
    m = dash(api, auth, "PRJ-SM-001")
    assert {"project", "as_of", "samples", "runs", "calibration", "qc", "accepted", "isr", "deviations",
            "milestones", "run_summaries", "risks", "rule_status"} <= set(m)
    assert set(m["samples"]) == {"total", "valid", "blq", "above_uloq", "reanalysis", "missing"}
    assert set(m["runs"]) == {"planned", "existing", "completed", "under_review", "in_progress", "progress"}
    assert set(m["runs"]["progress"]) == {"value", "numerator", "denominator", "reason", "exceeds_plan"}
    assert set(m["qc"]) == {"eligible_runs", "pass", "fail", "pending_review", "pass_rate"}
    assert set(m["isr"]["records"]) == {"total", "pass", "fail", "pending_review"}
    assert set(m["deviations"]) >= {"total", "open", "closed", "open_by_severity", "oldest_open_days", "items"}
    assert m["rule_status"] == "demo_unconfirmed" and m["project"]["is_simulated"] is True
    o = api.get("/api/dashboard/overview", params={"as_of": AS_OF.isoformat()}, headers=auth).json()
    assert set(o["totals"]) >= {"projects", "samples", "runs", "progress", "qc", "isr", "accepted",
                                "open_deviations", "open_major_plus", "risks_by_level"}
    assert [p["project"]["id"] for p in o["projects"]] == ["PRJ-BIO-002", "PRJ-SM-001", "PRJ-SM-003"]


# ---------- 指标口径（与数据库独立核对） ----------
def test_sample_counts_match_database(api, auth, app):
    with app.state.session_factory() as s:
        for pid in ("PRJ-SM-001", "PRJ-BIO-002", "PRJ-SM-003"):
            run_ids = s.scalars(select(AnalyticalRun.id).where(AnalyticalRun.project_id == pid)).all()
            total = s.scalar(select(func.count()).select_from(StudySample).where(StudySample.run_id.in_(run_ids)))
            valid = s.scalar(select(func.count()).select_from(StudySample).where(
                StudySample.run_id.in_(run_ids), StudySample.flag == "ok"))
            m = dash(api, auth, pid)
            assert m["samples"]["total"] == total and m["samples"]["valid"] == valid
            assert m["samples"]["valid"] + m["samples"]["blq"] + m["samples"]["above_uloq"] + \
                m["samples"]["reanalysis"] + m["samples"]["missing"] == total


def test_progress_definition(api, auth):
    p = {pid: dash(api, auth, pid)["runs"] for pid in ("PRJ-SM-001", "PRJ-BIO-002", "PRJ-SM-003")}
    # 完成进度 = completed / planned；under_review 不算完成，in_progress 不算完成
    assert (p["PRJ-SM-001"]["completed"], p["PRJ-SM-001"]["under_review"], p["PRJ-SM-001"]["planned"]) == (2, 1, 3)
    assert p["PRJ-SM-001"]["progress"]["value"] == pytest.approx(2 / 3)
    assert (p["PRJ-BIO-002"]["completed"], p["PRJ-BIO-002"]["in_progress"], p["PRJ-BIO-002"]["planned"]) == (3, 1, 5)
    assert p["PRJ-BIO-002"]["progress"]["value"] == pytest.approx(0.6)
    assert p["PRJ-SM-003"]["progress"]["value"] == pytest.approx(0.5)


def test_pass_rate_excludes_pending_and_unfinished(api, auth):
    m = dash(api, auth, "PRJ-SM-001")                       # R01、R02 QC 通过，R03 QC 待复核
    assert (m["qc"]["pass"], m["qc"]["fail"], m["qc"]["pending_review"]) == (2, 0, 1)
    assert m["qc"]["pass_rate"] == {"value": 1.0, "numerator": 2, "denominator": 2, "reason": None}
    b = dash(api, auth, "PRJ-BIO-002")                      # R04 进行中，不计入任何通过率
    assert b["qc"]["eligible_runs"] == 3 and b["isr"]["eligible_runs"] == 3
    assert (b["isr"]["pass"], b["isr"]["fail"], b["isr"]["pending_review"]) == (2, 1, 0)
    assert b["isr"]["pass_rate"]["value"] == pytest.approx(2 / 3)
    r4 = next(r for r in b["run_summaries"] if r["id"] == "PRJ-BIO-002-R04")
    assert r4["status"] == "in_progress" and r4["counted_in_rates"] is False and r4["isr"] == "pending_review"
    s = dash(api, auth, "PRJ-SM-003")
    assert (s["qc"]["pass"], s["qc"]["fail"]) == (2, 1) and s["qc"]["pass_rate"]["value"] == pytest.approx(2 / 3)
    assert (s["accepted"]["pass"], s["accepted"]["fail"], s["accepted"]["pending_review"]) == (1, 1, 1)
    assert s["accepted"]["pass_rate"]["value"] == pytest.approx(0.5)


def test_overview_totals_are_sums_not_average_of_rates(api, auth):
    o = api.get("/api/dashboard/overview", params={"as_of": AS_OF.isoformat()}, headers=auth).json()["totals"]
    assert o["runs"] == {"planned": 12, "existing": 10, "completed": 7, "under_review": 2, "in_progress": 1}
    assert o["progress"]["numerator"] == 7 and o["progress"]["denominator"] == 12
    # QC: BIO 3/0 + SM-001 2/0 + SM-003 2/1 -> 7/8（而不是三个项目通过率 1.0、1.0、0.667 的平均值 0.889）
    assert (o["qc"]["pass"], o["qc"]["fail"]) == (7, 1)
    assert o["qc"]["pass_rate"]["value"] == pytest.approx(7 / 8)
    assert o["open_deviations"] == 3 and o["open_major_plus"] == 2
    assert o["risks_by_level"] == {"high": 5, "medium": 2, "low": 2}


def test_deviation_metrics(api, auth):
    b = dash(api, auth, "PRJ-BIO-002")["deviations"]
    assert (b["total"], b["open"], b["closed"]) == (2, 2, 0)
    assert b["open_by_severity"] == {"minor": 1, "major": 1, "critical": 0}
    assert b["oldest_open_days"] == (AS_OF - date(2026, 7, 20)).days
    s1 = dash(api, auth, "PRJ-SM-001")["deviations"]
    assert (s1["total"], s1["open"], s1["closed"], s1["oldest_open_days"]) == (1, 0, 1, None)
    s3 = dash(api, auth, "PRJ-SM-003")["deviations"]
    assert s3["open"] == 1 and s3["open_by_severity"]["major"] == 1
    assert {i["id"] for i in s3["items"]} == {"DEV-2026-001", "DEV-2026-005"}


def test_milestone_states_depend_on_as_of(api, auth):
    def state(pid, typ, as_of):
        m = dash(api, auth, pid, as_of)
        return next(x for x in m["milestones"] if x["type"] == typ)
    x = state("PRJ-SM-001", "validation_report", AS_OF)
    assert x["state"] == "overdue" and x["days_to_due"] == -8
    assert state("PRJ-BIO-002", "validation_report", AS_OF)["state"] == "delivered"   # 已交付不会逾期
    assert state("PRJ-BIO-002", "validation_report", date(2030, 1, 1))["state"] == "delivered"
    assert state("PRJ-SM-003", "sample_report", AS_OF)["state"] == "on_track"          # 距到期 9 天
    assert state("PRJ-SM-003", "sample_report", date(2026, 10, 25))["state"] == "due_soon"   # 7 天
    assert state("PRJ-SM-003", "sample_report", date(2026, 10, 24))["state"] == "on_track"   # 8 天
    assert state("PRJ-SM-003", "sample_report", date(2026, 11, 1))["state"] == "due_soon"    # 当天到期
    assert state("PRJ-SM-003", "sample_report", date(2026, 11, 2))["state"] == "overdue"


# ---------- 预置风险案例 ----------
def codes(m):
    return [r["risk_code"] for r in m["risks"]]


def test_planted_risk_cases(api, auth):
    s3 = dash(api, auth, "PRJ-SM-003")
    assert codes(s3) == ["RISK-OPEN-MAJOR-DEV", "RISK-QC-FAIL", "RISK-PENDING-REVIEW"]
    qc = next(r for r in s3["risks"] if r["risk_code"] == "RISK-QC-FAIL")
    assert qc["level"] == "high" and qc["evidence"]["run_ids"] == ["PRJ-SM-003-R02"]
    pend = next(r for r in s3["risks"] if r["risk_code"] == "RISK-PENDING-REVIEW")
    assert pend["level"] == "medium" and pend["evidence"]["run_ids"] == ["PRJ-SM-003-R03"]
    dev = next(r for r in s3["risks"] if r["risk_code"] == "RISK-OPEN-MAJOR-DEV")
    assert dev["evidence"]["deviation_ids"] == ["DEV-2026-001"]

    b = dash(api, auth, "PRJ-BIO-002")
    assert codes(b) == ["RISK-ISR-FAIL", "RISK-OPEN-MAJOR-DEV", "RISK-OPEN-MINOR-DEV", "RISK-RUNS-IN-PROGRESS"]
    assert next(r for r in b["risks"] if r["risk_code"] == "RISK-ISR-FAIL")["evidence"]["run_ids"] == ["PRJ-BIO-002-R02"]

    s1 = dash(api, auth, "PRJ-SM-001")
    assert codes(s1) == ["RISK-MILESTONE-OVERDUE", "RISK-PENDING-REVIEW"]
    od = s1["risks"][0]
    assert od["level"] == "high" and od["evidence"]["days_overdue"] == 8
    assert [r["level"] for r in b["risks"]] == ["high", "high", "low", "low"]          # 按严重度排序


def test_due_soon_risk_appears_near_deadline(api, auth):
    assert "RISK-MILESTONE-DUE-SOON" not in codes(dash(api, auth, "PRJ-SM-003"))
    near = dash(api, auth, "PRJ-SM-003", date(2026, 10, 28))
    r = next(r for r in near["risks"] if r["risk_code"] == "RISK-MILESTONE-DUE-SOON")
    assert r["level"] == "medium" and r["evidence"]["days_to_due"] == 4


def test_audit_records_triggered_risk_codes(api, auth):
    r = api.get("/api/dashboard/projects/PRJ-SM-003", params={"as_of": AS_OF.isoformat()}, headers=auth)
    audit = api.get("/api/audit", params={"request_id": r.headers["X-Request-ID"]}, headers=auth).json()[0]
    assert audit["feature"] == "/api/dashboard/projects/PRJ-SM-003"
    assert set(audit["rule_ids"]) == {"RISK-OPEN-MAJOR-DEV", "RISK-QC-FAIL", "RISK-PENDING-REVIEW"}


# ---------- 边界：分母为零、待复核、未完成、超计划 ----------
@pytest.fixture
def scratch(app):
    """往库里加临时项目，测试后删除，避免影响其它用例。"""
    created: list[str] = []

    def make(pid, planned, runs=(), **kw):
        with app.state.session_factory() as s:
            s.add(Project(id=pid, title=f"{pid} 边界用例（模拟）", modality="small_molecule", analyte="DEMO-EDGE",
                          matrix="人血浆", method_type="LC-MS/MS", unit="ng/mL", lloq=1.0, uloq=100.0,
                          phase="sample_analysis", planned_run_count=planned, is_simulated=True, **kw))
            s.flush()
            for i, status in enumerate(runs, start=1):
                s.add(AnalyticalRun(id=f"{pid}-R{i:02d}", project_id=pid, run_date=date(2026, 9, i),
                                    status=status, rule_set_id="RS-CHROM-DEMO-v1"))
            s.commit()
        created.append(pid)
        return pid

    yield make
    with app.state.session_factory() as s:
        for pid in created:
            for model in (AnalyticalRun,):
                for row in s.scalars(select(model).where(model.project_id == pid)).all():
                    s.delete(row)
            s.delete(s.get(Project, pid))
        s.commit()


def test_zero_planned_runs_and_no_runs(api, auth, scratch):
    m = dash(api, auth, scratch("PRJ-EDGE-001", planned=0))
    assert m["runs"]["progress"] == {"value": None, "numerator": 0, "denominator": 0,
                                     "reason": "no_planned_runs", "exceeds_plan": False}
    for k in ("calibration", "qc", "accepted", "isr"):
        assert m[k]["pass_rate"]["value"] is None and m[k]["pass_rate"]["reason"] == "no_decided_runs"
        assert m[k]["eligible_runs"] == 0
    assert m["samples"] == {"total": 0, "valid": 0, "blq": 0, "above_uloq": 0, "reanalysis": 0, "missing": 0}
    assert m["deviations"]["oldest_open_days"] is None and m["milestones"] == []
    assert codes(m) == ["RISK-NO-PLAN"]


def test_planned_but_no_runs_progress_is_zero_not_null(api, auth, scratch):
    m = dash(api, auth, scratch("PRJ-EDGE-002", planned=3))
    assert m["runs"]["progress"]["value"] == 0.0 and m["runs"]["progress"]["reason"] is None
    assert m["qc"]["pass_rate"]["value"] is None


def test_only_in_progress_runs_have_no_rates(api, auth, scratch):
    m = dash(api, auth, scratch("PRJ-EDGE-003", planned=2, runs=("in_progress", "in_progress")))
    assert m["runs"]["in_progress"] == 2 and m["runs"]["progress"]["value"] == 0.0
    assert m["qc"]["eligible_runs"] == 0 and m["qc"]["pass_rate"]["value"] is None
    assert codes(m) == ["RISK-RUNS-IN-PROGRESS"]


def test_pending_only_runs_pass_rate_is_null_but_pending_counted(api, auth, scratch):
    # 已完成但没有任何校准 / QC 数据 -> 全部判定待复核（不是通过，也不是未通过）
    m = dash(api, auth, scratch("PRJ-EDGE-004", planned=2, runs=("completed", "under_review")))
    assert m["qc"]["pending_review"] == 2 and m["qc"]["pass"] == 0 and m["qc"]["fail"] == 0
    assert m["qc"]["pass_rate"]["value"] is None and m["qc"]["pass_rate"]["denominator"] == 0
    assert m["accepted"]["pending_review"] == 2
    assert "RISK-PENDING-REVIEW" in codes(m)


def test_completed_exceeding_plan_is_clamped_and_flagged(api, auth, scratch):
    m = dash(api, auth, scratch("PRJ-EDGE-005", planned=1, runs=("completed", "completed")))
    assert m["runs"]["progress"]["value"] == 1.0 and m["runs"]["progress"]["exceeds_plan"] is True
    assert m["runs"]["completed"] == 2


def test_overview_with_zero_denominators_still_ok(api, auth, scratch):
    scratch("PRJ-EDGE-006", planned=0)
    o = api.get("/api/dashboard/overview", params={"as_of": AS_OF.isoformat()}, headers=auth).json()
    assert o["totals"]["projects"] == 4 and o["totals"]["runs"]["planned"] == 12   # 零计划批不影响汇总分母
    assert o["totals"]["progress"]["denominator"] == 12
    assert "RISK-NO-PLAN" in [r["risk_code"] for p in o["projects"] for r in p["risks"]]
