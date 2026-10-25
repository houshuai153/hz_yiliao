"""模拟种子数据生成器（固定随机种子，纯函数，不碰数据库）。

全部数据为模拟数据：化合物/生物制品名称、受试者编码、偏差描述均为虚构。
各分析批按 profile 植入通过 / 失败 / 待复核案例，供看板、报告与评测使用。
"""
import math
import random
from datetime import date

SEED = 20260101
TIMEPOINTS = ["0h", "0.5h", "1h", "2h", "4h", "8h", "12h", "24h"]
PK_PROFILE = [0.0, 0.35, 0.8, 1.0, 0.7, 0.35, 0.12, 0.03]  # 相对 Cmax 的模拟曲线
RULE_SET_BY_METHOD = {"LC-MS/MS": "RS-CHROM-DEMO-v1", "LBA": "RS-LBA-DEMO-v1"}

PROJECTS = [
    {
        "id": "PRJ-SM-001", "short": "SM001",
        "title": "DEMO-API-101 人血浆 LC-MS/MS 方法验证（模拟项目）",
        "modality": "small_molecule", "analyte": "DEMO-API-101", "matrix": "人血浆(K2EDTA)",
        "method_type": "LC-MS/MS", "unit": "ng/mL", "lloq": 1.0, "uloq": 500.0,
        "qc_nominals": {"LLOQ": 1.0, "LQC": 3.0, "MQC": 60.0, "HQC": 400.0},
        "phase": "validation", "planned_run_count": 3, "is_simulated": True,
        "internal_standard": "DEMO-IS-101-d4（稳定同位素标记内标）",
        "sample_prep": "蛋白沉淀法（模拟）",
        "instrument_setup": "模拟 LC-MS/MS 系统 SIM-LCMS-01；模拟 C18 色谱柱（2.1×50 mm，1.7 µm）",
        "reagent_kit": None,
    },
    {
        "id": "PRJ-BIO-002", "short": "BIO002",
        "title": "DEMO-mAb-202 人血清 ELISA 药代样品分析（模拟项目）",
        "modality": "biologic", "analyte": "DEMO-mAb-202", "matrix": "人血清",
        "method_type": "LBA", "unit": "ng/mL", "lloq": 50.0, "uloq": 3200.0,
        "qc_nominals": {"LLOQ": 50.0, "LQC": 150.0, "MQC": 800.0, "HQC": 2400.0},
        "phase": "sample_analysis", "planned_run_count": 5, "is_simulated": True,
        "internal_standard": None,  # LBA 不使用内标
        "sample_prep": "血清样品经缓冲液稀释后上板（模拟）",
        "instrument_setup": "模拟酶标仪 SIM-READER-01",
        "reagent_kit": "模拟 ELISA 试剂盒，批号 KIT-DEMO-2606",
    },
    {
        "id": "PRJ-SM-003", "short": "SM003",
        "title": "DEMO-API-303 大鼠血浆 LC-MS/MS 毒代样品分析（模拟项目）",
        "modality": "small_molecule", "analyte": "DEMO-API-303", "matrix": "大鼠血浆(K2EDTA)",
        "method_type": "LC-MS/MS", "unit": "ng/mL", "lloq": 0.5, "uloq": 250.0,
        "qc_nominals": {"LLOQ": 0.5, "LQC": 1.5, "MQC": 40.0, "HQC": 200.0},
        "phase": "sample_analysis", "planned_run_count": 4, "is_simulated": True,
        "internal_standard": "DEMO-IS-303-d5",
        "sample_prep": "液液萃取（模拟）",
        "instrument_setup": None,  # 故意缺失：模拟“仪器信息尚未录入”
        "reagent_kit": None,
    },
]

# (项目, 序号, 日期, 状态, profile)
RUN_PLANS = [
    ("PRJ-SM-001", 1, date(2026, 6, 2), "completed", "clean"),
    ("PRJ-SM-001", 2, date(2026, 6, 9), "completed", "clean"),
    ("PRJ-SM-001", 3, date(2026, 6, 16), "under_review", "borderline_qc"),
    ("PRJ-BIO-002", 1, date(2026, 7, 7), "completed", "clean"),
    ("PRJ-BIO-002", 2, date(2026, 7, 14), "completed", "isr_fail"),
    ("PRJ-BIO-002", 3, date(2026, 7, 21), "completed", "isr_one_fail"),
    ("PRJ-BIO-002", 4, date(2026, 10, 12), "in_progress", "in_progress"),
    ("PRJ-SM-003", 1, date(2026, 8, 18), "completed", "clean"),
    ("PRJ-SM-003", 2, date(2026, 9, 1), "completed", "qc_fail"),
    ("PRJ-SM-003", 3, date(2026, 9, 8), "under_review", "cal_pending"),
]

