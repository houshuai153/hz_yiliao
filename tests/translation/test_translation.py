"""阶段 3：术语表、术语命中、句型翻译、缩写大小写、冲突警告、未知句型。"""
import re

import pytest

from app.settings import Settings
from app.translation.service import TranslationError, detect_lang


def codes(par: dict) -> set[str]:
    return {w["code"] for w in par["warnings"]}


# ---------- 术语表本身 ----------
def test_glossary_sanity(glossary):
    ids = [t.id for t in glossary.terms]
    assert len(ids) == len(set(ids))
    assert len({t.zh for t in glossary.terms}) == len(glossary.terms)
    for t in glossary.terms:
        assert t.zh and t.en
        if t.abbr:
            assert t.abbr == t.abbr.upper(), t.id
        for f in t.forbidden_en:
            assert f.lower() != t.en.lower(), t.id  # 禁用译法不能等于推荐译法
    assert {t.id for t in glossary.by_category("matrix")} >= {"human_plasma", "human_serum", "rat_plasma"}
    assert "演示" in glossary.disclaimer


# ---------- 术语命中 ----------
def test_term_hits_longest_match_and_abbr(glossary):
    hits = glossary.find_hits("LLOQ质控样品的相对偏差和偏差均需记录")
    got = [(h.term_id, h.kind) for h in hits]
    assert ("lloq", "abbr") in got
    assert ("qc_sample", "zh") in got           # “质控样品”优先于“质控”
    assert ("relative_error", "zh") in got      # “相对偏差”优先于“偏差”
    assert ("deviation", "zh") in got           # 单独的“偏差”仍命中
    assert not any(h.term_id == "qc" for h in hits)
    assert [h.start for h in hits] == sorted(h.start for h in hits)


def test_term_hits_english_phrase_plural_and_short_abbr(glossary):
    hits = glossary.find_hits("The internal standard (IS) is added to quality control (QC) samples.")
    ids = [h.term_id for h in hits]
    assert "qc_sample" in ids and "qc" not in ids       # 复数 samples 命中，且长短语优先于 QC 缩写
    by_kind = [(h.term_id, h.kind, h.matched_text) for h in hits if h.term_id == "internal_standard"]
    assert by_kind == [("internal_standard", "en", "internal standard"), ("internal_standard", "abbr", "IS")]
    # 短缩写 IS 只精确匹配；普通词 "is" 不得被当作术语
    assert not any(h.matched_text == "is" for h in hits)


def test_lowercase_abbr_detected_with_case_flag(glossary):
    hits = glossary.find_hits("样品的lloq为1 ng/mL")
    h = next(h for h in hits if h.term_id == "lloq")
    assert h.kind == "abbr" and h.matched_text == "lloq" and h.case_ok is False


# ---------- 句型翻译 ----------
CASES_ZH2EN = {
    "本方法的定量下限（LLOQ）为0.5 ng/mL。": "The lower limit of quantification (LLOQ) of this method is 0.5 ng/mL.",
    "本方法的定量下限为1 pg/mL。": "The lower limit of quantification (LLOQ) of this method is 1 pg/mL.",
    "校准曲线的浓度范围为1至500 ng/mL。": "The calibration curve ranged from 1 to 500 ng/mL.",
    "校准曲线包含8个非零浓度水平。": "The calibration curve contained 8 non-zero concentration levels.",
    "HQC质控样品的准确度在±15%以内。": "The accuracy of HQC quality control (QC) samples was within ±15%.",
    "分析批PRJ-SM-003-R02未通过接受标准。": "Analytical run PRJ-SM-003-R02 did not meet the acceptance criteria.",
    "分析批PRJ-BIO-002-R01符合接受标准。": "Analytical run PRJ-BIO-002-R01 met the acceptance criteria.",
    "已测样品再分析（ISR）的合格比例为83.3%。": "The pass rate of incurred sample reanalysis (ISR) was 83.3%.",
    "共分析42份研究样品。": "A total of 42 study samples were analyzed.",
    "本批未关闭的偏差为2项。": "There were 2 open deviations in this run.",
    "本方法的分析基质为大鼠血浆。": "The matrix of this method is rat plasma.",
    "内标为DEMO-IS-d4。": "The internal standard (IS) is DEMO-IS-d4.",
    "本报告为AI辅助草稿，需人工复核。": "This report is an AI-assisted draft and requires manual review.",
}


