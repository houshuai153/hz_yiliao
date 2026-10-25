"""报告人工审核流程：状态机、受控修订、审核意见、通过前检查、导出。

边界（与 CLAUDE.md 一致）：
- 只能修订叙述句（static / fact）的文字；表格、占位句、事实表、QC/ISR 判定与规则结果不可在此改写；
- 修订后的句子沿用同一套自检（app.reports.checks.check_sentence）；无法对应来源 = 标记 flag，阻止审核通过；
- 判定（pass/fail/pending_review）仍只来自 app.rules，此处不重算；
- operator 来自请求头 X-Operator，是“操作人记录”，不是受监管的电子签名；审核通过也不等于正式 QA 批准。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ReportComment, ReportDraft, ReportReviewEvent, ReportVersion
from app.reports.checks import ReportIntegrityError, check_report, check_sentence
from app.reports.docx_render import BANNER, render_docx
from app.reports.model import ReportDoc, Sentence, TableBlock, doc_from_content, doc_to_content

STATUSES = ("draft", "in_review", "changes_requested", "reviewed")
STATUS_ZH = {"draft": "待审核", "in_review": "审核中", "changes_requested": "退回修改", "reviewed": "审核通过"}
# action -> (允许的起始状态, 目标状态)
TRANSITIONS = {
    "start_review": ("draft", "in_review"),
    "request_changes": ("in_review", "changes_requested"),
    "resubmit": ("changes_requested", "in_review"),
    "approve": ("in_review", "reviewed"),
}
EDITABLE = {"draft", "in_review", "changes_requested"}
LOCKED_SECTIONS = ("数据溯源附录", "签名")      # 不允许在这些章节新增叙述
# 审核通过时必须逐项确认（服务端校验，页面只负责展示）
REQUIRED_CONFIRMATIONS = {
    "facts_checked": "已逐项核对业务结论及其数据来源标签",
    "verdicts_untouched": "确认 QC / ISR / 批接受判定来自规则引擎，审核中未被改写",
    "not_formal_approval": "知悉：本次人工审核通过不等于正式 QA 批准，正式报告须按质量体系签署",
}
_PLACEHOLDER_RE = re.compile(r"\[待补充: [^\]]*\]")
MAX_TEXT = 2000


class ReviewError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# ---------- 读取 ----------
def _row(session: Session, draft_id: str) -> ReportDraft:
    row = session.get(ReportDraft, draft_id)
    if row is None:
        raise ReviewError("draft_not_found", f"草稿不存在：{draft_id}", 404)
    return row


def _structured(row: ReportDraft) -> ReportDraft:
    if row.current_version is None:
        raise ReviewError("legacy_draft", "该草稿生成于审核功能上线之前，没有保存可审核的结构化内容，请重新生成草稿。")
    return row


def _load(session: Session, row: ReportDraft, version: int | None = None) -> tuple[ReportDoc, ReportVersion]:
    v = session.get(ReportVersion, (row.id, version or row.current_version))
    if v is None:
        raise ReviewError("version_not_found", f"版本不存在：{version}", 404)
    return doc_from_content(v.content_json, row.facts_json), v


def _event(session: Session, row: ReportDraft, operator: str, action: str, *, from_status=None, to_status=None,
           comment: str = "", detail: dict | None = None) -> ReportReviewEvent:
    ev = ReportReviewEvent(draft_id=row.id, operator=operator, action=action, from_status=from_status,
                           to_status=to_status, version=row.current_version or 0, comment=comment,
                           detail=detail or {})
    session.add(ev)
    return ev


def _event_dict(e: ReportReviewEvent) -> dict:
    return {"id": e.id, "ts": iso(e.ts), "operator": e.operator, "action": e.action, "from_status": e.from_status,
            "to_status": e.to_status, "version": e.version, "comment": e.comment, "detail": e.detail}


def _comment_dict(c: ReportComment) -> dict:
    return {"id": c.id, "block_id": c.block_id, "kind": c.kind, "text": c.text, "status": c.status,
            "version": c.version, "created_by": c.created_by, "created_at": iso(c.created_at),
            "resolved_by": c.resolved_by, "resolved_at": iso(c.resolved_at), "resolution": c.resolution}


def _comments(session: Session, draft_id: str) -> list[ReportComment]:
    return list(session.scalars(select(ReportComment).where(ReportComment.draft_id == draft_id)
                                .order_by(ReportComment.id)).all())


def _summary(row: ReportDraft) -> dict:
    return {"draft_id": row.id, "project_id": row.project_id, "kind": row.kind, "run_ids": row.run_ids,
            "review_status": row.review_status, "review_status_zh": STATUS_ZH[row.review_status],
            "current_version": row.current_version, "legacy": row.current_version is None,
            "created_at": iso(row.created_at), "missing_fields": row.missing_fields}


def list_drafts(session: Session, project_id: str | None = None, status: str | None = None) -> list[dict]:
    q = select(ReportDraft).order_by(ReportDraft.created_at.desc(), ReportDraft.id.desc())
    if project_id:
        q = q.where(ReportDraft.project_id == project_id)
    if status:
        q = q.where(ReportDraft.review_status == status)
    out = []
    for r in session.scalars(q).all():
        open_issues = sum(c.kind == "issue" and c.status == "open" for c in _comments(session, r.id))
        out.append({**_summary(r), "open_issues": open_issues})
    return out


def _block_dict(b, doc: ReportDoc, comments: list[ReportComment]) -> dict:
    mine = [c for c in comments if c.block_id == b.bid]
    base = {"bid": b.bid, "open_issues": sum(c.kind == "issue" and c.status == "open" for c in mine),
            "comment_count": len(mine)}
    if isinstance(b, TableBlock):
        return {**base, "type": "table", "caption": b.caption, "source": b.source, "header": b.header,
                "rows": b.rows, "editable": False}
    return {**base, "type": "sentence", "kind": b.kind, "text": b.text, "fact_keys": b.fact_keys,
            "tags": [doc.facts.get(k).tag for k in b.fact_keys if k in doc.facts], "missing": b.missing,
            "edited": b.edited, "added": b.edited and b.original_text is None, "original_text": b.original_text,
            "flag": b.flag, "editable": b.kind in ("static", "fact")}


def readiness(session: Session, row: ReportDraft, doc: ReportDoc, original: ReportDoc) -> dict:
    """审核通过前的检查。blockers 非空 = 不能通过；missing_to_acknowledge 须在通过时逐项确认。"""
    blockers: list[dict] = []
    comments = _comments(session, row.id)
    open_issues = [c for c in comments if c.kind == "issue" and c.status == "open"]
    if open_issues:
        blockers.append({"code": "open_issues", "message": f"仍有 {len(open_issues)} 条未解决问题",
                         "ids": [c.id for c in open_issues]})
    flagged = [b for b in doc.sentences() if b.flag]
    if flagged:
        blockers.append({"code": "unsourced_edit", "message": f"{len(flagged)} 处修订后的结论无法对应数据来源",
                         "block_ids": [b.bid for b in flagged]})
    try:
        check_report(doc)
    except ReportIntegrityError as exc:
        blockers.append({"code": "integrity", "message": "报告自检未通过：" + "；".join(exc.problems[:5]),
                         "problems": exc.problems})
    drift = _structure_drift(doc, original)
    if drift:
        blockers.append({"code": "structure_changed", "message": "不可修订的内容与生成版本不一致：" + "；".join(drift)})
    return {"blockers": blockers, "missing_to_acknowledge": list(row.missing_fields),
            "required_confirmations": REQUIRED_CONFIRMATIONS, "open_issue_count": len(open_issues),
            "can_approve": row.review_status == "in_review" and not blockers}


def _structure_drift(doc: ReportDoc, original: ReportDoc) -> list[str]:
    """表格、占位句、事实键必须与 v1 逐项一致（防止绕过受控接口改写）。"""
    def frozen(d: ReportDoc) -> dict:
        out = {}
        for s in d.sections:
            for b in s.blocks:
                if isinstance(b, TableBlock):
                    out[b.bid] = ("table", b.caption, tuple(b.header), tuple(tuple(r) for r in b.rows))
                elif b.kind == "placeholder":
                    out[b.bid] = ("placeholder", b.text)
        return out
    a, b = frozen(original), frozen(doc)
    return [f"{bid} 被改动或缺失" for bid in a if a[bid] != b.get(bid)]


def detail(session: Session, draft_id: str, version: int | None = None) -> dict:
    row = _row(session, draft_id)
    out = {**_summary(row), "banner": BANNER}
    if row.current_version is None:
        out["message"] = "该草稿生成于审核功能上线之前，没有保存可审核的结构化内容，请重新生成草稿。"
        return out
    doc, v = _load(session, row, version)
    original, _ = _load(session, row, 1)
    comments = _comments(session, draft_id)
    current, _ = _load(session, row)
    out.update({
        "title": doc.title, "viewing_version": v.version, "is_current": v.version == row.current_version,
        "rule_ids": doc.rule_ids,
        "sections": [{"title": s.title, "blocks": [_block_dict(b, doc, comments) for b in s.blocks]}
                     for s in doc.sections],
        "facts": [f.to_dict() for f in doc.facts.all()],
        "stats": {"fact_count": len(doc.facts.all()),
                  "fact_sentences": sum(b.kind == "fact" for b in doc.sentences()),
                  "edited_blocks": sum(b.edited for b in doc.sentences()),
                  "flagged_blocks": sum(bool(b.flag) for b in doc.sentences()),
                  "missing_fields": len(doc.missing_fields)},
        "comments": [_comment_dict(c) for c in comments],
        "readiness": readiness(session, row, current, original),
        "export_ready": row.review_status == "reviewed",
    })
    return out


def versions(session: Session, draft_id: str) -> list[dict]:
    row = _structured(_row(session, draft_id))
    rows = session.scalars(select(ReportVersion).where(ReportVersion.draft_id == row.id)
                           .order_by(ReportVersion.version)).all()
    return [{"version": v.version, "created_at": iso(v.created_at), "operator": v.operator,
             "change_type": v.change_type, "note": v.note, "is_current": v.version == row.current_version}
            for v in rows]


def events(session: Session, draft_id: str) -> list[dict]:
    _row(session, draft_id)
    rows = session.scalars(select(ReportReviewEvent).where(ReportReviewEvent.draft_id == draft_id)
                           .order_by(ReportReviewEvent.id)).all()
    return [_event_dict(e) for e in rows]


# ---------- 状态转换 ----------
def transition(session: Session, draft_id: str, action: str, operator: str, comment: str = "",
               confirmations: list[str] | None = None, missing_ack: dict[str, str] | None = None) -> dict:
    if action not in TRANSITIONS:
        raise ReviewError("unknown_action", f"未知操作：{action}", 422)
    row = _structured(_row(session, draft_id))
    src, dst = TRANSITIONS[action]
    if row.review_status != src:
        raise ReviewError("invalid_transition",
                          f"当前状态“{STATUS_ZH[row.review_status]}”不能执行 {action}（要求状态：{STATUS_ZH[src]}）")
    comment = (comment or "").strip()
    detail_: dict = {}
    if action in ("request_changes", "approve") and not comment:
        raise ReviewError("comment_required", "请填写审核意见", 422)
    if action == "approve":
        detail_ = _check_approval(session, row, comment, confirmations or [], missing_ack or {})
    _event(session, row, operator, action, from_status=src, to_status=dst, comment=comment, detail=detail_)
    row.review_status = dst
    session.commit()
    return _summary(row)


def _check_approval(session: Session, row: ReportDraft, comment: str, confirmations: list[str],
                    missing_ack: dict[str, str]) -> dict:
    doc, _ = _load(session, row)
    original, _ = _load(session, row, 1)
    info = readiness(session, row, doc, original)
    if info["blockers"]:
        raise ReviewError("approval_blocked", "存在未解决的问题，不能审核通过：" +
                          "；".join(b["message"] for b in info["blockers"]), 409, {"blockers": info["blockers"]})
    lacking = [k for k in REQUIRED_CONFIRMATIONS if k not in confirmations]
    if lacking:
        raise ReviewError("confirmations_required", "审核通过前须逐项勾选确认：" +
                          "；".join(REQUIRED_CONFIRMATIONS[k] for k in lacking), 422, {"missing": lacking})
    unack = [f for f in row.missing_fields if not (missing_ack.get(f) or "").strip()]
    if unack:
        raise ReviewError("missing_fields_unacknowledged",
                          "以下待补充字段须逐项填写处理说明（说明不会补齐数据，报告中仍保留“[待补充: …]”）：" + "；".join(unack),
                          422, {"fields": unack})
    return {"confirmations": sorted(confirmations),
            "missing_ack": {f: missing_ack[f].strip() for f in row.missing_fields}}


# ---------- 受控修订 ----------
def _next_bid(doc: ReportDoc) -> str:
    nums = [int(b.bid[1:]) for s in doc.sections for b in s.blocks if re.fullmatch(r"B\d+", b.bid)]
    return f"B{max(nums, default=0) + 1:03d}"


def _find(doc: ReportDoc, bid: str):
    for s in doc.sections:
        for i, b in enumerate(s.blocks):
            if b.bid == bid:
                return s, i, b
    raise ReviewError("block_not_found", f"块不存在：{bid}", 404)


def _flag_for(doc: ReportDoc, s: Sentence) -> str | None:
    problems = check_sentence(doc.facts, s)
    return "；".join(problems) if problems else None


def _guard_edit(row: ReportDraft, base_version: int) -> None:
    if row.review_status not in EDITABLE:
        raise ReviewError("report_locked", f"当前状态“{STATUS_ZH[row.review_status]}”不允许修订")
    if base_version != row.current_version:
        raise ReviewError("version_conflict", f"页面版本 v{base_version} 已过期，当前为 v{row.current_version}，请刷新后重试")


def _save_version(session: Session, row: ReportDraft, doc: ReportDoc, operator: str, change_type: str,
                  note: str, event_detail: dict) -> dict:
    row.current_version += 1
    session.add(ReportVersion(draft_id=row.id, version=row.current_version, content_json=doc_to_content(doc),
                              operator=operator, change_type=change_type, note=note))
    _event(session, row, operator, change_type, comment=note, detail=event_detail)
    session.commit()
    return {"current_version": row.current_version}


def edit_block(session: Session, draft_id: str, operator: str, base_version: int, bid: str, text: str,
               fact_keys: list[str] | None, note: str) -> dict:
    row = _structured(_row(session, draft_id))
    _guard_edit(row, base_version)
    text, note = (text or "").strip(), (note or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ReviewError("invalid_text", f"修订文字不能为空且不超过 {MAX_TEXT} 字", 422)
    if not note:
        raise ReviewError("note_required", "请填写修订原因", 422)
    doc, _ = _load(session, row)
    _, _, blk = _find(doc, bid)
    if not isinstance(blk, Sentence) or blk.kind == "placeholder":
        raise ReviewError("not_editable", "表格与“待补充”占位句不可在审核中改写；缺失数据须回到源数据补齐后重新生成", 422)
    old_ph = _PLACEHOLDER_RE.findall(blk.text)
    if any(p not in _PLACEHOLDER_RE.findall(text) for p in old_ph):
        raise ReviewError("placeholder_removed", "修订不得删除句中的“[待补充: …]”标记", 422)
    keys = list(blk.fact_keys if fact_keys is None else fact_keys)
    if blk.kind == "static" and keys:
        raise ReviewError("invalid_sources", "说明性文字不挂事实来源", 422)
    unknown = [k for k in keys if k not in doc.facts]
    if unknown:
        raise ReviewError("unknown_fact", f"来源事实不存在：{unknown}", 422)
    if text == blk.text and keys == blk.fact_keys:
        raise ReviewError("no_change", "内容没有变化", 422)
    if not blk.edited:
        blk.original_text = blk.text
    blk.text, blk.fact_keys, blk.edited = text, keys, True
    blk.flag = _flag_for(doc, blk)
    res = _save_version(session, row, doc, operator, "edit", note,
                        {"bid": bid, "flag": blk.flag, "fact_keys": keys})
    return {**res, "flag": blk.flag, "bid": bid}


def add_block(session: Session, draft_id: str, operator: str, base_version: int, after_bid: str, kind: str,
              text: str, fact_keys: list[str], note: str) -> dict:
    row = _structured(_row(session, draft_id))
    _guard_edit(row, base_version)
    text, note = (text or "").strip(), (note or "").strip()
    if kind not in ("static", "fact"):
        raise ReviewError("invalid_kind", "只能新增说明性文字（static）或带来源的业务结论（fact）", 422)
    if not text or len(text) > MAX_TEXT:
        raise ReviewError("invalid_text", f"文字不能为空且不超过 {MAX_TEXT} 字", 422)
    if not note:
        raise ReviewError("note_required", "请填写新增原因", 422)
    if kind == "static" and fact_keys:
        raise ReviewError("invalid_sources", "说明性文字不挂事实来源", 422)
    doc, _ = _load(session, row)
    unknown = [k for k in fact_keys if k not in doc.facts]
    if unknown:
        raise ReviewError("unknown_fact", f"来源事实不存在：{unknown}", 422)
    sec, idx, _ = _find(doc, after_bid)
    if any(t in sec.title for t in LOCKED_SECTIONS):
        raise ReviewError("section_locked", f"“{sec.title}”不允许新增内容", 422)
    blk = Sentence(text, kind, list(fact_keys), [], _next_bid(doc), edited=True, original_text=None)
    blk.flag = _flag_for(doc, blk)
    sec.blocks.insert(idx + 1, blk)
    res = _save_version(session, row, doc, operator, "add", note,
                        {"bid": blk.bid, "after": after_bid, "flag": blk.flag, "fact_keys": blk.fact_keys})
    return {**res, "flag": blk.flag, "bid": blk.bid}


# ---------- 审核意见 ----------
def add_comment(session: Session, draft_id: str, operator: str, kind: str, text: str, block_id: str | None) -> dict:
    row = _structured(_row(session, draft_id))
    if row.review_status == "reviewed":
        raise ReviewError("report_locked", "已审核通过，不再接受新的审核意见")
    if kind not in ("comment", "issue"):
        raise ReviewError("invalid_kind", "kind 必须是 comment 或 issue", 422)
    text = (text or "").strip()
    if not text:
        raise ReviewError("invalid_text", "意见内容不能为空", 422)
    if block_id:
        _find(_load(session, row)[0], block_id)
    c = ReportComment(draft_id=row.id, block_id=block_id or None, kind=kind, text=text, version=row.current_version,
                      created_by=operator)
    session.add(c)
    _event(session, row, operator, f"{kind}_added", comment=text, detail={"block_id": block_id})
    session.commit()
    return _comment_dict(c)


def resolve_comment(session: Session, draft_id: str, comment_id: int, operator: str, resolution: str) -> dict:
    row = _structured(_row(session, draft_id))
    if row.review_status == "reviewed":
        raise ReviewError("report_locked", "已审核通过，不能再改动审核意见")
    c = session.get(ReportComment, comment_id)
    if c is None or c.draft_id != row.id:
        raise ReviewError("comment_not_found", f"意见不存在：{comment_id}", 404)
    if c.status == "resolved":
        raise ReviewError("already_resolved", "该意见已处理")
    resolution = (resolution or "").strip()
    if not resolution:
        raise ReviewError("resolution_required", "请填写处理说明", 422)
    c.status, c.resolved_by, c.resolved_at, c.resolution = "resolved", operator, _now(), resolution
    _event(session, row, operator, "comment_resolved", comment=resolution, detail={"comment_id": comment_id})
    session.commit()
    return _comment_dict(c)


# ---------- 导出 ----------
def export_docx(session: Session, draft_id: str, operator: str, exports_dir: str | Path) -> Path:
    row = _row(session, draft_id)
    if row.current_version is None:
        raise ReviewError("legacy_draft", "该草稿生成于审核功能上线之前，没有经过审核流程，不能导出，请重新生成草稿。")
    if row.review_status != "reviewed":
        raise ReviewError("report_not_approved",
                          f"草稿状态为“{STATUS_ZH[row.review_status]}”，只有“审核通过”的报告才能导出 DOCX")
    if not operator or operator == "anonymous":
        raise ReviewError("operator_required", "导出须在 X-Operator 中提供操作人记录", 400)
    doc, v = _load(session, row)
    approve = session.scalars(select(ReportReviewEvent).where(
        ReportReviewEvent.draft_id == row.id, ReportReviewEvent.action == "approve")
        .order_by(ReportReviewEvent.id.desc())).first()
    if approve is None:                       # 状态被绕过接口直接改成 reviewed 时没有通过记录：不放行
        raise ReviewError("report_not_approved", "找不到审核通过记录，不能导出")
    review = {"version": v.version, "operator": approve.operator, "ts": iso(approve.ts), "comment": approve.comment,
              "missing_ack": approve.detail.get("missing_ack", {})}
    path = render_docx(doc, Path(exports_dir) / f"{row.id}-v{v.version}-reviewed.docx", row.id,
                       iso(row.created_at)[:16].replace("T", " ") + " UTC", review=review)
    _event(session, row, operator, "export", detail={"file": path.name, "version": v.version})
    session.commit()
    return path


def normalize_legacy(session: Session) -> int:
    """审核功能上线前的旧草稿没有审核记录：任何非 draft 状态一律还原为 draft，避免出现误导性的“已审核”。"""
    n = 0
    for r in session.scalars(select(ReportDraft).where(ReportDraft.current_version.is_(None))).all():
        if r.review_status != "draft":
            r.review_status = "draft"
            n += 1
    session.commit()
    return n


# ---------- 大模型润色建议（只读：不改任何状态；采纳须走 edit_block，并重新经过同一套校验）----------
_NUM = re.compile(r"\d+(?:\.\d+)?")


def suggest_rewrite(session: Session, draft_id: str, bid: str, provider) -> dict:
    row = _structured(_row(session, draft_id))
    if row.review_status not in EDITABLE:
        raise ReviewError("report_locked", f"当前状态“{STATUS_ZH[row.review_status]}”不允许修订")
    if provider is None or not getattr(provider, "is_generative", False):
        raise ReviewError("llm_not_enabled", "未启用大模型（LLM_PROVIDER=deterministic），没有可用的润色建议")
    doc, _ = _load(session, row)
    _, _, blk = _find(doc, bid)
    if not isinstance(blk, Sentence) or blk.kind == "placeholder":
        raise ReviewError("not_editable", "表格与“待补充”占位句不可润色", 422)
    allowed = set(_NUM.findall(blk.text))
    for k in blk.fact_keys:
        if k in doc.facts:
            allowed |= set(_NUM.findall(doc.facts.get(k).display or ""))
    rw = provider.rewrite(
        blk.text, instruction="在不改变含义的前提下，把下面这句话改写得更通顺、简洁、书面。",
        allowed_numbers=allowed, must_keep=_PLACEHOLDER_RE.findall(blk.text), allow_digits=blk.kind == "fact")
    used, reason = rw.used, rw.reason
    if used and rw.text.strip() == blk.text.strip():
        used, reason = False, "no_change"
    if used:   # 与人工修订相同的自检：不过关就不给建议
        problems = check_sentence(doc.facts, Sentence(rw.text, blk.kind, list(blk.fact_keys)))
        if problems:
            used, reason = False, "failed_self_check"
    return {"bid": bid, "original": blk.text, "suggestion": rw.text if used else None, "used": used,
            "reason": reason, "redactions": rw.redactions, "provider": provider.name}
