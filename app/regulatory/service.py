"""法规与 SOP 助手：混合召回 → RRF → 门控 → 抽取式作答 → 引用校验 → 拒答。

诚实说明：答案是从检索到的演示章节里抽取的原句，不由任何生成式模型撰写；
知识库全部是“教学演示摘要 / 模拟内部 SOP”，不是监管机构原文，不构成合规结论。
`confidence` 是证据充分度的启发式分数（见 SPEC 第 6 节），不是答案正确的概率，也未经校准。
"""
import re
from dataclasses import dataclass, field
from dataclasses import replace
from functools import partial
from typing import Callable

from app.kb.fusion import FusedHit, rrf_fuse
from app.kb.indexer import KBIndex
from app.kb.tokenize import tokenize
from app.regulatory.config import RegulatoryConfig

DISCLAIMER = "以上内容均摘自本地知识库中的教学演示摘要或模拟内部 SOP，并非监管机构原文，不构成合规结论，须由具备资质的人员复核。"
LABELS = {"demo_summary": "教学演示摘要（非监管原文）", "demo_sop": "模拟内部 SOP（无合规效力）"}
CONFIDENCE_NOTE = "证据充分度启发式分数，不是答案正确的概率，未经校准"

REFUSAL_TEXT = {
    "clause_fabrication_risk": "无法提供条款编号或监管原文：本知识库只有自行编写的教学演示摘要，不含官方原文，我不会编造条款编号或原文。可改问具体主题（如“ISR 的演示判定标准”）。",
    "unknown_section": "知识库中不存在你指定的章节编号，无法回答。",
    "out_of_scope": "本地知识库中没有足够依据回答该问题（超出知识库范围）。",
    "no_supporting_sentence": "检索到的章节中找不到可直接支撑答案的句子，无法作答。",
    "citation_validation_failed": "答案的引用未通过校验（引用不在检索结果内或与原文不符），已拒绝输出。",
    "empty_question": "问题为空。",
}

_DEMO_ID_RE = re.compile(r"DEMO-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}")
_CLAUSE_RE = re.compile(
    r"第\s*\d+(?:\.\d+)*\s*[条章节款]|(?:section|sec\.?|clause|article|paragraph)\s*\d+(?:\.\d+)*|"
    r"原文|逐字|逐条|官方条款|条款编号|真实条款|条款号|verbatim", re.I)
_QUESTION_WORDS = re.compile(
    r"请问|什么|怎么办|怎么样|怎么|如何|是否|哪些|哪个|多少|应该|需要|可以|能否|能不能|吗|呢|一下|告诉我|请")
_SENT_SPLIT = re.compile(r"(?<=[。；;！？])")


@dataclass(frozen=True)
class Segment:
    text: str
    section_id: str


@dataclass
class Draft:
    segments: list[Segment]
    citations: list[dict]


@dataclass
class AskResult:
    answer: str
    citations: list[dict]
    confidence: float
    confidence_basis: dict
    refused: bool
    refusal_reason: str | None = None
    segments: list[dict] = field(default_factory=list)
    answer_mode: str = "extractive"            # extractive：规则抽取的原句；llm_polished：大模型整理过措辞
    extractive_answer: str | None = None       # llm_polished 时保留未经模型改写的抽取式答案
    llm: dict = field(default_factory=dict)    # {"provider","model_used","fallback_reason","redactions"}；不含提示词与密钥

    @property
    def section_ids(self) -> list[str]:
        return sorted({c["section_id"] for c in self.citations})


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def question_terms(question: str) -> set[str]:
    """用于覆盖度与句子打分的关键词：中文 bigram + 英文/数字词，去掉疑问词。"""
    cleaned = _QUESTION_WORDS.sub(" ", question)
    return {t for t in tokenize(cleaned) if len(t) >= 2}


