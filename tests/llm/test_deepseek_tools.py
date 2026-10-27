"""DeepSeek 的 tools / tool_calls 传输、模型响应的严格解析、模型客户端的启用条件。全部使用伪造的 HTTP 会话，不联网。
这些测试只证明请求 / 解析逻辑正确，不代表真实 DeepSeek 在线调用已验证。"""
import json
from types import SimpleNamespace

import pytest
import requests

from app.agent.orchestrator import AgentDisabled, client_of
from app.agent.schema import ModelProtocolError, parse_model_message
from app.llm.deepseek import LLMError
from tests.llm.test_deepseek import KEY, FakeResp, make

TOOLS = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
RAW = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]}}], "usage": {"total_tokens": 9}}


def test_chat_with_tools_sends_tools_and_returns_the_raw_response():
    p, sess = make(FakeResp(payload=RAW))
    out = p.chat_with_tools(MSGS, TOOLS, max_tokens=77)
    c = sess.calls[0]
    assert out == RAW                                                           # 不在传输层做解析，交给编排层严格校验
    assert c["json"]["tools"] == TOOLS and c["json"]["tool_choice"] == "auto" and c["json"]["messages"] == MSGS
    assert c["json"]["max_tokens"] == 77 and c["json"]["temperature"] == 0 and c["json"]["stream"] is False
    assert c["allow_redirects"] is False and c["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert KEY not in json.dumps(c["json"])


@pytest.mark.parametrize("reply", [FakeResp(500), FakeResp(payload="bad"), FakeResp(payload=["x"]),
                                   requests.ConnectionError("boom " + KEY), requests.Timeout("t")])
def test_transport_errors_become_llm_error_without_leaking_the_key(reply):
    p, _ = make(reply)
    with pytest.raises(LLMError) as e:
        p.chat_with_tools(MSGS, TOOLS)
    assert KEY not in str(e.value) and KEY not in repr(e.value)


# ---------- parse_model_message ----------
def msg(**message):
    return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": "stop"}], "usage": {"total_tokens": 12}}


def test_parse_final_answer_and_usage():
    t = parse_model_message(msg(content="你好"))
    assert (t.content, t.calls, t.total_tokens, t.finish_reason) == ("你好", (), 12, "stop")


def test_parse_tool_calls_drops_intermediate_content():
    t = parse_model_message(msg(content="我先想想……", tool_calls=RAW["choices"][0]["message"]["tool_calls"]))
    assert t.content is None and [(c.id, c.name, c.args) for c in t.calls] == [("c1", "t", {})]      # 不保留模型的中间文字


def test_parse_empty_arguments_string_means_no_arguments():
    c = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "  "}}
    assert parse_model_message(msg(tool_calls=[c])).calls[0].args == {}


@pytest.mark.parametrize("usage,expected", [({"total_tokens": 5}, 5), ({"total_tokens": -1}, None), ({"total_tokens": True}, None),
                                            ({"total_tokens": "7"}, None), ("x", None), (None, None)])
def test_parse_usage_is_validated(usage, expected):
    raw = msg(content="x")
    raw["usage"] = usage
    assert parse_model_message(raw).total_tokens == expected


@pytest.mark.parametrize("raw", [None, [], {}, {"choices": "x"}, {"choices": [None]}, {"choices": [{}]},
                                 {"choices": [{"message": []}]}, msg(content="  "), msg(content="x" * 20_001)])
def test_parse_rejects_malformed_responses(raw):
    with pytest.raises(ModelProtocolError):
        parse_model_message(raw)


# ---------- 模型客户端何时可用 ----------
def test_client_is_disabled_by_default_and_when_the_provider_cannot_call_tools(settings):
    with pytest.raises(AgentDisabled):
        client_of(SimpleNamespace(settings=settings))                            # 默认 deterministic
    state = SimpleNamespace(settings=settings, llm_provider_obj=SimpleNamespace(is_generative=True))
    with pytest.raises(AgentDisabled):
        client_of(state)                                                         # 在线但不支持 tools


def test_client_uses_a_generative_provider_with_tool_support_or_an_injected_client(settings):
    prov = SimpleNamespace(is_generative=True, chat_with_tools=lambda *a, **k: RAW)
    assert client_of(SimpleNamespace(settings=settings, llm_provider_obj=prov)) is prov
    injected = object()
    assert client_of(SimpleNamespace(settings=settings, agent_client=injected)) is injected
