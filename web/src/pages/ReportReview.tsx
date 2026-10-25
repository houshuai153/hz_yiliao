import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Download, MessageSquarePlus, Plus, Sparkles, Undo2, X } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, downloadFile, saveBlob } from "../api/client";
import { useDraft, useEvents, useHealth, useVersions } from "../api/hooks";
import type { Block, Comment, DraftDetail, FactRow, ReviewStatus, SentenceBlock, Suggestion } from "../api/types";
import { useMe } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import { Button, EmptyState, ErrorBlock, Field, Notice, PageHead, Panel, Pill, Query, ReviewPill, SkelRows, Tabs } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { ACTION_ZH, CHG_ZH, KIND_ZH, REVIEW_ZH } from "../lib/labels";

type Side = "review" | "edit" | "comments" | "history";
const PH = /(\[待补充: [^\]]*\])/g;
const withMarks = (t: string): ReactNode => t.split(PH).map((s, i) => (i % 2 ? <mark key={i} className="mark-missing">{s}</mark> : s));
const NUMERIC = /^[-+±]?\d[\d,]*(\.\d+)?\s?%?$/;   // 仅用于对齐显示：看起来是数字的单元格右对齐
const EDITABLE: ReviewStatus[] = ["draft", "in_review", "changes_requested"];

/* ---------- 流程步骤：生成 → 预览 → 修订/意见 → 审核通过 → 导出 ---------- */
function Steps({ status }: { status: ReviewStatus }) {
  const cur = status === "draft" ? 1 : status === "reviewed" ? 4 : 2;
  const labels = ["生成", "预览", status === "changes_requested" ? "按意见修订" : "修订 / 意见", "审核通过", "导出"];
  return (
    <ol className="steps" aria-label="流程" style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {labels.map((l, i) => (
        <li key={l} className="step" data-state={i < cur ? "done" : i === cur ? "current" : "todo"} aria-current={i === cur ? "step" : undefined}>
          <span className="step__dot" aria-hidden />{l}
        </li>))}
    </ol>
  );
}

/* ---------- 文档预览 ---------- */
function BlockView({ b, selected, selectable, onSelect, showDiff }: { b: Block; selected: boolean; selectable: boolean; onSelect: () => void; showDiff: boolean }) {
  if (b.type === "table") {
    return (
      <div className="doc-table" id={b.bid}>
        <div className="tablewrap"><table className="table table--dense doc-table">
          <caption>{b.caption}</caption>
          <thead><tr>{b.header.map((h, j) => <th key={h} className={b.rows.length > 0 && b.rows.every((r) => NUMERIC.test(String(r[j]))) ? "num" : undefined}>{h}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={NUMERIC.test(String(c)) ? "num" : undefined}>{withMarks(String(c))}</td>)}</tr>)}</tbody>
        </table></div>
        <div className="doc-source">数据来源：{b.source}{b.comment_count > 0 && ` · ${b.comment_count} 条意见`}</div>
      </div>
    );
  }
  const s = b as SentenceBlock;
  const cls = `block${s.flag ? " block--flag" : ""}`;
  return (
    <div id={s.bid} className={cls} data-selected={selected} data-selectable={selectable} role={selectable ? "button" : undefined} tabIndex={selectable ? 0 : undefined}
      aria-label={selectable ? `选择 ${s.bid}` : undefined} onClick={selectable ? onSelect : undefined}
      onKeyDown={selectable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); } } : undefined}>
      <span>{withMarks(s.text)}</span>
      {s.tags.length > 0 && <span className="block__tags">[{s.tags.join(",")}]</span>}
      <span className="block__meta">
        {s.edited && <Pill tone="info">{s.added ? "人工新增" : "人工修订"}</Pill>}
        {s.open_issues > 0 && <Pill tone="fail">问题 {s.open_issues}</Pill>}
        {s.comment_count > s.open_issues && <Pill tone="outline">意见 {s.comment_count - s.open_issues}</Pill>}
      </span>
      {s.flag && <div className="notice notice--error" style={{ marginTop: 6 }} role="alert"><div className="notice__body">无法对应数据来源，须处理后才能审核通过：{s.flag}</div></div>}
      {showDiff && s.edited && s.original_text && (
        <div className="diff"><div><div className="diff__label">生成时原文</div><div className="diff__old">{s.original_text}</div></div><div><div className="diff__label">当前</div><div className="diff__new">{s.text}</div></div></div>)}
    </div>
  );
}

