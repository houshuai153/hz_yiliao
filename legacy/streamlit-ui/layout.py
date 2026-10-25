"""Streamlit 页面共用的版式：统一标题、紧凑的“模拟数据”提示、窄屏友好的样式、状态标签。

只负责展示；不连数据库，也不计算任何业务指标。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402

COMPACT_NOTICE = "模拟数据 · 演示规则（demo_unconfirmed）· 规则与模板实现，非 LLM · 输出需人工复核"
COMPACT_NOTICE_LLM = ("模拟数据 · 演示规则（demo_unconfirmed）· 已启用大模型（仅翻译兜底 / 问答措辞 / 报告修订建议，发送前脱敏，"
                      "判定仍由规则计算）· 输出需人工复核")

_CSS = """
<style>
.block-container{padding-top:2.4rem;padding-bottom:3rem;max-width:1400px}
h1{font-size:1.65rem!important;line-height:1.25!important;margin-bottom:.1rem!important}
h4{margin-top:1.1rem!important}
/* 窄屏：多列自动换行，而不是被挤成不可读的细条 */
[data-testid="stHorizontalBlock"]{flex-wrap:wrap;row-gap:.6rem}
[data-testid="stColumn"]{min-width:min(100%,150px)}
[data-testid="stMetric"]{background:rgba(120,130,150,.08);border-radius:.5rem;padding:.55rem .8rem}
[data-testid="stMetricLabel"] p{font-size:.82rem}
[data-testid="stMetricValue"]{font-size:1.35rem}
.notice{color:#6b7280;font-size:.82rem;margin:.1rem 0 .9rem}
.block-edited{border-left:3px solid #1f4e9a;padding-left:.6rem}
.block-flag{border-left:3px solid #c00000;padding-left:.6rem}
.src-tag{color:#6b7280;font-size:.75rem;margin-left:.3rem}
@media (max-width:640px){
  .block-container{padding-left:.8rem;padding-right:.8rem}
  h1{font-size:1.35rem!important}
}
</style>
"""

STATUS_STYLE = {   # 审核状态 -> (中文, 颜色)
    "draft": ("待审核", "gray"), "in_review": ("审核中", "blue"),
    "changes_requested": ("退回修改", "orange"), "reviewed": ("审核通过", "green"),
}


def setup(title: str, *, icon: str = "", subtitle: str | None = None) -> None:
    """页面统一入口：页面配置、样式、标题、紧凑的模拟数据提示，以及侧栏的操作人记录。"""
    st.set_page_config(page_title=title, page_icon=icon or None, layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.text_input("操作人", key="operator", placeholder="填写姓名或工号",
                      help="用于审核记录与审计日志的“操作人记录”，不是电子签名，也不做身份认证。")
        h = api.health()
        llm_on = bool(h and h.get("llm_enabled"))
        with st.expander("关于本原型", expanded=False):
            st.caption(api.SIM_WARNING)
            if llm_on:
                st.caption(api.LLM_WARNING.format(model=h.get("llm_model") or h["llm_provider"]))
    st.title(f"{icon} {title}".strip())
    notice = COMPACT_NOTICE_LLM if llm_on else COMPACT_NOTICE
    st.markdown(f'<div class="notice">{notice}</div>', unsafe_allow_html=True)
    if subtitle:
        st.caption(subtitle)


def status_badge(status: str) -> None:
    zh, color = STATUS_STYLE.get(status, (status, "gray"))
    st.badge(zh, color=color)


def status_text(status: str) -> str:
    zh, color = STATUS_STYLE.get(status, (status, "gray"))
    return f":{color}-badge[{zh}]"


def empty(msg: str) -> None:
    st.info(msg, icon=":material/inbox:")


# ---------- 状态语义色（所有图表保持一致；同时配有文字与符号，不只靠颜色）----------
OK_C, PENDING_C, FAIL_C, PROGRESS_C, NEUTRAL_C, WARN_C = "#2f8f5b", "#e0a526", "#c8483f", "#5b7db1", "#8a94a6", "#d9822b"
RUN_STATUS = {   # 分析批状态 -> (中文, 颜色, 符号)
    "completed": ("已完成", OK_C, "✓"), "under_review": ("复核中", PENDING_C, "!"), "in_progress": ("进行中", PROGRESS_C, "…"),
}
VERDICT = {      # 规则判定 -> (中文, 颜色, 符号)
    "pass": ("通过", OK_C, "✓"), "pending_review": ("待复核", PENDING_C, "!"), "fail": ("未通过", FAIL_C, "✕"),
}
SAMPLE_FLAG = {  # 样品结果标记 -> (中文, 颜色)
    "ok": ("正常（ok）", OK_C), "BLQ": ("低于定量下限（BLQ）", NEUTRAL_C), "above_ULOQ": ("高于定量上限（above_ULOQ）", WARN_C),
    "reanalysis": ("需重分析（reanalysis）", PENDING_C), "missing": ("缺失（missing）", FAIL_C),
}


def run_status_label(status: str) -> str:
    zh, _, sym = RUN_STATUS[status]
    return f"{sym} {zh}"


def verdict_label(v: str) -> str:
    zh, _, sym = VERDICT[v]
    return f"{sym} {zh}"
