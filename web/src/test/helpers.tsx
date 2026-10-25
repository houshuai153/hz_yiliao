import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/Toast";

type Handler = unknown | ((init: RequestInit | undefined, url: URL) => unknown);
export interface Call { method: string; path: string; body: unknown; headers: Record<string, string> }

/** 以“路由表”伪造 fetch：键为 "METHOD /path"（不含查询串）。未登记的请求返回 404，测试会因此失败而不是静默通过。 */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers: Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>)) });
    const h = routes[`${method} ${url.pathname}`];
    if (h === undefined) return new Response(JSON.stringify({ detail: `no mock for ${method} ${url.pathname}` }), { status: 404 });
    const out = typeof h === "function" ? (h as (i: RequestInit | undefined, u: URL) => unknown)(init, url) : h;
    if (out instanceof Response) return out;
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}
export const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const ME = { username: "tester", csrf_token: "csrf-123", llm_enabled: false, llm_model: null };
export const HEALTH = { status: "ok", version: "0.1.0", llm_provider: "deterministic", llm_enabled: false, llm_model: null, offline: true, simulated_data: true };

export function renderAt(ui: ReactElement, { path = "/", route = "/" }: { path?: string; route?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider><ToastProvider>
          <Routes><Route path={route} element={ui} /></Routes>
        </ToastProvider></AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
