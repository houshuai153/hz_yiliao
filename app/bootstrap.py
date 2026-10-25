"""初始化：建表 → 种子数据 → 知识库导入 → 本地索引。可重复运行。"""
from app.db import models  # noqa: F401
from app.db.base import Base, add_missing_columns, make_engine, make_session_factory
from app.kb.indexer import build_index
from app.kb.ingest import ingest_knowledge
from app.settings import Settings
from seed.loader import seed_database


def initialize(settings: Settings, reset: bool = False) -> dict:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    added_columns = add_missing_columns(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        seed_counts = seed_database(session, reset=reset)
        kb_counts = ingest_knowledge(session, settings.knowledge_dir)
        index_meta = build_index(session, settings.index_dir)
    engine.dispose()
    return {"seed": seed_counts, "knowledge": kb_counts, "index": index_meta, "added_columns": added_columns}
