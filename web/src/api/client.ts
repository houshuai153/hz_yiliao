// 同源 API 客户端：只带会话 Cookie（HttpOnly，脚本读不到）与 CSRF 令牌；不接触静态 API_TOKEN。
export class ApiError extends Error {
  constructor(public status: number, public code: string | undefined, message: string, public detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

type Params = Record<string, string | number | boolean | null | undefined>;
interface Options { method?: string; body?: unknown; params?: Params; signal?: AbortSignal }

function url(path: string, params?: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

async function toError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try { body = await res.json(); } catch { /* 非 JSON 响应 */ }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "message" in (detail as object)) {
    const d = detail as { message: string; error_code?: string };
    return new ApiError(res.status, d.error_code, d.message, detail);
  }
  if (Array.isArray(detail)) return new ApiError(res.status, "validation", "输入不符合要求，请检查后重试", detail);
  return new ApiError(res.status, undefined, typeof detail === "string" ? detail : `请求失败（HTTP ${res.status}）`, detail);
}

function headers(method: string, hasBody: boolean): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json" };
  if (hasBody) h["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) h["X-CSRF-Token"] = csrfToken;
  return h;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  let res: Response;
  try {
    res = await fetch(url(path, opts.params), {
      method, credentials: "same-origin", signal: opts.signal,
      headers: headers(method, opts.body !== undefined),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "无法连接服务，请检查网络后重试");
  }
  if (!res.ok) {
    const err = await toError(res);
    if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("auth:expired"));
    throw err;
  }
  return (await res.json()) as T;
}

/** 以文件形式取回（导出 DOCX）。后端门禁不通过时抛出 ApiError。 */
export async function downloadFile(path: string): Promise<{ blob: Blob; filename: string }> {
  let res: Response;
  try { res = await fetch(path, { credentials: "same-origin" }); }
  catch { throw new ApiError(0, "network", "无法连接服务，请检查网络后重试"); }
  if (!res.ok) throw await toError(res);
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  return { blob: await res.blob(), filename: m ? decodeURIComponent(m[1]) : "report.docx" };
}

export function saveBlob(blob: Blob, filename: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