def default_composer(index: KBIndex, terms: set[str], hits: list[FusedHit], max_sentences: int,
                     weighted: bool = True, floor: float = 0.5) -> Draft:
    """按关键词重叠为句子打分（weighted=True 时用 BM25 的 IDF 加权，常见词如“偏差”权重低）。

    句子须达到最佳句得分的 floor 倍才入选，且最多 max_sentences 句；每个入选块至少取最相关的一句。
    """
    def weight(t: str) -> float:
        return index.bm25.idf(t) if weighted else 1.0

    candidates: list[tuple[int, int, float, Segment]] = []  # (块名次, 句位置, 得分, 句)
    for rank, h in enumerate(hits):
        for pos, sent in enumerate(x.strip() for x in _SENT_SPLIT.split(h.chunk.text)):
            if len(sent) < 6:
                continue
            low = sent.lower()
            score = sum(weight(t) for t in terms if t in low) if terms else 1.0  # 无关键词=显式章节模式
            if score > 0:
                candidates.append((rank, pos, score, Segment(sent, h.chunk.section_id)))
    if candidates:
        best = max(c[2] for c in candidates)
        candidates = [c for c in candidates if c[2] >= floor * best]
    by_score = sorted(candidates, key=lambda c: (-c[2], c[0], c[1]))
    chosen: list[tuple[int, int, float, Segment]] = []
    seen_rank: set[int] = set()
    for c in by_score:  # 先每块取最相关的一句
        if c[0] not in seen_rank and len(chosen) < max_sentences:
            chosen.append(c)
            seen_rank.add(c[0])
    for c in by_score:  # 再按得分补足
        if c not in chosen and len(chosen) < max_sentences:
            chosen.append(c)
    chosen.sort(key=lambda c: (c[0], c[1]))
    segments = [c[3] for c in chosen]
    meta = {h.chunk.section_id: h.chunk for h in hits}
    citations = [{
        "doc_id": meta[x.section_id].doc_id, "version": meta[x.section_id].version,
        "section_id": x.section_id, "title": meta[x.section_id].heading,
        "source_type": meta[x.section_id].source_type, "is_demo": meta[x.section_id].is_demo,
        "label": LABELS[meta[x.section_id].source_type], "quote": x.text,
    } for x in segments]
    return Draft(segments, citations)


