# SPEC：生物分析业务 AI 效能平台（工程原型）

状态：v0.2（已与需求方确认：混合检索、Python 3.12、目标部署云服务器）

## 0. 定位与诚实声明
- 工程原型。翻译、问答、报告均为**规则 + 模板 + 检索的确定性实现**，默认不具备 LLM 能力（`DeterministicProvider`）。可经 `.env` 显式启用 DeepSeek（阶段 8，见第 11 节），启用后大模型只做翻译兜底 / 问答措辞整理 / 报告修订建议，判定与指标仍只由规则计算。
- 所有业务数据为**模拟数据**，界面、API 响应、DOCX 页眉均标记“模拟数据”。
- 知识库为**教学演示摘要**，不是监管机构原文，不具合规效力，不使用真实条款编号。

## 1. 系统边界
- 范围内：翻译与术语核对、报告草稿、法规与 SOP 问答、项目看板、审计、离线评测。
- 范围外：LIMS 对接、电子签名、合规结论、细粒度权限；大模型默认关闭，仅在显式配置后启用（第 11 节）。
- 进程：FastAPI（业务 + 同源托管 React 构建产物）；前端 `web/`（React + TypeScript）只经同源 `/api` 通信，不直连数据库、不在前端计算判定或指标。
- 云部署：API 容器仅内部网络暴露；前置反向代理做 HTTPS；API 使用静态 Bearer 令牌（环境变量），Streamlit 经代理口令保护。运行时默认不访问公网；仅当显式启用在线 LLM 时，API 进程向 `LLM_BASE_URL` 发送脱敏文本（第 11 节）。

## 2. 数据模型（SQLAlchemy 2.0，SQLite 默认，PostgreSQL 兼容）
| 表 | 关键字段 |
|---|---|
| project | id（`PRJ-SM-001`）, title, modality, analyte, matrix, method_type, unit, lloq, uloq, phase, planned_run_count, internal_standard / sample_prep / instrument_setup / reagent_kit（方案类信息，可空；LBA 不使用内标），is_simulated |
| analytical_run | id（`PRJ-SM-001-R01`）, project_id, run_date, status(planned/in_progress/completed/under_review), rule_set_id |
| calibration_point | run_id, level_no, nominal, measured, back_calc_conc, accuracy_pct, included |
| qc_sample | (run_id, level, replicate), nominal, measured（可空）, bias_pct |
| study_sample | id, run_id, subject_code, timepoint, conc, flag(ok/BLQ/above_ULOQ/reanalysis/missing) |
| isr_record | sample_id, run_id, original_conc, repeat_conc（空=尚未复测）, diff_pct |
| deviation | id, project_id, run_id?, category, severity(minor/major/critical), status(open/closed), description, opened_at, closed_at |
| report_milestone | (project_id, type), planned_date, status(not_started/in_progress/delivered), completed_date |
| report_draft | id, project_id, run_ids, kind, path, created_at, review_status(draft/in_review/changes_requested/reviewed), facts_json, missing_fields, current_version(空=旧草稿) |
| report_version | (draft_id, version), content_json, operator, change_type(generated/edit/add), note |
| report_review_event | id, draft_id, ts, operator, action, from/to_status, version, comment, detail（只追加） |
| report_comment | id, draft_id, block_id, kind(comment/issue), text, status(open/resolved), 处理人与说明 |
| kb_document / kb_section | doc_id, version, title, source_type, is_demo, disclaimer, content_sha256 / section_id（`DEMO-*`）, doc_id, heading, text, position |
| audit_log | 见第 7 节 |

判定结果（pass/fail/pending_review）**不入库存储**，由规则引擎按规则集实时计算，保证口径单一。子表用自然复合主键，种子按主键 merge，重复运行不产生重复行。`bias_pct` 与 `diff_pct` 是展示用派生列，判定始终由原始浓度值重算。

判定算法（`app/rules/engine.py`）：每个数据点分 PASS/FAIL/BORDERLINE（恰在阈值）/UNKNOWN（缺值）；批级判定在“边界/缺值全算合格”和“全算不合格”两种假设下各算一次，两者一致才给出 pass/fail，不一致为 pending_review。数据不足（校准点 <6 水平、缺 QC 水平、ISR <10 例）也判 pending_review。分析批接受 = 校准 + QC；ISR 单独评价，不并入批接受。

种子数据（阶段 1）：固定种子 `20260101`；3 个项目（小分子 LC-MS/MS ×2、生物制品 LBA ×1），每项目 2–4 批；每批 6–8 校准点、LLOQ/LQC/MQC/HQC QC、30–50 样品、10–15 ISR；含通过/失败/待复核、未关闭严重偏差、ISR 失败批、逾期里程碑、未完成批。ID、单位与跨表引用一致性由测试保证。

