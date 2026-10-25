import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../pages/Dashboard";
import { json, mockFetch, renderAt, ME } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const rate = (n: number, d: number) => ({ value: d ? n / d : null, numerator: n, denominator: d, reason: d ? null : "no_decided_runs" });
const block = (p: number, f: number, w: number) => ({ eligible_runs: p + f + w, pass: p, fail: f, pending_review: w, pass_rate: rate(p, p + f) });
const runs = { planned: 4, existing: 3, completed: 2, under_review: 1, in_progress: 0, progress: { ...rate(2, 4), reason: null } };
const proj = (id: string, extra: Record<string, unknown> = {}) => ({
  project: { id, title: `${id} 演示项目`, modality: "small_molecule", method_type: "LC-MS/MS", analyte: "DEMO", matrix: "人血浆", unit: "ng/mL", phase: "sample_analysis", is_simulated: true },
  as_of: "2026-10-23", samples: { total: 127, valid: 105, blq: 18, above_uloq: 0, reanalysis: 1, missing: 3 }, runs,
  calibration: block(2, 0, 1), qc: block(1, 1, 1), accepted: block(1, 1, 1), isr: block(2, 0, 1),
  deviations: { total: 1, open: 1, closed: 0, open_by_severity: { minor: 0, major: 1, critical: 0 }, oldest_open_days: 51, items: [{ id: "DEV-1", run_id: `${id}-R02`, category: "QC失控", severity: "major", status: "open", opened_at: "2026-09-02", closed_at: null, description: "d" }] },
  milestones: [{ type: "sample_report", planned_date: "2026-11-01", status: "in_progress", completed_date: null, state: "on_track", days_to_due: 8 }],
  run_summaries: [
    { id: `${id}-R01`, run_date: "2026-08-18", status: "completed", counted_in_rates: true, calibration: "pass", qc: "pass", accepted: "pass", isr: "pass" },
    { id: `${id}-R02`, run_date: "2026-09-01", status: "completed", counted_in_rates: true, calibration: "pass", qc: "fail", accepted: "fail", isr: "pass" },
    { id: `${id}-R03`, run_date: "2026-09-08", status: "under_review", counted_in_rates: true, calibration: "pending_review", qc: "pending_review", accepted: "pending_review", isr: "pending_review" }],
  risks: [{ risk_code: "RISK-QC-FAIL", level: "high", message: "1 个分析批 QC 未通过", evidence: { run_ids: [`${id}-R02`] } }], rule_status: "demo_unconfirmed", ...extra,
});
const OV = (ps = [proj("PRJ-A"), proj("PRJ-B")]) => ({
  as_of: "2026-10-23", projects: ps,
  totals: { projects: ps.length, samples: { total: 254, valid: 210 }, runs: runs, progress: { ...rate(4, 8), reason: null }, open_deviations: 2, open_major_plus: 2, risks_by_level: { high: 2, medium: 0, low: 0 }, qc: block(2, 2, 2), isr: block(4, 0, 2), accepted: block(2, 2, 2) },
});
const comp = (total: number) => ({ total, parts: ["ok", "BLQ", "above_ULOQ", "reanalysis", "missing"].map((f, i) => ({ flag: f, count: total ? [105, 18, 0, 1, 3][i] : 0, share: total ? [105, 18, 0, 1, 3][i] / total : null })) });
const CHARTS = (empty = false) => ({
  as_of: "2026-10-23", timeline_basis: "run_date_cumulative_current_status",
  notes: { progress: "按分析批当前状态计数。", timeline: "横轴为分析批日期（run_date）。库中没有状态变更历史，因此不代表历史上某日的完成情况；", samples: "占比 = 该标记数 / 样品总数。" },
  progress: [{ project_id: "PRJ-A", title: "A", planned: 4, existing: 3, completed: 2, under_review: 1, in_progress: 0, unscheduled: 1, exceeds_plan: false },
    { project_id: "PRJ-B", title: "B", planned: 1, existing: 2, completed: 2, under_review: 0, in_progress: 0, unscheduled: 0, exceeds_plan: true }],
  runs: { "PRJ-A": [], "PRJ-B": [] },
  timeline: { "PRJ-A": empty ? [] : [{ date: "2026-08-18", run_ids: ["PRJ-A-R01"], runs_on_date: 1, cumulative: 1, after_as_of: false, runs: [{ id: "PRJ-A-R01", status: "completed", cumulative_rank: 1 }] }], ALL: [] },
  sample_composition: { "PRJ-A": comp(empty ? 0 : 127), "PRJ-B": comp(0), ALL: comp(0) },
});
const RUN = { run: { id: "PRJ-A-R02", project_id: "PRJ-A", run_date: "2026-09-01", status: "completed", rule_set_id: "RS-1" }, rule_status: "demo_unconfirmed",
  assessment: { calibration: { verdict: "pass" }, qc: { verdict: "fail" }, isr: { verdict: "pass" }, accepted: "fail" },
  calibration_points: [{ level_no: 3, nominal: 3.9, back_calc_conc: null, bias_pct: null, limit_pct: 15, status: "unknown" }], qc_samples: [], isr_records: [], sample_counts: {} };

