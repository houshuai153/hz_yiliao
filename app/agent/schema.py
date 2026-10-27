"""工具参数校验、统一结果结构、发给模型前的净化。

结果结构（所有工具统一）：
  成功 {"ok": true, "data": {...}, "sources": [{"type","id"}], "data_trust": "untrusted", ...}
  失败 {"ok": false, "error_code": "...", "message": "..."}
工具结果是“数据”，不是指令：data_trust 恒为 untrusted，供编排层在把它交给模型时明确标注，
内容不能改变角色权限或系统规则。净化步骤（finalize）：去控制字符 → app.llm.redact 脱敏 → 限长 → 限总大小。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.llm.redact import redact

MAX_STR = 800                   # 单个字符串字段的最大长度（超出截断并标记 truncated）
MAX_RESULT_BYTES = 24_000       # 单次工具结果的最大序列化大小（超出直接拒绝，不静默截断整体）
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# 草稿编号含 14 位时间戳，会被“长数字串”脱敏规则误伤；只放行严格匹配该格式的编号，其余长数字仍然脱敏
_KEEP_ID = re.compile(r"DRAFT-(?:VAL|SMP)-\d{14}-[0-9a-f]{6}")
_STASH = re.compile(r"\x00(\d+)\x00")


class ArgumentError(ValueError):
    """参数不合法。消息只含参数路径和规则，不回显取值。"""


# ---------- 参数校验（JSON Schema 子集：够用且不引入新依赖）----------
def _type_ok(v: Any, t: str) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return False


def _check(v: Any, spec: dict, path: str) -> None:
    t = spec.get("type")
    if t and not _type_ok(v, t):
        raise ArgumentError(f"参数 {path} 类型应为 {t}")
    if "enum" in spec and v not in spec["enum"]:
        raise ArgumentError(f"参数 {path} 不在允许的取值内：{list(spec['enum'])}")
    if isinstance(v, str):
        if len(v) < spec.get("minLength", 0):
            raise ArgumentError(f"参数 {path} 不能为空或过短")
        if len(v) > spec.get("maxLength", 10**9):
            raise ArgumentError(f"参数 {path} 超过长度上限 {spec['maxLength']}")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], v):
            raise ArgumentError(f"参数 {path} 格式不正确")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v < spec.get("minimum", float("-inf")) or v > spec.get("maximum", float("inf")):
            raise ArgumentError(f"参数 {path} 超出范围 [{spec.get('minimum')}, {spec.get('maximum')}]")
    if isinstance(v, list):
        if len(v) > spec.get("maxItems", 10**9):
            raise ArgumentError(f"参数 {path} 元素过多（上限 {spec['maxItems']}）")
        for i, item in enumerate(v):
            _check(item, spec.get("items", {}), f"{path}[{i}]")


def validate_args(schema: dict, args: Any) -> dict:
    """返回补全默认值后的新字典；不认识的字段一律拒绝（模型不能夹带 operator / confirmed 之类的值）。"""
    if not isinstance(args, dict):
        raise ArgumentError("工具参数必须是 JSON 对象")
    props = schema.get("properties", {})
    extra = sorted(set(args) - set(props))
    if extra:
        raise ArgumentError(f"不允许的参数：{extra}")
    out = {}
    for name, spec in props.items():
        if name in args and args[name] is not None:
            _check(args[name], spec, name)
            out[name] = args[name]
        elif "default" in spec:
            out[name] = spec["default"]
    missing = [r for r in schema.get("required", []) if r not in out]
    if missing:
        raise ArgumentError(f"缺少必填参数：{missing}")
    return out


def canonical_hash(tool: str, args: dict) -> str:
    """参数的规范化哈希：确认令牌绑定的是“哪个工具 + 哪组参数”，参数变化哈希就变。"""
    blob = json.dumps({"tool": tool, "args": args}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------- 统一结果 ----------
def ok(data: dict, sources: list[tuple[str, str]] | None = None) -> dict:
    seen, out = set(), []
    for typ, ident in sources or []:
        if (typ, ident) not in seen:
            seen.add((typ, ident))
            out.append({"type": typ, "id": ident})
    return {"ok": True, "data": data, "sources": out}


def err(code: str, message: str) -> dict:
    return {"ok": False, "error_code": code, "message": message}


class _Sanitizer:
    def __init__(self, max_len: int = MAX_STR) -> None:
        self.max_len = max_len
        self.redactions: dict[str, int] = {}
        self.truncated = False

    def text(self, s: str) -> str:
        s = _CTRL.sub("", s)                                   # 先去掉控制字符，\x00 才可安全用作占位
        kept: list[str] = []

        def stash(m: re.Match) -> str:
            kept.append(m.group(0))
            return f"\x00{len(kept) - 1}\x00"
        s, counts = redact(_KEEP_ID.sub(stash, s))
        s = _STASH.sub(lambda m: kept[int(m.group(1))], s)
        for k, n in counts.items():
            self.redactions[k] = self.redactions.get(k, 0) + n
        if len(s) > self.max_len:
            self.truncated = True
            s = s[: self.max_len] + "…[已截断]"
        return s

    def walk(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, dict):
            return {str(k): self.walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [self.walk(x) for x in v]
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return None
        if v is None or isinstance(v, (bool, int, float)):
            return v
        raise TypeError(f"结果含不可序列化的类型 {type(v).__name__}")


def finalize(result: dict, *, max_bytes: int | None = None) -> dict:
    """发给模型 / 返回给调用方之前的最后一道：脱敏、限长、限大小、标注为不可信数据。"""
    limit = MAX_RESULT_BYTES if max_bytes is None else max_bytes
    san = _Sanitizer()
    try:
        if result.get("ok"):
            out = {"ok": True, "data": san.walk(result["data"]), "sources": san.walk(result.get("sources", [])),
                   "data_trust": "untrusted"}
        else:
            out = {"ok": False, "error_code": str(result.get("error_code", "error"))[:60],
                   "message": san.text(str(result.get("message", "")))}
        if san.redactions:
            out["redactions"] = san.redactions
        if san.truncated:
            out["truncated"] = True
        size = len(json.dumps(out, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        return err("result_not_serializable", "工具结果无法序列化，已丢弃")
    if size > limit:
        return err("result_too_large", f"结果超过 {limit} 字节上限，请缩小查询范围（例如指定 section 或减小 limit）")
    return out


# ---------- 发给模型 / 存入会话前的净化 ----------
def sanitize_text(text: str, *, max_len: int = MAX_STR) -> tuple[str, dict[str, int]]:
    """去控制字符 → 脱敏 → 限长。返回（净化后的文本，脱敏命中统计）。"""
    san = _Sanitizer(max_len)
    return san.text(str(text)), san.redactions


def sanitize_json(obj: Any, *, max_len: int = MAX_STR) -> Any:
    return _Sanitizer(max_len).walk(obj)


# ---------- 模型响应的严格解析（OpenAI 兼容 tool_calls）----------
CALL_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
MAX_CONTENT = 20_000
MAX_ARGS_JSON = 8_000


class ModelProtocolError(Exception):
    """模型响应不符合协议（工具名 / 参数 JSON / 调用 ID 非法等）。整轮拒绝，不执行其中任何调用。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass(frozen=True)
