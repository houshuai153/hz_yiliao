"""React 构建产物（web/dist）的安全检查：不含静态令牌 / 密钥，不使用可读存储，不引用外部域名。需先 `cd web && npm run build`；没有构建产物时跳过。"""
import re

import pytest

from app.settings import ROOT, Settings

DIST = ROOT / "web" / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="未构建前端（cd web && npm run build）")


def bundle_text() -> dict[str, str]:
    return {str(p.relative_to(DIST)): p.read_text(encoding="utf-8", errors="ignore")
            for p in DIST.rglob("*") if p.suffix in {".js", ".css", ".html", ".svg"}}


def test_bundle_contains_no_secrets():
    s = Settings()
    secrets_ = [v for v in (s.api_token, s.llm_api_key.get_secret_value()) if len(v) >= 8]
    blob = "\n".join(bundle_text().values())
    for secret in secrets_:
        assert secret not in blob
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", blob)
    assert "API_TOKEN" not in blob


def test_bundle_uses_no_readable_storage_for_credentials():
    blob = "\n".join(t for n, t in bundle_text().items() if n.endswith(".js") and n.startswith("assets/index"))
    assert "localStorage" not in blob and "sessionStorage" not in blob


def test_bundle_loads_nothing_from_other_origins():
    """离线 / 内网可用：字体随包自带，页面与样式不引用 CDN，脚本不向外部地址发请求。
    （库内部错误提示里的文档链接字符串不是请求，不在检查范围内。）"""
    for name, text in bundle_text().items():
        if name.endswith((".html", ".css", ".svg")):
            assert not re.search(r"(src|href)=[\"']https?://(?!www\.w3\.org)", text), name
            assert not re.search(r"(@import|url\()\s*[\"']?https?://", text), name
        if name.endswith(".js"):
            assert not re.search(r"(fetch|import|WebSocket|EventSource|sendBeacon)\(\s*[\"'`]https?://", text), name
    html = (DIST / "index.html").read_text(encoding="utf-8")
    assert "fonts.googleapis" not in html and "cdn" not in html.lower()
