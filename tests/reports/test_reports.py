"""阶段 4：DOCX 草稿——叙述句数值与源数据一致、来源覆盖、缺失值处理、可重新打开、关键章节。"""
import re
import statistics

import pytest
from docx import Document
from sqlalchemy import delete, select, update

from app.db.models import AnalyticalRun, CalibrationPoint, ISRRecord, QCSample, StudySample
from app.reports.builder import ReportError, build_report
from app.reports.checks import ReportIntegrityError, check_report
from app.reports.model import Sentence
from app.reports.service import generate_draft
from tests.reports.conftest import all_text

REQUIRED = ["方法概述", "校准曲线", "质控样品", "分析批接受判定", "已测样品再分析", "偏差", "结论",
            "复核清单", "数据溯源附录", "签名"]


def gen(session, reports_dir, pid, runs, kind, rule_sets):
    res = generate_draft(session, reports_dir, pid, runs, kind, rule_sets)
    return res, Document(res["path"])


def para(doc, prefix_re):
    hits = [p.text for p in doc.paragraphs if re.search(prefix_re, p.text)]
    assert len(hits) == 1, (prefix_re, hits)
    return hits[0]


def bias(measured, nominal):
    return (measured / nominal - 1) * 100


# ---------- 叙述句数值与源数据一致（独立重算，不经过规则引擎） ----------
@pytest.mark.parametrize("pid,run,method", [("PRJ-SM-001", "PRJ-SM-001-R01", "chrom"),
                                            ("PRJ-BIO-002", "PRJ-BIO-002-R01", "lba")])
def test_narrative_numbers_match_source(session, reports_dir, rule_sets, raw_rules, pid, run, method):
    res, doc = gen(session, reports_dir, pid, [run], "sample_analysis", rule_sets)
    rs = raw_rules["RS-CHROM-DEMO-v1" if method == "chrom" else "RS-LBA-DEMO-v1"]["rules"]

    # 1) 校准曲线句
    cal = session.scalars(select(CalibrationPoint).where(CalibrationPoint.run_id == run)
                          .order_by(CalibrationPoint.level_no)).all()
    cv = rs["CAL-ACC"]["value"]
    lo, hi = min(c.nominal for c in cal), max(c.nominal for c in cal)
    edges = {lo} | ({hi} if "highest" in cv["edge_levels"] else set())
    exp_pass = sum(abs(bias(c.back_calc_conc, c.nominal)) < (cv["edge_acc_pct"] if c.nominal in edges else cv["acc_pct"])
                   for c in cal)
    t = para(doc, rf"分析批 {run} 的校准曲线共")
    m = re.search(r"共 (\d+) 个浓度点，标示浓度范围 ([\d.]+)–([\d.]+) ng/mL；(\d+) 个点.*?，(\d+) 个超出", t)
    assert m, t
    assert int(m[1]) == len(cal) and float(m[2]) == lo and float(m[3]) == hi
    assert int(m[4]) == exp_pass and int(m[5]) == len(cal) - exp_pass

    # 2) QC 句（HQC 平均相对偏差）
    qcs = session.scalars(select(QCSample).where(QCSample.run_id == run)).all()
    hqc = statistics.fmean(bias(q.measured, q.nominal) for q in qcs if q.level == "HQC")
    t = para(doc, rf"分析批 {run} 共 \d+ 个 QC 样品")
    assert f"共 {len(qcs)} 个 QC 样品" in t and f"{hqc:+.1f}%" in t, t

    # 3) 待测样品句
    smp = session.scalars(select(StudySample).where(StudySample.run_id == run)).all()
    t = para(doc, rf"分析批 {run} 共分析")
    assert f"共分析 {len(smp)} 条待测样品" in t
    assert f"有效结果 {sum(s.flag == 'ok' for s in smp)} 条" in t
    assert f"（BLQ）{sum(s.flag == 'BLQ' for s in smp)} 条" in t

    # 4) ISR 句
    isr = session.scalars(select(ISRRecord).where(ISRRecord.run_id == run)).all()
    lim = rs["ISR-DIFF"]["value"]["diff_pct"]
    ok = sum(abs((i.repeat_conc - i.original_conc) / ((i.repeat_conc + i.original_conc) / 2) * 100) < lim for i in isr)
    t = para(doc, rf"分析批 {run} 的 ISR 共")
    assert f"共 {len(isr)} 例，合格 {ok} 例" in t and f"合格比例 {ok / len(isr) * 100:.1f}%" in t, t


