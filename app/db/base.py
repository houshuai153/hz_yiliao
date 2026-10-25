"""数据库基础：引擎工厂与声明式基类。不使用 SQLite 专有特性，便于切换 PostgreSQL。"""
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def add_missing_columns(engine: Engine) -> list[str]:
    """给已存在的表补上模型里新增的**可空**列（create_all 不会改已有表）。返回新增的 `表.列`。

    仅用于原型的简单演进；正式环境应换成 Alembic 迁移。不处理删列、改类型、非空列。
    """
    added: list[str] = []
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"]: c for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                _widen_varchar(engine, table.name, col, have[col.name], added)
                continue
            if not col.nullable or col.primary_key:
                raise RuntimeError(f"无法自动补列 {table.name}.{col.name}：非空/主键列需要正式迁移")
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=engine.dialect)}'
            with engine.begin() as conn:
                conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    return added


def _widen_varchar(engine: Engine, table: str, col, existing: dict, added: list[str]) -> None:
    """模型里的 VARCHAR 变长时，PostgreSQL 上同步加宽（只加宽，不缩）；SQLite 不校验长度，无需处理。"""
    old, new = getattr(existing["type"], "length", None), getattr(col.type, "length", None)
    if engine.dialect.name != "postgresql" or not old or not new or new <= old:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{col.name}" TYPE VARCHAR({new})'))
    added.append(f"{table}.{col.name}(VARCHAR {old}->{new})")
