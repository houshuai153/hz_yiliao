"""看板图表所需的聚合数据。判定一律取自 metrics（规则引擎），这里只做分组计数与累计，不另算判定。

口径：
- 进度对比：各项目按分析批**当前状态**计数（completed / under_review / in_progress）；planned 为项目计划批数，
  unscheduled = max(planned - existing, 0)，即计划内尚未建立的批数；existing > planned 时 exceeds_plan=True。
- 时间曲线：**按分析批日期（run_date）累计的批次数**，各批按其当前状态着色。库里没有状态变更历史，
  不能还原“某日历史上已完成多少批”，所以这不是历史完成趋势；也不受 as_of 过滤（as_of 只影响里程碑 / 偏差账龄），
  晚于 as_of 的日期只用 after_as_of 标出，不删除、不补点。
- 样本构成：study_sample.flag 的计数；total=0 时 share 为 None（不给虚假的 100%）。
"""
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalyticalRun, StudySample

SAMPLE_FLAGS = ("ok", "BLQ", "above_ULOQ", "reanalysis", "missing")
RUN_STATUSES = ("completed", "under_review", "in_progress")
TIMELINE_BASIS = "run_date_cumulative_current_status"
NOTES = {
    "progress": "按分析批当前状态计数；计划批数来自项目设置；“未建批”= 计划数 - 已建批数（不小于 0）。",
    "timeline": "横轴为分析批日期（run_date），纵轴为截至该日期已有的分析批累计数，颜色为各批**当前**状态。"
                "库中没有状态变更历史，因此不代表历史上某日的完成情况；不受“统计截止日期”过滤；日期之间不补点。",
    "samples": "统计所选项目全部分析批的待测样品结果标记；占比 = 该标记数 / 样品总数。",
}


def _timeline(runs: list[tuple[str, date, str]], as_of: date) -> list[dict]:
    by_date: dict[date, list[tuple[str, str]]] = defaultdict(list)
    for rid, d, status in runs:
        by_date[d].append((rid, status))
    points, cum, cum_status = [], 0, Counter()
    for d in sorted(by_date):
        cum += len(by_date[d])
        cum_status.update(s for _, s in by_date[d])
        points.append({
            "date": d.isoformat(), "run_ids": [r for r, _ in by_date[d]], "runs_on_date": len(by_date[d]),
            # 每个批在累计曲线上的位置（该批计入后的累计数），供图上按状态标注各批，前端不必自行换算
            "runs": [{"id": r, "status": st, "cumulative_rank": cum - len(by_date[d]) + i}
                     for i, (r, st) in enumerate(by_date[d], start=1)],
            "cumulative": cum, "cumulative_by_status": {s: cum_status[s] for s in RUN_STATUSES},
            "after_as_of": d > as_of,
        })
    return points


def _composition(counter: Counter) -> dict:
    total = sum(counter[f] for f in SAMPLE_FLAGS)
    return {"total": total, "parts": [{"flag": f, "count": counter[f],
                                       "share": (counter[f] / total) if total else None} for f in SAMPLE_FLAGS]}


def chart_data(session: Session, items: list[dict], as_of: date) -> dict:
    """items 为 metrics.overview()["projects"]。"""
    runs = session.scalars(select(AnalyticalRun).order_by(AnalyticalRun.project_id, AnalyticalRun.run_date,
                                                           AnalyticalRun.id)).all()
    flags: dict[str, Counter] = defaultdict(Counter)
    for run_id, flag in session.execute(select(StudySample.run_id, StudySample.flag)):
        flags[run_id][flag] += 1
    runs_by_project: dict[str, list[AnalyticalRun]] = defaultdict(list)
    for r in runs:
        runs_by_project[r.project_id].append(r)

    progress, run_detail, timeline, composition = [], {}, {}, {}
    all_flags: Counter = Counter()
    for m in items:
        pid, rb = m["project"]["id"], m["runs"]
        progress.append({
            "project_id": pid, "title": m["project"]["title"], "planned": rb["planned"], "existing": rb["existing"],
            "completed": rb["completed"], "under_review": rb["under_review"], "in_progress": rb["in_progress"],
            "unscheduled": max(rb["planned"] - rb["existing"], 0), "exceeds_plan": rb["existing"] > rb["planned"],
        })
        summ = {s["id"]: s for s in m["run_summaries"]}
        run_detail[pid] = [{
            "id": r.id, "run_date": r.run_date.isoformat(), "status": r.status,
            "samples": _composition(flags[r.id]), "calibration": summ[r.id]["calibration"], "qc": summ[r.id]["qc"],
            "accepted": summ[r.id]["accepted"], "isr": summ[r.id]["isr"],
        } for r in runs_by_project[pid]]
        timeline[pid] = _timeline([(r.id, r.run_date, r.status) for r in runs_by_project[pid]], as_of)
        proj_flags = sum((flags[r.id] for r in runs_by_project[pid]), Counter())
        composition[pid] = _composition(proj_flags)
        all_flags.update(proj_flags)
    timeline["ALL"] = _timeline([(r.id, r.run_date, r.status) for r in runs], as_of)
    composition["ALL"] = _composition(all_flags)
    return {"as_of": as_of.isoformat(), "notes": NOTES, "timeline_basis": TIMELINE_BASIS, "progress": progress,
            "runs": run_detail, "timeline": timeline, "sample_composition": composition}
