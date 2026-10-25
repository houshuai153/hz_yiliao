import type { RunStatus } from "../api/types";

/** 图表色规范：唯一的图表色（--hue）只给主数据；状态色只出现在小标记上；其余用中性灰。 */
export const C = {
  hue: "#1d5fbf", amber: "#d99a1b", red: "#b4232a", ink: "#0d1117", grey: "#8a94a6", slate: "#3b4252",
  grid: "#e3e7ed", track: "#e3e7ed", axis: "#566070",
} as const;

export const STATUS_FILL: Record<RunStatus, string> = { completed: C.hue, under_review: C.amber, in_progress: "url(#hatch)" };
export const FLAG_FILL: Record<string, string> = { ok: C.hue, BLQ: C.grey, above_ULOQ: C.slate, reanalysis: C.amber, missing: C.red };

/** 进行中 = 斜纹填充（形状编码，不只靠颜色）。放进每个图表的 <defs>。 */
export function HatchDefs() {
  return (
    <defs>
      <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="6" height="6" fill="#ffffff" />
        <line x1="0" y1="0" x2="0" y2="6" stroke={C.ink} strokeWidth="2" />
      </pattern>
    </defs>
  );
}

export function Tip({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="chart-tip">
      <div className="chart-tip__title">{title}</div>
      {rows.map(([k, v]) => <div key={k} className="chart-tip__row"><span>{k}</span><span>{v}</span></div>)}
    </div>
  );
}