/* ---------- 动作封装：调用 → 失效缓存 → 提示 ---------- */
function useReviewApi(id: string) {
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = () => Promise.all(["draft", "drafts", "versions", "events"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown; ok: string }) => api<unknown>(`/api/reports/${id}${path}`, { method: "POST", body }),
    onSuccess: async (_d, v) => { await refresh(); toast.ok(v.ok); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "操作失败，请重试"),
  });
}

/* ---------- 侧栏：审核 ---------- */
function ReviewTab({ d }: { d: DraftDetail }) {
  const id = d.draft_id;
  const act = useReviewApi(id);
  const toast = useToast();
  const qc = useQueryClient();
  const events = useEvents(id, d.review_status === "changes_requested");
  const [opinion, setOpinion] = useState("");
  const [confirms, setConfirms] = useState<string[]>([]);
  const [acks, setAcks] = useState<Record<string, string>>({});
  const [exporting, setExporting] = useState(false);
  const rd = d.readiness!;
  const lastReturn = events.data?.slice().reverse().find((e) => e.action === "request_changes");
  const readOnly = d.is_current === false;

  async function doExport() {
    setExporting(true);
    try {
      const { blob, filename } = await downloadFile(`/api/reports/${id}/download`);
      saveBlob(blob, filename);
      toast.ok(`已导出 ${filename}（最终审核版本 v${d.current_version}）`);
      void qc.invalidateQueries({ queryKey: ["events"] });
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "导出失败"); } finally { setExporting(false); }
  }
  if (readOnly) return <Notice tone="info">正在查看历史版本，只读。切换到当前版本后才能审核。</Notice>;

  if (d.review_status === "draft") return (
    <div style={{ display: "grid", gap: 12 }}>
      <p style={{ fontSize: 13.5 }}>草稿已生成，尚未开始审核。开始后可以修订叙述、留意见，并给出通过或退回的结论。</p>
      <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ path: "/transition", body: { action: "start_review" }, ok: "已开始审核。" })}>开始审核</Button>
      <p className="faint" style={{ fontSize: 12.5 }}>导出需要先通过审核；这一规则由后端强制，不只是隐藏按钮。</p>
    </div>);

  if (d.review_status === "changes_requested") return (
    <div style={{ display: "grid", gap: 12 }}>
      {lastReturn && <Notice tone="warn" title={`退回意见 · ${lastReturn.operator} · ${fmtDateTime(lastReturn.ts)}`}>{lastReturn.comment}</Notice>}
      <p style={{ fontSize: 13.5 }}>请按意见在“修订”和“意见”中处理，然后重新提交。</p>
      <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ path: "/transition", body: { action: "resubmit" }, ok: "已重新提交审核。" })}>修订完成，重新提交</Button>
    </div>);

  if (d.review_status === "reviewed") return (
    <div style={{ display: "grid", gap: 12 }}>
      <Notice tone="ok" title="审核通过（操作人记录）">人工审核通过不等于正式 QA 批准；正式报告须按质量体系签署。导出文件保留“AI 辅助草稿 / 模拟数据”标记和“[待签署]”签署页。</Notice>
      <Button variant="primary" icon={<Download />} loading={exporting} onClick={() => void doExport()}>导出 DOCX（v{d.current_version}）</Button>
      <p className="faint" style={{ fontSize: 12.5 }}>导出会写入审核记录与审计日志；文件由最终审核版本重新渲染，不是生成时的初稿。</p>
    </div>);

  const needAck = rd.missing_to_acknowledge;
  const ready = !rd.blockers.length && opinion.trim() && Object.keys(rd.required_confirmations).every((k) => confirms.includes(k)) && needAck.every((f) => (acks[f] ?? "").trim());
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div aria-live="polite" style={{ display: "grid", gap: 8 }}>
        {rd.blockers.length === 0 ? <Notice tone="ok">没有阻止通过的问题；还需填写意见、完成确认项和待补充字段说明。</Notice>
          : rd.blockers.map((b, i) => <Notice key={i} tone="error" title="不能审核通过">{b.message}</Notice>)}
      </div>
      <Field label="审核意见（通过 / 退回都必须填写）">{(fid) => <textarea id={fid} className="textarea" rows={3} value={opinion} onChange={(e) => setOpinion(e.target.value)} />}</Field>
      <fieldset style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 8 }}>
        <legend className="field__label" style={{ marginBottom: 6 }}>确认项</legend>
        {Object.entries(rd.required_confirmations).map(([k, label]) => (
          <label key={k} className="checkrow"><input type="checkbox" checked={confirms.includes(k)} onChange={(e) => setConfirms(e.target.checked ? [...confirms, k] : confirms.filter((x) => x !== k))} /><span>{label}</span></label>))}
      </fieldset>
      {needAck.length > 0 && (
        <fieldset style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 10 }}>
          <legend className="field__label" style={{ marginBottom: 2 }}>待补充字段（{needAck.length}）</legend>
          <p className="muted" style={{ fontSize: 12.5 }}>这里只记录“为什么仍保留待补充”，不会补齐数据；报告里仍显示“[待补充: …]”，须回到源数据补齐后重新生成。</p>
          {needAck.map((f) => <Field key={f} label={f}>{(fid) => <input id={fid} className="input" placeholder="处理说明（必填）" value={acks[f] ?? ""} onChange={(e) => setAcks({ ...acks, [f]: e.target.value })} />}</Field>)}
        </fieldset>)}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="primary" icon={<Check />} loading={act.isPending} disabled={!ready}
          onClick={() => act.mutate({ path: "/transition", body: { action: "approve", comment: opinion, confirmations: confirms, missing_ack: acks }, ok: "审核通过。现在可以导出 DOCX。" })}>审核通过</Button>
        <Button icon={<Undo2 />} loading={act.isPending} disabled={!opinion.trim()} onClick={() => act.mutate({ path: "/transition", body: { action: "request_changes", comment: opinion }, ok: "已退回修改。" })}>退回修改</Button>
      </div>
      {!ready && <p className="faint" style={{ fontSize: 12.5 }}>“审核通过”需要：无阻止项、已填意见、已勾选全部确认项、{needAck.length ? "每个待补充字段都有说明" : "无待补充字段"}。以上检查在后端同样执行。</p>}
    </div>
  );
}

