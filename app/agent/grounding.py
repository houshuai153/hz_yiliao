"""最终回答的事实核对：证据收集、模型文字校验、服务端兜底摘要。

原则（CLAUDE.md：判定只由 app/rules 计算）：
- 证据只来自**工具结果**（它们的判定来自 app.rules）。服务端把这些判定原样填进最终响应的 evidence 区块，
  模型文字不参与判定字段。
- 模型文字（answer）必须通过校验：引用的项目 / 分析批 / 报告 / 知识编号都要有工具结果；数字必须出现在工具结果或用户消息里；
  含“通过 / 未通过 / 待复核”等判定词的分句必须锚定到具体分析批，且与规则结果一致；不得声称已审核通过 / 已批准 / 已导出；
  声称“已生成草稿”须有生成工具的成功结果。任一不满足 → 整段拒绝，回退到服务端按证据生成的摘要。
- 这些是确定性的启发式规则：宁可误拒（回退到摘要）也不放行未经证实的结论。
"""
from __future__ import annotations

import json
import re
from typing import Any

ID_RE = re.compile(r"PRJ-[A-Z0-9]+(?:-[A-Z0-9]+)+|DRAFT-(?:VAL|SMP)-\d{14}-[0-9a-f]{6}|DEMO-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}")
SHORT_RUN_RE = re.compile(r"(?<![A-Za-z0-9])R\d{2}(?![0-9])")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
MARK_RE = re.compile(r"【[^】]*】")
CLAUSE_RE = re.compile(r"[。！？!?\n；;，,]")
APPROVAL_CLAIM_RE = re.compile(r"(?:已经?|均已|现已|成功)(?:经)?(?:审核通过|批准|签署|导出|完成审核)")
GENERATED_CLAIM_RE = re.compile(r"(?:已经?|成功)(?:为您)?(?:生成|创建|准备好?)[^。；;\n]{0,14}(?:草稿|报告)")
VERDICT_WORDS = (("未通过", "fail"), ("不通过", "fail"), ("不合格", "fail"), ("失败", "fail"),
                 ("待复核", "pending_review"), ("需复核", "pending_review"), ("通过", "pass"), ("合格", "pass"))
DIMS = (("calibration", "calibration"), ("accepted", "accepted"), ("校准", "calibration"), ("质控", "qc"), ("QC", "qc"), ("再分析", "isr"), ("ISR", "isr"),
        ("批接受", "accepted"), ("接受", "accepted"))
EN_VERDICT_RE = re.compile(r"(?<![A-Za-z_])(pending_review|fail|pass)(?![A-Za-z_])", re.I)
EN_CLASS = {"pending_review": "pending_review", "fail": "fail", "pass": "pass"}
VERDICT_ZH = {"pass": "通过", "fail": "未通过", "pending_review": "待复核"}
RUN_STATUS_ZH = {"completed": "已完成", "under_review": "复核中", "in_progress": "进行中"}
REVIEW_ZH = {"draft": "待审核", "in_review": "审核中", "changes_requested": "退回修改", "reviewed": "审核通过"}
MAX_ANSWER = 2000
MAX_NUMBERS = 3000


def new_evidence() -> dict:
    return {"runs": {}, "projects": {}, "reports": {}, "ids": [], "numbers": [], "generated": [],
            "touched_runs": [], "touched_projects": [], "touched_reports": []}


def begin_turn(ev: dict) -> None:
    """新一轮用户消息：证据保留（供多轮引用），“本轮涉及”清零（兜底摘要只列本轮的）。"""
    ev["touched_runs"], ev["touched_projects"], ev["touched_reports"] = [], [], []


def _norm_num(n: str) -> str:
    return n.rstrip("0").rstrip(".") if "." in n else str(int(n))


def _numbers_in(obj: Any) -> set[str]:
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, bool):
            return
        elif isinstance(v, float):
            out.update({_norm_num(f"{v:.6f}"), _norm_num(f"{v:.2f}"), _norm_num(f"{v:.1f}")})
            if 0 < v < 1:                                   # 比率常以百分数呈现
                out.update({_norm_num(f"{v * 100:.1f}"), _norm_num(f"{v * 100:.2f}"), _norm_num(f"{round(v * 100)}")})
        elif isinstance(v, int):
            out.add(str(v))
        elif isinstance(v, str):
            out.update(_norm_num(n) for n in NUM_RE.findall(ID_RE.sub("", v)))
    walk(obj)
    return out


def _ids_in(obj: Any) -> list[str]:
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            out.extend(ID_RE.findall(v))
    walk(obj)
    return out


def _add(lst: list, item: Any, cap: int = 500) -> None:
    if item not in lst and len(lst) < cap:
        lst.append(item)


def _touch_run(ev: dict, rid: str, status: str, cal: str, qc: str, acc: str, isr: str) -> None:
    ev["runs"][rid] = {"status": status, "calibration": cal, "qc": qc, "accepted": acc, "isr": isr}
    _add(ev["touched_runs"], rid)


