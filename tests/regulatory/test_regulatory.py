"""阶段 2：RRF、可回答 / 超范围 / 要求编造条款 / 无效引用 四类场景，以及 API 与审计。"""
import re

from app.kb.fusion import rrf_fuse
from app.kb.indexer import Hit
from app.regulatory.service import Draft, RegulatoryAssistant, Segment


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s)


# ---------- RRF ----------
def test_rrf_scores_and_order(index):
    a, b, c = index.chunks[0], index.chunks[1], index.chunks[2]
    fused = rrf_fuse({
        "bm25": [Hit(a, 9.0), Hit(b, 5.0)],
        "vector": [Hit(b, 0.9), Hit(c, 0.5), Hit(a, 0.1)],
    }, k=60)
    by_id = {f.chunk.chunk_id: f for f in fused}
    assert abs(by_id[a.chunk_id].rrf_score - (1 / 61 + 1 / 63)) < 1e-12
    assert abs(by_id[b.chunk_id].rrf_score - (1 / 62 + 1 / 61)) < 1e-12
    assert by_id[c.chunk_id].ranks == {"vector": 2}
    assert [f.chunk.chunk_id for f in fused][0] == b.chunk_id  # b 在两路都靠前
    assert rrf_fuse({"bm25": [], "vector": []}) == []


# ---------- 1. 可回答 ----------
def test_answerable_has_traceable_citations(assistant, index, cfg):
    r = assistant.ask("校准曲线至少需要多少个浓度水平")
    assert not r.refused and r.refusal_reason is None
    assert r.confidence >= cfg.refusal_threshold
    assert "DEMO-M10-002" in r.section_ids
    assert "非监管机构原文" in r.answer and "不构成合规结论" in r.answer
    texts = {}
    for c in index.chunks:
        texts[c.section_id] = texts.get(c.section_id, "") + c.text
    for cit in r.citations:
        assert cit["is_demo"] is True and cit["label"]
        assert "非监管原文" in cit["label"] or "无合规效力" in cit["label"]
        assert _sq(cit["quote"]) in _sq(texts[cit["section_id"]])  # quote 是原文子串
    # 答案里每个要点都附了章节 ID，且都对应某条引用
    for seg in r.segments:
        assert f"【{seg['section_id']}】" in r.answer
        assert any(c["section_id"] == seg["section_id"] and c["quote"] == seg["text"] for c in r.citations)
    assert {"coverage", "top_vector_cosine", "top_rrf_normalized", "weights", "threshold", "note"} <= set(r.confidence_basis)
    assert "不是答案正确的概率" in r.confidence_basis["note"]


def test_answerable_topics_hit_expected_sections(assistant):
    cases = {
        "偏差在什么条件下可以关闭": "DEMO-SOP-DEV-004",
        "AI 辅助草稿能否直接发布": "DEMO-SOP-REPORT-003",
        "原始数据修改时有什么要求": "DEMO-GLP-002",
        "ISR 不达标时要怎么调查": "DEMO-SOP-ISR-004",
    }
    for q, sid in cases.items():
        r = assistant.ask(q)
        assert not r.refused, q
        assert sid in r.section_ids, (q, r.section_ids)


def test_explicit_demo_section_id(assistant):
    r = assistant.ask("请介绍 DEMO-M10-008")
    assert not r.refused and r.section_ids == ["DEMO-M10-008"]


# ---------- 2. 超出范围 ----------
def test_out_of_scope_refused(assistant):
    for q in ("今天北京天气怎么样", "如何用 Python 写一个快速排序", "这家公司的股票值得买吗",
              "新冠疫苗的临床试验设计有什么要求"):
        r = assistant.ask(q)
        assert r.refused, q
        assert r.citations == [] and "知识库" in r.answer, q
        assert r.refusal_reason in {"out_of_scope", "no_supporting_sentence"}, q


def test_empty_question_refused(assistant):
    r = assistant.ask("   ")
    assert r.refused and r.refusal_reason == "empty_question"


