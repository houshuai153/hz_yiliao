"""离线向量化：字符 n-gram 哈希 TF-IDF + 截断 SVD（LSA）。

诚实说明：这是词法层面的“潜在语义”近似，不是神经网络语义嵌入；不下载任何权重。
`Embedder` 接口留给将来由用户显式提供本地模型路径后接入。
"""
import re
import zlib
from pathlib import Path
from typing import Protocol

import numpy as np

_NORM_RE = re.compile(r"[\s\W_]+")


class Embedder(Protocol):
    def fit(self, texts: list[str]) -> "Embedder": ...
    def encode(self, texts: list[str]) -> np.ndarray: ...  # 行归一化
    def save(self, path: Path) -> None: ...


class HashedLSA:
    name = "hashed-char-ngram-lsa"

    def __init__(self, n_features: int = 4096, ngrams: tuple[int, ...] = (2, 3), n_components: int = 48):
        self.n_features, self.ngrams, self.n_components = n_features, ngrams, n_components
        self.idf: np.ndarray | None = None
        self.components: np.ndarray | None = None

    def _tf(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.n_features), dtype=np.float32)
        for r, text in enumerate(texts):
            s = _NORM_RE.sub("", text.lower())
            for n in self.ngrams:
                for i in range(len(s) - n + 1):
                    m[r, zlib.crc32(s[i:i + n].encode("utf-8")) % self.n_features] += 1.0
        np.log1p(m, out=m)  # 次线性 tf
        return m

    @staticmethod
    def _l2(m: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return m / norm

    def fit(self, texts: list[str]) -> "HashedLSA":
        tf = self._tf(texts)
        n = len(texts)
        df = (tf > 0).sum(axis=0)
        self.idf = (np.log((1 + n) / (1 + df)) + 1).astype(np.float32)
        x = self._l2(tf * self.idf)
        k = max(1, min(self.n_components, n - 1))
        _, _, vt = np.linalg.svd(x, full_matrices=False)
        comps = vt[:k].copy()
        for row in comps:  # 固定符号，保证重复构建结果一致
            if row[np.argmax(np.abs(row))] < 0:
                row *= -1
        self.components = comps.astype(np.float32)
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        assert self.idf is not None and self.components is not None, "未 fit/load"
        x = self._l2(self._tf(texts) * self.idf)
        return self._l2(x @ self.components.T)

    def save(self, path: Path) -> None:
        np.savez(path, idf=self.idf, components=self.components,
                 params=np.array([self.n_features, self.n_components]), ngrams=np.array(self.ngrams))

    @classmethod
    def load(cls, path: Path) -> "HashedLSA":
        z = np.load(path)
        obj = cls(int(z["params"][0]), tuple(int(x) for x in z["ngrams"]), int(z["params"][1]))
        obj.idf, obj.components = z["idf"], z["components"]
        return obj


def top_k_cosine(query_vec: np.ndarray, doc_vecs: np.ndarray, top_k: int) -> list[tuple[int, float]]:
    """query_vec 与 doc_vecs 均已归一化；仅返回余弦 > 0 的结果。"""
    sims = doc_vecs @ query_vec
    order = sorted(range(len(sims)), key=lambda i: (-float(sims[i]), i))
    return [(i, float(sims[i])) for i in order[:top_k] if sims[i] > 0]