/* ---------- 侧栏：修订 ---------- */
function EditTab({ d, block, facts, onPick }: { d: DraftDetail; block: SentenceBlock | null; facts: FactRow[]; onPick: (id: string | null) => void }) {
  const act = useReviewApi(d.draft_id);
  const toast = useToast();
  const llm = useHealth().data?.llm_enabled;
  const [text, setText] = useState("");
  const [keys, setKeys] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [sug, setSug] = useState<Suggestion | null>(null);
  const [adding, setAdding] = useState(false);
  const [add, setAdd] = useState({ kind: "static" as "static" | "fact", text: "", keys: [] as string[], note: "" });
  const locked = !EDITABLE.includes(d.review_status) || d.is_current === false;
  const factMap = useMemo(() => new Map(facts.map((f) => [f.key, f])), [facts]);

  useEffect(() => { setText(block?.text ?? ""); setKeys(block?.fact_keys ?? []); setNote(""); setSug(null); }, [block?.bid, block?.text]);

  if (locked) return <Notice tone="info">{d.review_status === "reviewed" ? "已审核通过，不再接受修订。" : "历史版本只读。"}</Notice>;
  if (!block) return <EmptyState title="选择要修订的句子">在左侧文档中点击一句话。表格、事实表和规则判定不可在审核中改写。</EmptyState>;

  const base = { base_version: d.current_version };
  const suggest = async () => {
    try { setSug(await api<Suggestion>(`/api/reports/${d.draft_id}/blocks/${block.bid}/suggest`, { method: "POST", body: {} })); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "获取建议失败"); }
  };
  const factPicker = (sel: string[], set: (k: string[]) => void) => (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{sel.map((k) => (
        <span key={k} className="pill pill--outline" title={factMap.get(k)?.label}>{factMap.get(k)?.tag ?? k} {factMap.get(k)?.label}
          <button aria-label={`移除来源 ${k}`} style={{ border: 0, background: "none", padding: 0, cursor: "pointer", display: "grid" }} onClick={() => set(sel.filter((x) => x !== k))}><X /></button></span>))}
        {sel.length === 0 && <span className="muted" style={{ fontSize: 12.5 }}>未选择来源</span>}</div>
      <select className="select" aria-label="添加来源事实" value="" onChange={(e) => e.target.value && set([...sel, e.target.value])}>
        <option value="">添加来源事实…</option>
        {facts.filter((f) => !sel.includes(f.key)).map((f) => <option key={f.key} value={f.key}>{f.tag} · {f.label}{f.display ? `：${f.display}` : "（缺失）"}</option>)}</select>
    </div>);

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="callout"><span className="mono" style={{ fontSize: 12 }}>{block.bid}</span> · {block.kind === "fact" ? "业务结论（须有数据来源）" : block.kind === "static" ? "说明性文字（不含数字）" : "待补充占位句"}</div>
      {!block.editable ? <Notice tone="warn">“待补充”占位句不可改写，缺失数据须回到源数据补齐后重新生成。</Notice> : (
        <>
          <Field label="文字">{(id) => <textarea id={id} className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} />}</Field>
          {block.kind === "fact" && <Field label="数据来源（事实）">{() => factPicker(keys, setKeys)}</Field>}
          <Field label="修订原因（必填）">{(id) => <input id={id} className="input" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button variant="primary" loading={act.isPending} disabled={!note.trim() || !text.trim() || (text === block.text && keys.join() === block.fact_keys.join())}
              onClick={() => act.mutate({ path: `/blocks/${block.bid}/edit`, body: { ...base, text, fact_keys: block.kind === "fact" ? keys : null, note }, ok: "已保存修订，生成新版本。" })}>保存修订</Button>
            {llm && <Button icon={<Sparkles />} onClick={() => void suggest()}>AI 润色建议</Button>}
          </div>
          {sug && (sug.used ? (
            <Notice tone="info" title="大模型润色建议（未改动报告）">
              <div className="diff"><div className="diff__old">{sug.original}</div><div className="diff__new">{sug.suggestion}</div></div>
              <div style={{ marginTop: 8 }}><Button size="sm" onClick={() => { setText(sug.suggestion ?? ""); setNote("采纳大模型润色建议（经人工确认）"); setSug(null); }}>采纳到编辑框</Button></div>
              <p className="faint" style={{ fontSize: 12, marginTop: 6 }}>采纳后仍需保存；保存时会重新校验数字与来源，并作为人工修订留痕。</p>
            </Notice>) : <Notice tone="info">没有可用的建议（{sug.reason}）；原文保持不变。</Notice>)}
        </>)}
      <hr className="divider" />
      <div>
        <Button size="sm" variant="text" icon={<Plus />} aria-expanded={adding} onClick={() => setAdding(!adding)}>在此句之后新增一句</Button>
        {adding && (
          <div style={{ display: "grid", gap: 10, marginTop: 8 }}>
            <Field label="类型">{(id) => <select id={id} className="select" value={add.kind} onChange={(e) => setAdd({ ...add, kind: e.target.value as "static" | "fact", keys: [] })}><option value="static">说明性文字（不含数字）</option><option value="fact">业务结论（须挂数据来源）</option></select>}</Field>
            <Field label="文字">{(id) => <textarea id={id} className="textarea" rows={3} value={add.text} onChange={(e) => setAdd({ ...add, text: e.target.value })} />}</Field>
            {add.kind === "fact" && <Field label="数据来源（事实）">{() => factPicker(add.keys, (k) => setAdd({ ...add, keys: k }))}</Field>}
            <Field label="新增原因（必填）">{(id) => <input id={id} className="input" value={add.note} onChange={(e) => setAdd({ ...add, note: e.target.value })} />}</Field>
            <div><Button loading={act.isPending} disabled={!add.text.trim() || !add.note.trim()}
              onClick={() => act.mutate({ path: "/blocks", body: { ...base, after_block: block.bid, kind: add.kind, text: add.text, fact_keys: add.keys, note: add.note }, ok: "已新增，生成新版本。" }, { onSuccess: () => { setAdd({ kind: "static", text: "", keys: [], note: "" }); setAdding(false); } })}>新增</Button></div>
          </div>)}
      </div>
      <Button size="sm" variant="text" onClick={() => onPick(null)}>取消选择</Button>
    </div>
  );
}

