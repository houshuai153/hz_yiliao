"""句型引擎：把登记的句型模板编译成正则，匹配后用另一语言的模板输出。

只处理 config/glossary/patterns.yaml 中登记的句型。槽位内容（数字、单位、编号、质控水平缩写）原样复制，
术语槽位（TERM.<category>）通过术语表映射。未匹配的句子返回 None，由调用方标记为不支持，绝不猜测翻译。
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.settings import ROOT
from app.translation.glossary import Glossary

DEFAULT_PATTERNS_PATH = ROOT / "config" / "glossary" / "patterns.yaml"
_SLOT_RE = re.compile(r"\{(\w+):([\w.]+)\}")
_SLOT_REGEX = {
    "NUM": r"\d+(?:\.\d+)?",
    "INT": r"\d+",
    "PCT": r"[±+\-]?\d+(?:\.\d+)?%",
    "UNIT": r"(?:ng|pg|µg|μg|mg)/mL",
    "ID": r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+",
    "LV": r"LLOQ|LQC|MQC|HQC|ULOQ",
    "NAME": r"[A-Za-z0-9](?:[A-Za-z0-9_\-]|\.(?=[A-Za-z0-9]))*",
}
_ZH_PUNCT = str.maketrans({"(": "（", ")": "）", ",": "，", ";": "；"})
_EN_PUNCT = str.maketrans({"（": "(", "）": ")", "，": ",", "；": ";"})


@dataclass(frozen=True)
class PatternResult:
    target: str
    pattern_id: str


class _Compiled:
    def __init__(self, regex: re.Pattern, slots: list[tuple[str, str]]):
        self.regex, self.slots = regex, slots


class PatternTranslator:
    def __init__(self, patterns: list[dict], glossary: Glossary):
        self.patterns, self.glossary = patterns, glossary
        self._zh = {p["id"]: [self._compile(t, "zh") for t in p["zh"]] for p in patterns}
        self._en = {p["id"]: [self._compile(t, "en") for t in p["en"]] for p in patterns}

    @classmethod
    def load(cls, glossary: Glossary, path: Path | None = None) -> "PatternTranslator":
        raw = yaml.safe_load(Path(path or DEFAULT_PATTERNS_PATH).read_text(encoding="utf-8"))
        return cls(raw["patterns"], glossary)

    # ---------- 编译 ----------
    def _slot_regex(self, kind: str, lang: str) -> str:
        if kind.startswith("TERM."):
            cat = kind.split(".", 1)[1]
            words = [t.zh if lang == "zh" else t.en for t in self.glossary.by_category(cat)]
            return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        return _SLOT_REGEX[kind]

    def _compile(self, template: str, lang: str) -> _Compiled:
        template = template.rstrip("。.")
        parts, slots, pos = [], [], 0
        for m in _SLOT_RE.finditer(template):
            parts.append(self._lit(template[pos:m.start()], lang))
            parts.append(f"(?P<{m.group(1)}>{self._slot_regex(m.group(2), lang)})")
            slots.append((m.group(1), m.group(2)))
            pos = m.end()
        parts.append(self._lit(template[pos:], lang))
        tail = r"\s*[。]?\s*$" if lang == "zh" else r"\s*[.]?\s*$"
        return _Compiled(re.compile(r"^\s*" + "".join(parts) + tail), slots)

    @staticmethod
    def _lit(text: str, lang: str) -> str:
        if lang == "zh":
            return re.sub(r"(\\ )+|\s+", r"\\s*", re.escape(text))
        return r"(?i:" + re.sub(r"(\\ )+|\s+", r"\\s+", re.escape(text)) + ")"

    # ---------- 翻译 ----------
    def translate(self, sentence: str, direction: str) -> PatternResult | None:
        src_lang = "zh" if direction == "zh2en" else "en"
        s = sentence.strip().translate(_ZH_PUNCT if src_lang == "zh" else _EN_PUNCT)
        src, tgt = (self._zh, self._en) if src_lang == "zh" else (self._en, self._zh)
        for p in self.patterns:
            for comp in src[p["id"]]:
                m = comp.regex.match(s)
                if m:
                    return PatternResult(self._render(p, tgt[p["id"]][0], comp, m, src_lang), p["id"])
        return None

    def _render(self, p: dict, target: _Compiled, src_comp: _Compiled, m: re.Match, src_lang: str) -> str:
        tgt_lang = "en" if src_lang == "zh" else "zh"
        template = (p["en"] if tgt_lang == "en" else p["zh"])[0]
        out = template
        for name, kind in src_comp.slots:
            val = m.group(name)
            if kind.startswith("TERM."):
                val = self._map_term(val, kind.split(".", 1)[1], src_lang)
            out = out.replace("{%s:%s}" % (name, kind), val)
        return out

    def _map_term(self, value: str, category: str, src_lang: str) -> str:
        for t in self.glossary.by_category(category):
            if (t.zh == value) if src_lang == "zh" else (t.en.lower() == value.lower()):
                return t.en if src_lang == "zh" else t.zh
        raise KeyError(value)  # 正则只允许术语表内的词，理论上不可达
