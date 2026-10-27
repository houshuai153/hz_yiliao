"""业务表。判定结果（pass/fail/pending_review）不入库，由 app.rules 实时计算。

子表使用自然复合主键，使种子数据可按主键 merge，重复运行不产生重复行。
"""
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    modality: Mapped[str] = mapped_column(String(20))  # small_molecule / biologic
    analyte: Mapped[str] = mapped_column(String(100))
    matrix: Mapped[str] = mapped_column(String(50))
    method_type: Mapped[str] = mapped_column(String(20))  # LC-MS/MS / LBA
    unit: Mapped[str] = mapped_column(String(20))
    lloq: Mapped[float] = mapped_column(Float)
    uloq: Mapped[float] = mapped_column(Float)
    phase: Mapped[str] = mapped_column(String(30))  # validation / sample_analysis
    planned_run_count: Mapped[int] = mapped_column(Integer)
    # 方案类信息（可空：未录入时报告显示“[待补充: …]”；LBA 不使用内标，internal_standard 不适用）
    internal_standard: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sample_prep: Mapped[str | None] = mapped_column(String(200), nullable=True)
    instrument_setup: Mapped[str | None] = mapped_column(String(200), nullable=True)  # LC-MS/MS：仪器与色谱柱；LBA：读板仪
    reagent_kit: Mapped[str | None] = mapped_column(String(200), nullable=True)  # LBA：试剂盒及批号
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)


class AnalyticalRun(Base):
    __tablename__ = "analytical_run"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    run_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))  # in_progress / completed / under_review
    rule_set_id: Mapped[str] = mapped_column(String(40))


class CalibrationPoint(Base):
    __tablename__ = "calibration_point"

    run_id: Mapped[str] = mapped_column(ForeignKey("analytical_run.id"), primary_key=True)
    level_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    nominal: Mapped[float] = mapped_column(Float)
    back_calc_conc: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # 派生展示值


class QCSample(Base):
    __tablename__ = "qc_sample"

    run_id: Mapped[str] = mapped_column(ForeignKey("analytical_run.id"), primary_key=True)
    level: Mapped[str] = mapped_column(String(8), primary_key=True)  # LLOQ/LQC/MQC/HQC
    replicate: Mapped[int] = mapped_column(Integer, primary_key=True)
    nominal: Mapped[float] = mapped_column(Float)
    measured: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class StudySample(Base):
    __tablename__ = "study_sample"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analytical_run.id"), index=True)
    subject_code: Mapped[str] = mapped_column(String(32))
    timepoint: Mapped[str] = mapped_column(String(16))
    conc: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag: Mapped[str] = mapped_column(String(16))  # ok/BLQ/above_ULOQ/reanalysis/missing


class ISRRecord(Base):
    __tablename__ = "isr_record"

    sample_id: Mapped[str] = mapped_column(ForeignKey("study_sample.id"), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analytical_run.id"), index=True)
    original_conc: Mapped[float] = mapped_column(Float)
    repeat_conc: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = 尚未复测
    diff_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class Deviation(Base):
    __tablename__ = "deviation"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analytical_run.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))  # minor / major / critical
    status: Mapped[str] = mapped_column(String(10))  # open / closed
    description: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class ReportMilestone(Base):
    __tablename__ = "report_milestone"

    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), primary_key=True)
    type: Mapped[str] = mapped_column(String(24), primary_key=True)  # validation_report / sample_report
    planned_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))  # not_started / in_progress / delivered
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class KBDocument(Base):
    __tablename__ = "kb_document"

    doc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(16))  # demo_summary / demo_sop
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    disclaimer: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))


class KBSection(Base):
    __tablename__ = "kb_section"

    section_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_document.doc_id"), index=True)
    version: Mapped[str] = mapped_column(String(16))
    heading: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(16))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)


