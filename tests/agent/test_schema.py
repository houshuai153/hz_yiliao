"""参数校验、统一结果结构与发给模型前的净化。"""
import math

import pytest

from app.agent import schema
from app.agent.schema import ArgumentError, canonical_hash, err, finalize, ok, validate_args

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pid"], "properties": {
    "pid": {"type": "string", "pattern": schema.ID_PATTERN},
    "n": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
    "kind": {"type": "string", "enum": ["a", "b"]},
    "ids": {"type": "array", "items": {"type": "string", "maxLength": 4}, "maxItems": 2, "default": []}}}


def test_defaults_and_valid_args():
    assert validate_args(SCHEMA, {"pid": "PRJ-1"}) == {"pid": "PRJ-1", "n": 3, "ids": []}
    assert validate_args(SCHEMA, {"pid": "PRJ-1", "n": None, "kind": "a"})["n"] == 3     # None 视为未提供


@pytest.mark.parametrize("bad", [
    {}, {"pid": 5}, {"pid": "../etc"}, {"pid": "x" * 70}, {"pid": "ok", "n": 0}, {"pid": "ok", "n": 6},
    {"pid": "ok", "n": True}, {"pid": "ok", "n": 1.5}, {"pid": "ok", "kind": "c"},
    {"pid": "ok", "ids": ["a", "b", "c"]}, {"pid": "ok", "ids": ["toolong"]}, {"pid": "ok", "ids": "a"},
])
def test_invalid_args_rejected(bad):
    with pytest.raises(ArgumentError):
        validate_args(SCHEMA, bad)


@pytest.mark.parametrize("field", ["operator", "confirmed", "confirmation_token", "idempotency_key", "session_key", "grant"])
def test_unknown_fields_such_as_identity_or_confirmation_are_rejected(field):
    with pytest.raises(ArgumentError) as e:
        validate_args(SCHEMA, {"pid": "ok", field: "x"})
    assert "不允许的参数" in str(e.value)


def test_non_object_args_rejected():
    for bad in ("{}", [], None, 3):
        with pytest.raises(ArgumentError):
            validate_args(SCHEMA, bad)


def test_error_message_never_echoes_the_value():
    with pytest.raises(ArgumentError) as e:
        validate_args(SCHEMA, {"pid": "sk-0123456789abcdefABCDEF/../"})
    assert "sk-0123" not in str(e.value)


def test_canonical_hash_is_order_independent_and_argument_sensitive():
    assert canonical_hash("t", {"a": 1, "b": [1, 2]}) == canonical_hash("t", {"b": [1, 2], "a": 1})
    assert canonical_hash("t", {"a": 1}) != canonical_hash("t", {"a": 2})
    assert canonical_hash("t1", {"a": 1}) != canonical_hash("t2", {"a": 1})


def test_ok_dedupes_sources_and_err_shape():
    r = ok({"x": 1}, [("run", "R1"), ("run", "R1"), ("project", "P")])
    assert r["sources"] == [{"type": "run", "id": "R1"}, {"type": "project", "id": "P"}]
    assert err("c", "m") == {"ok": False, "error_code": "c", "message": "m"}


def test_finalize_marks_untrusted_and_redacts_everywhere():
    r = finalize(ok({"note": "联系 a@b.com 手机 13812345678", "nested": [{"t": "账号 123456789012"}],
                     "key": "sk-0123456789abcdefABCDEF"}, [("run", "R1")]))
    blob = str(r)
    for raw in ("a@b.com", "13812345678", "123456789012", "sk-0123456789abcdefABCDEF"):
        assert raw not in blob
    assert r["data_trust"] == "untrusted" and r["redactions"]["邮箱"] == 1 and r["ok"] is True


def test_finalize_redacts_error_messages_too():
    r = finalize(err("x", "failed for sk-0123456789abcdefABCDEF"))
    assert "sk-0123" not in r["message"] and r["ok"] is False


def test_finalize_strips_control_chars_and_truncates_long_strings():
    r = finalize(ok({"s": "a\x00b\x1bc" + "长" * 2000}))
    s = r["data"]["s"]
    assert "\x00" not in s and "\x1b" not in s and s.endswith("…[已截断]") and len(s) < 900 and r["truncated"] is True


def test_finalize_rejects_oversized_results_instead_of_silently_cutting():
    big = ok({"rows": [{"k": "v" * 50} for _ in range(2000)]})
    r = finalize(big)
    assert r == err("result_too_large", r["message"]) and "缩小" in r["message"]


def test_finalize_handles_nan_and_rejects_non_serializable():
    assert finalize(ok({"x": math.nan}))["data"]["x"] is None
    r = finalize(ok({"x": object()}))
    assert r["ok"] is False and r["error_code"] == "result_not_serializable"


def test_redaction_keeps_real_draft_ids_but_still_masks_other_long_numbers():
    did = "DRAFT-SMP-20261026123922-31f3e9"
    r = finalize(ok({"id": did, "note": f"见 {did}，账号 123456789012，另有 DRAFT-SMP-123-zzzzzz"}, [("report", did)]))
    assert r["data"]["id"] == did and r["sources"] == [{"type": "report", "id": did}]
    assert did in r["data"]["note"] and "123456789012" not in r["data"]["note"]
    assert r["redactions"] == {"长数字串": 1}
