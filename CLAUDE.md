# CLAUDE.md

生物分析业务 AI 效能平台（工程原型）。**全部业务数据为模拟数据；翻译/问答/报告默认为规则与模板实现（非 LLM）；大模型（DeepSeek）仅在 `.env` 显式启用后用于翻译兜底、问答措辞、报告修订建议，判定与指标仍只由规则计算。** 修改前先读 `SPEC.md` 对应章节。

## 结构
- `app/` 后端：`api/` 薄路由；`audit/` 审计中间件与表；`db/` 引擎与基类；`settings.py` 配置。已有 `kb/ regulatory/ translation/ rules/ llm/ reports/ dashboard/ agent/`。
- `app/agent/` 多智能体层（SPEC 第 14 节）：判定仍只来自 `app/rules`；写工具只经“用户独立确认”闸门；没有 `approve`；发给模型的内容必须过 `app/llm/redact.py`；审计只存哈希；Agent 接口只认浏览器会话 + CSRF，操作人取自 `operator_name(request)`。测试只用脚本化模拟模型，不得写成“真实 DeepSeek 已验证”。
- `web/` React + TypeScript 前端（Vite）：只经同源 `/api` 调后端，不直连数据库、不在页面里计算指标或判定；**浏览器里不得出现 `API_TOKEN`**（登录后只持有 HttpOnly 会话 Cookie + CSRF 令牌，操作人 = 登录名）。`legacy/streamlit-ui/` 是已被替换的旧 Streamlit 前端，不再维护、不在测试和镜像中。
- `config/` 规则集（`rules/`）、术语表与翻译句型（`glossary/terms.yaml`、`patterns.yaml`）、评测配置（`eval.yaml`）——**业务术语与阈值在这里，不在代码里**。
- `knowledge/` 教学演示摘要（`DEMO-*` 章节 ID，不得写入真实法规原文或真实条款编号）。
- `tests/` 按模块分目录；`scripts/` 初始化、评测、`verify.py`。

## 命令（Windows 下用 `.venv/Scripts/python.exe`，Linux/Mac 用 `.venv/bin/python`）
- 测试：`python -m pytest`
- API：`python -m uvicorn app.main:app --reload`
- 前端：`cd web && npm ci`；开发 `npm run dev`（http://localhost:5173，`/api` 代理到 8000）；`npm test`（vitest）；`npm run build`（产物 `web/dist` 由 FastAPI 同源托管，访问 http://127.0.0.1:8000/）
- 浏览器登录用户：`python scripts/make_ui_user.py <用户名>` 生成一行写入 `.env` 的 `UI_USERS`（口令只显示一次）
- 评测：`python scripts/run_eval.py`；统一验收（pytest + 评测 + 前端类型检查 / vitest / 构建）：`python scripts/verify.py`（`--skip-web` 可跳过前端）
- 部署演示：`docker compose config`（详见 README 第 5 节；本地开发不需要 Docker）

## 约定
- 测试与评测禁止联网（pytest-socket；`tests/conftest.py` 强制 `LLM_PROVIDER=deterministic`）；运行时默认离线、不得下载模型。仅当 `.env` 显式配置在线 LLM 时，才允许经 `app/llm` 向 `LLM_BASE_URL` 发送**已脱敏**文本；`LLM_API_KEY` 只放 `.env`，不得写进其它变量、日志、审计或文档。模型输出必须过数字 / 编号 / 引用校验，失败即回退确定性结果。
- 判定（pass/fail/pending_review）只由 `app/rules` 计算，看板与报告不得自行重算。
- 规则阈值是演示配置，状态 `demo_unconfirmed`，不得在文案中称为法规要求。
- 报告中的业务结论必须附数据来源（Fact 标签）；缺值写 `[待补充: 字段]`，不得补造；报告构建器不得自行重算判定，只消费 `app/rules`。
- **不得为通过评测修改答案或阈值**：评测集与阈值受 `eval/cases.lock.json` 哈希锁保护；确需修改须 `run_eval.py --update-lock --reason`（原因入历史），并向使用者说明。评测代码本身的 bug 可以修，但要如实报告。
- 模拟字段不用 `string`/`example`/`TODO` 等占位值。
- `.env` 不提交，不写真实密钥或真实受试者信息。
- 每阶段只做当前阶段内容；完成后跑验证、如实报告、等待确认。