@pytest.mark.parametrize("zh,en", CASES_ZH2EN.items())
def test_zh2en_supported_patterns_exact(svc, zh, en):
    p = svc.translate(zh, "zh2en")["paragraphs"][0]
    assert p["status"] == "translated" and p["target"] == en
    assert not [w for w in p["warnings"] if w["severity"] in ("warning", "error")], p["warnings"]
    assert all(s["pattern_id"] for s in p["sentences"])


@pytest.mark.parametrize("zh,en", CASES_ZH2EN.items())
def test_en2zh_roundtrip(svc, zh, en):
    p = svc.translate(en, "en2zh")["paragraphs"][0]
    assert p["status"] == "translated"
    # 回译得到该句型的规范中文（第一个中文模板），数值/缩写/编号不变
    back = svc.translate(p["target"], "zh2en")["paragraphs"][0]
    assert back["target"] == en


def test_numbers_units_ids_abbr_preserved_verbatim(svc):
    src = "HQC质控样品的准确度在±15%以内。"
    p = svc.translate(src, "zh2en")["paragraphs"][0]
    assert "HQC" in p["target"] and "±15%" in p["target"]
    p = svc.translate("共分析413份研究样品。", "zh2en")["paragraphs"][0]
    assert "413" in p["target"]


def test_every_registered_pattern_is_self_consistent(provider):
    """每个登记句型：用样例槽位填充 → 中译英 → 英译中 == 中文首选模板填充。"""
    sample = {"NUM": "0.5", "INT": "8", "PCT": "±15%", "UNIT": "ng/mL", "ID": "PRJ-SM-001-R01",
              "LV": "HQC", "NAME": "IS-d4"}
    slot = re.compile(r"\{(\w+):([\w.]+)\}")

    def fill(t, lang):
        def sub(m):
            k = m.group(2)
            if k.startswith("TERM."):
                return "人血浆" if lang == "zh" else "human plasma"
            return sample[k]
        return slot.sub(sub, t)

    pt = provider.patterns
    assert len(pt.patterns) >= 10
    for p in pt.patterns:
        for zh_t in p["zh"]:
            zh = fill(zh_t, "zh")
            r = pt.translate(zh, "zh2en")
            assert r and r.pattern_id == p["id"], (p["id"], zh)
            assert r.target == fill(p["en"][0], "en")
            back = pt.translate(r.target, "en2zh")
            assert back and back.target == fill(p["zh"][0], "zh"), p["id"]


# ---------- 大小写 / 缩写保留 ----------
def test_lowercase_abbr_in_slot_is_not_silently_fixed(svc):
    """质控水平缩写写成小写时不匹配句型（不替用户“纠正”后冒充可靠翻译），并给出缩写大小写警告。"""
    p = svc.translate("lloq质控样品的准确度在±20%以内。", "zh2en")["paragraphs"][0]
    assert p["status"] == "unsupported" and p["target"] is None
    assert {"UNSUPPORTED_PATTERN", "SOURCE_ABBR_CASE"} <= codes(p)


def test_reference_lowercase_abbr_flagged(svc):
    r = svc.translate("本方法的定量下限（LLOQ）为0.5 ng/mL。", "zh2en",
                      reference="The lloq of this method is 0.5 ng/mL.")["paragraphs"][0]
    assert r["status"] == "checked"
    assert "ABBR_CASE" in codes(r)


