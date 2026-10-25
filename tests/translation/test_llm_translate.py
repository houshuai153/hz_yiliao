"""翻译 + 大模型兜底：固定句型仍由规则翻译；其余句子经模型，须通过数字一致与术语核对，并带“须人工复核”警告。"""
from app.translation.service import TranslationService
from tests.llm.test_deepseek import KEY, make

H = {"Authorization": "Bearer test-token"}


def with_model(api, reply):
    p, sess = make(reply)
    api.app.state.translation_service = TranslationService(p, p.glossary)
    return sess


def test_unsupported_sentence_gets_llm_translation_with_warning(api):
    sess = with_model(api, "The half-life in rats is about 4 hours.")
    r = api.post("/api/translate", headers=H, json={
        "text": "共分析42份研究样品。该化合物在大鼠体内的半衰期约为4小时。", "direction": "zh2en"}).json()
    par = r["paragraphs"][0]
    assert par["status"] == "translated" and len(sess.calls) == 1                 # 固定句型不调用模型
    assert [s["generated_by"] for s in par["sentences"]] == ["pattern", "llm"]
    assert par["target"] == "A total of 42 study samples were analyzed. The half-life in rats is about 4 hours."
    w = {x["code"]: x for x in par["warnings"]}
    assert w["LLM_TRANSLATION"]["severity"] == "warning" and "人工复核" in w["LLM_TRANSLATION"]["message"]
    assert "UNSUPPORTED_PATTERN" not in w


def test_model_translation_with_changed_number_is_rejected(api):
    with_model(api, "The half-life in rats is about 5 hours.")
    r = api.post("/api/translate", headers=H, json={"text": "该化合物在大鼠体内的半衰期约为4小时。",
                                                    "direction": "zh2en"}).json()
    par = r["paragraphs"][0]
    assert par["status"] == "unsupported" and par["target"] is None            # 不伪装译文
    assert any(x["code"] == "UNSUPPORTED_PATTERN" for x in par["warnings"])


def test_capabilities_and_audit_never_leak_key_or_raw_pii(api, tmp_path):
    sess = with_model(api, "The half-life in rats is about 4 hours.")
    cap = api.get("/api/translate/capabilities", headers=H).json()
    assert cap["provider"] == "deepseek" and cap["is_generative"] is True and "大模型" in cap["note"]
    r = api.post("/api/translate", headers=H, json={"text": "该化合物在大鼠体内的半衰期约为4小时，联系 a@b.com。",
                                                    "direction": "zh2en"})
    assert r.status_code == 200 and KEY not in r.text
    audit = api.get("/api/audit", params={"request_id": r.json()["request_id"]}, headers=H).json()[0]
    assert "LLM:deepseek" in audit["rule_ids"] and KEY not in str(audit)
    assert "a@b.com" not in str(sess.calls[0]["json"])                          # 发送前已脱敏
    logs = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "audit").glob("*.jsonl"))
    assert KEY not in logs
