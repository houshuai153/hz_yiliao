import { LogIn } from "lucide-react";
import { type FormEvent, useState } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { BrandMark } from "../components/Shell";
import { Button, Field, Notice } from "../components/ui";

export function Login() {
  const { login, state, retry } = useAuth();
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try { await login(user.trim(), pw); }
    catch (x) { setErr(x instanceof ApiError ? x.message : "登录失败，请稍后重试"); setPw(""); }
    finally { setBusy(false); }
  }

  return (
    <div className="login">
      <form className="login__card" onSubmit={submit} aria-label="登录">
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <BrandMark />
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 600 }}>生物分析工作台</h1>
            <div className="muted" style={{ fontSize: 12.5 }}>演示环境 · 模拟数据</div>
          </div>
        </div>
        {state.status === "error" && (
          <Notice tone="error" title="无法连接服务">
            {state.error.message} <Button size="sm" variant="text" onClick={retry}>重试</Button>
          </Notice>
        )}
        {err && <Notice tone="error">{err}</Notice>}
        <Field label="用户名">{(id) => <input id={id} className="input" autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} required autoFocus />}</Field>
        <Field label="口令">{(id) => <input id={id} className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} required />}</Field>
        <Button variant="primary" type="submit" loading={busy} icon={<LogIn />}>登录</Button>
        <p className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>登录名将作为审核与审计中的“操作人记录”，不是受监管的电子签名。</p>
      </form>
    </div>
  );
}
