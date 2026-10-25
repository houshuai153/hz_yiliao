"""在调参集上度量问答的检索/作答质量（开发用，不属于阶段 6 评测）。

指标（仅对 kind=answerable 且未拒答的题）：
  hit        引用中至少有一个期望章节的题占比
  precision  所有引用里属于期望章节的比例（越高越少混入无关句）
  avg_cites  平均引用条数
并报告拒答类题（out_of_scope / fabricate）是否都被拒答。
用法：python scripts/dev_report.py [--no-rerank]
"""
import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app.kb.indexer import load_index  # noqa: E402
from app.regulatory.config import load_regulatory_config  # noqa: E402
from app.regulatory.service import RegulatoryAssistant  # noqa: E402
from app.settings import ROOT, get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    cfg = load_regulatory_config()
    if args.no_rerank and hasattr(cfg, "rerank_enabled"):
        cfg = dataclasses.replace(cfg, rerank_enabled=False)
    a = RegulatoryAssistant(load_index(get_settings().index_dir), cfg)
    cases = json.loads((ROOT / "eval/dev/regulatory_dev.json").read_text(encoding="utf-8"))["cases"]
    hits = n_ans = cited = cited_ok = 0
    refused_ok = refused_n = answered_n = 0
    for c in cases:
        r = a.ask(c["q"])
        if c["kind"] == "answerable":
            answered_n += 0 if r.refused else 1
            if r.refused:
                print("  ! 可答题被拒答:", c["id"], c["q"])
                continue
            n_ans += 1
            ids = [x["section_id"] for x in r.citations]
            exp = set(c["expect_sections"])
            hits += bool(exp & set(ids))
            cited += len(ids)
            cited_ok += sum(i in exp for i in ids)
            if args.v:
                print(f"  {c['id']:7} {c['q']}  -> {[i.replace('DEMO-', '') for i in ids]}")
        else:
            refused_n += 1
            refused_ok += r.refused
    print(f"rerank={'on' if getattr(cfg, 'rerank_enabled', False) else 'off'} "
          f"answerable={n_ans} hit={hits}/{n_ans} precision={cited_ok}/{cited}={cited_ok / max(cited, 1):.3f} "
          f"avg_cites={cited / max(n_ans, 1):.2f} refusal_correct={refused_ok}/{refused_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
