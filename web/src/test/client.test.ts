import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, downloadFile, setCsrf } from "../api/client";
import { json, mockFetch } from "./helpers";

afterEach(() => { vi.unstubAllGlobals(); setCsrf(null); });

describe("API 客户端", () => {
  it("写操作带 CSRF 令牌，读操作不带；任何请求都不带 Authorization", async () => {
    const { calls } = mockFetch({ "GET /api/x": { ok: 1 }, "POST /api/y": { ok: 2 } });
    setCsrf("tok-1");
    await api("/api/x");
    await api("/api/y", { method: "POST", body: { a: 1 } });
    expect(calls[0].headers["X-CSRF-Token"]).toBeUndefined();
    expect(calls[1].headers["X-CSRF-Token"]).toBe("tok-1");
    expect(calls.every((c) => !("Authorization" in c.headers))).toBe(true);
  });

  it("同源并带会话 Cookie（credentials=same-origin），查询参数忽略空值", async () => {
    const { fn } = mockFetch({ "GET /api/q": {} });
    await api("/api/q", { params: { a: "1", b: "", c: undefined, d: 0 } });
    expect(String(fn.mock.calls[0][0])).toBe("/api/q?a=1&d=0");
    expect(fn.mock.calls[0][1]?.credentials).toBe("same-origin");
  });

  it("解析后端业务错误 {error_code, message}", async () => {
    mockFetch({ "GET /api/e": () => json(409, { detail: { error_code: "report_not_approved", message: "只有审核通过的报告才能导出" } }) });
    await expect(api("/api/e")).rejects.toMatchObject({ status: 409, code: "report_not_approved", message: "只有审核通过的报告才能导出" });
  });

  it("422 校验错误给出可读文案；网络失败状态为 0", async () => {
    mockFetch({ "POST /api/v": () => json(422, { detail: [{ loc: ["body", "x"], msg: "bad" }] }) });
    await expect(api("/api/v", { method: "POST", body: {} })).rejects.toMatchObject({ status: 422, code: "validation" });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    await expect(api("/api/z")).rejects.toMatchObject({ status: 0, code: "network" });
  });

  it("401 触发 auth:expired（登录接口自身除外）", async () => {
    mockFetch({ "GET /api/p": () => json(401, { detail: "invalid or missing token" }), "POST /api/auth/login": () => json(401, { detail: { error_code: "invalid_credentials", message: "用户名或口令不正确" } }) });
    const spy = vi.fn();
    window.addEventListener("auth:expired", spy);
    await expect(api("/api/p")).rejects.toBeInstanceOf(ApiError);
    expect(spy).toHaveBeenCalledTimes(1);
    await expect(api("/api/auth/login", { method: "POST", body: {} })).rejects.toMatchObject({ code: "invalid_credentials" });
    expect(spy).toHaveBeenCalledTimes(1);
    window.removeEventListener("auth:expired", spy);
  });

  it("导出被后端门禁拒绝时抛出 ApiError，不产生文件", async () => {
    mockFetch({ "GET /api/reports/D1/download": () => json(409, { detail: { error_code: "report_not_approved", message: "草稿状态为“待审核”，只有“审核通过”的报告才能导出 DOCX" } }) });
    await expect(downloadFile("/api/reports/D1/download")).rejects.toMatchObject({ status: 409, code: "report_not_approved" });
  });

  it("从 Content-Disposition 取文件名", async () => {
    mockFetch({ "GET /api/reports/D2/download": () => new Response(new Blob(["x"]), { status: 200, headers: { "Content-Disposition": 'attachment; filename="D2-v3-reviewed.docx"' } }) });
    const f = await downloadFile("/api/reports/D2/download");
    expect(f.filename).toBe("D2-v3-reviewed.docx");
  });
});
