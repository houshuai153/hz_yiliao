import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportReview } from "../pages/ReportReview";
import { Reports } from "../pages/Reports";
import { json, mockFetch, renderAt, ME, HEALTH } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const sentence = (bid: string, text: string, extra: Record<string, unknown> = {}) => ({ type: "sentence", bid, kind: "static", text, fact_keys: [], tags: [], missing: [], edited: false, added: false, original_text: null, flag: null, editable: true, open_issues: 0, comment_count: 0, ...extra });
const READINESS = (over: Record<string, unknown> = {}) => ({ blockers: [], missing_to_acknowledge: [], required_confirmations: { facts_checked: "已逐项核对业务结论及其数据来源标签", verdicts_untouched: "确认判定来自规则引擎", not_formal_approval: "知悉：不等于正式 QA 批准" }, open_issue_count: 0, can_approve: true, ...over });
const DETAIL = (status: string, over: Record<string, unknown> = {}) => ({
  draft_id: "DRAFT-1", project_id: "PRJ-A", kind: "validation", run_ids: ["PRJ-A-R01"], review_status: status, review_status_zh: "x", current_version: 2, legacy: false,
  created_at: "2026-10-24T08:00:00+00:00", missing_fields: [], banner: "AI 辅助草稿，需人工复核", title: "方法学验证报告（AI 辅助草稿）", viewing_version: 2, is_current: true,
  sections: [{ title: "1 目的与范围", blocks: [sentence("B001", "本草稿汇总校准曲线信息（审核补充）。", { edited: true, original_text: "本草稿汇总校准曲线信息。" }), sentence("B002", "仪器：[待补充: 仪器型号]", { kind: "placeholder", editable: false })] }],
  facts: [], comments: [], readiness: READINESS(), stats: { fact_count: 3, fact_sentences: 1, edited_blocks: 1, flagged_blocks: 0, missing_fields: 1 }, export_ready: status === "reviewed", ...over,
});
const base = (detail: unknown) => ({ "GET /api/auth/me": ME, "GET /api/health": HEALTH, "GET /api/reports": [], "GET /api/reports/DRAFT-1": detail, "GET /api/reports/DRAFT-1/versions": [], "GET /api/reports/DRAFT-1/events": [] });
const open = () => renderAt(<ReportReview />, { path: "/reports/DRAFT-1", route: "/reports/:draftId" });

describe("报告审核：导出门禁在界面上的表现（真正的门禁在后端）", () => {
  it.each(["draft", "in_review", "changes_requested"])("状态 %s：页面上没有任何导出入口", async (st) => {
    mockFetch(base(DETAIL(st)));
    open();
    await screen.findByRole("heading", { name: /方法学验证报告/ });
    expect(screen.queryByRole("button", { name: /导出/ })).toBeNull();
  });

  it("审核通过：出现导出按钮，并声明不等于正式 QA 批准 / 非电子签名", async () => {
    mockFetch(base(DETAIL("reviewed")));
    open();
    expect(await screen.findByRole("button", { name: /导出 DOCX/ })).toBeEnabled();
    expect(screen.getByText(/不等于正式 QA 批准/, { selector: ".notice__body" })).toBeInTheDocument();
    expect(screen.getAllByText(/非电子签名/).length).toBeGreaterThan(0);
  });

  it("导出走带会话的下载接口；后端拒绝时给出错误提示，不保存文件", async () => {
    const user = userEvent.setup();
    mockFetch({ ...base(DETAIL("reviewed")), "GET /api/reports/DRAFT-1/download": () => json(409, { detail: { error_code: "report_not_approved", message: "只有审核通过的报告才能导出 DOCX" } }) });
    open();
    await user.click(await screen.findByRole("button", { name: /导出 DOCX/ }));
    expect(await screen.findByText("只有审核通过的报告才能导出 DOCX")).toBeInTheDocument();
  });

  it("旧草稿：提示重新生成，没有审核或导出操作", async () => {
    mockFetch(base({ ...DETAIL("draft"), legacy: true, current_version: null, message: "该草稿生成于审核功能上线之前，没有保存可审核的结构化内容，请重新生成草稿。" }));
    open();
    expect(await screen.findByText(/请重新生成草稿/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开始审核|导出|审核通过/ })).toBeNull();
  });
});

