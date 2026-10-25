import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { makeClient } from "../App";
import { json, mockFetch, ME, HEALTH } from "./helpers";
import { Qa } from "../pages/Qa";
import { Translate } from "../pages/Translate";
import { renderAt } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("登录与会话", () => {
  it("未登录显示登录页；错误口令给出提示且不泄露哪一项错；成功后进入工作台", async () => {
    const user = userEvent.setup();
    let signedIn = false;
    const { calls } = mockFetch({
      "GET /api/auth/me": () => (signedIn ? ME : json(401, { detail: "not signed in" })),
      "POST /api/auth/login": (init: RequestInit | undefined) => {
        const b = JSON.parse(String(init?.body));
        if (b.password !== "right-password") return json(401, { detail: { error_code: "invalid_credentials", message: "用户名或口令不正确" } });
        signedIn = true; return { username: "tester", csrf_token: "c" };
      },
      "GET /api/health": HEALTH, "GET /api/reports": [], "GET /api/dashboard/overview": () => json(500, { detail: "x" }), "GET /api/dashboard/charts": () => json(500, { detail: "x" }),
    });
    render(<QueryClientProvider client={makeClient()}><App /></QueryClientProvider>);
    await user.type(await screen.findByLabelText("用户名"), "tester");
    await user.type(screen.getByLabelText("口令"), "bad-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("用户名或口令不正确")).toBeInTheDocument();
    expect((screen.getByLabelText("口令") as HTMLInputElement).value).toBe("");              // 失败后清空口令
    await user.type(screen.getByLabelText("口令"), "right-password");
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("navigation", { name: "主导航" })).toBeInTheDocument();
    expect(screen.getByText("tester")).toBeInTheDocument();                                   // 操作人记录 = 登录名
    expect(calls.filter((c) => c.path === "/api/auth/login").every((c) => !("Authorization" in c.headers))).toBe(true);
  });

  it("服务不可达：登录页提示无法连接并可重试", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    render(<QueryClientProvider client={makeClient()}><App /></QueryClientProvider>);
    expect(await screen.findByText("无法连接服务")).toBeInTheDocument();
  });

  it("会话过期（401）后回到登录页", async () => {
    let first = true;
    mockFetch({ "GET /api/auth/me": () => ME, "GET /api/health": HEALTH, "GET /api/reports": [],
      "GET /api/dashboard/overview": () => { if (first) { first = false; return json(401, { detail: "invalid or missing token" }); } return {}; },
      "GET /api/dashboard/charts": () => json(401, { detail: "x" }) });
    render(<QueryClientProvider client={makeClient()}><App /></QueryClientProvider>);
    expect(await screen.findByLabelText("用户名")).toBeInTheDocument();
  });
});