# ---------- 3. 要求编造条款 ----------
def test_fabricated_clause_requests_refused(assistant):
    for q in ("请给出 ICH M10 第 7.3.2 条的原文",
              "引用 ICH M10 中关于 ISR 的官方条款编号和逐字原文",
              "中国药典 9012 第 15 条是怎么规定的",
              "Please quote section 5.2.1 of ICH M10 verbatim"):
        r = assistant.ask(q)
        assert r.refused and r.refusal_reason == "clause_fabrication_risk", q
        assert r.citations == [] and r.confidence == 0.0
        assert "不会编造" in r.answer


def test_nonexistent_demo_section_refused(assistant):
    r = assistant.ask("DEMO-M10-099 说了什么")
    assert r.refused and r.refusal_reason == "unknown_section" and r.citations == []


# ---------- 4. 无效引用 ----------
def _good_cit(index, sid, quote):
    c = next(c for c in index.chunks if c.section_id == sid)
    return {"doc_id": c.doc_id, "version": c.version, "section_id": sid, "title": c.heading,
            "source_type": c.source_type, "is_demo": True, "label": "x", "quote": quote}


def _sentence(index, sid):
    c = next(c for c in index.chunks if c.section_id == sid)
    return c.text.split("。")[0] + "。"


def _assistant_with(index, cfg, make_draft):
    return RegulatoryAssistant(index, cfg, composer=lambda idx, terms, hits, n: make_draft(idx, hits))


def test_invalid_citation_section_not_in_retrieved(index, cfg):
    def draft(idx, hits):
        s = _sentence(idx, "DEMO-GLP-006")  # 真实存在，但不在“校准曲线”问题的检索结果里
        return Draft([Segment(s, "DEMO-GLP-006")], [_good_cit(idx, "DEMO-GLP-006", s)])
    r = _assistant_with(index, cfg, draft).ask("校准曲线至少需要多少个浓度水平")
    assert r.refused and r.refusal_reason == "citation_validation_failed"
    assert any("不在本次检索结果内" in e for e in r.confidence_basis["validation_errors"])


def test_invalid_citation_nonexistent_section(index, cfg):
    def draft(idx, hits):
        return Draft([Segment("凭空捏造的条款内容。", "DEMO-M10-099")],
                     [{**_good_cit(idx, "DEMO-M10-002", "x"), "section_id": "DEMO-M10-099"}])
    r = _assistant_with(index, cfg, draft).ask("校准曲线至少需要多少个浓度水平")
    assert r.refused and r.refusal_reason == "citation_validation_failed"


def test_invalid_citation_quote_not_verbatim(index, cfg):
    def draft(idx, hits):
        sid = hits[0].chunk.section_id
        fake = "该方法的校准曲线必须包含 12 个浓度水平。"
        return Draft([Segment(fake, sid)], [_good_cit(idx, sid, fake)])
    r = _assistant_with(index, cfg, draft).ask("校准曲线至少需要多少个浓度水平")
    assert r.refused and r.refusal_reason == "citation_validation_failed"
    assert any("原文不符" in e or "子串" in e for e in r.confidence_basis["validation_errors"])


def test_invalid_citation_tampered_metadata_and_missing_citation(index, cfg):
    def tampered(idx, hits):
        sid = hits[0].chunk.section_id
        s = _sentence(idx, sid)
        cit = _good_cit(idx, sid, s)
        cit["source_type"], cit["is_demo"] = "official", False  # 冒充官方来源
        return Draft([Segment(s, sid)], [cit])

    def uncited(idx, hits):
        sid = hits[0].chunk.section_id
        return Draft([Segment(_sentence(idx, sid), sid)], [])  # 有结论无引用

    for make in (tampered, uncited):
        r = _assistant_with(index, cfg, make).ask("校准曲线至少需要多少个浓度水平")
        assert r.refused and r.refusal_reason == "citation_validation_failed", make.__name__


