"""翻译服务：分段 → 分句 → 句型翻译 → 术语命中 → 核对 → 汇总警告。

能力边界（如实）：只翻译 patterns.yaml 登记的句型；其它文本不生成译文，只给术语对照。
这不是通用机器翻译，也不是 LLM。
"""
import re
from collections import Counter

from app.llm.base import LLMProvider
from app.translation.checker import Warn, check_target, source_warnings
from app.translation.glossary import Glossary, TermHit

CAPABILITY_NOTE = (
    "离线规则实现：仅翻译已登记的固定句型（GET /api/translate/capabilities 可查看），"
    "不是通用机器翻译，也不具备 LLM 能力。无法识别的句子不会生成译文，只提供术语对照，需人工翻译。"
)
LLM_NOTE = (
    "已启用大模型（DeepSeek）作为兜底：已登记的固定句型仍由规则翻译；其余句子由大模型翻译，发送前已脱敏，"
    "译文须通过“数字与原文完全一致”和术语核对，否则不输出。大模型译文仅供参考，必须人工复核。"
)
_ZH_SENT = re.compile(r"(?<=[。！？；])")
_EN_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
MAX_PARAGRAPHS = 200


class TranslationError(ValueError):
    pass


def detect_lang(text: str) -> str:
    cjk = len(re.findall(r"[一-鿿]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk and cjk >= 0.3 * (cjk + letters) else "en"


def split_paragraphs(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def split_sentences(par: str, lang: str) -> list[str]:
    parts = _ZH_SENT.split(par) if lang == "zh" else _EN_SENT.split(par)
    return [p.strip() for p in parts if p.strip()]


def _hit_dict(glossary: Glossary, h: TermHit, src_lang: str) -> dict:
    t = glossary.by_id[h.term_id]
    return {
        "term_id": t.id, "matched_text": h.matched_text, "kind": h.kind, "span": [h.start, h.end],
        "case_ok": h.case_ok,
        "expected": {"zh": t.zh, "en": t.en, "abbr": t.abbr},
    }


class TranslationService:
    def __init__(self, provider: LLMProvider, glossary: Glossary):
        self.provider, self.glossary = provider, glossary

    def capabilities(self) -> dict:
        pats = getattr(self.provider, "patterns", None)
        return {
            "provider": self.provider.name, "is_generative": self.provider.is_generative,
            "note": LLM_NOTE if self.provider.is_generative else CAPABILITY_NOTE, "glossary_note": self.glossary.disclaimer,
            "glossary_terms": len(self.glossary.terms),
            "patterns": [{"id": p["id"], "zh": p["zh"][0], "en": p["en"][0]} for p in (pats.patterns if pats else [])],
        }

    def translate(self, text: str, direction: str, reference: str | None = None) -> dict:
        if direction not in {"zh2en", "en2zh", "bilingual"}:
            raise TranslationError(f"不支持的 direction：{direction}")
        if reference is not None and direction == "bilingual":
            raise TranslationError("bilingual 模式不支持 reference（现成译文核对）")
        paras = split_paragraphs(text)
        if not paras:
            raise TranslationError("文本为空")
        if len(paras) > MAX_PARAGRAPHS:
            raise TranslationError(f"段落数超过上限 {MAX_PARAGRAPHS}")
        refs = split_paragraphs(reference) if reference is not None else None

        out_paras = [self._one(i, p, direction, refs[i] if refs is not None and i < len(refs) else None,
                               refs is not None) for i, p in enumerate(paras)]
        doc_warnings: list[Warn] = []
        if refs is not None and len(refs) != len(paras):
            doc_warnings.append(Warn("PARAGRAPH_COUNT_MISMATCH", "error",
                                     f"原文 {len(paras)} 段，译文 {len(refs)} 段，按段落序号一一对应核对"))
        stats = Counter(p["status"] for p in out_paras)
        result = {
            "direction": direction, "paragraphs": out_paras,
            "document_warnings": [w.to_dict() for w in doc_warnings],
            "stats": {k: stats.get(k, 0) for k in ("translated", "partial", "unsupported", "checked")},
            "capability_note": CAPABILITY_NOTE, "glossary_note": self.glossary.disclaimer,
        }
        if direction == "bilingual":
            result["bilingual_rows"] = [
                {"index": p["index"], "zh": p["source"] if p["source_lang"] == "zh" else p["target"],
                 "en": p["source"] if p["source_lang"] == "en" else p["target"]} for p in out_paras]
        return result

    def _one(self, index: int, par: str, direction: str, ref: str | None, has_ref: bool) -> dict:
        lang = detect_lang(par)
        if direction == "bilingual":
            d = "zh2en" if lang == "zh" else "en2zh"
        else:
            d = direction
        src_lang, tgt_lang = ("zh", "en") if d == "zh2en" else ("en", "zh")
        hits = self.glossary.find_hits(par)
        base = {"index": index, "source": par, "source_lang": src_lang, "target_lang": tgt_lang,
                "direction": d, "target": None, "sentences": [],
                "term_hits": [_hit_dict(self.glossary, h, src_lang) for h in hits]}
        warnings = source_warnings(self.glossary, hits)

        if lang != src_lang:
            warnings.append(Warn("LANGUAGE_MISMATCH", "error",
                                 f"该段检测为{'中文' if lang == 'zh' else '英文'}，与方向 {d} 不符，未翻译"))
            return {**base, "status": "unsupported", "warnings": [w.to_dict() for w in warnings]}

        if has_ref:  # 核对模式：不翻译，只核对用户提供的译文
            if ref is None:
                warnings.append(Warn("NO_REFERENCE_PARAGRAPH", "error", "没有与该段对应的译文段落"))
                return {**base, "status": "checked", "warnings": [w.to_dict() for w in warnings]}
            warnings += check_target(self.glossary, hits, par, ref, tgt_lang)
            return {**base, "target": ref, "status": "checked", "warnings": [w.to_dict() for w in warnings]}

        sentences, targets, n_ok = [], [], 0
        for s in split_sentences(par, src_lang):
            r = self.provider.translate_sentence(s, d)
            if r is None:
                sentences.append({"source": s, "target": None, "status": "unsupported", "pattern_id": None})
                targets.append(f"[未翻译: {s}]")
            else:
                n_ok += 1
                sentences.append({"source": s, "target": r.target, "status": "translated", "pattern_id": r.pattern_id,
                                  "generated_by": "llm" if r.pattern_id.startswith("LLM:") else "pattern"})
                targets.append(r.target)
        base["sentences"] = sentences
        if n_ok == 0:
            warnings.append(Warn("UNSUPPORTED_PATTERN", "warning",
                                 "当前离线实现不支持该句型，未生成译文；仅提供术语对照，请人工翻译"))
            return {**base, "status": "unsupported", "warnings": [w.to_dict() for w in warnings]}
        target = ("" if tgt_lang == "zh" else " ").join(targets)
        base["target"] = target
        status = "translated" if n_ok == len(sentences) else "partial"
        if status == "partial":
            warnings.append(Warn("PARTIAL_TRANSLATION", "warning",
                                 "部分句子不在支持的句型内，已用“[未翻译: …]”标出，请人工翻译"))
        done_src = " ".join(s["source"] for s in sentences if s["status"] == "translated")
        done_tgt = ("" if tgt_lang == "zh" else " ").join(s["target"] for s in sentences if s["status"] == "translated")
        warnings += check_target(self.glossary, self.glossary.find_hits(done_src), done_src, done_tgt, tgt_lang)
        if any(x.get("generated_by") == "llm" for x in sentences):
            warnings.append(Warn("LLM_TRANSLATION", "warning",
                                 "部分句子由大模型翻译（已脱敏发送，并通过数字一致与术语核对），仅供参考，须人工复核"))
        return {**base, "status": status, "warnings": [w.to_dict() for w in warnings]}
