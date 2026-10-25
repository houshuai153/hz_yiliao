import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useDrafts, useProjectRuns, useProjects } from "../api/hooks";
import type { DraftCreated, DraftSummary, ReviewStatus } from "../api/types";
import { Button, EmptyState, ErrorBlock, Field, Notice, PageHead, Panel, Pill, ReviewPill, Seg, SkelRows } from "../components/ui";
import { useToast } from "../components/Toast";
import { fmtDateTime } from "../lib/format";
import { KIND_ZH, REVIEW_ZH } from "../lib/labels";

type Filter = "all" | ReviewStatus;

function NewDraft({ onClose }: { onClose: () => void }) {
  const projects = useProjects();
  const [pid, setPid] = useState("");
  const [kind, setKind] = useState<"validation" | "sample_analysis">("sample_analysis");
  const [runs, setRuns] = useState<string[]>([]);
  const toast = useToast();
  const nav = useNavigate();
  const qc = useQueryClient();
  const effective = pid || projects.data?.[0]?.id || "";
  const runList = useProjectRuns(effective);
  const gen = useMutation({
    mutationFn: () => api<DraftCreated>("/api/reports/draft", { method: "POST", body: { project_id: effective, run_ids: runs, kind } }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      toast.ok(`已生成 ${r.draft_id}：事实 ${r.fact_count} 条，业务结论句 ${r.fact_sentences} 条，缺来源 ${r.unsourced_sentences} 条。草稿不能直接导出，请先审核。`);
      nav(`/reports/${r.draft_id}`);
    },
  });
  return (
    <Panel title="新建草稿" meta={<Button size="sm" variant="text" onClick={onClose}>收起</Button>}>
      <form onSubmit={(e) => { e.preventDefault(); gen.mutate(); }} style={{ display: "grid", gap: 14 }}>
        <div className="controls">
          <Field label="项目">{(id) => <select id={id} className="select" value={effective} onChange={(e) => { setPid(e.target.value); setRuns([]); }}>
            {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.id} · {p.method_type}</option>)}</select>}</Field>
          <Field label="报告类型">{(id) => <select id={id} className="select" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="sample_analysis">样品分析报告</option><option value="validation">方法学验证报告</option></select>}</Field>
        </div>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field__label" style={{ marginBottom: 6 }}>纳入的分析批 <span className="muted" style={{ fontWeight: 400 }}>（不选 = 全部）</span></legend>
          {runList.isPending ? <SkelRows rows={1} h={32} /> : runList.isError ? <ErrorBlock error={runList.error} onRetry={() => void runList.refetch()} /> : (
            <div style={{ display: "flex", gap: "6px 18px", flexWrap: "wrap" }}>{runList.data.map((r) => (
              <label key={r.id} className="checkrow"><input type="checkbox" checked={runs.includes(r.id)} onChange={(e) => setRuns(e.target.checked ? [...runs, r.id] : runs.filter((x) => x !== r.id))} />
                <span><span className="mono">{r.id}</span> <span className="faint">{r.run_date}</span></span></label>))}</div>)}
        </fieldset>
        {gen.isError && <ErrorBlock error={gen.error} what="生成失败" />}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button variant="primary" type="submit" loading={gen.isPending} disabled={!effective} icon={<FilePlus2 />}>生成草稿</Button>
          <span className="muted" style={{ fontSize: 12.5 }}>草稿由规则与模板生成，每条业务结论附数据来源；缺失值保留“[待补充: 字段]”。</span>
        </div>
      </form>
    </Panel>
  );
}

function Row({ d, onOpen }: { d: DraftSummary & { open_issues: number }; onOpen: () => void }) {
  return (
    <tr className="rowlink" onClick={onOpen}>
      <td><div className="cell2"><button className="linkbtn mono" style={{ fontSize: 12.5 }} onClick={(e) => { e.stopPropagation(); onOpen(); }}>{d.draft_id}</button><span>{d.project_id} · {KIND_ZH[d.kind]}</span></div></td>
      <td data-label="状态">{d.legacy ? <Pill tone="outline">旧草稿 · 不可审核</Pill> : <ReviewPill status={d.review_status} />}</td>
      <td className="num" data-label="版本">{d.legacy ? <span className="dash">—</span> : `v${d.current_version}`}</td>
      <td className="num" data-label="未解决问题">{d.open_issues > 0 ? <strong style={{ fontWeight: 600 }}>{d.open_issues}</strong> : 0}</td>
      <td className="num" data-label="待补充字段">{d.missing_fields.length}</td>
      <td className="num nowrap" data-label="生成时间">{fmtDateTime(d.created_at)}</td>
    </tr>
  );
}

