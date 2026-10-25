import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import type { RunStatus, TimelinePoint } from "../api/types";
import { RUN_STATUS_ZH } from "../lib/labels";
import { C, Tip } from "./theme";

interface Pt { t: number; y: number; id: string; status: RunStatus; date: string; after: boolean }
const SHAPE: Record<RunStatus, "circle" | "diamond" | "square"> = { completed: "circle", under_review: "diamond", in_progress: "square" };
const FILL: Record<RunStatus, string> = { completed: C.hue, under_review: C.amber, in_progress: "#ffffff" };

function Marker(props: { cx?: number; cy?: number; payload?: Pt }) {
  const { cx = 0, cy = 0, payload } = props;
  if (!payload) return null;
  const s = 6;
  const common = { fill: FILL[payload.status], stroke: C.ink, strokeWidth: 1.4 };
  if (SHAPE[payload.status] === "circle") return <circle cx={cx} cy={cy} r={s} {...common} />;
  if (SHAPE[payload.status] === "diamond") return <path d={`M${cx} ${cy - s - 1}L${cx + s + 1} ${cy}L${cx} ${cy + s + 1}L${cx - s - 1} ${cy}Z`} {...common} />;
  return <rect x={cx - s} y={cy - s} width={s * 2} height={s * 2} {...common} />;
}

const dayMs = 86_400_000;
const fmtDay = (t: number) => new Date(t).toISOString().slice(0, 10);

/** 按分析批日期（run_date）累计的批次数：阶梯线，只有真实日期的点，不补点。颜色 / 形状 = 各批当前状态。 */
export function CumulativeChart({ points }: { points: TimelinePoint[] }) {
  const data: Pt[] = points.flatMap((p) => p.runs.map((r) => ({ t: Date.parse(p.date), y: r.cumulative_rank, id: r.id, status: r.status, date: p.date, after: p.after_as_of })));
  const lo = Math.min(...data.map((d) => d.t)), hi = Math.max(...data.map((d) => d.t));
  const pad = Math.max(7 * dayMs, (hi - lo) * 0.05);
  const top = Math.max(...data.map((d) => d.y));
  // 横轴刻度只取真实的分析批日期（最多 3 个：首、中、尾，均匀挑选并保留首尾），不生成不存在的日期
  const dates = [...new Set(data.map((d) => d.t))].sort((a, b) => a - b);
  const span = hi - lo || 1;
  const mid = dates[Math.floor((dates.length - 1) / 2)];
  const ticks = [dates[0], ...(dates.length > 2 && mid - dates[0] >= span * 0.3 && dates[dates.length - 1] - mid >= span * 0.3 ? [mid] : []), dates[dates.length - 1]]
    .filter((t, i, a) => a.indexOf(t) === i);
  return (
    <>
      <div className="chart" style={{ height: 400 }}>
        <ResponsiveContainer width="100%" height="100%" aria-hidden>
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="t" type="number" scale="time" domain={[lo - pad, hi + pad]} ticks={ticks} interval={0} tickFormatter={fmtDay}
            tick={{ fontSize: 12, fill: C.axis }} axisLine={{ stroke: C.axis }} tickLine={false} />
          <YAxis dataKey="y" type="number" domain={[0, top + 1]} allowDecimals={false} ticks={Array.from({ length: top + 2 }, (_, i) => i)} interval={0} tick={{ fontSize: 12, fill: C.axis }} axisLine={false} tickLine={false} width={32} />
          <Tooltip cursor={{ stroke: C.grid }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const d = payload[0].payload as Pt;
            return <Tip title={d.id} rows={[["分析批日期", d.date], ["累计分析批", `${d.y} 批`], ["当前状态", RUN_STATUS_ZH[d.status]], ...(d.after ? [["说明", "晚于统计截止日期"] as [string, string]] : [])]} />;
          }} />
          <Line type="stepAfter" dataKey="y" stroke={C.grey} strokeWidth={1.8} dot={false} activeDot={false} isAnimationActive={false} legendType="none" />
          <Scatter dataKey="y" shape={<Marker />} isAnimationActive={false} />
          <ReferenceLine y={0} stroke={C.axis} />
        </ComposedChart>
      </ResponsiveContainer>
      </div>
      <details className="chartdata"><summary>查看图表数据（表格）</summary>
        <table className="datatable">
        <caption>按分析批日期累计的批次数</caption>
        <thead><tr><th>日期</th><th>分析批</th><th>当前状态</th><th>累计</th></tr></thead>
        <tbody>{data.map((d) => <tr key={d.id}><td>{d.date}</td><td>{d.id}</td><td>{RUN_STATUS_ZH[d.status]}</td><td>{d.y}</td></tr>)}</tbody>
      </table>
      </details>
    </>
  );
}

export function TimelineLegend() {
  return (
    <div className="chart__legend" aria-hidden>
      <span><i className="swatch swatch--circle" style={{ background: C.hue, border: "1px solid #0d1117" }} />已完成</span>
      <span><i className="swatch swatch--diamond" style={{ background: C.amber, border: "1px solid #0d1117" }} />复核中</span>
      <span><i className="swatch" style={{ background: "#fff", border: "1.4px solid #0d1117" }} />进行中</span>
      <span>（颜色与形状 = 该批<strong style={{ fontWeight: 600 }}>当前</strong>状态）</span>
    </div>
  );
}
