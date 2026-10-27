"""Agent 会话状态存储（数据库）：任务、对话历史、步骤、待确认动作。

- 会话绑定登录用户和登录会话：不是该用户 / 会话创建的任务一律“不存在”（不区分，避免泄露）。
- 状态变更用带条件的 UPDATE（claim），同一任务 / 同一待确认动作并发时只有一个赢家。
- 到期清理：到期的任务标记 expired 并清空对话历史与证据；保留更久的步骤记录供排查，超过留存期后整体删除。
- 对话历史存的是**已脱敏、限长**的内容（与发给模型的一致），不含密钥。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete, select, update

from app.audit.models import AuditLog
from app.db.models import AgentMessage, AgentPendingAction, AgentStep, AgentTask

ACTIVE_STATUSES = ("running", "awaiting_confirmation", "idle")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class TaskView:
    id: str
    username: str
    session_key: str
    status: str
    active_role: str
    active_frame: str
    delegate_call_id: str | None
    frame_seq: int
    user_message: str
    user_message_id: str
    run_rounds: int
    run_tool_calls: int
    tokens_used: int
    evidence: dict
    final: dict | None
    termination_reason: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    audit_complete: bool = True


def _view(t: AgentTask) -> TaskView:
    return TaskView(t.id, t.username, t.session_key, t.status, t.active_role, t.active_frame, t.delegate_call_id,
                    t.frame_seq, t.user_message, t.user_message_id, t.run_rounds, t.run_tool_calls, t.tokens_used,
                    dict(t.evidence_json or {}), dict(t.final_json) if t.final_json else None, t.termination_reason,
                    t.error_code, aware(t.created_at), aware(t.updated_at), aware(t.expires_at),
                    t.audit_complete is not False)


@dataclass
class TaskStore:
    session_factory: Any
    clock: Callable[[], datetime] = utcnow
    ttl: timedelta = timedelta(minutes=30)
    purge_after: timedelta = timedelta(days=7)

    # ---------- 任务 ----------
    def create(self, username: str, session_key: str) -> str:
        now = self.clock()
        tid = str(uuid.uuid4())
        with self.session_factory() as s:
            s.add(AgentTask(id=tid, username=username, session_key=session_key, status="idle", created_at=now,
                            updated_at=now, expires_at=now + self.ttl, evidence_json={}))
            s.commit()
        return tid

    def view(self, task_id: str) -> TaskView:
        """内部读取（编排核心用，不做所有者检查；对外一律走 get）。"""
        with self.session_factory() as s:
            return _view(s.get(AgentTask, task_id))

    def get(self, task_id: str, username: str, session_key: str) -> TaskView | None:
        """按所有者取任务；到期则就地标记 expired 并清理。不属于该用户 / 会话返回 None。"""
        with self.session_factory() as s:
            t = s.get(AgentTask, task_id)
            if t is None or t.username != username or t.session_key != session_key:
                return None
            if t.status != "expired" and aware(t.expires_at) <= self.clock():
                self._expire(s, t)
                s.commit()
            return _view(t)

    def _expire(self, s, t: AgentTask) -> None:
        t.status, t.evidence_json, t.final_json = "expired", {}, None
        s.execute(delete(AgentMessage).where(AgentMessage.task_id == t.id))
        s.execute(update(AgentPendingAction).where(AgentPendingAction.task_id == t.id,
                                                   AgentPendingAction.status.in_(("pending", "confirming")))
                  .values(status="expired", decided_at=self.clock()))

    def update(self, task_id: str, **fields: Any) -> None:
        with self.session_factory() as s:
            t = s.get(AgentTask, task_id)
            for k, v in fields.items():
                setattr(t, k, v)
            now = self.clock()
            t.updated_at = now
            if t.status in ACTIVE_STATUSES:                   # 有活动就顺延到期时间
                t.expires_at = now + self.ttl
            s.commit()

    def claim(self, task_id: str, from_statuses: tuple[str, ...], to_status: str) -> bool:
        """原子地把任务从 from_statuses 之一切到 to_status；只有一个并发调用能成功。"""
        with self.session_factory() as s:
            n = s.execute(update(AgentTask).where(AgentTask.id == task_id, AgentTask.status.in_(from_statuses))
                          .values(status=to_status, updated_at=self.clock())).rowcount
            s.commit()
            return n == 1

    def list_tasks(self, username: str, session_key: str, limit: int = 20) -> list[dict]:
        """当前用户在当前登录会话下、尚未到期的任务（最近的在前）。只给任务号、状态、时间和本轮消息的前 60 个字。"""
        now = self.clock()
        with self.session_factory() as s:
            rows = s.scalars(select(AgentTask).where(AgentTask.username == username, AgentTask.session_key == session_key,
                                                     AgentTask.status != "expired", AgentTask.expires_at > now)
                             .order_by(AgentTask.updated_at.desc()).limit(limit)).all()
        return [{"task_id": t.id, "status": t.status, "created_at": aware(t.created_at).isoformat(),
                 "updated_at": aware(t.updated_at).isoformat(), "preview": (t.user_message or "")[:60]} for t in rows]

    def conversation(self, task_id: str, limit: int = 40) -> list[dict]:
        """给任务使用者看的对话：只有用户消息和用户已经看到的最终回答。不含系统提示、工具调用、工具结果、专家的内部对话。"""
        with self.session_factory() as s:
            rows = s.scalars(select(AgentMessage).where(AgentMessage.task_id == task_id, AgentMessage.frame_id == "root")
                             .order_by(AgentMessage.id)).all()
        out = [{"role": m.role, "content": m.content or ""} for m in rows
               if m.role == "user" or (m.role == "assistant" and not m.tool_calls_json and m.content)]
        return out[-limit:]

    # ---------- 对话历史 ----------
    def add_message(self, task_id: str, frame_id: str, agent_role: str, role: str, *, content: str | None = None,
                    tool_call_id: str | None = None, name: str | None = None, tool_calls: list | None = None) -> None:
        with self.session_factory() as s:
            s.add(AgentMessage(task_id=task_id, frame_id=frame_id, agent_role=agent_role, role=role, content=content,
                               tool_call_id=tool_call_id, name=name, tool_calls_json=tool_calls, created_at=self.clock()))
            s.commit()

    def messages(self, task_id: str, frame_id: str, *, limit: int | None = None) -> list[dict]:
        """某个调用帧的对话历史，OpenAI 消息格式；limit 取最近若干条（并保证不从孤立的 tool 消息开始）。"""
        with self.session_factory() as s:
            rows = s.scalars(select(AgentMessage).where(AgentMessage.task_id == task_id, AgentMessage.frame_id == frame_id)
                             .order_by(AgentMessage.id)).all()
        out: list[dict] = []
        for m in rows:
            if m.role == "assistant" and m.tool_calls_json:
                out.append({"role": "assistant", "content": None, "tool_calls": m.tool_calls_json})
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
            else:
                out.append({"role": m.role, "content": m.content or ""})
        if limit and len(out) > limit:
            out = out[-limit:]
            while out and out[0]["role"] == "tool":
                out.pop(0)
        return out

    # ---------- 步骤（审计）----------
    def add_step(self, task_id: str, kind: str, agent_role: str, *, tool_name: str | None = None,
                 status: str | None = None, summary: str = "", request_id: str | None = None, operator: str = "",
                 args_summary: str | None = None, source_ids: list[str] | None = None,
                 content_sha256: str | None = None, model_status: str | None = None, tokens: int | None = None) -> int:
        with self.session_factory() as s:
            st = AgentStep(task_id=task_id, kind=kind, agent_role=agent_role, tool_name=tool_name, status=status,
                           summary=summary[:300], request_id=request_id, operator=operator, args_summary=args_summary,
                           source_ids=source_ids, content_sha256=content_sha256, model_status=model_status,
                           tokens=tokens, created_at=self.clock())
            s.add(st)
            s.commit()
            return st.id

    def steps(self, task_id: str, limit: int = 300, *, full: bool = False) -> list[dict]:
        """full=False：给任务使用者看的公开视图（不含哈希 / 请求号 / 操作人）；full=True：审计视图。"""
        with self.session_factory() as s:
            rows = s.scalars(select(AgentStep).where(AgentStep.task_id == task_id).order_by(AgentStep.id).limit(limit)).all()
        out = []
        for r in rows:
            d = {"step_id": r.id, "kind": r.kind, "agent_role": r.agent_role, "tool_name": r.tool_name, "status": r.status,
                 "summary": r.summary, "args_summary": r.args_summary, "sources": r.source_ids or [],
                 "model_status": r.model_status, "created_at": aware(r.created_at).isoformat()}
            if full:
                d.update({"request_id": r.request_id, "operator": r.operator, "content_sha256": r.content_sha256,
                          "tokens": r.tokens})
            out.append(d)
        return out

    def http_audit(self, request_ids: list[str]) -> list[dict]:
        """与步骤同一个 request_id 的 HTTP 请求审计（audit_log）。"""
        if not request_ids:
            return []
        with self.session_factory() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.request_id.in_(request_ids)).order_by(AuditLog.id)).all()
        return [{"request_id": r.request_id, "ts": aware(r.ts).isoformat(), "feature": r.feature, "operator": r.operator,
                 "status_code": r.status_code, "agent_task_id": r.agent_task_id} for r in rows]

    def mark_audit_incomplete(self, task_id: str) -> None:
        try:
            with self.session_factory() as s:
                t = s.get(AgentTask, task_id)
                if t is not None:
                    t.audit_complete = False
                    s.commit()
        except Exception:  # noqa: BLE001  标志本身也写不进去（数据库整体不可用）：调用方已在进程内记录，且请求会失败
            pass

    # ---------- 待确认动作 ----------
    def add_pending(self, task_id: str, frame_id: str, agent_role: str, tool_call_id: str, tool: str, args: dict,
                    args_hash: str, summary: str, ttl: timedelta) -> str:
        now = self.clock()
        aid = str(uuid.uuid4())
        with self.session_factory() as s:
            s.add(AgentPendingAction(id=aid, task_id=task_id, frame_id=frame_id, agent_role=agent_role,
                                     tool_call_id=tool_call_id, tool=tool, args_json=args, args_hash=args_hash,
                                     summary=summary, status="pending", created_at=now, expires_at=now + ttl))
            s.commit()
        return aid

    def pending(self, task_id: str, action_id: str | None = None) -> list[dict]:
        with self.session_factory() as s:
            q = select(AgentPendingAction).where(AgentPendingAction.task_id == task_id)
            if action_id:
                q = q.where(AgentPendingAction.id == action_id)
            rows = s.scalars(q.order_by(AgentPendingAction.created_at)).all()
        return [{"action_id": r.id, "frame_id": r.frame_id, "agent_role": r.agent_role, "tool_call_id": r.tool_call_id,
                 "tool": r.tool, "args": r.args_json, "args_hash": r.args_hash, "summary": r.summary, "status": r.status,
                 "created_at": aware(r.created_at), "expires_at": aware(r.expires_at), "result": r.result_json}
                for r in rows]

    def claim_pending(self, action_id: str, from_status: str, to_status: str) -> bool:
        with self.session_factory() as s:
            n = s.execute(update(AgentPendingAction).where(AgentPendingAction.id == action_id,
                                                           AgentPendingAction.status == from_status)
                          .values(status=to_status, decided_at=self.clock())).rowcount
            s.commit()
            return n == 1

    def finish_pending(self, action_id: str, status: str, result: dict | None) -> None:
        with self.session_factory() as s:
            a = s.get(AgentPendingAction, action_id)
            a.status, a.result_json, a.decided_at = status, result, self.clock()
            s.commit()

    # ---------- 清理 ----------
    def cleanup(self) -> dict:
        """到期任务：清空对话与证据并标记 expired；超过留存期的任务整体删除（含步骤、待确认动作）。"""
        now = self.clock()
        expired = purged = 0
        with self.session_factory() as s:
            for t in s.scalars(select(AgentTask).where(AgentTask.status != "expired", AgentTask.expires_at <= now)).all():
                self._expire(s, t)
                expired += 1
            old = [t.id for t in s.scalars(select(AgentTask).where(AgentTask.expires_at <= now - self.purge_after)).all()]
            for tid in old:
                for model in (AgentMessage, AgentStep, AgentPendingAction):
                    s.execute(delete(model).where(model.task_id == tid))
                s.execute(delete(AgentTask).where(AgentTask.id == tid))
                purged += 1
            s.commit()
        return {"expired": expired, "purged": purged}