# ---------- 冲突 / 核对警告 ----------
def test_reference_forbidden_variant_conflict(svc):
    r = svc.translate("本方法的定量下限为0.5 ng/mL。", "zh2en",
                      reference="The lower limit of detection of this method is 0.5 ng/mL.")["paragraphs"][0]
    assert "FORBIDDEN_VARIANT" in codes(r)
    w = next(w for w in r["warnings"] if w["code"] == "FORBIDDEN_VARIANT")
    assert w["severity"] == "error" and w["term_id"] == "lloq" and "lower limit of detection" in w["message"]
    assert "TERM_MISSING" in codes(r)  # 同时缺少推荐译法


def test_reference_forbidden_variant_en2zh(svc):
    r = svc.translate("The lower limit of quantification (LLOQ) is 1 ng/mL.", "en2zh",
                      reference="LLOQ 即检测限，为1 ng/mL。")["paragraphs"][0]
    assert "FORBIDDEN_VARIANT" in codes(r)   # “检测限”是禁用译法
    assert "TERM_MISSING" not in codes(r)    # 缩写 LLOQ 已原样保留


def test_reference_good_translation_has_no_warnings(svc):
    r = svc.translate("质控样品的准确度在±15%以内。", "zh2en",
                      reference="The accuracy of quality control (QC) samples was within ±15%.")["paragraphs"][0]
    assert not [w for w in r["warnings"] if w["severity"] in ("warning", "error")]


def test_reference_term_missing_and_number_mismatch(svc):
    r = svc.translate("校准曲线包含8个非零浓度水平。", "zh2en",
                      reference="The standard line contained 6 levels.")["paragraphs"][0]
    assert {"TERM_MISSING", "NUMBER_MISMATCH"} <= codes(r)


def test_reference_paragraph_count_mismatch(svc):
    r = svc.translate("共分析42份研究样品。\n本批未关闭的偏差为2项。", "zh2en",
                      reference="A total of 42 study samples were analyzed.")
    assert any(w["code"] == "PARAGRAPH_COUNT_MISMATCH" for w in r["document_warnings"])
    assert "NO_REFERENCE_PARAGRAPH" in codes(r["paragraphs"][1])


def test_ambiguous_term_info(svc):
    p = svc.translate("该偏差已关闭。", "zh2en")["paragraphs"][0]
    assert p["status"] == "translated"
    amb = [w for w in p["warnings"] if w["code"] == "AMBIGUOUS_TERM"]
    assert amb and amb[0]["severity"] == "info"


# ---------- 未知句型 ----------
def test_unknown_sentence_not_pretended(svc):
    p = svc.translate("该化合物在大鼠体内的半衰期约为4小时。", "zh2en")["paragraphs"][0]
    assert p["status"] == "unsupported" and p["target"] is None
    assert "UNSUPPORTED_PATTERN" in codes(p)
    assert any("不支持" in w["message"] for w in p["warnings"])


def test_near_miss_of_supported_pattern_is_unsupported(svc):
    for s in ("本方法的定量下限为0.5 ng/mL，高于预期。", "本方法的定量下限为约0.5 ng/mL。", "本方法的定量下限为0.5。"):
        p = svc.translate(s, "zh2en")["paragraphs"][0]
        assert p["status"] == "unsupported" and p["target"] is None, s


def test_unsupported_still_gives_term_hits(svc):
    p = svc.translate("稀释完整性考察显示基质效应可以忽略。", "zh2en")["paragraphs"][0]
    assert p["status"] == "unsupported" and p["target"] is None
    assert {h["term_id"] for h in p["term_hits"]} >= {"dilution_integrity", "matrix_effect"}
    assert all(h["expected"]["en"] for h in p["term_hits"])


def test_partial_paragraph_marks_untranslated_sentence(svc):
    p = svc.translate("共分析42份研究样品。该化合物在大鼠体内的半衰期约为4小时。", "zh2en")["paragraphs"][0]
    assert p["status"] == "partial"
    assert "A total of 42 study samples were analyzed." in p["target"]
    assert "[未翻译: 该化合物在大鼠体内的半衰期约为4小时。]" in p["target"]
    assert "PARTIAL_TRANSLATION" in codes(p)


