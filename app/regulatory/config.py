from dataclasses import dataclass
from pathlib import Path

import yaml

from app.settings import ROOT

DEFAULT_EVAL_CONFIG = ROOT / "config" / "eval.yaml"


@dataclass(frozen=True)
class RegulatoryConfig:
    rrf_k: int
    retrieve_k: int
    top_k: int
    max_sentences: int
    w_coverage: float
    w_vector: float
    w_rrf: float
    refusal_threshold: float
    rerank_enabled: bool = True
    rerank_weight: float = 0.5   # 词法重排分在最终排序分中的占比
    sentence_floor: float = 0.5  # 句子相关度须不低于最佳句的该比例
    chunk_floor: float = 0.0     # 块的最终分须不低于第一名的该比例（0=不过滤）


def load_regulatory_config(path: Path | None = None) -> RegulatoryConfig:
    raw = yaml.safe_load(Path(path or DEFAULT_EVAL_CONFIG).read_text(encoding="utf-8"))["regulatory"]
    w = raw["weights"]
    return RegulatoryConfig(
        rrf_k=raw["rrf_k"], retrieve_k=raw["retrieve_k"], top_k=raw["top_k"],
        max_sentences=raw["max_sentences"], w_coverage=w["coverage"], w_vector=w["vector"],
        w_rrf=w["rrf"], refusal_threshold=raw["refusal_threshold"],
        rerank_enabled=raw.get("rerank", {}).get("enabled", True),
        rerank_weight=raw.get("rerank", {}).get("weight", 0.5),
        sentence_floor=raw.get("rerank", {}).get("sentence_floor", 0.5),
        chunk_floor=raw.get("rerank", {}).get("chunk_floor", 0.0),
    )