## 3. API 契约（前缀 `/api`；除 `/api/health` 外需 Bearer 令牌；响应头 `X-Request-ID`）
| 端点 | 说明 |
|---|---|
| `GET /health` | 版本、`llm_provider`、`offline`、`simulated_data` |
| `POST /regulatory/ask` | `{question, top_k?}` → `{answer, citations[{doc_id, version, section_id, title, source_type, is_demo, quote}], confidence, confidence_basis, refused, refusal_reason?, request_id}` |
| `POST /translate` | `{text, direction: zh2en/en2zh/bilingual}` → `{paragraphs[{index, source, target?, status, term_hits[], warnings[]}], capability_note}` |
| `GET /projects`、`GET /projects/{id}`、`GET /projects/{id}/runs`、`GET /runs/{id}` | 项目列表 / 详情（含方案类信息）；分析批列表（校准 / QC / 批接受 / ISR 判定）；分析批明细（校准点、QC、ISR 逐条状态及所用规则 ID） |
| `GET /dashboard/charts?as_of=`（图表聚合：进度对比、按分析批日期累计、样本构成；口径见下）、`GET /dashboard/overview?as_of=`、`GET /dashboard/projects/{id}?as_of=` | 看板指标与风险；`as_of`（YYYY-MM-DD）缺省为今天，用于里程碑逾期 / 偏差账龄 |
| `POST /reports/draft`；`GET /reports`、`GET /reports/{id}[?version=]`、`/versions`、`/events`；`POST /reports/{id}/transition`、`/blocks/{bid}/edit`、`/blocks`、`/comments`、`/comments/{cid}/resolve`；`GET /reports/{id}/download` | 报告草稿与人工审核；download 仅 `reviewed` 可用，否则 409 |
| `GET /audit?request_id=` | 审计查询 |

错误体：`{error_code, message, request_id}`。

## 4. 知识文档清单（全部教学演示摘要，Markdown + YAML 头，`## [DEMO-xxx-nnn] 标题` 切分）
- `DEMO-ICH-M10` v0.1：方法验证与样品分析要点（校准、准确度精密度、选择性、稳定性、ISR、再分析）
- `DEMO-ChP-9012` v0.1：药典通则生物样品定量分析方法验证要点（摘要）
- `DEMO-GLP` v0.1：记录、数据完整性、质量保证、归档
- `DEMO-SOP-QC / ISR / DEV / REPORT` v1.0：模拟内部 SOP

## 5. 规则集与指标口径
规则位于 `config/rules/*.yaml`，每条含 `rule_id, value, applies_to, source_note, status`。以下为**演示配置**，非法规结论，状态均为 `demo_unconfirmed`：

| rule_id | 演示取值 | 适用条件 | 待业务确认 |
|---|---|---|---|
| CAL-ACC | 回算偏差 ±15%，LLOQ ±20%（LBA：±20%，LLOQ/ULOQ ±25%） | 按方法类型 | LBA 阈值 |
| CAL-MIN | ≥75% 且 ≥6 个浓度水平合格 | 全部 | 项目方案覆盖 |
| QC-ACC | 偏差 ±15%，LLOQ ±20%（LBA 同 CAL-ACC） | 按方法类型 | LBA 阈值 |
| QC-RUN | ≥2/3 QC 合格，且每水平 ≥50% 合格 | 全部 | 方案调整 |
| ISR-DIFF | 差值 ≤20%（LBA：30%） | 按方法类型 | 阈值 |
| ISR-RATE | ≥67% 合格 | 全部 | 比例 |
| ISR-N | ≥10 例 | 全部 | 样本量 |

判定三态：`pass / fail / pending_review`。输入缺失、恰在阈值边界、样本数不足 → `pending_review`，不自动 pass。

看板口径（阶段 5 已实现，`app/dashboard/metrics.py`；指标全部由规则引擎判定汇总，不另算判定）：
- **已判定批** = 状态为 `completed` 或 `under_review` 的分析批。`in_progress` 不计入任何通过率，单列。
- **样本数**：`samples.total` = `study_sample` 总数；另给 `valid`（flag=ok）、`blq`、`above_uloq`、`reanalysis`、`missing`，五项之和等于总数。
- **完成进度** = `completed / planned_run_count`（`under_review`、`in_progress` 都不算完成）。`planned_run_count=0` → `value=null, reason=no_planned_runs`；`planned>0` 而尚无分析批 → `0.0`（不是 null）；完成数超过计划 → 取 1.0 并标 `exceeds_plan=true`。
- **QC / 校准 / 批接受 / ISR 通过率** = `pass / (pass + fail)`，基于已判定批的**批级判定**。`pending_review` 单列，既不入分子也不入分母；分母为 0 → `value=null, reason=no_decided_runs`。ISR 另给样品级计数 `records`（总数 / 通过 / 未通过 / 待复核）。
- **汇总（overview.totals）**：先把各项目的计数相加再相除，**不是**各项目通过率的平均。
- **未关闭偏差** = `status=open`，按严重度分组；`oldest_open_days = as_of - 最早登记日`。
- **里程碑状态**（相对 `as_of`）：`delivered`（已交付，永不逾期）/ `overdue`（计划日期早于 as_of）/ `due_soon`（0–7 天内，含当天与第 7 天）/ `on_track`。
- **风险提示**（`risks[]`，含 `risk_code`、`level`、`message`、`evidence`，按严重度排序）：