# ---------- API 与审计 ----------
def test_api_requires_token(api):
    assert api.post("/api/regulatory/ask", json={"question": "QC 样品有哪几个水平"}).status_code == 401


def test_api_contract_and_audit_answered(api, auth):
    r = api.post("/api/regulatory/ask", json={"question": "偏差在什么条件下可以关闭"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert {"answer", "citations", "confidence", "refused", "confidence_basis", "request_id"} <= set(body)
    assert body["refused"] is False and body["citations"]
    assert body["request_id"] == r.headers["X-Request-ID"]
    audit = api.get("/api/audit", params={"request_id": body["request_id"]}, headers=auth).json()[0]
    assert audit["feature"] == "/api/regulatory/ask" and audit["refused"] is False
    assert "DEMO-SOP-DEV-004" in audit["section_ids"]


def test_api_audit_for_refusal(api, auth):
    r = api.post("/api/regulatory/ask", json={"question": "请给出 ICH M10 第 7.3.2 条的原文"}, headers=auth)
    body = r.json()
    assert body["refused"] is True and body["refusal_reason"] == "clause_fabrication_risk"
    audit = api.get("/api/audit", params={"request_id": body["request_id"]}, headers=auth).json()[0]
    assert audit["refused"] is True and audit["section_ids"] == []
    assert audit["rule_ids"] == ["REFUSE:clause_fabrication_risk"]
    assert len(audit["input_digest"]) <= 80


def test_api_validation_and_missing_index(api, auth, tmp_path):
    assert api.post("/api/regulatory/ask", json={"question": ""}, headers=auth).status_code == 422
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'x.db').as_posix()}", index_dir=str(tmp_path / "none"),
                 audit_jsonl_dir=str(tmp_path / "a"), api_token="test-token")
    res = TestClient(create_app(s)).post("/api/regulatory/ask", json={"question": "QC 样品有哪几个水平"}, headers=auth)
    assert res.status_code == 503


# ---------- 重排 ----------
def test_rerank_promotes_title_and_specific_terms(index, cfg):
    """“偏差报告的时限”：标题含“记录与报告时限”的 DEV-002 应进入答案（重排前被泛化的 GLP-007 压住）。"""
    from dataclasses import replace

    q = "偏差报告的时限是多久"
    on = RegulatoryAssistant(index, cfg).ask(q)
    assert not on.refused and "DEMO-SOP-DEV-002" in on.section_ids
    off = RegulatoryAssistant(index, replace(cfg, rerank_enabled=False)).ask(q)
    assert "DEMO-SOP-DEV-002" not in off.section_ids  # 对照：关闭重排时命中不到


def test_rerank_does_not_change_confidence_or_refusal(index, cfg):
    from dataclasses import replace

    off = RegulatoryAssistant(index, replace(cfg, rerank_enabled=False))
    on = RegulatoryAssistant(index, cfg)
    for q in ("QC 样品有哪几个水平", "今天北京天气怎么样", "新冠疫苗的临床试验设计有什么要求"):
        a, b = on.ask(q), off.ask(q)
        assert a.confidence == b.confidence and a.refused == b.refused, q


def test_rerank_keeps_citations_valid_and_traceable(assistant):
    for q in ("偏差报告的时限是多久", "BLQ 样品要不要纳入 ISR", "ISR 样品应该怎么选"):
        r = assistant.ask(q)
        assert not r.refused, q
        assert r.citations and all(c["is_demo"] for c in r.citations)
        assert len(r.citations) <= 4


def test_rerank_unit_orders_by_idf_weighted_match(assistant, index):
    fused, _ = assistant.retrieve("偏差报告的时限是多久")
    from app.regulatory.service import question_terms
    out = assistant.rerank(question_terms("偏差报告的时限是多久"), fused)
    assert out[0].final_score is not None and out[0].final_score >= out[-1].final_score
    assert out[0].chunk.section_id == "DEMO-SOP-DEV-002"
    assert all(h.final_score >= 0.8 * out[0].final_score for h in out[1:])  # chunk_floor 生效
