"""问答 + 大模型润色：规则先定（检索、引用校验、拒答、confidence），模型只改措辞；校验不过关就保留抽取式答案。"""
import json

from app.regulatory.service import RegulatoryAssistant
from tests.llm.test_deepseek import make

Q = "偏差在什么条件下可以关闭"


def polished_reply(prefix="整理后："):
    def reply(payload):
        original = payload["messages"][1]["content"].split("原文：\n", 1)[1]
        return prefix + original.replace("•", "-")      # 只改措辞，保留数字与【引用】
    return reply


def test_polish_keeps_citations_confidence_and_markers(index, cfg):
    base = RegulatoryAssistant(index, cfg).ask(Q)
    p, sess = make(polished_reply())
    res = RegulatoryAssistant(index, cfg, provider=p).ask(Q)
    assert res.answer_mode == "llm_polished" and res.llm["used"] is True and res.llm["provider"] == "deepseek"
    assert res.answer.startswith("整理后：") and res.extractive_answer == base.answer
    assert res.citations == base.citations and res.confidence == base.confidence and res.refused is False
    assert all(f"【{c['section_id']}】" in res.answer for c in res.citations)
    sent = json.dumps(sess.calls[0]["json"], ensure_ascii=False)
    assert "sk-test" not in sent and len(sess.calls) == 1


def test_bad_model_output_falls_back_to_extractive_answer(index, cfg):
    base = RegulatoryAssistant(index, cfg).ask(Q)
    p, _ = make("编造的回答，没有任何引用。")
    res = RegulatoryAssistant(index, cfg, provider=p).ask(Q)
    assert res.answer == base.answer and res.answer_mode == "extractive" and res.extractive_answer is None
    assert res.llm["used"] is False and res.llm["fallback_reason"] == "citation_markers_changed"


def test_refusals_never_call_the_model(index, cfg):
    p, sess = make("不应被调用")
    a = RegulatoryAssistant(index, cfg, provider=p)
    r1, r2 = a.ask("请给出 ICH M10 第 7.3.2 条的原文"), a.ask("今天天气怎么样")
    assert r1.refused and r2.refused and sess.calls == [] and r1.llm == {}


def test_default_assistant_has_no_model(index, cfg):
    res = RegulatoryAssistant(index, cfg).ask(Q)
    assert res.answer_mode == "extractive" and res.llm == {}