export function Reports() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const status = (sp.get("status") as Filter | null) ?? "all";
  const [creating, setCreating] = useState(false);
  const drafts = useDrafts();
  useEffect(() => { document.title = "报告审核 · 生物分析工作台"; }, []);
  const all = drafts.data ?? [];
  // 旧草稿（审核功能上线前生成）没有审核流程，不计入任何状态筛选，只出现在“全部”里
  const count = (s: Filter) => (s === "all" ? all.length : all.filter((d) => !d.legacy && d.review_status === s).length);
  const rows = status === "all" ? all : all.filter((d) => !d.legacy && d.review_status === status);

  return (
    <>
      <PageHead title="报告审核" sub="生成草稿 → 预览 → 修订 / 意见 → 审核通过 → 导出。只有审核通过的草稿才能导出 DOCX；人工审核通过不等于正式 QA 批准。"
        actions={<Button variant="primary" icon={<FilePlus2 />} onClick={() => setCreating(true)} aria-expanded={creating}>新建草稿</Button>} />
      <div className="stack">
        {creating && <NewDraft onClose={() => setCreating(false)} />}
        <ol className="flowstrip" aria-label="审核流程与门禁">
          <li><b>生成</b><span>规则与模板生成，每条业务结论附数据来源；缺失值保留“[待补充: 字段]”。</span></li>
          <li><b>预览与修订</b><span>只能修订叙述句；表格、事实与规则判定不可改，无来源的结论会被标记。</span></li>
          <li><b>意见与问题</b><span>“问题”解决前阻止通过；“意见”不阻止。</span></li>
          <li><b>审核通过</b><span>须填意见、勾选确认项，并说明每个待补充字段；不等于正式 QA 批准。</span></li>
          <li><b>导出</b><span>只有审核通过才能导出；后端强制，导出最终审核版本。</span></li>
        </ol>

        <Panel title="草稿" meta={
          <Seg label="状态筛选" value={status} onChange={(v) => { const n = new URLSearchParams(sp); if (v === "all") n.delete("status"); else n.set("status", v); setSp(n, { replace: true }); }}
            options={[{ value: "all", label: `全部 ${count("all")}` }, ...(["draft", "in_review", "changes_requested", "reviewed"] as ReviewStatus[]).map((s) => ({ value: s, label: `${REVIEW_ZH[s]} ${count(s)}` }))]} />} flush>
          {drafts.isPending ? <SkelRows rows={5} /> : drafts.isError ? <ErrorBlock error={drafts.error} what="草稿列表" onRetry={() => void drafts.refetch()} /> :
            rows.length === 0 ? (
              <div style={{ padding: "0 var(--pad) var(--pad)" }}>
                <EmptyState title={all.length === 0 ? "还没有草稿" : `没有“${status === "all" ? "" : REVIEW_ZH[status]}”状态的草稿`}>
                  {all.length === 0 ? <>点击右上角“新建草稿”，选择项目和报告类型即可生成。</> : <button className="linkbtn" onClick={() => setSp(new URLSearchParams(), { replace: true })}>查看全部草稿</button>}
                </EmptyState>
              </div>) : (
              <div className="tablewrap"><table className="table table--stack">
                <thead><tr><th>草稿</th><th>状态</th><th className="num">版本</th><th className="num">未解决问题</th><th className="num">待补充字段</th><th className="num">生成时间</th></tr></thead>
                <tbody>{rows.map((d) => <Row key={d.draft_id} d={d as DraftSummary & { open_issues: number }} onOpen={() => nav(`/reports/${d.draft_id}`)} />)}</tbody>
              </table></div>)}
        </Panel>
        {all.some((d) => d.legacy) && <Notice tone="warn" title="存在审核功能上线前生成的旧草稿">旧草稿没有保存可审核的结构化内容，不能审核或导出，请重新生成。</Notice>}
        <p className="faint" style={{ fontSize: 12.5 }}><FileText size={12} aria-hidden style={{ verticalAlign: -1 }} /> 登录名即“操作人记录”，不是受监管的电子签名。</p>
      </div>
    </>
  );
}
