"""看板指标：全部由规则引擎的判定汇总，不另算判定。

口径（与 SPEC 第 5 节一致）：
- 已判定批（eligible）= 状态为 completed 或 under_review 的分析批；in_progress 不计入任何通过率，单列。
- 通过率 = pass / (pass + fail)。pending_review 单列，既不进分子也不进分母；分母为 0 时 value=None 并给出 reason。
- 完成进度 = completed / planned_run_count（under_review 不算完成）；planned_run_count=0 时 value=None。
- 未关闭偏差 = status=open。里程碑状态相对 as_of：delivered / overdue / due_soon(7 天内) / on_track。
"""
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticalRun, CalibrationPoint, Deviation, ISRRecord, Project, QCSample, ReportMilestone, StudySample,
)
from app.rules.engine import RuleSet, RunAssessment, Verdict, evaluate_run

DUE_SOON_DAYS = 7
ELIGIBLE = ("completed", "under_review")
LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}


def ratio(numerator: int, denominator: int, reason: str) -> dict:
    return {"value": (numerator / denominator) if denominator else None, "numerator": numerator,
            "denominator": denominator, "reason": None if denominator else reason}


def assess_run(session: Session, run: AnalyticalRun, rule_sets: dict[str, RuleSet]) -> RunAssessment:
    cal = session.scalars(select(CalibrationPoint).where(CalibrationPoint.run_id == run.id)
                          .order_by(CalibrationPoint.level_no)).all()
    qc = session.scalars(select(QCSample).where(QCSample.run_id == run.id)
                         .order_by(QCSample.level, QCSample.replicate)).all()
    isr = session.scalars(select(ISRRecord).where(ISRRecord.run_id == run.id)
                          .order_by(ISRRecord.sample_id)).all()
    return evaluate_run([(c.nominal, c.back_calc_conc) for c in cal],
                        [(q.level, q.nominal, q.measured) for q in qc],
                        [(i.original_conc, i.repeat_conc) for i in isr], rule_sets[run.rule_set_id])


def _verdict_block(verdicts: list[Verdict], reason: str = "no_decided_runs") -> dict:
    c = Counter(v.value for v in verdicts)
    p, f, w = c["pass"], c["fail"], c["pending_review"]
    return {"eligible_runs": len(verdicts), "pass": p, "fail": f, "pending_review": w,
            "pass_rate": ratio(p, p + f, reason)}


def milestone_state(m: ReportMilestone, as_of: date) -> tuple[str, int | None]:
    if m.status == "delivered":
        return "delivered", None
    days = (m.planned_date - as_of).days
    if days < 0:
        return "overdue", days
    return ("due_soon" if days <= DUE_SOON_DAYS else "on_track"), days


