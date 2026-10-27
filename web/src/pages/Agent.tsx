import { useQueryClient } from "@tanstack/react-query";
import { Bot, Check, ChevronRight, CircleAlert, Link2, Plus, Send, ShieldCheck, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAgentTask, useAgentTasks, useAgentTrace } from "../api/hooks";
import type { AgentFinal, AgentPending, AgentStep, AgentTask } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, EmptyState, ErrorBlock, Notice, PageHead, Panel, Pill, ReviewPill, RunStatusPill, SkelRows, VerdictStrip } from "../components/ui";
import { useToast } from "../components/Toast";
import { fmtDateTime } from "../lib/format";
import { AGENT_KIND_ZH, AGENT_ROLE_ZH, AGENT_STATUS_ZH, FALLBACK_ZH, STEP_STATUS_ZH, TOOL_ZH } from "../lib/labels";

/** 等待服务端处理的最长时间（服务端单次处理上限约 90 秒，再留出模型调用的余量）。测试里可改小。 */
export const agentConfig = { timeoutMs: 150_000 };
const EXAMPLE = "查询项目 PRJ-SM-003 及其批次，汇总规则结果，并准备报告草稿";
/** 摘要只是类型名的重复，不再单独显示 */
const REDUNDANT = new Set(["user_message", "tool_call", "pending_action", "write_result", "model_call"]);
const TERMINAL: AgentTask["status"][] = ["failed", "terminated", "expired"];

type Call =
  | { kind: "idle" }
  | { kind: "busy"; label: string; startedAt: number; message?: string }
  | { kind: "cancelled" | "timeout"; what: string }
  | { kind: "error"; error: unknown };

const statusTone = (s: AgentTask["status"]) => (s === "idle" ? "pass" : s === "awaiting_confirmation" ? "pending" : s === "running" ? "info" : "fail") as "pass" | "pending" | "info" | "fail";

function sourceLink(src: string): { label: string; to?: string } {
  const [type, ...rest] = src.split(":");
  const id = rest.join(":");
  if (type === "report") return { label: `报告 ${id}`, to: `/reports/${id}` };
  if (type === "project") return { label: `项目 ${id}`, to: `/?project=${encodeURIComponent(id)}` };
  if (type === "run") return { label: `分析批 ${id.split("-").slice(-1)[0]}`, to: `/?project=${encodeURIComponent(id.replace(/-R\d+$/, ""))}&run=${encodeURIComponent(id)}&tab=detail` };
  if (type === "kb_section") return { label: `知识 ${id}` };
  return { label: src };
}
const codeOf = (c: string) => FALLBACK_ZH[c.split(":")[0]] ?? c;

