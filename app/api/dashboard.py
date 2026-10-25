"""项目 / 分析批 / 看板 API（只读）。判定与指标全部来自 app.rules 与 app.dashboard.metrics。"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.auth import require_token
from app.dashboard.charts import chart_data
from app.dashboard.metrics import assess_run, overview, project_metrics
from app.db.models import AnalyticalRun, CalibrationPoint, ISRRecord, Project, QCSample, StudySample
from app.rules.engine import classify, isr_diff_pct, load_rule_sets

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _rule_sets(request: Request):
    state = request.app.state
    if getattr(state, "rule_sets", None) is None:
        state.rule_sets = load_rule_sets()
    return state.rule_sets


def _as_of(as_of: date | None) -> date:
    return as_of or datetime.now(timezone.utc).date()


def _project_or_404(session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, {"error_code": "project_not_found", "message": f"项目不存在：{project_id}"})
    return p


def _audit_risks(request: Request, text: str, risk_codes: set[str]) -> None:
    request.state.audit.input_text = text
    request.state.audit.rule_ids = sorted(risk_codes)


@router.get("/projects")
def list_projects(request: Request) -> list[dict]:
    with request.app.state.session_factory() as s:
        out = []
        for p in s.scalars(select(Project).order_by(Project.id)):
            n_runs = len(s.scalars(select(AnalyticalRun.id).where(AnalyticalRun.project_id == p.id)).all())
            out.append({"id": p.id, "title": p.title, "modality": p.modality, "method_type": p.method_type,
                        "analyte": p.analyte, "matrix": p.matrix, "unit": p.unit, "phase": p.phase,
                        "planned_run_count": p.planned_run_count, "run_count": n_runs,
                        "is_simulated": p.is_simulated})
        return out


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as s:
        p = _project_or_404(s, project_id)
        return {"id": p.id, "title": p.title, "modality": p.modality, "method_type": p.method_type,
                "analyte": p.analyte, "matrix": p.matrix, "unit": p.unit, "lloq": p.lloq, "uloq": p.uloq,
                "phase": p.phase, "planned_run_count": p.planned_run_count, "is_simulated": p.is_simulated,
                "internal_standard": p.internal_standard, "sample_prep": p.sample_prep,
                "instrument_setup": p.instrument_setup, "reagent_kit": p.reagent_kit}


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str, request: Request) -> list[dict]:
    rs = _rule_sets(request)
    with request.app.state.session_factory() as s:
        _project_or_404(s, project_id)
        out = []
        for r in s.scalars(select(AnalyticalRun).where(AnalyticalRun.project_id == project_id)
                           .order_by(AnalyticalRun.id)):
            a = assess_run(s, r, rs)
            n_s = len(s.scalars(select(StudySample.id).where(StudySample.run_id == r.id)).all())
            n_i = len(s.scalars(select(ISRRecord.sample_id).where(ISRRecord.run_id == r.id)).all())
            out.append({"id": r.id, "run_date": r.run_date.isoformat(), "status": r.status,
                        "rule_set_id": r.rule_set_id, "n_samples": n_s, "n_isr": n_i,
                        "calibration": a.calibration.verdict.value, "qc": a.qc.verdict.value,
                        "accepted": a.accepted.value, "isr": a.isr.verdict.value})
        return out


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    rs_all = _rule_sets(request)
    with request.app.state.session_factory() as s:
        run = s.get(AnalyticalRun, run_id)
        if run is None:
            raise HTTPException(404, {"error_code": "run_not_found", "message": f"分析批不存在：{run_id}"})
        rs, a = rs_all[run.rule_set_id], assess_run(s, run, rs_all)
        cal = s.scalars(select(CalibrationPoint).where(CalibrationPoint.run_id == run_id)
                        .order_by(CalibrationPoint.level_no)).all()
        qc = s.scalars(select(QCSample).where(QCSample.run_id == run_id)
                       .order_by(QCSample.level, QCSample.replicate)).all()
        isr = s.scalars(select(ISRRecord).where(ISRRecord.run_id == run_id).order_by(ISRRecord.sample_id)).all()
        flags = {}
        for smp in s.scalars(select(StudySample).where(StudySample.run_id == run_id)):
            flags[smp.flag] = flags.get(smp.flag, 0) + 1
        pts = a.calibration.detail.get("points")
        qs = a.qc.detail.get("samples")
        lim = rs.v("ISR-DIFF")["diff_pct"]
        _audit_risks(request, f"run {run_id}", set(a.calibration.rule_ids + a.qc.rule_ids + a.isr.rule_ids))

        def part(c):
            return {"verdict": c.verdict.value, "rule_ids": list(c.rule_ids), "detail": {
                k: v for k, v in c.detail.items() if k not in ("points", "samples")}}
        return {
            "run": {"id": run.id, "project_id": run.project_id, "run_date": run.run_date.isoformat(),
                    "status": run.status, "rule_set_id": run.rule_set_id},
            "rule_status": "demo_unconfirmed",
            "assessment": {"calibration": part(a.calibration), "qc": part(a.qc), "isr": part(a.isr),
                           "accepted": a.accepted.value},
            "calibration_points": [
                {"level_no": c.level_no, "nominal": c.nominal, "back_calc_conc": c.back_calc_conc,
                 **({"bias_pct": pts[i]["bias_pct"], "limit_pct": pts[i]["limit_pct"], "status": pts[i]["status"]}
                    if pts else {"bias_pct": None, "limit_pct": None, "status": None})} for i, c in enumerate(cal)],
            "qc_samples": [
                {"level": q.level, "replicate": q.replicate, "nominal": q.nominal, "measured": q.measured,
                 **({"bias_pct": qs[i]["bias_pct"], "limit_pct": qs[i]["limit_pct"], "status": qs[i]["status"]}
                    if qs else {"bias_pct": None, "limit_pct": None, "status": None})} for i, q in enumerate(qc)],
            "isr_records": [
                {"sample_id": i.sample_id, "original_conc": i.original_conc, "repeat_conc": i.repeat_conc,
                 "diff_pct": isr_diff_pct(i.original_conc, i.repeat_conc),
                 "limit_pct": lim, "status": classify(isr_diff_pct(i.original_conc, i.repeat_conc), lim).value}
                for i in isr],
            "sample_counts": {"total": sum(flags.values()), **flags},
        }


@router.get("/dashboard/charts")
def dashboard_charts(request: Request, as_of: date | None = Query(default=None)) -> dict:
    """图表数据：进度对比、按分析批日期的累计曲线、样本构成。口径见 app/dashboard/charts.py。"""
    as_of = _as_of(as_of)
    with request.app.state.session_factory() as s:
        items = overview(s, _rule_sets(request), as_of)["projects"]
        res = chart_data(s, items, as_of)
    request.state.audit.input_text = f"dashboard charts as_of={res['as_of']}"
    return res


@router.get("/dashboard/overview")
def dashboard_overview(request: Request, as_of: date | None = Query(default=None)) -> dict:
    with request.app.state.session_factory() as s:
        res = overview(s, _rule_sets(request), _as_of(as_of))
    _audit_risks(request, f"dashboard overview as_of={res['as_of']}",
                 {r["risk_code"] for p in res["projects"] for r in p["risks"]})
    return res


@router.get("/dashboard/projects/{project_id}")
def dashboard_project(project_id: str, request: Request, as_of: date | None = Query(default=None)) -> dict:
    with request.app.state.session_factory() as s:
        p = _project_or_404(s, project_id)
        res = project_metrics(s, p, _rule_sets(request), _as_of(as_of))
    _audit_risks(request, f"dashboard project={project_id} as_of={res['as_of']}", {r["risk_code"] for r in res["risks"]})
    return res
