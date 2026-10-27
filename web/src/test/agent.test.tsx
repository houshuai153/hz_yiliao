import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Agent, agentConfig } from "../pages/Agent";
import { json, mockFetch, renderAt, ME } from "./helpers";

const TID = "11111111-2222-3333-4444-555555555555";
const DRAFT = "DRAFT-SMP-20261027080000-abc123";
const HASH = "a".repeat(64);
const ON = { ...ME, llm_enabled: true, llm_model: "deepseek-chat" };
const FUTURE = new Date(Date.now() + 3600_000).toISOString();
const PAST = new Date(Date.now() - 60_000).toISOString();
const DEMO = "查询项目 PRJ-SM-003 及其批次，汇总规则结果，并准备报告草稿";

const step = (id: number, kind: string, role: string, extra: Record<string, unknown> = {}) => ({
  step_id: id, kind, agent_role: role, tool_name: null, status: "ok", summary: "", args_summary: null, sources: [], model_status: null,
  created_at: "2026-10-27T08:00:00+00:00", ...extra });
const EV = {
  verdict_source: "app.rules", rule_status: "demo_unconfirmed", projects: [{ project_id: "PRJ-SM-003", title: "演示项目" }],
  runs: [{ run_id: "PRJ-SM-003-R02", status: "completed", calibration: "pass", qc: "fail", accepted: "fail", isr: "pass" }],
  reports: [{ draft_id: DRAFT, review_status: "draft", project_id: "PRJ-SM-003", review_url: `/reports/${DRAFT}` }],
};
const FINAL = { answer: "PRJ-SM-003-R02 的 QC 未通过。报告草稿已生成，待人工审核。", answer_source: "model", fallback_reason: [], evidence: EV, verdict_source: "app.rules" };
const STEPS = [
  step(1, "user_message", "orchestrator", { summary: "用户消息" }),
  step(2, "handoff", "orchestrator", { tool_name: "delegate", summary: "总控 → 数据专家" }),
  step(3, "tool_call", "data", { tool_name: "list_project_runs", args_summary: "project_id=PRJ-SM-003", sources: ["project:PRJ-SM-003", "run:PRJ-SM-003-R02"] }),
  step(4, "tool_call", "qa", { tool_name: "generate_report_draft", status: "tool_not_permitted" }),
  step(5, "write_result", "report", { tool_name: "generate_report_draft", sources: [`report:${DRAFT}`, "kb_section:DEMO-SOP-DEV-004"] }),
];
const task = (over: Record<string, unknown> = {}) => ({
  task_id: TID, status: "idle", active_role: "orchestrator", active_role_zh: "总控", created_at: "2026-10-27T08:00:00+00:00",
  updated_at: "2026-10-27T08:01:00+00:00", expires_at: FUTURE, final: null, conversation: [], pending_actions: [], steps: [],
  usage: { rounds: 3, tool_calls: 2, tokens_used: 1200, limits: { max_rounds: 12, max_tool_calls: 16, max_seconds: 90, max_tokens: 60000 } },
  termination_reason: null, error_code: null, audit_complete: true, audit_note: null, ...over });
const ANSWERED = (over: Record<string, unknown> = {}) => task({
  final: FINAL, steps: STEPS, conversation: [{ role: "user", content: DEMO }, { role: "assistant", content: FINAL.answer }], ...over });
const PENDING = {
  action_id: "A1", tool: "generate_report_draft", status: "pending", args_hash: HASH, expires_at: FUTURE,
  summary: "为项目 PRJ-SM-003 生成样品分析报告草稿（纳入：全部分析批）。草稿状态为“待审核”，不会自动审核或导出；审核通过须由人在审核页完成。",
  args: { project_id: "PRJ-SM-003", kind: "sample_analysis", run_ids: [] },
};
const AWAITING = (over: Record<string, unknown> = {}) => task({
  status: "awaiting_confirmation", active_role: "report", active_role_zh: "报告专家", pending_actions: [PENDING], steps: STEPS,
  conversation: [{ role: "user", content: DEMO }], ...over });