看板图表（`app/dashboard/charts.py` 聚合，`web/src/charts/` 用 Recharts 只绘制；字体与脚本随包，不依赖 CDN）：
- **分析批进度对比**：按项目、按分析批**当前状态**（已完成 / 复核中 / 进行中）计数，叠加计划批数；`unscheduled = max(planned - existing, 0)`，`existing > planned` 时标“已超计划”。“所选项目分析批明细”视图：柱高 = 该批待测样品数，颜色 = 批状态，悬停显示规则判定（取自 `run_summaries`，图表层不重算）。点击柱子同步所选项目 / 分析批。
- **分析批日期累计曲线**：横轴 `run_date`，纵轴为截至该日已有的分析批累计数（阶梯线，只用真实日期，不补点）；标记的颜色与形状为各批**当前**状态。库里没有状态变更历史，所以**不是**“历史完成趋势”；不受 `as_of` 过滤（`as_of` 只影响里程碑逾期 / 偏差账龄，晚于它的日期仅以 `after_as_of` 标注）。
- **样本结果构成**：`study_sample.flag`（ok / BLQ / above_ULOQ / reanalysis / missing）计数与占比；总数为 0 时占比为空、页面显示空状态。
- 颜色语义固定：通过 / 正常 = 绿，待复核 / 复核中 = 琥珀，未通过 / 缺失 = 红，进行中 = 蓝，其余中性灰；同时配文字与符号（✓ ! ✕ …），不只靠颜色。


| risk_code | 级别 | 触发条件 |
|---|---|---|
| RISK-QC-FAIL | 高 | 已判定批的 QC 判定为未通过 |
| RISK-ISR-FAIL | 高 | 已判定批的 ISR 判定为未通过 |
| RISK-OPEN-MAJOR-DEV | 高 | 存在未关闭的重要 / 严重偏差 |
| RISK-MILESTONE-OVERDUE | 高 | 里程碑逾期 |
| RISK-MILESTONE-DUE-SOON | 中 | 里程碑 7 天内到期 |
| RISK-PENDING-REVIEW | 中 | 已判定批的批接受或 ISR 为待复核 |
| RISK-OPEN-MINOR-DEV | 低 | 存在未关闭的轻微偏差 |
| RISK-RUNS-IN-PROGRESS | 低 | 存在进行中的分析批 |
| RISK-NO-PLAN | 低 | 未设置计划分析批数 |

- **页面**：React 单页应用（`web/`）：项目看板（总览 → 项目 → 分析批 → 原始明细，路径在 URL 里）、法规与 SOP 问答、翻译与术语核对、报告审核（草稿列表 + 单份审核页）；全部数据来自 `/api`。

## 6. 检索与问答
- 切分：按章节 ID，过长章节按段再分，保留父章节 ID。
- BM25：中文字级 unigram+bigram，英文按词；不依赖 jieba。
- 向量：字符 n-gram 哈希 TF-IDF + 截断 SVD（LSA），本地拟合，不下载权重；这是词法近似而非神经语义嵌入。numpy 矩阵 + SQLite 元数据，暴力余弦。`Embedder` 接口预留本地模型（用户显式提供路径）。
- 融合：RRF，k=60，各取 top 10。
- 重排（简单词法重排，非学习型模型）：对融合结果按 `(1-w)×RRF相对分 + w×IDF加权词法匹配分` 重新排序（w=0.5；正文命中权 0.6、标题命中权 0.4，IDF 取自 BM25 语料），再丢弃最终分低于第一名 80% 的块；句子同样按 IDF 加权重叠打分，且须不低于最佳句的 50%。重排只决定“用哪些块/句作答”，**不改变 confidence 与拒答判定**。参数在 `config/eval.yaml` 的 `rerank` 段，调参记录与敏感度见 `eval/dev/README.md`；开发度量 `python scripts/dev_report.py`。
- 回答：抽取式（从命中章节按关键词重叠选句，最多 4 句），每句绑定 `[section_id]`，不生成新结论。
- 引用校验：section_id 须在本次检索结果内；quote 须为该章节原文子串；每个实质句须有引用。失败则拒答。
- 拒答（`refusal_reason`）：`out_of_scope`（证据分低于阈值）；`clause_fabrication_risk`（问题含“第 N 条/section N.N/原文/逐字/条款编号”等索要原文或条款号的表达）；`unknown_section`（点名了不存在的 DEMO 章节 ID）；`no_supporting_sentence`（检索到章节但无可支撑的句子）；`citation_validation_failed`；`empty_question`。点名存在的 `DEMO-*` 章节 ID 时直接返回该章节原句。
- confidence：**证据充分度启发式分数，非答案正确概率，未经校准**。`0.4*cov + 0.3*s_vec + 0.3*s_rrf`（cov=问题关键词被 top3 章节覆盖比例；s_vec=top1 余弦；s_rrf=top1 RRF 分相对理论最大值）。响应附 `confidence_basis`。拒答阈值写入 `config/eval.yaml`，在独立调参集（`eval/dev/regulatory_dev.json`，16 题）上确定为 **0.60**（调参集上拒答类最高 0.520、可答类最低 0.709）；初值 0.35 无法区分两类。不依评测集修改。已知局限：RRF 归一化分几乎恒为 0.9–1.0，区分力主要来自关键词覆盖度；调参集小，不保证泛化。

## 7. 审计
中间件生成 `request_id`；业务通过 `AuditContext` 登记 `section_ids / rule_ids / refused / draft_id`。表 `audit_log`：`request_id, ts, feature, operator, input_digest(≤80 字符，疑似敏感打码), input_sha256, section_ids, rule_ids, refused, draft_id, status_code, latency_ms`。不记录完整输入输出。同时按天追加 JSONL 便于云上排查。

