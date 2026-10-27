"""编排的安全与边界：非法模型输出、上限终止、最终回答校验与回退、发给模型的内容已脱敏、故障不泄露。"""
import itertools
import json

import pytest

from app.agent import grounding, schema
from app.agent.orchestrator import AgentLimits
from app.db.models import AgentMessage, Project
from tests.agent.agent_helpers import (
    FakeClient, calls, count_rows, delegate, find_draft_id, text,
)


def tool_steps(snap):
    return [s for s in snap["steps"] if s["kind"] == "tool_call"]


# ---------- 非法的模型响应：整轮拒绝，不执行任何调用 ----------
def raw_calls(tool_calls):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}}]}


def tc(cid="c1", name="list_projects", args="{}", typ="function"):
    return {"id": cid, "type": typ, "function": {"name": name, "arguments": args}}


BAD_RESPONSES = {
    "args-not-json": raw_calls([tc(args="{not json")]),
    "args-not-object": raw_calls([tc(args="[1, 2]")]),
    "args-not-string": raw_calls([tc(args={"a": 1})]),
    "duplicate-ids": raw_calls([tc("x"), tc("x")]),
    "bad-id-chars": raw_calls([tc("a b;c")]),
    "empty-id": raw_calls([tc("")]),
    "too-many-calls": raw_calls([tc(f"c{i}") for i in range(5)]),
    "bad-tool-name": raw_calls([tc(name="List Projects")]),
    "uppercase-name": raw_calls([tc(name="LIST_PROJECTS")]),
    "wrong-type": raw_calls([tc(typ="code")]),
    "call-not-dict": raw_calls(["list_projects"]),
    "tool_calls-not-list": raw_calls({"id": "c1"}),
    "role-user": {"choices": [{"message": {"role": "user", "content": "hi"}}]},
    "empty-turn": {"choices": [{"message": {"role": "assistant", "content": "  "}}]},
    "no-choices": {"choices": []},
    "not-an-object": ["x"],
    "content-not-str": {"choices": [{"message": {"role": "assistant", "content": 5}}]},
    "args-too-long": raw_calls([tc(args=json.dumps({"q": "x" * 9000}))]),
}


@pytest.mark.parametrize("name", sorted(BAD_RESPONSES))
def test_protocol_violations_are_rejected_whole_and_nothing_runs(web_app, alice, agent, name):
    client = agent([delegate("d1", "data", "查项目"), BAD_RESPONSES[name]])
    snap = alice.say("看项目").json()
    assert snap["status"] == "failed" and snap["error_code"] == "invalid_model_output"
    assert tool_steps(snap) == [] and snap["pending_actions"] == []                         # 非法回合里的调用一个都没执行
    assert len(client.calls) == 2 and "invalid_model_output" in json.dumps(snap["final"]["fallback_reason"])
    assert alice.say("继续", snap["task_id"]).json()["detail"]["error_code"] == "task_closed"   # 失败的任务不再接受消息


@pytest.mark.parametrize("response", [
    calls(("d1", "list_projects", {})),                                                      # 总控不能调业务工具
    calls(("d1", "delegate", {"role": "hacker", "instruction": "x"})),                       # 角色不存在
    calls(("d1", "delegate", {"role": "data"})),                                             # 缺 instruction
    calls(("d1", "delegate", {"role": "data", "instruction": "x", "operator": "root"})),     # 夹带字段
    calls(("d1", "delegate", {"role": "data", "instruction": "x"}), ("d2", "delegate", {"role": "qa", "instruction": "y"})),
])
def test_orchestrator_may_only_make_one_valid_delegate_call(alice, agent, response):
    client = agent([response])
    snap = alice.say("你好").json()
    assert snap["status"] == "failed" and snap["error_code"] == "invalid_model_output" and len(client.calls) == 1


# ---------- 上限：明确终止并给出服务端摘要 ----------
def assert_terminated(alice, snap, reason):
    assert snap["status"] == "terminated" and snap["termination_reason"] == reason
    assert snap["final"]["answer_source"] == "server_summary" and "已因达到上限" in snap["final"]["answer"]
    assert alice.say("继续", snap["task_id"]).json()["detail"]["error_code"] == "task_closed"