def test_language_mismatch(svc):
    p = svc.translate("A total of 42 study samples were analyzed.", "zh2en")["paragraphs"][0]
    assert p["status"] == "unsupported" and "LANGUAGE_MISMATCH" in codes(p)


def test_bilingual_mode(svc):
    r = svc.translate("共分析42份研究样品。\nThe deviation has been closed.\n今天天气很好。", "bilingual")
    rows = r["bilingual_rows"]
    assert rows[0] == {"index": 0, "zh": "共分析42份研究样品。", "en": "A total of 42 study samples were analyzed."}
    assert rows[1]["zh"] == "该偏差已关闭。" and rows[1]["en"] == "The deviation has been closed."
    assert rows[2]["zh"] == "今天天气很好。" and rows[2]["en"] is None
    assert r["stats"] == {"translated": 2, "partial": 0, "unsupported": 1, "checked": 0}
    assert "不是通用机器翻译" in r["capability_note"]


def test_input_validation(svc):
    with pytest.raises(TranslationError):
        svc.translate("   \n  ", "zh2en")
    with pytest.raises(TranslationError):
        svc.translate("共分析42份研究样品。", "bilingual", reference="x")
    with pytest.raises(TranslationError):
        svc.translate("共分析42份研究样品。", "fr2en")
    assert detect_lang("LLOQ is 1 ng/mL") == "en" and detect_lang("定量下限 LLOQ") == "zh"


# ---------- provider 边界 ----------
def test_provider_is_not_generative_and_no_silent_fallback(provider):
    from app.llm.base import get_provider

    assert provider.is_generative is False and provider.name == "deterministic"
    assert provider.polish("abc 1.5", {"1.5"}) == "abc 1.5"
    with pytest.raises(NotImplementedError):
        get_provider(Settings(llm_provider="local", local_model_path="D:/models/none"))


# ---------- API ----------
def test_api_requires_token(api):
    assert api.post("/api/translate", json={"text": "共分析42份研究样品。", "direction": "zh2en"}).status_code == 401
    assert api.get("/api/translate/capabilities").status_code == 401


def test_api_contract_and_audit(api, auth):
    r = api.post("/api/translate", headers=auth, json={
        "text": "共分析42份研究样品。\n该化合物在大鼠体内的半衰期约为4小时。", "direction": "zh2en"})
    assert r.status_code == 200
    body = r.json()
    assert {"paragraphs", "stats", "capability_note", "glossary_note", "request_id", "document_warnings"} <= set(body)
    p0, p1 = body["paragraphs"]
    assert {"index", "source", "target", "status", "term_hits", "warnings"} <= set(p0)
    assert p0["status"] == "translated" and p1["status"] == "unsupported" and p1["target"] is None
    audit = api.get("/api/audit", params={"request_id": body["request_id"]}, headers=auth).json()[0]
    assert audit["feature"] == "/api/translate" and audit["refused"] is False
    assert "P-N-SAMPLES" in audit["rule_ids"] and "WARN:UNSUPPORTED_PATTERN" in audit["rule_ids"]


def test_api_errors(api, auth):
    assert api.post("/api/translate", headers=auth, json={"text": "", "direction": "zh2en"}).status_code == 422
    assert api.post("/api/translate", headers=auth, json={"text": "x", "direction": "xx"}).status_code == 422
    r = api.post("/api/translate", headers=auth,
                 json={"text": "共分析42份研究样品。", "direction": "bilingual", "reference": "x"})
    assert r.status_code == 422


def test_api_capabilities(api, auth):
    c = api.get("/api/translate/capabilities", headers=auth).json()
    assert c["is_generative"] is False and c["provider"] == "deterministic"
    assert len(c["patterns"]) >= 10 and c["glossary_terms"] >= 40
    assert "不是通用机器翻译" in c["note"]