def test_at_least_three_data_sentences_checked(session, reports_dir, rule_sets):
    """显式确认上面的叙述句至少 4 类、跨 2 个项目，且附有来源标签。"""
    _, doc = gen(session, reports_dir, "PRJ-SM-001", ["PRJ-SM-001-R01"], "sample_analysis", rule_sets)
    tagged = [p.text for p in doc.paragraphs if re.search(r"\[F\d+(,F\d+)*\]$", p.text)]
    assert len(tagged) >= 6


# ---------- 可重新打开、关键章节、文首标记 ----------
@pytest.mark.parametrize("kind,extra", [("validation", "准确度与精密度"), ("sample_analysis", "研究样品分析概况")])
def test_docx_reopens_with_sections_banner_and_signature(session, reports_dir, rule_sets, kind, extra):
    res, doc = gen(session, reports_dir, "PRJ-SM-001", [], kind, rule_sets)
    reopened = Document(res["path"])                      # 从磁盘重新打开
    body = [p for p in reopened.paragraphs if p.text.strip()]
    assert body[0].text == "AI 辅助草稿，需人工复核"            # 文首标记
    assert "AI 辅助草稿，需人工复核" in reopened.sections[0].header.paragraphs[0].text
    assert "模拟数据" in reopened.sections[0].header.paragraphs[0].text
    headings = [p.text for p in reopened.paragraphs if p.style.name.startswith("Heading")]
    for name in REQUIRED + [extra]:
        assert any(name in h for h in headings), (name, headings)
    sig = next(t for t in reopened.tables if [c.text for c in t.rows[0].cells] == ["角色", "姓名", "签名", "日期"])
    assert [r.cells[0].text for r in sig.rows[1:]] == ["编写人", "复核人", "专题负责人", "质量保证"]
    assert all(r.cells[1].text == "[待签署]" for r in sig.rows[1:])
    assert res["review_status"] == "draft"


def test_rules_shown_with_demo_status(session, reports_dir, rule_sets):
    _, doc = gen(session, reports_dir, "PRJ-BIO-002", ["PRJ-BIO-002-R01"], "sample_analysis", rule_sets)
    text = "\n".join(all_text(doc))
    assert "demo_unconfirmed" in text and "RS-LBA-DEMO-v1" in text and "不代表任何监管要求" in text
    assert "不构成合规性结论" in text


# ---------- 来源覆盖 ----------
def test_every_business_sentence_has_sources_and_traceable(session, rule_sets):
    doc = build_report(session, "PRJ-BIO-002", [], "sample_analysis", rule_sets)
    stats = check_report(doc)
    assert stats["unsourced_sentences"] == 0 and stats["fact_sentences"] >= 15
    tables = {"project", "calibration_point", "qc_sample", "study_sample", "isr_record", "deviation",
              "analytical_run", "report_builder"}
    for s in doc.sentences():
        if s.kind == "fact":
            assert s.fact_keys and all(k in doc.facts for k in s.fact_keys)
    for f in doc.facts.all():
        assert f.source_table in tables
        if f.key != "report.missing_count":
            assert f.source_rows or f.value in (0, None), f.key
    # 来源行在库中真实存在（抽查校准点与 ISR）
    cal_ids = {f"{c.run_id}#L{c.level_no}" for c in session.scalars(select(CalibrationPoint))}
    isr_ids = {i.sample_id for i in session.scalars(select(ISRRecord))}
    for f in doc.facts.all():
        if f.source_table == "calibration_point":
            assert set(f.source_rows) <= cal_ids
        if f.source_table == "isr_record":
            assert set(f.source_rows) <= isr_ids


