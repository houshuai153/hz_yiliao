import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { SampleComposition } from "../api/types";
import { fmtInt, fmtPct } from "../lib/format";
import { SAMPLE_FLAG_ZH } from "../lib/labels";
import { EmptyState } from "../components/ui";
import { FLAG_FILL, Tip } from "./theme";

/** 样本结果构成：细环 + 图例表（名称 / 数量 / 占比）。总数为 0 时不画环。 */
export function SampleDonut({ comp }: { comp: SampleComposition }) {
  if (!comp.total) return <EmptyState title="该项目还没有待测样品结果">没有可统计的样品，因此不绘制构成图。</EmptyState>;
  const parts = comp.parts.filter((p) => p.count > 0);
  return (
    <div>
      <div className="chart" style={{ height: 184, position: "relative" }}>
        <ResponsiveContainer width="100%" height="100%" aria-hidden>
          <PieChart>
            <Pie data={parts} dataKey="count" nameKey="flag" innerRadius="68%" outerRadius="96%" startAngle={90} endAngle={-270}
              stroke="#fff" strokeWidth={2} isAnimationActive={false} paddingAngle={0}>
              {parts.map((p) => <Cell key={p.flag} fill={FLAG_FILL[p.flag]} />)}
            </Pie>
            <Tooltip content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as SampleComposition["parts"][number];
              return <Tip title={SAMPLE_FLAG_ZH[p.flag] ?? p.flag} rows={[["数量", `${fmtInt(p.count)} 条`], ["占比", fmtPct(p.share)]]} />;
            }} />
          </PieChart>
        </ResponsiveContainer>
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none", textAlign: "center" }}>
          <div><div className="num" style={{ fontSize: 24, lineHeight: 1.1 }}>{fmtInt(comp.total)}</div><div className="muted" style={{ fontSize: 12 }}>条待测样品</div></div>
        </div>
      </div>
      <table className="legend-table" aria-label="样本结果构成">
        <tbody>
          {comp.parts.map((p) => (
            <tr key={p.flag}>
              <td><i className="swatch swatch--circle" style={{ background: FLAG_FILL[p.flag], opacity: p.count ? 1 : 0.35 }} /><span className="truncate">{SAMPLE_FLAG_ZH[p.flag] ?? p.flag}</span></td>
              <td className="num">{fmtInt(p.count)}</td>
              <td className="num muted">{fmtPct(p.share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
