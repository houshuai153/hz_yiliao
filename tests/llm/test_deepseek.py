"""大模型接入（DeepSeek）：脱敏、校验、降级、密钥不外泄。全部使用伪造的 HTTP 会话，不联网。"""
import json

import pytest
import requests
from pydantic import ValidationError

from app.llm.base import DeterministicProvider, get_provider
from app.llm.deepseek import DeepSeekProvider, LLMError
from app.llm.redact import redact
from app.settings import Settings

KEY = "sk-test-0123456789abcdefABCDEF"


class FakeResp:
    def __init__(self, status=200, content="", payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"choices": [{"message": {"content": content}}]}

    def json(self):
        if self._payload == "bad":
            raise ValueError("bad json")
        return self._payload


class FakeSession:
    """记录每次请求；reply 为字符串、FakeResp 或抛出的异常。"""
    def __init__(self, reply=""):
        self.reply, self.calls = reply, []

    def post(self, url, json=None, timeout=None, allow_redirects=None, headers=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout, "allow_redirects": allow_redirects,
                           "headers": headers})
        r = self.reply(json) if callable(self.reply) else self.reply
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, FakeResp) else FakeResp(content=r)


def make(reply="", **kw):
    s = Settings(llm_provider="deepseek", llm_api_key=KEY, **kw)
    sess = FakeSession(reply)
    return DeepSeekProvider(s, session=sess), sess


# ---------- 配置 ----------
def test_default_provider_is_deterministic_and_offline():
    s = Settings()
    assert s.llm_provider == "deterministic" and isinstance(get_provider(s), DeterministicProvider)


def test_deepseek_requires_key_and_https():
    with pytest.raises(ValidationError):
        Settings(llm_provider="deepseek")
    with pytest.raises(ValidationError):
        Settings(llm_provider="deepseek", llm_api_key=KEY, llm_base_url="http://example.com")
    Settings(llm_provider="deepseek", llm_api_key=KEY, llm_base_url="http://127.0.0.1:9999")     # 回环地址允许
    with pytest.raises(ValidationError):
        Settings(llm_provider="mystery")


def test_key_in_local_model_path_is_rejected_without_echoing_it():
    with pytest.raises(ValidationError) as e:
        Settings(local_model_path=KEY)
    assert KEY not in str(e.value) and "LLM_API_KEY" in str(e.value)


def test_key_never_shown_in_repr_or_dump():
    s = Settings(llm_provider="deepseek", llm_api_key=KEY)
    assert KEY not in repr(s) and KEY not in str(s) and KEY not in json.dumps(s.model_dump(mode="json"))
    assert s.llm_api_key.get_secret_value() == KEY


# ---------- 脱敏 ----------
def test_redact_patterns():
    text = "联系 zhang.san@example.com 或 13812345678；身份证 110101199003071234；账号 123456789012；密钥 " + KEY
    out, counts = redact(text)
    assert counts == {"密钥": 1, "邮箱": 1, "身份证号": 1, "手机号": 1, "长数字串": 1}
    for raw in ("zhang.san@example.com", "13812345678", "110101199003071234", "123456789012", KEY):
        assert raw not in out
    assert "[已脱敏:邮箱]" in out and not any(c.isdigit() for c in out)       # 占位符不含数字


def test_redact_keeps_business_numbers_and_ids():
    text = "PRJ-SM-001-R01 的 LLOQ 为 0.5 ng/mL，共 12 个 QC，DEMO-SOP-DEV-004，批 2026-10-23。"
    assert redact(text) == (text, {})


# ---------- 请求安全 ----------
def test_request_is_redacted_authenticated_and_does_not_follow_redirects():
    p, sess = make("The sample was frozen at -80°C.")
    p.translate_sentence("样品于零下80度保存，联系 a@b.com，手机 13812345678。", "zh2en")
    c = sess.calls[0]
    assert c["headers"] == {"Authorization": f"Bearer {KEY}"} and c["allow_redirects"] is False
    sent = json.dumps(c["json"], ensure_ascii=False)
    assert "a@b.com" not in sent and "13812345678" not in sent and KEY not in sent
    assert c["url"] == "https://api.deepseek.com/chat/completions" and c["json"]["temperature"] == 0
    assert c["json"]["model"] == "deepseek-chat" and c["timeout"] == 30.0


