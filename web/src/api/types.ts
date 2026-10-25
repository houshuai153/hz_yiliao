// 与 FastAPI 响应一致的类型（只列页面实际使用的字段）。判定与指标一律来自后端，前端不重算。
export type Verdict = "pass" | "fail" | "pending_review";
export type RunStatus = "completed" | "under_review" | "in_progress";
export type Level = "high" | "medium" | "low";

export interface Rate { value: number | null; numerator: number; denominator: number; reason: string | null; exceeds_plan?: boolean }
export interface Me { username: string; csrf_token: string; llm_enabled: boolean; llm_model: string | null }
export interface Health { status: string; version: string; llm_provider: string; llm_enabled: boolean; llm_model: string | null; offline: boolean; simulated_data: boolean }

export interface Risk { risk_code: string; level: Level; message: string; evidence: Record<string, unknown> }
export interface VerdictBlock { eligible_runs: number; pass: number; fail: number; pending_review: number; pass_rate: Rate; records?: { total: number; pass: number; fail: number; pending_review: number } }
export interface RunSummary { id: string; run_date: string; status: RunStatus; counted_in_rates: boolean; calibration: Verdict; qc: Verdict; accepted: Verdict; isr: Verdict }
export interface Deviation { id: string; run_id: string | null; category: string; severity: "minor" | "major" | "critical"; status: "open" | "closed"; opened_at: string; closed_at: string | null; description: string }
export interface Milestone { type: string; planned_date: string; status: string; completed_date: string | null; state: "delivered" | "overdue" | "due_soon" | "on_track"; days_to_due: number | null }
export interface ProjectInfo { id: string; title: string; modality: string; method_type: string; analyte: string; matrix: string; unit: string; phase: string; is_simulated: boolean }
export interface RunsBlock { planned: number; existing: number; completed: number; under_review: number; in_progress: number; progress: Rate }
export interface ProjectMetrics {
  project: ProjectInfo; as_of: string;
  samples: { total: number; valid: number; blq: number; above_uloq: number; reanalysis: number; missing: number };
  runs: RunsBlock; calibration: VerdictBlock; qc: VerdictBlock; accepted: VerdictBlock; isr: VerdictBlock;
  deviations: { total: number; open: number; closed: number; open_by_severity: Record<string, number>; oldest_open_days: number | null; items: Deviation[] };
  milestones: Milestone[]; run_summaries: RunSummary[]; risks: Risk[]; rule_status: string;
}
export interface Overview {
  as_of: string; projects: ProjectMetrics[];
  totals: {
    projects: number; samples: { total: number; valid: number }; runs: RunsBlock; progress: Rate;
    open_deviations: number; open_major_plus: number; risks_by_level: Record<Level, number>;
    qc: VerdictBlock; isr: VerdictBlock; accepted: VerdictBlock;
  };
}
export interface PointRow { status: string; limit_pct: number | null; [k: string]: unknown }
export interface RunDetail {
  run: { id: string; project_id: string; run_date: string; status: RunStatus; rule_set_id: string };
  rule_status: string;
  assessment: { calibration: { verdict: Verdict }; qc: { verdict: Verdict }; isr: { verdict: Verdict }; accepted: Verdict };
  calibration_points: PointRow[]; qc_samples: PointRow[]; isr_records: PointRow[];
  sample_counts: Record<string, number>;
}
export interface SampleComposition { total: number; parts: { flag: string; count: number; share: number | null }[] }
export interface TimelinePoint { date: string; run_ids: string[]; runs_on_date: number; runs: { id: string; status: RunStatus; cumulative_rank: number }[]; cumulative: number; after_as_of: boolean }
export interface ChartData {
  as_of: string; notes: { progress: string; timeline: string; samples: string }; timeline_basis: string;
  progress: { project_id: string; title: string; planned: number; existing: number; completed: number; under_review: number; in_progress: number; unscheduled: number; exceeds_plan: boolean }[];
  runs: Record<string, { id: string; run_date: string; status: RunStatus; samples: SampleComposition; calibration: Verdict; qc: Verdict; accepted: Verdict; isr: Verdict }[]>;
  timeline: Record<string, TimelinePoint[]>;
  sample_composition: Record<string, SampleComposition>;
}