/* ---------- 最终回答 ---------- */
function FinalCard({ f, status }: { f: AgentFinal; status: AgentTask["status"] }) {
  const ev = f.evidence;
  const badge = f.answer_source === "model" ? <Pill tone="info" icon={<ShieldCheck />}>模型文字 · 已通过事实核对</Pill>
    : f.answer_source === "server_fallback" ? <Pill tone="pending">系统摘要 · 模型文字未通过校验</Pill> : <Pill tone="outline">系统摘要</Pill>;
  return (
    <article className="bubble bubble--assistant" aria-label="最终回答">
      <div className="bubble__meta">{badge}{status === "terminated" && <Pill tone="fail">已终止</Pill>}{status === "failed" && <Pill tone="fail">已失败</Pill>}</div>
      <p className="answer" style={{ fontSize: 14.5 }}>{f.answer}</p>
      {f.fallback_reason.length > 0 && (
        <p className="faint" style={{ fontSize: 12.5, marginTop: 6 }}>原因：{[...new Set(f.fallback_reason.map(codeOf))].join("；")}</p>)}
      {(ev.runs.length > 0 || ev.reports.length > 0 || ev.projects.length > 0) && (
        <div className="evidence">
          {ev.projects.length > 0 && <p className="muted" style={{ fontSize: 12.5 }}>项目：{ev.projects.map((p) => <span key={p.project_id}><Link to={`/?project=${encodeURIComponent(p.project_id)}`} className="mono">{p.project_id}</Link> </span>)}</p>}
          {ev.runs.length > 0 && (
            <div className="tablewrap">
              <table className="table table--dense table--stack">
                <caption className="evidence__cap">规则判定 · 来自规则引擎（{ev.verdict_source}），演示规则（{ev.rule_status}），不是法规要求；此表由服务端按工具结果填入，不取自模型文字</caption>
                <thead><tr><th>分析批</th><th>状态</th><th>判定</th></tr></thead>
                <tbody>{ev.runs.map((r) => (
                  <tr key={r.run_id}>
                    <td><Link className="mono" to={sourceLink(`run:${r.run_id}`).to!}>{r.run_id}</Link></td>
                    <td data-label="状态"><RunStatusPill status={r.status} /></td>
                    <td data-label="判定"><VerdictStrip calibration={r.calibration} qc={r.qc} accepted={r.accepted} isr={r.isr} /></td>
                  </tr>))}</tbody>
              </table>
            </div>)}
          {ev.reports.map((d) => (
            <div key={d.draft_id} className="callout" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 8 }}>
              <span className="mono">{d.draft_id}</span><ReviewPill status={d.review_status} />
              <Link className="btn btn--sm" to={d.review_url}><Link2 />打开审核页</Link>
              <span className="muted" style={{ fontSize: 12.5, flexBasis: "100%" }}>审核通过只能由人在审核页完成；Agent 不能批准或导出报告，人工审核通过也不等于正式 QA 批准。</span>
            </div>))}
        </div>)}
    </article>
  );
}