const open = (path = "/agent") => renderAt(<Agent />, { path, route: "/agent" });
const base = (extra: Record<string, unknown> = {}) => ({ "GET /api/auth/me": ON, "GET /api/agent/tasks": [], ...extra });

beforeEach(() => { agentConfig.timeoutMs = 150_000; });
afterEach(() => vi.unstubAllGlobals());

describe("Agent 对话：未启用", () => {
  it("没有启用大模型：说明原因、输入框禁用、不发任何请求", async () => {
    const { calls } = mockFetch({ "GET /api/auth/me": ME, "GET /api/agent/tasks": [] });
    open();
    expect(await screen.findByText("Agent 未启用")).toBeInTheDocument();
    expect(screen.getByText(/不会进行任何多智能体协作/)).toBeInTheDocument();
    expect(screen.getByLabelText("消息")).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    expect(calls.filter((c) => c.path.startsWith("/api/agent"))).toEqual([]);                  // 未启用时连任务列表都不请求
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("服务端返回 llm_not_enabled：显示明确提示", async () => {
    const user = userEvent.setup();
    mockFetch(base({ "POST /api/agent/messages": () => json(409, { detail: { error_code: "llm_not_enabled", message: "Agent 需要启用大模型：当前未启用" } }) }));
    open();
    await user.type(await screen.findByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/Agent 需要启用大模型/)).toBeInTheDocument();
  });
});

describe("Agent 对话：发送与结果", () => {
  it("发送后显示最终回答、规则判定表（来自 evidence）、审核入口和步骤；请求带 CSRF，任务号写入地址", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(base({ "POST /api/agent/messages": ANSWERED(), [`GET /api/agent/tasks/${TID}`]: ANSWERED() }));
    open();
    await user.type(await screen.findByLabelText("消息"), DEMO);
    await user.click(screen.getByRole("button", { name: "发送" }));
    const final = await screen.findByRole("article", { name: "最终回答" });
    expect(within(final).getByText(/PRJ-SM-003-R02 的 QC 未通过/)).toBeInTheDocument();
    expect(within(final).getByText("模型文字 · 已通过事实核对")).toBeInTheDocument();
    const row = within(final).getByRole("link", { name: "PRJ-SM-003-R02" }).closest("tr")!;       // 判定取自服务端填入的 evidence
    expect(within(row).getByText("校准")).toBeInTheDocument();
    expect(row.textContent).toMatch(/QC.*未通过/);
    expect(within(final).getByText(/不取自模型文字/)).toBeInTheDocument();
    expect(within(final).getByRole("link", { name: /打开审核页/ })).toHaveAttribute("href", `/reports/${DRAFT}`);
    expect(within(final).getByText(/Agent 不能批准或导出报告/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/agent/messages")!;
    expect(post.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(post.body).toEqual({ message: DEMO });
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("");              // 成功后清空输入
  });

  it("步骤：显示角色、工具名、白名单参数、状态和来源链接；不显示模型推理或原始参数", async () => {
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: ANSWERED() }));
    open(`/agent?task=${TID}`);
    const list = await screen.findByRole("list", { name: "" }).catch(() => null);
    void list;
    expect(await screen.findByText(/工具调用 · 分析批列表/)).toBeInTheDocument();
    expect(screen.getByText("project_id=PRJ-SM-003")).toBeInTheDocument();
    expect(screen.getAllByText("数据专家").length).toBeGreaterThan(0);
    expect(screen.getByText("无权调用")).toBeInTheDocument();                                    // 越权调用被拒绝也可见
    expect(screen.getByRole("link", { name: "分析批 R02" })).toHaveAttribute("href", "/?project=PRJ-SM-003&run=PRJ-SM-003-R02&tab=detail");
    expect(screen.getByRole("link", { name: "项目 PRJ-SM-003" })).toHaveAttribute("href", "/?project=PRJ-SM-003");
    expect(screen.getByRole("link", { name: `报告 ${DRAFT}` })).toHaveAttribute("href", `/reports/${DRAFT}`);
    expect(screen.getByText("知识 DEMO-SOP-DEV-004")).toBeInTheDocument();                       // 知识来源无页面，只作标签
    expect(screen.getByText(/不显示模型推理、提示词、工具结果正文或原始参数/)).toBeInTheDocument();
  });

  it("模型文字未通过校验：标明是系统摘要并给出原因", async () => {
    const f = { ...FINAL, answer_source: "server_fallback", answer: "分析批 PRJ-SM-003-R02（已完成）：QC 未通过",
      fallback_reason: ["verdict_conflict:PRJ-SM-003-R02", "unsupported_number:99"] };
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: ANSWERED({ final: f }) }));
    open(`/agent?task=${TID}`);
    expect(await screen.findByText("系统摘要 · 模型文字未通过校验")).toBeInTheDocument();
    expect(screen.getByText(/文字中的判定与规则结果冲突；出现了工具结果里没有的数字/)).toBeInTheDocument();
  });

  it("已终止 / 已失败的任务：显示原因，输入框锁定并提示新建任务", async () => {
    const f = { ...FINAL, answer_source: "server_summary", fallback_reason: ["max_rounds"], answer: "任务已因达到上限而终止" };
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: task({ status: "terminated", final: f, termination_reason: "max_rounds", conversation: [{ role: "user", content: "x" }] }) }));
    open(`/agent?task=${TID}`);
    expect((await screen.findAllByText("已终止", { selector: ".pill" })).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/达到模型调用轮数上限/).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("消息")).toBeDisabled();
    expect(screen.getAllByText("该任务已结束，请新建任务。").length).toBeGreaterThan(0);
  });

  it("审计不完整时明确提示，而不是当作完整", async () => {
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: ANSWERED({ audit_complete: false, audit_note: "部分步骤未能写入审计，留痕不完整" }) }));
    open(`/agent?task=${TID}`);
    expect(await screen.findByText("审计不完整")).toBeInTheDocument();
    expect(screen.getByText("留痕不完整", { selector: ".pill" })).toBeInTheDocument();
  });
});

