"""生成报告草稿：构建 → 自检 → 渲染 DOCX → 记录元数据。"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import ReportDraft, ReportReviewEvent, ReportVersion
from app.reports.builder import ReportError, build_report
from app.reports.checks import ReportIntegrityError, check_report
from app.reports.docx_render import BANNER, render_docx
from app.reports.model import assign_block_ids, doc_to_content
from app.rules.engine import RuleSet

KIND_CODE = {"validation": "VAL", "sample_analysis": "SMP"}


def generate_draft(session: Session, reports_dir: str | Path, project_id: str, run_ids: list[str],
                   kind: str, rule_sets: dict[str, RuleSet], operator: str = "system") -> dict:
    doc = build_report(session, project_id, run_ids, kind, rule_sets)
    try:
        stats = check_report(doc)
    except ReportIntegrityError as exc:
        raise ReportError("report_integrity_failed", "报告自检未通过，已拒绝生成：" + str(exc), 500) from exc
    assign_block_ids(doc)
    now = datetime.now(timezone.utc)
    draft_id = f"DRAFT-{KIND_CODE[kind]}-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    path = render_docx(doc, Path(reports_dir) / f"{draft_id}.docx", draft_id, now.strftime("%Y-%m-%d %H:%M UTC"))
    session.add(ReportDraft(
        id=draft_id, project_id=project_id, run_ids=doc.run_ids, kind=kind, path=str(path),
        review_status="draft", facts_json=[f.to_dict() for f in doc.facts.all()],
        missing_fields=doc.missing_fields, current_version=1,
    ))
    session.add(ReportVersion(draft_id=draft_id, version=1, content_json=doc_to_content(doc), operator=operator,
                              change_type="generated", note="系统依据源数据与规则生成"))
    session.add(ReportReviewEvent(draft_id=draft_id, operator=operator, action="generated", to_status="draft",
                                  version=1, comment="", detail={"project_id": project_id, "run_ids": doc.run_ids}))
    session.commit()
    return {"draft_id": draft_id, "kind": kind, "project_id": project_id, "run_ids": doc.run_ids,
            "review_status": "draft", "banner": BANNER, "missing_fields": doc.missing_fields,
            "rule_ids": doc.rule_ids, "path": str(path), **stats}