## 8. 翻译与术语核对（阶段 3 已实现）
能力边界（如实）：规则 + 模板实现，**只翻译 `config/glossary/patterns.yaml` 登记的固定句型**（当前 14 个），不是通用机器翻译，也不是 LLM。术语表 `config/glossary/terms.yaml`（44 条）是演示配置，译法与禁用译法均未经业务审定。
- 流程：分段（按行）→ 检测语言 → 分句 → 句型匹配并输出 → 术语命中 → 核对 → 汇总警告。槽位（数字、单位、批/项目编号、质控水平缩写）原样复制，故数值和大小写不会被改写。
- 段落状态：`translated`（全部句子命中句型）/ `partial`（部分命中，未命中句子以 `[未翻译: …]` 标出）/ `unsupported`（`target=null`，只给术语对照，不生成译文）/ `checked`（核对模式）。
- 方向：`zh2en`、`en2zh`、`bilingual`（逐段自动判断语言并译为另一种，返回 `bilingual_rows`）。语言与方向不符 → `LANGUAGE_MISMATCH`，不翻译。
- 术语命中：同位置取最长匹配（“相对偏差”优先于“偏差”）；缩写按原大小写精确匹配，长度 ≥3 的缩写额外识别大小写不一致；长度 <3 的缩写（IS、QA、CV）只精确匹配，避免把普通词 is 当术语。
- 核对模式：请求带 `reference`（现成译文，按段落序号对应）时不翻译，只核对。系统自己生成的译文用同一套检查自检。
- 警告码：`UNSUPPORTED_PATTERN` `PARTIAL_TRANSLATION` `LANGUAGE_MISMATCH`（error）`TERM_MISSING` `ABBR_CASE` `SOURCE_ABBR_CASE` `FORBIDDEN_VARIANT`（error，禁用/易混译法，如把“定量下限”译成 lower limit of detection）`NUMBER_MISMATCH`（error）`AMBIGUOUS_TERM`（info，如“偏差”）`PARAGRAPH_COUNT_MISMATCH` `NO_REFERENCE_PARAGRAPH`。
- 接口：`POST /api/translate`（`{text, direction, reference?}`）；`GET /api/translate/capabilities` 列出支持的句型与术语数。审计 `rule_ids` 记录用到的句型 ID 与 `WARN:<码>`。
- `LLMProvider`（`app/llm/base.py`）：接口含 `translate_sentence` 与 `polish`；仅 `DeterministicProvider`（`is_generative=False`）。配置其它 provider 会抛 `NotImplementedError`，不会静默回退。

## 9. 报告草稿（阶段 4 已实现）
**可溯源方案**：数据库 → 规则引擎判定 → 事实（Fact）→ 模板句 → 自检 → DOCX。没有任何生成式模型参与。
- **事实**：`Fact{key, label, value, display, source_table, source_rows, rule_id}`，附录编号 F1、F2…。一个分析批产生校准/QC/样品/ISR/判定等事实；项目级事实含分析批统计与偏差统计；验证报告另有跨批 QC 的平均相对偏差与变异系数（仅描述性统计，规则集未配置精密度接受标准，不作判定）。
- **句子三类**：`fact`（业务结论，槽位只能由 Fact 填充，句末附 `[F…]` 来源标签）、`static`（说明性文字，不得含数字）、`placeholder`（缺项占位）。
- **缺失值**：事实值为空时句中显示 `[待补充: 字段]` 并记入 `missing_fields`；方案类信息（内标、前处理方法、仪器与色谱柱 / 读板仪、试剂盒及批号）来自项目数据 `project` 表，有值则作为带来源标签的事实填入，为空才占位；LBA 方法不使用内标，直接写明而不占位。绝不估算补造。种子数据中 PRJ-SM-003 的仪器信息故意留空，用来演示占位。
- **自检（`checks.py`，失败即拒绝生成，HTTP 500）**：每条 fact 句必须有存在的来源事实；句中数字（去掉编号和占位符后）必须出现在其来源事实的显示值中；static 句不得含数字。
- **章节**：目的与范围、方法概述、校准曲线、质控样品（QC）、分析批接受判定（含规则及 demo_unconfirmed 状态）、[验证: 准确度与精密度 / 样品分析: 研究样品分析概况]、已测样品再分析（ISR）、偏差、结论、复核清单、数据溯源附录、签名占位区。
- **文首**：正文第一段与页眉均为“AI 辅助草稿，需人工复核”，并标注“模拟数据”。
- **未完成批**：`in_progress` 批列入表格但不计入通过/未通过/待复核统计，并写入复核清单。
- **接口**：`POST /api/reports/draft`（`{project_id, run_ids[], kind}`，`run_ids` 为空=全部分析批）→ `{draft_id, download_url, missing_fields, fact_count, sentence_count, unsourced_sentences, rule_ids, review_status:"draft", …}`；`GET /api/reports/{draft_id}/download`。审计记录 `draft_id` 与触发的规则 ID。
- **人工复核**：`review_status` 恒为 `draft`；原型尚未提供改为 `reviewed` 的流程。
- **将来接入本地模型**：`LLMProvider.polish()` 只能润色已填好的句子，输出须通过同一套自检（数字必须属于来源事实），否则回退模板文本。当前 `DeterministicProvider.polish` 原样返回。