# ---------- 翻译 ----------
def test_registered_pattern_does_not_call_the_model():
    p, sess = make("should not be used")
    r = p.translate_sentence("共分析42份研究样品。", "zh2en")
    assert r.target == "A total of 42 study samples were analyzed." and not r.pattern_id.startswith("LLM:")
    assert sess.calls == []


def test_unknown_sentence_uses_model_when_numbers_match():
    p, sess = make("The half-life in rats is about 4 hours.")
    r = p.translate_sentence("该化合物在大鼠体内的半衰期约为4小时。", "zh2en")
    assert r.target == "The half-life in rats is about 4 hours." and r.pattern_id == "LLM:deepseek"
    assert len(sess.calls) == 1


@pytest.mark.parametrize("reply", [
    "The half-life in rats is about 5 hours.",         # 数字被改
    "The half-life is about 4 hours or 6 hours.",       # 多出数字
    "UNTRANSLATABLE", "", "line one\nline two 4",
    FakeResp(500), FakeResp(payload="bad"), FakeResp(payload={"choices": []}),
    requests.ConnectionError("boom http://x?key=" + KEY), requests.Timeout("t"),
])
def test_bad_or_failed_model_output_is_never_used(reply):
    p, _ = make(reply)
    assert p.translate_sentence("该化合物在大鼠体内的半衰期约为4小时。", "zh2en") is None


def test_model_output_is_redacted():
    p, _ = make("Contact a@b.com: the half-life is about 4 hours.")
    r = p.translate_sentence("该化合物的半衰期约为4小时。", "zh2en")
    assert "a@b.com" not in r.target and "[已脱敏:邮箱]" in r.target


def test_error_messages_do_not_contain_the_key():
    p, _ = make(requests.ConnectionError("failed for " + KEY))
    with pytest.raises(LLMError) as e:
        p._chat("s", "u")
    assert KEY not in str(e.value) and KEY not in repr(e.value)


# ---------- 改写校验 ----------
SRC = "• 偏差关闭须完成根因分析，周期不超过 30 天。【DEMO-SOP-DEV-004】"


@pytest.mark.parametrize("reply,reason", [
    ("偏差关闭须完成根因分析，周期不超过 45 天。【DEMO-SOP-DEV-004】", "new_numbers"),
    ("偏差关闭须完成根因分析，周期不超过 30 天。【DEMO-SOP-DEV-005】", "citation_markers_changed"),
    ("偏差关闭须完成根因分析，周期不超过 30 天。", "citation_markers_changed"),
    ("参见 DEMO-SOP-QC-001：偏差关闭须完成根因分析，周期不超过 30 天。【DEMO-SOP-DEV-004】", "new_identifiers"),
    ("", "empty_output"),
    ("长" * 500, "too_long"),
])
def test_rewrite_rejects_unfaithful_output(reply, reason):
    p, _ = make(reply)
    rw = p.rewrite(SRC, instruction="润色", allowed_numbers={"30"})
    assert rw.used is False and rw.text == SRC and rw.reason == reason


def test_rewrite_accepts_faithful_output_and_requires_kept_text():
    good = "偏差关闭须先完成根因分析，且周期不超过 30 天。【DEMO-SOP-DEV-004】"
    p, _ = make(good)
    rw = p.rewrite(SRC, instruction="润色", allowed_numbers={"30"})
    assert rw.used and rw.text == good
    rw = p.rewrite(SRC, instruction="润色", allowed_numbers={"30"}, must_keep=["[待补充: 仪器]"])
    assert not rw.used and rw.reason == "required_text_missing"
    rw = p.rewrite(SRC, instruction="润色", allowed_numbers={"30"}, allow_digits=False)
    assert not rw.used and rw.reason == "digits_not_allowed"


def test_rewrite_degrades_on_network_error_without_raising():
    p, _ = make(requests.ConnectionError("down"))
    rw = p.rewrite(SRC, instruction="润色", allowed_numbers={"30"})
    assert rw.used is False and rw.text == SRC and rw.reason.startswith("llm_error")


def test_rewrite_reports_outbound_redactions():
    p, sess = make("ok")
    rw = p.rewrite("联系 a@b.com。", instruction="润色", allowed_numbers=set())
    assert rw.redactions == {"邮箱": 1} and "a@b.com" not in json.dumps(sess.calls[0]["json"], ensure_ascii=False)


def test_deterministic_rewrite_is_a_noop():
    rw = DeterministicProvider().rewrite("x", instruction="y", allowed_numbers=set())
    assert (rw.text, rw.used, rw.reason) == ("x", False, "llm_disabled")