def test_round_limit(alice, agent):
    client = agent([delegate("d1", "data", "x"), calls(("t1", "list_projects", {})), calls(("t2", "list_projects", {}))],
                   agent_limits=AgentLimits(max_rounds=3))
    snap = alice.say("看项目").json()
    assert_terminated(alice, snap, "max_rounds") and len(client.calls) == 3
    assert snap["usage"]["rounds"] == 3 and snap["usage"]["limits"]["max_rounds"] == 3


def test_tool_call_limit(alice, agent):
    client = agent([delegate("d1", "data", "x"), calls(("a", "list_projects", {}), ("b", "list_projects", {}), ("c", "list_projects", {}))],
                   agent_limits=AgentLimits(max_tool_calls=2))
    snap = alice.say("看项目").json()
    assert_terminated(alice, snap, "max_tool_calls") and snap["usage"]["tool_calls"] == 2 and len(tool_steps(snap)) == 2


def test_token_budget(alice, agent):
    client = agent([delegate("d1", "data", "x", tokens=120), calls(("a", "list_projects", {}))],
                   agent_limits=AgentLimits(max_tokens=100))
    snap = alice.say("看项目").json()
    assert_terminated(alice, snap, "token_budget") and len(client.calls) == 1 and snap["usage"]["tokens_used"] == 120


def test_total_time_limit(alice, agent):
    ticks = itertools.count(0, 50)
    client = agent([delegate("d1", "data", "x"), calls(("a", "list_projects", {}))],
                   agent_limits=AgentLimits(max_seconds=90), agent_monotonic=lambda: next(ticks))
    snap = alice.say("看项目").json()
    assert_terminated(alice, snap, "timeout") and len(client.calls) == 1


def test_missing_usage_is_estimated_not_ignored(alice, agent):
    r = text("你好")
    del r["usage"]
    agent([r])
    assert alice.say("你好").json()["usage"]["tokens_used"] > 0


def test_oversized_tool_result_is_an_error_the_model_sees(alice, agent, monkeypatch):
    monkeypatch.setattr(schema, "MAX_RESULT_BYTES", 200)
    client = agent([delegate("d1", "data", "x"), calls(("a", "list_projects", {})), text("结果过大。"), text("没有数据。")])
    snap = alice.say("看项目").json()
    assert json.loads([m for m in client.calls[2]["messages"] if m["role"] == "tool"][-1]["content"])["error_code"] == "result_too_large"
    assert snap["status"] == "idle"


def test_history_sent_to_the_model_is_bounded(alice, agent):
    lim = AgentLimits(max_history_messages=3)
    client = agent([text(f"回答{i}") for i in range(6)], agent_limits=lim)
    tid = None
    for i in range(6):
        tid = alice.say(f"问题{i}", tid).json()["task_id"]
    assert all(len(c["messages"]) <= 1 + 3 for c in client.calls)                              # 1 条 system + 最近 3 条


# ---------- 最终回答：校验，不通过就回退到服务端摘要 ----------
@pytest.fixture
def runs_by_verdict(alice):
    runs = alice.get("/api/projects/PRJ-SM-003/runs").json()
    return {"fail_qc": next(r["id"] for r in runs if r["qc"] == "fail"), "pass_qc": next(r["id"] for r in runs if r["qc"] == "pass"),
            "fail_acc": next(r["id"] for r in runs if r["accepted"] == "fail"), "runs": runs}


def ask_with_final(alice, agent, final_text):
    client = agent([delegate("d1", "data", "查批次"), calls(("t1", "list_project_runs", {"project_id": "PRJ-SM-003"})),
                    text("已查询。"), final_text])
    return alice.say("汇总 PRJ-SM-003 的批次规则结果").json(), client


def test_valid_model_answer_is_accepted_and_verdicts_still_come_from_the_server(alice, agent, runs_by_verdict):
    r = runs_by_verdict
    answer = f"{r['fail_qc']} 的 QC 未通过，{r['pass_qc']} 的 QC 通过。"
    snap, _ = ask_with_final(alice, agent, text(answer))
    assert snap["final"]["answer_source"] == "model" and snap["final"]["answer"] == answer
    api = {x["id"]: x for x in r["runs"]}
    assert all(row["qc"] == api[row["run_id"]]["qc"] for row in snap["final"]["evidence"]["runs"])


