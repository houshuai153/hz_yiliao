"""项目 / 分析批 / 看板 API（只读）。判定与指标全部来自 app.rules 与 app.dashboard.metrics。"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_token
from app.dashboard import service as svc
from app.dashboard.charts import chart_data
from app.dashboard.metrics import overview, project_metrics
from app.db.models import Project
from app.deps import rule_sets_of

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _rule_sets(request: Request):
    return rule_sets_of(request.app.state)


def _as_of(as_of: date | None) -> date:
    return as_of or datetime.now(timezone.utc).date()


def _nf(exc: svc.NotFound) -> HTTPException:
    return HTTPException(404, {"error_code": exc.code, "message": exc.message})


def _project_or_404(session, project_id: str) -> Project:
    try:
        return svc.require_project(session, project_id)
    except svc.NotFound as exc:
        raise _nf(exc) from exc


def _audit_risks(request: Request, text: str, risk_codes: set[str]) -> None:
    request.state.audit.input_text = text
    request.state.audit.rule_ids = sorted(risk_codes)


@router.get("/projects")
def list_projects(request: Request) -> list[dict]:
    with request.app.state.session_factory() as s:
        return svc.list_projects(s)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict:
    with request.app.state.session_factory() as s:
        try:
            return svc.get_project(s, project_id)
        except svc.NotFound as exc:
            raise _nf(exc) from exc


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str, request: Request) -> list[dict]:
    rs = _rule_sets(request)
    with request.app.state.session_factory() as s:
        try:
            return svc.list_runs(s, rs, project_id)
        except svc.NotFound as exc:
            raise _nf(exc) from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    rs_all = _rule_sets(request)
    with request.app.state.session_factory() as s:
        try:
            res = svc.run_detail(s, rs_all, run_id)
        except svc.NotFound as exc:
            raise _nf(exc) from exc
    a = res["assessment"]
    _audit_risks(request, f"run {run_id}", set(a["calibration"]["rule_ids"] + a["qc"]["rule_ids"] + a["isr"]["rule_ids"]))
    return res


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
