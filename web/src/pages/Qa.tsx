import { useMutation } from "@tanstack/react-query";
import { Ban, Search, Sparkles } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { ask } from "../api/hooks";
import type { AskResult } from "../api/types";
import { Button, EmptyState, ErrorBlock, Notice, PageHead, Panel, Pill, Skel } from "../components/ui";
import { REFUSAL_ZH } from "../lib/labels";

const EXAMPLES = ["偏差在什么条件下可以关闭", "ISR 的演示判定标准是什么", "校准曲线接受标准的演示配置"];

function CitationList({ r }: { r: AskResult }) {
  if (!r.citations.length) return <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="没有引用">{r.refused ? "已拒答，因此没有可引用的章节。" : "本次回答没有引用。"}</EmptyState></div>;
  return (
    <div>
      {r.citations.map((c, i) => (
        <article className="cite" key={`${c.section_id}-${i}`}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="cite__id">{c.section_id}</span>
            <span className="muted" style={{ fontSize: 13 }}>{c.title}</span>
            <Pill tone="outline">{c.label}</Pill>
          </div>
          <div className="faint" style={{ fontSize: 12.5, marginTop: 2 }}>文档 {c.doc_id} · 版本 {c.version}</div>
          <blockquote className="cite__quote" style={{ margin: "6px 0 0" }}>{c.quote}</blockquote>
        </article>
      ))}
    </div>
  );
}

export function Qa() {
  const [q, setQ] = useState("");
  useEffect(() => { document.title = "法规与 SOP 问答 · 生物分析工作台"; }, []);
  const m = useMutation({ mutationFn: ask });
  const submit = (e?: FormEvent, text = q) => { e?.preventDefault(); if (text.trim()) { setQ(text); m.mutate(text.trim()); } };
  const r = m.data;
  const empty = m.isIdle;

  return (
    <>
      <PageHead title="法规与 SOP 问答" sub="知识库全部是教学演示摘要和模拟内部 SOP，不是监管机构原文；答案来自检索到的原句，证据不足时拒答。" />
      <div className="stack">
        <Panel title="提问">
          <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
            <label className="sr-only" htmlFor="qa-q">问题</label>
            <textarea id="qa-q" className="textarea" rows={3} maxLength={500} placeholder="例如：偏差在什么条件下可以关闭" value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); }} />
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Button variant="primary" type="submit" loading={m.isPending} disabled={!q.trim()} icon={<Search />}>提问</Button>
              <span className="faint" style={{ fontSize: 12.5 }}>Ctrl + Enter 提交 · {q.length}/500</span>
            </div>
          </form>
          <div style={{ marginTop: 14 }}>
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>可以试试</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{EXAMPLES.map((x) => <Button key={x} size="sm" onClick={() => submit(undefined, x)}>{x}</Button>)}</div>
          </div>
        </Panel>

        <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", alignItems: "stretch" }} data-qa>
          <Panel title="回答" className="panel--fill" meta={r && <span className="num">confidence {r.confidence}</span>} foot="答案只来自检索到的原句；证据不足时拒答并说明原因。">
            {m.isPending ? <div style={{ display: "grid", gap: 10 }} role="status" aria-label="检索中"><Skel h={16} /><Skel h={16} w="92%" /><Skel h={16} w="70%" /></div>
              : m.isError ? <ErrorBlock error={m.error} what="提问失败" onRetry={() => m.mutate(q.trim())} />
              : empty || !r ? <EmptyState title="还没有提问">输入问题后，答案与引用会显示在这里。</EmptyState>
              : r.refused ? (
                <Notice tone="warn" title={`已拒答：${REFUSAL_ZH[r.refusal_reason ?? ""] ?? r.refusal_reason}`}>
                  <p style={{ marginTop: 4 }}>{r.answer}</p>
                  <p className="faint" style={{ fontSize: 12, marginTop: 6 }}>拒答代码 <span className="mono">{r.refusal_reason}</span>；confidence {r.confidence}，低于阈值或属于不应回答的类型。</p>
                </Notice>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {r.answer_mode === "llm_polished" && (
                    <Notice tone="info" title="大模型整理了措辞">引用、confidence 与拒答判定仍由规则决定；请对照原句核对。
                      <details style={{ marginTop: 6 }}><summary style={{ cursor: "pointer" }}><Sparkles size={12} aria-hidden /> 查看未经模型改写的抽取式答案</summary><p className="answer" style={{ fontSize: 13.5, marginTop: 6 }}>{r.extractive_answer}</p></details>
                    </Notice>)}
                  {r.answer_mode === "extractive" && r.llm?.used === false && <Notice tone="info">已调用大模型但未采用其输出（{r.llm.fallback_reason}），显示的是规则抽取的答案。</Notice>}
                  <p className="answer">{r.answer}</p>
                </div>
              )}
          </Panel>
          <Panel title="引用证据" className="panel--fill" meta={r ? `${r.citations.length} 条` : undefined} flush foot="引用的是演示知识库中的原句，可按文档、版本和章节编号回到来源。">
          {m.isPending ? <SkelBlocks /> : r ? <CitationList r={r} /> : <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="暂无引用">回答所依据的章节会列在这里。</EmptyState></div>}
        </Panel>
        </div>
      </div>
      {r?.refused && <p className="faint" style={{ marginTop: 12, fontSize: 12.5 }}><Ban size={12} aria-hidden /> 拒答是设计行为：系统不会编造条款编号或监管原文。</p>}
    </>
  );
}
function SkelBlocks() { return <div style={{ padding: "8px var(--pad) var(--pad)", display: "grid", gap: 14 }} role="status" aria-label="加载中"><Skel h={14} w="40%" /><Skel h={34} /><Skel h={14} w="50%" /><Skel h={34} /></div>; }