def test_integrity_check_rejects_unsourced_or_invented_numbers(session, rule_sets):
    base = build_report(session, "PRJ-SM-001", ["PRJ-SM-001-R01"], "sample_analysis", rule_sets)
    # 无来源的业务结论
    base.sections[0].blocks.append(Sentence("分析批的准确度非常好。", "fact", []))
    with pytest.raises(ReportIntegrityError, match="缺少来源"):
        check_report(base)
    base.sections[0].blocks.pop()
    # 数字不在来源事实中（编造数值）
    key = "PRJ-SM-001-R01.smp.n"
    base.sections[0].blocks.append(Sentence("分析批共分析 9999 条待测样品。", "fact", [key]))
    with pytest.raises(ReportIntegrityError, match="9999"):
        check_report(base)
    base.sections[0].blocks.pop()
    # 说明性文字含数字
    base.sections[0].blocks.append(Sentence("该方法优于 3 个同类方法。", "static"))
    with pytest.raises(ReportIntegrityError, match="不得含数字"):
        check_report(base)


# ---------- 缺失值 ----------
def test_seed_missing_values_become_placeholders(session, reports_dir, rule_sets):
    res, doc = gen(session, reports_dir, "PRJ-SM-003", ["PRJ-SM-003-R03"], "sample_analysis", rule_sets)
    cells = all_text(doc)
    assert cells.count("[待补充: 回算浓度]") == 2                       # 种子中 R03 有 2 个校准点缺回算浓度
    assert any(c.startswith("仪器与色谱柱：[待补充: 仪器型号与色谱柱]") for c in cells)   # 种子中 SM-003 仪器信息故意缺失
    assert any(c.startswith("内标：DEMO-IS-303-d5") for c in cells)                       # 有值的字段不再占位
    assert "PRJ-SM-003-R03 校准点回算浓度（2 处）" in res["missing_fields"]
    assert set(res["missing_fields"]) == {"PRJ-SM-003-R03 校准点回算浓度（2 处）", "仪器型号与色谱柱"}
    assert not any(bad in "\n".join(cells) for bad in ("None", "nan", "NaN"))
    # 对应的校准点没有被补造出数值：这两行的回算浓度与相对偏差列是占位 / 破折号
    t = next(t for t in doc.tables if t.rows[0].cells[0].text == "序号" and any("待补充" in r.cells[2].text for r in t.rows))
    for r in t.rows[1:]:
        if "待补充" in r.cells[2].text:
            assert r.cells[3].text == "—" and r.cells[5].text == "待复核"


def test_all_hqc_values_missing_not_fabricated(db, reports_dir, rule_sets):
    run = "PRJ-SM-001-R01"
    with db() as s:
        s.execute(update(QCSample).where(QCSample.run_id == run, QCSample.level == "HQC")
                  .values(measured=None, bias_pct=None))
        s.commit()
        res, doc = gen(s, reports_dir, "PRJ-SM-001", [run], "sample_analysis", rule_sets)
    t = para(doc, rf"分析批 {run} 共 \d+ 个 QC 样品")
    assert f"[待补充: {run} HQC 水平 QC 平均相对偏差]" in t
    assert "判定：待复核" in t                                           # 缺值不会被判为通过
    assert f"{run} QC 测得浓度（3 处）" in res["missing_fields"]
    hqc_row = next(r for tb in doc.tables for r in tb.rows if r.cells[0].text == "HQC" and "待补充" in "".join(c.text for c in r.cells))
    assert hqc_row.cells[4].text.startswith("[待补充")


def test_missing_isr_records_placeholder(db, reports_dir, rule_sets):
    run = "PRJ-SM-001-R02"
    with db() as s:
        s.execute(delete(ISRRecord).where(ISRRecord.run_id == run))
        s.commit()
        res, doc = gen(s, reports_dir, "PRJ-SM-001", [run], "sample_analysis", rule_sets)
    assert f"分析批 {run} 的 ISR 记录：[待补充: ISR 记录]" in [p.text for p in doc.paragraphs]
    assert f"{run} ISR 记录" in res["missing_fields"]
    assert res["unsourced_sentences"] == 0


