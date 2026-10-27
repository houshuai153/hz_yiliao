"""Agent 编排核心：总控委派 → 专家调用工具 → 交回结果 → 总控给出最终回答。

全部由服务端执行与记录，模型只“提出”动作：
- 角色权限：总控只能 delegate；专家只能调用白名单内的工具（run_tool 的 allowed）。
- 交接：delegate / 专家交回结果都是服务端步骤；专家的结果以“不可信数据”交给总控。
- 写操作：专家只能提出；服务端要求用户本轮消息明确要求该动作，展示待确认动作并暂停任务，
  由独立的确认请求（绑定登录会话 + 参数哈希 + 有效期）执行；确认后任务从暂停处继续。approve 永远不开放。
- 上限：模型调用轮数、工具调用次数、总耗时、token 用量；达到上限时明确终止并给出服务端摘要。
- 最终回答：模型文字须通过 grounding.validate_answer，否则回退到服务端按规则结果生成的摘要；
  响应里的判定字段始终由服务端从工具结果（app.rules）填入。
- 工具结果与专家输出都是不可信数据，不能改变角色权限、系统规则或上限。
"""
from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from app.agent import grounding
from app.agent.audit import AuditUnavailable, StepRecorder
from app.agent.context import AgentContext, issue_grant
from app.agent.roles import (
    DELEGATE_NAME, DELEGATE_SPEC, ORCHESTRATOR, PROMPTS, ROLE_ZH, role_tool_names, user_requested,
)
from app.agent.schema import (
    ArgumentError, ModelProtocolError, ToolCall, canonical_hash, err, parse_model_message, sanitize_json, sanitize_text,
    validate_args,
)
from app.agent.state import TaskStore, TaskView, utcnow
from app.agent.tools import TOOLS, prepare_write, run_tool, tool_specs
from app.llm.base import provider_of

log = logging.getLogger("app.agent.orchestrator")

TERMINATION_ZH = {"max_rounds": "模型调用轮数", "max_tool_calls": "工具调用次数", "timeout": "总耗时",
                  "token_budget": "token 用量", "confirmation_expired": "待确认动作已过期"}

@dataclass(frozen=True)
class AgentLimits:
    max_rounds: int = 12               # 每条用户消息最多的模型调用次数
    max_tool_calls: int = 16           # 每条用户消息最多的工具调用次数
    max_seconds: float = 90.0          # 每次处理（提交 / 确认续跑）的总耗时上限
    max_tokens: int = 60_000           # 每个任务累计的 token 用量上限
    max_calls_per_turn: int = 4        # 单次模型响应最多的工具调用数
    max_history_messages: int = 40     # 每次发给模型的最近消息条数
    model_max_tokens: int = 1024
    user_message_max: int = 2000
    specialist_result_max: int = 1500
    task_ttl_s: int = 1800             # 任务空闲多久后到期并清理
    pending_ttl_s: int = 300           # 待确认动作的有效期
    grant_ttl_s: int = 120             # 确认后签发的写权限有效期（立即使用）

    def public(self) -> dict:
        return {"max_rounds": self.max_rounds, "max_tool_calls": self.max_tool_calls, "max_seconds": self.max_seconds,
                "max_tokens": self.max_tokens}


class AgentDisabled(Exception):
    """未启用模型（默认 deterministic）。不会进行任何多智能体协作。"""


class TaskError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def client_of(state):
    """模型客户端：测试 / 部署可通过 app.state.agent_client 注入；否则只有启用了在线 provider 才可用。"""
    injected = getattr(state, "agent_client", None)
    if injected is not None:
        return injected
    provider = provider_of(state)
    if not getattr(provider, "is_generative", False) or not hasattr(provider, "chat_with_tools"):
        raise AgentDisabled()
    return provider


