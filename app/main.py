"""FastAPI 入口。create_app 便于测试注入配置。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.dashboard import router as dashboard_router
from app.api.regulatory import router as regulatory_router
from app.api.reports import router as reports_router
from app.api.routes import router
from app.api.translate import router as translate_router
from app.audit.middleware import AuditMiddleware
from app.db import models  # noqa: F401  注册业务表
from app.db.base import Base, add_missing_columns, make_engine, make_session_factory
from app.reports.review import normalize_legacy
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="生物分析业务 AI 效能平台（原型，模拟数据）", version="0.1.0")
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    with app.state.session_factory() as session:
        normalize_legacy(session)
    app.add_middleware(AuditMiddleware)
    app.include_router(router)
    app.include_router(regulatory_router)
    app.include_router(translate_router)
    app.include_router(reports_router)
    app.include_router(dashboard_router)
    app.include_router(auth_router)
    _mount_web(app, Path(settings.web_dist_dir))
    return app


# 前端构建产物的 CSP：只允许同源脚本 / 样式 / 字体 / 接口，不引用任何外部域名（离线可用）
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
WEB_HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _mount_web(app: FastAPI, dist: Path) -> None:
    """若存在前端构建产物（web/dist），由同一进程同源托管：/assets 为带哈希的静态文件，其余前端路由回落到 index.html。"""
    index = dist / "index.html"
    if not index.is_file():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path in ("docs", "openapi.json", "redoc"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:     # 例如 favicon.svg；不允许越出 dist
            return FileResponse(target, headers=WEB_HEADERS)
        return FileResponse(index, headers={**WEB_HEADERS, "Cache-Control": "no-cache"})


app = create_app()
