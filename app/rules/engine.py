"""规则引擎：QC / 校准 / ISR 判定的唯一来源。

判定三态 pass / fail / pending_review。对每个数据点分为 PASS / FAIL / BORDERLINE（恰在阈值）/
UNKNOWN（缺值）；BORDERLINE 与 UNKNOWN 在“最乐观”和“最悲观”两种假设下各算一次批级判定，
两者一致才给出 pass/fail，不一致则 pending_review（不自动放行）。
阈值来自 config/rules/demo_rules.yaml，状态为 demo_unconfirmed。
"""
from dataclasses import dataclass, field
from enum import Enum
from math import isclose
from pathlib import Path
from typing import Iterable, Optional

import yaml

from app.settings import ROOT

DEFAULT_RULES_PATH = ROOT / "config" / "rules" / "demo_rules.yaml"
EPS = 1e-9
QC_LEVELS = ("LLOQ", "LQC", "MQC", "HQC")


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending_review"


class PointStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BORDERLINE = "borderline"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuleSet:
    rule_set_id: str
    method_type: str
    rules: dict  # rule_id -> {value, applies_to, source_note, status, to_confirm}

    def v(self, rule_id: str) -> dict:
        return self.rules[rule_id]["value"]

    @property
    def statuses(self) -> dict[str, str]:
        return {rid: r["status"] for rid, r in self.rules.items()}


@dataclass(frozen=True)
class CriterionResult:
    verdict: Verdict
    rule_ids: tuple[str, ...]
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RunAssessment:
    calibration: CriterionResult
    qc: CriterionResult
    isr: CriterionResult

    @property
    def accepted(self) -> Verdict:
        """分析批接受 = 校准 + QC；ISR 是独立的结果评价，不并入批接受。"""
        vs = {self.calibration.verdict, self.qc.verdict}
        if Verdict.FAIL in vs:
            return Verdict.FAIL
        if Verdict.PENDING in vs:
            return Verdict.PENDING
        return Verdict.PASS


