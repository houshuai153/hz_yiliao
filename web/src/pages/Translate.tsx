import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Languages, Sparkles } from "lucide-react";
import { Fragment, type ReactNode, useEffect, useState } from "react";
import { api } from "../api/client";
import { useCapabilities } from "../api/hooks";
import type { TermHit, TransPara, TransResult, TransWarn } from "../api/types";
import { Button, EmptyState, ErrorBlock, Field, Notice, PageHead, Panel, Pill, Query, Seg, Skel } from "../components/ui";
import { DIRECTION_ZH, TRANS_STATUS_ZH } from "../lib/labels";

type Dir = keyof typeof DIRECTION_ZH;
const STATUS_TONE = { translated: "pass", partial: "pending", unsupported: "fail", checked: "info" } as const;

/** 用术语命中的 span 在原文里标出术语（span 由后端计算，这里只负责高亮）。 */
function highlight(text: string, hits: TermHit[]): ReactNode {
  const spans = [...hits].sort((a, b) => a.span[0] - b.span[0]).filter((h, i, a) => i === 0 || h.span[0] >= a[i - 1].span[1]);
  if (!spans.length) return text;
  const out: ReactNode[] = [];
  let at = 0;
  spans.forEach((h, i) => {
    out.push(text.slice(at, h.span[0]));
    out.push(<mark key={i} className={`term${h.case_ok ? "" : " term--bad"}`} title={`${h.expected.zh} / ${h.expected.en}`}>{text.slice(h.span[0], h.span[1])}</mark>);
    at = h.span[1];
  });
  out.push(text.slice(at));
  return out.map((n, i) => <Fragment key={i}>{n}</Fragment>);
}
const sevTone = (s: TransWarn["severity"]) => (s === "error" ? "error" : s === "warning" ? "warn" : "info");

