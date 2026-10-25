"""报告草稿与人工审核：生成 → 预览 / 修订 / 审核 → 审核通过后才能导出 DOCX。

审核类写操作必须带有效的 X-Operator（“操作人记录”，不是电子签名）。
"""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import operator_name, require_token
from app.llm.base import provider_of
from app.reports import review
from app.reports.builder import ReportError
from app.reports.service import generate_draft
from app.rules.engine import load_rule_sets

router = APIRouter(prefix="/api/reports", dependencies=[Depends(require_token)])
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DraftRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=32)
    run_ids: list[str] = Field(default_factory=list, max_length=20, description="为空表示该项目全部分析批")
    kind: Literal["validation", "sample_analysis"]


def operator_of(request: Request) -> str:
    """审核类操作必须有明确的操作人记录；匿名不允许。"""
    op = (operator_name(request) or request.headers.get("X-Operator", "")).strip()[:64]   # 会话用户名优先，客户端不能冒充
    if not op or op == "anonymous":
        raise HTTPException(400, detail={"error_code": "operator_required",
                                         "message": "审核类操作须在 X-Operator 中提供操作人（仅为操作人记录，不是电子签名）"})
    return op


def _fail(exc: review.ReviewError) -> HTTPException:
    return HTTPException(exc.status, detail={"error_code": exc.code, "message": exc.message, **exc.extra})


class DraftResponse(BaseModel):
    draft_id: str
    kind: str
    project_id: str
    run_ids: list[str]
    review_status: str
    banner: str
    export_requires_review: bool = True
    missing_fields: list[str]
    rule_ids: list[str]
    fact_count: int
    sentence_count: int
    fact_sentences: int
    unsourced_sentences: int
    request_id: str


@router.post("/draft", response_model=DraftResponse)
def create_draft(body: DraftRequest, request: Request):
    state = request.app.state
    if getattr(state, "rule_sets", None) is None:
        state.rule_sets = load_rule_sets()
    audit = request.state.audit
    audit.input_text = f"report[{body.kind}] project={body.project_id} runs={body.run_ids}"
    try:
        with state.session_factory() as session:
            res = generate_draft(session, state.settings.reports_dir, body.project_id, body.run_ids,
                                 body.kind, state.rule_sets,
                                 (operator_name(request) or request.headers.get("X-Operator", "anonymous"))[:64])
    except ReportError as exc:
        raise HTTPException(status_code=exc.status, detail={"error_code": exc.code, "message": exc.message}) from exc
    audit.draft_id = res["draft_id"]
    audit.rule_ids = res["rule_ids"] + [f"REPORT:{body.kind}"]
    return DraftResponse(**{k: v for k, v in res.items() if k != "path"}, request_id=request.state.request_id)


class TransitionRequest(BaseModel):
    action: Literal["start_review", "request_changes", "resubmit", "approve"]
    comment: str = Field(default="", max_length=2000)
    confirmations: list[str] = Field(default_factory=list, max_length=10)
    missing_ack: dict[str, str] = Field(default_factory=dict)


class EditRequest(BaseModel):
    base_version: int
    text: str = Field(max_length=review.MAX_TEXT + 1)
    fact_keys: list[str] | None = None
    note: str = Field(default="", max_length=500)


class AddRequest(BaseModel):
    base_version: int
    after_block: str = Field(max_length=8)
    kind: Literal["static", "fact"]
    text: str = Field(max_length=review.MAX_TEXT + 1)
    fact_keys: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=500)


class CommentRequest(BaseModel):
    kind: Literal["comment", "issue"]
    text: str = Field(max_length=2000)
    block_id: str | None = Field(default=None, max_length=8)


class ResolveRequest(BaseModel):
    resolution: str = Field(default="", max_length=2000)


def _run(request: Request, draft_id: str, label: str, fn):
    """统一执行审核操作：登记审计上下文、转换业务错误。"""
    audit = request.state.audit
    audit.draft_id, audit.input_text = draft_id, f"review {label} {draft_id}"
    try:
        with request.app.state.session_factory() as session:
            return fn(session)
    except review.ReviewError as exc:
        raise _fail(exc) from exc


