import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Shell } from "./components/Shell";
import { ToastProvider } from "./components/Toast";
import { Skel } from "./components/ui";
import { Agent } from "./pages/Agent";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";
import { Qa } from "./pages/Qa";
import { ReportReview } from "./pages/ReportReview";
import { Reports } from "./pages/Reports";
import { Translate } from "./pages/Translate";

export const makeClient = () => new QueryClient({
  defaultOptions: {
    queries: { retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 1, refetchOnWindowFocus: false },
  },
});

function Gate() {
  const { state } = useAuth();
  if (state.status === "loading") return <div className="login" role="status" aria-label="加载中"><Skel w={280} h={160} /></div>;
  if (state.status !== "ready") return <Login />;
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Dashboard />} />
        <Route path="reports" element={<Reports />} />
        <Route path="reports/:draftId" element={<ReportReview />} />
        <Route path="agent" element={<Agent />} />
        <Route path="qa" element={<Qa />} />
        <Route path="translate" element={<Translate />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App({ client }: { client?: QueryClient }) {
  const qc = client ?? makeClient();
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <Gate />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
