"""BM25（Okapi），纯 Python 实现，本地离线。"""
import math
from collections import Counter


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._tf: list[Counter] = []
        self._len: list[int] = []
        self._idf: dict[str, float] = {}
        self._avg = 0.0

    def fit(self, docs: list[list[str]]) -> "BM25":
        self._tf = [Counter(d) for d in docs]
        self._len = [len(d) for d in docs]
        n = len(docs)
        self._avg = sum(self._len) / n if n else 0.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        return self

    def idf(self, term: str) -> float:
        """词的 IDF；语料中没出现过的词为 0（对排序无贡献）。"""
        return self._idf.get(term, 0.0)

    def search(self, query_tokens: list[str], top_k: int = 10) -> list[tuple[int, float]]:
        """返回 [(文档下标, 分数)]，仅含分数 > 0 的文档，按分数降序、下标升序。"""
        q = Counter(t for t in query_tokens if t in self._idf)
        scored = []
        for i, tf in enumerate(self._tf):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * self._len[i] / self._avg) if self._avg else self.k1
            for t in q:
                f = tf.get(t)
                if f:
                    s += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            if s > 0:
                scored.append((i, s))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]