class ReportDraft(Base):
    """报告草稿元数据。review_status 只能经 app.reports.review 的状态机改变。

    current_version 为空 = 审核功能上线前生成的旧草稿（没有保存结构化内容），不可审核、不可导出，须重新生成。
    """
    __tablename__ = "report_draft"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    run_ids: Mapped[list] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(20))  # validation / sample_analysis
    path: Mapped[str] = mapped_column(String(300))  # 生成时的初稿 DOCX 快照；不可下载，导出以最终审核版本重新渲染
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    review_status: Mapped[str] = mapped_column(String(24), default="draft")  # draft / in_review / changes_requested / reviewed
    facts_json: Mapped[list] = mapped_column(JSON)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReportVersion(Base):
    """报告内容的每个版本（整份结构化内容快照）。v1 = 生成时的原文，之后每次人工修订追加一个版本。"""
    __tablename__ = "report_version"

    draft_id: Mapped[str] = mapped_column(ForeignKey("report_draft.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    operator: Mapped[str] = mapped_column(String(64))
    change_type: Mapped[str] = mapped_column(String(16))  # generated / edit / add
    note: Mapped[str] = mapped_column(Text, default="")


class ReportReviewEvent(Base):
    """审核事件流水（只追加）：状态变化、修订、导出。operator 是“操作人记录”，不是电子签名。"""
    __tablename__ = "report_review_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("report_draft.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    operator: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24))  # generated / start_review / request_changes / resubmit / approve / edit / add / export
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ReportComment(Base):
    """审核意见 / 待处理问题。kind=issue 且 status=open 时阻止审核通过。"""
    __tablename__ = "report_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("report_draft.id"), index=True)
    block_id: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 为空 = 针对整份报告
    kind: Mapped[str] = mapped_column(String(8))  # comment / issue
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open / resolved
    version: Mapped[int] = mapped_column(Integer)  # 提出时所处版本
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class UISession(Base):
    """浏览器登录会话。库里只存会话 ID 的 SHA-256，Cookie 里的原值泄露库也无法直接使用；可按行撤销。"""
    __tablename__ = "ui_session"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentWriteClaim(Base):
    """Agent 写工具的幂等记录：同一个幂等键只执行一次，重复提交返回已保存的结果。

    status=claimed 表示已占用但尚未写入结果（执行中，或进程在中途退出）；done 表示 result_json 已是最终结果。
    confirmation_id 唯一：一次确认只能对应一次占用。
    """
    __tablename__ = "agent_write_claim"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    confirmation_id: Mapped[str] = mapped_column(String(64), unique=True)
    tool: Mapped[str] = mapped_column(String(40))
    operator: Mapped[str] = mapped_column(String(64))
    session_key: Mapped[str] = mapped_column(String(64))
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="claimed")  # claimed / done
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentTask(Base):
    """Agent 会话（多轮任务）。绑定登录用户与登录会话；到期清理。"""
    __tablename__ = "agent_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    session_key: Mapped[str] = mapped_column(String(64), index=True)
    # running / awaiting_confirmation / idle / failed / terminated / expired
    status: Mapped[str] = mapped_column(String(24), default="idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active_role: Mapped[str] = mapped_column(String(16), default="orchestrator")   # 当前持有控制权的角色
    active_frame: Mapped[str] = mapped_column(String(16), default="root")
    delegate_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 总控等待结果的 delegate 调用
    frame_seq: Mapped[int] = mapped_column(Integer, default=0)
    user_message: Mapped[str] = mapped_column(Text, default="")                     # 本轮用户消息（已脱敏限长）
    user_message_id: Mapped[str] = mapped_column(String(40), default="")
    run_rounds: Mapped[int] = mapped_column(Integer, default=0)
    run_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    final_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    audit_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)  # False = 有步骤未能写入审计


class AgentMessage(Base):
    """发给模型的对话历史（已脱敏、限长）。随任务到期清理。"""
    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    frame_id: Mapped[str] = mapped_column(String(16))
    agent_role: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(10))            # user / assistant / tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tool_calls_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentStep(Base):
    """服务端执行并记录的步骤：角色切换、模型调用、工具调用、确认、最终回答。id 即 step_id。"""
    __tablename__ = "agent_step"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    agent_role: Mapped[str] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # 与 audit_log.request_id 对应
    operator: Mapped[str] = mapped_column(String(64), default="")
    # ---- 阶段 3：审计字段。只存白名单参数摘要、来源编号、内容哈希，不存提示词 / 模型输出 / 工具结果正文 ----
    args_summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)         # ["run:PRJ-…-R01", "kb_section:DEMO-…"]
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 已脱敏内容的 SHA-256（可核对、不可还原）
    model_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok / rejected / model / fallback
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentPendingAction(Base):
    """等待用户独立确认的写操作。参数与哈希由服务端保存，模型无法在确认后改动。"""
    __tablename__ = "agent_pending_action"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    frame_id: Mapped[str] = mapped_column(String(16))
    agent_role: Mapped[str] = mapped_column(String(16))
    tool_call_id: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(40))
    args_json: Mapped[dict] = mapped_column(JSON)
    args_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending")   # pending / confirming / executed / rejected / expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