/* ---------- 待用户确认的写操作 ---------- */
function PendingCard({ a, now, busy, onConfirm, onReject }: { a: AgentPending; now: number; busy: boolean; onConfirm: () => void; onReject: () => void }) {
  const left = Math.max(0, Math.round((Date.parse(a.expires_at) - now) / 1000));
  const expired = left <= 0;
  const entries = Object.entries(a.args).filter(([, v]) => v !== "" && !(Array.isArray(v) && v.length === 0));
  return (
    <section className="pending" aria-label="待确认的操作">
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <CircleAlert size={16} aria-hidden style={{ color: "var(--warning)" }} />
        <strong>待你确认：{TOOL_ZH[a.tool] ?? a.tool}</strong>
        <Pill tone={expired ? "fail" : "pending"}>{expired ? "已过期" : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} 内有效`}</Pill>
      </div>
      <p style={{ margin: "6px 0" }}>{a.summary}</p>
      <dl className="kv" style={{ marginBottom: 10 }}>
        {entries.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd className="mono">{Array.isArray(v) ? v.join("、") : String(v)}</dd></div>)}
      </dl>
      <p className="faint" style={{ fontSize: 12.5, marginBottom: 8 }}>确认与上面显示的内容绑定：参数变化、超过有效期或重复确认都会被服务端拒绝。确认前不会写入任何数据。</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="primary" icon={<Check />} onClick={onConfirm} disabled={busy || expired}>确认执行</Button>
        <Button icon={<X />} onClick={onReject} disabled={busy}>拒绝</Button>
      </div>
    </section>
  );
}

/* ---------- 步骤 ---------- */
function StepRow({ s }: { s: AgentStep }) {
  const role = AGENT_ROLE_ZH[s.agent_role] ?? s.agent_role;
  const bad = !!s.status && !["ok", "accepted", "awaiting_confirmation", "model"].includes(s.status);
  return (
    <li className="step-row">
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "baseline" }}>
        <strong style={{ fontWeight: 500 }}>{role}</strong>
        <span className="muted">{AGENT_KIND_ZH[s.kind] ?? s.kind}{s.tool_name ? ` · ${TOOL_ZH[s.tool_name] ?? s.tool_name}` : ""}</span>
        {s.status && <Pill tone={bad ? "fail" : "outline"}>{STEP_STATUS_ZH[s.status] ?? s.status}</Pill>}
      </div>
      {s.summary && !REDUNDANT.has(s.kind) && <div className="faint" style={{ fontSize: 12.5 }}>{s.summary}</div>}
      {s.args_summary && <div className="mono faint" style={{ fontSize: 12 }}>{s.args_summary}</div>}
      {s.sources.length > 0 && (
        <div className="chips">{s.sources.slice(0, 8).map((src) => {
          const l = sourceLink(src);
          return l.to ? <Link key={src} to={l.to} className="chip">{l.label}</Link> : <span key={src} className="chip">{l.label}</span>;
        })}{s.sources.length > 8 && <span className="chip">+{s.sources.length - 8}</span>}</div>)}
    </li>
  );
}

function Trace({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const q = useAgentTrace(id, open);
  return (
    <details onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary style={{ cursor: "pointer", fontSize: 13 }}>审计记录（仅你可见）</summary>
      {open && (q.isPending ? <SkelRows rows={3} h={32} /> : q.isError ? <ErrorBlock error={q.error} what="审计记录" onRetry={() => void q.refetch()} /> : (
        <div style={{ marginTop: 8 }}>
          <p className="faint" style={{ fontSize: 12 }}>{q.data.content_policy}。判定来源：{q.data.verdict_source}。</p>
          <div className="tablewrap"><table className="table table--dense">
            <thead><tr><th>步骤</th><th>请求号</th><th>角色 · 类型</th><th>哈希</th></tr></thead>
            <tbody>{q.data.steps.map((s) => (
              <tr key={s.step_id}><td className="num">{s.step_id}</td><td className="mono">{(s.request_id ?? "—").slice(0, 8)}</td>
                <td>{AGENT_ROLE_ZH[s.agent_role] ?? s.agent_role} · {AGENT_KIND_ZH[s.kind] ?? s.kind}</td><td className="mono">{s.content_sha256 ? s.content_sha256.slice(0, 8) : "—"}</td></tr>))}</tbody>
          </table></div>
        </div>))}
    </details>
  );
}

/* ---------- 页面 ---------- */
export function Agent() {
  const auth = useAuth();
  if (auth.state.status !== "ready") return <SkelRows rows={4} />;                // 登录状态未就绪（应用的登录门控通常已保证就绪）
  return <AgentPage enabled={auth.state.me.llm_enabled} />;
}

function AgentPage({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const taskId = sp.get("task") ?? undefined;
  const task = useAgentTask(enabled ? taskId : undefined);                       // 未启用时不请求任何 Agent 接口
  const tasks = useAgentTasks(enabled);
  const [draft, setDraft] = useState("");
  const [call, setCall] = useState<Call>({ kind: "idle" });
  const [now, setNow] = useState(() => Date.now());
  const [allSteps, setAllSteps] = useState(false);
  const busyRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const t = task.data;
  const busy = call.kind === "busy";

  useEffect(() => { document.title = "Agent 对话 · 生物分析工作台"; }, []);
  useEffect(() => {
    if (!busy && !t?.pending_actions.length) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [busy, t?.pending_actions.length]);

  /** 统一的“调用服务端”：防重复提交、超时、停止等待、错误归类。 */
  async function run(label: string, fn: (signal: AbortSignal) => Promise<AgentTask>, message?: string): Promise<boolean> {
    if (busyRef.current) return false;                                           // 重复点击 / 重复提交：只处理第一次
    busyRef.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; ac.abort(); }, agentConfig.timeoutMs);
    setCall({ kind: "busy", label, startedAt: Date.now(), message });
    try {
      const snap = await fn(ac.signal);
      qc.setQueryData(["agent-task", snap.task_id], snap);
      if (snap.task_id !== taskId) setSp({ task: snap.task_id }, { replace: false });
      setCall({ kind: "idle" });
      void qc.invalidateQueries({ queryKey: ["agent-tasks"] });
      if (snap.replayed) toast.ok("该操作此前已确认，没有重复执行。");
      return true;
    } catch (e) {
      if ((e as Error).name === "AbortError") setCall({ kind: timedOut ? "timeout" : "cancelled", what: label });
      else {
        setCall({ kind: "error", error: e });
        if (e instanceof ApiError && ["task_busy", "task_not_waiting", "action_expired", "action_in_progress", "task_expired"].includes(e.code ?? "")) {
          void qc.invalidateQueries({ queryKey: ["agent-task", taskId] });
        }
      }
      void qc.invalidateQueries({ queryKey: ["agent-tasks"] });
      if (taskId) void qc.invalidateQueries({ queryKey: ["agent-task", taskId] });
      return false;
    } finally {
      clearTimeout(timer);
      busyRef.current = false;
      abortRef.current = null;
    }
  }

  const send = async () => {
    const text = draft.trim();
    if (!text || busyRef.current) return;
    if (await run("发送消息", (signal) => api<AgentTask>("/api/agent/messages", { method: "POST", body: { message: text, task_id: taskId }, signal }), text)) setDraft("");
  };
  const confirm = (a: AgentPending) => void run("确认执行", (signal) => api<AgentTask>(`/api/agent/tasks/${taskId}/actions/${a.action_id}/confirm`, { method: "POST", body: { args_hash: a.args_hash }, signal }));
  const reject = (a: AgentPending) => void run("拒绝操作", (signal) => api<AgentTask>(`/api/agent/tasks/${taskId}/actions/${a.action_id}/reject`, { method: "POST", body: {}, signal }));
  const newTask = () => { setSp({}, { replace: false }); setCall({ kind: "idle" }); setDraft(""); };

  // ---------- 输入区是否可用，以及原因 ----------
  let lock: string | null = null;
  if (!enabled) lock = "Agent 未启用：当前部署没有启用大模型。";
  else if (taskId && task.isError) lock = "任务不可用，请新建任务。";
  else if (t?.status === "awaiting_confirmation") lock = "请先确认或拒绝上方待执行的操作。";
  else if (t?.status === "running") lock = "任务仍在处理中，完成后才能继续提问。";
  else if (t && TERMINAL.includes(t.status)) lock = t.status === "expired" ? "任务已过期，请新建任务。" : "该任务已结束，请新建任务。";

  const conv = t?.conversation ?? [];
  const lastIsAnswered = !!t?.final && conv.length > 0 && conv[conv.length - 1].role === "assistant" && t.status === "idle";
  // 默认只看本轮、且不列“模型调用”（用量已在上方统计）；“显示全部”才包含历史轮次与模型调用
  const steps = t ? (allSteps ? t.steps : t.steps.slice(Math.max(0, t.steps.map((s) => s.kind).lastIndexOf("user_message"))).filter((s) => s.kind !== "model_call")) : [];
  const elapsed = call.kind === "busy" ? Math.max(0, Math.round((now - call.startedAt) / 1000)) : 0;

  return (
    <>
      <PageHead title="Agent 对话"
        sub="总控把任务委派给数据、审核、问答、翻译、报告专家；专家只能调用各自获授权的工具。判定始终来自规则引擎，写操作须你确认，Agent 不能审核通过报告。"
        actions={<Button icon={<Plus />} onClick={newTask}>新任务</Button>} />
      {!enabled && (
        <div style={{ marginBottom: 16 }}>
          <Notice tone="warn" title="Agent 未启用">当前部署没有启用大模型，不会进行任何多智能体协作。需在服务器 <span className="mono">.env</span> 中设置 <span className="mono">LLM_PROVIDER=deepseek</span> 与 <span className="mono">LLM_API_KEY</span>（Docker 部署的 api 容器默认无法访问外网）。</Notice>
        </div>)}
      <div className="grid grid--main agent">
        <div className="stack">
          <Panel title="对话" meta={t && <Pill tone={statusTone(t.status)}>{AGENT_STATUS_ZH[t.status]}</Pill>} className="chat">
            {taskId && task.isPending ? <SkelRows rows={3} /> : taskId && task.isError ? (
              <ErrorBlock error={task.error} what="任务" onRetry={() => void task.refetch()} />
            ) : (
              <div className="chat__log" role="log" aria-live="polite" aria-label="对话记录">
                {conv.length === 0 && !busy && <EmptyState title="开始一个任务"><span>例如：</span><button className="linkbtn" onClick={() => setDraft(EXAMPLE)}>{EXAMPLE}</button></EmptyState>}
                {conv.map((m, i) => (m.role === "user" ? <article key={i} className="bubble bubble--user">{m.content}</article>
                  : i === conv.length - 1 && lastIsAnswered && t?.final ? <FinalCard key={i} f={t.final} status={t.status} />
                  : <article key={i} className="bubble bubble--assistant"><p className="answer" style={{ fontSize: 14.5 }}>{m.content}</p></article>))}
                {busy && call.message && <article className="bubble bubble--user">{call.message}</article>}
                {busy && (
                  <div className="bubble bubble--assistant" role="status" aria-label="处理中">
                    <span className="btn__spin" aria-hidden style={{ display: "inline-block", marginRight: 8, verticalAlign: -2 }} />{call.label}：处理中 {elapsed} 秒…
                    <div className="faint" style={{ fontSize: 12.5, marginTop: 4 }}>服务端正在调用模型与工具。停止等待不会撤销已经开始的处理。</div>
                    <div style={{ marginTop: 8 }}><Button size="sm" icon={<Square />} onClick={() => abortRef.current?.abort()}>停止等待</Button></div>
                  </div>)}
                {!lastIsAnswered && t?.final && !busy && <FinalCard f={t.final} status={t.status} />}
                {t?.pending_actions.map((a) => (
                  <PendingCard key={a.action_id} a={a} now={now} busy={busy} onConfirm={() => confirm(a)} onReject={() => reject(a)} />))}
              </div>)}
            {call.kind === "error" && (() => {
              const e = call.error;
              if (e instanceof ApiError && e.code === "llm_not_enabled") return <Notice tone="warn" title="Agent 未启用">{e.message}</Notice>;
              if (e instanceof ApiError && e.code === "audit_unavailable") return <Notice tone="error" title="操作未执行">{e.message}（审计不可用时系统不会执行写操作）。</Notice>;
              if (e instanceof ApiError && (e.code === "task_expired" || e.code === "task_not_found")) return <Notice tone="warn" title="任务不可用">{e.message}。请点击“新任务”。</Notice>;
              return <ErrorBlock error={e} what="请求失败" onRetry={busy ? undefined : () => setCall({ kind: "idle" })} />;
            })()}
            {call.kind === "timeout" && <Notice tone="warn" title="等待超时">“{call.what}”等待超过 {Math.round(agentConfig.timeoutMs / 1000)} 秒。服务端可能仍在处理，{taskId ? "状态会自动刷新" : "请在右侧“我的任务”里查看"}；确认前不会重复执行写操作。</Notice>}
            {call.kind === "cancelled" && <Notice tone="info" title="已停止等待">“{call.what}”仍可能在服务端完成；{taskId ? "状态会自动刷新" : "请在右侧“我的任务”里查看"}。</Notice>}
            {t?.audit_complete === false && <div style={{ marginTop: 10 }}><Notice tone="warn" title="审计不完整">{t.audit_note ?? "部分步骤未能写入审计。"}</Notice></div>}
            <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
              <label className="sr-only" htmlFor="agent-input">消息</label>
              <textarea id="agent-input" className="textarea" rows={3} maxLength={4000} value={draft} disabled={!!lock || busy}
                placeholder={lock ?? "输入任务，例如：" + EXAMPLE} onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }} />
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <Button type="submit" variant="primary" icon={<Send />} loading={busy} disabled={!!lock || !draft.trim()}>发送</Button>
                <span className="faint" style={{ fontSize: 12.5 }}>{lock ?? `Ctrl + Enter 发送 · ${draft.length}/4000 · 发送前会自动脱敏`}</span>
              </div>
            </form>
          </Panel>
        </div>

        <aside className="stack" aria-label="任务信息">
          <Panel title="任务状态" meta={t && <span className="mono faint">{t.task_id.slice(0, 8)}</span>}>
            {!t ? <EmptyState title="还没有任务">发送一条消息后，这里显示角色、步骤和用量。</EmptyState> : (
              <dl className="kv">
                <dt>状态</dt><dd><Pill tone={statusTone(t.status)}>{AGENT_STATUS_ZH[t.status]}</Pill></dd>
                <dt>当前角色</dt><dd><span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Bot size={14} aria-hidden />{t.active_role_zh}</span></dd>
                <dt>本轮用量</dt><dd className="num">模型 {t.usage.rounds}/{t.usage.limits.max_rounds} 轮 · 工具 {t.usage.tool_calls}/{t.usage.limits.max_tool_calls} 次</dd>
                <dt>token</dt><dd className="num">{t.usage.tokens_used.toLocaleString("zh-CN")} / {t.usage.limits.max_tokens.toLocaleString("zh-CN")}</dd>
                <dt>有效至</dt><dd className="num">{fmtDateTime(t.expires_at)}</dd>
                {t.termination_reason && <><dt>终止原因</dt><dd>{FALLBACK_ZH[t.termination_reason] ?? t.termination_reason}</dd></>}
                {t.error_code && <><dt>失败原因</dt><dd>{FALLBACK_ZH[t.error_code] ?? t.error_code}</dd></>}
              </dl>)}
          </Panel>

          <Panel title="执行步骤" meta={t && t.steps.length > 0 && <button className="linkbtn" style={{ fontWeight: 400, fontSize: 12.5 }} onClick={() => setAllSteps(!allSteps)}>{allSteps ? "只看本轮" : "显示全部"}</button>} flush
            foot="步骤由服务端执行并记录；这里只显示角色、工具名、白名单参数、状态和来源，不显示模型推理、提示词、工具结果正文或原始参数。">
            {steps.length === 0 ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="暂无步骤" /></div> : <ol className="step-list">{steps.map((s) => <StepRow key={s.step_id} s={s} />)}</ol>}
          </Panel>

          <Panel title="我的任务" meta={<Pill tone="outline">当前登录会话</Pill>} flush>
            {!enabled ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="没有任务">启用 Agent 后，你的任务会列在这里。</EmptyState></div> : tasks.isPending ? <SkelRows rows={2} h={40} /> : tasks.isError ? <ErrorBlock error={tasks.error} onRetry={() => void tasks.refetch()} /> :
              tasks.data.length === 0 ? <div style={{ padding: "0 var(--pad) var(--pad)" }}><EmptyState title="没有进行中的任务" /></div> : (
                <ul className="list">{tasks.data.map((r) => (
                  <li key={r.task_id} className="list__item">
                    <button className="linkbtn list__main" style={{ textAlign: "left", fontWeight: 400 }} aria-current={r.task_id === taskId ? "true" : undefined}
                      onClick={() => { setSp({ task: r.task_id }, { replace: false }); setCall({ kind: "idle" }); }}>
                      <div className="list__title truncate">{r.preview || "（空）"}</div>
                      <div className="list__sub">{fmtDateTime(r.updated_at)}</div>
                    </button>
                    <Pill tone={statusTone(r.status)}>{AGENT_STATUS_ZH[r.status]}</Pill>
                    <ChevronRight size={14} aria-hidden className="faint" />
                  </li>))}</ul>)}
          </Panel>
          {t && <Panel title="审计" meta={<Pill tone={t.audit_complete ? "pass" : "pending"}>{t.audit_complete ? "留痕完整" : "留痕不完整"}</Pill>}><Trace id={t.task_id} /></Panel>}
        </aside>
      </div>
    </>
  );
}
