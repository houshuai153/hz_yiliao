"""POST /api/regulatory/ask"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_token
from app.kb.indexer import load_index
from app.llm.base import provider_of
from app.regulatory.config import load_regulatory_config
from app.regulatory.service import RegulatoryAssistant

router = APIRouter(prefix="/api/regulatory", dependencies=[Depends(require_token)])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=10)


class Citation(BaseModel):
    doc_id: str
    version: str
    section_id: str
    title: str
    source_type: str
    is_demo: bool
    label: str
    quote: str


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    confidence_basis: dict
    refused: bool
    refusal_reason: str | None = None
    answer_mode: str = "extractive"
    extractive_answer: str | None = None
    llm: dict = {}
    request_id: str


def get_assistant(request: Request) -> RegulatoryAssistant:
    state = request.app.state
    if getattr(state, "assistant", None) is None:
        try:
            index = load_index(state.settings.index_dir)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        state.assistant = RegulatoryAssistant(index, load_regulatory_config(), provider=provider_of(state))
    return state.assistant


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, assistant: RegulatoryAssistant = Depends(get_assistant)):
    result = assistant.ask(body.question, body.top_k)
    audit = request.state.audit
    audit.input_text = f"ask: {body.question}"
    audit.section_ids = result.section_ids
    audit.refused = result.refused
    audit.rule_ids = [f"REFUSE:{result.refusal_reason}"] if result.refused else []
    if result.llm:                                   # 审计只记录“是否使用了模型”和脱敏命中，不记录提示词 / 输出 / 密钥
        audit.rule_ids += [f"LLM:{result.llm['provider']}:{'polished' if result.llm['used'] else 'fallback'}"]
        audit.rule_ids += [f"REDACT:{k}={v}" for k, v in result.llm["redactions"].items()]
    return AskResponse(
        answer=result.answer, citations=result.citations, confidence=result.confidence,
        confidence_basis=result.confidence_basis, refused=result.refused,
        refusal_reason=result.refusal_reason, answer_mode=result.answer_mode,
        extractive_answer=result.extractive_answer, llm=result.llm, request_id=request.state.request_id,
    )