def _evidence_sources(ev: dict) -> list[dict]:
    """最终回答涉及的来源编号（本轮触及的项目 / 分析批 / 报告）。"""
    out = [{"type": "project", "id": i} for i in ev.get("touched_projects", [])]
    out += [{"type": "run", "id": i} for i in ev.get("touched_runs", [])]
    out += [{"type": "report", "id": i} for i in ev.get("touched_reports", [])]
    return out


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Orchestrator:
    def __init__(self, state, *, client=None, limits: AgentLimits | None = None, clock: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic):
        self.state = state
        self.client = client if client is not None else client_of(state)
        self.limits = limits or getattr(state, "agent_limits", None) or AgentLimits()
        self.clock, self.mono = clock, monotonic
        self.store = TaskStore(state.session_factory, clock, timedelta(seconds=self.limits.task_ttl_s))
        self.audit = StepRecorder(self.store, state.settings.audit_jsonl_dir, clock)

    @classmethod
    def for_state(cls, state) -> "Orchestrator":
        # agent_clock / agent_monotonic / agent_limits 只供测试注入；生产环境不设置，使用真实时钟与默认上限
        return cls(state, clock=getattr(state, "agent_clock", None) or utcnow,
                   monotonic=getattr(state, "agent_monotonic", None) or time.monotonic)

    # ==================== 对外操作 ====================
    def submit(self, username: str, session_key: str, message: str, task_id: str | None = None,
               request_id: str | None = None) -> dict:
        self.store.cleanup()
        text, _ = sanitize_text(message, max_len=self.limits.user_message_max)
        if not text.strip():
            raise TaskError(422, "empty_message", "消息不能为空")
        if task_id is None:
            tid = self.store.create(username, session_key)
        else:
            tv = self.store.get(task_id, username, session_key)
            if tv is None:
                raise TaskError(404, "task_not_found", "任务不存在")
            if tv.status == "expired":
                raise TaskError(409, "task_expired", "任务已过期，请新建任务")
            if tv.status in ("running", "awaiting_confirmation"):
                raise TaskError(409, "task_busy", "任务正在处理或在等待你确认待执行的操作")
            if tv.status != "idle":
                raise TaskError(409, "task_closed", "该任务已结束，请新建任务")
            tid = task_id
        if not self.store.claim(tid, ("idle",), "running"):
            raise TaskError(409, "task_busy", "任务正在处理")
        ev = self.store.view(tid).evidence or grounding.new_evidence()
        grounding.begin_turn(ev)
        self.store.update(tid, user_message=text, user_message_id=f"um-{uuid.uuid4().hex[:16]}", run_rounds=0,
                          run_tool_calls=0, active_role=ORCHESTRATOR, active_frame="root", delegate_call_id=None,
                          evidence_json=ev, final_json=None, termination_reason=None, error_code=None)
        self.store.add_message(tid, "root", ORCHESTRATOR, "user", content=text)
        self._step(tid, "user_message", ORCHESTRATOR, username, request_id, status="ok", summary="用户消息", content=text)
        self._drive(tid, username, session_key, request_id)
        return self.snapshot(username, session_key, tid)

    def confirm(self, username: str, session_key: str, task_id: str, action_id: str, args_hash: str,
                request_id: str | None = None) -> dict:
        tv, act = self._owned_action(username, session_key, task_id, action_id)
        if act["status"] == "executed":                                   # 重复确认：幂等，返回当前状态
            self._step(task_id, "confirmation", act["agent_role"], username, request_id, tool_name=act["tool"],
                       status="replayed", summary="重复确认（幂等，未重复执行）", args=act["args"])
            return self.snapshot(username, session_key, task_id, replayed=True)
        self._require_pending(act)
        if not hmac.compare_digest(str(args_hash), act["args_hash"]):
            raise TaskError(409, "args_mismatch", "确认的内容与待执行的操作不一致")
        if canonical_hash(act["tool"], act["args"]) != act["args_hash"]:
            raise TaskError(409, "action_tampered", "待执行动作的参数校验失败，已拒绝")
        if self.clock() >= act["expires_at"]:
            self.store.claim_pending(action_id, "pending", "expired")
            self._terminate(task_id, username, "confirmation_expired", request_id)
            raise TaskError(409, "action_expired", "待执行动作已过期，请重新发起")
        if tv.status != "awaiting_confirmation":
            raise TaskError(409, "task_not_waiting", "任务当前没有等待确认的操作")
        if not self.store.claim_pending(action_id, "pending", "confirming"):
            raise TaskError(409, "action_in_progress", "该操作正在被确认或已处理")
        if not self.store.claim(task_id, ("awaiting_confirmation",), "running"):
            raise TaskError(409, "task_busy", "任务正在处理")
        try:                                                              # 先审计、后执行（fail-closed）：留不下痕就不执行
            self._step(task_id, "confirmation", act["agent_role"], username, request_id, tool_name=act["tool"],
                       status="accepted", summary="用户确认待执行操作", args=act["args"], content=act["args_hash"],
                       required=True)
        except AuditUnavailable:
            self.store.claim_pending(action_id, "confirming", "pending")
            self.store.claim(task_id, ("running",), "awaiting_confirmation")
            raise TaskError(503, "audit_unavailable", "审计暂时不可用，操作未执行；请稍后重试确认") from None
        grant = issue_grant(tool=act["tool"], args_hash=act["args_hash"], operator=username, session_key=session_key,
                            user_message_id=tv.user_message_id, ttl_s=self.limits.grant_ttl_s, now=self.clock(),
                            idempotency_key=f"act-{action_id}", confirmation_id=f"cf-{action_id}")
        ctx = AgentContext(state=self.state, operator=username, session_key=session_key, grant=grant, clock=self.clock)
        result = run_tool(ctx, act["tool"], act["args"], allowed=role_tool_names(act["agent_role"]))
        self.store.finish_pending(action_id, "executed", result)
        self._step(task_id, "write_result", act["agent_role"], username, request_id, tool_name=act["tool"],
                   status="ok" if result.get("ok") else result.get("error_code", "error"), summary="写操作执行结果",
                   args=act["args"], sources=result.get("sources") if result.get("ok") else None, content=result)
        self._record_tool_result(task_id, act["frame_id"], act["agent_role"], act["tool_call_id"], act["tool"], result,
                                 absorb=True)
        self._drive(task_id, username, session_key, request_id)
        return self.snapshot(username, session_key, task_id)

    def reject(self, username: str, session_key: str, task_id: str, action_id: str,
               request_id: str | None = None) -> dict:
        tv, act = self._owned_action(username, session_key, task_id, action_id)
        if act["status"] == "rejected":
            self._step(task_id, "confirmation", act["agent_role"], username, request_id, tool_name=act["tool"],
                       status="replayed", summary="重复拒绝（幂等）", args=act["args"])
            return self.snapshot(username, session_key, task_id, replayed=True)
        self._require_pending(act)
        if tv.status != "awaiting_confirmation":
            raise TaskError(409, "task_not_waiting", "任务当前没有等待确认的操作")
        if not self.store.claim_pending(action_id, "pending", "rejected"):
            raise TaskError(409, "action_in_progress", "该操作已被处理")
        if not self.store.claim(task_id, ("awaiting_confirmation",), "running"):
            raise TaskError(409, "task_busy", "任务正在处理")
        result = err("user_rejected", "用户拒绝执行该操作")
        self.store.finish_pending(action_id, "rejected", result)
        self._step(task_id, "confirmation", act["agent_role"], username, request_id, tool_name=act["tool"],
                   status="rejected", summary="用户拒绝了待执行的操作", args=act["args"], content=act["args_hash"])
        self._record_tool_result(task_id, act["frame_id"], act["agent_role"], act["tool_call_id"], act["tool"], result)
        self._drive(task_id, username, session_key, request_id)
        return self.snapshot(username, session_key, task_id)

    def snapshot(self, username: str, session_key: str, task_id: str, *, replayed: bool = False) -> dict:
        tv = self.store.get(task_id, username, session_key)
        if tv is None:
            raise TaskError(404, "task_not_found", "任务不存在")
        pend = [{"action_id": p["action_id"], "tool": p["tool"], "summary": p["summary"],
                 "args": sanitize_json(p["args"], max_len=500), "args_hash": p["args_hash"], "status": p["status"],
                 "expires_at": p["expires_at"].isoformat()} for p in self.store.pending(task_id)
                if p["status"] in ("pending", "confirming")]
        out = {"task_id": tv.id, "status": tv.status, "active_role": tv.active_role,
               "active_role_zh": ROLE_ZH.get(tv.active_role, tv.active_role), "created_at": tv.created_at.isoformat(),
               "updated_at": tv.updated_at.isoformat(), "expires_at": tv.expires_at.isoformat(), "final": tv.final,
               "conversation": self.store.conversation(task_id), "pending_actions": pend,
               "steps": self.store.steps(task_id),
               "usage": {"rounds": tv.run_rounds, "tool_calls": tv.run_tool_calls, "tokens_used": tv.tokens_used,
                         "limits": self.limits.public()},
               "termination_reason": tv.termination_reason, "error_code": tv.error_code,
               "audit_complete": tv.audit_complete,
               "audit_note": None if tv.audit_complete else "部分步骤未能写入审计，留痕不完整"}
        if replayed:
            out["replayed"] = True
        return out

    def list_tasks(self, username: str, session_key: str) -> list[dict]:
        self.store.cleanup()
        return self.store.list_tasks(username, session_key)

    def trace(self, username: str, session_key: str, task_id: str) -> dict:
        """审计视图（仅任务所有者）：每一步的 request_id / step_id / 操作人 / 内容哈希，以及对应的 HTTP 请求审计。"""
        tv = self.store.get(task_id, username, session_key)
        if tv is None:
            raise TaskError(404, "task_not_found", "任务不存在")
        steps = self.store.steps(task_id, full=True)
        rids = sorted({s["request_id"] for s in steps if s["request_id"]})
        return {"task_id": task_id, "status": tv.status, "audit_complete": tv.audit_complete, "verdict_source": "app.rules",
                "steps": steps, "http_audit": self.store.http_audit(rids),
                "content_policy": "审计只保存白名单参数摘要、来源编号和内容哈希；不保存提示词、模型输出、工具结果或用户消息正文"}

    def _step(self, tid: str, kind: str, role: str, username: str, request_id: str | None, **kw) -> int | None:
        """写审计步骤。required=True 的失败会抛 AuditUnavailable；其它失败不阻断任务，但把任务标记为审计不完整。"""
        sid = self.audit.record(tid, kind, role, operator=username, request_id=request_id, **kw)
        if sid is None:
            self.store.mark_audit_incomplete(tid)
        return sid

    # ==================== 内部：确认相关 ====================
    def _owned_action(self, username: str, session_key: str, task_id: str, action_id: str) -> tuple[TaskView, dict]:
        tv = self.store.get(task_id, username, session_key)
        if tv is None:
            raise TaskError(404, "task_not_found", "任务不存在")
        acts = self.store.pending(task_id, action_id)
        if not acts:
            raise TaskError(404, "action_not_found", "待执行动作不存在")
        return tv, acts[0]

    @staticmethod
    def _require_pending(act: dict) -> None:
        if act["status"] == "pending":
            return
        code = {"confirming": "action_in_progress", "rejected": "action_rejected", "expired": "action_expired"}
        raise TaskError(409, code.get(act["status"], "action_not_pending"), f"该操作状态为 {act['status']}，不能再确认")

    # ==================== 内部：主循环 ====================
    def _drive(self, tid: str, username: str, session_key: str, request_id: str | None) -> None:
        deadline = self.mono() + self.limits.max_seconds
        try:
            self._loop(tid, username, session_key, request_id, deadline)
        except Exception as exc:  # noqa: BLE001  编排自身出错：任务置为 failed，不留在 running，也不把异常正文暴露出去
            log.warning("agent drive failed: %s", type(exc).__name__)
            self._fail(tid, username, "internal_error", "Agent 处理出错，已中止", request_id)

    def _loop(self, tid: str, username: str, session_key: str, request_id: str | None, deadline: float) -> None:
        lim = self.limits
        while True:
            t = self.store.view(tid)
            reason = (("max_rounds" if t.run_rounds >= lim.max_rounds else None)
                      or ("token_budget" if t.tokens_used >= lim.max_tokens else None)
                      or ("timeout" if self.mono() >= deadline else None))
            if reason:
                return self._terminate(tid, username, reason, request_id)
            role, frame = t.active_role, t.active_frame
            messages = [{"role": "system", "content": PROMPTS[role]}] + self.store.messages(
                tid, frame, limit=lim.max_history_messages)
            tools = [DELEGATE_SPEC] if role == ORCHESTRATOR else tool_specs(role_tool_names(role))
            try:
                raw = self.client.chat_with_tools(messages, tools, max_tokens=lim.model_max_tokens)
            except Exception as exc:  # noqa: BLE001  模型 / 网络错误：只记录类型
                log.warning("agent model call failed: %s", type(exc).__name__)
                return self._fail(tid, username, "model_unavailable", "模型调用失败，任务已中止", request_id)
            try:
                turn = parse_model_message(raw, max_calls=lim.max_calls_per_turn)
            except ModelProtocolError as exc:
                self._step(tid, "model_call", role, username, request_id, status="rejected", model_status="rejected",
                           summary=f"模型响应被拒绝：{exc.message}")
                return self._fail(tid, username, "invalid_model_output", "模型返回了不符合协议的响应，已拒绝并中止", request_id)
            used = turn.total_tokens if turn.total_tokens is not None else len(_dumps(messages)) // 3
            self.store.update(tid, run_rounds=t.run_rounds + 1, tokens_used=t.tokens_used + used)
            digest = ([{"name": c.name, "args": sanitize_json(c.args, max_len=500)} for c in turn.calls] if turn.calls
                      else sanitize_text(turn.content or "", max_len=grounding.MAX_ANSWER + 200)[0])
            self._step(tid, "model_call", role, username, request_id, status="ok", model_status="ok", tokens=used,
                       summary=f"{ROLE_ZH[role]}第 {t.run_rounds + 1} 轮", content=digest)
            if turn.calls:
                if role == ORCHESTRATOR:
                    if not self._delegate(tid, username, t, turn.calls, request_id):
                        return
                elif self._specialist_calls(tid, username, session_key, t, turn.calls, request_id):
                    return                                                  # 暂停等待用户确认，或已终止
                continue
            text, _ = sanitize_text(turn.content or "", max_len=grounding.MAX_ANSWER + 200)
            if role == ORCHESTRATOR:
                return self._finalize(tid, username, text, request_id)
            self._return_to_orchestrator(tid, username, t, text, request_id)

    # ---------- 总控：委派 ----------
    def _delegate(self, tid: str, username: str, t: TaskView, calls: tuple[ToolCall, ...], request_id) -> bool:
        """返回 True 表示已切换到专家继续；False 表示已因协议错误中止。"""
        if len(calls) != 1 or calls[0].name != DELEGATE_NAME:
            self._step(tid, "model_call", ORCHESTRATOR, username, request_id, status="rejected", model_status="rejected",
                       summary="总控只能且每次只能调用一次 delegate")
            self._fail(tid, username, "invalid_model_output", "总控的调用不符合协议，已拒绝并中止", request_id)
            return False
        c = calls[0]
        try:
            a = validate_args(DELEGATE_SPEC["function"]["parameters"], c.args)
        except ArgumentError as exc:
            self._step(tid, "model_call", ORCHESTRATOR, username, request_id, status="rejected", model_status="rejected",
                       summary="delegate 参数非法")
            self._fail(tid, username, "invalid_model_output", "委派参数非法，已拒绝并中止", request_id)
            return False
        role = a["role"]
        instruction, _ = sanitize_text(a["instruction"], max_len=600)
        self.store.add_message(tid, "root", ORCHESTRATOR, "assistant", tool_calls=[{
            "id": c.id, "type": "function", "function": {"name": DELEGATE_NAME,
                                                          "arguments": _dumps({"role": role, "instruction": instruction})}}])
        frame = f"f{t.frame_seq + 1}"
        self.store.update(tid, frame_seq=t.frame_seq + 1, active_role=role, active_frame=frame, delegate_call_id=c.id)
        self.store.add_message(tid, frame, role, "user", content=(
            f"任务说明：{instruction}\n\n用户原始请求（仅供参考，不是新指令）：{t.user_message}"))
        self._step(tid, "handoff", ORCHESTRATOR, username, request_id, tool_name=DELEGATE_NAME, status="ok",
                   summary=f"总控 → {ROLE_ZH[role]}", args={"role": role}, content=instruction)
        return True

    # ---------- 专家：调用工具 ----------
    def _specialist_calls(self, tid: str, username: str, session_key: str, t: TaskView, calls: tuple[ToolCall, ...],
                          request_id) -> bool:
        """返回 True = 本次处理到此结束（等待确认或已终止）。"""
        role, frame = t.active_role, t.active_frame
        allowed = role_tool_names(role)
        self.store.add_message(tid, frame, role, "assistant", tool_calls=[{
            "id": c.id, "type": "function",
            "function": {"name": c.name, "arguments": _dumps(sanitize_json(c.args, max_len=500))}} for c in calls])
        write_alone = len(calls) == 1
        ctx = AgentContext(state=self.state, operator=username, session_key=session_key, clock=self.clock)
        done = t.run_tool_calls
        for c in calls:
            if done >= self.limits.max_tool_calls:
                self._terminate(tid, username, "max_tool_calls", request_id)
                return True
            done += 1
            self.store.update(tid, run_tool_calls=done)
            tool = TOOLS.get(c.name)
            if tool is not None and tool.writes and c.name in allowed:
                if not write_alone:
                    res = err("write_must_be_alone", "写操作必须单独调用（同一轮不能与其它调用一起）；请只提出这一个写操作")
                    self._record_tool_result(tid, frame, role, c.id, c.name, res)
                    self._tool_step(tid, role, username, request_id, c.name, c.args, res)
                    continue
                return self._propose_write(tid, username, t, c, request_id)
            res = run_tool(ctx, c.name, c.args, allowed=allowed)       # 未知 / 越权 / 参数非法都由它统一拒绝
            self._record_tool_result(tid, frame, role, c.id, c.name, res, absorb=True)
            self._tool_step(tid, role, username, request_id, c.name, c.args, res)
        return False

    def _propose_write(self, tid: str, username: str, t: TaskView, c: ToolCall, request_id) -> bool:
        role, frame = t.active_role, t.active_frame
        try:
            pending = prepare_write(c.name, c.args)
        except ArgumentError as exc:
            res = err("invalid_arguments", str(exc))
            self._record_tool_result(tid, frame, role, c.id, c.name, res)
            self._tool_step(tid, role, username, request_id, c.name, c.args, res)
            return False
        if not user_requested(c.name, pending.args, t.user_message):
            res = err("user_intent_missing", "用户本轮消息没有明确要求该操作，不能提出；如有需要，请在回答里建议用户明确提出")
            self._record_tool_result(tid, frame, role, c.id, c.name, res)
            self._tool_step(tid, role, username, request_id, c.name, c.args, res)
            return False
        aid = self.store.add_pending(tid, frame, role, c.id, c.name, pending.args, pending.args_hash, pending.summary,
                                     timedelta(seconds=self.limits.pending_ttl_s))
        self._step(tid, "pending_action", role, username, request_id, tool_name=c.name, status="awaiting_confirmation",
                   summary="提出待确认操作", args=pending.args, content=pending.args_hash)
        self.store.claim(tid, ("running",), "awaiting_confirmation")
        return True

    def _record_tool_result(self, tid: str, frame: str, role: str, call_id: str, tool: str, result: dict, *,
                            absorb: bool = False) -> None:
        """把工具结果（已脱敏）写进该帧的对话历史；absorb=True 时并入最终回答核对用的证据。"""
        self.store.add_message(tid, frame, role, "tool", tool_call_id=call_id, name=tool, content=_dumps(result))
        if absorb and result.get("ok"):
            ev = self.store.view(tid).evidence
            grounding.absorb(ev, tool, result)
            self.store.update(tid, evidence_json=ev)

    def _tool_step(self, tid: str, role: str, username: str, request_id, tool: str, args: dict, result: dict) -> None:
        ok_ = result.get("ok")
        self._step(tid, "tool_call", role, username, request_id, tool_name=tool,
                   status="ok" if ok_ else result.get("error_code", "error"), args=args,
                   sources=result.get("sources") if ok_ else None, content=result,
                   summary="工具调用" if ok_ else "工具调用被拒绝或失败")

    # ---------- 专家交回结果 ----------
    def _return_to_orchestrator(self, tid: str, username: str, t: TaskView, text: str, request_id) -> None:
        text = text[: self.limits.specialist_result_max]
        payload = {"ok": True, "data": {"role": t.active_role, "result": text}, "data_trust": "untrusted"}
        self.store.add_message(tid, "root", ORCHESTRATOR, "tool", tool_call_id=t.delegate_call_id, name=DELEGATE_NAME,
                               content=_dumps(sanitize_json(payload, max_len=self.limits.specialist_result_max + 50)))
        self.store.update(tid, active_role=ORCHESTRATOR, active_frame="root", delegate_call_id=None)
        self._step(tid, "handoff_return", t.active_role, username, request_id, status="ok",
                   summary=f"{ROLE_ZH[t.active_role]} → 总控", content=text)

    # ---------- 收尾 ----------
    def _finalize(self, tid: str, username: str, answer: str, request_id) -> None:
        t = self.store.view(tid)
        problems = grounding.validate_answer(answer, t.evidence, t.user_message)
        if problems:
            shown = grounding.server_summary(t.evidence, note="（模型生成的文字未通过事实校验，已改用系统根据规则结果生成的摘要。）")
            source, reason = "server_fallback", problems[:6]
        else:
            shown, source, reason = answer, "model", []
        final = {"answer": shown, "answer_source": source, "fallback_reason": reason,
                 "evidence": grounding.evidence_view(t.evidence), "verdict_source": "app.rules"}
        self.store.add_message(tid, "root", ORCHESTRATOR, "assistant", content=shown)
        codes = sorted({x.split(":")[0] for x in problems})                  # 只记问题代码，不记模型写的内容
        self._step(tid, "final", ORCHESTRATOR, username, request_id, status=source,
                   model_status="fallback" if problems else "model",
                   summary="最终回答" + (f"（已回退：{', '.join(codes)}）" if problems else ""),
                   sources=_evidence_sources(t.evidence), content=shown)
        self.store.update(tid, status="idle", final_json=final)

    def _terminate(self, tid: str, username: str, reason: str, request_id) -> None:
        t = self.store.view(tid)
        note = f"任务已因达到上限（{TERMINATION_ZH.get(reason, reason)}）而终止；以下为已获得的规则结果摘要。"
        final = {"answer": grounding.server_summary(t.evidence, note=note), "answer_source": "server_summary",
                 "fallback_reason": [reason], "evidence": grounding.evidence_view(t.evidence), "verdict_source": "app.rules"}
        self._step(tid, "terminated", t.active_role, username, request_id, status=reason, summary=note,
                   sources=_evidence_sources(t.evidence))
        self.store.update(tid, status="terminated", final_json=final, termination_reason=reason)

    def _fail(self, tid: str, username: str, code: str, message: str, request_id) -> None:
        t = self.store.view(tid)
        final = {"answer": grounding.server_summary(t.evidence, note=f"{message}。"), "answer_source": "server_summary",
                 "fallback_reason": [code], "evidence": grounding.evidence_view(t.evidence), "verdict_source": "app.rules"}
        self._step(tid, "error", t.active_role, username, request_id, status=code, summary=message)
        self.store.update(tid, status="failed", final_json=final, error_code=code)