def load_rule_sets(path: Path | None = None) -> dict[str, RuleSet]:
    data = yaml.safe_load(Path(path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
    return {
        rsid: RuleSet(rsid, body["method_type"], body["rules"])
        for rsid, body in data["rule_sets"].items()
    }


def bias_pct(measured: Optional[float], nominal: float) -> Optional[float]:
    if measured is None:
        return None
    return round((measured / nominal - 1) * 100, 6)


def classify(bias: Optional[float], limit: float) -> PointStatus:
    if bias is None:
        return PointStatus.UNKNOWN
    r = round(abs(bias), 6)
    if isclose(r, limit, abs_tol=EPS):
        return PointStatus.BORDERLINE
    return PointStatus.PASS if r < limit else PointStatus.FAIL


def _decide(ok_min: bool, ok_max: bool) -> Verdict:
    if ok_min and ok_max:
        return Verdict.PASS
    if not ok_min and not ok_max:
        return Verdict.FAIL
    return Verdict.PENDING


def _counts(statuses: Iterable[PointStatus]) -> dict[str, int]:
    out = {s.value: 0 for s in PointStatus}
    for s in statuses:
        out[s.value] += 1
    return out


def _pass_range(c: dict[str, int]) -> tuple[int, int]:
    """(最悲观通过数, 最乐观通过数)。"""
    return c["pass"], c["pass"] + c["borderline"] + c["unknown"]


def evaluate_calibration(points: list[tuple[float, Optional[float]]], rs: RuleSet) -> CriterionResult:
    """points: [(nominal, back_calc_conc)]。"""
    acc, mn = rs.v("CAL-ACC"), rs.v("CAL-MIN")
    ids = ("CAL-ACC", "CAL-MIN")
    n = len(points)
    if n < mn["min_levels"]:
        return CriterionResult(Verdict.PENDING, ids, {"reason": "insufficient_levels", "n": n})
    lo = min(p[0] for p in points)
    hi = max(p[0] for p in points)
    rows, statuses = [], []
    for nominal, bc in points:
        edge = ("lowest" in acc["edge_levels"] and nominal == lo) or (
            "highest" in acc["edge_levels"] and nominal == hi
        )
        limit = acc["edge_acc_pct"] if edge else acc["acc_pct"]
        b = bias_pct(bc, nominal)
        st = classify(b, limit)
        statuses.append(st)
        rows.append({"nominal": nominal, "bias_pct": b, "limit_pct": limit, "status": st.value})
    c = _counts(statuses)
    lo_p, hi_p = _pass_range(c)
    ok = lambda p: p / n >= mn["min_fraction"] - EPS  # noqa: E731
    return CriterionResult(_decide(ok(lo_p), ok(hi_p)), ids, {"n": n, **c, "points": rows})


def evaluate_qc(samples: list[tuple[str, float, Optional[float]]], rs: RuleSet) -> CriterionResult:
    """samples: [(level, nominal, measured)]。"""
    acc, run = rs.v("QC-ACC"), rs.v("QC-RUN")
    ids = ("QC-ACC", "QC-RUN")
    by_level: dict[str, list[PointStatus]] = {lv: [] for lv in QC_LEVELS}
    rows = []
    for level, nominal, measured in samples:
        limit = acc["lloq_acc_pct"] if level == "LLOQ" else acc["acc_pct"]
        b = bias_pct(measured, nominal)
        st = classify(b, limit)
        by_level[level].append(st)
        rows.append({"level": level, "nominal": nominal, "bias_pct": b, "limit_pct": limit, "status": st.value})
    missing = [lv for lv, sts in by_level.items() if not sts]
    if missing:
        return CriterionResult(Verdict.PENDING, ids, {"reason": "missing_levels", "missing_levels": missing})
    rng = {lv: _pass_range(_counts(sts)) for lv, sts in by_level.items()}
    totals = {lv: len(sts) for lv, sts in by_level.items()}
    total = sum(totals.values())

    def ok(idx: int) -> bool:
        passed = sum(r[idx] for r in rng.values())
        return passed / total >= run["run_min_fraction"] - EPS and all(
            rng[lv][idx] / totals[lv] >= run["level_min_fraction"] - EPS for lv in totals
        )

    detail = {
        "n": total,
        "levels": {lv: {"n": totals[lv], **_counts(by_level[lv])} for lv in QC_LEVELS},
        "samples": rows,
    }
    return CriterionResult(_decide(ok(0), ok(1)), ids, detail)


def isr_diff_pct(original: Optional[float], repeat: Optional[float]) -> Optional[float]:
    """两次结果之差 /两次均值 ×100。"""
    if original is None or repeat is None:
        return None
    mean = (original + repeat) / 2
    return round((repeat - original) / mean * 100, 6)


def evaluate_isr(records: list[tuple[Optional[float], Optional[float]]], rs: RuleSet) -> CriterionResult:
    """records: [(original_conc, repeat_conc)]。"""
    diff, rate, nmin = rs.v("ISR-DIFF"), rs.v("ISR-RATE"), rs.v("ISR-N")
    ids = ("ISR-DIFF", "ISR-RATE", "ISR-N")
    n = len(records)
    statuses = [classify(isr_diff_pct(o, r), diff["diff_pct"]) for o, r in records]
    c = _counts(statuses)
    if n < nmin["min_n"]:
        return CriterionResult(Verdict.PENDING, ids, {"reason": "insufficient_n", "n": n, **c})
    lo_p, hi_p = _pass_range(c)
    ok = lambda p: p / n >= rate["min_rate"] - EPS  # noqa: E731
    return CriterionResult(
        _decide(ok(lo_p), ok(hi_p)),
        ids,
        {"n": n, **c, "pass_rate_min": round(lo_p / n, 4), "pass_rate_max": round(hi_p / n, 4)},
    )


def evaluate_run(
    calibration: list[tuple[float, Optional[float]]],
    qc: list[tuple[str, float, Optional[float]]],
    isr: list[tuple[Optional[float], Optional[float]]],
    rs: RuleSet,
) -> RunAssessment:
    return RunAssessment(
        evaluate_calibration(calibration, rs), evaluate_qc(qc, rs), evaluate_isr(isr, rs)
    )