DEVIATIONS = [
    ("DEV-2026-001", "PRJ-SM-003", "PRJ-SM-003-R02", "QC失败", "major", "open",
     "第2分析批 HQC 水平3个重复中2个超出演示阈值，批被拒绝，调查中", date(2026, 9, 2), None),
    ("DEV-2026-002", "PRJ-BIO-002", "PRJ-BIO-002-R02", "ISR不达标", "major", "open",
     "第2分析批 ISR 合格比例低于演示阈值，待调查根本原因", date(2026, 7, 20), None),
    ("DEV-2026-003", "PRJ-SM-001", "PRJ-SM-001-R03", "样品处理", "minor", "closed",
     "第3分析批个别 QC 样品复溶时间超出操作规程，已评估影响并关闭", date(2026, 6, 17), date(2026, 6, 24)),
    ("DEV-2026-004", "PRJ-BIO-002", None, "稳定性", "minor", "open",
     "部分研究样品冻融次数接近稳定性考察上限，待评估", date(2026, 10, 5), None),
    ("DEV-2026-005", "PRJ-SM-003", "PRJ-SM-003-R01", "仪器维护", "minor", "closed",
     "质谱计划性维护延期两天，已补做性能确认并关闭", date(2026, 8, 19), date(2026, 8, 26)),
]

MILESTONES = [
    ("PRJ-SM-001", "validation_report", date(2026, 10, 15), "in_progress", None),
    ("PRJ-BIO-002", "validation_report", date(2026, 6, 30), "delivered", date(2026, 6, 28)),
    ("PRJ-BIO-002", "sample_report", date(2026, 12, 20), "not_started", None),
    ("PRJ-SM-003", "sample_report", date(2026, 11, 1), "in_progress", None),
]


def _sig(x: float, n: int = 4) -> float:
    return float(f"{x:.{n}g}")


def _bias(measured, nominal):
    return None if measured is None else round((measured / nominal - 1) * 100, 2)


def _cal_nominals(lloq: float, uloq: float, n: int) -> list[float]:
    return [float(f"{lloq * (uloq / lloq) ** (i / (n - 1)):.3g}") for i in range(n)]


def _gen_calibration(rng, run_id, proj, profile):
    n = 8 if proj["method_type"] == "LBA" else rng.choice([6, 7, 8])
    nominals = _cal_nominals(proj["lloq"], proj["uloq"], n)
    edges = {nominals[0]} | ({nominals[-1]} if proj["method_type"] == "LBA" else set())
    rows = []
    for i, nom in enumerate(nominals):
        span = 8 if nom in edges else 6
        bc = _sig(nom * (1 + rng.uniform(-span, span) / 100))
        rows.append({"run_id": run_id, "level_no": i + 1, "nominal": nom, "back_calc_conc": bc})
    if profile == "cal_pending":  # 2 个缺值 + 1 个超限 -> 悲观判 fail / 乐观判 pass -> 待复核
        rows[3]["back_calc_conc"] = None
        rows[5]["back_calc_conc"] = None
        rows[2]["back_calc_conc"] = _sig(rows[2]["nominal"] * 1.175)
    for r in rows:
        r["bias_pct"] = _bias(r["back_calc_conc"], r["nominal"])
    return rows


def _gen_qc(rng, run_id, proj, profile):
    rows = []
    for level, nom in proj["qc_nominals"].items():
        span = 10 if level == "LLOQ" else 8
        devs = [rng.uniform(-span, span) for _ in range(3)]
        planted = level == "HQC" and profile in ("qc_fail", "borderline_qc")
        if level == "HQC" and profile == "qc_fail":
            devs = [19.0, 24.0, 3.0]
        if level == "HQC" and profile == "borderline_qc":
            devs = [22.0, 15.0, -3.0]  # 一个超限、一个恰在阈值、一个合格
        for rep, d in enumerate(devs, start=1):
            raw = nom * (1 + d / 100)
            measured = round(raw, 6) if planted else _sig(raw)  # 植入值保留精确，才能恰在阈值
            rows.append({
                "run_id": run_id, "level": level, "replicate": rep, "nominal": nom,
                "measured": measured, "bias_pct": _bias(measured, nom),
            })
    return rows


