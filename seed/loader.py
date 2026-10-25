"""把生成的种子数据按主键 merge 入库：重复运行不产生重复行。"""
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticalRun, CalibrationPoint, Deviation, ISRRecord, Project, QCSample,
    ReportMilestone, StudySample,
)
from seed.generator import generate

_MAP = [
    ("projects", Project), ("runs", AnalyticalRun), ("calibration_points", CalibrationPoint),
    ("qc_samples", QCSample), ("study_samples", StudySample), ("isr_records", ISRRecord),
    ("deviations", Deviation), ("milestones", ReportMilestone),
]
# 删除顺序：子表在前
_RESET_ORDER = [ISRRecord, StudySample, QCSample, CalibrationPoint, Deviation,
                ReportMilestone, AnalyticalRun, Project]


def seed_database(session: Session, reset: bool = False) -> dict[str, int]:
    """reset=True 时先清空种子拥有的业务表（仅用于开发重置，不碰知识库与审计表）。"""
    data = generate()
    if reset:
        for model in _RESET_ORDER:
            session.execute(delete(model))
    for key, model in _MAP:
        for row in data[key]:
            session.merge(model(**row))
        session.flush()
    session.commit()
    return count_rows(session)


def count_rows(session: Session) -> dict[str, int]:
    return {m.__tablename__: session.scalar(select(func.count()).select_from(m)) for _, m in _MAP}
