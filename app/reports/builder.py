"""报告构建器：数据库 → 规则引擎 → 事实（Fact）→ 模板句 → ReportDoc。

没有任何生成式模型参与：业务结论句全部来自带槽位的模板，槽位只能由 Fact 填充；
缺失值显示 `[待补充: 字段]`，绝不估算或补造。判定一律来自 app.rules.engine，这里不重复计算。
"""
import json
import statistics
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticalRun, CalibrationPoint, Deviation, ISRRecord, Project, QCSample, StudySample,
)
from app.reports.model import (
    FactStore, ReportDoc, Section, Sentence, TableBlock, placeholder,
)
from app.rules.engine import (
    QC_LEVELS, RuleSet, Verdict, bias_pct, evaluate_calibration, evaluate_isr, evaluate_qc,
)

VERDICT_ZH = {"pass": "通过", "fail": "未通过", "pending_review": "待复核"}
STATUS_ZH = {"completed": "已完成", "under_review": "复核中", "in_progress": "进行中"}
SEVERITY_ZH = {"minor": "轻微", "major": "重要", "critical": "严重"}
KINDS = {"validation": "方法学验证报告（AI 辅助草稿）", "sample_analysis": "样品分析报告（AI 辅助草稿）"}
DASH = "—"


class ReportError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def num(v: float) -> str:
    return format(v, ".6g")


def pct_signed(v: float) -> str:
    return f"{v:+.1f}%"


def pct(v: float) -> str:
    return f"{v:.1f}%"


