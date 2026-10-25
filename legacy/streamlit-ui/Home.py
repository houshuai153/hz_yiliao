"""工作台入口：关键状态 + 待办 + 各功能入口。仅通过 HTTP 访问 FastAPI，不直连数据库、不在页面里计算指标。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402
import layout  # noqa: E402

layout.setup("生物分析 AI 效能平台", icon=":material/biotech:")

ok, info = api.call("GET", "/api/health")
if ok:
    st.caption(f"API 在线：版本 {info['version']}，provider={info['llm_provider']}，offline={info['offline']}")
else:
    api.show_error(st, info)

ok, ov = api.call("GET", "/api/dashboard/overview", params={"as_of": date.today().isoformat()})
ok_r, reports = api.call("GET", "/api/reports")
waiting = [r for r in reports if r["review_status"] != "reviewed" and not r["legacy"]] if ok_r else []

st.markdown("#### 当前状态")
c1, c2, c3, c4 = st.columns(4)
if ok:
    t = ov["totals"]
    c1.metric("分析批完成进度", api.fmt_rate(t["progress"]))
    c2.metric("未关闭偏差（重要及以上）", f"{t['open_deviations']}（{t['open_major_plus']}）")
    c3.metric("高风险提示", t["risks_by_level"]["high"])
else:
    api.show_error(st, ov)
if ok_r:
    c4.metric("待处理的报告草稿", len(waiting))

left, right = st.columns([3, 2])
with left:
    st.markdown("#### 待办")
    if ok_r and waiting:
        for r in waiting[:6]:
            with st.container(border=True):
                st.markdown(f"{layout.status_text(r['review_status'])} **{r['draft_id']}**  \n"
                            f"{r['project_id']} · {api.KIND_ZH[r['kind']]} · v{r['current_version']}"
                            + (f" · 未解决问题 {r['open_issues']}" if r["open_issues"] else ""))
        st.page_link("pages/4_报告草稿.py", label="进入报告审核", icon=":material/rate_review:")
    elif ok_r:
        layout.empty("没有待处理的报告草稿。可在“报告草稿”页生成。")
    if ok:
        highs = [(p["project"]["id"], r) for p in ov["projects"] for r in p["risks"] if r["level"] == "high"]
        if highs:
            st.markdown("#### 高风险提示")
            for pid, r in highs[:5]:
                st.error(f"**{pid}** · {r['risk_code']}：{r['message']}")
            st.page_link("pages/1_项目看板.py", label="在项目看板查看依据", icon=":material/dashboard:")
with right:
    st.markdown("#### 功能入口")
    for page, label, icon, desc in [
        ("pages/1_项目看板.py", "项目看板", ":material/dashboard:", "进度、QC / ISR、偏差、里程碑、风险"),
        ("pages/2_法规与SOP问答.py", "法规与 SOP 问答", ":material/quiz:", "带引用的抽取式问答，证据不足则拒答"),
        ("pages/3_翻译与术语核对.py", "翻译与术语核对", ":material/translate:", "固定句型翻译与术语一致性核对"),
        ("pages/4_报告草稿.py", "报告草稿与审核", ":material/description:", "生成 → 预览审核 → 通过后导出 DOCX"),
    ]:
        with st.container(border=True):
            st.page_link(page, label=label, icon=icon)
            st.caption(desc)