function routes(over: Record<string, unknown> = {}) {
  return { "GET /api/auth/me": ME, "GET /api/health": {}, "GET /api/dashboard/overview": OV(), "GET /api/dashboard/charts": CHARTS(),
    "GET /api/dashboard/projects/PRJ-A": proj("PRJ-A"), "GET /api/dashboard/projects/PRJ-B": proj("PRJ-B"), "GET /api/reports": [],
    "GET /api/runs/PRJ-A-R02": RUN, ...over };
}

describe("项目看板", () => {
  it("指标带显示后端给出的值，图表附数据表，口径说明可见", async () => {
    mockFetch(routes());
    renderAt(<Dashboard />, { path: "/?asOf=2026-10-23" });
    const kpis = await screen.findByRole("group", { name: "关键指标" });
    expect(within(kpis).getByText("分析批完成进度").nextSibling).toHaveTextContent("50.0%");   // 4/8，由后端给出的值
    expect(within(kpis).getByText("QC 通过率").nextSibling).toHaveTextContent("50.0%");
    expect(await screen.findByText(/不代表历史上某日的完成情况/)).toBeInTheDocument();
    const t = (await screen.findAllByRole("table", { hidden: true })).find((x) => x.querySelector("caption")?.textContent?.includes("各项目分析批进度"));
    expect(t).toBeTruthy();
    expect(within(t!).getByText("PRJ-B").closest("tr")).toHaveTextContent("PRJ-B2000" + "1");  // 已完成 2 / 复核 0 / 进行 0 / 未建批 0 / 计划 1
  });

  it("分母为 0 的比率显示“—”和原因，而不是 0%", async () => {
    const ov = OV(); ov.totals.qc = { ...block(0, 0, 0) }; ov.totals.isr = { ...block(0, 0, 0) };
    mockFetch(routes({ "GET /api/dashboard/overview": ov }));
    renderAt(<Dashboard />);
    const kpis = await screen.findByRole("group", { name: "关键指标" });
    expect(within(kpis).getAllByText("暂无已判定分析批").length).toBe(2);
    expect(within(kpis).queryByText("0.0%")).toBeNull();
  });

  it("样品总数为 0：显示空状态，不画环形图", async () => {
    mockFetch(routes({ "GET /api/dashboard/charts": CHARTS(true) }));
    renderAt(<Dashboard />);
    expect(await screen.findByText("该项目还没有待测样品结果")).toBeInTheDocument();
    expect(screen.getByText("没有可绘制的分析批日期")).toBeInTheDocument();
  });

  it("接口失败：在失败的区块内显示错误和“重试”，其余区块不受影响", async () => {
    const calls = { n: 0 };
    mockFetch(routes({ "GET /api/dashboard/charts": () => { calls.n++; return calls.n === 1 ? json(500, { detail: "boom" }) : CHARTS(); } }));
    renderAt(<Dashboard />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("boom"))).toBe(true);
    expect(await screen.findByRole("group", { name: "关键指标" })).toBeInTheDocument();     // 指标带仍然在
    await userEvent.click(screen.getAllByRole("button", { name: "重试" })[0]);
    await waitFor(() => expect(calls.n).toBeGreaterThan(1));
  });

  it("下钻：项目 → 分析批 → 原始明细；缺值显示“—”并标注无法判定", async () => {
    const user = userEvent.setup();
    mockFetch(routes());
    renderAt(<Dashboard />, { path: "/?project=PRJ-A&tab=runs" });
    const runBtn = await screen.findByRole("button", { name: "PRJ-A-R02" });
    await user.click(runBtn);
    expect(await screen.findByText(/规则集 RS-1/)).toBeInTheDocument();
    expect(screen.getByText("缺值，无法判定")).toBeInTheDocument();
    const row = screen.getByText("缺值，无法判定").closest("tr")!;
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(2);                  // 回算浓度、偏差 %
    expect(screen.getByRole("navigation", { name: "层级" })).toHaveTextContent("全部项目›PRJ-A›PRJ-A-R02");
  });

  it("未登录用户信息不影响渲染（操作人由会话决定）", async () => {
    mockFetch(routes());
    renderAt(<Dashboard />);
    expect(await screen.findByRole("heading", { name: "项目看板" })).toBeInTheDocument();
  });
});