class _Builder:
    def __init__(self, session: Session, project: Project, runs: list[AnalyticalRun],
                 rule_sets: dict[str, RuleSet], kind: str):
        self.s, self.p, self.runs, self.rule_sets, self.kind = session, project, runs, rule_sets, kind
        self.store = FactStore()
        self.missing: list[str] = []
        self.rule_ids: set[str] = set()
        self.run_info: dict[str, dict] = {}

    # ---------- 工具 ----------
    def add(self, key, label, value, table, rows, rule=None, unit="", display=None):
        if display is None and value is not None:
            display = str(value)
        return self.store.add(key, label, value, source_table=table, source_rows=rows,
                              rule_id=rule, unit=unit, display=display)

    def fsent(self, template: str, slots: dict[str, str], **plain) -> Sentence:
        vals, used, miss = {}, [], []
        for name, key in slots.items():
            f = self.store.get(key)
            used.append(key)
            if f.display is None:
                vals[name] = placeholder(f.label)
                miss.append(f.label)
                self.missing.append(f.label)
            else:
                vals[name] = f.display
        return Sentence(template.format(**vals, **plain), "fact", used, miss)

    def static(self, text: str) -> Sentence:
        return Sentence(text, "static")

    def todo(self, label: str, field_name: str) -> Sentence:
        self.missing.append(field_name)
        return Sentence(f"{label}：{placeholder(field_name)}", "placeholder", [], [field_name])

    # ---------- 单个分析批 ----------
    def compute_run(self, run: AnalyticalRun) -> None:
        rs = self.rule_sets[run.rule_set_id]
        rid, s = run.id, self.s
        cal_rows = s.scalars(select(CalibrationPoint).where(CalibrationPoint.run_id == rid)
                             .order_by(CalibrationPoint.level_no)).all()
        qc_rows = s.scalars(select(QCSample).where(QCSample.run_id == rid)
                            .order_by(QCSample.level, QCSample.replicate)).all()
        smp_rows = s.scalars(select(StudySample).where(StudySample.run_id == rid)
                             .order_by(StudySample.id)).all()
        isr_rows = s.scalars(select(ISRRecord).where(ISRRecord.run_id == rid)
                             .order_by(ISRRecord.sample_id)).all()
        cal = evaluate_calibration([(c.nominal, c.back_calc_conc) for c in cal_rows], rs)
        qc = evaluate_qc([(q.level, q.nominal, q.measured) for q in qc_rows], rs)
        isr = evaluate_isr([(i.original_conc, i.repeat_conc) for i in isr_rows], rs)
        accepted = (Verdict.FAIL if Verdict.FAIL in (cal.verdict, qc.verdict)
                    else Verdict.PENDING if Verdict.PENDING in (cal.verdict, qc.verdict) else Verdict.PASS)
        for r in (cal, qc, isr):
            self.rule_ids.update(r.rule_ids)
        self.run_info[rid] = {"run": run, "cal": cal, "qc": qc, "isr": isr, "accepted": accepted,
                              "cal_rows": cal_rows, "qc_rows": qc_rows}

        cal_src = [f"{rid}#L{c.level_no}" for c in cal_rows]
        cd = cal.detail
        self.add(f"{rid}.cal.n", "校准点数", len(cal_rows), "calibration_point", cal_src, "CAL-MIN")
        if cal_rows:
            self.add(f"{rid}.cal.min", "校准曲线最低标示浓度", min(c.nominal for c in cal_rows),
                     "calibration_point", cal_src, display=num(min(c.nominal for c in cal_rows)))
            self.add(f"{rid}.cal.max", "校准曲线最高标示浓度", max(c.nominal for c in cal_rows),
                     "calibration_point", cal_src, display=num(max(c.nominal for c in cal_rows)))
        else:
            self.add(f"{rid}.cal.min", "校准曲线最低标示浓度", None, "calibration_point", cal_src)
            self.add(f"{rid}.cal.max", "校准曲线最高标示浓度", None, "calibration_point", cal_src)
        has = "pass" in cd
        self.add(f"{rid}.cal.pass", "校准点合格数", cd["pass"] if has else None, "calibration_point", cal_src, "CAL-ACC")
        self.add(f"{rid}.cal.fail", "校准点超限数", cd["fail"] if has else None, "calibration_point", cal_src, "CAL-ACC")
        self.add(f"{rid}.cal.pending", "校准点缺值或恰在阈值数",
                 (cd["borderline"] + cd["unknown"]) if has else None, "calibration_point", cal_src, "CAL-ACC")
        self.add(f"{rid}.cal.verdict", "校准曲线判定", cal.verdict.value, "calibration_point", cal_src,
                 "CAL-ACC", display=VERDICT_ZH[cal.verdict.value])
        n_missing_bc = sum(c.back_calc_conc is None for c in cal_rows)
        if n_missing_bc:
            self.missing.append(f"{rid} 校准点回算浓度（{n_missing_bc} 处）")

        qc_src = [f"{rid}#{q.level}#{q.replicate}" for q in qc_rows]
        levels = qc.detail.get("levels", {})
        self.add(f"{rid}.qc.n", "QC 样品数", len(qc_rows), "qc_sample", qc_src, "QC-RUN")
        self.add(f"{rid}.qc.pass", "QC 合格数",
                 sum(v["pass"] for v in levels.values()) if levels else None, "qc_sample", qc_src, "QC-ACC")
        self.add(f"{rid}.qc.verdict", "QC 判定", qc.verdict.value, "qc_sample", qc_src, "QC-RUN",
                 display=VERDICT_ZH[qc.verdict.value])
        for lv in QC_LEVELS:
            vals = [bias_pct(q.measured, q.nominal) for q in qc_rows if q.level == lv and q.measured is not None]
            v = statistics.fmean(vals) if vals else None
            self.add(f"{rid}.qc.bias.{lv}", f"{rid} {lv} 水平 QC 平均相对偏差", v, "qc_sample",
                     [x for x in qc_src if f"#{lv}#" in x], "QC-ACC", display=pct_signed(v) if v is not None else None)
        n_missing_qc = sum(q.measured is None for q in qc_rows)
        if n_missing_qc:
            self.missing.append(f"{rid} QC 测得浓度（{n_missing_qc} 处）")

        flags = Counter(x.flag for x in smp_rows)
        smp_src = [x.id for x in smp_rows]
        for key, label, val in (("n", "待测样品数", len(smp_rows)), ("ok", "有效结果数", flags["ok"]),
                                ("blq", "低于定量下限（BLQ）样品数", flags["BLQ"]),
                                ("above", "高于定量上限样品数", flags["above_ULOQ"]),
                                ("rean", "标记再分析样品数", flags["reanalysis"]),
                                ("missing", "缺失样品数", flags["missing"])):
            self.add(f"{rid}.smp.{key}", label, val, "study_sample", smp_src)

        isr_src = [i.sample_id for i in isr_rows]
        idt = isr.detail
        n_isr = len(isr_rows)
        self.add(f"{rid}.isr.n", "ISR 样品数", n_isr, "isr_record", isr_src, "ISR-N")
        self.add(f"{rid}.isr.pass", "ISR 合格数", idt.get("pass") if n_isr else None, "isr_record", isr_src, "ISR-DIFF")
        self.add(f"{rid}.isr.fail", "ISR 不合格数", idt.get("fail") if n_isr else None, "isr_record", isr_src, "ISR-DIFF")
        self.add(f"{rid}.isr.pending", "ISR 待复核数（缺复测值或恰在阈值）",
                 (idt["borderline"] + idt["unknown"]) if n_isr else None, "isr_record", isr_src, "ISR-DIFF")
        rate = (idt["pass"] / n_isr * 100) if n_isr else None
        self.add(f"{rid}.isr.rate", "ISR 合格比例", rate, "isr_record", isr_src, "ISR-RATE",
                 display=pct(rate) if rate is not None else None)
        self.add(f"{rid}.isr.verdict", "ISR 判定", isr.verdict.value, "isr_record", isr_src, "ISR-RATE",
                 display=VERDICT_ZH[isr.verdict.value])
        self.add(f"{rid}.acc", "批接受判定", accepted.value, "analytical_run", [rid], "QC-RUN",
                 display=VERDICT_ZH[accepted.value])
        self.add(f"{rid}.status", "分析批状态", run.status, "analytical_run", [rid], display=STATUS_ZH[run.status])
        if not n_isr:
            self.missing.append(f"{rid} ISR 记录")

    # ---------- 章节 ----------
    def build(self) -> ReportDoc:
        for run in self.runs:
            self.compute_run(run)
        p = self.p
        rows_p = [p.id]
        self.add("proj.analyte", "分析物", p.analyte, "project", rows_p)
        self.add("proj.matrix", "生物基质", p.matrix, "project", rows_p)
        self.add("proj.method", "分析方法", p.method_type, "project", rows_p)
        self.add("proj.lloq", "LLOQ", p.lloq, "project", rows_p, display=num(p.lloq), unit=p.unit)
        self.add("proj.uloq", "ULOQ", p.uloq, "project", rows_p, display=num(p.uloq), unit=p.unit)
        lc = p.method_type == "LC-MS/MS"
        if lc:  # LBA 不使用内标
            self.add("proj.is", "内标名称", p.internal_standard, "project", rows_p)
        self.add("proj.prep", "样品前处理方法", p.sample_prep, "project", rows_p)
        self.add("proj.instrument", "仪器型号与色谱柱" if lc else "读板仪型号", p.instrument_setup, "project", rows_p)
        if not lc:
            self.add("proj.kit", "试剂盒及批号", p.reagent_kit, "project", rows_p)
        ids = [r.id for r in self.runs]
        done = [r for r in self.runs if r.status != "in_progress"]
        cnt = Counter(self.run_info[r.id]["accepted"].value for r in done)
        self.add("proj.n_runs", "纳入分析批数", len(self.runs), "analytical_run", ids)
        self.add("proj.n_pass", "批接受判定通过的分析批数", cnt["pass"], "analytical_run", [r.id for r in done], "QC-RUN")
        self.add("proj.n_fail", "批接受判定未通过的分析批数", cnt["fail"], "analytical_run", [r.id for r in done], "QC-RUN")
        self.add("proj.n_pending", "批接受判定待复核的分析批数", cnt["pending_review"], "analytical_run", [r.id for r in done], "QC-RUN")
        self.add("proj.n_inprog", "尚未完成的分析批数", len(self.runs) - len(done), "analytical_run",
                 [r.id for r in self.runs if r.status == "in_progress"])
        devs = [d for d in self.s.scalars(select(Deviation).where(Deviation.project_id == p.id)
                                          .order_by(Deviation.id)).all() if d.run_id is None or d.run_id in ids]
        dsrc = [d.id for d in devs]
        open_d = [d for d in devs if d.status == "open"]
        self.add("dev.total", "登记偏差数", len(devs), "deviation", dsrc)
        self.add("dev.open", "未关闭偏差数", len(open_d), "deviation", [d.id for d in open_d])
        self.add("dev.open_major", "未关闭的重要及以上偏差数",
                 sum(d.severity in ("major", "critical") for d in open_d), "deviation",
                 [d.id for d in open_d if d.severity in ("major", "critical")])

        sections = [
            self.sec_purpose(), self.sec_method(), self.sec_calibration(), self.sec_qc(), self.sec_accept(),
            self.sec_validation_stats() if self.kind == "validation" else self.sec_samples(),
            self.sec_isr(), self.sec_deviation(devs), self.sec_conclusion(),
        ]
        sections.append(self.sec_checklist())
        sections.append(self.sec_appendix())
        sections.append(self.sec_signature())
        for i, sec in enumerate(sections, start=1):
            sec.title = f"{i} {sec.title}"
        return ReportDoc(self.kind, KINDS[self.kind], p.id, ids, sections, self.store,
                         list(dict.fromkeys(self.missing)), sorted(self.rule_ids))

    def sec_purpose(self) -> Section:
        what = "方法学验证" if self.kind == "validation" else "研究样品分析"
        return Section("目的与范围", [
            self.static(f"本草稿汇总{what}相关的校准曲线、质控样品、批接受判定、已测样品再分析（ISR）和偏差信息，"
                        "全部数据来自本原型系统中的模拟数据，每条业务结论均标注数据来源。"),
            self.static("本文件为 AI 辅助草稿，由规则和模板生成，不含生成式模型撰写的内容，须经人工复核后方可使用。"),
        ])

    def sec_method(self) -> Section:
        p = self.p
        blocks: list = [
            self.fsent("本项目（{pid}）针对 {analyte} 在 {matrix} 中的定量，采用 {method} 方法；"
                       "定量范围 {lloq}–{uloq} {unit}。",
                       {"analyte": "proj.analyte", "matrix": "proj.matrix", "method": "proj.method",
                        "lloq": "proj.lloq", "uloq": "proj.uloq"}, pid=p.id, unit=p.unit),
            TableBlock("项目信息", "project", ["项目", "内容"], [
                ["项目编号", p.id], ["项目名称", p.title], ["分析物", p.analyte], ["生物基质", p.matrix],
                ["分析方法", p.method_type], ["浓度单位", p.unit],
                ["内标", p.internal_standard or ("不适用（LBA）" if p.method_type == "LBA" else placeholder("内标名称"))],
                ["样品前处理方法", p.sample_prep or placeholder("样品前处理方法")],
                ["仪器与色谱柱" if p.method_type == "LC-MS/MS" else "读板仪", p.instrument_setup or placeholder(
                    "仪器型号与色谱柱" if p.method_type == "LC-MS/MS" else "读板仪型号")],
                ["纳入分析批", "、".join(r.id for r in self.runs)],
            ]),
        ]
        if p.method_type == "LC-MS/MS":
            blocks.append(self.fsent("内标：{v}", {"v": "proj.is"}))
        else:
            blocks.append(self.static("本方法为配体结合分析，不使用内标。"))
        blocks.append(self.fsent("样品前处理方法：{v}", {"v": "proj.prep"}))
        if p.method_type == "LC-MS/MS":
            blocks.append(self.fsent("仪器与色谱柱：{v}", {"v": "proj.instrument"}))
        else:
            blocks.append(self.fsent("读板仪：{v}", {"v": "proj.instrument"}))
            blocks.append(self.fsent("检测试剂盒及批号：{v}", {"v": "proj.kit"}))
        return Section("方法概述", blocks)

    def sec_calibration(self) -> Section:
        blocks: list = []
        for r in self.runs:
            info, k = self.run_info[r.id], r.id
            blocks.append(self.fsent(
                "分析批 {rid} 的校准曲线共 {n} 个浓度点，标示浓度范围 {lo}–{hi} {unit}；"
                "{ok} 个点的回算偏差在演示阈值内，{bad} 个超出，{pend} 个缺值或恰在阈值（规则 CAL-ACC、CAL-MIN）；判定：{v}。",
                {"n": f"{k}.cal.n", "lo": f"{k}.cal.min", "hi": f"{k}.cal.max", "ok": f"{k}.cal.pass",
                 "bad": f"{k}.cal.fail", "pend": f"{k}.cal.pending", "v": f"{k}.cal.verdict"},
                rid=k, unit=self.p.unit))
            pts = info["cal"].detail.get("points")
            rows = []
            for i, c in enumerate(info["cal_rows"]):
                if c.back_calc_conc is None:
                    rows.append([str(c.level_no), num(c.nominal), placeholder("回算浓度"), DASH, DASH,
                                 VERDICT_ZH["pending_review"]])
                else:
                    pt = pts[i] if pts else None
                    st = {"pass": "通过", "fail": "超限", "borderline": "恰在阈值", "unknown": "缺值"}[pt["status"]] if pt else DASH
                    rows.append([str(c.level_no), num(c.nominal), num(c.back_calc_conc),
                                 pct_signed(pt["bias_pct"]) if pt else DASH,
                                 f"±{num(pt['limit_pct'])}%" if pt else DASH, st])
            blocks.append(TableBlock(f"{k} 校准点明细", f"calibration_point（run_id={k}）",
                                     ["序号", f"标示浓度({self.p.unit})", f"回算浓度({self.p.unit})", "相对偏差", "阈值", "单点判定"], rows))
        return Section("校准曲线", blocks)

    def sec_qc(self) -> Section:
        blocks: list = []
        for r in self.runs:
            info, k = self.run_info[r.id], r.id
            slots = {"n": f"{k}.qc.n", "ok": f"{k}.qc.pass", "v": f"{k}.qc.verdict"}
            slots.update({lv: f"{k}.qc.bias.{lv}" for lv in QC_LEVELS})
            blocks.append(self.fsent(
                "分析批 {rid} 共 {n} 个 QC 样品，{ok} 个在演示阈值内；LLOQ、LQC、MQC、HQC 水平的平均相对偏差分别为 "
                "{LLOQ}、{LQC}、{MQC}、{HQC}（规则 QC-ACC、QC-RUN）；判定：{v}。", slots, rid=k))
            levels = info["qc"].detail.get("levels", {})
            acc = self.rule_sets[r.rule_set_id].v("QC-ACC")  # 阈值直接取规则集，不在此处重写规则
            limit = lambda lv: acc["lloq_acc_pct"] if lv == "LLOQ" else acc["acc_pct"]  # noqa: E731
            rows = []
            for lv in QC_LEVELS:
                qs = [q for q in info["qc_rows"] if q.level == lv]
                lvd = levels.get(lv)
                f = self.store.get(f"{k}.qc.bias.{lv}")
                rows.append([lv, num(qs[0].nominal) if qs else placeholder(f"{lv} 标示浓度"), str(len(qs)),
                             str(lvd["pass"]) if lvd else DASH, f.display or placeholder(f.label),
                             f"±{num(limit(lv))}%"])
            blocks.append(TableBlock(f"{k} QC 汇总", f"qc_sample（run_id={k}）",
                                     ["水平", f"标示浓度({self.p.unit})", "重复数", "合格数", "平均相对偏差", "阈值"], rows))
        return Section("质控样品（QC）", blocks)

    def sec_accept(self) -> Section:
        blocks: list = [self.static("批接受判定 = 校准曲线判定与 QC 判定的综合；ISR 单独评价，不并入批接受。"
                                    "输入缺失或恰在阈值时判为待复核，不自动放行。")]
        for r in self.runs:
            k = r.id
            blocks.append(self.fsent("分析批 {rid} 批接受判定：{acc}（校准：{cal}；QC：{qc}；状态：{st}）。",
                                     {"acc": f"{k}.acc", "cal": f"{k}.cal.verdict", "qc": f"{k}.qc.verdict",
                                      "st": f"{k}.status"}, rid=k))
        g = lambda k, key: self.store.get(f"{k}.{key}").display  # noqa: E731
        blocks.append(TableBlock("分析批判定总表", "analytical_run；规则引擎实时判定",
                                 ["分析批", "日期", "状态", "校准", "QC", "批接受", "ISR"],
                                 [[r.id, r.run_date.isoformat(), g(r.id, "status"), g(r.id, "cal.verdict"),
                                   g(r.id, "qc.verdict"), g(r.id, "acc"), g(r.id, "isr.verdict")] for r in self.runs]))
        rs_used = {r.rule_set_id for r in self.runs}
        rule_rows = []
        for rsid in sorted(rs_used):
            for rule_id, body in self.rule_sets[rsid].rules.items():
                rule_rows.append([rsid, rule_id, json.dumps(body["value"], ensure_ascii=False), body["status"],
                                  body["source_note"]])
        blocks.append(self.static("判定所用规则集及状态见下表；所有阈值均为演示配置（状态 demo_unconfirmed），"
                                  "待业务确认，不代表任何监管要求。"))
        blocks.append(TableBlock("判定规则及来源说明", "config/rules/demo_rules.yaml",
                                 ["规则集", "规则", "取值", "状态", "来源说明"], rule_rows))
        return Section("分析批接受判定", blocks)

    def sec_samples(self) -> Section:
        blocks: list = []
        for r in self.runs:
            k = r.id
            blocks.append(self.fsent(
                "分析批 {rid} 共分析 {n} 条待测样品，其中有效结果 {ok} 条，低于定量下限（BLQ）{blq} 条，"
                "高于定量上限 {above} 条，标记再分析 {rean} 条，缺失 {miss} 条。",
                {"n": f"{k}.smp.n", "ok": f"{k}.smp.ok", "blq": f"{k}.smp.blq",
                 "above": f"{k}.smp.above", "rean": f"{k}.smp.rean", "miss": f"{k}.smp.missing"},
                rid=k))
        return Section("研究样品分析概况", blocks)

    def sec_validation_stats(self) -> Section:
        blocks: list = [self.static("下表汇总所有纳入分析批的 QC 结果（描述性统计）。当前规则集未配置精密度（CV）的接受标准，"
                                    "因此仅列出统计量，未作判定。")]
        rows = []
        for lv in QC_LEVELS:
            qs = [q for ri in self.run_info.values() for q in ri["qc_rows"] if q.level == lv and q.measured is not None]
            src = [f"{q.run_id}#{q.level}#{q.replicate}" for q in qs]
            nominal = qs[0].nominal if qs else None
            mean_bias = (statistics.fmean(q.measured for q in qs) / nominal - 1) * 100 if qs else None
            cv = (statistics.stdev(q.measured for q in qs) / statistics.fmean(q.measured for q in qs) * 100
                  if len(qs) >= 2 else None)
            self.add(f"val.qc.{lv}.n", f"{lv} 水平 QC 结果数", len(qs), "qc_sample", src)
            self.add(f"val.qc.{lv}.bias", f"{lv} 水平 QC 跨批平均相对偏差", mean_bias, "qc_sample", src, "QC-ACC",
                     display=pct_signed(mean_bias) if mean_bias is not None else None)
            self.add(f"val.qc.{lv}.cv", f"{lv} 水平 QC 跨批变异系数", cv, "qc_sample", src,
                     display=pct(cv) if cv is not None else None)
            blocks.append(self.fsent(
                "{lv} 水平共 {n} 个 QC 结果，跨批平均相对偏差 {bias}，变异系数 {cv}。",
                {"n": f"val.qc.{lv}.n", "bias": f"val.qc.{lv}.bias", "cv": f"val.qc.{lv}.cv"}, lv=lv))
            rows.append([lv, str(len(qs)), self.store.get(f"val.qc.{lv}.bias").display or placeholder(f"{lv} 跨批平均相对偏差"),
                         self.store.get(f"val.qc.{lv}.cv").display or placeholder(f"{lv} 跨批变异系数")])
        blocks.append(TableBlock("跨批 QC 汇总", "qc_sample（所有纳入分析批）", ["水平", "结果数", "平均相对偏差", "变异系数"], rows))
        return Section("准确度与精密度（QC 汇总）", blocks)

    def sec_isr(self) -> Section:
        blocks: list = []
        for r in self.runs:
            k = r.id
            if self.store.get(f"{k}.isr.n").value == 0:
                blocks.append(Sentence(f"分析批 {k} 的 ISR 记录：{placeholder('ISR 记录')}", "placeholder", [], ["ISR 记录"]))
                continue
            blocks.append(self.fsent(
                "分析批 {rid} 的 ISR 共 {n} 例，合格 {ok} 例、不合格 {bad} 例、待复核（缺复测值或恰在阈值）{pend} 例，"
                "合格比例 {rate}（规则 ISR-DIFF、ISR-RATE、ISR-N）；判定：{v}。",
                {"n": f"{k}.isr.n", "ok": f"{k}.isr.pass", "bad": f"{k}.isr.fail", "pend": f"{k}.isr.pending",
                 "rate": f"{k}.isr.rate", "v": f"{k}.isr.verdict"}, rid=k))
        return Section("已测样品再分析（ISR）", blocks)

    def sec_deviation(self, devs) -> Section:
        blocks: list = [self.fsent("项目 {pid} 在本报告范围内登记偏差 {total} 项，其中未关闭 {open} 项，"
                                   "未关闭的重要及以上偏差 {major} 项。",
                                   {"total": "dev.total", "open": "dev.open", "major": "dev.open_major"}, pid=self.p.id)]
        rows = [[d.id, d.run_id or DASH, d.category, SEVERITY_ZH[d.severity], "未关闭" if d.status == "open" else "已关闭",
                 d.opened_at.isoformat(), d.closed_at.isoformat() if d.closed_at else DASH, d.description] for d in devs]
        blocks.append(TableBlock("偏差清单", "deviation", ["编号", "分析批", "类别", "严重度", "状态", "登记日期", "关闭日期", "描述"], rows))
        return Section("偏差", blocks)

    def sec_conclusion(self) -> Section:
        blocks: list = [
            self.fsent("本报告纳入 {n} 个分析批（项目 {pid}）：批接受判定通过 {ok} 个、未通过 {bad} 个、待复核 {pend} 个；"
                       "另有 {ip} 个分析批尚未完成，未计入上述统计。",
                       {"n": "proj.n_runs", "ok": "proj.n_pass", "bad": "proj.n_fail", "pend": "proj.n_pending",
                        "ip": "proj.n_inprog"}, pid=self.p.id),
            self.fsent("未关闭偏差 {open} 项，其中重要及以上 {major} 项。", {"open": "dev.open", "major": "dev.open_major"}),
        ]
        if self.store.get("dev.open").value:
            blocks.append(self.fsent("存在未关闭偏差，须在评估关闭后再确定本报告的结论。", {"open": "dev.open"}))
        if self.store.get("proj.n_fail").value or self.store.get("proj.n_pending").value:
            blocks.append(self.fsent("存在未通过或待复核的分析批，不能据此得出批接受或方法适用的结论，须人工复核。",
                                     {"bad": "proj.n_fail", "pend": "proj.n_pending"}))
        blocks.append(self.static("以上为系统依据演示规则集（待业务确认）汇总的状态描述，不构成合规性结论；"
                                  "是否接受分析批、是否出具正式报告，须由人工复核后决定。"))
        return Section("结论", blocks)

    def sec_checklist(self) -> Section:
        blocks: list = []
        for r in self.runs:
            k = r.id
            if r.status == "in_progress":
                blocks.append(self.fsent("分析批 {rid} 尚未完成（状态：{st}），未计入统计，完成后须重新生成草稿。",
                                         {"st": f"{k}.status"}, rid=k))
                continue
            if self.run_info[k]["accepted"] is not Verdict.PASS:
                blocks.append(self.fsent("分析批 {rid} 批接受判定为{acc}（校准：{cal}；QC：{qc}），须人工复核确认。",
                                         {"acc": f"{k}.acc", "cal": f"{k}.cal.verdict", "qc": f"{k}.qc.verdict"}, rid=k))
            if self.run_info[k]["isr"].verdict is not Verdict.PASS and self.store.get(f"{k}.isr.n").value:
                blocks.append(self.fsent("分析批 {rid} 的 ISR 判定为{v}（合格 {ok}/{n} 例），须人工确认。",
                                         {"v": f"{k}.isr.verdict", "ok": f"{k}.isr.pass", "n": f"{k}.isr.n"}, rid=k))
        if self.store.get("dev.open").value:
            blocks.append(self.fsent("未关闭偏差 {open} 项（重要及以上 {major} 项），须评估对结论的影响。",
                                     {"open": "dev.open", "major": "dev.open_major"}))
        uniq = list(dict.fromkeys(self.missing))
        self.add("report.missing_count", "待补充字段数", len(uniq), "report_builder", [])
        blocks.append(self.fsent("本草稿中待补充的字段共 {n} 项，见各章节标注的“[待补充: …]”，定稿前须补齐。",
                                 {"n": "report.missing_count"}))
        blocks.append(self.static("所有规则阈值均为演示配置（状态 demo_unconfirmed），报告发布前须由业务确认。"))
        blocks.append(self.static("草稿须经编写人、复核人、专题负责人逐级复核并签署，质量部门核对报告与原始数据一致后方可作为正式报告。"))
        return Section("复核清单", blocks)

    def sec_appendix(self) -> Section:
        rows = []
        for f in self.store.all():
            src = ",".join(f.source_rows[:3]) + (f" …共{len(f.source_rows)}行" if len(f.source_rows) > 3 else "")
            rows.append([f.tag, f.key, f.label, (f.display or placeholder(f.label)) + (f" {f.unit}" if f.unit and f.display else ""),
                         f.source_table, src or DASH, f.rule_id or DASH])
        return Section("数据溯源附录", [
            self.static("下表列出本报告全部事实及其来源表、来源行与关联规则；正文中的“[F…]”标注对应本表编号。"),
            TableBlock("事实溯源表", "report_builder", ["编号", "事实键", "含义", "取值", "来源表", "来源行", "规则"], rows)])

    def sec_signature(self) -> Section:
        rows = [[role, "[待签署]", "[待签署]", "[待签署]"] for role in ("编写人", "复核人", "专题负责人", "质量保证")]
        return Section("签名", [self.static("本草稿未经签署，不具备正式报告效力。"),
                              TableBlock("签署页", "—", ["角色", "姓名", "签名", "日期"], rows)])


def build_report(session: Session, project_id: str, run_ids: list[str], kind: str,
                 rule_sets: dict[str, RuleSet]) -> ReportDoc:
    if kind not in KINDS:
        raise ReportError("invalid_kind", f"kind 必须是 {sorted(KINDS)}")
    project = session.get(Project, project_id)
    if project is None:
        raise ReportError("project_not_found", f"项目不存在：{project_id}", 404)
    runs = session.scalars(select(AnalyticalRun).where(AnalyticalRun.project_id == project_id)
                           .order_by(AnalyticalRun.id)).all()
    unknown = set(run_ids) - {r.id for r in runs}
    if unknown:
        raise ReportError("run_not_in_project", f"分析批不属于该项目：{sorted(unknown)}")
    selected = [r for r in runs if not run_ids or r.id in set(run_ids)]
    if not selected:
        raise ReportError("no_runs", "该项目没有可纳入的分析批")
    return _Builder(session, project, selected, rule_sets, kind).build()