def test_short_run_ids_resolve_against_evidence(alice, agent, runs_by_verdict):
    short = runs_by_verdict["fail_qc"].split("-")[-1]
    snap, _ = ask_with_final(alice, agent, text(f"{short} 的 QC 未通过。"))
    assert snap["final"]["answer_source"] == "model"


@pytest.mark.parametrize("bad,code", [
    ("{pass_qc} 的 QC 未通过。", "verdict_conflict"),                                         # 与规则结果冲突
    ("{fail_qc} 通过。", "verdict_conflict"),                                                 # 未指明维度时按批接受判定
    ("所有批次均通过。", "unanchored_verdict"),                                               # 无证据的结论
    ("PRJ-SM-003 整体通过。", "project_level_verdict"),
    ("PRJ-SM-009-R01 的 QC 通过。", "unknown_id"),                                            # 编造的编号
    ("共 99 个批次。", "unsupported_number"),                                                 # 编造的数字
    ("报告已审核通过，可以导出。", "approval_claim"),
    ("已批准 PRJ-SM-003 的报告。", "approval_claim"),
    ("已为您生成报告草稿。", "generated_claim_without_evidence"),
    ("已生成 DRAFT-SMP-20261026123922-aaaaaa。", "unknown_id"),
    ("很长" * 1100, "too_long"),
])
def test_unsupported_model_text_is_rejected_and_replaced_by_a_sourced_server_summary(alice, agent, runs_by_verdict, bad, code):
    snap, _ = ask_with_final(alice, agent, text(bad.format(**runs_by_verdict)))
    f = snap["final"]
    assert f["answer_source"] == "server_fallback" and any(x.startswith(code) for x in f["fallback_reason"])
    assert bad.format(**runs_by_verdict) not in f["answer"]                                  # 被拒绝的整段文字不会出现在最终回答里
    assert "未通过模型文字校验" not in f["answer"] and "已改用系统根据规则结果生成的摘要" in f["answer"]
    api = {x["id"]: x for x in runs_by_verdict["runs"]}
    for row in f["evidence"]["runs"]:                                                       # 摘要里的判定逐项来自规则结果
        a = api[row["run_id"]]
        assert (row["calibration"], row["qc"], row["accepted"], row["isr"]) == (a["calibration"], a["qc"], a["accepted"], a["isr"])
        assert f"分析批 {row['run_id']}" in f["answer"]


# ---------- 发给模型的内容已脱敏 ----------
SECRETS = ["a@b.com", "13812345678", "sk-0123456789abcdefABCDEF", "123456789012"]


def test_everything_sent_to_the_model_and_stored_is_redacted(web_app, alice, agent):
    inj_title = "联系 c@d.com 或 13912345678，账号 987654321098"
    with web_app.state.session_factory() as s:
        s.add(Project(id="PRJ-RED-001", title=inj_title, modality="small_molecule", analyte="DEMO-RED", matrix="人血浆",
                      method_type="LC-MS/MS", unit="ng/mL", lloq=1.0, uloq=10.0, phase="sample_analysis",
                      planned_run_count=1, is_simulated=True))
        s.commit()
    try:
        client = agent([delegate("d1", "data", "读取 PRJ-RED-001，联系 a@b.com"), calls(("t1", "get_project", {"project_id": "PRJ-RED-001"})),
                        text("已读取。"), text("已读取项目信息。")])
        msg = "请读取 PRJ-RED-001。联系 a@b.com 手机 13812345678 密钥 sk-0123456789abcdefABCDEF 账号 123456789012"
        snap = alice.say(msg).json()
        sent = client.sent_text()
        for secret in SECRETS + ["c@d.com", "13912345678", "987654321098"]:
            assert secret not in sent, secret
        assert "[已脱敏:邮箱]" in sent
        with web_app.state.session_factory() as s:
            stored = json.dumps([m.content for m in s.query(AgentMessage).filter_by(task_id=snap["task_id"]).all()], ensure_ascii=False)
        assert all(x not in stored for x in SECRETS) and all(x not in json.dumps(snap, ensure_ascii=False) for x in SECRETS)
    finally:
        with web_app.state.session_factory() as s:
            s.delete(s.get(Project, "PRJ-RED-001"))
            s.commit()


