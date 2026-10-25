"""项目看板：全部数据来自 /api/dashboard/* 与 /api/runs/*；图表数据由后端聚合，页面只绘制、不计算指标或判定。

联动：顶部“项目”选择、进度图里点击柱子、分析批明细图里点击柱子，三者同步切换下方的表格与风险。
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402
import charts  # noqa: E402
import layout  # noqa: E402

PLOT_CONFIG = {"displaylogo": False, "displayModeBar": False}

layout.setup("项目看板", icon=":material/dashboard:")


def clicked(key: str) -> str | None:
    """读取上一次交互中图表被点击的柱子（customdata[0]）。"""
    state = st.session_state.get(key)
    try:
        pts = state["selection"]["points"]
    except (KeyError, TypeError):
        return None
    for p in pts:
        cd = p.get("customdata")
        if cd:
            return cd[0]
    return None


# ---- 顶部：筛选 ----
with st.spinner("加载看板…"):
    f1, f2 = st.columns([1, 2])
    as_of = f1.date_input("统计截止日期", value=date.today(),
                          help="只用于里程碑逾期 / 即将到期与偏差账龄；不是所有数据的时间筛选条件。")
    ok, ov = api.call("GET", "/api/dashboard/overview", params={"as_of": as_of.isoformat()})
    if not ok:
        api.show_error(st, ov)
        st.stop()
    ok_c, cd = api.call("GET", "/api/dashboard/charts", params={"as_of": as_of.isoformat()})

pids = [p["project"]["id"] for p in ov["projects"]]
titles = {p["project"]["id"]: p["project"]["title"] for p in ov["projects"]}

# 图表点击 -> 同步项目 / 分析批选择（只在点击发生变化时覆盖，避免盖掉手动选择）
for key, target in (("chart_progress", "proj_pick"), ("chart_runs", "run_pick")):
    hit = clicked(key)
    if hit and hit != st.session_state.get(f"_applied_{key}"):
        st.session_state[f"_applied_{key}"] = hit
        if hit in (pids if target == "proj_pick" else [r["id"] for r in (cd["runs"].get(st.session_state.get("proj_pick"), []) if ok_c else [])]):
            st.session_state[target] = hit
if st.session_state.get("proj_pick") not in pids:
    st.session_state["proj_pick"] = pids[0]
pid = f2.selectbox("项目", pids, key="proj_pick", format_func=lambda i: f"{titles[i]}（{i}）")

# ---- 顶部：关键指标 ----
t = ov["totals"]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("项目数", t["projects"])
c2.metric("分析批完成进度", api.fmt_rate(t["progress"]))
c3.metric("样本数（有效 / 总）", f"{t['samples']['valid']} / {t['samples']['total']}")
c4.metric("未关闭偏差（重要及以上）", f"{t['open_deviations']}（{t['open_major_plus']}）")
c5.metric("高风险提示", t["risks_by_level"]["high"])
st.caption(f"汇总 QC 通过率 {api.fmt_rate(t['qc']['pass_rate'])}；汇总 ISR 通过率 {api.fmt_rate(t['isr']['pass_rate'])}。"
           "通过率 = 通过 /（通过 + 未通过），待复核单列，进行中的分析批不计入。")

ok, m = api.call("GET", f"/api/dashboard/projects/{pid}", params={"as_of": as_of.isoformat()})
if not ok:
    api.show_error(st, m)
    st.stop()
run_ids = [r["id"] for r in m["run_summaries"]]
if st.session_state.get("run_pick") not in run_ids and run_ids:
    st.session_state["run_pick"] = run_ids[0]

# ---- 中部：图表（项目对比 → 时间变化 → 样本构成）----
st.markdown("#### 图表")
if not ok_c:
    st.warning("图表数据加载失败，下方表格不受影响。")
    api.show_error(st, cd)
else:
    with st.container(border=True):
        h1, h2 = st.columns([2, 1])
        h1.markdown("**分析批进度**")
        view = h2.segmented_control("视图", ["项目对比", "所选项目分析批明细"], default="项目对比", key="prog_view",
                                    label_visibility="collapsed") or "项目对比"
        if view == "项目对比":
            if not cd["progress"]:
                layout.empty("还没有项目。")
            else:
                st.plotly_chart(charts.progress_by_project(cd["progress"], pid), key="chart_progress", on_select="rerun",
                                selection_mode="points", config=PLOT_CONFIG, width="stretch")
                st.caption(f"横轴为分析批数量。{cd['notes']['progress']}　点击柱子可切换所选项目（当前：{pid}）。")
        else:
            runs = cd["runs"].get(pid, [])
            if not runs:
                layout.empty(f"{pid} 还没有分析批。")
            else:
                st.plotly_chart(charts.runs_of_project(runs, st.session_state.get("run_pick")), key="chart_runs",
                                on_select="rerun", selection_mode="points", config=PLOT_CONFIG, width="stretch")
                st.caption("柱高 = 该批待测样品数，颜色与文字 = 批状态；悬停查看日期与规则判定。点击柱子可在下方“批明细”页签查看该批。")

    left, right = st.columns([3, 2])
    with left, st.container(border=True):
        st.markdown("**分析批日期累计曲线**")
        scope = st.segmented_control("范围", ["所选项目", "全部项目"], default="所选项目", key="tl_scope",
                                     label_visibility="collapsed") or "所选项目"
        points = cd["timeline"]["ALL" if scope == "全部项目" else pid]
        if not points:
            layout.empty("没有可绘制的分析批日期。")
        else:
            st.plotly_chart(charts.run_date_cumulative(points), key="chart_timeline", config=PLOT_CONFIG, width="stretch")
        st.caption("**统计口径**：" + cd["notes"]["timeline"])
    with right, st.container(border=True):
        st.markdown(f"**样本结果构成**　{pid}")
        comp = cd["sample_composition"][pid]
        fig = charts.sample_donut(comp)
        if fig is None:
            layout.empty("该项目还没有待测样品结果。")
        else:
            st.plotly_chart(fig, key="chart_samples", config=PLOT_CONFIG, width="stretch")
            st.dataframe(pd.DataFrame([{
                "结果": layout.SAMPLE_FLAG[p["flag"]][0], "数量": p["count"],
                "占比": f"{p['share'] * 100:.1f}%"} for p in comp["parts"]]), width="stretch", hide_index=True)
        st.caption(cd["notes"]["samples"])

# ---- 下部：所选项目的风险与明细 ----
st.divider()
st.markdown(f"#### {m['project']['title']}")
a, b, c, d = st.columns(4)
a.metric("完成进度", api.fmt_rate(m["runs"]["progress"]))
b.metric("QC 通过率", api.fmt_rate(m["qc"]["pass_rate"]))
c.metric("ISR 通过率", api.fmt_rate(m["isr"]["pass_rate"]))
d.metric("批接受通过率", api.fmt_rate(m["accepted"]["pass_rate"]))
st.caption(f"待复核：QC {m['qc']['pending_review']} 个批、ISR {m['isr']['pending_review']} 个批；"
           f"进行中（未计入）{m['runs']['in_progress']} 个批；ISR 样品 {m['isr']['records']['total']} 例"
           f"（通过 {m['isr']['records']['pass']} / 未通过 {m['isr']['records']['fail']} / 待复核 {m['isr']['records']['pending_review']}）。")

risk_tab, run_tab, dev_tab, proj_tab, detail_tab = st.tabs(
    [f"风险提示（{len(m['risks'])}）", f"分析批（{len(m['run_summaries'])}）", "偏差与里程碑", "项目汇总", "批明细"])

with risk_tab:
    if not m["risks"]:
        st.success("当前没有触发风险规则。")
    for r in m["risks"]:
        text = f"**【{api.LEVEL_ZH[r['level']]}】{r['risk_code']}**：{r['message']}  \n依据：{r['evidence']}"
        {"high": st.error, "medium": st.warning, "low": st.info}[r["level"]](text)

with run_tab:
    st.dataframe(pd.DataFrame([{
        "分析批": r["id"], "日期": r["run_date"], "状态": layout.run_status_label(r["status"]),
        "计入通过率": "是" if r["counted_in_rates"] else "否（进行中）",
        "校准": layout.verdict_label(r["calibration"]), "QC": layout.verdict_label(r["qc"]),
        "批接受": layout.verdict_label(r["accepted"]), "ISR": layout.verdict_label(r["isr"]),
    } for r in m["run_summaries"]]), width="stretch", hide_index=True)

with dev_tab:
    left, right = st.columns(2)
    with left:
        st.markdown("**偏差**")
        st.caption(f"未关闭 {m['deviations']['open']} 项（轻微 {m['deviations']['open_by_severity']['minor']} / "
                   f"重要 {m['deviations']['open_by_severity']['major']} / 严重 {m['deviations']['open_by_severity']['critical']}）")
        st.dataframe(pd.DataFrame([{
            "编号": x["id"], "分析批": x["run_id"] or "—", "类别": x["category"],
            "严重度": api.SEVERITY_ZH[x["severity"]], "状态": "未关闭" if x["status"] == "open" else "已关闭",
            "登记日期": x["opened_at"]} for x in m["deviations"]["items"]]), width="stretch", hide_index=True)
    with right:
        st.markdown("**报告里程碑**")
        st.dataframe(pd.DataFrame([{
            "类型": x["type"], "计划日期": x["planned_date"], "状态": api.MILESTONE_ZH[x["state"]],
            "距到期天数": str(x["days_to_due"]) if x["days_to_due"] is not None else "—"} for x in m["milestones"]]),
            width="stretch", hide_index=True)

with proj_tab:
    rows = []
    for p in ov["projects"]:
        q, i, dv = p["qc"], p["isr"], p["deviations"]
        rows.append({
            "项目": p["project"]["id"], "方法": p["project"]["method_type"], "样本数": p["samples"]["total"],
            "完成进度": api.fmt_rate(p["runs"]["progress"]),
            "QC 通过/未通过/待复核": f"{q['pass']}/{q['fail']}/{q['pending_review']}",
            "ISR 通过/未通过/待复核": f"{i['pass']}/{i['fail']}/{i['pending_review']}",
            "未关闭偏差": dv["open"], "风险数": len(p["risks"]),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

with detail_tab:
    if not run_ids:
        layout.empty("该项目还没有分析批。")
    else:
        rid = st.selectbox("分析批", run_ids, key="run_pick")
        ok, run = api.call("GET", f"/api/runs/{rid}")
        if not ok:
            api.show_error(st, run)
        else:
            st.caption(f"规则集 {run['run']['rule_set_id']}（状态 {run['rule_status']}）；批接受：{api.VERDICT_ZH[run['assessment']['accepted']]}")
            t1, t2, t3 = st.tabs(["校准点", "QC 样品", "ISR 记录"])
            t1.dataframe(pd.DataFrame(run["calibration_points"]), width="stretch", hide_index=True)
            t2.dataframe(pd.DataFrame(run["qc_samples"]), width="stretch", hide_index=True)
            t3.dataframe(pd.DataFrame(run["isr_records"]), width="stretch", hide_index=True)
