"""Agent 最小 API：提交消息、读取任务状态、确认 / 拒绝待执行的写操作。

- 只接受浏览器登录会话（require_token 校验会话 + 写请求 CSRF；require_agent_session 拒绝静态 Bearer）。
- 操作人只取 operator_name(request)；请求体里没有任何身份 / 确认令牌字段。
- 默认 deterministic：返回 409 llm_not_enabled，不创建任务，不联网。
- 确认是独立请求，必须带用户看到的 args_hash；同一动作重复确认是幂等的。
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent.orchestrator import AgentDisabled, Orchestrator, TaskError
from app.api.auth import operator_name, require_agent_session, require_token, session_key_of

router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_token), Depends(require_agent_session)])


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    task_id: str | None = Field(default=None, max_length=36, description="继续已有任务（多轮）；缺省则新建")


class ConfirmRequest(BaseModel):
    args_hash: str = Field(min_length=64, max_length=64, description="用户确认时看到的待执行动作的参数哈希")


def _orch(request: Request) -> Orchestrator:
    try:
        return Orchestrator.for_state(request.app.state)
    except AgentDisabled as exc:
        raise HTTPException(409, {"error_code": "llm_not_enabled",
                                  "message": "Agent 需要启用大模型：当前未启用（LLM_PROVIDER=deterministic），"
                                             "不会进行任何多智能体协作。"}) from exc


def _who(request: Request) -> tuple[str, str]:
    return operator_name(request), session_key_of(request)


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _run(request: Request, label: str, fn, task_id: str | None = None):
    audit = request.state.audit
    audit.input_text = f"agent {label}"                        # 不记录消息正文
    if task_id and _UUID.match(task_id):                       # 路径里的任务号：仅当格式合法才写入 HTTP 审计
        audit.agent_task_id = task_id
    try:
        res = fn()
    except TaskError as exc:
        raise HTTPException(exc.status, {"error_code": exc.code, "message": exc.message}) from exc
    if isinstance(res, dict) and _UUID.match(str(res.get("task_id", ""))):
        audit.agent_task_id = res["task_id"]                   # 新建任务：响应里才有任务号
    return res


@router.post("/messages")
def post_message(body: MessageRequest, request: Request):
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "message", lambda: orch.submit(user, key, body.message, body.task_id, request.state.request_id),
                body.task_id)


@router.get("/tasks")
def list_tasks(request: Request):
    """我的任务（当前登录会话、未到期）。用于刷新页面或“停止等待”之后找回任务。"""
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "list", lambda: orch.list_tasks(user, key))


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request):
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "task", lambda: orch.snapshot(user, key, task_id), task_id)


@router.get("/tasks/{task_id}/trace")
def get_trace(task_id: str, request: Request):
    """审计视图（任务所有者）：每一步的 request_id / step_id / 内容哈希，以及对应的 HTTP 请求审计。不含任何内容正文。"""
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "trace", lambda: orch.trace(user, key, task_id), task_id)


@router.post("/tasks/{task_id}/actions/{action_id}/confirm")
def confirm_action(task_id: str, action_id: str, body: ConfirmRequest, request: Request):
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "confirm", lambda: orch.confirm(user, key, task_id, action_id, body.args_hash,
                                                         request.state.request_id), task_id)


@router.post("/tasks/{task_id}/actions/{action_id}/reject")
def reject_action(task_id: str, action_id: str, request: Request):
    orch, (user, key) = _orch(request), _who(request)
    return _run(request, "reject", lambda: orch.reject(user, key, task_id, action_id, request.state.request_id), task_id)