describe("Agent 对话：待确认的写操作", () => {
  it("显示待执行动作与有效期；输入框锁定；确认只发送一次并带参数哈希；哈希不显示给用户", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: AWAITING(),
      [`POST /api/agent/tasks/${TID}/actions/A1/confirm`]: () => new Promise((r) => setTimeout(() => r(ANSWERED()), 150)),   // 慢响应：第二次点击落在处理中
    }));
    open(`/agent?task=${TID}`);
    const card = await screen.findByRole("region", { name: "待确认的操作" });
    expect(within(card).getByText("待你确认：生成报告草稿")).toBeInTheDocument();
    expect(within(card).getByText(/不会自动审核或导出/)).toBeInTheDocument();
    expect(within(card).getByText("sample_analysis")).toBeInTheDocument();
    expect(card.textContent).not.toContain(HASH);
    expect(screen.getByLabelText("消息")).toBeDisabled();
    expect(screen.getAllByText("请先确认或拒绝上方待执行的操作。").length).toBeGreaterThan(0);
    await user.dblClick(within(card).getByRole("button", { name: "确认执行" }));                // 双击：只处理第一次
    await screen.findByRole("article", { name: "最终回答" });
    const confirms = calls.filter((c) => c.method === "POST" && c.path.endsWith("/confirm"));
    expect(confirms).toHaveLength(1);
    expect(confirms[0].body).toEqual({ args_hash: HASH });
    expect(confirms[0].headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(screen.queryByRole("region", { name: "待确认的操作" })).toBeNull();
  });

  it("拒绝：发送 reject，不发送确认", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: AWAITING(), [`POST /api/agent/tasks/${TID}/actions/A1/reject`]: () => task({ conversation: [{ role: "user", content: DEMO }] }),
    }));
    open(`/agent?task=${TID}`);
    await user.click(await screen.findByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/reject"))).toBe(true));
    expect(calls.some((c) => c.path.endsWith("/confirm"))).toBe(false);
  });

  it("已过期的待确认动作：不能再确认", async () => {
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: AWAITING({ pending_actions: [{ ...PENDING, expires_at: PAST }] }) }));
    open(`/agent?task=${TID}`);
    const card = await screen.findByRole("region", { name: "待确认的操作" });
    expect(within(card).getByText("已过期")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "确认执行" })).toBeDisabled();
  });

  it("审计不可用：明确说明操作未执行", async () => {
    const user = userEvent.setup();
    mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: AWAITING(),
      [`POST /api/agent/tasks/${TID}/actions/A1/confirm`]: () => json(503, { detail: { error_code: "audit_unavailable", message: "审计暂时不可用，操作未执行；请稍后重试确认" } }),
    }));
    open(`/agent?task=${TID}`);
    await user.click(await screen.findByRole("button", { name: "确认执行" }));
    expect(await screen.findByText("操作未执行")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeEnabled();                    // 仍可重试
  });

  it("重复确认（服务端返回 replayed）：提示没有重复执行", async () => {
    const user = userEvent.setup();
    mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: AWAITING(),
      [`POST /api/agent/tasks/${TID}/actions/A1/confirm`]: () => ({ ...ANSWERED(), replayed: true }),
    }));
    open(`/agent?task=${TID}`);
    await user.click(await screen.findByRole("button", { name: "确认执行" }));
    expect(await screen.findByText("该操作此前已确认，没有重复执行。")).toBeInTheDocument();
  });
});

