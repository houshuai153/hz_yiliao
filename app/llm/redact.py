"""发送前 / 返回后的脱敏：密钥、邮箱、身份证号、手机号、长数字串（疑似受试者 / 账号编号）。

规则只覆盖这些**可机器识别**的模式；它不能保证文本里没有别的敏感信息，所以真实数据接入前仍需另行评估。
替换为不含数字的占位符，不影响“数字必须来自来源”的校验。
"""
import re
from collections import Counter

_RULES: list[tuple[str, re.Pattern]] = [
    ("密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("令牌", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("身份证号", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("长数字串", re.compile(r"(?<![\d.])\d{9,}(?![\d.])")),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """返回（脱敏后文本，各类型命中次数）。"""
    counts: Counter = Counter()
    for name, rx in _RULES:
        text, n = rx.subn(f"[已脱敏:{name}]", text)
        if n:
            counts[name] += n
    return text, dict(counts)
