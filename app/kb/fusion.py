"""RRF（Reciprocal Rank Fusion）融合多路召回结果。"""
from dataclasses import dataclass, field

from app.kb.indexer import Hit
from app.kb.parser import Chunk


@dataclass(frozen=True)
class FusedHit:
    chunk: Chunk
    rrf_score: float
    ranks: dict = field(default_factory=dict)  # {"bm25": 名次, "vector": 名次}，缺席的路不出现
    final_score: float | None = None            # 重排后的最终分（未重排为 None）


def rrf_fuse(rankings: dict[str, list[Hit]], k: int = 60) -> list[FusedHit]:
    """score(d) = Σ 1/(k + rank_i(d))，名次从 1 起。同分按 chunk_id 升序，保证确定性。"""
    scores: dict[str, float] = {}
    ranks: dict[str, dict] = {}
    chunks: dict[str, Chunk] = {}
    for name, hits in rankings.items():
        for rank, h in enumerate(hits, start=1):
            cid = h.chunk.chunk_id
            chunks[cid] = h.chunk
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(cid, {})[name] = rank
    order = sorted(scores, key=lambda c: (-scores[c], c))
    return [FusedHit(chunks[c], scores[c], ranks[c]) for c in order]
