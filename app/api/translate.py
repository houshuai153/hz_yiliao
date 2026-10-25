"""POST /api/translate、GET /api/translate/capabilities"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_token
from app.llm.base import get_provider
from app.translation.glossary import Glossary
from app.translation.service import TranslationError, TranslationService

router = APIRouter(prefix="/api/translate", dependencies=[Depends(require_token)])


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    direction: Literal["zh2en", "en2zh", "bilingual"]
    reference: str | None = Field(default=None, max_length=20000, description="现成译文；提供时只核对术语，不翻译")


class TranslateResponse(BaseModel):
    direction: str
    paragraphs: list[dict]
    document_warnings: list[dict]
    stats: dict
    bilingual_rows: list[dict] | None = None
    capability_note: str
    glossary_note: str
    request_id: str


def get_service(request: Request) -> TranslationService:
    state = request.app.state
    if getattr(state, "translation_service", None) is None:
        glossary = Glossary.load()
        state.translation_service = TranslationService(get_provider(state.settings, glossary), glossary)
    return state.translation_service


@router.get("/capabilities")
def capabilities(service: TranslationService = Depends(get_service)) -> dict:
    return service.capabilities()


@router.post("", response_model=TranslateResponse)
def translate(body: TranslateRequest, request: Request, service: TranslationService = Depends(get_service)):
    try:
        result = service.translate(body.text, body.direction, body.reference)
    except TranslationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    codes = {w["code"] for p in result["paragraphs"] for w in p["warnings"]}
    codes |= {w["code"] for w in result["document_warnings"]}
    patterns = {s["pattern_id"] for p in result["paragraphs"] for s in p["sentences"] if s["pattern_id"]}
    audit = request.state.audit
    audit.input_text = f"translate[{body.direction}]: {body.text}"
    audit.rule_ids = sorted(patterns) + sorted(f"WARN:{c}" for c in codes)
    audit.refused = False
    return TranslateResponse(**result, request_id=request.state.request_id)