export interface Citation { doc_id: string; version: string; section_id: string; title: string; source_type: string; is_demo: boolean; label: string; quote: string }
export interface AskResult {
  answer: string; citations: Citation[]; confidence: number; confidence_basis: Record<string, unknown>; refused: boolean;
  refusal_reason: string | null; answer_mode: "extractive" | "llm_polished"; extractive_answer: string | null;
  llm: { provider?: string; used?: boolean; fallback_reason?: string | null; redactions?: Record<string, number> }; request_id: string;
}
export interface TransWarn { code: string; severity: "error" | "warning" | "info"; message: string }
export interface TermHit { term_id: string; matched_text: string; kind: string; span: [number, number]; case_ok: boolean; expected: { zh: string; en: string; abbr: string | null } }
export interface TransPara {
  index: number; source: string; source_lang: string; target_lang: string; direction: string; target: string | null;
  status: "translated" | "partial" | "unsupported" | "checked"; warnings: TransWarn[]; term_hits: TermHit[];
  sentences: { source: string; target: string | null; status: string; pattern_id: string | null; generated_by?: "pattern" | "llm" }[];
}
export interface TransResult { direction: string; paragraphs: TransPara[]; document_warnings: TransWarn[]; stats: Record<string, number>; capability_note: string; glossary_note: string; request_id: string }
export interface Capabilities { provider: string; is_generative: boolean; note: string; glossary_note: string; glossary_terms: number; patterns: { id: string; zh: string; en: string }[] }

export type ReviewStatus = "draft" | "in_review" | "changes_requested" | "reviewed";
export interface DraftSummary { draft_id: string; project_id: string; kind: "validation" | "sample_analysis"; run_ids: string[]; review_status: ReviewStatus; review_status_zh: string; current_version: number | null; legacy: boolean; created_at: string; missing_fields: string[]; open_issues?: number }
export interface SentenceBlock { type: "sentence"; bid: string; kind: "fact" | "static" | "placeholder"; text: string; fact_keys: string[]; tags: string[]; missing: string[]; edited: boolean; added: boolean; original_text: string | null; flag: string | null; editable: boolean; open_issues: number; comment_count: number }
export interface TableBlock { type: "table"; bid: string; caption: string; source: string; header: string[]; rows: string[][]; editable: false; open_issues: number; comment_count: number }
export type Block = SentenceBlock | TableBlock;
export interface Comment { id: number; block_id: string | null; kind: "comment" | "issue"; text: string; status: "open" | "resolved"; version: number; created_by: string; created_at: string; resolved_by: string | null; resolved_at: string | null; resolution: string | null }
export interface Blocker { code: string; message: string; ids?: number[]; block_ids?: string[] }
export interface Readiness { blockers: Blocker[]; missing_to_acknowledge: string[]; required_confirmations: Record<string, string>; open_issue_count: number; can_approve: boolean }
export interface FactRow { tag: string; key: string; label: string; value: unknown; display: string | null; unit: string; source_table: string; source_rows: string[]; rule_id: string | null }
export interface DraftDetail extends DraftSummary {
  banner: string; message?: string; title?: string; viewing_version?: number; is_current?: boolean; rule_ids?: string[];
  sections?: { title: string; blocks: Block[] }[]; facts?: FactRow[]; comments?: Comment[]; readiness?: Readiness;
  stats?: { fact_count: number; fact_sentences: number; edited_blocks: number; flagged_blocks: number; missing_fields: number }; export_ready?: boolean;
}
export interface VersionRow { version: number; created_at: string; operator: string; change_type: "generated" | "edit" | "add"; note: string; is_current: boolean }
export interface EventRow { id: number; ts: string; operator: string; action: string; from_status: ReviewStatus | null; to_status: ReviewStatus | null; version: number; comment: string; detail: Record<string, unknown> }
export interface Suggestion { bid: string; original: string; suggestion: string | null; used: boolean; reason: string | null; redactions: Record<string, number>; provider: string }
export interface DraftCreated { draft_id: string; review_status: ReviewStatus; missing_fields: string[]; fact_count: number; fact_sentences: number; unsourced_sentences: number }
export interface ProjectRow { id: string; title: string; method_type: string; planned_run_count: number; run_count: number }