describe("报告审核：审核通过前检查", () => {
  it("有阻止项：显示原因且“审核通过”不可用", async () => {
    mockFetch(base(DETAIL("in_review", { readiness: READINESS({ can_approve: false, blockers: [{ code: "open_issues", message: "仍有 1 条未解决问题" }], open_issue_count: 1 }) })));
    open();
    expect(await screen.findByText("仍有 1 条未解决问题")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
  });

  it("待补充字段须逐项填写处理说明；说明明确“不会补齐数据”；意见与确认项齐备后才可提交", async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({ ...base(DETAIL("in_review", { readiness: READINESS({ missing_to_acknowledge: ["仪器型号"] }) })), "POST /api/reports/DRAFT-1/transition": { review_status: "reviewed" } });
    open();
    const approve = await screen.findByRole("button", { name: "审核通过" });
    expect(screen.getByText(/不会补齐数据/)).toBeInTheDocument();
    expect(approve).toBeDisabled();
    await user.type(screen.getByLabelText(/审核意见/), "已核对");
    for (const c of screen.getAllByRole("checkbox").filter((x) => x.closest("fieldset"))) await user.click(c);
    expect(approve).toBeDisabled();                                                          // 还缺“仪器型号”的处理说明
    await user.type(screen.getByLabelText("仪器型号"), "待业务提供记录后重新生成");
    expect(approve).toBeEnabled();
    await user.click(approve);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/transition"))).toBe(true));
    const body = calls.find((c) => c.path.endsWith("/transition"))!.body as Record<string, unknown>;
    expect(body).toMatchObject({ action: "approve", comment: "已核对", missing_ack: { "仪器型号": "待业务提供记录后重新生成" } });
    expect((body.confirmations as string[]).sort()).toEqual(["facts_checked", "not_formal_approval", "verdicts_untouched"]);
  });

  it("预览：修订前后对比、待补充标记、占位句不可选中修订", async () => {
    mockFetch(base(DETAIL("in_review")));
    open();
    expect(await screen.findByText("生成时原文")).toBeInTheDocument();
    expect(document.querySelector(".diff__old")?.textContent).toBe("本草稿汇总校准曲线信息。");
    expect(document.querySelector("mark.mark-missing")?.textContent).toBe("[待补充: 仪器型号]");
    expect(document.getElementById("B002")).toHaveAttribute("data-selectable", "true");     // 可选中查看意见，但“修订”页签会拒绝改写
  });

  it("历史版本只读", async () => {
    mockFetch(base(DETAIL("in_review", { is_current: false, viewing_version: 1 })));
    open();
    expect(await screen.findByText(/正在查看历史版本，只读/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核通过" })).toBeNull();
  });
});

describe("报告草稿列表", () => {
  const D = (id: string, status: string, legacy = false) => ({ draft_id: id, project_id: "PRJ-A", kind: "validation", run_ids: [], review_status: status, review_status_zh: "x", current_version: legacy ? null : 1, legacy, created_at: "2026-10-24T08:00:00+00:00", missing_fields: [], open_issues: 0 });
  it("旧草稿不计入任何状态筛选，也不会显示为“审核通过”", async () => {
    mockFetch({ "GET /api/auth/me": ME, "GET /api/reports": [D("D1", "draft"), D("D2", "reviewed"), D("D3", "draft", true)] });
    renderAt(<Reports />, { path: "/reports", route: "/reports" });
    const seg = await screen.findByRole("group", { name: "状态筛选" });
    expect(await within(seg).findByRole("button", { name: "全部 3" })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: "待审核 1" })).toBeInTheDocument();
    expect(within(seg).getByRole("button", { name: "审核通过 1" })).toBeInTheDocument();
    expect(screen.getByText("旧草稿 · 不可审核")).toBeInTheDocument();
  });
  it("空列表给出下一步指引", async () => {
    mockFetch({ "GET /api/auth/me": ME, "GET /api/reports": [] });
    renderAt(<Reports />, { path: "/reports", route: "/reports" });
    expect(await screen.findByText("还没有草稿")).toBeInTheDocument();
  });
  it("列表加载失败：错误块带重试", async () => {
    mockFetch({ "GET /api/auth/me": ME, "GET /api/reports": () => json(500, { detail: "db down" }) });
    renderAt(<Reports />, { path: "/reports", route: "/reports" });
    expect(await screen.findByText(/db down/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