/* ---------- 侧栏：意见 ---------- */
function CommentsTab({ d, selected }: { d: DraftDetail; selected: string | null }) {
  const act = useReviewApi(d.draft_id);
  const [kind, setKind] = useState<"comment" | "issue">("comment");
  const [text, setText] = useState("");
  const [scope, setScope] = useState<string>("");
  const [res, setRes] = useState<Record<number, string>>({});
  useEffect(() => { setScope(selected ?? ""); }, [selected]);
  const locked = d.review_status === "reviewed" || d.is_current === false;
  const comments = (d.comments ?? []).slice().reverse();
  const blocks = (d.sections ?? []).flatMap((s) => s.blocks).filter((b) => b.type === "sentence") as SentenceBlock[];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {!locked && (
        <form onSubmit={(e) => { e.preventDefault(); act.mutate({ path: "/comments", body: { kind, text, block_id: scope || null }, ok: kind === "issue" ? "已提出问题。" : "已添加意见。" }, { onSuccess: () => setText("") }); }} style={{ display: "grid", gap: 10 }}>
          <Field label="针对">{(id) => <select id={id} className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">整份报告</option>{blocks.map((b) => <option key={b.bid} value={b.bid}>{b.bid} · {b.text.slice(0, 24)}</option>)}</select>}</Field>
          <div className="seg" role="group" aria-label="类型">
            <button type="button" aria-pressed={kind === "comment"} onClick={() => setKind("comment")}>意见</button>
            <button type="button" aria-pressed={kind === "issue"} onClick={() => setKind("issue")}>问题（解决前阻止通过）</button>
          </div>
          <Field label="内容">{(id) => <textarea id={id} className="textarea" rows={3} value={text} onChange={(e) => setText(e.target.value)} />}</Field>
          <div><Button type="submit" icon={<MessageSquarePlus />} loading={act.isPending} disabled={!text.trim()}>提交</Button></div>
        </form>)}
      {comments.length === 0 ? <EmptyState title="还没有审核意见" /> : comments.map((c: Comment) => (
        <article key={c.id} className="callout" style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <Pill tone={c.kind === "issue" ? (c.status === "open" ? "fail" : "pass") : "outline"}>{c.kind === "issue" ? (c.status === "open" ? "问题 · 未解决" : "问题 · 已处理") : "意见"}</Pill>
            <span className="muted" style={{ fontSize: 12 }}>{c.created_by} · {fmtDateTime(c.created_at)} · v{c.version}{c.block_id ? ` · ${c.block_id}` : " · 整份报告"}</span>
          </div>
          <div>{c.text}</div>
          {c.status === "resolved" && <div className="muted" style={{ fontSize: 12.5 }}>处理说明（{c.resolved_by}）：{c.resolution}</div>}
          {c.status === "open" && !locked && c.kind === "issue" && (
            <div style={{ display: "flex", gap: 6 }}>
              <input className="input" aria-label="处理说明" placeholder="处理说明（必填）" value={res[c.id] ?? ""} onChange={(e) => setRes({ ...res, [c.id]: e.target.value })} />
              <Button size="sm" disabled={!(res[c.id] ?? "").trim()} loading={act.isPending} onClick={() => act.mutate({ path: `/comments/${c.id}/resolve`, body: { resolution: res[c.id] }, ok: "已标记处理。" })}>标记已处理</Button>
            </div>)}
        </article>))}
    </div>
  );
}

