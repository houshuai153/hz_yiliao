import { describe, expect, it } from "vitest";
import { fmtDateTime, fmtPct, rateText } from "../lib/format";

describe("格式化（只显示后端给出的值）", () => {
  it("分母为 0：显示“—”和原因，而不是 0%", () => {
    expect(rateText({ value: null, numerator: 0, denominator: 0, reason: "no_decided_runs" })).toEqual({ main: "—", note: "暂无已判定分析批", missing: true });
    expect(rateText({ value: null, numerator: 0, denominator: 0, reason: "no_planned_runs" }).note).toBe("未设置计划分析批数");
  });
  it("有值：百分比 + 分子/分母", () => {
    expect(rateText({ value: 0.5833, numerator: 7, denominator: 12, reason: null })).toEqual({ main: "58.3%", note: "7 / 12", missing: false });
    expect(rateText({ value: 0, numerator: 0, denominator: 3, reason: null }).main).toBe("0.0%");       // 真实的 0 要写成 0
  });
  it("占比缺失显示短横线", () => { expect(fmtPct(null)).toBe("—"); expect(fmtPct(0)).toBe("0.0%"); });
  it("无法解析的时间原样截断，不抛错", () => { expect(fmtDateTime("not-a-date-at-all")).toBe("not-a-date-at-al"); });
});
