"""scripts/setup_env.py 生成的网页登录哈希必须能被后端校验；脚本不得覆盖已有 .env。"""
import importlib.util
import subprocess
import sys

from app.api.auth import parse_users, verify_password
from app.settings import ROOT

spec = importlib.util.spec_from_file_location("setup_env", ROOT / "scripts" / "setup_env.py")
setup_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_env)


def test_generated_hash_is_accepted_by_backend():
    h = setup_env.scrypt_hash("some-long-password")
    assert verify_password("some-long-password", h) and not verify_password("wrong", h)
    assert parse_users(f"reviewer:{h}") == {"reviewer": h}


def test_refuses_to_overwrite_existing_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("KEEP=1\n", encoding="utf-8")
    monkeypatch.setattr(setup_env, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["setup_env.py"])
    try:
        setup_env.main()
        raise AssertionError("应当退出")
    except SystemExit as e:
        assert "已存在" in str(e.code)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
