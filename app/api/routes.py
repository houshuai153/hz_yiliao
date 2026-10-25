from fastapi import APIRouter, Depends, Request

from app.api.auth import require_token

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict:
    s = request.app.state.settings
    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_provider": s.llm_provider,
        "llm_enabled": s.llm_provider != "deterministic",
        "llm_model": s.llm_model if s.llm_provider == "deepseek" else None,
        "offline": s.llm_provider == "deterministic",
        "simulated_data": True,
    }


@router.get("/audit", dependencies=[Depends(require_token)])
def query_audit(request: Request, request_id: str) -> list[dict]:
    from sqlalchemy import select

    from app.audit.models import AuditLog

    with request.app.state.session_factory() as session:
        rows = session.scalars(select(AuditLog).where(AuditLog.request_id == request_id)).all()
        return [
            {
                "request_id": r.request_id, "ts": r.ts.isoformat(), "feature": r.feature,
                "operator": r.operator, "input_digest": r.input_digest,
                "section_ids": r.section_ids, "rule_ids": r.rule_ids, "refused": r.refused,
                "draft_id": r.draft_id, "status_code": r.status_code,
            }
            for r in rows
        ]
