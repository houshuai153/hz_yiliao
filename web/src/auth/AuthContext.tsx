import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, setCsrf } from "../api/client";
import type { Me } from "../api/types";

type State = { status: "loading" } | { status: "anon" } | { status: "ready"; me: Me } | { status: "error"; error: ApiError };
interface AuthApi { state: State; login: (u: string, p: string) => Promise<void>; logout: () => Promise<void>; retry: () => void }
const Ctx = createContext<AuthApi | null>(null);

export function useAuth(): AuthApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthProvider 缺失");
  return v;
}
export function useMe(): Me {
  const a = useAuth();
  if (a.state.status !== "ready") throw new Error("未登录");
  return a.state.me;
}

/** 会话由 HttpOnly Cookie 承载：这里只知道“是否登录”和 CSRF 令牌（存内存，不进 localStorage）。 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const qc = useQueryClient();

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const me = await api<Me>("/api/auth/me");
      setCsrf(me.csrf_token);
      setState({ status: "ready", me });
    } catch (e) {
      setCsrf(null);
      if (e instanceof ApiError && e.status === 401) setState({ status: "anon" });
      else setState({ status: "error", error: e instanceof ApiError ? e : new ApiError(0, "network", "无法连接服务") });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const onExpired = () => { setCsrf(null); qc.clear(); setState({ status: "anon" }); };
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, [qc]);

  const login = useCallback(async (username: string, password: string) => {
    await api("/api/auth/login", { method: "POST", body: { username, password } });
    qc.clear();
    await load();
  }, [load, qc]);
  const logout = useCallback(async () => {
    try { await api("/api/auth/logout", { method: "POST", body: {} }); } finally { setCsrf(null); qc.clear(); setState({ status: "anon" }); }
  }, [qc]);

  return <Ctx.Provider value={{ state, login, logout, retry: () => void load() }}>{children}</Ctx.Provider>;
}
