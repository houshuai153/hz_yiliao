"""把解析后的知识文档按主键 upsert 入库；文件里已不存在的文档/章节会被删除。重复运行结果不变。"""
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import KBDocument, KBSection
from app.kb.parser import parse_dir


def ingest_knowledge(session: Session, knowledge_dir: str | Path) -> dict[str, int]:
    docs = parse_dir(knowledge_dir)
    keep_docs = {d.doc_id for d in docs}
    keep_sections = {s.section_id for d in docs for s in d.sections}

    for sid in session.scalars(select(KBSection.section_id)).all():
        if sid not in keep_sections:
            session.execute(delete(KBSection).where(KBSection.section_id == sid))
    for did in session.scalars(select(KBDocument.doc_id)).all():
        if did not in keep_docs:
            session.execute(delete(KBDocument).where(KBDocument.doc_id == did))
    session.flush()

    for d in docs:
        session.merge(KBDocument(
            doc_id=d.doc_id, version=d.version, title=d.title, source_type=d.source_type,
            is_demo=d.is_demo, disclaimer=d.disclaimer, content_sha256=d.content_sha256,
        ))
        session.flush()
        for s in d.sections:
            session.merge(KBSection(
                section_id=s.section_id, doc_id=s.doc_id, version=s.version, heading=s.heading,
                text=s.text, position=s.position, source_type=s.source_type, is_demo=s.is_demo,
            ))
    session.commit()
    return {"documents": len(docs), "sections": len(keep_sections)}
