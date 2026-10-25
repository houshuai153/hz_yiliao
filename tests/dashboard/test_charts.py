"""看板图表数据：与库中项目 / 分析批 / 样品逐项核对（独立 SQL 计数），以及零分母、待复核、进行中、空数据。"""
from collections import Counter
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import AnalyticalRun, Project, StudySample
from app.dashboard.charts import SAMPLE_FLAGS
from tests.dashboard.conftest import AS_OF
from tests.dashboard.test_dashboard_api import dash, scratch  # noqa: F401  (scratch 为 fixture)


def charts(api, auth, as_of=AS_OF):
    r = api.get("/api/dashboard/charts", params={"as_of": as_of.isoformat()}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def db_runs(app):
    with app.state.session_factory() as s:
        return s.scalars(select(AnalyticalRun).order_by(AnalyticalRun.id)).all()


def test_requires_token_and_validates_date(api, auth):
    assert api.get("/api/dashboard/charts").status_code == 401
    assert api.get("/api/dashboard/charts", params={"as_of": "bad"}, headers=auth).status_code == 422


def test_progress_matches_database(api, auth, app):
    c = charts(api, auth)
    runs = db_runs(app)
    with app.state.session_factory() as s:
        projects = {p.id: p for p in s.scalars(select(Project)).all()}
    assert [p["project_id"] for p in c["progress"]] == sorted(projects)
    for row in c["progress"]:
        mine = [r for r in runs if r.project_id == row["project_id"]]
        cnt = Counter(r.status for r in mine)
        plan = projects[row["project_id"]].planned_run_count
        assert (row["completed"], row["under_review"], row["in_progress"]) == (
            cnt["completed"], cnt["under_review"], cnt["in_progress"])
        assert row["existing"] == len(mine) and row["planned"] == plan
        assert row["unscheduled"] == max(plan - len(mine), 0) and row["exceeds_plan"] == (len(mine) > plan)
    # 与既有看板接口一致
    for row in c["progress"]:
        m = dash(api, auth, row["project_id"])["runs"]
        assert (row["planned"], row["completed"], row["under_review"], row["in_progress"]) == (
            m["planned"], m["completed"], m["under_review"], m["in_progress"])


def test_sample_composition_matches_database_and_existing_counts(api, auth, app):
    c = charts(api, auth)
    with app.state.session_factory() as s:
        rows = s.execute(select(AnalyticalRun.project_id, StudySample.flag).join(
            StudySample, StudySample.run_id == AnalyticalRun.id)).all()
    for pid, comp in c["sample_composition"].items():
        want = Counter(f for p, f in rows if pid == "ALL" or p == pid)
        assert {p["flag"]: p["count"] for p in comp["parts"]} == {f: want[f] for f in SAMPLE_FLAGS}
        assert comp["total"] == sum(want[f] for f in SAMPLE_FLAGS) == sum(p["count"] for p in comp["parts"])
        assert comp["total"] > 0 and abs(sum(p["share"] for p in comp["parts"]) - 1) < 1e-9
        if pid != "ALL":
            sm = dash(api, auth, pid)["samples"]
            assert (comp["total"], want["ok"], want["BLQ"]) == (sm["total"], sm["valid"], sm["blq"])
    assert c["sample_composition"]["ALL"]["total"] == sum(
        v["total"] for k, v in c["sample_composition"].items() if k != "ALL")


def test_run_detail_matches_dashboard_verdicts_and_samples(api, auth):
    c = charts(api, auth)
    for pid, runs in c["runs"].items():
        m = dash(api, auth, pid)
        by_id = {r["id"]: r for r in m["run_summaries"]}
        assert [r["id"] for r in runs] == sorted(by_id, key=lambda i: (by_id[i]["run_date"], i))
        for r in runs:                                              # 判定直接取自规则引擎结果，图表层不重算
            s = by_id[r["id"]]
            assert (r["status"], r["calibration"], r["qc"], r["accepted"], r["isr"], r["run_date"]) == (
                s["status"], s["calibration"], s["qc"], s["accepted"], s["isr"], s["run_date"])
        assert sum(r["samples"]["total"] for r in runs) == c["sample_composition"][pid]["total"]


def test_timeline_uses_real_run_dates_without_filling(api, auth, app):
    c = charts(api, auth)
    runs = db_runs(app)
    assert c["timeline_basis"] == "run_date_cumulative_current_status"
    for pid, pts in c["timeline"].items():
        mine = [r for r in runs if pid == "ALL" or r.project_id == pid]
        assert [p["date"] for p in pts] == sorted({r.run_date.isoformat() for r in mine})    # 只有真实日期，不补点
        cum = 0
        for p in pts:
            on = [r for r in mine if r.run_date.isoformat() == p["date"]]
            cum += len(on)
            assert p["cumulative"] == cum and p["runs_on_date"] == len(on) and sorted(p["run_ids"]) == sorted(r.id for r in on)
            assert sum(p["cumulative_by_status"].values()) == cum
            assert [x["id"] for x in p["runs"]] == p["run_ids"] and p["runs"][-1]["cumulative_rank"] == cum
            assert {x["id"]: x["status"] for x in p["runs"]} == {r.id: r.status for r in on}
        assert pts[-1]["cumulative"] == len(mine)
        last = Counter(r.status for r in mine)
        assert pts[-1]["cumulative_by_status"] == {k: last[k] for k in ("completed", "under_review", "in_progress")}
    assert "不代表历史上某日的完成情况" in c["notes"]["timeline"]


def test_as_of_does_not_filter_timeline_only_flags_it(api, auth):
    early, late = charts(api, auth, date(2026, 6, 10)), charts(api, auth, date(2026, 12, 31))
    strip = lambda pts: [{k: v for k, v in p.items() if k != "after_as_of"} for p in pts]   # noqa: E731
    assert strip(early["timeline"]["ALL"]) == strip(late["timeline"]["ALL"])
    assert any(p["after_as_of"] for p in early["timeline"]["ALL"]) and not any(p["after_as_of"] for p in late["timeline"]["ALL"])


# ---------- 边界 ----------
def row_of(c, pid):
    return next(p for p in c["progress"] if p["project_id"] == pid)


def test_project_without_plan_or_runs(api, auth, scratch):  # noqa: F811
    pid = scratch("PRJ-EDGE-101", planned=0)
    c = charts(api, auth)
    r = row_of(c, pid)
    assert (r["planned"], r["existing"], r["unscheduled"], r["exceeds_plan"]) == (0, 0, 0, False)
    assert c["runs"][pid] == [] and c["timeline"][pid] == []
    comp = c["sample_composition"][pid]
    assert comp["total"] == 0 and all(p["count"] == 0 and p["share"] is None for p in comp["parts"])   # 不是 0% / 100%


def test_planned_but_not_built_and_exceeding_plan(api, auth, scratch):  # noqa: F811
    a = scratch("PRJ-EDGE-102", planned=3)
    b = scratch("PRJ-EDGE-103", planned=1, runs=("completed", "completed"))
    c = charts(api, auth)
    assert (row_of(c, a)["unscheduled"], row_of(c, a)["existing"]) == (3, 0)
    rb = row_of(c, b)
    assert rb["exceeds_plan"] is True and rb["unscheduled"] == 0 and rb["completed"] == 2   # 图表显示真实批数，不截断


def test_pending_and_in_progress_runs_are_separate_buckets(api, auth, scratch):  # noqa: F811
    pid = scratch("PRJ-EDGE-104", planned=4, runs=("completed", "under_review", "in_progress", "in_progress"))
    c = charts(api, auth)
    r = row_of(c, pid)
    assert (r["completed"], r["under_review"], r["in_progress"], r["unscheduled"]) == (1, 1, 2, 0)
    pts = c["timeline"][pid]
    assert [p["cumulative"] for p in pts] == [1, 2, 3, 4] and pts[-1]["cumulative_by_status"] == {
        "completed": 1, "under_review": 1, "in_progress": 2}
    assert [x["status"] for x in c["runs"][pid]] == ["completed", "under_review", "in_progress", "in_progress"]
    assert all(x["samples"]["total"] == 0 for x in c["runs"][pid])
