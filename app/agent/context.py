"""Agent 工具执行上下文：共享依赖 + 服务端可信的身份与写权限。

信任边界（阶段 1 起生效）：
- operator 与 session_key 只能来自登录会话（`AgentContext.from_request` 取 operator_name / session_key_of）。
  静态 Bearer 令牌没有操作人，会被拒绝；模型参数、前端自报的 X-Operator 都不会进入这里。
- 写工具需要 WriteGrant：由服务端在“用户独立确认”之后签发，绑定工具名、参数哈希、操作人、会话与有效期。
  模型的工具参数里不能携带这些值（工具 JSON Schema 禁止额外字段）。阶段 1 没有确认流程，
  所以 ctx.grant 默认为 None，写工具一律拒绝；`issue_grant` 供阶段 2 的确认接口与测试使用。
- 依赖（rule_sets / assistant / translation_service）按需从 app.state 取，复用现有实现。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.api.auth import operator_name, session_key_of
from app.deps import assistant_of, rule_sets_of, translation_service_of

DEFAULT_GRANT_TTL_S = 300


class AgentAuthError(Exception):
    """没有可信的登录会话（例如只带了静态 Bearer 令牌）。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WriteGrant:
    """用户对“某个工具 + 某组参数”的一次性确认。只能由服务端构造。"""
    confirmation_id: str        # 服务端生成的确认标识（一次确认只能占用一次）
    tool: str
    args_hash: str              # app.agent.schema.canonical_hash(tool, 规范化后的参数)
    operator: str
    session_key: str
    expires_at: datetime
    idempotency_key: str
    user_message_id: str        # 触发这次待确认动作的用户消息（服务端记录，用于审计）


def issue_grant(*, tool: str, args_hash: str, operator: str, session_key: str, user_message_id: str,
                ttl_s: int = DEFAULT_GRANT_TTL_S, now: datetime | None = None,
                idempotency_key: str | None = None, confirmation_id: str | None = None) -> WriteGrant:
    """【仅服务端】在用户独立确认之后签发。不是工具，不暴露给模型。"""
    now = now or _utcnow()
    return WriteGrant(
        confirmation_id=confirmation_id or secrets.token_urlsafe(18), tool=tool, args_hash=args_hash,
        operator=operator, session_key=session_key, expires_at=now + timedelta(seconds=ttl_s),
        idempotency_key=idempotency_key or secrets.token_urlsafe(18), user_message_id=user_message_id)


@dataclass
class AgentContext:
    state: Any                                   # app.state
    operator: str                                # 登录用户名（可信）
    session_key: str | None = None               # 登录会话标识（可信）
    grant: WriteGrant | None = None              # 写权限；None = 不允许任何写操作
    clock: Callable[[], datetime] = field(default=_utcnow, repr=False)

    def __post_init__(self) -> None:
        self.operator = (self.operator or "").strip()[:64]

    # ---- 共享依赖（按需创建，复用现有实现）----
    @property
    def settings(self):
        return self.state.settings

    @property
    def session_factory(self):
        return self.state.session_factory

    @property
    def rule_sets(self):
        return rule_sets_of(self.state)

    @property
    def assistant(self):
        return assistant_of(self.state)

    @property
    def translation_service(self):
        return translation_service_of(self.state)

    @classmethod
    def from_request(cls, request, *, grant: WriteGrant | None = None) -> "AgentContext":
        """只接受浏览器登录会话；操作人只取 operator_name(request)。"""
        op, key = operator_name(request), session_key_of(request)
        if not op or not key or op == "anonymous":
            raise AgentAuthError("Agent 只接受浏览器登录会话")
        return cls(state=request.app.state, operator=op, session_key=key, grant=grant)