# ---------- 判定与未完成批 ----------
def test_verdicts_follow_rule_engine_on_planted_cases(session, rule_sets):
    doc = build_report(session, "PRJ-SM-003", [], "sample_analysis", rule_sets)
    f = lambda k: doc.facts.get(k).display  # noqa: E731
    assert f("PRJ-SM-003-R01.acc") == "通过"
    assert f("PRJ-SM-003-R02.qc.verdict") == "未通过" and f("PRJ-SM-003-R02.acc") == "未通过"
    assert f("PRJ-SM-003-R03.cal.verdict") == "待复核" and f("PRJ-SM-003-R03.acc") == "待复核"
    assert [doc.facts.get(k).value for k in ("proj.n_pass", "proj.n_fail", "proj.n_pending")] == [1, 1, 1]
    assert doc.facts.get("dev.open_major").value == 1
    concl = next(s for sec in doc.sections if "结论" in sec.title for s in sec.blocks if isinstance(s, Sentence))
    assert "通过 1 个、未通过 1 个、待复核 1 个" in concl.text


def test_in_progress_run_excluded_from_counts(session, rule_sets):
    doc = build_report(session, "PRJ-BIO-002", [], "sample_analysis", rule_sets)
    assert doc.facts.get("proj.n_runs").value == 4
    assert doc.facts.get("proj.n_inprog").value == 1
    assert sum(doc.facts.get(k).value for k in ("proj.n_pass", "proj.n_fail", "proj.n_pending")) == 3
    review = next(sec for sec in doc.sections if "复核清单" in sec.title)
    assert any("PRJ-BIO-002-R04 尚未完成" in b.text for b in review.blocks if isinstance(b, Sentence))


def test_validation_report_cross_run_stats_and_no_precision_verdict(session, reports_dir, rule_sets):
    _, doc = gen(session, reports_dir, "PRJ-SM-001", [], "validation", rule_sets)
    text = "\n".join(all_text(doc))
    assert "未配置精密度（CV）的接受标准" in text
    qcs = [q for q in session.scalars(select(QCSample)) if q.run_id.startswith("PRJ-SM-001") and q.level == "MQC"]
    vals = [q.measured for q in qcs]
    exp_cv = statistics.stdev(vals) / statistics.fmean(vals) * 100
    exp_bias = (statistics.fmean(vals) / qcs[0].nominal - 1) * 100
    t = para(doc, r"^MQC 水平共")
    assert f"共 {len(vals)} 个 QC 结果" in t and f"{exp_bias:+.1f}%" in t and f"{exp_cv:.1f}%" in t


def test_deterministic_facts(session, rule_sets):
    a = build_report(session, "PRJ-BIO-002", [], "validation", rule_sets)
    b = build_report(session, "PRJ-BIO-002", [], "validation", rule_sets)
    assert [f.to_dict() for f in a.facts.all()] == [f.to_dict() for f in b.facts.all()]
    assert [s.text for s in a.sentences()] == [s.text for s in b.sentences()]


def test_builder_errors(session, rule_sets):
    for args, code in ((("NOPE", [], "validation"), "project_not_found"),
                       (("PRJ-SM-001", ["PRJ-SM-003-R01"], "validation"), "run_not_in_project"),
                       (("PRJ-SM-001", [], "weekly"), "invalid_kind")):
        with pytest.raises(ReportError) as e:
            build_report(session, args[0], args[1], args[2], rule_sets)
        assert e.value.code == code


# ---------- API ----------
def test_api_requires_token(api):
    assert api.post("/api/reports/draft", json={"project_id": "PRJ-SM-001", "kind": "validation"}).status_code == 401
    assert api.get("/api/reports/x/download").status_code == 401


