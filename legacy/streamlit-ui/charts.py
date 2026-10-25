"""看板图表：把 /api/dashboard/charts 的聚合数据画成 Plotly 图。只做绘制，不计算任何业务指标或判定。

不依赖 CDN / 外网：Plotly 图形由 Streamlit 自带的前端渲染，数据随页面传输。
"""
import plotly.graph_objects as go

import layout

FONT = "'Source Sans', 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif"
UNSCHEDULED_C = "#d5dae3"
STATUSES = ("completed", "under_review", "in_progress")


def _base(fig: go.Figure, height: int, legend_below: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_white", height=height, margin=dict(l=8, r=8, t=8, b=8), font=dict(family=FONT, size=13),
        legend=dict(orientation="h", y=-0.22 if legend_below else 1.0, x=0, yanchor="top", font=dict(size=12),
                    traceorder="normal"),
        hoverlabel=dict(font=dict(family=FONT, size=13)), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def progress_by_project(progress: list[dict], selected: str | None) -> go.Figure:
    """各项目分析批数量（按当前状态堆叠）+ 计划批数标记。点击柱子 = 选择该项目（customdata[0] 为项目 ID）。"""
    ids = [p["project_id"] for p in progress]
    cd = [[p["project_id"], p["title"]] for p in progress]
    fig = go.Figure()
    for key in STATUSES:
        zh, color, sym = layout.RUN_STATUS[key]
        fig.add_bar(
            y=ids, x=[p[key] for p in progress], name=f"{sym} {zh}", orientation="h", marker_color=color,
            marker_line=dict(color="#1f2937", width=[2.5 if i == selected else 0 for i in ids]),
            text=[p[key] or "" for p in progress], textposition="inside", insidetextanchor="middle", customdata=cd,
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>" + zh + "：%{x} 批<extra></extra>")
    fig.add_bar(
        y=ids, x=[p["unscheduled"] for p in progress], name="未建批（计划 - 已建）", orientation="h",
        marker_color=UNSCHEDULED_C, text=[p["unscheduled"] or "" for p in progress], textposition="inside",
        customdata=cd, hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>计划内尚未建批：%{x} 批<extra></extra>")
    fig.add_scatter(
        y=ids, x=[p["planned"] for p in progress], mode="markers+text", name="◆ 计划批数",
        marker=dict(symbol="diamond", size=12, color="#111827", line=dict(color="white", width=1)),
        text=[f"计划 {p['planned']}" + "（已超计划）" * p["exceeds_plan"] for p in progress],
        textposition="middle right", customdata=cd, hovertemplate="<b>%{customdata[0]}</b><br>计划批数：%{x}<extra></extra>")
    top = max([max(p["planned"], p["existing"]) for p in progress] + [1])
    fig.update_layout(barmode="stack", clickmode="event+select")
    fig.update_xaxes(title=None, range=[0, top + max(2.5, top * 0.6)], dtick=1, gridcolor="#eef0f4")
    fig.update_yaxes(autorange="reversed", title=None)
    return _base(fig, 120 + 52 * len(progress))


def runs_of_project(runs: list[dict], selected_run: str | None) -> go.Figure:
    """所选项目的各分析批：柱高 = 该批待测样品总数，颜色 = 批状态；悬停显示规则判定（来自 API，不重算）。"""
    fig = go.Figure()
    for key in STATUSES:
        zh, color, sym = layout.RUN_STATUS[key]
        mine = [r for r in runs if r["status"] == key]
        if not mine:
            continue
        fig.add_bar(
            x=[f"{r['id'].split('-')[-1]}<br>{zh}" for r in mine], y=[r["samples"]["total"] for r in mine],
            name=f"{sym} {zh}", marker_color=color,
            marker_line=dict(color="#1f2937", width=[2.5 if r["id"] == selected_run else 0 for r in mine]),
            text=[r["samples"]["total"] for r in mine], textposition="outside", cliponaxis=False,
            customdata=[[r["id"], r["run_date"], layout.verdict_label(r["calibration"]), layout.verdict_label(r["qc"]),
                         layout.verdict_label(r["accepted"]), layout.verdict_label(r["isr"])] for r in mine],
            hovertemplate="<b>%{customdata[0]}</b>（" + zh + "）<br>日期 %{customdata[1]}<br>待测样品 %{y} 条<br>"
                          "校准 %{customdata[2]}　QC %{customdata[3]}<br>批接受 %{customdata[4]}　ISR %{customdata[5]}<extra></extra>")
    fig.update_layout(barmode="group", clickmode="event+select", bargap=0.35)
    fig.update_yaxes(title="待测样品数（条）", gridcolor="#eef0f4", rangemode="tozero")
    fig.update_xaxes(title=None, type="category")
    return _base(fig, 340)


def run_date_cumulative(points: list[dict]) -> go.Figure:
    """按分析批日期累计的批次数（阶梯线）。点位 = 真实分析批；标记颜色 / 形状 = 该批当前状态。不补点。"""
    xs = [p["date"] for p in points for _ in p["runs"]]
    ys = [r["cumulative_rank"] for p in points for r in p["runs"]]
    fig = go.Figure()
    fig.add_scatter(x=xs, y=ys, mode="lines", line=dict(color=layout.NEUTRAL_C, width=2, shape="hv"),
                    name="累计分析批数", hoverinfo="skip")
    shapes = {"completed": "circle", "under_review": "diamond", "in_progress": "square-open"}
    for key in STATUSES:
        zh, color, sym = layout.RUN_STATUS[key]
        sel = [(p["date"], r) for p in points for r in p["runs"] if r["status"] == key]
        if not sel:
            continue
        fig.add_scatter(
            x=[d for d, _ in sel], y=[r["cumulative_rank"] for _, r in sel], mode="markers", name=f"{sym} {zh}（当前状态）",
            marker=dict(symbol=shapes[key], size=12, color=color, line=dict(color="#1f2937", width=1.2)),
            customdata=[[r["id"], zh] for _, r in sel],
            hovertemplate="分析批日期 %{x}<br>累计 %{y} 批<br><b>%{customdata[0]}</b>（当前状态：%{customdata[1]}）<extra></extra>")
    top = max(ys + [1])
    fig.update_xaxes(title="分析批日期（run_date）", title_standoff=6, type="date", gridcolor="#eef0f4", tickformat="%Y-%m-%d")
    fig.update_yaxes(title="累计分析批数", dtick=1, range=[0, top + 1], gridcolor="#eef0f4")
    return _base(fig, 320)


def sample_donut(comp: dict) -> go.Figure | None:
    """样本结果构成环形图。总数为 0 返回 None（调用方显示空状态，不画虚假的 100% 圆环）。"""
    if not comp["total"]:
        return None
    parts = [p for p in comp["parts"] if p["count"] > 0]
    fig = go.Figure(go.Pie(
        labels=[f"{layout.SAMPLE_FLAG[p['flag']][0]}：{p['count']}" for p in parts],      # 图例同时显示名称和数量
        values=[p["count"] for p in parts], hole=0.58, sort=False, direction="clockwise",
        marker=dict(colors=[layout.SAMPLE_FLAG[p["flag"]][1] for p in parts], line=dict(color="white", width=2)),
        customdata=[[layout.SAMPLE_FLAG[p["flag"]][0], p["count"], p["share"] * 100] for p in parts],
        textinfo="percent", textposition="inside", insidetextorientation="horizontal",
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} 条（%{customdata[2]:.1f}%）<extra></extra>"))
    _base(fig, 360)       # 图例放在下方并自动换行：长名称在窄屏上不会挤压圆环
    fig.update_layout(annotations=[dict(text=f"共 {comp['total']}<br>条", x=0.5, y=0.5, showarrow=False, font=dict(size=15))])
    return fig
