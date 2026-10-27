"""与 FastAPI Request 无关的只读查询服务：项目 / 分析批 / 分析批明细。

原先写在 app/api/dashboard.py 的路由函数里；抽出后由路由与 Agent 工具共用同一份实现，
判定一律来自 app.dashboard.metrics.assess_run（即 app.rules），这里不做任何判定计算。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dashboard.metrics import assess_run
from app.db.models import AnalyticalRun, CalibrationPoint, ISRRecord, Project, QCSample, StudySample
from app.rules.engine import RuleSet, classify, isr_diff_pct


class NotFound(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def require_project(session: Session, project_id: str) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise NotFound("project_not_found", f"项目不存在：{project_id}")
    return p


def list_projects(session: Session) -> list[dict]:
    out = []
    for p in session.scalars(select(Project).order_by(Project.id)):
        n_runs = len(session.scalars(select(AnalyticalRun.id).where(AnalyticalRun.project_id == p.id)).all())
        out.append({"id": p.id, "title": p.title, "modality": p.modality, "method_type": p.method_type,
                    "analyte": p.analyte, "matrix": p.matrix, "unit": p.unit, "phase": p.phase,
                    "planned_run_count": p.planned_run_count, "run_count": n_runs,
                    "is_simulated": p.is_simulated})
    return out


def get_project(session: Session, project_id: str) -> dict:
    p = require_project(session, project_id)
    return {"id": p.id, "title": p.title, "modality": p.modality, "method_type": p.method_type,
            "analyte": p.analyte, "matrix": p.matrix, "unit": p.unit, "lloq": p.lloq, "uloq": p.uloq,
            "phase": p.phase, "planned_run_count": p.planned_run_count, "is_simulated": p.is_simulated,
            "internal_standard": p.internal_standard, "sample_prep": p.sample_prep,
            "instrument_setup": p.instrument_setup, "reagent_kit": p.reagent_kit}


def list_runs(session: Session, rule_sets: dict[str, RuleSet], project_id: str) -> list[dict]:
    require_project(session, project_id)
    out = []
    for r in session.scalars(select(AnalyticalRun).where(AnalyticalRun.project_id == project_id)
                             .order_by(AnalyticalRun.id)):
        a = assess_run(session, r, rule_sets)
        n_s = len(session.scalars(select(StudySample.id).where(StudySample.run_id == r.id)).all())
        n_i = len(session.scalars(select(ISRRecord.sample_id).where(ISRRecord.run_id == r.id)).all())
        out.append({"id": r.id, "run_date": r.run_date.isoformat(), "status": r.status,
                    "rule_set_id": r.rule_set_id, "n_samples": n_s, "n_isr": n_i,
                    "calibration": a.calibration.verdict.value, "qc": a.qc.verdict.value,
                    "accepted": a.accepted.value, "isr": a.isr.verdict.value})
    return out


def run_detail(session: Session, rule_sets: dict[str, RuleSet], run_id: str) -> dict:
    run = session.get(AnalyticalRun, run_id)
    if run is None:
        raise NotFound("run_not_found", f"分析批不存在：{run_id}")
    rs, a = rule_sets[run.rule_set_id], assess_run(session, run, rule_sets)
    cal = session.scalars(select(CalibrationPoint).where(CalibrationPoint.run_id == run_id)
                          .order_by(CalibrationPoint.level_no)).all()
    qc = session.scalars(select(QCSample).where(QCSample.run_id == run_id)
                         .order_by(QCSample.level, QCSample.replicate)).all()
    isr = session.scalars(select(ISRRecord).where(ISRRecord.run_id == run_id).order_by(ISRRecord.sample_id)).all()
    flags: dict[str, int] = {}
    for smp in session.scalars(select(StudySample).where(StudySample.run_id == run_id)):
        flags[smp.flag] = flags.get(smp.flag, 0) + 1
    pts = a.calibration.detail.get("points")
    qs = a.qc.detail.get("samples")
    lim = rs.v("ISR-DIFF")["diff_pct"]

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