def test_user_message_is_length_limited(alice, agent):
    client = agent([text("好的")])
    alice.say("长" * 3900)
    sent_user = [m["content"] for m in client.calls[0]["messages"] if m["role"] == "user"][0]
    assert len(sent_user) <= 2000 + 10 and sent_user.endswith("…[已截断]")
    assert alice.post("/api/agent/messages", {"message": ""}).status_code == 422
    assert alice.post("/api/agent/messages", {"message": "x" * 4001}).status_code == 422


def test_whitespace_only_message_is_refused(alice, agent):
    agent([text("x")])
    r = alice.say("   \n  ")
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "empty_message"


# ---------- 故障不泄露、不卡死 ----------
def test_model_errors_do_not_leak_details(alice, agent):
    class Boom(FakeClient):
        def chat_with_tools(self, messages, tools, *, max_tokens=1024):
            raise RuntimeError("upstream failed with key sk-0123456789abcdefABCDEF")
    from tests.agent.agent_helpers import FakeClient as _  # noqa: F401
    agent([])
    alice.c.app.state.agent_client = Boom([])
    r = alice.say("你好")
    snap = r.json()
    assert r.status_code == 200 and snap["status"] == "failed" and snap["error_code"] == "model_unavailable"
    assert "sk-0123" not in r.text and "RuntimeError" not in r.text and "upstream" not in r.text


def test_internal_errors_fail_the_task_instead_of_leaving_it_running(alice, agent, monkeypatch):
    agent([delegate("d1", "data", "x"), calls(("t1", "list_projects", {}))])

    def boom(*a, **k):
        raise ValueError("secret internal detail")
    monkeypatch.setattr(grounding, "absorb", boom)
    snap = alice.say("看项目").json()
    assert snap["status"] == "failed" and snap["error_code"] == "internal_error" and "secret" not in json.dumps(snap)


def test_steps_never_contain_free_text_arguments(alice, agent):
    agent([delegate("d1", "qa", "回答"), calls(("t1", "ask_regulatory", {"question": "偏差在什么条件下可以关闭 联系 a@b.com"})),
           text("见引用。"), text("已查询。")])
    snap = alice.say("请问偏差什么时候可以关闭").json()
    blob = json.dumps(snap["steps"], ensure_ascii=False)
    assert "偏差在什么条件下" not in blob and "a@b.com" not in blob                           # 只展示白名单键的短标量值
    assert count_rows(alice.c.app, AgentMessage, AgentMessage.task_id == snap["task_id"]) > 0


def test_codes_inside_tool_results_are_known_ids_but_invented_ones_still_fail(alice, agent):
    """真实模型回归：项目名里的化合物代号（DEMO-API-303）出现在工具结果里，引用它不是“编造编号”。"""
    def ask(final_text):
        agent([delegate("d1", "data", "查项目"), calls(("t1", "get_project", {"project_id": "PRJ-SM-003"})), text("已查询。"), final_text])
        return alice.say("介绍一下 PRJ-SM-003").json()["final"]
    assert ask(text("PRJ-SM-003 是 DEMO-API-303 的毒代样品分析项目。"))["answer_source"] == "model"
    bad = ask(text("PRJ-SM-003 是 DEMO-API-999 的毒代样品分析项目。"))
    assert bad["answer_source"] == "server_fallback" and "unknown_id:DEMO-API-999" in bad["fallback_reason"]


def test_english_verdict_words_are_checked_too(alice, agent, runs_by_verdict):
    """真实模型回归：规则引擎原文 pass / fail 也不能和判定冲突（此前英文判定词完全不被检查）。"""
    r = runs_by_verdict
    good, _ = ask_with_final(alice, agent, text(f"{r['fail_qc']} 的 QC fail。"))
    assert good["final"]["answer_source"] == "model"
    bad, _ = ask_with_final(alice, agent, text(f"{r['fail_qc']} 的 QC pass。"))
    assert bad["final"]["answer_source"] == "server_fallback" and any(x.startswith("verdict_conflict") for x in bad["final"]["fallback_reason"])


REAL_STYLE = """- 涉及分析批（共 3 个）：
  - {a}（2026-08-18，37 份样品/12 份 ISR）：校准 通过、QC 通过、批接受 通过、ISR 通过
  - {b}（2026-09-01，49 份样品/14 份 ISR）：校准 通过、QC {qc}、批接受 未通过、ISR 通过
  - {c}（2026-09-08，41 份样品/12 份 ISR）：校准 待复核、QC 通过、批接受 待复核、ISR 通过"""