### 9.x 人工审核流程（阶段 7）
- **状态机**：`draft` →(start_review)→ `in_review` →(request_changes，须填意见)→ `changes_requested` →(resubmit)→ `in_review` →(approve)→ `reviewed`；其余转换一律 409。`reviewed` 为终态，之后不可修订、不可再加意见。
- **只有 `reviewed` 能导出**：`GET /api/reports/{id}/download` 在后端检查状态（并要求存在通过记录），其余状态 409 `report_not_approved`；旧草稿 409 `legacy_draft`。生成时落盘的初稿 DOCX 不提供下载；导出文件由**最终审核版本**重新渲染（`exports/{id}-v{n}-reviewed.docx`），含审核记录段落与“人工修订 / 人工新增”标记，仍保留“AI 辅助草稿 / 模拟数据”标记与“[待签署]”签署页。每次导出写入 `export` 事件。
- **修订边界**：只能改叙述句（`static` / `fact`）文字，或新增叙述句（不可加在“数据溯源附录 / 签名”）；表格、占位句、事实表、规则判定不可改；不得删除句中的“[待补充: …]”。修订后沿用生成自检（`checks.check_sentence`）：无法对应来源（无来源 / 数字不在来源事实中 / 说明性文字含数字）则标记 `flag`，阻止审核通过。每次修订生成新版本（整份内容快照，v1 = 生成原文），保留 `original_text`；`base_version` 过期返回 409。
- **通过前检查**（服务端）：无未解决问题（`issue`）；无 `flag` 块；整份报告自检通过；表格 / 占位句与 v1 一致；必填审核意见；三项确认全部勾选；`missing_fields` 逐项填写处理说明（仅记录“为何仍保留待补充”，报告中仍是占位）。
- **操作人**：审核类写操作与导出必须带非匿名 `X-Operator`，记为“操作人记录”，**不是电子签名**，也没有身份认证；审核通过不等于正式 QA 批准。审计：每次请求仍进 `audit_log`（含 `draft_id`），详细动作进 `report_review_event`。
- **旧数据**：审核功能上线前的草稿（`current_version` 为空）没有结构化内容，不可审核 / 导出，须重新生成；启动时把其中非 `draft` 的状态还原为 `draft`。PostgreSQL 上 `review_status` 列自动加宽到 VARCHAR(24)。
- **UI**：`web/src/pages/Reports.tsx`（草稿列表、状态筛选、新建）与 `ReportReview.tsx`（流程步骤、章节导航、预览与修订对比、审核 / 修订 / 意见 / 记录四个页签）。页面不做门禁：导出按钮只在 `reviewed` 时出现，真正的拦截在后端 `/download`。

## 10. 离线与部署
- 配置经 `.env` / `config/*.yaml`。测试用 `pytest-socket` 禁止外网（仅放行回环地址）；评测期间由 `app.evaluation.netguard` 禁止外网。启动不下载任何内容；`LLM_PROVIDER=deepseek` 缺 `LLM_API_KEY`、`LLM_BASE_URL` 非 https、或 `LOCAL_MODEL_PATH` 里放了 `sk-` 开头的密钥 → 启动报错，不静默回退。测试与评测一律强制 `deterministic`（`tests/conftest.py`；评测直接使用确定性实现）。
- 需要联网的步骤仅限 `pip install -r requirements-dev.txt` / 构建镜像；之后运行、测试、检索、评测、生成全部离线。
- 数据库：默认 SQLite；部署用 PostgreSQL（`DATABASE_URL=postgresql+psycopg://…`）。`tests/test_postgres.py` 用内嵌的真实 PostgreSQL 16 验证了：初始化幂等、旧库补列、API / 看板 / 报告 / 问答端到端，结果与 SQLite 一致。
- Docker Compose（`docker-compose.yml`、`Dockerfile`、`deploy/Caddyfile`）：`proxy`(Caddy, HTTPS + basic auth) → `ui` → `api` → `db`。只有 proxy 暴露 80/443；`backend` 网络为 `internal: true`（容器间互通、不能访问外网）；密钥缺失时 compose 直接报错。**已在本机 Docker Desktop 实测**：镜像构建、`up`、db/api 健康、HTTPS + basic auth（401/200）、WebSocket 经代理、内部网络隔离、5 个页面容器内渲染。本机 80/443 被占用时用 `docker-compose.local.yml` 改为 8080/8443。**未验证**：真实公网域名的自动证书、长时间运行与并发、真实浏览器目视；上线前须在目标服务器再验证。
- 认证为单一静态 Bearer 令牌 + 反向代理 basic auth；无用户体系与细粒度权限。

## 11. 阶段验证命令
| 阶段 | 命令 |
|---|---|
| 0 | `python -c "import app.main"`；`pytest`；`uvicorn app.main:app` |
| 1 | `python scripts/init_data.py`（重复运行行数不变）；`pytest tests/data tests/kb` |
| 2 | `pytest tests/regulatory`；`python scripts/dev_report.py`（调参集度量） |
| 3 | `pytest tests/translation` |
| 4 | `pytest tests/reports` |
| 5 | `pytest tests/dashboard`（后端指标与图表聚合） |
| 6 | `python scripts/run_eval.py`；`python scripts/verify.py`；`pytest tests/evaluation tests/test_postgres.py`；`docker compose config` |