/* ---------- 侧栏：版本与记录 ---------- */
function HistoryTab({ d, onView }: { d: DraftDetail; onView: (v: number | null) => void }) {
  const versions = useVersions(d.draft_id), events = useEvents(d.draft_id);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div>
        <h3 style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 6 }}>版本</h3>
        <Query q={versions} what="版本" skeleton={<SkelRows rows={2} h={36} />}>
          {(v) => <ul className="list" style={{ margin: "0 calc(var(--pad) * -1)" }}>{v.slice().reverse().map((x) => (
            <li key={x.version} className="list__item" style={{ alignItems: "flex-start" }}>
              <div className="list__main"><div><strong className="num" style={{ fontWeight: 600 }}>v{x.version}</strong> {CHG_ZH[x.change_type]} {x.is_current && <Pill tone="info">当前</Pill>}</div>
                <div className="list__sub">{x.operator} · {fmtDateTime(x.created_at)}{x.note ? ` · ${x.note}` : ""}</div></div>
              <Button size="sm" onClick={() => onView(x.is_current ? null : x.version)}>{x.is_current ? "回到当前" : "查看"}</Button>
            </li>))}</ul>}
        </Query>
      </div>
      <div>
        <h3 style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 6 }}>审核记录</h3>
        <p className="faint" style={{ fontSize: 12, marginBottom: 6 }}>“操作人”为登录名，是操作人记录，不是电子签名。</p>
        <Query q={events} what="审核记录" skeleton={<SkelRows rows={3} h={36} />}>
          {(ev) => <ol className="list" style={{ margin: "0 calc(var(--pad) * -1)" }}>{ev.slice().reverse().map((e) => (
            <li key={e.id} className="list__item" style={{ alignItems: "flex-start" }}>
              <div className="list__main">
                <div><strong style={{ fontWeight: 500 }}>{ACTION_ZH[e.action] ?? e.action}</strong>{e.to_status && <span className="muted"> · {e.from_status ? `${REVIEW_ZH[e.from_status]} → ` : ""}{REVIEW_ZH[e.to_status]}</span>}</div>
                <div className="list__sub">{e.operator} · {fmtDateTime(e.ts)} · v{e.version}</div>
                {e.comment && <div style={{ fontSize: 13, marginTop: 2 }}>{e.comment}</div>}
              </div>
            </li>))}</ol>}
        </Query>
      </div>
    </div>
  );
}