def test_real_model_style_bulleted_runs_with_parenthetical_details(alice, agent):
    """真实模型回归：括号里带日期和样品数的清单曾因括号内的逗号被拆句，导致正确回答被误判回退。"""
    runs = alice.get("/api/projects/PRJ-SM-003/runs").json()
    ids = [r["id"] for r in sorted(runs, key=lambda r: r["id"])]
    fmt = lambda qc: REAL_STYLE.format(a=ids[0], b=ids[1], c=ids[2], qc=qc)  # noqa: E731
    snap, _ = ask_with_final(alice, agent, text(fmt("未通过")))
    assert snap["final"]["answer_source"] == "model", snap["final"]["fallback_reason"]
    snap, _ = ask_with_final(alice, agent, text(fmt("通过")))                      # 真正的冲突仍被抓到
    assert snap["final"]["answer_source"] == "server_fallback"


TABLE = """**分析批与规则结果**

| 批次 | 日期 | 批次状态 | 样品数 | ISR 数 | 校准 | QC | 批接受 | ISR | 报告 |
|---|---|---|---|---|---|---|---|---|---|
| {a} | 2026-08-18 | completed | 37 | 12 | 通过 | 通过 | 通过 | 通过 | 无报告记录 |
| {b} | 2026-09-01 | completed | 49 | 14 | 通过 | {qc} | 未通过 | 通过 | 无报告记录 |
| {c} | 2026-09-08 | under_review | 41 | 12 | 待复核 | 通过 | 待复核 | 通过 | 无报告记录 |

要点：R02 的 QC 与批接受为未通过，R03 的校准与批接受为待复核。"""


def test_markdown_tables_are_checked_column_by_column(alice, agent):
    """真实模型回归：判定在数据行、维度名在表头的表格——正确的被采纳，改错任何一格都被抓到。"""
    runs = alice.get("/api/projects/PRJ-SM-003/runs").json()
    ids = [r["id"] for r in sorted(runs, key=lambda r: r["id"])]
    fmt = lambda **k: TABLE.format(a=ids[0], b=ids[1], c=ids[2], **{"qc": "未通过", **k})  # noqa: E731
    snap, _ = ask_with_final(alice, agent, text(fmt()))
    assert snap["final"]["answer_source"] == "model", snap["final"]["fallback_reason"]
    snap, _ = ask_with_final(alice, agent, text(fmt(qc="通过")))                       # R02 的 QC 实际未通过
    assert snap["final"]["answer_source"] == "server_fallback" and f"verdict_conflict:{ids[1]}" in snap["final"]["fallback_reason"]
    snap, _ = ask_with_final(alice, agent, text(fmt().replace("| 37 | 12 | 通过 | 通过 | 通过 | 通过 |", "| 37 | 12 | 通过 | 通过 | 未通过 | 通过 |")))
    assert snap["final"]["answer_source"] == "server_fallback"                         # R01 的批接受被改错


def test_english_dimension_names_and_run_anchor_carried_within_a_line(alice, agent):
    """真实模型回归：英文列名 / “R02：calibration 通过；qc 未通过”式要点。借用批号只限同一行里以维度名开头的分句。"""
    runs = alice.get("/api/projects/PRJ-SM-003/runs").json()
    ids = [r["id"] for r in sorted(runs, key=lambda r: r["id"])]
    ok = f"- {ids[1]}：calibration 通过；qc 未通过；isr 通过；accepted 未通过。"
    assert ask_with_final(alice, agent, text(ok))[0]["final"]["answer_source"] == "model"
    wrong_dim = f"- {ids[1]}：calibration 通过；qc 通过；isr 通过。"                       # R02 的 qc 实际未通过
    assert ask_with_final(alice, agent, text(wrong_dim))[0]["final"]["answer_source"] == "server_fallback"
    wrong_acc = f"| 批次 | accepted |\n|---|---|\n| {ids[1]} | 通过 |"                    # 英文表头同样逐列核对
    assert ask_with_final(alice, agent, text(wrong_acc))[0]["final"]["answer_source"] == "server_fallback"
    loose = f"- {ids[0]}：calibration 通过；所有批次均通过。"                                # 不以维度名开头的无批号句不借批号
    assert "unanchored_verdict" in ask_with_final(alice, agent, text(loose))[0]["final"]["fallback_reason"]
