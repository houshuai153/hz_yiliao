"""审计中间件与 AuditContext。

业务代码通过 request.state.audit 登记知识章节、触发规则、拒答状态、报告草稿 ID，
中间件在响应结束后统一落库（SQLite/PostgreSQL 表）并追加按天 JSONL。
"""
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.audit.models import AuditLog

DIGEST_MAX = 80
# 疑似敏感信息（长数字串、邮箱）打码
_SENSITIVE = re.compile(r"\d{6,}|[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass
class AuditContext:
    input_text: str = ""
    section_ids: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    refused: bool = False
    draft_id: str | None = None
    agent_task_id: str | None = None


def make_digest(text: str) -> str:
    return _SENSITIVE.sub("***", text)[:DIGEST_MAX]


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.audit = AuditContext(
            input_text=f"{request.method} {request.url.path}?{request.url.query}"
        )
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            self._record(request, request_id, status, int((time.perf_counter() - start) * 1000))

    def _record(self, request: Request, request_id: str, status: int, latency_ms: int) -> None:
        ctx: AuditContext = request.state.audit
        row = AuditLog(
            request_id=request_id,
            feature=request.url.path,
            operator=(getattr(request.state, "user", None) or request.headers.get("X-Operator", "anonymous"))[:64],
            input_digest=make_digest(ctx.input_text),
            input_sha256=hashlib.sha256(ctx.input_text.encode("utf-8")).hexdigest(),
            section_ids=ctx.section_ids,
            rule_ids=ctx.rule_ids,
            refused=ctx.refused,
            draft_id=ctx.draft_id,
            agent_task_id=ctx.agent_task_id,
            status_code=status,
            latency_ms=latency_ms,
        )
        factory = request.app.state.session_factory
        with factory() as session:
            session.add(row)
            session.commit()
        self._append_jsonl(request.app.state.settings.audit_jsonl_dir, row)

    @staticmethod
    def _append_jsonl(directory: str, row: AuditLog) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        record = {
            "request_id": row.request_id, "ts": now.isoformat(), "feature": row.feature,
            "operator": row.operator, "input_digest": row.input_digest,
            "section_ids": row.section_ids, "rule_ids": row.rule_ids,
            "refused": row.refused, "draft_id": row.draft_id, "agent_task_id": row.agent_task_id,
            "status_code": row.status_code, "latency_ms": row.latency_ms,
        }
        with open(path / f"audit-{now:%Y%m%d}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
