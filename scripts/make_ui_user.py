"""生成浏览器登录用户（写进 .env 的 UI_USERS）。

用法：
  python scripts/make_ui_user.py alice            # 随机生成口令，打印一次
  python scripts/make_ui_user.py alice --password '...'   # 指定口令（注意 shell 历史）
输出一行 `name:scrypt$...`；把它加到 .env 的 UI_USERS（多个用户用分号隔开，整体用单引号包住）。口令只显示这一次。
"""
import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.auth import hash_password  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--password")
    args = ap.parse_args()
    if ":" in args.username or ";" in args.username or not args.username.strip():
        print("用户名不能为空，且不能包含 : 或 ;", file=sys.stderr)
        return 2
    password = args.password or secrets.token_urlsafe(12)
    if len(password) < 10:
        print("口令至少 10 个字符", file=sys.stderr)
        return 2
    print(f"{args.username}:{hash_password(password)}")
    if not args.password:
        print(f"口令（仅显示一次）：{password}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