describe("Agent 对话：等待、超时、取消、失败、会话过期", () => {
  const hang = (init?: RequestInit) => new Promise<Response>((_, rej) => init?.signal?.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError"))));
  function stubHangingSend() {
    const { fn, calls } = mockFetch(base());
    vi.stubGlobal("fetch", (i: RequestInfo | URL, init?: RequestInit) => (String(i).includes("/api/agent/messages") ? (calls.push({ method: "POST", path: "/api/agent/messages", body: undefined, headers: {} }), hang(init)) : fn(i, init)));
    return calls;
  }

  it("处理中：显示进度和“停止等待”，重复点击只发一次；停止后可再次发送且保留输入", async () => {
    const user = userEvent.setup();
    const calls = stubHangingSend();
    open();
    await user.type(await screen.findByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("status", { name: "处理中" })).toBeInTheDocument();
    expect(screen.getByText(/停止等待不会撤销已经开始的处理/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送/ })).toBeDisabled();
    expect(calls.filter((c) => c.path === "/api/agent/messages")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "停止等待" }));
    expect(await screen.findByText("已停止等待")).toBeInTheDocument();
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("你好");           // 输入不丢
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
  });

  it("超时：提示服务端可能仍在处理，写操作不会重复执行", async () => {
    agentConfig.timeoutMs = 40;
    const user = userEvent.setup();
    stubHangingSend();
    open();
    await user.type(await screen.findByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("等待超时")).toBeInTheDocument();
    expect(screen.getByText(/服务端可能仍在处理/)).toBeInTheDocument();
    expect(screen.getByText(/确认前不会重复执行写操作/)).toBeInTheDocument();
  });

  it("任务繁忙（409 task_busy）：显示服务端提示并刷新任务状态", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: ANSWERED(),
      "POST /api/agent/messages": () => json(409, { detail: { error_code: "task_busy", message: "任务正在处理或在等待你确认待执行的操作" } }),
    }));
    open(`/agent?task=${TID}`);
    await user.type(await screen.findByLabelText("消息"), "继续");
    const before = calls.filter((c) => c.path === `/api/agent/tasks/${TID}`).length;
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/任务正在处理或在等待你确认/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path === `/api/agent/tasks/${TID}`).length).toBeGreaterThan(before));
  });

  it("网络错误：显示错误并可重试；输入保留", async () => {
    const user = userEvent.setup();
    const { fn } = mockFetch(base());
    vi.stubGlobal("fetch", (i: RequestInfo | URL, init?: RequestInit) => { if (String(i).includes("/api/agent/messages")) throw new TypeError("offline"); return fn(i, init); });
    open();
    await user.type(await screen.findByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/无法连接服务/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("你好");
  });

  it("登录会话过期（401）：通知应用回到登录页，而不是静默失败", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    window.addEventListener("auth:expired", spy);
    mockFetch(base({ "POST /api/agent/messages": () => json(401, { detail: "invalid or missing token" }) }));
    open();
    await user.type(await screen.findByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    window.removeEventListener("auth:expired", spy);
  });

  it("任务已过期（服务端状态 expired）：锁定输入并提示新建", async () => {
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: task({ status: "expired" }) }));
    open(`/agent?task=${TID}`);
    expect((await screen.findAllByText("任务已过期，请新建任务。")).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("消息")).toBeDisabled();
  });

  it("任务不存在或不属于当前会话（404）：错误块 + 输入锁定", async () => {
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: () => json(404, { detail: { error_code: "task_not_found", message: "任务不存在" } }) }));
    open(`/agent?task=${TID}`);
    expect(await screen.findByText(/任务不存在/)).toBeInTheDocument();
    expect(screen.getByLabelText("消息")).toBeDisabled();
  });

  it("别处仍在处理（status=running）：自动轮询直到完成", async () => {
    let n = 0;
    mockFetch(base({ [`GET /api/agent/tasks/${TID}`]: () => (++n === 1 ? task({ status: "running", active_role: "data", active_role_zh: "数据专家", conversation: [{ role: "user", content: "x" }] }) : ANSWERED()) }));
    open(`/agent?task=${TID}`);
    expect(await screen.findAllByText("处理中")).not.toHaveLength(0);
    expect(screen.getAllByText("任务仍在处理中，完成后才能继续提问。").length).toBeGreaterThan(0);
    await screen.findByRole("article", { name: "最终回答" }, { timeout: 5000 });
  }, 10_000);
});