@router.get("")
def list_reports(request: Request, project_id: str | None = None, status: str | None = None):
    if status is not None and status not in review.STATUSES:
        raise HTTPException(422, detail={"error_code": "invalid_status", "message": f"status 必须是 {list(review.STATUSES)}"})
    with request.app.state.session_factory() as session:
        return review.list_drafts(session, project_id, status)


@router.get("/{draft_id}")
def get_report(draft_id: str, request: Request, version: int | None = None):
    return _run(request, draft_id, "view", lambda s: review.detail(s, draft_id, version))


@router.get("/{draft_id}/versions")
def get_versions(draft_id: str, request: Request):
    return _run(request, draft_id, "versions", lambda s: review.versions(s, draft_id))


@router.get("/{draft_id}/events")
def get_events(draft_id: str, request: Request):
    return _run(request, draft_id, "events", lambda s: review.events(s, draft_id))


@router.post("/{draft_id}/transition")
def post_transition(draft_id: str, body: TransitionRequest, request: Request):
    op = operator_of(request)
    return _run(request, draft_id, body.action, lambda s: review.transition(
        s, draft_id, body.action, op, body.comment, body.confirmations, body.missing_ack))


@router.post("/{draft_id}/blocks/{block_id}/edit")
def post_edit(draft_id: str, block_id: str, body: EditRequest, request: Request):
    op = operator_of(request)
    return _run(request, draft_id, f"edit {block_id}", lambda s: review.edit_block(
        s, draft_id, op, body.base_version, block_id, body.text, body.fact_keys, body.note))


@router.post("/{draft_id}/blocks/{block_id}/suggest")
def post_suggest(draft_id: str, block_id: str, request: Request):
    """大模型润色建议（只读，不改状态）。采纳须通过 edit 接口，并重新经过来源 / 数字校验。"""
    operator_of(request)
    res = _run(request, draft_id, f"suggest {block_id}", lambda s: review.suggest_rewrite(
        s, draft_id, block_id, provider_of(request.app.state)))
    request.state.audit.rule_ids = [f"LLM:{res['provider']}:{'suggested' if res['used'] else 'fallback'}"] + [
        f"REDACT:{k}={v}" for k, v in res["redactions"].items()]
    return res


@router.post("/{draft_id}/blocks")
def post_add(draft_id: str, body: AddRequest, request: Request):
    op = operator_of(request)
    return _run(request, draft_id, "add", lambda s: review.add_block(
        s, draft_id, op, body.base_version, body.after_block, body.kind, body.text, body.fact_keys, body.note))


@router.post("/{draft_id}/comments")
def post_comment(draft_id: str, body: CommentRequest, request: Request):
    op = operator_of(request)
    return _run(request, draft_id, "comment", lambda s: review.add_comment(
        s, draft_id, op, body.kind, body.text, body.block_id))


@router.post("/{draft_id}/comments/{comment_id}/resolve")
def post_resolve(draft_id: str, comment_id: int, body: ResolveRequest, request: Request):
    op = operator_of(request)
    return _run(request, draft_id, "resolve", lambda s: review.resolve_comment(s, draft_id, comment_id, op, body.resolution))


@router.get("/{draft_id}/download")
def download(draft_id: str, request: Request):
    """只有“审核通过”的草稿才能导出；导出的是最终审核版本重新渲染的 DOCX，不是生成时的初稿文件。"""
    op = (operator_name(request) or request.headers.get("X-Operator", "")).strip()[:64]      # 状态检查在前：未通过审核的草稿无论有无操作人都拒绝
    path = _run(request, draft_id, "export", lambda s: review.export_docx(
        s, draft_id, op, Path(request.app.state.settings.reports_dir) / "exported"))
    return FileResponse(path, media_type=DOCX_MIME, filename=path.name)
