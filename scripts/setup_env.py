"""一键生成部署用 .env：随机密钥、外层 basic auth、网页登录账号，口令只在终端和凭据文件里出现一次。

只依赖 Python 标准库和 docker（用 caddy 镜像生成 basic auth 的 bcrypt 哈希），服务器不需要装其它 Python 包。

用法（在项目根目录）：
  python3 scripts/setup_env.py                       # 没有域名：DOMAIN=localhost（自签证书，仅内部试用）
  python3 scripts/setup_env.py --domain bio.example.com
已存在 .env 时拒绝覆盖（加 --force 才会覆盖，并先备份为 .env.bak）。
生成的口令写入 deploy-credentials.txt（权限 600，已在 .gitignore 中）：请抄到你的密码管理器后删除该文件。
"""
import argparse
import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)   # 必须与 app/api/auth.py 保持一致


def scrypt_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def caddy_hash(password: str) -> str:
    try:
        out = subprocess.run(["docker", "run", "--rm", "caddy:2-alpine", "caddy", "hash-password", "--plaintext", password],
                             capture_output=True, text=True, timeout=300, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"生成 basic auth 哈希失败（需要已安装并运行 docker，并能拉取 caddy:2-alpine 镜像）：{exc}")
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", default="localhost")
    ap.add_argument("--ui-user", default="reviewer", help="网页登录用户名")
    ap.add_argument("--basic-user", default="admin", help="外层 basic auth 用户名")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    env_path, cred_path = ROOT / ".env", ROOT / "deploy-credentials.txt"
    if env_path.exists():
        if not a.force:
            sys.exit(".env 已存在，未改动。确认要重新生成请加 --force（旧文件会备份为 .env.bak）。")
        shutil.copy(env_path, ROOT / ".env.bak")
    if any(c in a.ui_user + a.basic_user for c in ":;' "):
        sys.exit("用户名不能包含 : ; 引号或空格")

    pg, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    ui_pw, basic_pw = secrets.token_urlsafe(14), secrets.token_urlsafe(14)
    lines = [
        "# 由 scripts/setup_env.py 生成。不要提交。",
        "APP_ENV=production",
        f"POSTGRES_PASSWORD={pg}",
        f"API_TOKEN={token}",
        f"DOMAIN={a.domain}",
        f"BASIC_AUTH_USER={a.basic_user}",
        f"BASIC_AUTH_HASH='{caddy_hash(basic_pw)}'",
        f"UI_USERS='{a.ui_user}:{scrypt_hash(ui_pw)}'",
        "LLM_PROVIDER=deterministic",
        "APP_VERSION=prod",
    ]
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    scheme = "https" if a.domain != "localhost" else "https（自签证书，浏览器会提示不受信任）"
    text = (f"访问地址：{scheme}://{a.domain}\n\n第一步 浏览器弹出的口令框（外层 basic auth）\n  用户名：{a.basic_user}\n  口令：{basic_pw}\n\n"
            f"第二步 应用登录页\n  用户名：{a.ui_user}\n  口令：{ui_pw}\n")
    fd = os.open(cred_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f".env 已生成（权限 600）。\n\n{text}\n以上口令只显示这一次，已同时写入 {cred_path.name}；请抄好后删除该文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