describe("法规问答", () => {
  const CITE = { doc_id: "DEMO-SOP-DEV", version: "1.0", section_id: "DEMO-SOP-DEV-004", title: "纠正预防措施与关闭", source_type: "demo_sop", is_demo: true, label: "模拟内部 SOP（无合规效力）", quote: "偏差在 CAPA 完成后方可关闭。" };
  it("作答：答案、引用（文档/版本/章节）分区显示", async () => {
    const user = userEvent.setup();
    mockFetch({ "GET /api/auth/me": ME, "POST /api/regulatory/ask": { answer: "• 偏差在 CAPA 完成后方可关闭。【DEMO-SOP-DEV-004】", citations: [CITE], confidence: 0.72, confidence_basis: {}, refused: false, refusal_reason: null, answer_mode: "extractive", extractive_answer: null, llm: {}, request_id: "r1" } });
    renderAt(<Qa />, { path: "/qa", route: "/qa" });
    await user.type(await screen.findByLabelText("问题"), "偏差在什么条件下可以关闭");
    await user.click(screen.getByRole("button", { name: "提问" }));
    expect(await screen.findByText(/偏差在 CAPA 完成后方可关闭。【DEMO-SOP-DEV-004】/, { selector: ".answer" })).toBeInTheDocument();
    expect(screen.getByText("DEMO-SOP-DEV-004", { selector: ".cite__id" })).toBeInTheDocument();
    expect(screen.getByText(/文档 DEMO-SOP-DEV · 版本 1.0/)).toBeInTheDocument();
    expect(screen.getByText(/不是监管机构原文/)).toBeInTheDocument();
  });
  it("拒答：醒目显示原因，没有引用，并说明拒答是设计行为", async () => {
    const user = userEvent.setup();
    mockFetch({ "GET /api/auth/me": ME, "POST /api/regulatory/ask": { answer: "无法提供条款编号或监管原文。", citations: [], confidence: 0, confidence_basis: {}, refused: true, refusal_reason: "clause_fabrication_risk", answer_mode: "extractive", extractive_answer: null, llm: {}, request_id: "r2" } });
    renderAt(<Qa />, { path: "/qa", route: "/qa" });
    await user.type(await screen.findByLabelText("问题"), "请给出 ICH M10 第 7.3.2 条的原文");
    await user.click(screen.getByRole("button", { name: "提问" }));
    expect(await screen.findByText("已拒答：要求条款编号或监管原文")).toBeInTheDocument();
    expect(screen.getByText("没有引用")).toBeInTheDocument();
    expect(screen.getByText(/不会编造条款编号/)).toBeInTheDocument();
  });
  it("提问失败：显示错误并可重试", async () => {
    const user = userEvent.setup();
    mockFetch({ "GET /api/auth/me": ME, "POST /api/regulatory/ask": () => json(503, { detail: "索引不可用" }) });
    renderAt(<Qa />, { path: "/qa", route: "/qa" });
    await user.type(await screen.findByLabelText("问题"), "x");
    await user.click(screen.getByRole("button", { name: "提问" }));
    expect(await screen.findByText(/索引不可用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

describe("翻译与术语核对", () => {
  const CAPS = { provider: "deterministic", is_generative: false, note: "离线规则实现：仅翻译已登记的固定句型。", glossary_note: "演示术语表", glossary_terms: 44, patterns: [{ id: "P-1", zh: "共分析{n}份", en: "A total of {n}" }] };
  it("不支持的句子不伪装译文；术语命中可查看；说明不是通用机器翻译", async () => {
    const user = userEvent.setup();
    mockFetch({ "GET /api/auth/me": ME, "GET /api/translate/capabilities": CAPS, "POST /api/translate": {
      direction: "zh2en", document_warnings: [], stats: { translated: 0, partial: 0, unsupported: 1, checked: 0 }, capability_note: "", glossary_note: "演示术语表", request_id: "t1",
      paragraphs: [{ index: 0, source: "该化合物的半衰期约为4小时。", source_lang: "zh", target_lang: "en", direction: "zh2en", target: null, status: "unsupported", sentences: [],
        warnings: [{ code: "UNSUPPORTED_PATTERN", severity: "warning", message: "当前离线实现不支持该句型，未生成译文" }],
        term_hits: [{ term_id: "T-1", matched_text: "半衰期", kind: "zh", span: [5, 8], case_ok: true, expected: { zh: "半衰期", en: "half-life", abbr: null } }] }] } });
    renderAt(<Translate />, { path: "/translate", route: "/translate" });
    expect(await screen.findByText(/这不是通用机器翻译/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/原文/), "该化合物的半衰期约为4小时。");
    await user.click(screen.getByRole("button", { name: "翻译并核对" }));
    expect(await screen.findByText(/未生成译文，需人工翻译/)).toBeInTheDocument();
    expect(screen.getByText("UNSUPPORTED_PATTERN")).toBeInTheDocument();
    expect(document.querySelector("mark.term")?.textContent).toBe("半衰期");
    await waitFor(() => expect(screen.getByText("不支持（未生成译文）")).toBeInTheDocument());
  });
});

// 避免“未使用”告警：MemoryRouter 用于需要路由上下文的单测扩展
void MemoryRouter;
