import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartData } from "../api/types";
import { RUN_STATUS_ZH } from "../lib/labels";
import { shortRun } from "../lib/format";
import { C, HatchDefs, STATUS_FILL, Tip } from "./theme";

type Progress = ChartData["progress"][number];
const SERIES = [["completed", "已完成"], ["under_review", "复核中"], ["in_progress", "进行中"]] as const;

export function Legend({ unscheduled = true }: { unscheduled?: boolean }) {
  return (
    <div className="chart__legend" aria-hidden>
      <span><i className="swatch" style={{ background: C.hue }} />已完成</span>
      <span><i className="swatch" style={{ background: C.amber }} />复核中</span>
      <span><i className="swatch" style={{ background: "repeating-linear-gradient(45deg,#0d1117 0 2px,#fff 2px 5px)", border: "1px solid #0d1117" }} />进行中</span>
      {unscheduled && <span><i className="swatch swatch--dashed" />未建批（计划 − 已建）</span>}
    </div>
  );
}

/** 各项目分析批进度：横向堆叠柱，长度单位为“批”；点击柱子选择项目。 */
export function ProgressChart({ data, selected, onSelect }: { data: Progress[]; selected?: string; onSelect: (id: string) => void }) {
  const rows = data.map((p) => ({ ...p, label: p.project_id }));
  const max = Math.max(1, ...data.map((p) => Math.max(p.planned, p.existing)));
  return (
    <>
      <div className="chart" style={{ height: 70 + data.length * 54 }}>
        <ResponsiveContainer width="100%" height="100%" aria-hidden>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 0 }} barCategoryGap={14}
          onClick={(s) => { const id = (s as { activeLabel?: string } | null)?.activeLabel; if (id) onSelect(id); }}>
          <HatchDefs />
          <CartesianGrid horizontal={false} stroke={C.grid} />
          <XAxis type="number" domain={[0, max]} allowDecimals={false} tickCount={Math.min(max + 1, 8)} tick={{ fontSize: 12, fill: C.axis }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="label" width={128} tickLine={false} axisLine={{ stroke: C.axis }}
            tick={(p: { x?: number | string; y?: number | string; payload?: { value?: string | number } }) => {
              const v = String(p.payload?.value ?? "");
              const row = rows.find((r) => r.project_id === v);
              const x = Number(p.x) - 6, y = Number(p.y);
              return (
                <g style={{ cursor: "pointer" }} onClick={() => onSelect(v)}>
                  <text x={x} y={y} dy={-1} textAnchor="end" fontSize={12.5} fontWeight={v === selected ? 600 : 400} fill={v === selected ? C.hue : C.ink}>{v}</text>
                  {row && <text x={x} y={y} dy={14} textAnchor="end" fontSize={11.5} fill={C.axis}>{`计划 ${row.planned} 批${row.exceeds_plan ? "（已超计划）" : ""}`}</text>}
                </g>
              );
            }}/>
          <Tooltip cursor={{ fill: "rgba(13,17,23,.04)" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const p = payload[0].payload as Progress;
            return <Tip title={p.project_id} rows={[["已完成", `${p.completed} 批`], ["复核中", `${p.under_review} 批`], ["进行中", `${p.in_progress} 批`],
              ["未建批", `${p.unscheduled} 批`], ["计划批数", `${p.planned} 批${p.exceeds_plan ? "（已超计划）" : ""}`]]} />;
          }} />
          {SERIES.map(([k, zh]) => (
            <Bar key={k} dataKey={k} stackId="a" name={zh} isAnimationActive={false} cursor="pointer" barSize={22} radius={0}>
              {rows.map((r) => <Cell key={r.project_id} fill={STATUS_FILL[k]} stroke={r.project_id === selected ? C.ink : k === "in_progress" ? C.ink : "none"} strokeWidth={k === "in_progress" ? 1 : 2} />)}
              <LabelList dataKey={k} position="center" fill={k === "in_progress" ? C.ink : "#fff"} fontSize={12} style={k === "in_progress" ? { paintOrder: "stroke", stroke: "#fff", strokeWidth: 3 } : undefined} formatter={(v: unknown) => (Number(v) > 0 ? String(v) : "")} />
            </Bar>
          ))}
          <Bar dataKey="unscheduled" stackId="a" name="未建批" isAnimationActive={false} cursor="pointer" fill="#ffffff" stroke={C.grey} strokeDasharray="4 3" barSize={22}>
</Bar>
        </BarChart>
      </ResponsiveContainer>
      </div>
      <details className="chartdata"><summary>查看图表数据（表格）</summary>
        <table className="datatable">
        <caption>各项目分析批进度（单位：批）</caption>
        <thead><tr><th>项目</th><th>已完成</th><th>复核中</th><th>进行中</th><th>未建批</th><th>计划批数</th></tr></thead>
        <tbody>{data.map((p) => <tr key={p.project_id}><td>{p.project_id}</td><td>{p.completed}</td><td>{p.under_review}</td><td>{p.in_progress}</td><td>{p.unscheduled}</td><td>{p.planned}</td></tr>)}</tbody>
      </table>
      </details>
    </>
  );
}

type RunRow = ChartData["runs"][string][number];
/** 所选项目的各分析批：柱高 = 该批待测样品数；颜色 / 斜纹 = 批状态；点击选择分析批。 */
export function RunsChart({ runs, selected, onSelect }: { runs: RunRow[]; selected?: string; onSelect: (id: string) => void }) {
  const rows = runs.map((r) => ({ ...r, label: shortRun(r.id), total: r.samples.total }));
  return (
    <>
      <div className="chart" style={{ height: 300 }}>
        <ResponsiveContainer width="100%" height="100%" aria-hidden>
        <BarChart data={rows} margin={{ top: 18, right: 8, bottom: 4, left: 0 }}>
          <HatchDefs />
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="label" tick={{ fontSize: 12.5, fill: C.axis }} axisLine={{ stroke: C.axis }} tickLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: C.axis }} axisLine={false} tickLine={false} width={36} />
          <Tooltip cursor={{ fill: "rgba(13,17,23,.04)" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as RunRow;
            return <Tip title={r.id} rows={[["状态", RUN_STATUS_ZH[r.status]], ["日期", r.run_date], ["待测样品", `${r.samples.total} 条`]]} />;
          }} />
          <Bar dataKey="total" isAnimationActive={false} barSize={34} cursor="pointer" onClick={(d) => onSelect((d as unknown as RunRow).id)}>
            {rows.map((r) => <Cell key={r.id} fill={STATUS_FILL[r.status]} stroke={r.id === selected ? C.ink : r.status === "in_progress" ? C.ink : "none"} strokeWidth={r.id === selected ? 2.5 : 1} />)}
            <LabelList dataKey="total" position="top" fontSize={12} fill={C.axis} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      </div>
      <details className="chartdata"><summary>查看图表数据（表格）</summary>
        <table className="datatable">
        <caption>所选项目各分析批的待测样品数</caption>
        <thead><tr><th>分析批</th><th>状态</th><th>日期</th><th>样品数</th></tr></thead>
        <tbody>{runs.map((r) => <tr key={r.id}><td>{r.id}</td><td>{RUN_STATUS_ZH[r.status]}</td><td>{r.run_date}</td><td>{r.samples.total}</td></tr>)}</tbody>
      </table>
      </details>
    </>
  );
}
