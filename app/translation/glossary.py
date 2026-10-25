"""术语表加载与术语命中。

匹配规则：同一位置取最长匹配（“相对偏差”优先于“偏差”）；缩写按原大小写精确匹配，
长度 >= 3 的缩写额外识别大小写不一致（如 lloq），长度 < 3 的缩写（IS、QA、CV 等）只做精确匹配，
避免把普通英文词（is）误当术语。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.settings import ROOT

DEFAULT_TERMS_PATH = ROOT / "config" / "glossary" / "terms.yaml"
CASE_CHECK_MIN_LEN = 3


@dataclass(frozen=True)
class Term:
    id: str
    zh: str
    en: str
    abbr: str | None = None
    category: str | None = None
    forbidden_en: tuple[str, ...] = ()
    forbidden_zh: tuple[str, ...] = ()
    ambiguity_note: str | None = None


@dataclass(frozen=True)
class TermHit:
    term_id: str
    matched_text: str
    kind: str  # zh / en / abbr
    start: int
    end: int
    case_ok: bool = True


def en_phrase_regex(phrase: str) -> re.Pattern:
    """英文短语，大小写不敏感，允许复数后缀。"""
    return re.compile(rf"(?<![A-Za-z]){re.escape(phrase)}(?:s|es)?(?![A-Za-z])", re.I)


def abbr_regex(abbr: str, ignore_case: bool) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(abbr)}(?![A-Za-z0-9])", re.I if ignore_case else 0)


@dataclass
class Glossary:
    terms: list[Term]
    disclaimer: str = ""
    by_id: dict[str, Term] = field(init=False)

    def __post_init__(self):
        self.by_id = {t.id: t for t in self.terms}
        self._en = {t.id: en_phrase_regex(t.en) for t in self.terms}
        self._abbr_exact = {t.id: abbr_regex(t.abbr, False) for t in self.terms if t.abbr}
        self._abbr_ci = {t.id: abbr_regex(t.abbr, True) for t in self.terms
                         if t.abbr and len(t.abbr) >= CASE_CHECK_MIN_LEN}

    @classmethod
    def load(cls, path: Path | None = None) -> "Glossary":
        raw = yaml.safe_load(Path(path or DEFAULT_TERMS_PATH).read_text(encoding="utf-8"))
        terms = [Term(
            id=t["id"], zh=t["zh"], en=t["en"], abbr=t.get("abbr"), category=t.get("category"),
            forbidden_en=tuple(t.get("forbidden_en", ())), forbidden_zh=tuple(t.get("forbidden_zh", ())),
            ambiguity_note=t.get("ambiguity_note"),
        ) for t in raw["terms"]]
        return cls(terms, raw.get("disclaimer", ""))

    def by_category(self, category: str) -> list[Term]:
        return [t for t in self.terms if t.category == category]

    def find_hits(self, text: str) -> list[TermHit]:
        cands: list[TermHit] = []
        for t in self.terms:
            for m in re.finditer(re.escape(t.zh), text):
                cands.append(TermHit(t.id, m.group(), "zh", m.start(), m.end()))
            for m in self._en[t.id].finditer(text):
                cands.append(TermHit(t.id, m.group(), "en", m.start(), m.end()))
            if t.abbr:
                exact = {(m.start(), m.end()) for m in self._abbr_exact[t.id].finditer(text)}
                for s, e in exact:
                    cands.append(TermHit(t.id, text[s:e], "abbr", s, e))
                if t.id in self._abbr_ci:
                    for m in self._abbr_ci[t.id].finditer(text):
                        if (m.start(), m.end()) not in exact:
                            cands.append(TermHit(t.id, m.group(), "abbr", m.start(), m.end(), case_ok=False))
        chosen: list[TermHit] = []
        taken: list[tuple[int, int]] = []
        for h in sorted(cands, key=lambda h: (-(h.end - h.start), h.start)):
            if not any(h.start < e and s < h.end for s, e in taken):
                chosen.append(h)
                taken.append((h.start, h.end))
        return sorted(chosen, key=lambda h: h.start)