## 12. 数据库演进（原型）
`create_all` 不会修改已存在的表，因此 `app/db/base.py::add_missing_columns` 在启动（`create_app`）和 `init_data.py` 时给已有表补上模型新增的**可空**列（幂等）；非空/主键新列会报错要求正式迁移。仅用于原型，正式环境应改用 Alembic。

## 13. 评测（阶段 6）
- **评测集**（`eval/cases/`，与调参集 `eval/dev/` 的问题互不相同，有测试保证）：法规问答 10 例（7 例应作答、3 例应拒答）、翻译术语 5 例、报告 4 例（来源覆盖、自然缺失、LBA 不适用内标 + 进行中批、人为制造缺值）。
- **指标、阈值、计算方法**写在 `config/eval.yaml` 的 `evaluation` 段（每个指标含 `formula`、`op`、`threshold`、`gate`）；`gate=false` 的只报告不判定。
- **评判依据**：问答看答案 / 引用 / 拒答及与库中章节原文的比对；翻译看返回结果；报告**只看落盘后重新打开的 DOCX** 和对数据库的独立查询，不调用被评对象内部的校验函数。
- **防止事后改题 / 改阈值**：`eval/cases.lock.json` 记录评测集文件与 evaluation 配置段的哈希；不一致时评测拒绝运行（退出码 2）。确需修改须 `--update-lock --reason`，原因进入锁文件历史，变更因此可审计。
- **结果**：`eval/results/latest.json`，含逐指标的值 / 分子 / 分母 / 阈值 / 是否通过 / 公式，以及逐题明细。
- **退出码**：`run_eval.py` 0 通过、1 有 gate 指标未通过、2 锁不一致或配置错误；`verify.py` 依次运行 pytest 与评测，任一失败返回非零。

## 11. 大模型接入（阶段 8，默认关闭）
- **启用**：`.env` 设置 `LLM_PROVIDER=deepseek`、`LLM_API_KEY`（SecretStr，不进 repr / 日志 / 审计 / 异常信息）、`LLM_BASE_URL`（默认 `https://api.deepseek.com`，须 https，回环地址除外）、`LLM_MODEL`（默认 `deepseek-chat`）、`LLM_TIMEOUT_S`。`/api/health` 返回 `llm_enabled`，页面提示据此显示。
- **接入点**（`app/llm/deepseek.py`，OpenAI 兼容 `chat/completions`，`temperature=0`，不跟随重定向）：
  1. 翻译：固定句型仍由规则翻译；句型之外的句子问模型，输出须满足“数字集合与原文完全一致”、单行、无 `UNTRANSLATABLE`，并照常过术语核对；通过后标 `generated_by=llm` 并追加 `LLM_TRANSLATION` 警告（须人工复核）。不通过 → 仍为“不支持”，不输出译文。
  2. 问答：检索、重排、confidence、拒答、引用校验全部先由规则完成；只有已通过校验的作答才把抽取式要点交给模型整理措辞。输出须保留全部【章节编号】标记、不得新增编号和数字；否则保留抽取式答案（`answer_mode=extractive`，`llm.fallback_reason` 给原因）。采用时 `answer_mode=llm_polished` 并同时返回未改写的 `extractive_answer`。拒答不调用模型。
  3. 报告：生成时**不**调用模型；审核页对选中的叙述句提供“AI 润色建议”（`POST /reports/{id}/blocks/{bid}/suggest`，只读）。建议须过与人工修订相同的自检（数字属于来源事实、说明性文字不含数字、保留“[待补充: …]”），采纳走原 `edit` 接口并留痕。
  4. 看板：不使用模型（指标与判定只来自规则）。
- **脱敏**（`app/llm/redact.py`）：发送前和收到后各做一次——密钥、Bearer 令牌、邮箱、身份证号、手机号、≥9 位数字串，替换为不含数字的 `[已脱敏:类型]`，命中次数记入审计 `REDACT:*`。只覆盖这些可机器识别的模式，不保证文本里没有别的敏感信息；接入真实数据前须另行评估。
- **降级**：网络错误、非 200、响应异常、校验失败一律回到确定性结果，不抛给用户；审计只记录 `LLM:deepseek:<polished|fallback|suggested>`，不记录提示词与输出。
- **部署**：默认 `docker-compose.yml` 不变——`backend` 网络 `internal: true`，api 完全离线，`LLM_PROVIDER` 固定 `deterministic`。要在容器里启用在线模型，叠加 `docker-compose.llm.yml`（受控出网，见第 14 节“受控出网”）。
- **约定变化**：此前“运行时不得调用公网 API”改为“默认离线；仅在显式配置在线 LLM 时，经 `app/llm` 向配置的地址发送已脱敏文本；测试 / 评测仍禁止联网”。

