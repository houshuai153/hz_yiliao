import { CheckCircle2, CircleAlert } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

interface Toast { id: number; text: string; tone: "ok" | "error" }
const Ctx = createContext<{ ok: (t: string) => void; error: (t: string) => void }>({ ok: () => {}, error: () => {} });
export const useToast = () => useContext(Ctx);

/** 操作结果的明确反馈：成功 / 失败各一条，几秒后自动消失；role=status 供读屏软件播报。 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"]) => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s.slice(-3), { id, text, tone }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), tone === "error" ? 8000 : 4000);
  }, []);
  const api = useMemo(() => ({ ok: (t: string) => push(t, "ok"), error: (t: string) => push(t, "error") }), [push]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast${t.tone === "error" ? " toast--error" : ""}`}>
            {t.tone === "error" ? <CircleAlert aria-hidden /> : <CheckCircle2 aria-hidden />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
