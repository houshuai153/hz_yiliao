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

// ---------- Agent ----------
export const AGENT_STATUS_ZH = { running: "处理中", awaiting_confirmation: "等待你确认", idle: "已完成本轮", failed: "已失败", terminated: "已终止", expired: "已过期" } as const;
export const AGENT_ROLE_ZH: Record<string, string> = { orchestrator: "总控", data: "数据专家", review: "审核专家", qa: "问答专家", translation: "翻译专家", report: "报告专家" };
export const AGENT_KIND_ZH: Record<string, string> = {
  user_message: "用户消息", model_call: "模型调用", handoff: "交接", handoff_return: "交回结果", tool_call: "工具调用",
  pending_action: "提出待确认操作", confirmation: "确认", write_result: "写操作结果", final: "最终回答", terminated: "终止", error: "错误",
};
export const TOOL_ZH: Record<string, string> = {
  list_projects: "项目列表", get_project: "项目信息", list_project_runs: "分析批列表", get_run_detail: "分析批明细", dashboard_project: "项目看板指标",
  ask_regulatory: "法规 / SOP 检索", translate_text: "翻译与术语核对", generate_report_draft: "生成报告草稿", list_reports: "报告列表",
  get_report: "报告审核证据", transition_report: "推进审核状态", delegate: "委派专家",
};
export const STEP_STATUS_ZH: Record<string, string> = {
  ok: "成功", rejected: "已拒绝", accepted: "已确认", replayed: "重复（未重复执行）", awaiting_confirmation: "等待确认", model: "模型", server_fallback: "系统摘要",
  tool_not_permitted: "无权调用", unknown_tool: "未知工具", invalid_arguments: "参数不合法", user_intent_missing: "用户未要求该操作", write_must_be_alone: "写操作须单独调用",
  confirmation_required: "需要确认", result_too_large: "结果过大",
};
export const FALLBACK_ZH: Record<string, string> = {
  verdict_conflict: "文字中的判定与规则结果冲突", unanchored_verdict: "判定词没有对应到具体分析批", project_level_verdict: "项目级结论没有规则依据",
  unknown_id: "引用了工具结果里没有的编号", unsupported_number: "出现了工具结果里没有的数字", approval_claim: "声称已审核通过 / 批准 / 导出",
  generated_claim_without_evidence: "声称已生成草稿，但没有生成结果", too_long: "回答过长",
  max_rounds: "达到模型调用轮数上限", max_tool_calls: "达到工具调用次数上限", timeout: "达到总耗时上限", token_budget: "达到 token 用量上限",
  confirmation_expired: "待确认操作已过期", invalid_model_output: "模型返回了不符合协议的响应", model_unavailable: "模型调用失败", internal_error: "Agent 处理出错",
};
