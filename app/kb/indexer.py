"""索引构建与加载。索引文件位于 index_dir：chunks.json、vectors.npz（模型+文档向量）、meta.json。

BM25 在加载时由 chunks.json 重建（语料很小），向量文件持久化。
"""
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KBSection
from app.kb.bm25 import BM25
from app.kb.parser import Chunk, SectionData, chunk_section
from app.kb.tokenize import tokenize
from app.kb.vector import HashedLSA, top_k_cosine


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class KBIndex:
    def __init__(self, chunks: list[Chunk], lsa: HashedLSA, doc_vecs: np.ndarray, meta: dict):
        self.chunks, self.lsa, self.doc_vecs, self.meta = chunks, lsa, doc_vecs, meta
        self.bm25 = BM25().fit([tokenize(c.text + " " + c.heading) for c in chunks])

    def search_bm25(self, query: str, top_k: int = 10) -> list[Hit]:
        return [Hit(self.chunks[i], s) for i, s in self.bm25.search(tokenize(query), top_k)]

    def search_vector(self, query: str, top_k: int = 10) -> list[Hit]:
        qv = self.lsa.encode([query])[0]
        return [Hit(self.chunks[i], s) for i, s in top_k_cosine(qv, self.doc_vecs, top_k)]


def _section_data(row: KBSection) -> SectionData:
    return SectionData(row.section_id, row.doc_id, row.version, row.heading, row.text,
                       row.position, row.source_type, row.is_demo)


def _index_text(c: Chunk) -> str:
    return f"{c.heading}\n{c.text}"


def build_index(session: Session, index_dir: str | Path) -> dict:
    rows = session.scalars(select(KBSection).order_by(KBSection.section_id)).all()
    if not rows:
        raise RuntimeError("kb_section 为空，请先导入知识文档")
    chunks = [c for r in rows for c in chunk_section(_section_data(r))]
    lsa = HashedLSA().fit([_index_text(c) for c in chunks])
    doc_vecs = lsa.encode([_index_text(c) for c in chunks])

    out = Path(index_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = [asdict(c) for c in chunks]
    (out / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    lsa.save(out / "lsa_model.npz")
    np.save(out / "doc_vectors.npy", doc_vecs)
    meta = {
        "embedder": HashedLSA.name,
        "n_sections": len(rows),
        "n_chunks": len(chunks),
        "n_features": lsa.n_features,
        "n_components": int(lsa.components.shape[0]),
        "corpus_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "note": "词法潜在语义近似，非神经嵌入；离线构建，未下载任何权重",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_index(index_dir: str | Path) -> KBIndex:
    d = Path(index_dir)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(f"索引不存在：{d}，请先运行 python scripts/init_data.py")
    chunks = [Chunk(**c) for c in json.loads((d / "chunks.json").read_text(encoding="utf-8"))]
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return KBIndex(chunks, HashedLSA.load(d / "lsa_model.npz"), np.load(d / "doc_vectors.npy"), meta)
