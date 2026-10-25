# 生物分析业务 AI 效能平台（工程原型）

> **全部业务数据为模拟数据**；知识库是**教学演示摘要**，不是监管机构原文，不具合规效力。
> 翻译、问答、报告默认由本地**规则 + 模板 + 检索**实现，**不具备 LLM 能力**。可在 `.env` 设置 `LLM_PROVIDER=deepseek` 与 `LLM_API_KEY` 显式启用 DeepSeek（翻译兜底、问答措辞整理、报告修订建议；发送前脱敏，输出须过校验并人工复核；详见 SPEC 第 11 节）。
> 规则阈值是演示配置（状态 `demo_unconfirmed`），待业务确认。输出需人工复核。

设计与口径见 [SPEC.md](SPEC.md)；开发约定见 [CLAUDE.md](CLAUDE.md)。

## 功能
| 模块 | 说明 | 入口 |
|---|---|---|
| 法规与 SOP 问答 | BM25 + 本地向量检索 → RRF 融合 → 简单重排 → 抽取式作答；引用校验，证据不足 / 索要原文条款时拒答 | `POST /api/regulatory/ask` |
| 翻译与术语核对 | 仅翻译登记的固定句型；其余只给术语对照；可核对现成译文的术语 / 缩写 / 数字 | `POST /api/translate` |
| 报告草稿与审核 | 生成草稿 → 在线预览、人工审核（修订、意见、版本）→ 审核通过后才能导出 DOCX；每条结论附事实来源标签，缺值显示 `[待补充: 字段]` | `POST /api/reports/draft`、`/api/reports/{id}/transition`、`/download`（仅审核通过） |
| 项目看板 | 样本数、分析批进度、QC / ISR 结果、未关闭偏差、报告里程碑、风险提示 | `GET /api/dashboard/*`、Streamlit 页面 |
| 审计 | 每次 API 调用写入审计表与按天 JSONL | `GET /api/audit` |

## 1. 环境准备（全新环境，需联网，仅此一步）
要求 Python 3.11+（按 3.12 验证）。下面以 Windows 的 Git Bash 为例；Linux / macOS 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`。

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
cp .env.example .env            # 本地默认值即可；.env 不要提交
```
如果你的 pip 镜像拒绝下载个别包（遇到过 403），加 `-i https://pypi.org/simple` 重试。
只需运行、不需要测试时可用 `requirements.txt`。**安装完成后，运行、测试、检索、评测、报告生成全部离线。**

## 2. 初始化数据与索引（离线，可重复运行）
```bash
.venv/Scripts/python.exe scripts/init_data.py            # 建表 + 模拟种子数据 + 知识库导入 + 本地索引
.venv/Scripts/python.exe scripts/init_data.py --reset    # 先清空种子业务表再重建
```
重复运行不会产生重复数据。已有旧库会自动补上新增的可空列。

## 3. 启动
```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000        # API 文档: http://127.0.0.1:8000/docs
```
前端（React + TypeScript，`web/`）：
```bash
cd web && npm ci                       # 首次：安装依赖（需联网一次）
npm run build                          # 产物 web/dist，由上面的 uvicorn 同源托管 -> http://127.0.0.1:8000/
npm run dev                            # 或开发模式：http://localhost:5173（/api 代理到 8000，热更新）
```
浏览器登录用户（口令哈希写进 `.env` 的 `UI_USERS`，口令只显示一次）：
```bash
.venv/Scripts/python.exe scripts/make_ui_user.py reviewer      # 输出 reviewer:scrypt$…，加到 .env: UI_USERS='…'（多个用户用分号隔开）
```
鉴权：脚本 / 测试用 `Authorization: Bearer <API_TOKEN>`（默认 `local-dev-token-change-me`，仅限本地）；**浏览器不持有该令牌**，登录后只有 HttpOnly + SameSite=Strict 的会话 Cookie（会话存库、可撤销、12 小时过期）和写操作必带的 CSRF 令牌；审核与审计里的“操作人”取自登录名，不是客户端声明。除 `GET /api/health` 与登录接口外，接口都需要令牌或会话。

示例（注意：Windows 的 Git Bash 直接在命令行里写中文会按 GBK 传参导致 400，建议用文件或 Python 传 UTF-8 请求体）：
```bash
curl -s -X POST http://127.0.0.1:8000/api/regulatory/ask -H "Authorization: Bearer local-dev-token-change-me" \
  -H "Content-Type: application/json" --data-binary @question.json     # question.json: {"question": "偏差在什么条件下可以关闭"}
```