## 12. React 前端与会话鉴权（阶段 9）
- **替换**：Streamlit 前端（现存放在 `legacy/streamlit-ui/`，不再维护）由 `web/` 的 React + TypeScript + Vite 取代；后端、数据库、规则引擎、知识库、报告服务不变。页面所需接口原本已齐，只新增 `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。
- **鉴权**（`app/api/auth.py`）：`require_token` 接受 ① `Authorization: Bearer <API_TOKEN>`（脚本 / 测试 / 服务端，行为不变）或 ② 浏览器会话 Cookie `wb_session`。静态 `API_TOKEN` **不进入前端代码、构建产物或任何浏览器存储**（`tests/test_web_bundle.py` 与 `web/src/test/no-secrets.test.ts` 检查）。
  - 登录：`UI_USERS`（`name:scrypt$盐$哈希`，`scripts/make_ui_user.py` 生成）校验口令（标准库 scrypt，常量时间比较；用户不存在也做一次哈希）；同用户 + 来源 5 次失败后 5 分钟内 429。
  - 会话：随机 256 位 ID，库里只存 SHA-256（表 `ui_session`），Cookie 为 HttpOnly + SameSite=Strict（经 HTTPS / `X-Forwarded-Proto` 时加 Secure），默认 12 小时过期，登出即在服务端删除。
  - CSRF：会话请求的写操作（含登出）必须带 `X-CSRF-Token`（与会话绑定，存前端内存，刷新后由 `/api/auth/me` 重新取得）；Bearer 请求不依赖 Cookie，不需要。
  - 操作人：会话请求的审计 `operator` 与审核事件 / 导出事件的操作人取自登录名，客户端的 `X-Operator` 对会话请求无效。仍是“操作人记录”，不是电子签名。
- **托管**：若存在 `web/dist`，FastAPI 同源提供 `/assets/*` 与前端路由回落（`/api/*`、`/docs` 不被回落吞掉；路径穿越被拒），并加 CSP（`default-src 'self'`，无外部域名）、`nosniff`、`no-referrer`。开发时 Vite 把 `/api` 代理到 8000，行为一致。
- **部署**：Dockerfile 多阶段构建（node 阶段只在构建时联网）；compose 去掉 `ui` 服务，proxy → `api:8000`，api 需要 `UI_USERS`；Caddy 的 HTTPS 与 basic auth 保留作为外层（浏览器随之发出的 `Authorization: Basic` 头被 API 忽略）。
- **设计**：taste-skill 的 `achromatic-ledger` 应用风格（界面无彩色，唯一图表色只给主数据；KPI 是文字不是卡片；细线与色阶分隔，无阴影；右对齐等宽数字）+ impeccable 的 Operate 模式与 craft-floor（状态齐全、标准控件、无卡片套卡片 / 渐变 / 彩色左边条 / emoji 图标）。产品独有元素是“判定带”（校准 / QC / 批接受 / ISR 四格，符号 + 文字）。检查：`impeccable detect`（静态规则）与 taste-skill 的 `inspect.mjs --app`（真实浏览器测量，登录后逐页检查）。
- **仍然成立的限制**：图表“按分析批日期累计”不是历史完成趋势；审核通过不等于正式 QA 批准；Docker 内 api 无出网，无法使用在线大模型（第 11 节）。


## 14. 多智能体协作层（Agent，默认随大模型开关，未启用即不可用）
- **定位**：在既有接口之上增加“总控 + 数据 / 审核 / 问答 / 翻译 / 报告专家”的对话式协作（`app/agent/`、`app/api/agent.py`、页面 `/agent`）。不依赖 LangChain / LangGraph 等框架；既有接口与页面不变（已对照提交 `8b608e6` 的实现逐接口比对，仅请求号不同）。判定仍只由 `app/rules` 产生。
- **启用**：需要 `.env` 启用支持工具调用的在线模型（`LLM_PROVIDER=deepseek`）。默认 / 测试 / Docker 部署为确定性模式，所有 Agent 接口返回 409 `llm_not_enabled`，不创建任务。
- **接口**（均要求浏览器登录会话 + CSRF；静态 Bearer 令牌一律 403 `session_required`）：`POST /api/agent/messages`、`GET /api/agent/tasks`、`GET /api/agent/tasks/{id}`、`GET .../trace`（仅任务所有者）、`POST .../actions/{aid}/confirm`（请求体只有 `args_hash`）、`POST .../actions/{aid}/reject`。操作人只取自登录会话；任务绑定“用户 + 登录会话”。
- **角色与权限**：总控只有 `delegate` 伪工具（一次一个专家，专家结果作为不可信数据交回）；各专家只拿到自己白名单里的工具（`ROLE_TOOLS`），越权调用与未知工具被拒绝且留痕。共 11 个工具，其中写工具仅 `generate_report_draft` 与 `transition_report`（仅 `start_review` / `request_changes` / `resubmit`，**没有 `approve`**，Agent 不能审核通过也不能导出）。
- **写操作闸门**：用户本轮消息须明确提出该操作（关键词意图检查）→ 服务端生成“待确认动作”（展示动作说明与白名单参数，不自动执行）→ 用户通过独立接口确认（绑定会话、参数哈希、300 秒有效期）→ 服务端签发 120 秒一次性授权 → 执行并以幂等键去重。参数被改、过期、重复确认均被拒绝或幂等返回；一轮最多一个写操作。
- **最终回答**：判定字段（校准 / QC / 批接受 / ISR / 状态）、报告草稿、来源由服务端按规则结果与工具证据填入（`verdict_source=app.rules`）；模型文字须过校验（审核 / 导出声称、无证据的“已生成”、未知编号、无依据的数字、与规则冲突的判定、项目级结论、超长），不通过则替换为带来源的服务端摘要并给出原因（`answer_source=server_fallback`）。
- **发给模型的内容**：全部经 `app/llm/redact.py` 脱敏、控制字符清理与长度上限（单字段 800 字符、单次工具结果 24000 字节、历史条数有界），标注 `data_trust: untrusted`；工具结果里的指令不改变权限。脱敏是模式匹配，不保证无其他敏感信息。
- **限额**：每任务 12 轮模型调用 / 16 次工具调用 / 90 秒 / 60000 tokens，单轮最多 4 个工具调用；任务 30 分钟过期；超限即终止并说明原因。
- **逐步审计**（表 `agent_step` + 追加式 `agent-YYYYMMDD.jsonl`）：每步记录 request_id / task_id / step_id、角色、动作、操作人、白名单参数摘要、来源编号、状态、token；**内容只存 SHA-256（对脱敏后的内容），不存提示词、模型原文、工具结果正文**。`audit_log.agent_task_id` 把每个 HTTP 请求与任务串起来。写入确认前先落审计（审计不可用 → 503 `audit_unavailable`，操作不执行、待确认动作保留）；其它步骤审计失败则标记 `audit_complete=false` 并在页面提示。
- **前端**：`/agent` 页——对话、待确认卡（确认 / 拒绝、倒计时）、步骤与来源、服务端填入的判定表（不取自模型文字）、“我的任务”找回、审计记录（仅本人）、各类失败 / 超时 / 未启用状态。会话令牌不进浏览器存储。
- **真实 DeepSeek 小范围验证（2026-10-27，模拟数据，临时库）**：用 `.env` 里的真实配置跑了 4 类请求：项目汇总（总控 → 数据 / 审核专家）、SOP 问答（总控 → 问答专家）、“生成报告草稿”（总控 → 报告专家 → 待确认 → 确认 → 草稿生成）、“审核通过并导出”（无任何写入）。结果与发现：
  1. 多角色委派、专家只用自己的工具、工具调用格式、待确认 / 确认链路在真实模型上可用；越权的“审核通过并导出”没有产生写入。
  2. 真实模型暴露了 4 个校验 / 提示问题并已修复（均有回归测试）：项目名里的化合物代号被当作“未知编号”误拒；英文判定词 `pass/fail` 原先**完全不被检查**（漏洞）；同句列多个维度时只比较判定集合，把某维度改错可能漏过（改为逐维度核对）；Markdown 表格（判定在数据行、维度在表头）此前一律误判回退（现按表头列名逐格核对）；括号里的逗号会把批号与判定拆散。另外提示词里“只能提出”被模型理解成“用文字描述”而没有真正调用写工具，已明确为“直接调用写工具，系统拦截并等待确认”。
  3. 仍存在的情况：模型偶尔一次并行发出多个 `delegate`，会被严格协议拒绝并终止本轮（用户可重新提问）；模型文字未通过校验时回退为服务端摘要（内容正确但不够友好）；同一问题多次运行结果有差异。样本只有十几次调用，**不是统计意义上的成功率**。
- **验证与限制**：测试用脚本化模拟模型（`tests/agent/`，离线）覆盖编排、闸门、校验、审计、前端；**真实 DeepSeek 的工具调用在本仓库中未经验证**（含真实模型的格式漂移、延迟、校验误拒率）。“取消”只是前端停止等待，服务端不取消已开始的处理。写意图检查是关键词规则，会有误放行 / 误拒绝。回答校验是启发式规则，不是事实正确性的证明。会话历史（已脱敏）保存到任务过期。默认 Docker 部署下 api 无出网，Agent 不可用；叠加 `docker-compose.llm.yml` 才启用（受控出网，见下）。规则阈值仍是 `demo_unconfirmed`，全部数据为模拟数据。

### 14.x 受控出网（Docker，可选）
- 用法：`docker compose -f docker-compose.yml -f docker-compose.llm.yml up -d --build`，`.env` 必须有 `LLM_API_KEY`（缺失时 compose 报错）。
- 结构：`api` →（`backend` 内部网络）→ `egress`（Squid，`deploy/squid.conf`）→（`egress` 网络）→ `api.deepseek.com:443`。`backend` 仍是 `internal: true`，只有 `egress` 同时接入可出网的 `egress` 网络；api / db 不能直连外网，仍无宿主机端口。
- 代理只放行到 `api.deepseek.com:443` 的 HTTPS CONNECT 隧道；其它主机 / 端口 / 明文 HTTP / 非 CONNECT 一律 403；TLS 端到端，代理看不到内容、拿不到密钥；不缓存；只读文件系统、丢弃全部 capability；访问日志只含目标与结果。
- 换模型服务须同时改 `deploy/squid.conf` 的域名与 `LLM_BASE_URL`（有测试检查两处一致）。
- 验证：`tests/test_deploy_egress.py`（静态：内部网络未放开、只有 egress 在可出网网络、代理只放行一个域名；对放开网络 / 通配域名的变异实验均被抓到）。实机（Docker Desktop）：从内部网络的客户端容器测试，`api.deepseek.com:443` → 200，`example.com`、`:80`、仿冒域名、`127.0.0.1`、`db:5432`、明文 GET 全部 403，客户端直连外网失败，经隧道的 TLS 到达真实 DeepSeek（证书校验通过）。
- 未验证 / 限制：公网云服务器上的行为；DNS 层面的绕过（Squid 按域名匹配，客户端若自己解析 IP 再 CONNECT 到 IP 也被拒，因为只允许域名）；api 容器本身被攻破时仍可借代理向该一个主机发请求（这是启用在线模型的固有范围）；放行域名下的内容仍是已脱敏文本（脱敏只是模式匹配）。