describe("Agent 对话：任务列表与审计记录", () => {
  it("我的任务：显示预览与状态，点击后加载该任务", async () => {
    const user = userEvent.setup();
    mockFetch({ "GET /api/auth/me": ON,
      "GET /api/agent/tasks": [{ task_id: TID, status: "idle", created_at: "2026-10-27T08:00:00+00:00", updated_at: "2026-10-27T08:01:00+00:00", preview: "查询项目 PRJ-SM-003" }],
      [`GET /api/agent/tasks/${TID}`]: ANSWERED() });
    open();
    const title = await screen.findByText("查询项目 PRJ-SM-003", { selector: ".list__title" });
    await user.click(title.closest("button")!);
    expect(await screen.findByRole("article", { name: "最终回答" })).toBeInTheDocument();
  });

  it("审计记录：展开后显示步骤、请求号前缀和哈希前缀，并说明不保存正文", async () => {
    const user = userEvent.setup();
    mockFetch(base({
      [`GET /api/agent/tasks/${TID}`]: ANSWERED(),
      [`GET /api/agent/tasks/${TID}/trace`]: { task_id: TID, status: "idle", audit_complete: true, verdict_source: "app.rules",
        content_policy: "审计只保存白名单参数摘要、来源编号和内容哈希；不保存提示词、模型输出、工具结果或用户消息正文", http_audit: [],
        steps: [{ ...step(1, "user_message", "orchestrator"), request_id: "abcdef1234567890", operator: "alice", content_sha256: "0123456789abcdef".repeat(4), tokens: null }] },
    }));
    open(`/agent?task=${TID}`);
    await user.click(await screen.findByText("审计记录（仅你可见）"));
    expect(await screen.findByText("abcdef12")).toBeInTheDocument();
    expect(screen.getByText("01234567")).toBeInTheDocument();
    expect(screen.getByText(/不保存提示词/)).toBeInTheDocument();
  });

  it("空任务：给出示例，点击示例填入输入框（不自动发送）", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch(base());
    open();
    await user.click(await screen.findByRole("button", { name: DEMO }));
    expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe(DEMO);
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