def absorb(ev: dict, tool: str, result: dict) -> None:
    """把一次成功的工具结果并入证据。判定值原样取自结果（规则引擎输出），不做任何计算。"""
    if not result.get("ok"):
        return
    data = result.get("data", {})
    for s in result.get("sources", []):
        _add(ev["ids"], s["id"])
    for i in _ids_in(data):                                  # 工具结果文本里出现的编号（如项目名中的化合物代号）也是工具提供的事实
        _add(ev["ids"], i)
    nums = sorted(_numbers_in(data))
    ev["numbers"] = sorted(set(ev["numbers"]) | set(nums))[:MAX_NUMBERS]
    if tool == "list_project_runs":
        for r in data["runs"]:
            _touch_run(ev, r["id"], r["status"], r["calibration"], r["qc"], r["accepted"], r["isr"])
    elif tool == "get_run_detail":
        a = data["assessment"]
        _touch_run(ev, data["run"]["id"], data["run"]["status"], a["calibration"]["verdict"], a["qc"]["verdict"],
                   a["accepted"], a["isr"]["verdict"])
    elif tool == "dashboard_project":
        for r in data["run_summaries"]:
            _touch_run(ev, r["id"], r["status"], r["calibration"], r["qc"], r["accepted"], r["isr"])
        ev["projects"][data["project"]["id"]] = {"title": data["project"]["title"]}
        _add(ev["touched_projects"], data["project"]["id"])
    elif tool == "get_project":
        p = data["project"]
        ev["projects"][p["id"]] = {"title": p["title"]}
        _add(ev["touched_projects"], p["id"])
    elif tool == "list_projects":
        for p in data["projects"]:
            ev["projects"][p["id"]] = {"title": p["title"]}
            _add(ev["touched_projects"], p["id"])
    elif tool == "generate_report_draft":
        ev["reports"][data["draft_id"]] = {"review_status": data["review_status"], "project_id": data["project_id"],
                                           "review_url": data["review_url"]}
        _add(ev["generated"], data["draft_id"])
        _add(ev["touched_reports"], data["draft_id"])
    elif tool == "get_report":
        r = data["report"]
        ev["reports"][r["draft_id"]] = {"review_status": r["review_status"], "project_id": r["project_id"],
                                        "review_url": data["review_url"]}
        _add(ev["touched_reports"], r["draft_id"])
    elif tool == "list_reports":
        for r in data["reports"]:
            ev["reports"][r["draft_id"]] = {"review_status": r["review_status"], "project_id": r["project_id"],
                                            "review_url": r["review_url"]}
            _add(ev["touched_reports"], r["draft_id"])


# ---------- 校验模型文字 ----------
NL = chr(10)
PAREN_RE = re.compile(r"[（(][^（）()]*[）)]")


def _clauses(answer: str) -> list[str]:
    """分句。括号里的逗号 / 分号不切开（“R03（2026-09-08，41 份样品）：校准 待复核”仍是同一句，批号和判定不会被拆散）。"""
    masked = PAREN_RE.sub(lambda m: CLAUSE_RE.sub(" ", m.group(0)), answer)
    return CLAUSE_RE.split(masked)


def _verdict_classes(clause: str) -> set[str]:
    t = clause.replace("通过率", "").replace("合格率", "").replace("审核通过", "")   # “审核通过”是动作名，不是判定
    out: set[str] = set()
    for m in EN_VERDICT_RE.findall(t):                        # 规则引擎原文 pass / fail / pending_review 同样要核对
        out.add(EN_CLASS[m.lower()])
    t = EN_VERDICT_RE.sub("", t)
    for word, cls in VERDICT_WORDS:                           # 长词在前，避免“未通过”被“通过”重复匹配
        if word in t:
            out.add(cls)
            t = t.replace(word, "")
    return out


DIM_RE = re.compile("|".join(w for w, _ in sorted(DIMS, key=lambda x: -len(x[0]))), re.I)
DIM_OF = {w.upper(): d for w, d in DIMS}


def _dim_segments(clause: str) -> list[tuple[str | None, str]]:
    """把一句拆成 (维度, 该维度名之后到下一个维度名之前的文字)；第一个维度名之前的文字归到 None（按批接受判定）。"""
    marks = list(DIM_RE.finditer(clause))
    if not marks:
        return [(None, clause)]
    out: list[tuple[str | None, str]] = [(None, clause[:marks[0].start()])]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(clause)
        out.append((DIM_OF[m.group(0).upper()], clause[m.end():end]))
    return out


