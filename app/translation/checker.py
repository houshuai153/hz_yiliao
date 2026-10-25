"""译文核对：术语是否按术语表使用、缩写大小写、禁用译法、数字是否一致。

同一套检查既用于“用户提供的现成译文”（reference），也用于系统自己生成的译文（自检）。
"""
import re
from dataclasses import asdict, dataclass

from app.translation.glossary import CASE_CHECK_MIN_LEN, Glossary, TermHit, abbr_regex, en_phrase_regex

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Warn:
    code: str
    severity: str  # info / warning / error
    message: str
    term_id: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def source_warnings(glossary: Glossary, hits: list[TermHit]) -> list[Warn]:
    out: list[Warn] = []
    seen_amb: set[str] = set()
    for h in hits:
        t = glossary.by_id[h.term_id]
        if h.kind == "abbr" and not h.case_ok:
            out.append(Warn("SOURCE_ABBR_CASE", "warning",
                            f"源文中缩写大小写不规范：{h.matched_text} 应写作 {t.abbr}", t.id))
        if t.ambiguity_note and t.id not in seen_amb:
            seen_amb.add(t.id)
            out.append(Warn("AMBIGUOUS_TERM", "info", f"术语“{t.zh}”可能多义：{t.ambiguity_note}", t.id))
    return out


def check_target(glossary: Glossary, hits: list[TermHit], source: str, target: str, tgt_lang: str) -> list[Warn]:
    out: list[Warn] = []
    for term_id in dict.fromkeys(h.term_id for h in hits):  # 去重保序
        t = glossary.by_id[term_id]
        abbr_exact = bool(t.abbr and abbr_regex(t.abbr, False).search(target))
        abbr_ci = bool(t.abbr and len(t.abbr) >= CASE_CHECK_MIN_LEN and abbr_regex(t.abbr, True).search(target))
        phrase = bool(en_phrase_regex(t.en).search(target)) if tgt_lang == "en" else (t.zh in target)
        if not (phrase or abbr_exact):
            if abbr_ci:
                out.append(Warn("ABBR_CASE", "warning",
                                f"缩写大小写与术语表不一致：应为 {t.abbr}", t.id))
            else:
                expected = t.en if tgt_lang == "en" else t.zh
                if t.abbr:
                    expected += f" / {t.abbr}"
                out.append(Warn("TERM_MISSING", "warning",
                                f"译文中未找到术语“{t.zh if tgt_lang == 'en' else t.en}”的推荐译法：{expected}", t.id))
        forbidden = t.forbidden_en if tgt_lang == "en" else t.forbidden_zh
        for f in forbidden:
            hit = en_phrase_regex(f).search(target) if tgt_lang == "en" else (f in target)
            if hit:
                out.append(Warn("FORBIDDEN_VARIANT", "error",
                                f"术语“{t.zh}”出现禁用/易混译法“{f}”，推荐：{t.en if tgt_lang == 'en' else t.zh}", t.id))
    if sorted(_NUM_RE.findall(source)) != sorted(_NUM_RE.findall(target)):
        out.append(Warn("NUMBER_MISMATCH", "error",
                        f"原文与译文中的数字不一致：{_NUM_RE.findall(source)} vs {_NUM_RE.findall(target)}"))
    return out
