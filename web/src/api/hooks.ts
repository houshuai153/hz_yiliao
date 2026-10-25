import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AskResult, Capabilities, ChartData, DraftDetail, DraftSummary, EventRow, Health, Overview, ProjectMetrics,
  ProjectRow, RunDetail, VersionRow,
} from "./types";

const STALE = 15_000;
export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health"), staleTime: 60_000 });
export const useOverview = (asOf: string) => useQuery({ queryKey: ["overview", asOf], queryFn: () => api<Overview>("/api/dashboard/overview", { params: { as_of: asOf } }), staleTime: STALE });
export const useCharts = (asOf: string) => useQuery({ queryKey: ["charts", asOf], queryFn: () => api<ChartData>("/api/dashboard/charts", { params: { as_of: asOf } }), staleTime: STALE });
export const useProject = (pid: string | undefined, asOf: string) => useQuery({
  queryKey: ["project", pid, asOf], enabled: !!pid, staleTime: STALE,
  queryFn: () => api<ProjectMetrics>(`/api/dashboard/projects/${pid}`, { params: { as_of: asOf } }),
});
export const useRun = (rid: string | undefined) => useQuery({ queryKey: ["run", rid], enabled: !!rid, queryFn: () => api<RunDetail>(`/api/runs/${rid}`), staleTime: STALE });
export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => api<ProjectRow[]>("/api/projects"), staleTime: 60_000 });
export const useProjectRuns = (pid: string | undefined) => useQuery({ queryKey: ["project-runs", pid], enabled: !!pid, queryFn: () => api<{ id: string; run_date: string; status: string }[]>(`/api/projects/${pid}/runs`) });
export const useCapabilities = () => useQuery({ queryKey: ["capabilities"], queryFn: () => api<Capabilities>("/api/translate/capabilities"), staleTime: 300_000 });
export const useDrafts = () => useQuery({ queryKey: ["drafts"], queryFn: () => api<(DraftSummary & { open_issues: number })[]>("/api/reports"), staleTime: 5_000 });
export const useDraft = (id: string | undefined, version?: number) => useQuery({
  queryKey: ["draft", id, version ?? "current"], enabled: !!id,
  queryFn: () => api<DraftDetail>(`/api/reports/${id}`, { params: { version } }),
});
export const useVersions = (id: string | undefined, enabled = true) => useQuery({ queryKey: ["versions", id], enabled: !!id && enabled, queryFn: () => api<VersionRow[]>(`/api/reports/${id}/versions`) });
export const useEvents = (id: string | undefined, enabled = true) => useQuery({ queryKey: ["events", id], enabled: !!id && enabled, queryFn: () => api<EventRow[]>(`/api/reports/${id}/events`) });

export const ask = (question: string) => api<AskResult>("/api/regulatory/ask", { method: "POST", body: { question } });