def project_metrics(session: Session, project: Project, rule_sets: dict[str, RuleSet], as_of: date) -> dict:
    runs = session.scalars(select(AnalyticalRun).where(AnalyticalRun.project_id == project.id)
                           .order_by(AnalyticalRun.id)).all()
    infos = {r.id: assess_run(session, r, rule_sets) for r in runs}
    eligible = [r for r in runs if r.status in ELIGIBLE]
    by_status = Counter(r.status for r in runs)

    flags = Counter(s.flag for s in session.scalars(select(StudySample).where(
        StudySample.run_id.in_([r.id for r in runs])))) if runs else Counter()
    samples = {"total": sum(flags.values()), "valid": flags["ok"], "blq": flags["BLQ"],
               "above_uloq": flags["above_ULOQ"], "reanalysis": flags["reanalysis"], "missing": flags["missing"]}

    planned = project.planned_run_count
    completed = by_status["completed"]
    prog = ratio(min(completed, planned), planned, "no_planned_runs")
    prog["exceeds_plan"] = completed > planned
    runs_block = {"planned": planned, "existing": len(runs), "completed": completed,
                  "under_review": by_status["under_review"], "in_progress": by_status["in_progress"],
                  "progress": prog}

    cal = _verdict_block([infos[r.id].calibration.verdict for r in eligible])
    qc = _verdict_block([infos[r.id].qc.verdict for r in eligible])
    acc = _verdict_block([infos[r.id].accepted for r in eligible])
    isr = _verdict_block([infos[r.id].isr.verdict for r in eligible])
    rec = Counter()
    for r in eligible:
        d = infos[r.id].isr.detail
        rec["total"] += d.get("n", 0)
        rec["pass"] += d.get("pass", 0)
        rec["fail"] += d.get("fail", 0)
        rec["pending_review"] += d.get("borderline", 0) + d.get("unknown", 0)
    isr["records"] = {k: rec[k] for k in ("total", "pass", "fail", "pending_review")}

    devs = session.scalars(select(Deviation).where(Deviation.project_id == project.id)
                           .order_by(Deviation.id)).all()
    open_devs = [d for d in devs if d.status == "open"]
    dev_block = {
        "total": len(devs), "open": len(open_devs), "closed": len(devs) - len(open_devs),
        "open_by_severity": {s: sum(d.severity == s for d in open_devs) for s in ("minor", "major", "critical")},
        "oldest_open_days": max(((as_of - d.opened_at).days for d in open_devs), default=None),
        "items": [{"id": d.id, "run_id": d.run_id, "category": d.category, "severity": d.severity,
                   "status": d.status, "opened_at": d.opened_at.isoformat(),
                   "closed_at": d.closed_at.isoformat() if d.closed_at else None,
                   "description": d.description} for d in devs],
    }

    ms_rows = session.scalars(select(ReportMilestone).where(ReportMilestone.project_id == project.id)
                              .order_by(ReportMilestone.type)).all()
    milestones = []
    for m in ms_rows:
        state, days = milestone_state(m, as_of)
        milestones.append({"type": m.type, "planned_date": m.planned_date.isoformat(), "status": m.status,
                           "completed_date": m.completed_date.isoformat() if m.completed_date else None,
                           "state": state, "days_to_due": days})

    run_summaries = [{
        "id": r.id, "run_date": r.run_date.isoformat(), "status": r.status,
        "counted_in_rates": r.status in ELIGIBLE,
        "calibration": infos[r.id].calibration.verdict.value, "qc": infos[r.id].qc.verdict.value,
        "accepted": infos[r.id].accepted.value, "isr": infos[r.id].isr.verdict.value,
    } for r in runs]

    return {
        "project": {"id": project.id, "title": project.title, "modality": project.modality,
                    "method_type": project.method_type, "analyte": project.analyte, "matrix": project.matrix,
                    "unit": project.unit, "phase": project.phase, "is_simulated": project.is_simulated},
        "as_of": as_of.isoformat(), "samples": samples, "runs": runs_block,
        "calibration": cal, "qc": qc, "accepted": acc, "isr": isr, "deviations": dev_block,
        "milestones": milestones, "run_summaries": run_summaries,
        "risks": project_risks(project, runs, infos, open_devs, milestones),
        "rule_status": "demo_unconfirmed",
    }