## 4. 测试与评测
```bash
.venv/Scripts/python.exe -m pytest                      # 全部测试（禁止外网；含 PostgreSQL 兼容性测试）
.venv/Scripts/python.exe scripts/run_eval.py            # 固定评测集 -> eval/results/latest.json
.venv/Scripts/python.exe scripts/verify.py              # 统一验收：依次 pytest + 评测，任一失败返回非零退出码
```
- 评测集在 `eval/cases/`（法规问答 10 例其中 3 例应拒答、翻译术语 5 例、报告来源覆盖与缺失值 4 例）；
  指标定义、计算方法和通过阈值在 `config/eval.yaml` 的 `evaluation` 段。
- 评测集与阈值的哈希锁在 `eval/cases.lock.json`：被改动时评测拒绝运行（退出码 2）。**不得为通过验收而修改答案或阈值**；
  确需修改须 `scripts/run_eval.py --update-lock --reason "原因"`，原因会记入锁文件的历史。
- 评测在临时目录重建数据，不碰 `data/`，且评测期间禁止访问外部网络。
- `scripts/dev_report.py` 是开发用的问答质量度量（调参集），不属于验收。

## 5. 部署演示（Docker Compose，云服务器）
Docker 仅用于部署演示，**不是**本地开发 / 测试 / 评测的前置条件。

```
internet ──> proxy (Caddy: HTTPS + basic auth) ──> api (FastAPI：同源托管 React 前端 + /api，会话登录) ──> db (PostgreSQL)
```
只有 `proxy` 暴露 80/443；`api`、`ui`、`db` 在不可访问外网的内部网络里，没有宿主机端口。

```bash
cp .env.example .env     # 然后填写：POSTGRES_PASSWORD、API_TOKEN、BASIC_AUTH_USER、BASIC_AUTH_HASH、DOMAIN
docker run --rm caddy:2-alpine caddy hash-password          # 生成 BASIC_AUTH_HASH（写入 .env 时用单引号包住，因含 $）
docker compose config                                       # 先静态校验；缺少必填密钥会直接报错
docker compose up -d --build
```
- 构建镜像与拉取基础镜像需要联网（准备步骤）；容器运行后不需要外网。
- `DOMAIN` 设为解析到该服务器的公网域名时，Caddy 自动申请证书（需 80/443 对公网开放）；`localhost` 时用本地自签证书。
- `API` 容器启动时会运行幂等的 `init_data.py`（种子数据全部是模拟数据）。
- **本机端口被占用时**（80/443 常被占用）：加上覆盖文件改用 8080/8443：
  `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`，然后访问 `https://localhost:8443`（本地自签证书，浏览器会提示不受信任，属正常）。
- **验证状态（如实）**：已在本机 Docker Desktop（Linux 容器）上实测：镜像构建成功；`up` 后 db/api 健康检查通过；
  HTTPS + basic auth 生效（无凭据 / 错误口令 401，正确口令 200，带 HSTS 等响应头）；Streamlit 的 WebSocket 握手经 Caddy 代理成功；
  数据写入 PostgreSQL；api 容器在 `--network none` 下也能初始化并服务；内部网络内的容器连不上外网，宿主机连不上 api/ui/db 端口；
  在 ui 容器内渲染全部 5 个页面无异常。
  **尚未验证**：在真实公网域名上由 Caddy 自动签发证书（需要服务器 80/443 对公网开放）；长时间运行与并发；真实浏览器里目视检查版式。
  上线到云服务器前，请在目标服务器上再做一次同样的检查。

## 目录
```
app/        后端：api/ audit/ db/ kb/ regulatory/ translation/ rules/ llm/ reports/ dashboard/ evaluation/
web/        React + TypeScript 前端（Vite；构建产物由 FastAPI 同源托管）
legacy/     已被替换的旧 Streamlit 前端（不再维护）
config/     规则集、术语表与句型、评测配置（业务术语与阈值在这里，不在代码里）
knowledge/  教学演示摘要（DEMO-* 章节 ID）
seed/       模拟种子数据生成器（固定随机种子）
eval/       评测集、调参集、结果、锁文件
scripts/    init_data.py run_eval.py verify.py dev_report.py
tests/      按模块分目录
deploy/     Caddyfile
```

## 已知局限（摘要，详见 SPEC）
- 翻译只覆盖 14 个固定句型，不是通用机器翻译；术语表是自编演示版，未经业务审定。
- 向量检索是字符 n-gram 哈希 + LSA 的词法近似，不是神经语义嵌入；问答答案为抽取式，相关性有限。
- 报告句式机械；方案类信息缺失时占位；没有“人工复核通过”的流程。
- 没有用户体系 / 细粒度权限；API 用单一静态令牌。