/* ---------- 页面 ---------- */
function Review({ d, version, setVersion }: { d: DraftDetail; version?: number; setVersion: (v: number | null) => void }) {
  const me = useMe();
  const [side, setSide] = useState<Side>("review");
  const [sel, setSel] = useState<string | null>(null);
  const [diff, setDiff] = useState(true);
  const [active, setActive] = useState<string>("");
  const sections = d.sections ?? [];
  const locked = !EDITABLE.includes(d.review_status) || d.is_current === false;
  const block = useMemo(() => (sel ? (sections.flatMap((s) => s.blocks).find((b) => b.bid === sel && b.type === "sentence") as SentenceBlock | undefined) ?? null : null), [sel, sections]);
  const docRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!("IntersectionObserver" in window) || !docRef.current) return;
    const io = new IntersectionObserver((es) => { const v = es.find((e) => e.isIntersecting); if (v) setActive(v.target.id); }, { rootMargin: "-10% 0px -80% 0px" });
    docRef.current.querySelectorAll("h3[id]").forEach((h) => io.observe(h));
    return () => io.disconnect();
  }, [sections.length, d.draft_id]);

  const open = d.readiness?.open_issue_count ?? 0;
  return (
    <>
      <nav className="crumbs" aria-label="层级"><Link to="/reports">报告审核</Link><span aria-hidden>›</span><span aria-current="page" className="mono">{d.draft_id}</span></nav>
      <PageHead title={d.title ?? "报告"} sub={<><span className="mono" style={{ fontSize: 13 }}>{d.draft_id}</span> · {d.project_id} · {KIND_ZH[d.kind]} · 分析批 {d.run_ids.join("、")}</>}
        actions={<><ReviewPill status={d.review_status} /><Pill tone="outline">当前 v{d.current_version}</Pill>{d.is_current === false && <Pill tone="pending">查看历史 v{d.viewing_version}（只读）</Pill>}</>} />
      <div style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}><Steps status={d.review_status} /><a className="btn btn--sm jump-review" href="#review-panel" onClick={(e) => { e.preventDefault(); document.getElementById("review-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>前往审核操作</a></div>
      <div className="kpis" style={{ marginBottom: 16, gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }} role="group" aria-label="草稿概况">
        <div className="kpi"><div className="kpi__label">事实（溯源）</div><div className="kpi__value num">{d.stats?.fact_count}</div></div>
        <div className="kpi"><div className="kpi__label">业务结论句</div><div className="kpi__value num">{d.stats?.fact_sentences}</div><div className="kpi__note">已修订 {d.stats?.edited_blocks} · 无来源 {d.stats?.flagged_blocks}</div></div>
        <div className="kpi"><div className="kpi__label">待补充字段</div><div className="kpi__value num">{d.stats?.missing_fields}</div></div>
        <div className="kpi"><div className="kpi__label">未解决问题</div><div className="kpi__value num">{open}</div></div>
      </div>
      <div className="review">
        <nav className="toc" aria-label="章节导航">
          {sections.map((s, i) => <a key={s.title} href={`#sec-${i}`} data-active={active === `sec-${i}`}
            onClick={(e) => { e.preventDefault(); document.getElementById(`sec-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{s.title}</a>)}
        </nav>
        <Panel title="预览" meta={<label className="checkrow" style={{ fontSize: 12.5 }}><input type="checkbox" checked={diff} onChange={(e) => setDiff(e.target.checked)} />显示修订对比</label>} flush
          foot={<>AI 辅助草稿，需人工复核 · 模拟数据。方括号内为数据来源标签（F…），对应“数据溯源附录”。{!locked && "点击一句话可修订或留意见。"}</>}>
          <div className="doc" ref={docRef}>
            {sections.map((s, i) => (
              <div key={s.title}>
                <h3 id={`sec-${i}`}>{s.title}</h3>
                {s.blocks.map((b) => <BlockView key={b.bid} b={b} showDiff={diff} selected={sel === b.bid} selectable={b.type === "sentence"}
                  onSelect={() => { setSel(b.bid); if (!locked) setSide("edit"); else setSide("comments"); }} />)}
              </div>))}
          </div>
        </Panel>
        <aside className="sidepanel panel" id="review-panel" aria-label="审核操作">
          <div style={{ padding: "0 var(--pad)" }}>
            <Tabs<Side> label="审核操作" value={side} onChange={setSide} tabs={[{ value: "review", label: "审核" }, { value: "edit", label: "修订" }, { value: "comments", label: "意见", count: d.comments?.length ?? 0 }, { value: "history", label: "记录" }]} />
          </div>
          <div className="panel__body" style={{ paddingTop: 16 }} role="tabpanel">
            {side === "review" && <ReviewTab d={d} />}
            {side === "edit" && <EditTab d={d} block={block} facts={d.facts ?? []} onPick={setSel} />}
            {side === "comments" && <CommentsTab d={d} selected={sel} />}
            {side === "history" && <HistoryTab d={d} onView={(v) => setVersion(v)} />}
          </div>
          <div className="panel__foot">操作人：{me.username}（操作人记录，非电子签名）{version ? ` · 正在查看 v${version}` : ""}</div>
        </aside>
      </div>
    </>
  );
}

export function ReportReview() {
  const { draftId } = useParams();
  const [sp, setSp] = useSearchParams();
  const version = Number(sp.get("v")) || undefined;
  const q = useDraft(draftId, version);
  useEffect(() => { document.title = `${draftId ?? "报告"} · 报告审核`; }, [draftId]);
  const setVersion = (v: number | null) => { const n = new URLSearchParams(sp); if (v === null) n.delete("v"); else n.set("v", String(v)); setSp(n, { replace: true }); };
  return (
    <Query q={q} what="草稿" skeleton={<><PageHead title="加载草稿…" /><Panel><SkelRows rows={8} /></Panel></>}>
      {(d) => d.legacy ? (
        <><PageHead title="旧草稿" /><Notice tone="warn" title="该草稿不可审核">{d.message}</Notice><p style={{ marginTop: 12 }}><Link to="/reports">返回草稿列表</Link></p></>
      ) : <Review d={d} version={version} setVersion={setVersion} />}
    </Query>
  );
}
export { ErrorBlock };