def project_risks(project, runs, infos, open_devs, milestones) -> list[dict]:
    risks: list[dict] = []

    def add(code, level, message, **evidence):
        risks.append({"risk_code": code, "level": level, "message": message, "evidence": evidence})

    elig = [r for r in runs if r.status in ELIGIBLE]
    qc_fail = [r.id for r in elig if infos[r.id].qc.verdict is Verdict.FAIL]
    isr_fail = [r.id for r in elig if infos[r.id].isr.verdict is Verdict.FAIL]
    pending = [r.id for r in elig if infos[r.id].accepted is Verdict.PENDING or infos[r.id].isr.verdict is Verdict.PENDING]
    if qc_fail:
        add("RISK-QC-FAIL", "high", f"{len(qc_fail)} 个分析批 QC 未通过", run_ids=qc_fail)
    if isr_fail:
        add("RISK-ISR-FAIL", "high", f"{len(isr_fail)} 个分析批 ISR 未通过", run_ids=isr_fail)
    major = [d for d in open_devs if d.severity in ("major", "critical")]
    if major:
        add("RISK-OPEN-MAJOR-DEV", "high", f"{len(major)} 项重要及以上偏差未关闭", deviation_ids=[d.id for d in major])
    for m in milestones:
        if m["state"] == "overdue":
            add("RISK-MILESTONE-OVERDUE", "high", f"{m['type']} 已逾期 {-m['days_to_due']} 天",
                milestone=m["type"], planned_date=m["planned_date"], days_overdue=-m["days_to_due"])
        elif m["state"] == "due_soon":
            add("RISK-MILESTONE-DUE-SOON", "medium", f"{m['type']} 将在 {m['days_to_due']} 天内到期",
                milestone=m["type"], planned_date=m["planned_date"], days_to_due=m["days_to_due"])
    if pending:
        add("RISK-PENDING-REVIEW", "medium", f"{len(pending)} 个分析批存在待复核结果", run_ids=pending)
    minor = [d for d in open_devs if d.severity == "minor"]
    if minor:
        add("RISK-OPEN-MINOR-DEV", "low", f"{len(minor)} 项轻微偏差未关闭", deviation_ids=[d.id for d in minor])
    inprog = [r.id for r in runs if r.status == "in_progress"]
    if inprog:
        add("RISK-RUNS-IN-PROGRESS", "low", f"{len(inprog)} 个分析批进行中，未计入通过率", run_ids=inprog)
    if project.planned_run_count == 0:
        add("RISK-NO-PLAN", "low", "未设置计划分析批数，无法计算完成进度")
    risks.sort(key=lambda r: (LEVEL_ORDER[r["level"]], r["risk_code"]))
    return risks


def overview(session: Session, rule_sets: dict[str, RuleSet], as_of: date) -> dict:
    projects = session.scalars(select(Project).order_by(Project.id)).all()
    items = [project_metrics(session, p, rule_sets, as_of) for p in projects]
    tot = Counter()
    verd = {k: Counter() for k in ("qc", "isr", "accepted")}
    risk_levels = Counter()
    for m in items:
        for k in ("total", "valid"):
            tot[f"samples_{k}"] += m["samples"][k]
        for k in ("planned", "existing", "completed", "under_review", "in_progress"):
            tot[f"runs_{k}"] += m["runs"][k]
        tot["open_deviations"] += m["deviations"]["open"]
        tot["open_major_plus"] += m["deviations"]["open_by_severity"]["major"] + m["deviations"]["open_by_severity"]["critical"]
        for k in verd:
            for s in ("pass", "fail", "pending_review"):
                verd[k][s] += m[k][s]
        for r in m["risks"]:
            risk_levels[r["level"]] += 1
    totals = {
        "projects": len(items),
        "samples": {"total": tot["samples_total"], "valid": tot["samples_valid"]},
        "runs": {k: tot[f"runs_{k}"] for k in ("planned", "existing", "completed", "under_review", "in_progress")},
        "progress": ratio(tot["runs_completed"], tot["runs_planned"], "no_planned_runs"),
        "open_deviations": tot["open_deviations"], "open_major_plus": tot["open_major_plus"],
        "risks_by_level": {lv: risk_levels[lv] for lv in ("high", "medium", "low")},
    }
    for k in verd:   # 汇总通过率 = 各项目计数相加后再除，而不是各项目通过率的平均
        totals[k] = {**{s: verd[k][s] for s in ("pass", "fail", "pending_review")},
                     "pass_rate": ratio(verd[k]["pass"], verd[k]["pass"] + verd[k]["fail"], "no_decided_runs")}
    return {"as_of": as_of.isoformat(), "totals": totals, "projects": items}
