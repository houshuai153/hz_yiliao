import type { Rate } from "../api/types";
import { REASON_ZH } from "./labels";

/** 只负责显示：值与分子分母由后端给出；分母为 0 时显示灰色说明而不是 0%。 */
export function rateText(r: Rate): { main: string; note: string; missing: boolean } {
  if (r.value === null) return { main: "—", note: REASON_ZH[r.reason ?? ""] ?? "暂无数据", missing: true };
  return { main: `${(r.value * 100).toFixed(1)}%`, note: `${r.numerator} / ${r.denominator}`, missing: false };
}
export const fmtInt = (n: number) => n.toLocaleString("zh-CN");
export const fmtPct = (share: number | null) => (share === null ? "—" : `${(share * 100).toFixed(1)}%`);
const pad = (n: number) => String(n).padStart(2, "0");
export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export const shortRun = (id: string) => id.split("-").slice(-1)[0];