class ModelTurn:
    content: str | None            # 最终回答文本；带工具调用的回合不保留 content（不转存模型的中间“思考”）
    calls: tuple[ToolCall, ...]
    finish_reason: str | None
    total_tokens: int | None       # 模型报告的 token 用量；缺失为 None


def parse_model_message(raw: Any, *, max_calls: int = 4) -> ModelTurn:
    if not isinstance(raw, dict):
        raise ModelProtocolError("响应不是 JSON 对象")
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelProtocolError("响应缺少 choices")
    msg = choices[0].get("message")
    if not isinstance(msg, dict):
        raise ModelProtocolError("响应缺少 message")
    if msg.get("role", "assistant") != "assistant":
        raise ModelProtocolError("message.role 必须是 assistant")
    content = msg.get("content")
    if content is not None and (not isinstance(content, str) or len(content) > MAX_CONTENT):
        raise ModelProtocolError("message.content 非法或过长")
    raw_calls = msg.get("tool_calls")
    calls: list[ToolCall] = []
    if raw_calls not in (None, []):
        if not isinstance(raw_calls, list) or len(raw_calls) > max_calls:
            raise ModelProtocolError(f"tool_calls 非法或超过 {max_calls} 个")
        seen: set[str] = set()
        for c in raw_calls:
            if not isinstance(c, dict) or c.get("type", "function") != "function" or not isinstance(c.get("function"), dict):
                raise ModelProtocolError("tool_call 结构非法")
            cid, fn = c.get("id"), c["function"]
            name, arguments = fn.get("name"), fn.get("arguments")
            if not isinstance(cid, str) or not CALL_ID_RE.match(cid) or cid in seen:
                raise ModelProtocolError("tool_call.id 非法或重复")
            if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
                raise ModelProtocolError("工具名非法")
            if not isinstance(arguments, str) or len(arguments) > MAX_ARGS_JSON:
                raise ModelProtocolError("工具参数必须是长度受限的 JSON 字符串")
            try:
                args = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                raise ModelProtocolError("工具参数不是合法 JSON") from None
            if not isinstance(args, dict):
                raise ModelProtocolError("工具参数必须是 JSON 对象")
            seen.add(cid)
            calls.append(ToolCall(cid, name, args))
    if not calls and (content is None or not content.strip()):
        raise ModelProtocolError("响应既没有工具调用也没有回答")
    usage = raw.get("usage")
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else None
    fin = choices[0].get("finish_reason")
    return ModelTurn(None if calls else content, tuple(calls), fin if isinstance(fin, str) else None, tokens)
