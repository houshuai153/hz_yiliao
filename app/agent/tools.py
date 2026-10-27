"""Agent 工具：定义、注册、参数校验与执行（阶段 1：工具层）。

原则：
- 只复用现有业务计算与服务；判定（pass/fail/pending_review）一律来自 app.rules（经 assess_run / project_metrics），
  工具不计算、不改写、不补造判定。每个含判定的结果都带 verdict_source="app.rules"。
- 所有工具统一结果结构（见 schema.py）。未预期异常只返回 internal_error，不回显异常正文。
- 结果在返回前经过 finalize：脱敏（app.llm.redact）、限长、限大小，并标注 data_trust="untrusted"。
- 默认只返回完成任务所需的字段；不把整份报告或全部原始样品记录交给模型（get_run_detail 默认只给汇总，
  明细需显式指定 section 且限条数；研究样品只给计数；偏差自由文本不返回）。
- 写工具（generate_report_draft / transition_report）只有在 ctx.grant 有效时才会执行：
  grant 由服务端在用户独立确认后签发，绑定工具名 + 参数哈希 + 登录操作人 + 登录会话 + 有效期，
  并通过幂等记录保证同一次确认只生效一次。模型参数里不能出现 operator / 确认 / 幂等键等字段。
- transition_report 不开放 approve：审核通过只能由人在报告审核页完成。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from app.agent.context import AgentContext, WriteGrant
from app.agent.schema import ID_PATTERN, ArgumentError, canonical_hash, err, finalize, ok, validate_args
from app.dashboard import service as svc
from app.dashboard.metrics import project_metrics
from app.db.models import AgentWriteClaim
from app.reports import review
from app.reports.builder import ReportError
from app.reports.review import ReviewError
from app.reports.service import generate_draft
from app.translation.service import TranslationError

log = logging.getLogger("app.agent.tools")

VERDICT_SOURCE = "app.rules"
RULE_STATUS = "demo_unconfirmed"
KIND_ZH = {"validation": "方法学验证报告", "sample_analysis": "样品分析报告"}
# 开放给 Agent 的状态转换。approve 不在其中：审核通过只能由人进入报告审核页完成。
AGENT_TRANSITIONS = ("start_review", "request_changes", "resubmit")
APPROVAL_NOTE = "审核通过须由人在报告审核页完成；Agent 不能批准报告，也不能导出。"
MAX_RUNS, MAX_DEVIATIONS, MAX_REPORTS, MAX_PARAGRAPHS = 50, 20, 30, 20

_ID = {"type": "string", "pattern": ID_PATTERN}


# ---------- 工具定义 ----------
@dataclass(frozen=True)
class PendingAction:
    """待用户确认的写操作（由服务端展示给用户；确认令牌绑定其中的 args_hash）。"""
    tool: str
    args: dict
    args_hash: str
    summary: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str            # 用途与限制
    parameters: dict            # JSON Schema（additionalProperties 恒为 false）
    _impl: Callable[[AgentContext, dict], dict]
    writes: bool = False
    normalize: Callable[[dict], dict] | None = None
    summarize: Callable[[dict], str] | None = None

    # ---- 给模型看的定义（OpenAI 兼容 tools 格式）----
    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": self.parameters}}

    # ---- 参数 ----
    def _args(self, raw: Any) -> dict:
        args = validate_args(self.parameters, raw)
        return self.normalize(args) if self.normalize else args

    def prepare(self, raw: Any) -> PendingAction:
        """校验并规范化写操作的参数，给出人读摘要和参数哈希（供“待确认动作”展示与确认令牌绑定）。"""
        if not self.writes or self.summarize is None:
            raise ArgumentError(f"{self.name} 不是写工具")
        args = self._args(raw)
        return PendingAction(self.name, args, canonical_hash(self.name, args), self.summarize(args))

    # ---- 执行：校验 → （写工具）门禁与幂等 → 业务 → 净化 ----
    def execute(self, ctx: AgentContext, raw_args: Any) -> dict:
        try:
            args = self._args(raw_args)
        except ArgumentError as exc:
            return finalize(err("invalid_arguments", str(exc)))
        if not self.writes:
            return self._run(ctx, args)
        args_hash = canonical_hash(self.name, args)
        denied = _authorize(ctx, self.name, args_hash)
        if denied:
            return finalize(denied)
        state, payload = _claim(ctx, ctx.grant, self.name, args_hash)
        if state == "replay":
            return {**payload, "replayed": True}
        if state == "error":
            return finalize(payload)
        result = self._run(ctx, args)
        _store(ctx, ctx.grant, result)
        return result

    def _run(self, ctx: AgentContext, args: dict) -> dict:
        try:
            return finalize(self._impl(ctx, args))
        except ArgumentError as exc:
            return finalize(err("invalid_arguments", str(exc)))
        except svc.NotFound as exc:
            return finalize(err(exc.code, exc.message))
        except (ReportError, ReviewError) as exc:
            return finalize(err(exc.code, exc.message))
        except TranslationError as exc:
            return finalize(err("invalid_arguments", str(exc)))
        except FileNotFoundError:
            return finalize(err("knowledge_unavailable", "本地知识库索引不可用"))
        except Exception as exc:  # noqa: BLE001  未预期异常：只记录类型，不把正文（可能含密钥或数据）交给模型
            log.warning("agent tool %s failed: %s", self.name, type(exc).__name__)
            return finalize(err("internal_error", "工具执行出错，已记录；请稍后重试或改用其它方式"))


# ---------- 写权限门禁与幂等 ----------
def _authorize(ctx: AgentContext, tool: str, args_hash: str) -> dict | None:
    if not ctx.operator or ctx.operator == "anonymous":
        return err("operator_required", "写操作需要可信的登录操作人")
    g: WriteGrant | None = ctx.grant
    if g is None:
        return err("confirmation_required", "该操作会修改数据：需要用户在独立的确认步骤中确认后才能执行")
    if g.tool != tool or g.args_hash != args_hash:
        return err("grant_mismatch", "确认的内容与待执行的操作不一致（工具或参数已变化）")
    if not ctx.session_key or g.session_key != ctx.session_key or g.operator != ctx.operator:
        return err("grant_not_bound", "确认未绑定当前登录会话")
    if not (g.confirmation_id and g.idempotency_key and g.user_message_id) or len(g.idempotency_key) > 64 \
            or len(g.confirmation_id) > 64:
        return err("grant_invalid", "确认信息不完整")
    now = ctx.clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now >= g.expires_at:
        return err("grant_expired", "确认已过期，请重新发起并确认")
    return None


def _claim(ctx: AgentContext, g: WriteGrant, tool: str, args_hash: str) -> tuple[str, dict]:
    """占用幂等键。返回 ("new", {}) / ("replay", 已保存的结果) / ("error", 错误结果)。"""
    with ctx.session_factory() as s:
        s.add(AgentWriteClaim(idempotency_key=g.idempotency_key, confirmation_id=g.confirmation_id, tool=tool,
                              operator=ctx.operator, session_key=ctx.session_key or "", args_hash=args_hash,
                              status="claimed"))
        try:
            s.commit()
            return "new", {}
        except IntegrityError:
            s.rollback()
        row = s.get(AgentWriteClaim, g.idempotency_key)
        if row is None:                                   # 幂等键没冲突，是确认标识已被另一个键占用
            return "error", err("confirmation_reused", "该确认已被使用")
        same = (row.tool, row.args_hash, row.operator, row.session_key, row.confirmation_id) == (
            tool, args_hash, ctx.operator, ctx.session_key or "", g.confirmation_id)
        if not same:
            return "error", err("idempotency_conflict", "该幂等键已用于另一个操作")
        if row.status == "done" and isinstance(row.result_json, dict):
            return "replay", row.result_json
        return "error", err("action_in_progress", "该操作正在执行或上次执行未完成，未重复执行")


def _store(ctx: AgentContext, g: WriteGrant, result: dict) -> None:
    try:
        with ctx.session_factory() as s:
            row = s.get(AgentWriteClaim, g.idempotency_key)
            if row is not None:
                row.status, row.result_json = "done", result
                s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("agent write result not stored: %s", type(exc).__name__)


# ---------- 读工具 ----------
def _list_projects(ctx, a):
    with ctx.session_factory() as s:
        rows = svc.list_projects(s)
    return ok({"count": len(rows), "projects": rows}, [("project", r["id"]) for r in rows])


def _get_project(ctx, a):
    with ctx.session_factory() as s:
        p = svc.get_project(s, a["project_id"])
    return ok({"project": p}, [("project", p["id"])])


def _list_project_runs(ctx, a):
    with ctx.session_factory() as s:
        runs = svc.list_runs(s, ctx.rule_sets, a["project_id"])
    shown = runs[:MAX_RUNS]
    return ok({"project_id": a["project_id"], "verdict_source": VERDICT_SOURCE, "rule_status": RULE_STATUS,
               "total_runs": len(runs), "runs": shown},
              [("project", a["project_id"])] + [("run", r["id"]) for r in shown])


_SECTION_KEY = {"calibration": "calibration_points", "qc": "qc_samples", "isr": "isr_records"}


def _get_run_detail(ctx, a):
    with ctx.session_factory() as s:
        d = svc.run_detail(s, ctx.rule_sets, a["run_id"])
    data = {"run": d["run"], "rule_status": d["rule_status"], "verdict_source": VERDICT_SOURCE,
            "assessment": d["assessment"], "sample_counts": d["sample_counts"],   # 研究样品只给计数，不给逐条记录
            "row_counts": {k: len(d[v]) for k, v in _SECTION_KEY.items()}}
    if a["section"] != "summary":
        rows = d[_SECTION_KEY[a["section"]]]
        data.update({"section": a["section"], "rows_total": len(rows), "rows": rows[: a["limit"]]})
    return ok(data, [("run", d["run"]["id"]), ("project", d["run"]["project_id"])])


def _parse_as_of(a) -> date:
    if a.get("as_of"):
        try:
            return date.fromisoformat(a["as_of"])
        except ValueError as exc:
            raise ArgumentError("参数 as_of 不是有效日期") from exc
    return datetime.now(timezone.utc).date()


def _dashboard_project(ctx, a):
    as_of = _parse_as_of(a)
    with ctx.session_factory() as s:
        p = svc.require_project(s, a["project_id"])
        m = project_metrics(s, p, ctx.rule_sets, as_of)
    dev = m["deviations"]
    data = {
        "verdict_source": VERDICT_SOURCE, "rule_status": m["rule_status"], "as_of": m["as_of"],
        "project": {k: m["project"][k] for k in ("id", "title", "method_type", "unit", "phase")},
        "samples": m["samples"], "runs": m["runs"],
        "calibration": m["calibration"], "qc": m["qc"], "accepted": m["accepted"], "isr": m["isr"],
        # 偏差只给结构化字段；自由文本描述（description）可能含不可信内容，不交给模型
        "deviations": {k: dev[k] for k in ("total", "open", "closed", "open_by_severity", "oldest_open_days")}
                      | {"items": [{k: d[k] for k in ("id", "run_id", "category", "severity", "status", "opened_at")}
                                   for d in dev["items"][:MAX_DEVIATIONS]], "items_total": len(dev["items"])},
        "milestones": m["milestones"], "run_summaries": m["run_summaries"][:MAX_RUNS],
        "risks": [{k: r[k] for k in ("risk_code", "level", "message", "evidence")} for r in m["risks"]],
    }
    return ok(data, [("project", p.id)] + [("run", r["id"]) for r in data["run_summaries"]])


def _ask_regulatory(ctx, a):
    # polish=False：Agent 内只用规则抽取的原句作答，不在工具内再次调用模型
    res = ctx.assistant.ask(a["question"], a.get("top_k"), polish=False)
    cites = [{k: c[k] for k in ("doc_id", "version", "section_id", "title", "label", "quote")} for c in res.citations]
    data = {"refused": res.refused, "refusal_reason": res.refusal_reason, "answer": res.answer,
            "confidence": res.confidence, "confidence_note": "证据充分度启发式分数，不是答案正确的概率",
            "citations": cites}
    return ok(data, [("kb_section", c["section_id"]) for c in cites])


def _translate_text(ctx, a):
    svc_ = ctx.translation_service
    res = svc_.translate(a["text"], a["direction"], a.get("reference"))
    paras = []
    for p in res["paragraphs"][:MAX_PARAGRAPHS]:
        paras.append({
            "index": p["index"], "status": p["status"], "source": p["source"], "target": p["target"],
            "generated_by": sorted({x.get("generated_by", "pattern") for x in p["sentences"]
                                    if x["status"] == "translated"}),
            "warnings": [{k: w[k] for k in ("code", "severity", "message")} for w in p["warnings"]],
            "term_hits": [{"term_id": h["term_id"], "matched_text": h["matched_text"], "expected": h["expected"]}
                          for h in p["term_hits"][:20]]})
    data = {"provider": svc_.provider.name, "is_generative": svc_.provider.is_generative, "direction": res["direction"],
            "stats": res["stats"], "document_warnings": [w for w in res["document_warnings"]],
            "paragraphs": paras, "paragraphs_total": len(res["paragraphs"]),
            "note": "仅翻译已登记的固定句型（启用大模型时句型之外由模型兜底，须人工复核）"}
    return ok(data)


def _draft_row(d: dict) -> dict:
    return {k: d.get(k) for k in ("draft_id", "project_id", "kind", "run_ids", "review_status", "current_version",
                                  "legacy", "created_at", "missing_fields", "open_issues")}


def _list_reports(ctx, a):
    with ctx.session_factory() as s:
        rows = review.list_drafts(s, a.get("project_id"), a.get("status"))
    shown = [_draft_row(r) for r in rows[:MAX_REPORTS]]
    for r in shown:
        r["review_url"] = f"/reports/{r['draft_id']}"
    return ok({"total": len(rows), "reports": shown}, [("report", r["draft_id"]) for r in shown])


def _get_report(ctx, a):
    with ctx.session_factory() as s:
        d = review.detail(s, a["draft_id"])
    if d.get("legacy"):
        raise ReviewError("legacy_draft", d.get("message") or "旧草稿不可审核", 409)
    sections = d.get("sections", [])
    blocks = [b for sec in sections for b in sec["blocks"]]
    rd = d["readiness"]
    data = {
        "report": _draft_row(d), "title": d.get("title"), "stats": d.get("stats"),
        "readiness": {"can_approve": rd["can_approve"], "open_issue_count": rd["open_issue_count"],
                      "missing_to_acknowledge": rd["missing_to_acknowledge"],
                      "blockers": [{"code": b["code"], "message": b["message"]} for b in rd["blockers"]]},
        "open_issues": [{"id": c["id"], "block_id": c["block_id"], "created_by": c["created_by"],
                         "text": (c["text"] or "")[:200]}
                        for c in d.get("comments", []) if c["kind"] == "issue" and c["status"] == "open"][:10],
        "flagged_blocks": [{"bid": b["bid"], "flag": (b["flag"] or "")[:200]} for b in blocks
                           if b["type"] == "sentence" and b.get("flag")][:20],
        "sections": [{"index": i, "title": sec["title"],
                      "sentences": sum(b["type"] == "sentence" for b in sec["blocks"]),
                      "tables": sum(b["type"] == "table" for b in sec["blocks"])} for i, sec in enumerate(sections)],
        "review_url": f"/reports/{d['draft_id']}", "approval_note": APPROVAL_NOTE,
    }
    si = a.get("section_index")
    if si is not None:
        if si >= len(sections):
            raise ArgumentError("参数 section_index 超出章节范围")
        sec = sections[si]
        data["section"] = {
            "index": si, "title": sec["title"],
            "sentences": [{"bid": b["bid"], "kind": b["kind"], "text": b["text"], "tags": b["tags"],
                           "edited": b["edited"]} for b in sec["blocks"] if b["type"] == "sentence"][:30],
            "tables": [{"caption": b["caption"], "rows": len(b["rows"])} for b in sec["blocks"] if b["type"] == "table"]}
    return ok(data, [("report", d["draft_id"]), ("project", d["project_id"])] + [("run", r) for r in d["run_ids"]])


# ---------- 写工具（需要 ctx.grant）----------
def _generate_report_draft(ctx, a):
    with ctx.session_factory() as s:
        res = generate_draft(s, ctx.settings.reports_dir, a["project_id"], a["run_ids"], a["kind"],
                             ctx.rule_sets, ctx.operator)      # 操作人只取登录会话
    data = {"draft_id": res["draft_id"], "kind": res["kind"], "project_id": res["project_id"],
            "run_ids": res["run_ids"], "review_status": res["review_status"],
            "missing_fields": res["missing_fields"], "rule_ids": res["rule_ids"],
            "fact_count": res["fact_count"], "fact_sentences": res["fact_sentences"],
            "unsourced_sentences": res["unsourced_sentences"],
            "review_url": f"/reports/{res['draft_id']}", "approval_note": APPROVAL_NOTE}
    return ok(data, [("report", res["draft_id"]), ("project", res["project_id"])] + [("run", r) for r in res["run_ids"]])


def _transition_report(ctx, a):
    if a["action"] not in AGENT_TRANSITIONS:        # 纵深防御：即使参数校验被绕过，也绝不执行 approve 等其它转换
        return err("action_forbidden", "该状态转换不允许由 Agent 执行；审核通过须由人在审核页完成")
    with ctx.session_factory() as s:
        res = review.transition(s, a["draft_id"], a["action"], ctx.operator, a.get("comment", ""))
    data = {"report": {k: res[k] for k in ("draft_id", "project_id", "kind", "review_status", "current_version")},
            "review_url": f"/reports/{res['draft_id']}", "approval_note": APPROVAL_NOTE}
    return ok(data, [("report", res["draft_id"])])


def _norm_generate(a: dict) -> dict:
    return {**a, "run_ids": sorted(set(a["run_ids"]))}


def _norm_transition(a: dict) -> dict:
    out = dict(a)
    out["comment"] = (a.get("comment") or "").strip()
    return out


def _sum_generate(a: dict) -> str:
    runs = "、".join(a["run_ids"]) if a["run_ids"] else "全部分析批"
    return (f"为项目 {a['project_id']} 生成{KIND_ZH[a['kind']]}草稿（纳入：{runs}）。草稿状态为“待审核”，"
            "不会自动审核或导出；审核通过须由人在审核页完成。")


def _sum_transition(a: dict) -> str:
    zh = {"start_review": "开始审核", "request_changes": "退回修改", "resubmit": "重新提交审核"}[a["action"]]
    tail = f"，意见：{a['comment']}" if a["comment"] else ""
    return f"将报告草稿 {a['draft_id']} 执行“{zh}”{tail}。这不是审核通过。"


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_projects", "列出全部项目的基本信息和分析批数量。只读；无参数。",
         _obj({}), _list_projects),
    Tool("get_project", "读取一个项目的方法与方案信息（LLOQ/ULOQ、内标、前处理、仪器等）。只读；项目不存在返回 project_not_found。",
         _obj({"project_id": _ID}, ["project_id"]), _get_project),
    Tool("list_project_runs", "列出一个项目的全部分析批及其规则判定（校准 / QC / 批接受 / ISR）。判定来自规则引擎，"
                              "不可改写；最多返回 50 个批。只读。",
         _obj({"project_id": _ID}, ["project_id"]), _list_project_runs),
    Tool("get_run_detail", "读取一个分析批的规则判定与统计。默认只返回汇总（判定、规则 ID、样品计数）；"
                           "如需逐条记录，指定 section=calibration/qc/isr 并用 limit 限制条数（≤50）。"
                           "研究样品只提供计数，不提供逐条记录。只读。",
         _obj({"run_id": _ID,
               "section": {"type": "string", "enum": ["summary", "calibration", "qc", "isr"], "default": "summary"},
               "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}, ["run_id"]),
         _get_run_detail),
    Tool("dashboard_project", "读取一个项目的看板指标：进度、QC/ISR 通过率、偏差统计（不含自由文本）、里程碑、风险提示。"
                              "指标和判定来自规则引擎。as_of 为统计截止日期（YYYY-MM-DD，缺省今天），只影响里程碑与偏差账龄。只读。",
         _obj({"project_id": _ID, "as_of": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}}, ["project_id"]),
         _dashboard_project),
    Tool("ask_regulatory", "在本地演示知识库中检索并回答法规 / SOP 问题。答案是检索到的原句，带引用；证据不足时拒答。"
                           "知识库只是教学演示摘要，不是监管原文；不会提供条款编号或原文。只读。",
         _obj({"question": {"type": "string", "minLength": 1, "maxLength": 500},
               "top_k": {"type": "integer", "minimum": 1, "maximum": 5}}, ["question"]), _ask_regulatory),
    Tool("translate_text", "翻译并核对术语（中英）。只翻译已登记的固定句型，其余句子不生成译文（启用大模型时由模型兜底并带"
                           "“须人工复核”警告）。text ≤4000 字，每行一个段落，最多返回 20 段。只读；不保存任何内容。",
         _obj({"text": {"type": "string", "minLength": 1, "maxLength": 4000},
               "direction": {"type": "string", "enum": ["zh2en", "en2zh", "bilingual"]},
               "reference": {"type": "string", "maxLength": 4000}}, ["text", "direction"]), _translate_text),
    Tool("generate_report_draft", "【写操作，需用户确认】为项目生成报告草稿（DOCX 草稿 + 可审核的结构化内容）。草稿状态为“待审核”，"
                                  "不会审核通过、不会导出。run_ids 为空表示全部分析批。",
         _obj({"project_id": _ID, "kind": {"type": "string", "enum": ["validation", "sample_analysis"]},
               "run_ids": {"type": "array", "items": _ID, "maxItems": 20, "default": []}}, ["project_id", "kind"]),
         _generate_report_draft, writes=True, normalize=_norm_generate, summarize=_sum_generate),
    Tool("list_reports", "列出报告草稿及其审核状态（最新在前，最多 30 条）。可按项目或状态过滤。只读。",
         _obj({"project_id": _ID, "status": {"type": "string", "enum": list(review.STATUSES)}}), _list_reports),
    Tool("get_report", "读取一份报告草稿的审核证据汇总：状态、统计、通过前检查（阻止项）、未解决问题、被标记的句子、章节目录。"
                       "不返回整份报告；如需某一章的句子，指定 section_index。含审核页链接。只读。",
         _obj({"draft_id": _ID, "section_index": {"type": "integer", "minimum": 0, "maximum": 50}}, ["draft_id"]),
         _get_report),
    Tool("transition_report", "【写操作，需用户逐项确认】推进报告审核状态：start_review（开始审核）、request_changes（退回修改，"
                              "须填意见）、resubmit（重新提交）。不能审核通过（approve）——审核通过只能由人在审核页完成。",
         _obj({"draft_id": _ID, "action": {"type": "string", "enum": list(AGENT_TRANSITIONS)},
               "comment": {"type": "string", "maxLength": 500}}, ["draft_id", "action"]),
         _transition_report, writes=True, normalize=_norm_transition, summarize=_sum_transition),
]}


def run_tool(ctx: AgentContext, name: str, raw_args: Any, *, allowed: frozenset[str] | set[str] | None = None) -> dict:
    """按名称执行工具。allowed 为该角色获授权的工具集合（阶段 2 由 roles.py 提供）；不在其中的工具一律拒绝。"""
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return err("unknown_tool", "未知工具")
    if allowed is not None and name not in allowed:
        return err("tool_not_permitted", f"当前角色无权调用工具 {name}")
    return tool.execute(ctx, raw_args)


def prepare_write(name: str, raw_args: Any) -> PendingAction:
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise ArgumentError("未知工具")
    return tool.prepare(raw_args)


def tool_specs(names: set[str] | frozenset[str] | None = None) -> list[dict]:
    return [t.spec() for n, t in TOOLS.items() if names is None or n in names]
