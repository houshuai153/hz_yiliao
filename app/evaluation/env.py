"""评测用的隔离环境：每次都在临时目录里重建数据库 / 知识库 / 索引，不碰使用者的 data/。"""
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.bootstrap import initialize
from app.db import models  # noqa: F401
from app.db.base import Base, make_engine, make_session_factory
from app.settings import ROOT, Settings
from seed.loader import seed_database


@dataclass
class EvalEnv:
    settings: Settings
    session_factory: object
    reports_dir: Path


@contextmanager
def full_env():
    """种子数据 + 知识库 + 本地索引（问答评测用）。"""
    with tempfile.TemporaryDirectory(prefix="bioeval-") as d:
        dd = Path(d)
        s = Settings(database_url=f"sqlite:///{(dd / 'eval.db').as_posix()}", index_dir=str(dd / "index"),
                     knowledge_dir=str(ROOT / "knowledge"), audit_jsonl_dir=str(dd / "audit"),
                     reports_dir=str(dd / "reports"), api_token="eval-token")
        initialize(s)
        engine = make_engine(s.database_url)
        try:
            yield EvalEnv(s, make_session_factory(engine), dd / "reports")
        finally:
            engine.dispose()


@contextmanager
def seeded_db():
    """只含种子数据的全新数据库（报告评测用；允许被用例修改）。"""
    with tempfile.TemporaryDirectory(prefix="bioeval-") as d:
        dd = Path(d)
        engine = make_engine(f"sqlite:///{(dd / 'eval.db').as_posix()}")
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        with factory() as s:
            seed_database(s)
        try:
            yield factory, dd / "reports"
        finally:
            engine.dispose()
