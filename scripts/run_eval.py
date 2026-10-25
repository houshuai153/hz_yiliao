"""运行固定评测集并写出 JSON 结果（离线）。

用法：
  python scripts/run_eval.py                       # 校验锁 -> 运行 -> eval/results/latest.json；任一 gate 指标不过则退出码 1
  python scripts/run_eval.py --suite regulatory    # 只跑某个套件
  python scripts/run_eval.py --update-lock --reason "说明为什么要改评测集/阈值"   # 显式更新锁（会记入历史）
退出码：0 全部通过；1 有 gate 指标未通过；2 评测集 / 阈值与锁定版本不一致或配置错误。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation import runner  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", action="append", choices=["regulatory", "translation", "report"])
    ap.add_argument("--out", type=Path, default=runner.DEFAULT_OUT)
    ap.add_argument("--update-lock", action="store_true")
    ap.add_argument("--reason", default="")
    args = ap.parse_args()
    cfg = runner.load_eval_config()

    if args.update_lock:
        try:
            runner.update_lock(cfg, args.reason)
        except runner.EvalError as exc:
            print(f"错误：{exc}")
            return 2
        print("已更新 eval/cases.lock.json，并记入历史。")
        return 0

    problems = runner.check_lock(cfg)
    if problems:
        print("评测集 / 阈值与锁定版本不一致，拒绝运行：")
        for p in problems:
            print("  -", p)
        print("若确属有意修改，请使用 --update-lock --reason \"原因\"（会留下记录）；不得为通过验收而修改答案或阈值。")
        return 2
    try:
        res = runner.run_all(args.out, args.suite)
    except runner.EvalError as exc:
        print(f"错误：{exc}")
        return 2

    for name, suite in res["suites"].items():
        print(f"\n== {name}（{suite['n_cases']} 题）{'通过' if suite['passed'] else '未通过'} ==")
        for m in suite["metrics"]:
            val = "n/a" if m["value"] is None else f"{m['value']:.3f}"
            frac = f"{m['numerator']}/{m['denominator']}" if m["denominator"] is not None else f"{m['numerator']}"
            flag = "PASS" if m["passed"] else "FAIL"
            gate = "" if m["gate"] else "（信息性，不参与通过判定）"
            print(f"  [{flag}] {m['name']:<28} {val:>6} ({frac:>5})  要求 {m['op']} {m['threshold']}{gate}")
        for c in suite["cases"]:
            if not c.get("passed", True):
                print(f"    未通过的题：{c['id']}")
    print(f"\n结果已写入 {args.out}")
    print("总体：" + ("通过" if res["overall_passed"] else "未通过"))
    return 0 if res["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