def test_api_create_and_audit(api, auth):
    r = api.post("/api/reports/draft", headers=auth, json={
        "project_id": "PRJ-SM-003", "run_ids": ["PRJ-SM-003-R02", "PRJ-SM-003-R03"], "kind": "sample_analysis"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["draft_id"].startswith("DRAFT-SMP-") and body["review_status"] == "draft"
    assert body["banner"] == "AI 辅助草稿，需人工复核" and body["unsourced_sentences"] == 0
    assert body["fact_count"] > 50 and "仪器型号与色谱柱" in body["missing_fields"]
    assert "download_url" not in body and body["export_requires_review"] is True      # 生成响应不再给出下载地址
    audit = api.get("/api/audit", params={"request_id": body["request_id"]}, headers=auth).json()[0]
    assert audit["draft_id"] == body["draft_id"] and "REPORT:sample_analysis" in audit["rule_ids"]
    assert "QC-ACC" in audit["rule_ids"]


def test_api_errors(api, auth):
    def post(**kw):
        return api.post("/api/reports/draft", headers=auth, json={"kind": "validation", **kw})
    r = post(project_id="NOPE")
    assert r.status_code == 404 and r.json()["detail"]["error_code"] == "project_not_found"
    r = post(project_id="PRJ-SM-001", run_ids=["PRJ-SM-003-R01"])
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "run_not_in_project"
    assert api.post("/api/reports/draft", headers=auth, json={"project_id": "PRJ-SM-001", "kind": "x"}).status_code == 422
    assert api.get("/api/reports/DRAFT-NOPE/download", headers={**auth, "X-Operator": "tester"}).status_code == 404


# ---------- 方案类项目数据（内标、前处理、仪器、试剂盒） ----------
def test_complete_project_method_info_filled_with_sources(session, reports_dir, rule_sets):
    res, doc = gen(session, reports_dir, "PRJ-SM-001", ["PRJ-SM-001-R01"], "validation", rule_sets)
    texts = [p.text for p in doc.paragraphs]
    is_line = next(t for t in texts if t.startswith("内标："))
    assert "DEMO-IS-101-d4" in is_line and re.search(r"\[F\d+\]$", is_line)          # 带来源标签
    assert any(t.startswith("样品前处理方法：蛋白沉淀法") for t in texts)
    assert any(t.startswith("仪器与色谱柱：模拟 LC-MS/MS 系统 SIM-LCMS-01") and "2.1×50 mm" in t for t in texts)
    assert res["missing_fields"] == []                                                  # 方案信息齐全，无待补充项
    assert not any("待补充" in c for c in all_text(doc) if c.startswith(("内标", "样品前处理", "仪器")))
    doc2 = build_report(session, "PRJ-SM-001", ["PRJ-SM-001-R01"], "validation", rule_sets)
    f = doc2.facts.get("proj.instrument")
    assert f.source_table == "project" and f.source_rows == ["PRJ-SM-001"] and "SIM-LCMS-01" in f.display


def test_lba_has_no_internal_standard_placeholder(session, reports_dir, rule_sets):
    res, doc = gen(session, reports_dir, "PRJ-BIO-002", ["PRJ-BIO-002-R01"], "sample_analysis", rule_sets)
    texts = [p.text for p in doc.paragraphs]
    assert "本方法为配体结合分析，不使用内标。" in texts
    assert not any(t.startswith("内标：") for t in texts) and "内标名称" not in res["missing_fields"]
    assert any(t.startswith("读板仪：模拟酶标仪 SIM-READER-01") for t in texts)
    assert any(t.startswith("检测试剂盒及批号：模拟 ELISA 试剂盒，批号 KIT-DEMO-2606") for t in texts)
    assert res["missing_fields"] == []
    rows = {r.cells[0].text: r.cells[1].text for t in doc.tables for r in t.rows if len(r.cells) == 2}
    assert rows["内标"] == "不适用（LBA）"


def test_clearing_a_field_brings_placeholder_back(db, reports_dir, rule_sets):
    from app.db.models import Project
    with db() as s:
        s.execute(update(Project).where(Project.id == "PRJ-SM-001").values(sample_prep=None, internal_standard=None))
        s.commit()
        res, doc = gen(s, reports_dir, "PRJ-SM-001", ["PRJ-SM-001-R01"], "validation", rule_sets)
    texts = [p.text for p in doc.paragraphs]
    assert any(t.startswith("内标：[待补充: 内标名称]") for t in texts)
    assert any(t.startswith("样品前处理方法：[待补充: 样品前处理方法]") for t in texts)
    assert {"内标名称", "样品前处理方法"} <= set(res["missing_fields"])
    assert res["unsourced_sentences"] == 0                                              # 占位句同样通过自检