class RegulatoryAssistant:
    def __init__(self, index: KBIndex, cfg: RegulatoryConfig,
                 composer: Callable[..., Draft] | None = None, provider=None):
        self.index, self.cfg = index, cfg
        self.provider = provider if getattr(provider, "is_generative", False) else None   # 默认不用模型
        self.composer = composer or partial(
            default_composer, weighted=cfg.rerank_enabled,
            floor=cfg.sentence_floor if cfg.rerank_enabled else 0.0)
        self._sections: dict[str, str] = {}
        self._meta: dict[str, object] = {}
        for c in index.chunks:
            self._sections[c.section_id] = self._sections.get(c.section_id, "") + c.text + "\n"
            self._meta.setdefault(c.section_id, c)

    # ---------- 检索 ----------
    def retrieve(self, question: str) -> tuple[list[FusedHit], float]:
        bm25 = self.index.search_bm25(question, self.cfg.retrieve_k)
        vec = self.index.search_vector(question, self.cfg.retrieve_k)
        fused = rrf_fuse({"bm25": bm25, "vector": vec}, self.cfg.rrf_k)
        return fused, (vec[0].score if vec else 0.0)

    def rerank(self, terms: set[str], fused: list[FusedHit]) -> list[FusedHit]:
        """简单重排：最终分 = (1-w)×RRF相对分 + w×IDF加权词法匹配分。

        词法匹配分 = Σ idf(t)×(0.6×[t 在块正文] + 0.4×[t 在章节标题]) / Σ idf(t)；
        标题命中额外加分，有助于“章节主题正好是所问内容”的块上浮。
        只影响用哪些块作答的顺序；confidence 与拒答判定仍由重排前的融合结果计算。
        """
        if not self.cfg.rerank_enabled or not terms or not fused:
            return fused
        idf = self.index.bm25.idf
        total = sum(idf(t) for t in terms)
        if total <= 0:
            return fused
        top = fused[0].rrf_score or 1.0
        w = self.cfg.rerank_weight

        def final(h: FusedHit) -> float:
            body, head = set(tokenize(h.chunk.text)), set(tokenize(h.chunk.heading))
            lex = sum(idf(t) * (0.6 * (t in body) + 0.4 * (t in head)) for t in terms) / total
            return (1 - w) * (h.rrf_score / top) + w * lex

        scored = sorted(((final(h), h) for h in fused), key=lambda x: (-x[0], x[1].chunk.chunk_id))
        out = [replace(h, final_score=f) for f, h in scored]
        floor = self.cfg.chunk_floor * out[0].final_score
        return [h for i, h in enumerate(out) if i == 0 or h.final_score >= floor]

    def _confidence(self, terms: set[str], fused: list[FusedHit], top_vec: float) -> dict:
        top3_tokens = {t for h in fused[:3] for t in tokenize(h.chunk.text + " " + h.chunk.heading)}
        coverage = (sum(1 for t in terms if t in top3_tokens) / len(terms)) if terms else 0.0
        max_rrf = 2.0 / (self.cfg.rrf_k + 1)
        s_rrf = min(1.0, fused[0].rrf_score / max_rrf) if fused else 0.0
        score = self.cfg.w_coverage * coverage + self.cfg.w_vector * top_vec + self.cfg.w_rrf * s_rrf
        return {
            "coverage": round(coverage, 4), "top_vector_cosine": round(top_vec, 4),
            "top_rrf_normalized": round(s_rrf, 4),
            "weights": {"coverage": self.cfg.w_coverage, "vector": self.cfg.w_vector, "rrf": self.cfg.w_rrf},
            "threshold": self.cfg.refusal_threshold, "score": round(score, 4), "note": CONFIDENCE_NOTE,
        }

    # ---------- 引用校验 ----------
    def validate(self, draft: Draft, retrieved_sections: set[str]) -> list[str]:
        errors: list[str] = []
        if not draft.segments:
            errors.append("答案没有任何带引用的内容")
        cited = {(c["section_id"], _squash(c["quote"])) for c in draft.citations}
        for seg in draft.segments:
            if seg.section_id not in retrieved_sections:
                errors.append(f"引用 {seg.section_id} 不在本次检索结果内")
                continue
            if _squash(seg.text) not in _squash(self._sections.get(seg.section_id, "")):
                errors.append(f"句子与 {seg.section_id} 原文不符")
            if (seg.section_id, _squash(seg.text)) not in cited:
                errors.append(f"句子未附对应引用：{seg.section_id}")
        for c in draft.citations:
            sid = c["section_id"]
            if sid not in retrieved_sections:
                errors.append(f"引用 {sid} 不在本次检索结果内")
                continue
            if _squash(c["quote"]) not in _squash(self._sections.get(sid, "")):
                errors.append(f"引用 {sid} 的 quote 不是章节原文子串")
            m = self._meta[sid]
            if (c["doc_id"], c["version"], c["source_type"]) != (m.doc_id, m.version, m.source_type) or c["is_demo"] is not True:
                errors.append(f"引用 {sid} 的来源元数据与知识库不一致")
        return errors

    # ---------- 主流程 ----------
    def _refuse(self, reason: str, confidence: float = 0.0, basis: dict | None = None) -> AskResult:
        return AskResult(REFUSAL_TEXT[reason], [], round(confidence, 4),
                         basis or {"note": CONFIDENCE_NOTE, "score": round(confidence, 4)}, True, reason)

    def ask(self, question: str, top_k: int | None = None, *, polish: bool = True) -> AskResult:
        q = question.strip()
        if not q:
            return self._refuse("empty_question")
        top_k = max(1, min(top_k or self.cfg.top_k, 10))

        mentioned = _DEMO_ID_RE.findall(q.upper())
        if mentioned:
            missing = [m for m in mentioned if m not in self._sections]
            if missing:
                return self._refuse("unknown_section")
            fused = [h for h in (FusedHit(c, 1.0, {}) for c in self.index.chunks
                                 if c.section_id in set(mentioned))]
            basis = {"note": CONFIDENCE_NOTE, "score": 1.0, "mode": "explicit_section_id",
                     "threshold": self.cfg.refusal_threshold}
            return self._compose(fused, basis, 1.0, set(mentioned), set(), polish)

        if _CLAUSE_RE.search(q):
            return self._refuse("clause_fabrication_risk")

        fused, top_vec = self.retrieve(q)
        terms = question_terms(q)
        basis = self._confidence(terms, fused, top_vec)
        score = basis["score"]
        if not fused or score < self.cfg.refusal_threshold:
            return self._refuse("out_of_scope", score, basis)
        retrieved = {h.chunk.section_id for h in fused}
        return self._compose(self.rerank(terms, fused)[:top_k], basis, score, retrieved, terms, polish)

    def _compose(self, hits, basis, score, retrieved_sections, terms, polish: bool = True) -> AskResult:
        draft = self.composer(self.index, terms, hits, self.cfg.max_sentences)
        if not draft.segments:
            return self._refuse("no_supporting_sentence", score, basis)
        errors = self.validate(draft, retrieved_sections)
        if errors:
            res = self._refuse("citation_validation_failed", score, {**basis, "validation_errors": errors})
            return res
        body = "\n".join(f"• {s.text}【{s.section_id}】" for s in draft.segments)
        res = AskResult(
            f"{body}\n\n{DISCLAIMER}", draft.citations, round(score, 4), basis, False, None,
            [{"text": s.text, "section_id": s.section_id} for s in draft.segments],
        )
        if self.provider is not None and polish:
            self._polish(res, body)
        return res

    def _polish(self, res: AskResult, body: str) -> None:
        """只改措辞：引用、confidence、拒答判定均已在此之前由规则确定；模型输出须保留全部数字、编号和引用标记。"""
        rw = self.provider.rewrite(
            body, instruction="把下面的要点整理成通顺、简洁的中文回答，每条要点后的【章节编号】引用标记必须原样保留。",
            allowed_numbers=set(re.findall(r"\d+(?:\.\d+)?", body)))
        res.llm = {"provider": self.provider.name, "used": rw.used, "fallback_reason": rw.reason,
                   "redactions": rw.redactions}
        if rw.used:
            res.extractive_answer, res.answer_mode = res.answer, "llm_polished"
            res.answer = f"{rw.text}\n\n{DISCLAIMER}"
