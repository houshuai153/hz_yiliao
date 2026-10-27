"""写工具门禁：没有确认就不执行；确认绑定工具 / 参数 / 操作人 / 会话 / 有效期；幂等；不能 approve；角色白名单。"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agent.context import AgentContext, issue_grant
from app.agent.schema import ArgumentError, canonical_hash
from app.agent.tools import TOOLS, prepare_write, run_tool
from app.db.models import AgentWriteClaim, ReportDraft, ReportReviewEvent
from tests.agent.conftest import count

GEN = {"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": ["PRJ-SM-001-R01"]}


def with_grant(ctx, grant):
    return AgentContext(state=ctx.state, operator=ctx.operator, session_key=ctx.session_key, grant=grant, clock=ctx.clock)


def drafts(app):
    return count(app, ReportDraft)


@pytest.fixture
def seed_draft(api, auth):
    r = api.post("/api/reports/draft", headers={**auth, "X-Operator": "seed"},
                 json={"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": []})
    assert r.status_code == 200, r.text
    return r.json()["draft_id"]


def events(app, draft_id):
    with app.state.session_factory() as s:
        return s.scalars(select(ReportReviewEvent).where(ReportReviewEvent.draft_id == draft_id)
                         .order_by(ReportReviewEvent.id)).all()


# ---------- 默认拒绝 ----------
@pytest.mark.parametrize("name,args", [("generate_report_draft", GEN),
                                       ("transition_report", {"draft_id": "DRAFT-X", "action": "start_review"})])
def test_write_tools_refuse_without_a_grant(app, ctx, name, args):
    d0, c0 = drafts(app), count(app, AgentWriteClaim)
    r = run_tool(ctx, name, args)
    assert r["ok"] is False and r["error_code"] == "confirmation_required" and "确认" in r["message"]
    assert drafts(app) == d0 and count(app, AgentWriteClaim) == c0


@pytest.mark.parametrize("field,value", [("operator", "mallory"), ("confirmed", True), ("confirmation_token", "abc"),
                                         ("idempotency_key", "k1"), ("user_confirmed", True), ("session_key", "sess-A"),
                                         ("grant", {"tool": "x"}), ("approved", True)])
@pytest.mark.parametrize("name,args", [("generate_report_draft", GEN),
                                       ("transition_report", {"draft_id": "DRAFT-X", "action": "start_review"})])
def test_model_cannot_supply_identity_confirmation_or_idempotency_values(app, ctx, grant_for, name, args, field, value):
    g = grant_for(ctx, name, args)                                 # 即使服务端确实有一份有效确认
    d0 = drafts(app)
    r = run_tool(with_grant(ctx, g), name, {**args, field: value})
    assert r["ok"] is False and r["error_code"] == "invalid_arguments" and "不允许的参数" in r["message"]
    assert drafts(app) == d0


# ---------- 有确认：只执行被确认的那一个动作，且只一次 ----------
def test_confirmed_generation_runs_once_and_uses_the_session_operator(app, ctx, grant_for):
    g = grant_for(ctx, "generate_report_draft", GEN)
    d0 = drafts(app)
    r = run_tool(with_grant(ctx, g), "generate_report_draft", GEN)
    assert r["ok"] is True and drafts(app) == d0 + 1
    d = r["data"]
    assert d["review_status"] == "draft" and d["review_url"] == f"/reports/{d['draft_id']}" and "审核通过须由人" in d["approval_note"]
    assert {"type": "report", "id": d["draft_id"]} in r["sources"] and "path" not in d
    assert [e.operator for e in events(app, d["draft_id"])] == ["alice"]       # 操作人来自 ctx，不是模型
    with app.state.session_factory() as s:
        claim = s.get(AgentWriteClaim, g.idempotency_key)
    assert claim.status == "done" and claim.operator == "alice" and claim.result_json["data"]["draft_id"] == d["draft_id"]


def test_duplicate_confirmation_is_idempotent_and_creates_nothing_new(app, ctx, grant_for):
    g = grant_for(ctx, "generate_report_draft", GEN)
    first = run_tool(with_grant(ctx, g), "generate_report_draft", GEN)
    n = drafts(app)
    again = run_tool(with_grant(ctx, g), "generate_report_draft", GEN)
    assert again["ok"] and again["replayed"] is True and again["data"]["draft_id"] == first["data"]["draft_id"]
    assert drafts(app) == n


def test_a_new_confirmation_is_a_new_explicit_action(app, ctx, grant_for):
    a = run_tool(with_grant(ctx, grant_for(ctx, "generate_report_draft", GEN)), "generate_report_draft", GEN)
    n = drafts(app)
    b = run_tool(with_grant(ctx, grant_for(ctx, "generate_report_draft", GEN)), "generate_report_draft", GEN)
    assert b["ok"] and "replayed" not in b and b["data"]["draft_id"] != a["data"]["draft_id"] and drafts(app) == n + 1


def test_run_id_order_is_normalized_so_the_confirmed_hash_still_matches(app, ctx, grant_for):
    args = {"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": ["PRJ-SM-001-R02", "PRJ-SM-001-R01", "PRJ-SM-001-R02"]}
    g = grant_for(ctx, "generate_report_draft", args)
    swapped = {**args, "run_ids": ["PRJ-SM-001-R01", "PRJ-SM-001-R02"]}
    assert run_tool(with_grant(ctx, g), "generate_report_draft", swapped)["ok"]


# ---------- 越权：确认与实际动作不一致 ----------
@pytest.mark.parametrize("changed", [
    {"project_id": "PRJ-SM-003"}, {"kind": "sample_analysis"}, {"run_ids": []}, {"run_ids": ["PRJ-SM-001-R02"]}])
def test_grant_is_bound_to_the_exact_arguments(app, ctx, grant_for, changed):
    g = grant_for(ctx, "generate_report_draft", GEN)
    d0 = drafts(app)
    r = run_tool(with_grant(ctx, g), "generate_report_draft", {**GEN, **changed})
    assert r["error_code"] == "grant_mismatch" and drafts(app) == d0


def test_grant_for_one_tool_cannot_run_another(app, ctx, grant_for, seed_draft):
    g = grant_for(ctx, "transition_report", {"draft_id": seed_draft, "action": "start_review"})
    d0 = drafts(app)
    assert run_tool(with_grant(ctx, g), "generate_report_draft", GEN)["error_code"] == "grant_mismatch"
    assert drafts(app) == d0


@pytest.mark.parametrize("over,code", [({"operator": "mallory"}, "grant_not_bound"),
                                       ({"session_key": "sess-OTHER"}, "grant_not_bound"),
                                       ({"user_message_id": ""}, "grant_invalid")])
def test_grant_must_be_bound_to_this_operator_and_session(app, ctx, grant_for, over, code):
    g = grant_for(ctx, "generate_report_draft", GEN, **over)
    d0 = drafts(app)
    assert run_tool(with_grant(ctx, g), "generate_report_draft", GEN)["error_code"] == code
    assert drafts(app) == d0


def test_grant_expires(app, ctx, grant_for):
    g = grant_for(ctx, "generate_report_draft", GEN, ttl_s=60)
    d0 = drafts(app)
    for delta in (61, 60):                                              # 过期后，以及恰在到期时刻，都拒绝
        late = AgentContext(state=ctx.state, operator="alice", session_key="sess-A", grant=g,
                            clock=lambda d=delta: g.expires_at - timedelta(seconds=60) + timedelta(seconds=d))
        assert run_tool(late, "generate_report_draft", GEN)["error_code"] == "grant_expired"
    assert drafts(app) == d0
    early = AgentContext(state=ctx.state, operator="alice", session_key="sess-A", grant=g,
                         clock=lambda: g.expires_at - timedelta(seconds=1))
    assert run_tool(early, "generate_report_draft", GEN)["ok"]


@pytest.mark.parametrize("operator", ["", "anonymous", "   "])
def test_untrusted_or_missing_operator_never_writes(app, grant_for, operator):
    bad = AgentContext(state=app.state, operator=operator, session_key="sess-A")
    g = grant_for(AgentContext(state=app.state, operator="alice", session_key="sess-A"), "generate_report_draft", GEN)
    d0 = drafts(app)
    assert run_tool(with_grant(bad, g), "generate_report_draft", GEN)["error_code"] == "operator_required"
    assert drafts(app) == d0


# ---------- 幂等的边界 ----------
def test_same_key_with_different_arguments_is_a_conflict(app, ctx, grant_for):
    g1 = grant_for(ctx, "generate_report_draft", GEN)
    assert run_tool(with_grant(ctx, g1), "generate_report_draft", GEN)["ok"]
    other = {**GEN, "kind": "sample_analysis"}
    g2 = grant_for(ctx, "generate_report_draft", other, idempotency_key=g1.idempotency_key)
    n = drafts(app)
    r = run_tool(with_grant(ctx, g2), "generate_report_draft", other)
    assert r["error_code"] == "idempotency_conflict" and drafts(app) == n


def test_one_confirmation_cannot_be_reused_under_another_key(app, ctx, grant_for):
    g1 = grant_for(ctx, "generate_report_draft", GEN)
    assert run_tool(with_grant(ctx, g1), "generate_report_draft", GEN)["ok"]
    g2 = grant_for(ctx, "generate_report_draft", GEN, confirmation_id=g1.confirmation_id)
    n = drafts(app)
    assert run_tool(with_grant(ctx, g2), "generate_report_draft", GEN)["error_code"] == "confirmation_reused"
    assert drafts(app) == n


def test_an_unfinished_claim_is_not_executed_twice(app, ctx, grant_for):
    g = grant_for(ctx, "generate_report_draft", GEN)
    pending = prepare_write("generate_report_draft", GEN)
    with app.state.session_factory() as s:                              # 模拟上一次执行占用了键但进程中途退出
        s.add(AgentWriteClaim(idempotency_key=g.idempotency_key, confirmation_id=g.confirmation_id,
                              tool="generate_report_draft", operator="alice", session_key="sess-A",
                              args_hash=pending.args_hash, status="claimed"))
        s.commit()
    n = drafts(app)
    assert run_tool(with_grant(ctx, g), "generate_report_draft", GEN)["error_code"] == "action_in_progress"
    assert drafts(app) == n


def test_business_errors_are_returned_and_also_made_idempotent(app, ctx, grant_for):
    bad = {"project_id": "PRJ-NOPE", "kind": "validation", "run_ids": []}
    g = grant_for(ctx, "generate_report_draft", bad)
    n = drafts(app)
    r1 = run_tool(with_grant(ctx, g), "generate_report_draft", bad)
    r2 = run_tool(with_grant(ctx, g), "generate_report_draft", bad)
    assert r1["error_code"] == "project_not_found" and r2["error_code"] == "project_not_found" and r2["replayed"] is True
    assert drafts(app) == n


# ---------- transition_report：不能审核通过 ----------
def test_confirmed_start_review_changes_status_with_session_operator(app, ctx, grant_for, seed_draft):
    args = {"draft_id": seed_draft, "action": "start_review"}
    r = run_tool(with_grant(ctx, grant_for(ctx, "transition_report", args)), "transition_report", args)
    assert r["ok"] and r["data"]["report"]["review_status"] == "in_review" and r["data"]["review_url"] == f"/reports/{seed_draft}"
    ev = events(app, seed_draft)[-1]
    assert (ev.action, ev.operator, ev.to_status) == ("start_review", "alice", "in_review")


def test_transition_grant_is_bound_to_action_and_comment(app, ctx, grant_for, seed_draft):
    start = {"draft_id": seed_draft, "action": "start_review"}
    g = grant_for(ctx, "transition_report", start)
    assert run_tool(with_grant(ctx, g), "transition_report", {"draft_id": seed_draft, "action": "request_changes",
                                                                "comment": "x"})["error_code"] == "grant_mismatch"
    assert run_tool(with_grant(ctx, g), "transition_report", {**start, "comment": "多出的意见"})["error_code"] == "grant_mismatch"


def test_request_changes_without_comment_is_a_business_error_and_changes_nothing(app, ctx, grant_for, seed_draft):
    run_tool(with_grant(ctx, grant_for(ctx, "transition_report", {"draft_id": seed_draft, "action": "start_review"})),
             "transition_report", {"draft_id": seed_draft, "action": "start_review"})
    args = {"draft_id": seed_draft, "action": "request_changes"}
    r = run_tool(with_grant(ctx, grant_for(ctx, "transition_report", args)), "transition_report", args)
    assert r["error_code"] == "comment_required"
    assert events(app, seed_draft)[-1].action == "start_review"        # 状态没有变化


def test_agent_can_never_approve(app, ctx, grant_for, seed_draft, api, auth):
    start = {"draft_id": seed_draft, "action": "start_review"}
    run_tool(with_grant(ctx, grant_for(ctx, "transition_report", start)), "transition_report", start)
    approve = {"draft_id": seed_draft, "action": "approve", "comment": "ok"}
    # 1) 参数校验：approve 不在允许的取值内；服务端也无法为它生成待确认动作
    assert run_tool(ctx, "transition_report", approve)["error_code"] == "invalid_arguments"
    with pytest.raises(ArgumentError):
        prepare_write("transition_report", approve)
    # 2) 即使伪造出一份“恰好匹配”的确认，仍被参数校验拒绝
    forged = issue_grant(tool="transition_report", args_hash=canonical_hash("transition_report", approve),
                         operator="alice", session_key="sess-A", user_message_id="m")
    assert run_tool(with_grant(ctx, forged), "transition_report", approve)["error_code"] == "invalid_arguments"
    # 3) 纵深防御：绕过参数校验直接调用实现，也不会执行 approve
    r = TOOLS["transition_report"]._impl(with_grant(ctx, forged), approve)
    assert r["ok"] is False and r["error_code"] == "action_forbidden"
    status = api.get(f"/api/reports/{seed_draft}", headers=auth).json()["review_status"]
    assert status == "in_review" and all(e.action != "approve" for e in events(app, seed_draft))


# ---------- 角色白名单（阶段 2 的 roles.py 会提供集合；这里验证机制）----------
def test_tools_outside_the_allowed_set_are_refused_even_with_a_valid_grant(app, ctx, grant_for):
    allowed = frozenset({"list_projects", "get_project"})
    assert run_tool(ctx, "list_projects", {}, allowed=allowed)["ok"]
    assert run_tool(ctx, "dashboard_project", {"project_id": "PRJ-SM-001"}, allowed=allowed)["error_code"] == "tool_not_permitted"
    g = grant_for(ctx, "generate_report_draft", GEN)
    d0 = drafts(app)
    r = run_tool(with_grant(ctx, g), "generate_report_draft", GEN, allowed=allowed)
    assert r["error_code"] == "tool_not_permitted" and drafts(app) == d0
    with app.state.session_factory() as s:
        assert s.get(AgentWriteClaim, g.idempotency_key) is None        # 被拒绝时不占用幂等键


@pytest.mark.parametrize("name", ["no_such_tool", "", None, 7, "approve_report", "execute_sql"])
def test_unknown_tool_names_are_refused(ctx, name):
    assert run_tool(ctx, name, {})["error_code"] == "unknown_tool"


# ---------- 待确认动作的展示 ----------
def test_prepare_write_gives_a_human_readable_summary_and_stable_hash():
    p1 = prepare_write("generate_report_draft", {"project_id": "PRJ-SM-001", "kind": "validation", "run_ids": ["B", "A"]})
    p2 = prepare_write("generate_report_draft", {"kind": "validation", "run_ids": ["A", "B"], "project_id": "PRJ-SM-001"})
    assert p1.args_hash == p2.args_hash and p1.args == p2.args
    assert "PRJ-SM-001" in p1.summary and "不会自动审核" in p1.summary and "方法学验证报告" in p1.summary
    t = prepare_write("transition_report", {"draft_id": "D1", "action": "request_changes", "comment": " 请补充  "})
    assert "退回修改" in t.summary and "这不是审核通过" in t.summary and t.args["comment"] == "请补充"
    with pytest.raises(ArgumentError):
        prepare_write("list_projects", {})                               # 读工具没有待确认动作
    with pytest.raises(ArgumentError):
        prepare_write("generate_report_draft", {"project_id": "PRJ-SM-001"})
    with pytest.raises(ArgumentError):
        prepare_write("nope", {})
