"""审计日志表。只存摘要与哈希，不存完整输入输出。"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    feature: Mapped[str] = mapped_column(String(100))
    operator: Mapped[str] = mapped_column(String(64))
    input_digest: Mapped[str] = mapped_column(String(120))
    input_sha256: Mapped[str] = mapped_column(String(64))
    section_ids: Mapped[list] = mapped_column(JSON, default=list)
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
