import type { Level, ReviewStatus, RunStatus, Verdict } from "../api/types";

export const VERDICT_ZH: Record<Verdict, string> = { pass: "通过", fail: "未通过", pending_review: "待复核" };
export const RUN_STATUS_ZH: Record<RunStatus, string> = { completed: "已完成", under_review: "复核中", in_progress: "进行中" };
export const LEVEL_ZH: Record<Level, string> = { high: "高", medium: "中", low: "低" };
export const SEVERITY_ZH = { minor: "轻微", major: "重要", critical: "严重" } as const;
export const MILESTONE_ZH = { delivered: "已交付", overdue: "逾期", due_soon: "即将到期", on_track: "正常" } as const;
export const MILESTONE_TYPE_ZH: Record<string, string> = { validation_report: "方法学验证报告", sample_report: "样品分析报告" };
export const REVIEW_ZH: Record<ReviewStatus, string> = { draft: "待审核", in_review: "审核中", changes_requested: "退回修改", reviewed: "审核通过" };
export const KIND_ZH = { validation: "方法学验证报告", sample_analysis: "样品分析报告" } as const;
export const REASON_ZH: Record<string, string> = { no_planned_runs: "未设置计划分析批数", no_decided_runs: "暂无已判定分析批" };
export const REFUSAL_ZH: Record<string, string> = {
  clause_fabrication_risk: "要求条款编号或监管原文", unknown_section: "指定的章节不存在", out_of_scope: "超出知识库范围",
  no_supporting_sentence: "没有可直接支撑答案的原句", citation_validation_failed: "引用校验未通过", empty_question: "问题为空",
};
export const SAMPLE_FLAG_ZH: Record<string, string> = {
  ok: "正常", BLQ: "低于定量下限（BLQ）", above_ULOQ: "高于定量上限（ULOQ）", reanalysis: "需重分析", missing: "缺失",
};
export const ACTION_ZH: Record<string, string> = {
  generated: "生成草稿", start_review: "开始审核", request_changes: "退回修改", resubmit: "重新提交审核", approve: "审核通过",
  edit: "修订", add: "新增", export: "导出 DOCX", issue_added: "提出问题", comment_added: "添加意见", comment_resolved: "处理意见",
};
export const TRANS_STATUS_ZH = { translated: "已翻译", partial: "部分翻译", unsupported: "不支持（未生成译文）", checked: "已核对" } as const;
export const DIRECTION_ZH = { zh2en: "中译英", en2zh: "英译中", bilingual: "双语对照（自动判断语言）" } as const;
export const CHG_ZH = { generated: "生成", edit: "修订", add: "新增" } as const;
