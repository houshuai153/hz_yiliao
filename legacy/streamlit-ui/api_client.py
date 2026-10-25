"""Streamlit 页面共用的 API 客户端：只经 HTTP 访问 FastAPI，不直连数据库。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:           # 无论用 `streamlit run` 还是 `python -m streamlit run` 都能 import app.*
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from app.settings import get_settings  # noqa: E402

SIM_WARNING = "原型系统：全部数据为模拟数据；翻译、问答、报告默认为规则与模板实现（非 LLM）；规则阈值为演示配置（demo_unconfirmed）；输出需人工复核。"
LLM_WARNING = ("已启用大模型（{model}）：仅用于翻译兜底、问答措辞整理、报告修订建议；发送前已脱敏，输出须通过数字 / 编号 / 引用校验并经人工复核；"
               "QC / ISR 判定、指标与事实数据仍由规则引擎计算，不交给模型。")
VERDICT_ZH = {"pass": "通过", "fail": "未通过", "pending_review": "待复核"}
STATUS_ZH = {"completed": "已完成", "under_review": "复核中", "in_progress": "进行中"}
SEVERITY_ZH = {"minor": "轻微", "major": "重要", "critical": "严重"}
LEVEL_ZH = {"high": "高", "medium": "中", "low": "低"}
REASON_ZH = {"no_planned_runs": "未设置计划分析批数", "no_decided_runs": "暂无已判定分析批"}
MILESTONE_ZH = {"delivered": "已交付", "overdue": "逾期", "due_soon": "即将到期", "on_track": "正常"}


def operator() -> str | None:
    """侧栏填写的操作人记录；未填写返回 None（审核类操作必须先填写）。"""
    try:
        import streamlit as st
        return (st.session_state.get("operator") or "").strip()[:64] or None
    except Exception:
        return None


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().api_token}", "X-Operator": operator() or "streamlit-ui"}


def call(method: str, path: str, **kw):
    """返回 (ok, data)。失败时 data 为 {"status": 状态码或 None, "detail": 说明}。"""
    url = get_settings().api_base_url.rstrip("/") + path
    try:
        r = requests.request(method, url, headers=_headers(), timeout=30, **kw)
    except requests.RequestException as exc:
        return False, {"status": None, "detail": f"无法连接 API（{url}）：{exc.__class__.__name__}"}
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        return False, {"status": r.status_code, "detail": detail}
    return True, (r if kw.get("stream") else r.json())


def get_bytes(path: str):
    url = get_settings().api_base_url.rstrip("/") + path
    try:
        r = requests.get(url, headers=_headers(), timeout=60)
    except requests.RequestException as exc:
        return False, f"无法连接 API：{exc.__class__.__name__}"
    if r.status_code < 400:
        return True, r.content
    try:
        d = r.json()["detail"]
        return False, d["message"] if isinstance(d, dict) else str(d)
    except (ValueError, KeyError):
        return False, f"HTTP {r.status_code}"


def fmt_rate(r: dict) -> str:
    if r["value"] is None:
        return f"—（{REASON_ZH.get(r['reason'], r['reason'])}）"
    return f"{r['value'] * 100:.1f}%（{r['numerator']}/{r['denominator']}）"


def show_error(st, data) -> None:
    detail = data["detail"]
    if isinstance(detail, dict) and "message" in detail:       # 后端业务错误：{error_code, message}
        st.error(f"{detail['message']}（{detail.get('error_code', data['status'])}）")
    else:
        st.error(f"请求失败（{data['status']}）：{detail}")


REVIEW_STATUS_ZH = {"draft": "待审核", "in_review": "审核中", "changes_requested": "退回修改", "reviewed": "审核通过"}
KIND_ZH = {"validation": "方法学验证报告", "sample_analysis": "样品分析报告"}
REFUSAL_ZH = {
    "clause_fabrication_risk": "要求条款编号或原文", "unknown_section": "章节不存在", "out_of_scope": "超出知识库范围",
    "no_supporting_sentence": "无可支撑的原句", "citation_validation_failed": "引用校验未通过", "empty_question": "问题为空",
}


def health(base_url: str | None = None) -> dict | None:
    """/api/health（页面脚本内短时缓存，避免每页每次重复请求）；失败返回 None。"""
    try:
        import streamlit as st
    except Exception:
        return None

    @st.cache_data(ttl=20, show_spinner=False)
    def _h(url: str):
        ok, info = call("GET", "/api/health")
        return info if ok else None
    return _h(base_url or get_settings().api_base_url)
