import { AlertOctagon, ArrowUpRight, CalendarDays } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCharts, useDrafts, useOverview, useProject, useRun } from "../api/hooks";
import type { ChartData, Deviation, Level, PointRow, ProjectMetrics, Risk, RunSummary } from "../api/types";
import { CumulativeChart, TimelineLegend } from "../charts/Cumulative";
import { Legend, ProgressChart, RunsChart } from "../charts/ProgressChart";
import { SampleDonut } from "../charts/SampleDonut";
import { PageHead, Panel, Pill, Query, EmptyState, ErrorBlock, ReviewPill, RunStatusPill, Seg, Skel, SkelRows, Tabs, VerdictMark, VerdictStrip, Field } from "../components/ui";
import { fmtInt, rateText, shortRun, todayISO } from "../lib/format";
import { KIND_ZH, LEVEL_ZH, MILESTONE_TYPE_ZH, MILESTONE_ZH, SEVERITY_ZH, VERDICT_ZH } from "../lib/labels";

type TabKey = "risks" | "runs" | "dev" | "projects" | "detail";

function useParam(sp: URLSearchParams, set: (n: URLSearchParams, o?: { replace: boolean }) => void) {
  return (patch: Record<string, string | null>, replace = true) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) (v === null ? n.delete(k) : n.set(k, v));
    set(n, { replace });
  };
}

function Kpi({ label, value, note, small }: { label: string; value: ReactNode; note?: ReactNode; small?: string }) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className="kpi__value num">{value}{small && <small>{small}</small>}</div>
      {note && <div className="kpi__note">{note}</div>}
    </div>
  );
}
function RateKpi({ label, rate }: { label: string; rate: Parameters<typeof rateText>[0] }) {
  const t = rateText(rate);
  return <Kpi label={label} value={t.missing ? <span className="dash">—</span> : t.main} note={t.note} />;
}

function KpiStrip({ ov }: { ov: NonNullable<ReturnType<typeof useOverview>["data"]> }) {
  const t = ov.totals;
  return (
    <div className="kpis" role="group" aria-label="关键指标">
      <RateKpi label="分析批完成进度" rate={t.progress} />
      <Kpi label="样本数（有效 / 总）" value={fmtInt(t.samples.valid)} small={`/ ${fmtInt(t.samples.total)}`} note="有效 = 结果标记为 ok" />
      <RateKpi label="QC 通过率" rate={t.qc.pass_rate} />
      <RateKpi label="ISR 通过率" rate={t.isr.pass_rate} />
      <Kpi label="未关闭偏差" value={t.open_deviations} small="项" note={`其中重要及以上 ${t.open_major_plus} 项`} />
      <Kpi label="高风险提示" value={t.risks_by_level.high} small="项" note={`中 ${t.risks_by_level.medium} · 低 ${t.risks_by_level.low}`} />
    </div>
  );
}

const LEVEL_TONE: Record<Level, "fail" | "pending" | "neutral"> = { high: "fail", medium: "pending", low: "neutral" };
function RiskPill({ level }: { level: Level }) { return <Pill tone={LEVEL_TONE[level]}>{LEVEL_ZH[level]}</Pill>; }

function evidenceText(ev: Record<string, unknown>): string {
  return Object.entries(ev).map(([k, v]) => `${k}：${Array.isArray(v) ? v.join("、") : String(v)}`).join("；");
}

