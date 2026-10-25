import {
  AlertTriangle, Check, CircleAlert, Inbox, Info, Minus, RotateCw, ShieldAlert, WifiOff, X, CheckCircle2,
} from "lucide-react";
import { type ReactNode, useId } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import type { ReviewStatus, RunStatus, Verdict } from "../api/types";
import { REVIEW_ZH, RUN_STATUS_ZH, VERDICT_ZH } from "../lib/labels";

/* ---------- 按钮 ---------- */
interface BtnProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "text" | "danger"; size?: "md" | "sm"; loading?: boolean; icon?: ReactNode;
}
export function Button({ variant = "default", size = "md", loading, icon, children, disabled, className = "", ...rest }: BtnProps) {
  const cls = ["btn", variant !== "default" && `btn--${variant}`, size === "sm" && "btn--sm", className].filter(Boolean).join(" ");
  return (
    <button type="button" {...rest} className={cls} disabled={disabled || loading} aria-busy={loading || undefined}>
      {loading ? <span className="btn__spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

/* ---------- 状态标记（符号 + 文字，不只靠颜色）---------- */
export function Pill({ tone = "neutral", children, icon }: { tone?: "neutral" | "pass" | "pending" | "fail" | "info" | "outline"; children: ReactNode; icon?: ReactNode }) {
  return <span className={`pill${tone !== "neutral" ? ` pill--${tone}` : ""}`}>{icon}{children}</span>;
}
export function VerdictMark({ v, label }: { v: Verdict | null | undefined; label?: string }) {
  const k = v ?? "none";
  const Icon = k === "pass" ? Check : k === "fail" ? X : k === "pending_review" ? CircleAlert : Minus;
  const text = v ? VERDICT_ZH[v] : "—";
  return (
    <span className={`verdict verdict--${k === "pending_review" ? "pending" : k}`}>
      <Icon aria-hidden />
      {label && <span className="verdict__k">{label}</span>}
      <span>{text}</span>
    </span>
  );
}
/** 判定带：校准 / QC / 批接受 / ISR 四格（符号 + 文字）。这个产品独有的元素。 */
export function VerdictStrip({ calibration, qc, accepted, isr }: { calibration: Verdict; qc: Verdict; accepted: Verdict; isr: Verdict }) {
  return (
    <span className="verdicts">
      <VerdictMark v={calibration} label="校准" />
      <VerdictMark v={qc} label="QC" />
      <VerdictMark v={accepted} label="批接受" />
      <VerdictMark v={isr} label="ISR" />
    </span>
  );
}
export function RunStatusPill({ status }: { status: RunStatus }) {
  const tone = status === "completed" ? "outline" : status === "under_review" ? "pending" : "info";
  return <Pill tone={tone}>{RUN_STATUS_ZH[status]}</Pill>;
}
export function ReviewPill({ status }: { status: ReviewStatus }) {
  const tone = status === "reviewed" ? "pass" : status === "changes_requested" ? "fail" : status === "in_review" ? "info" : "neutral";
  return <Pill tone={tone}>{REVIEW_ZH[status]}</Pill>;
}

/* ---------- 版式 ---------- */
export function PageHead({ title, sub, actions, crumbs }: { title: string; sub?: ReactNode; actions?: ReactNode; crumbs?: ReactNode }) {
  return (
    <header>
      {crumbs}
      <div className="pagehead">
        <div style={{ minWidth: 0 }}>
          <h1 className="pagehead__title">{title}</h1>
          {sub && <p className="pagehead__sub">{sub}</p>}
        </div>
        {actions && <div className="pagehead__actions">{actions}</div>}
      </div>
    </header>
  );
}
export function Panel({ title, meta, children, foot, flush, className = "", id }: { title?: ReactNode; meta?: ReactNode; children: ReactNode; foot?: ReactNode; flush?: boolean; className?: string; id?: string }) {
  return (
    <section className={`panel${flush ? " panel--flush" : ""} ${className}`} id={id} aria-label={typeof title === "string" ? title : undefined}>
      {(title || meta) && (
        <div className="panel__head">
          <h2 className="panel__title">{title}</h2>
          {meta && <div className="panel__meta">{meta}</div>}
        </div>
      )}
      <div className="panel__body">{children}</div>
      {foot && <div className="panel__foot">{foot}</div>}
    </section>
  );
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}
export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}
export function Tabs<T extends string>({ value, tabs, onChange, label }: { value: T; tabs: { value: T; label: string; count?: number | string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.value} role="tab" type="button" className="tab" aria-selected={t.value === value} onClick={() => onChange(t.value)}>
          {t.label}{t.count !== undefined && <span className="tab__count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------- 状态：加载 / 空 / 错误 / 提示 ---------- */
export function Skel({ w = "100%", h = 12, style }: { w?: number | string; h?: number; style?: React.CSSProperties }) {
  return <span className="skel" style={{ width: w, height: h, ...style }} aria-hidden />;
}
export function SkelRows({ rows = 4, h = 44 }: { rows?: number; h?: number }) {
  return (
    <div role="status" aria-label="加载中" style={{ display: "grid", gap: 2 }}>
      {Array.from({ length: rows }, (_, i) => <Skel key={i} h={h - 16} style={{ margin: "8px var(--pad)", width: "auto" }} />)}
    </div>
  );
}
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty" role="note">
      <Inbox size={18} aria-hidden style={{ marginBottom: 4 }} />
      <strong>{title}</strong>
      {children}
    </div>
  );
}
export function errorText(e: unknown): { icon: ReactNode; text: string } {
  if (e instanceof ApiError) {
    if (e.status === 403) return { icon: <ShieldAlert aria-hidden />, text: e.message === "missing or invalid CSRF token" ? "页面凭据已过期，请刷新后重试" : "没有权限执行该操作" };
    if (e.status === 401) return { icon: <ShieldAlert aria-hidden />, text: "登录已过期，请重新登录" };
    if (e.status === 0) return { icon: <WifiOff aria-hidden />, text: e.message };
    return { icon: <AlertTriangle aria-hidden />, text: e.message };
  }
  return { icon: <AlertTriangle aria-hidden />, text: "出错了，请稍后重试" };
}
export function ErrorBlock({ error, onRetry, what }: { error: unknown; onRetry?: () => void; what?: string }) {
  const { icon, text } = errorText(error);
  return (
    <div className="errblock" role="alert">
      {icon}
      <span style={{ flex: 1, minWidth: 200 }}>{what ? `${what}：` : ""}{text}</span>
      {onRetry && <Button size="sm" icon={<RotateCw />} onClick={onRetry}>重试</Button>}
    </div>
  );
}
export function Notice({ tone = "info", title, children }: { tone?: "info" | "warn" | "error" | "ok"; title?: string; children?: ReactNode }) {
  const Icon = tone === "ok" ? CheckCircle2 : tone === "info" ? Info : tone === "warn" ? AlertTriangle : CircleAlert;
  return (
    <div className={`notice notice--${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon aria-hidden />
      <div className="notice__body">{title && <div className="notice__title">{title}</div>}{children}</div>
    </div>
  );
}

/** 统一处理 react-query 的 加载 / 错误 / 空 状态；children 只在有数据时渲染。 */
export function Query<T>({ q, skeleton, empty, what, children }: {
  q: UseQueryResult<T>; skeleton?: ReactNode; empty?: (d: T) => ReactNode | null; what?: string; children: (d: T) => ReactNode;
}) {
  if (q.isPending) return <>{skeleton ?? <SkelRows />}</>;
  if (q.isError) return <ErrorBlock error={q.error} what={what} onRetry={() => void q.refetch()} />;
  const e = empty?.(q.data);
  if (e) return <>{e}</>;
  return <>{children(q.data)}</>;
}
