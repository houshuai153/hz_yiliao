"""跨平台统一验收入口：依次运行 测试 -> 评测。任一步失败则以非零退出码结束。

用法：
  python scripts/verify.py                 # 先 pytest，再评测（两步都会运行，最后汇总）
  python scripts/verify.py --fail-fast     # 某步失败立即停止
  python scripts/verify.py --skip-tests    # 只跑评测
  python scripts/verify.py --skip-eval     # 只跑测试
  python scripts/verify.py --skip-web      # 不跑前端检查
退出码：0 全部通过；非 0 表示至少一步失败（评测集 / 阈值被改动时为 2，见 scripts/run_eval.py）。
全程离线：测试由 pytest-socket 禁止外网，评测由 app.evaluation.netguard 禁止外网。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_steps(steps: list[tuple[str, list[str]]], fail_fast: bool = False, cwd: Path = ROOT) -> int:
    """依次运行 (名称, 命令)；返回整体退出码（0=全部通过，否则为第一个失败步骤的退出码）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    summary: list[tuple[str, int, float]] = []
    for name, cmd in steps:
        print(f"\n{'=' * 8} {name} {'=' * 8}\n$ {' '.join(cmd)}", flush=True)
        t0 = time.time()
        code = subprocess.call(cmd, cwd=cwd, env=env)
        summary.append((name, code, time.time() - t0))
        if code != 0 and fail_fast:
            break
    print(f"\n{'=' * 8} 汇总 {'=' * 8}")
    for name, code, secs in summary:
        print(f"  [{'PASS' if code == 0 else 'FAIL'}] {name}（退出码 {code}，{secs:.1f}s）")
    skipped = [n for n, _ in steps if n not in {s[0] for s in summary}]
    for n in skipped:
        print(f"  [SKIP] {n}（因 --fail-fast 未运行）")
    failed = [c for _, c, _ in summary if c != 0]
    print("验收结果：" + ("通过" if not failed and not skipped else "未通过"))
    return 0 if not failed and not skipped else (failed[0] if failed else 1)


def web_steps() -> list[tuple[str, list[str]]]:
    """React 前端：类型检查、单元 / 组件测试、生产构建。缺少 Node 或依赖时明确说明“没有运行”，不算通过。"""
    web = ROOT / "web"
    npm = shutil.which("npm")
    if not (web / "package.json").exists():
        return []
    if npm is None or not (web / "node_modules").exists():
        print("【提示】未运行前端检查：需要 Node 22+ 与 `cd web && npm ci`。这不代表前端检查通过。", flush=True)
        return []
    return [("前端类型检查（tsc）", [npm, "--prefix", str(web), "run", "typecheck"]),
            ("前端测试（vitest）", [npm, "--prefix", str(web), "test"]),
            ("前端构建（vite build）", [npm, "--prefix", str(web), "run", "build"])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-eval", action="store_true")
    ap.add_argument("--skip-web", action="store_true")
    a = ap.parse_args()
    steps = []
    if not a.skip_tests:
        steps.append(("测试（pytest）", [sys.executable, "-m", "pytest", "-q"]))
    if not a.skip_eval:
        steps.append(("评测（run_eval）", [sys.executable, str(ROOT / "scripts" / "run_eval.py")]))
    if not a.skip_web:
        steps += web_steps()
    if not steps:
        print("没有要运行的步骤")
        return 2
    return run_steps(steps, a.fail_fast)


if __name__ == "__main__":
    raise SystemExit(main())