function Para({ p }: { p: TransPara }) {
  const llm = p.sentences.some((s) => s.generated_by === "llm");
  return (
    <div className="parabox" role="group" aria-label={`段落 ${p.index + 1}`}>
      <div className="parabox__head">
        <span>段落 {p.index + 1}</span><Pill tone={STATUS_TONE[p.status]}>{TRANS_STATUS_ZH[p.status]}</Pill>
        {llm && <Pill tone="info" icon={<Sparkles />}>含大模型译文</Pill>}
      </div>
      <div className="parabox__col">
        <div className="parabox__label">原文（{p.source_lang === "zh" ? "中文" : "英文"}）</div>
        <div>{highlight(p.source, p.term_hits)}</div>
      </div>
      <div className="parabox__col">
        <div className="parabox__label">译文（{p.target_lang === "zh" ? "中文" : "英文"}）</div>
        {p.target === null ? <span className="muted">— 未生成译文，需人工翻译</span> : <div>{p.target}</div>}
        {p.sentences.some((s) => s.status === "unsupported") && p.target !== null && <div className="faint" style={{ fontSize: 12.5, marginTop: 4 }}>含“[未翻译: …]”的句子不在支持范围内。</div>}
      </div>
      {(p.warnings.length > 0 || p.term_hits.length > 0) && (
        <div className="parabox__warn">
          {p.warnings.map((w, i) => <Notice key={i} tone={sevTone(w.severity)}><span className="mono" style={{ fontSize: 12 }}>{w.code}</span>　{w.message}</Notice>)}
          {p.term_hits.length > 0 && (
            <details>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>术语命中（{p.term_hits.length}）</summary>
              <div className="tablewrap"><table className="table table--dense" style={{ marginTop: 6 }}>
                <thead><tr><th style={{ paddingLeft: 0 }}>命中文本</th><th>术语</th><th>推荐中文</th><th>推荐英文</th><th>缩写</th></tr></thead>
                <tbody>{p.term_hits.map((h, i) => <tr key={i}><td style={{ paddingLeft: 0 }}>{h.matched_text}</td><td className="mono">{h.term_id}</td><td>{h.expected.zh}</td><td>{h.expected.en}</td><td>{h.expected.abbr ?? <span className="dash">—</span>}</td></tr>)}</tbody>
              </table></div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export function Translate() {
  const [dir, setDir] = useState<Dir>("zh2en");
  const [text, setText] = useState("");
  const [ref, setRef] = useState("");
  useEffect(() => { document.title = "翻译与术语核对 · 生物分析工作台"; }, []);
  const caps = useCapabilities();
  const m = useMutation({
    mutationFn: () => api<TransResult>("/api/translate", { method: "POST", body: { text, direction: dir, ...(ref.trim() && dir !== "bilingual" ? { reference: ref } : {}) } }),
  });
  const r = m.data;
  const checking = !!ref.trim() && dir !== "bilingual";

  return (
    <>
      <PageHead title="翻译与术语核对" sub="只翻译已登记的固定句型，并核对术语一致性；这不是通用机器翻译。无法识别的句子不会生成译文，需人工翻译。" />
      <div className="stack">
        <Query q={caps} what="能力说明" skeleton={<Skel h={44} />}>
          {(c) => (
            <Notice tone={c.is_generative ? "info" : "info"} title={c.is_generative ? "已启用大模型兜底" : "离线规则实现"}>
              {c.note}
              <details style={{ marginTop: 6 }}><summary style={{ cursor: "pointer" }}>支持的固定句型（{c.patterns.length}）与术语表说明</summary>
                <p className="muted" style={{ fontSize: 12.5, margin: "6px 0" }}>{c.glossary_note}（术语 {c.glossary_terms} 条）</p>
                <div className="tablewrap"><table className="table table--dense"><thead><tr><th style={{ paddingLeft: 0 }}>编号</th><th>中文</th><th>英文</th></tr></thead>
                  <tbody>{c.patterns.map((p) => <tr key={p.id}><td className="mono" style={{ paddingLeft: 0 }}>{p.id}</td><td>{p.zh}</td><td>{p.en}</td></tr>)}</tbody></table></div>
              </details>
            </Notice>)}
        </Query>

        <Panel title="输入" meta={<Seg label="方向" value={dir} onChange={setDir} options={(Object.keys(DIRECTION_ZH) as Dir[]).map((v) => ({ value: v, label: DIRECTION_ZH[v] }))} />}>
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) m.mutate(); }} style={{ display: "grid", gap: 12 }}>
            <div className="grid grid--2">
              <Field label="原文（每行一个段落）">{(id) => <textarea id={id} className="textarea" rows={6} value={text} maxLength={20000} onChange={(e) => setText(e.target.value)} placeholder="共分析42份研究样品。" />}</Field>
              <Field label="现成译文（可选）" hint={dir === "bilingual" ? "双语对照模式不支持核对现成译文" : "按行与原文对应；填写后只做术语核对，不翻译"}>
                {(id) => <textarea id={id} className="textarea" rows={6} value={ref} maxLength={20000} disabled={dir === "bilingual"} onChange={(e) => setRef(e.target.value)} />}</Field>
            </div>
            <div><Button variant="primary" type="submit" loading={m.isPending} disabled={!text.trim()} icon={<Languages />}>{checking ? "核对术语" : "翻译并核对"}</Button></div>
          </form>
        </Panel>

        <Panel title="结果" meta={r && <span className="num">已翻译 {r.stats.translated} · 部分 {r.stats.partial} · 不支持 {r.stats.unsupported} · 已核对 {r.stats.checked}</span>} flush
          foot={r ? r.glossary_note : undefined}>
          {m.isPending ? <div style={{ padding: "8px var(--pad) var(--pad)", display: "grid", gap: 10 }} role="status" aria-label="处理中"><Skel h={16} /><Skel h={16} w="80%" /></div>
            : m.isError ? <ErrorBlock error={m.error} what="处理失败" onRetry={() => m.mutate()} />
            : !r ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="还没有结果">输入原文并点击“翻译并核对”。</EmptyState></div>
            : (
              <div>
                {r.document_warnings.map((w, i) => <div key={i} style={{ padding: "0 var(--pad) 8px" }}><Notice tone="error"><AlertTriangle size={0} aria-hidden /><span className="mono" style={{ fontSize: 12 }}>{w.code}</span>　{w.message}</Notice></div>)}
                {r.paragraphs.map((p) => <Para key={p.index} p={p} />)}
              </div>
            )}
        </Panel>
      </div>
    </>
  );
}