def _runs_in(clause: str, ev: dict) -> tuple[list[str], list[str]]:
    full = [i for i in ID_RE.findall(clause) if i in ev["runs"]]
    short = []
    for token in SHORT_RUN_RE.findall(clause):
        short += [rid for rid in ev["runs"] if rid.endswith(f"-{token}")]
    others = [i for i in ID_RE.findall(clause) if i not in ev["runs"]]
    return sorted(set(full + short)), others


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _check_tables(answer: str, ev: dict, problems: list[str]) -> str:
    """Markdown 表格：表头列名 → 维度，数据行里的判定词只与该列维度的规则结果比。返回去掉表格后的正文（其余按句校验）。"""
    rest: list[str] = []
    header: list[str] | None = None
    for line in answer.split(NL):
        if not line.lstrip().startswith("|"):
            header = None
            rest.append(line)
            continue
        cells = _split_row(line)
        if header is None:
            header = cells                                    # 表格的第一行是表头
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue                                          # 分隔行
        row_runs = sorted({r for c in cells for r in _runs_in(c, ev)[0]})
        for col, cell in enumerate(cells):
            got = _verdict_classes(cell)
            if not got:
                continue
            if not row_runs:
                problems.append("unanchored_verdict")
                continue
            m = DIM_RE.search(header[col]) if col < len(header) else None
            dim = DIM_OF[m.group(0).upper()] if m else "accepted"
            for rid in row_runs:
                if not got <= {ev["runs"][rid][dim]}:
                    problems.append(f"verdict_conflict:{rid}")
    return NL.join(rest)


def validate_answer(answer: str, ev: dict, user_message: str = "") -> list[str]:
    """返回问题代码列表；空列表 = 通过。"""
    problems: list[str] = []
    if len(answer) > MAX_ANSWER:
        problems.append("too_long")
    if APPROVAL_CLAIM_RE.search(answer):
        problems.append("approval_claim")
    if GENERATED_CLAIM_RE.search(answer) and not ev["generated"]:
        problems.append("generated_claim_without_evidence")
    known_ids = set(ev["ids"]) | set(ID_RE.findall(user_message))
    for i in sorted(set(ID_RE.findall(answer))):
        if i not in known_ids:
            problems.append(f"unknown_id:{i}")
    allowed = set(ev["numbers"]) | {_norm_num(n) for n in NUM_RE.findall(ID_RE.sub("", user_message))}
    body = SHORT_RUN_RE.sub("", MARK_RE.sub("", ID_RE.sub("", answer)))      # 编号（含 R02 这样的简写）不当作数字
    for n in sorted({_norm_num(x) for x in NUM_RE.findall(body)}):
        if n not in allowed:
            problems.append(f"unsupported_number:{n}")
    body_text = _check_tables(answer, ev, problems)
    for line in body_text.split(NL):
        inherited: list[str] = []                             # 同一行里前面句子出现的批号：只借给以维度名开头的分句（“R02：校准 通过；qc 未通过”）
        for clause in _clauses(line):
            runs, others = _runs_in(clause, ev)
            if runs:
                inherited = runs
            classes = _verdict_classes(clause)
            if not classes:
                continue
            m = DIM_RE.match(clause.strip().lstrip("-*• 　"))
            if not runs and inherited and m:
                runs = inherited
            if not runs:
                problems.append("unanchored_verdict" if not others else "project_level_verdict")
                continue
            for rid in runs:
                rec = ev["runs"][rid]
                for dim, seg in _dim_segments(clause):      # 逐维度核对：每个维度名后面的判定词只与该维度的规则结果比
                    got = _verdict_classes(seg)
                    if got and not got <= {rec[dim or "accepted"]}:
                        problems.append(f"verdict_conflict:{rid}")
    return list(dict.fromkeys(problems))


# ---------- 服务端摘要 / 证据区块 ----------
def evidence_view(ev: dict) -> dict:
    """最终响应里的“证据”：判定字段由服务端从规则结果填入。"""
    return {
        "verdict_source": "app.rules", "rule_status": "demo_unconfirmed",
        "projects": [{"project_id": p, **ev["projects"][p]} for p in ev["touched_projects"] if p in ev["projects"]],
        "runs": [{"run_id": r, **ev["runs"][r]} for r in ev["touched_runs"] if r in ev["runs"]],
        "reports": [{"draft_id": d, **ev["reports"][d]} for d in ev["touched_reports"] if d in ev["reports"]],
    }


def server_summary(ev: dict, *, note: str | None = None) -> str:
    lines: list[str] = [note] if note else []
    view = evidence_view(ev)
    for p in view["projects"]:
        lines.append(f"项目 {p['project_id']}：{p['title']}")
    for r in view["runs"]:
        lines.append(f"分析批 {r['run_id']}（{RUN_STATUS_ZH.get(r['status'], r['status'])}）："
                     f"校准 {VERDICT_ZH[r['calibration']]}；QC {VERDICT_ZH[r['qc']]}；"
                     f"批接受 {VERDICT_ZH[r['accepted']]}；ISR {VERDICT_ZH[r['isr']]}")
    for d in view["reports"]:
        lines.append(f"报告草稿 {d['draft_id']}：{REVIEW_ZH.get(d['review_status'], d['review_status'])}；"
                     f"审核入口 {d['review_url']}（审核通过须由人在审核页完成）")
    if not (view["projects"] or view["runs"] or view["reports"]):
        lines.append("本轮没有获得可汇总的工具结果。")
    return "\n".join(lines)


def dumps(ev: dict) -> str:
    return json.dumps(ev, ensure_ascii=False)