/* ---------- 逐级下钻：项目 → 分析批 → 原始明细 ---------- */
function RiskList({ risks, onRun }: { risks: Risk[]; onRun: (id: string) => void }) {
  if (!risks.length) return <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="当前没有触发风险规则" /></div>;
  return (
    <div>
      {risks.map((r, i) => {
        const runs = (r.evidence.run_ids as string[] | undefined) ?? [];
        return (
          <div className="risk" key={`${r.risk_code}-${i}`}>
            <span className="risk__level"><RiskPill level={r.level} /></span>
            <div style={{ minWidth: 0 }}>
              <div>{r.message} <span className="risk__code">{r.risk_code}</span></div>
              <div className="risk__evidence">依据：{evidenceText(r.evidence)}</div>
              {runs.length > 0 && <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {runs.map((id) => <button key={id} className="btn btn--sm" onClick={() => onRun(id)}>查看 {shortRun(id)}</button>)}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RunsTable({ runs, selected, onPick, samples }: { runs: RunSummary[]; selected?: string; onPick: (id: string) => void; samples?: Record<string, number> }) {
  return (
    <div className="tablewrap">
      <table className="table table--stack">
        <thead><tr><th>分析批</th><th>日期</th><th>状态</th>{samples && <th className="num">样品数</th>}<th>判定</th><th><span className="sr-only">操作</span></th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="rowlink" data-active={r.id === selected} onClick={() => onPick(r.id)}>
              <td><button className="linkbtn mono" onClick={(e) => { e.stopPropagation(); onPick(r.id); }}>{r.id}</button></td>
              <td className="num nowrap" data-label="日期">{r.run_date}</td>
              <td data-label="状态"><RunStatusPill status={r.status} />{!r.counted_in_rates && <span className="faint" style={{ fontSize: 12, marginLeft: 6 }}>不计入通过率</span>}</td>
              {samples && <td className="num" data-label="样品数">{samples[r.id] ?? "—"}</td>}
              <td data-label="判定"><VerdictStrip calibration={r.calibration} qc={r.qc} accepted={r.accepted} isr={r.isr} /></td>
              <td style={{ textAlign: "right" }}><ArrowUpRight size={14} aria-hidden className="faint" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DevMilestones({ m }: { m: ProjectMetrics }) {
  const d = m.deviations;
  const sevTone = (s: Deviation["severity"]) => (s === "minor" ? "neutral" : s === "major" ? "pending" : "fail");
  return (
    <div className="grid grid--2" style={{ padding: "16px var(--pad) var(--pad)" }}>
      <div style={{ minWidth: 0 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>偏差</h3>
        <p className="muted" style={{ fontSize: 12.5, margin: "2px 0 8px" }}>
          未关闭 {d.open} 项（轻微 {d.open_by_severity.minor ?? 0} · 重要 {d.open_by_severity.major ?? 0} · 严重 {d.open_by_severity.critical ?? 0}）
          {d.oldest_open_days !== null && `，最久已 ${d.oldest_open_days} 天`}
        </p>
        {d.items.length === 0 ? <EmptyState title="没有登记偏差" /> : (
          <div className="tablewrap"><table className="table table--dense">
            <thead><tr><th style={{ paddingLeft: 0 }}>编号</th><th>类别</th><th>严重度</th><th>状态</th><th className="num">登记日期</th></tr></thead>
            <tbody>{d.items.map((x) => (
              <tr key={x.id} title={x.description}>
                <td className="mono" style={{ paddingLeft: 0 }}>{x.id}{x.run_id && <div className="faint" style={{ fontFamily: "var(--font)", fontSize: 11.5 }}>{x.run_id}</div>}</td>
                <td>{x.category}</td><td><Pill tone={sevTone(x.severity)}>{SEVERITY_ZH[x.severity]}</Pill></td>
                <td>{x.status === "open" ? "未关闭" : "已关闭"}</td><td className="num">{x.opened_at}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
      <div style={{ minWidth: 0 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600 }}>报告里程碑</h3>
        <p className="muted" style={{ fontSize: 12.5, margin: "2px 0 8px" }}>状态相对统计截止日期 {m.as_of} 计算</p>
        {m.milestones.length === 0 ? <EmptyState title="没有里程碑" /> : (
          <div className="tablewrap"><table className="table table--dense">
            <thead><tr><th style={{ paddingLeft: 0 }}>类型</th><th className="num">计划日期</th><th>状态</th><th className="num">距到期</th></tr></thead>
            <tbody>{m.milestones.map((x) => (
              <tr key={x.type}>
                <td style={{ paddingLeft: 0 }}>{MILESTONE_TYPE_ZH[x.type] ?? x.type}</td><td className="num">{x.planned_date}</td>
                <td><Pill tone={x.state === "overdue" ? "fail" : x.state === "due_soon" ? "pending" : x.state === "delivered" ? "pass" : "neutral"}>{MILESTONE_ZH[x.state]}</Pill></td>
                <td className="num">{x.days_to_due === null ? <span className="dash">—</span> : `${x.days_to_due} 天`}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}

const num = (v: unknown, d = 3) => (typeof v === "number" ? Number(v.toFixed(d)).toString() : v === null || v === undefined ? null : String(v));
function Cell({ v, d }: { v: unknown; d?: number }) { const s = num(v, d); return s === null ? <span className="dash">—</span> : <>{s}</>; }
function PointStatus({ s }: { s: string }) {
  if (s === "pass" || s === "fail" || s === "pending_review") return <VerdictMark v={s} />;
  return <Pill tone="outline">{s === "unknown" ? "缺值，无法判定" : s}</Pill>;
}
function PointsTable({ rows, cols }: { rows: PointRow[]; cols: { k: string; h: string; d?: number; mono?: boolean }[] }) {
  if (!rows.length) return <div style={{ padding: "12px var(--pad)" }}><EmptyState title="没有记录" /></div>;
  return (
    <div className="tablewrap"><table className="table table--dense">
      <thead><tr>{cols.map((c) => <th key={c.k} className={c.mono ? "" : "num"}>{c.h}</th>)}<th className="num">限值 %</th><th>判定</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i}>
          {cols.map((c) => <td key={c.k} className={c.mono ? "mono" : "num"}><Cell v={r[c.k]} d={c.d} /></td>)}
          <td className="num"><Cell v={r.limit_pct} /></td><td><PointStatus s={r.status} /></td>
        </tr>))}</tbody>
    </table></div>
  );
}

function RunDetailView({ rid, onBack }: { rid: string; onBack: () => void }) {
  const q = useRun(rid);
  const [sub, setSub] = useSubTab();
  return (
    <Query q={q} what="分析批明细" skeleton={<SkelRows rows={5} />}>
      {(r) => (
        <div>
          <div style={{ padding: "12px var(--pad) 8px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <strong className="mono" style={{ fontSize: 13.5 }}>{r.run.id}</strong><RunStatusPill status={r.run.status} />
            <span className="muted" style={{ fontSize: 13 }}>{r.run.run_date} · 规则集 {r.run.rule_set_id}（{r.rule_status}）</span>
            <span style={{ marginLeft: "auto" }}><VerdictStrip calibration={r.assessment.calibration.verdict} qc={r.assessment.qc.verdict} accepted={r.assessment.accepted} isr={r.assessment.isr.verdict} /></span>
          </div>
          <div style={{ padding: "0 var(--pad)" }}>
            <Tabs label="原始明细" value={sub} onChange={setSub} tabs={[
              { value: "cal", label: "校准点", count: r.calibration_points.length }, { value: "qc", label: "QC 样品", count: r.qc_samples.length },
              { value: "isr", label: "ISR 记录", count: r.isr_records.length }]} />
          </div>
          {sub === "cal" && <PointsTable rows={r.calibration_points} cols={[{ k: "level_no", h: "水平" }, { k: "nominal", h: "标示浓度" }, { k: "back_calc_conc", h: "回算浓度" }, { k: "bias_pct", h: "偏差 %", d: 2 }]} />}
          {sub === "qc" && <PointsTable rows={r.qc_samples} cols={[{ k: "level", h: "水平", mono: true }, { k: "replicate", h: "重复" }, { k: "nominal", h: "标示浓度" }, { k: "measured", h: "测得浓度" }, { k: "bias_pct", h: "偏差 %", d: 2 }]} />}
          {sub === "isr" && <PointsTable rows={r.isr_records} cols={[{ k: "sample_id", h: "样品", mono: true }, { k: "original_conc", h: "原始浓度" }, { k: "repeat_conc", h: "重测浓度" }, { k: "diff_pct", h: "差异 %", d: 2 }]} />}
          <div className="panel__foot" style={{ marginTop: 8 }}>缺失值显示为“—”，不按 0 处理；“缺值，无法判定”表示该点没有可用数值，需人工复核。<button className="btn btn--sm btn--text" onClick={onBack}>返回分析批列表</button></div>
        </div>
      )}
    </Query>
  );
}
import { useState } from "react";
function useSubTab() { return useState<"cal" | "qc" | "isr">("cal"); }

function ProjectsTable({ ov, selected, onPick }: { ov: NonNullable<ReturnType<typeof useOverview>["data"]>; selected?: string; onPick: (id: string) => void }) {
  return (
    <div className="tablewrap">
      <table className="table table--stack">
        <thead><tr><th>项目</th><th>方法</th><th className="num">样本数</th><th className="num">完成进度</th><th className="num">QC 通/未通/待</th><th className="num">ISR 通/未通/待</th><th className="num">未关闭偏差</th><th className="num">风险</th></tr></thead>
        <tbody>{ov.projects.map((p) => {
          const rt = rateText(p.runs.progress);
          return (
            <tr key={p.project.id} className="rowlink" data-active={p.project.id === selected} onClick={() => onPick(p.project.id)}>
              <td><div className="cell2"><button className="linkbtn mono" onClick={(e) => { e.stopPropagation(); onPick(p.project.id); }}>{p.project.id}</button><span className="truncate" style={{ maxWidth: 300 }}>{p.project.title}</span></div></td>
              <td data-label="方法">{p.project.method_type}</td><td className="num" data-label="样本数">{fmtInt(p.samples.total)}</td>
              <td className="num" data-label="完成进度">{rt.missing ? <span className="dash">—</span> : rt.main}<span className="faint" style={{ fontSize: 12 }}> {rt.note}</span></td>
              <td className="num" data-label="QC">{p.qc.pass}/{p.qc.fail}/{p.qc.pending_review}</td><td className="num" data-label="ISR">{p.isr.pass}/{p.isr.fail}/{p.isr.pending_review}</td>
              <td className="num" data-label="未关闭偏差">{p.deviations.open}</td><td className="num" data-label="风险">{p.risks.length}</td>
            </tr>);
        })}</tbody>
      </table>
    </div>
  );
}

/* ---------- 页面 ---------- */
export function Dashboard() {
  const [sp, setSp] = useSearchParams();
  const set = useParam(sp, setSp);
  const asOf = sp.get("asOf") ?? todayISO();
  const ov = useOverview(asOf), charts = useCharts(asOf), drafts = useDrafts();
  const pid = sp.get("project") ?? ov.data?.projects[0]?.project.id;
  const rid = sp.get("run") ?? undefined;
  const tab = (sp.get("tab") as TabKey | null) ?? "risks";
  const view = (sp.get("view") as "projects" | "runs" | null) ?? "projects";
  const scope = (sp.get("scope") as "project" | "all" | null) ?? "project";
  const proj = useProject(pid, asOf);

  useEffect(() => { document.title = "项目看板 · 生物分析工作台"; }, []);
  const pickProject = (id: string) => set({ project: id, run: null, tab: tab === "detail" ? "runs" : tab }, false);
  const pickRun = (id: string) => set({ run: id, tab: "detail" }, false);

  const high = ov.data?.totals.risks_by_level.high;
  const waiting = drafts.data?.filter((d) => !d.legacy && d.review_status !== "reviewed") ?? [];
  const highRisks = ov.data?.projects.flatMap((p) => p.risks.filter((r) => r.level === "high").map((r) => ({ pid: p.project.id, r }))) ?? [];

  return (
    <>
      <PageHead
        title="项目看板"
        sub={ov.data ? <>当前有 <strong>{high} 项高风险提示</strong>、{ov.data.totals.open_major_plus} 项重要及以上偏差未关闭；{waiting.length} 份报告草稿待处理。</> : "加载指标…"}
        actions={
          <div className="controls">
            <Field label="统计截止日期">{(id) => (
              <div style={{ position: "relative" }}><CalendarDays size={14} aria-hidden style={{ position: "absolute", left: 9, top: 10, color: "var(--muted)" }} />
                <input id={id} type="date" className="input" title="只影响里程碑逾期与偏差账龄，不是所有数据的时间筛选" style={{ paddingLeft: 30, width: 168 }} value={asOf} onChange={(e) => e.target.value && set({ asOf: e.target.value })} /></div>)}</Field>
            <Field label="项目">{(id) => (
              <select id={id} className="select" style={{ width: 236 }} value={pid ?? ""} onChange={(e) => pickProject(e.target.value)} disabled={!ov.data}>
                {ov.data?.projects.map((p) => <option key={p.project.id} value={p.project.id}>{p.project.id} · {p.project.method_type}</option>)}</select>)}</Field>
          </div>}
      />

      <Query q={ov} what="项目指标" skeleton={<div className="kpis" aria-busy><div className="kpi" style={{ gridColumn: "1 / -1" }}><Skel w={240} h={34} /></div></div>}>
        {(o) => <KpiStrip ov={o} />}
      </Query>

      <div className="grid grid--main section-gap">
        <div className="stack">
          <Panel title="分析批进度" meta={
            <Seg label="图表视图" value={view} onChange={(v) => set({ view: v })} options={[{ value: "projects", label: "项目对比" }, { value: "runs", label: "所选项目的分析批" }]} />}
            foot={charts.data ? <>{charts.data.notes.progress}　{view === "projects" ? "点击柱子或项目名可切换下方的项目。" : "点击柱子可在“批明细”中查看该批。"}</> : undefined}>
            <Query q={charts} what="进度图表" skeleton={<Skel h={200} style={{ margin: "8px 0" }} />}
              empty={(c) => (c.progress.length === 0 ? <EmptyState title="还没有项目" /> : null)}>
              {(c) => view === "projects" ? (<><ProgressChart data={c.progress} selected={pid} onSelect={pickProject} /><Legend /></>)
                : (c.runs[pid ?? ""]?.length ? <><RunsChart runs={c.runs[pid ?? ""]} selected={rid} onSelect={pickRun} /><Legend unscheduled={false} /></> : <EmptyState title="该项目还没有分析批" />)}
            </Query>
          </Panel>

          <Panel title="按分析批日期累计" meta={
            <Seg label="曲线范围" value={scope} onChange={(v) => set({ scope: v })} options={[{ value: "project", label: "所选项目" }, { value: "all", label: "全部项目" }]} />}
            foot={charts.data ? <><strong style={{ fontWeight: 600, color: "var(--text)" }}>统计口径：</strong>{charts.data.notes.timeline.replace(/\*\*/g, "")}</> : undefined}>
            <Query q={charts} what="累计曲线" skeleton={<Skel h={230} />}>
              {(c: ChartData) => { const pts = c.timeline[scope === "all" ? "ALL" : pid ?? ""] ?? [];
                return pts.length ? <><CumulativeChart points={pts} /><TimelineLegend /></> : <EmptyState title="没有可绘制的分析批日期">该范围内还没有分析批。</EmptyState>; }}
            </Query>
          </Panel>
        </div>

        <aside className="stack" aria-label="待处理">
          <Panel title="样本结果构成" meta={<span className="mono">{pid}</span>} foot={charts.data?.notes.samples}>
            <Query q={charts} what="样本构成" skeleton={<Skel h={230} />}>
              {(c) => <SampleDonut comp={c.sample_composition[pid ?? ""] ?? { total: 0, parts: [] }} />}
            </Query>
          </Panel>
          <Panel title="待处理的报告草稿" meta={<Link to="/reports" className="muted">全部</Link>} flush>
            {drafts.isPending ? <SkelRows rows={3} /> : drafts.isError ? <ErrorBlock error={drafts.error} onRetry={() => void drafts.refetch()} what="草稿列表" /> :
              waiting.length === 0 ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="没有待处理的草稿">在“报告审核”中生成新草稿。</EmptyState></div> : (
                <ul className="list">{waiting.slice(0, 5).map((d) => (
                  <li key={d.draft_id} className="list__item">
                    <div className="list__main"><Link className="list__title mono" style={{ fontSize: 12.5, textDecoration: "none" }} to={`/reports/${d.draft_id}`}>{d.draft_id}</Link>
                      <div className="list__sub">{d.project_id} · {KIND_ZH[d.kind]} · v{d.current_version}{d.open_issues ? ` · 未解决问题 ${d.open_issues}` : ""}</div></div>
                    <ReviewPill status={d.review_status} />
                  </li>))}</ul>)}
          </Panel>
          <Panel title="高风险提示" meta={`${highRisks.length} 项`} flush>
            {ov.isPending ? <SkelRows rows={3} /> : highRisks.length === 0 ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="没有高风险提示" /></div> : (
              <ul className="list">{highRisks.slice(0, 6).map(({ pid: p, r }, i) => (
                <li key={`${p}-${r.risk_code}-${i}`} className="list__item" style={{ alignItems: "flex-start" }}>
                  <AlertOctagon size={15} aria-hidden style={{ color: "var(--negative)", marginTop: 3, flex: "none" }} />
                  <div className="list__main"><div style={{ fontSize: 13.5 }}>{r.message}</div>
                    <button className="linkbtn" style={{ fontSize: 12, color: "var(--muted)", fontWeight: 400 }} onClick={() => set({ project: p, tab: "risks", run: null }, false)}>{p} · {r.risk_code}</button></div>
                </li>))}</ul>)}
          </Panel>
        </aside>
      </div>

      {/* 下钻：全部项目 → 项目 → 分析批 → 原始明细（路径写在 URL 里，可刷新、可分享）*/}
      <section className="section-gap" aria-label="项目详情">
        <nav className="crumbs" aria-label="层级">
          <button onClick={() => set({ project: null, run: null, tab: "projects" }, false)}>全部项目</button>
          {pid && <><span aria-hidden>›</span>{rid ? <button onClick={() => set({ run: null, tab: "runs" }, false)}>{pid}</button> : <span aria-current="page">{pid}</span>}</>}
          {rid && <><span aria-hidden>›</span><span aria-current="page">{rid}</span></>}
        </nav>
        <Query q={proj} what="项目详情" skeleton={<Panel><SkelRows rows={5} /></Panel>}>
          {(m) => {
            const rt = (r: Parameters<typeof rateText>[0]) => { const t = rateText(r); return t.missing ? "—" : t.main; };
            return (
              <Panel flush title={<span style={{ display: "inline-flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>{m.project.title}</span>}
                meta={<span>{m.project.method_type} · {m.project.matrix} · 单位 {m.project.unit} · 规则集状态 {m.rule_status}</span>}>
                <div style={{ padding: "0 var(--pad) 12px", display: "flex", gap: "6px 24px", flexWrap: "wrap", fontSize: 13 }} className="muted">
                  <span>完成进度 <strong className="num" style={{ color: "var(--text)", fontWeight: 500 }}>{rt(m.runs.progress)}</strong> ({m.runs.completed}/{m.runs.planned} 批)</span>
                  <span>QC 通过率 <strong className="num" style={{ color: "var(--text)", fontWeight: 500 }}>{rt(m.qc.pass_rate)}</strong></span>
                  <span>ISR 通过率 <strong className="num" style={{ color: "var(--text)", fontWeight: 500 }}>{rt(m.isr.pass_rate)}</strong></span>
                  <span>批接受通过率 <strong className="num" style={{ color: "var(--text)", fontWeight: 500 }}>{rt(m.accepted.pass_rate)}</strong></span>
                  <span>待复核：QC {m.qc.pending_review} 批 · ISR {m.isr.pending_review} 批；进行中（不计入）{m.runs.in_progress} 批</span>
                </div>
                <div style={{ padding: "0 var(--pad)" }}>
                  <Tabs label="项目明细" value={tab} onChange={(t) => set({ tab: t }, false)} tabs={[
                    { value: "risks", label: "风险提示", count: m.risks.length }, { value: "runs", label: "分析批", count: m.run_summaries.length },
                    { value: "dev", label: "偏差与里程碑" }, { value: "detail", label: "批明细" }, { value: "projects", label: "全部项目" }]} />
                </div>
                {tab === "risks" && <RiskList risks={m.risks} onRun={pickRun} />}
                {tab === "runs" && <RunsTable runs={m.run_summaries} selected={rid} onPick={pickRun} samples={Object.fromEntries((charts.data?.runs[m.project.id] ?? []).map((r) => [r.id, r.samples.total]))} />}
                {tab === "dev" && <DevMilestones m={m} />}
                {tab === "projects" && ov.data && <ProjectsTable ov={ov.data} selected={pid} onPick={(id) => set({ project: id, run: null, tab: "risks" }, false)} />}
                {tab === "detail" && (rid ? <RunDetailView rid={rid} onBack={() => set({ run: null, tab: "runs" }, false)} /> : (
                  <div style={{ padding: "16px var(--pad) var(--pad)" }}><EmptyState title="请选择一个分析批">在“分析批”页签或图表中点击某个分析批，查看校准点、QC 样品与 ISR 记录。</EmptyState></div>))}
              </Panel>
            );
          }}
        </Query>
      </section>
    </>
  );
}
export { VERDICT_ZH };
