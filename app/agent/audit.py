"""Agent 步骤审计：记录每个步骤，并明确写入失败时的处理策略。

记录什么（每步）：时间、登录操作人、request_id / task_id / step_id、角色、工具名、**白名单参数摘要**、结果状态、
来源编号、模型 / 回退状态、token 用量，以及**已脱敏内容的 SHA-256**。

不记录什么：原始提示词、完整模型输出、工具结果正文、用户消息正文、密钥。内容只以哈希出现（可用来核对“是不是同一段内容”，
不能还原）。步骤摘要只用服务端写死的文字；不保存任何模型生成的自由文本，也不保存“排查用摘录”——需要排查内容时应由
有权限的人用任务的会话历史（带到期清理）和哈希核对，而不是在审计里留一份。

写入失败的策略：
- **写操作（确认后的执行）是“先审计、后执行”**：执行前的 confirmation 步骤是 required；写不进去就不执行（fail-closed，返回 503 audit_unavailable，
  待确认动作保持可重试）。
- 其它步骤（模型调用、读工具、交接、最终回答）**不阻断任务**，但会把任务标记为 audit_complete=false，并在任务状态里明确显示
  “审计不完整”；不会默默当作完整。
- 主存储是数据库（agent_step，与 audit_log 用 request_id 关联）；另有只追加的 JSONL（agent-YYYYMMDD.jsonl）作为次存储，
  数据库写入失败时仍会尽力写入 JSONL。JSONL 写入失败只记警告。
- 数据库整体不可用时，任务状态本身也无法保存，请求会失败（而不是“假装成功”）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.agent.schema import sanitize_text

log = logging.getLogger("app.agent.audit")

# 参数摘要只取这些键、且只取短标量值（ID / 枚举 / 小整数）；自由文本（question、text、comment、instruction）永远不进审计
ARG_KEYS = ("project_id", "run_id", "draft_id", "kind", "action", "section", "direction", "as_of", "status", "limit",
            "top_k", "section_index", "role")
SOURCE_CAP = 30


class AuditUnavailable(Exception):
    """必须留痕的步骤没能写入审计。"""


def args_summary(args: dict | None) -> str | None:
    if not args:
        return None
    parts = [f"{k}={args[k]}" for k in ARG_KEYS if isinstance(args.get(k), (str, int)) and not isinstance(args.get(k), bool)
             and len(str(args[k])) <= 60]
    if isinstance(args.get("run_ids"), list):                                   # 只记数量
        parts.append(f"run_ids={len(args['run_ids'])}个")
    s, _ = sanitize_text(", ".join(parts), max_len=200)
    return s or None


def content_hash(content: Any) -> str | None:
    """内容（已脱敏的文本或可序列化对象）的 SHA-256。None 表示没有内容。"""
    if content is None:
        return None
    blob = content if isinstance(content, str) else json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def source_ids(sources: list[dict] | None) -> list[str] | None:
    if not sources:
        return None
    out = []
    for s in sources:
        item = f"{s.get('type')}:{s.get('id')}"
        if item not in out:
            out.append(item)
    return out[:SOURCE_CAP]


class StepRecorder:
    def __init__(self, store, jsonl_dir: str, clock: Callable[[], datetime]):
        self.store, self.jsonl_dir, self.clock = store, jsonl_dir, clock

    # 单独成方法，便于测试注入失败
    def _insert(self, **row) -> int:
        return self.store.add_step(**row)

    def _append_jsonl(self, rec: dict) -> bool:
        try:
            path = Path(self.jsonl_dir)
            path.mkdir(parents=True, exist_ok=True)
            now = self.clock()
            with open(path / f"agent-{now:%Y%m%d}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({**rec, "ts": now.isoformat()}, ensure_ascii=False) + "\n")
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("agent audit jsonl write failed: %s", type(exc).__name__)
            return False

    def record(self, task_id: str, kind: str, agent_role: str, *, operator: str, request_id: str | None,
               tool_name: str | None = None, status: str | None = None, summary: str = "", args: dict | None = None,
               sources: list[dict] | None = None, content: Any = None, model_status: str | None = None,
               tokens: int | None = None, required: bool = False) -> int | None:
        """写一个步骤。返回 step_id；主存储失败时返回 None（required=True 则抛 AuditUnavailable）。"""
        summ, _ = sanitize_text(summary, max_len=200)
        row = dict(task_id=task_id, kind=kind, agent_role=agent_role, tool_name=tool_name, status=status, summary=summ,
                   request_id=request_id, operator=operator, args_summary=args_summary(args),
                   source_ids=source_ids(sources), content_sha256=content_hash(content), model_status=model_status,
                   tokens=tokens)
        step_id: int | None
        try:
            step_id = self._insert(**row)
        except Exception as exc:  # noqa: BLE001
            log.warning("agent audit db write failed (%s): %s", kind, type(exc).__name__)
            step_id = None
        self._append_jsonl({"task_id": task_id, "step_id": step_id, **{k: v for k, v in row.items() if k != "task_id"},
                            "sink_primary_ok": step_id is not None})
        if step_id is None and required:
            raise AuditUnavailable(kind)
        return step_id