def _gen_samples(rng, run_id, run_idx, proj):
    n = rng.randint(30, 50)
    lloq, uloq = proj["lloq"], proj["uloq"]
    rows, cmax = [], {}
    for i in range(n):
        subj = (run_idx - 1) * 10 + i // 8 + 1
        if subj not in cmax:
            cmax[subj] = math.exp(rng.gauss(math.log(uloq * 0.25), 0.5))
        tp_i = i % 8
        raw = PK_PROFILE[tp_i] * cmax[subj] * math.exp(rng.gauss(0, 0.1))
        flag, conc = "ok", _sig(raw, 3)
        if raw < lloq:
            flag, conc = "BLQ", None
        elif raw > uloq:
            flag = "above_ULOQ"
        elif rng.random() < 0.03:
            flag = "reanalysis"
        elif rng.random() < 0.015:
            flag, conc = "missing", None
        rows.append({
            "id": f"{run_id}-S{i + 1:02d}", "run_id": run_id,
            "subject_code": f"SUBJ-{proj['short']}-{subj:03d}",
            "timepoint": TIMEPOINTS[tp_i], "conc": conc, "flag": flag,
        })
    return rows


def _gen_isr(rng, run_id, proj, samples, profile):
    cands = sorted(s["id"] for s in samples if s["flag"] == "ok" and s["conc"] is not None)
    k = min(rng.randint(10, 15), len(cands))
    if k < 10:
        raise ValueError(f"{run_id}: 可用于 ISR 的样品不足 10 条（{len(cands)}）")
    chosen = rng.sample(cands, k)
    conc = {s["id"]: s["conc"] for s in samples}
    n_fail = {"isr_fail": math.ceil(k * 0.5), "isr_one_fail": 1}.get(profile, 0)
    rows = []
    for idx, sid in enumerate(chosen):
        orig = conc[sid]
        if idx < n_fail:
            repeat = _sig(orig * rng.choice([1.5, 0.6]), 3)  # 差值约 ±40%
        else:
            repeat = _sig(orig * (1 + rng.uniform(-0.12, 0.12)), 3)
        rows.append({"sample_id": sid, "run_id": run_id, "original_conc": orig, "repeat_conc": repeat})
    if profile == "in_progress":  # 后半尚未复测
        for r in rows[k // 2:]:
            r["repeat_conc"] = None
    for r in rows:
        if r["repeat_conc"] is None:
            r["diff_pct"] = None
        else:
            mean = (r["original_conc"] + r["repeat_conc"]) / 2
            r["diff_pct"] = round((r["repeat_conc"] - r["original_conc"]) / mean * 100, 2)
    return rows


def generate(seed: int = SEED) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    projects = {p["id"]: p for p in PROJECTS}
    out: dict[str, list[dict]] = {
        k: [] for k in ("projects", "runs", "calibration_points", "qc_samples",
                        "study_samples", "isr_records", "deviations", "milestones")
    }
    for p in PROJECTS:
        out["projects"].append({k: v for k, v in p.items() if k not in ("short", "qc_nominals")})
    for pid, idx, run_date, status, profile in RUN_PLANS:
        proj = projects[pid]
        run_id = f"{pid}-R{idx:02d}"
        out["runs"].append({
            "id": run_id, "project_id": pid, "run_date": run_date, "status": status,
            "rule_set_id": RULE_SET_BY_METHOD[proj["method_type"]],
        })
        out["calibration_points"] += _gen_calibration(rng, run_id, proj, profile)
        out["qc_samples"] += _gen_qc(rng, run_id, proj, profile)
        samples = _gen_samples(rng, run_id, idx, proj)
        out["study_samples"] += samples
        out["isr_records"] += _gen_isr(rng, run_id, proj, samples, profile)
    for did, pid, rid, cat, sev, st, desc, opened, closed in DEVIATIONS:
        out["deviations"].append({
            "id": did, "project_id": pid, "run_id": rid, "category": cat, "severity": sev,
            "status": st, "description": desc, "opened_at": opened, "closed_at": closed,
        })
    for pid, typ, planned, st, done in MILESTONES:
        out["milestones"].append({
            "project_id": pid, "type": typ, "planned_date": planned, "status": st, "completed_date": done,
        })
    return out
