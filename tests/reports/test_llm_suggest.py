"""报告审核中的大模型润色建议：只读、校验后才给出；采纳仍走人工修订接口并重新校验。"""
import pytest
from sqlalchemy import update

from app.db.models import Project, ReportDraft
from tests.llm.test_deepseek import KEY, make
from tests.reports.test_review import blocks, detail, new_draft, pick, to_in_review


@pytest.fixture
def h(auth):
    return {**auth, "X-Operator": "reviewer-a"}


def use(api, reply):
    p, sess = make(reply)
    api.app.state.llm_provider_obj = p
    return sess


def suggest(api, h, did, bid):
    return api.post(f"/api/reports/{did}/blocks/{bid}/suggest", headers=h)


def test_disabled_by_default(api, h):
    d = new_draft(api, h)
    r = suggest(api, h, d["draft_id"], pick(d, kind="static")["bid"])
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "llm_not_enabled"
    assert api.app.state.settings.llm_provider == "deterministic"


def test_requires_operator(api, auth):
    assert api.post("/api/reports/x/blocks/B001/suggest", headers=auth).status_code == 400


def test_valid_static_suggestion_can_be_adopted_through_edit(api, h):
    d = new_draft(api, h)
    did, st = d["draft_id"], pick(d, kind="static")
    new_text = st["text"] + "（措辞整理）"
    sess = use(api, new_text)
    r = suggest(api, h, did, st["bid"])
    body = r.json()
    assert r.status_code == 200 and body["used"] and body["suggestion"] == new_text and body["original"] == st["text"]
    assert detail(api, h, did)["current_version"] == 1                       # 建议本身不改任何状态
    assert len(sess.calls) == 1 and KEY not in r.text
    ok = api.post(f"/api/reports/{did}/blocks/{st['bid']}/edit", headers=h, json={
        "base_version": 1, "text": body["suggestion"], "note": "采纳 DeepSeek 润色建议（人工确认）"})
    assert ok.status_code == 200 and ok.json()["flag"] is None
    assert detail(api, h, did)["current_version"] == 2


@pytest.mark.parametrize("bad", ["APPEND:合格率 95%。", ""])
def test_unfaithful_suggestions_are_dropped(api, h, bad):
    d = new_draft(api, h)
    st = pick(d, kind="static")
    use(api, st["text"] + bad[7:] if bad.startswith("APPEND:") else bad)
    body = suggest(api, h, d["draft_id"], st["bid"]).json()
    assert body["used"] is False and body["suggestion"] is None and body["reason"]


def test_fact_sentence_numbers_must_come_from_its_source_facts(api, h):
    d = new_draft(api, h)
    fs = pick(d, kind="fact", tagged=True, with_digits=True)
    use(api, fs["text"] + " 另有 99999 例。")
    assert suggest(api, h, d["draft_id"], fs["bid"]).json()["reason"] == "new_numbers"
    use(api, fs["text"] + "（措辞整理）")
    assert suggest(api, h, d["draft_id"], fs["bid"]).json()["used"] is True


def test_placeholders_must_be_kept_and_tables_not_suggestable(api, h):
    with api.app.state.session_factory() as s:
        s.execute(update(Project).where(Project.id == "PRJ-SM-001").values(sample_prep=None))
        s.commit()
    d = new_draft(api, h)
    ph = next(b for b in blocks(d) if b["type"] == "sentence" and "[待补充: 样品前处理方法]" in b["text"])
    use(api, "样品前处理方法：已完成。")
    assert suggest(api, h, d["draft_id"], ph["bid"]).json()["reason"] == "required_text_missing"
    tb = next(b for b in blocks(d) if b["type"] == "table")
    assert suggest(api, h, d["draft_id"], tb["bid"]).status_code == 422


def test_locked_after_approval_and_audit_has_no_key(api, h):
    d = new_draft(api, h)
    st = pick(d, kind="static")
    use(api, st["text"] + "（整理）")
    r = suggest(api, h, d["draft_id"], st["bid"])
    audit = api.get("/api/audit", params={"request_id": r.headers["x-request-id"]}, headers=h).json()[0]
    assert "LLM:deepseek:suggested" in audit["rule_ids"] and KEY not in str(audit)
    to_in_review(api, h, d["draft_id"])
    with api.app.state.session_factory() as s:
        s.get(ReportDraft, d["draft_id"]).review_status = "reviewed"
        s.commit()
    assert suggest(api, h, d["draft_id"], st["bid"]).status_code == 409
