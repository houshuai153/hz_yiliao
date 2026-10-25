"""中英混合分词：中文字级 unigram + bigram，英文/数字按词。不依赖 jieba。"""
import re

_TOKEN_RE = re.compile(r"[一-鿿]+|[a-z0-9]+")
# 仅过滤中文单字虚词；bigram 不过滤
_STOP_CHARS = set("的了是在和与或及对为等中应可")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        s = m.group()
        if "一" <= s[0] <= "鿿":
            tokens.extend(c for c in s if c not in _STOP_CHARS)
            tokens.extend(s[i:i + 2] for i in range(len(s) - 1))
        else:
            tokens.append(s)
    return tokens
