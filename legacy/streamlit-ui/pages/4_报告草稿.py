"""报告草稿与审核：生成 → 在线预览与人工审核 → 审核通过 → 导出 DOCX。

页面只经 HTTP 调用 API；状态、检查结果（readiness）、导出权限全部由后端决定，页面不做判定也不做门禁。
"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402
import layout  # noqa: E402

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ACTION_ZH = {"generated": "生成草稿", "start_review": "开始审核", "request_changes": "退回修改", "resubmit": "重新提交审核",
             "approve": "审核通过", "edit": "修订", "add": "新增", "export": "导出 DOCX", "issue_added": "提出问题",
             "comment_added": "添加意见", "comment_resolved": "处理意见"}
_PH = re.compile(r"\[待补充: [^\]]*\]")

layout.setup("报告草稿与审核", icon=":material/description:")
st.caption("流程：① 生成草稿 → ② 预览并人工审核（可修订叙述、留意见）→ ③ 审核通过后才能导出 DOCX。"
           "草稿由规则与模板生成，每条业务结论附数据来源标签，缺失值保留“[待补充: 字段]”。")

me = api.operator()


CHG_ZH = {"generated": "生成", "edit": "修订", "add": "新增"}


def flash(kind: str, msg: str) -> None:
    st.session_state.setdefault("flash", []).append((kind, msg))


def post(path: str, body: dict, ok_msg: str) -> bool:
    ok, res = api.call("POST", path, json=body)
    if ok:
        flash("success", ok_msg)
        return True
    api.show_error(st, res)
    return False


for fkind, fmsg in st.session_state.pop("flash", []):
    getattr(st, fkind)(fmsg)

# ---------------- ① 生成 ----------------
ok, drafts = api.call("GET", "/api/reports")
if not ok:
    api.show_error(st, drafts)
    st.stop()

with st.expander("① 新建草稿", expanded=not drafts):
    ok, projects = api.call("GET", "/api/projects")
    if not ok:
        api.show_error(st, projects)
        st.stop()
    pid = st.selectbox("项目", [p["id"] for p in projects],
                       format_func=lambda i: next(p["title"] for p in projects if p["id"] == i))
    ok, runs = api.call("GET", f"/api/projects/{pid}/runs")
    if not ok:
        api.show_error(st, runs)
        st.stop()
    g1, g2 = st.columns(2)
    chosen = g1.multiselect("纳入的分析批（不选 = 全部）", [r["id"] for r in runs])
    kind = g2.radio("报告类型", list(api.KIND_ZH), format_func=api.KIND_ZH.get, horizontal=True)
    if st.button("生成草稿", type="primary"):
        with st.spinner("生成中…"):
            ok, res = api.call("POST", "/api/reports/draft", json={"project_id": pid, "run_ids": chosen, "kind": kind})
        if not ok:
            api.show_error(st, res)
        else:
            st.session_state["draft_pick"] = res["draft_id"]
            st.query_params["draft"] = res["draft_id"]
            flash("success", f"已生成 {res['draft_id']}（状态：{api.REVIEW_STATUS_ZH[res['review_status']]}）。"
                             f"事实 {res['fact_count']} 条；业务结论句 {res['fact_sentences']} 条，缺来源 {res['unsourced_sentences']} 条。"
                             "草稿不能直接导出，请在下方预览并完成人工审核。")
            if res["missing_fields"]:
                flash("warning", "待补充字段：" + "；".join(res["missing_fields"]) + "（审核通过时须逐项填写处理说明）")
            st.rerun()

# ---------------- ② 选择草稿 ----------------
if not drafts:
    layout.empty("还没有草稿。展开上方“新建草稿”生成第一份。")
    st.stop()

ids = [d["draft_id"] for d in drafts]
by_id = {d["draft_id"]: d for d in drafts}
if st.session_state.get("draft_pick") not in ids:
    qp = st.query_params.get("draft")
    st.session_state["draft_pick"] = qp if qp in ids else ids[0]
did = st.selectbox(
    "② 选择草稿（按生成时间倒序，刷新页面后保持当前选择）", ids, key="draft_pick",
    format_func=lambda i: f"{i}｜{by_id[i]['project_id']}｜{api.REVIEW_STATUS_ZH[by_id[i]['review_status']]}"
                          + ("｜旧草稿" if by_id[i]["legacy"] else f"｜v{by_id[i]['current_version']}"))
st.query_params["draft"] = did

ok, d = api.call("GET", f"/api/reports/{did}")
if not ok:
    api.show_error(st, d)
    st.stop()
if d["legacy"]:
    st.warning(d["message"])
    st.stop()

# 历史版本（只读）
ok_v, vers = api.call("GET", f"/api/reports/{did}/versions")
view_ver = d["current_version"]
if ok_v and len(vers) > 1:
    view_ver = st.selectbox(
        "查看版本", [v["version"] for v in reversed(vers)], key=f"view_{did}",
        format_func=lambda n: next(f"v{v['version']}{'（当前）' if v['is_current'] else '（历史，只读）'}｜"
                                   f"{CHG_ZH[v['change_type']]}｜{v['operator']}｜{v['created_at'][:16].replace('T', ' ')}"
                                   for v in vers if v["version"] == n))
    if view_ver != d["current_version"]:
        ok_d, d_view = api.call("GET", f"/api/reports/{did}", params={"version": view_ver})
        if ok_d:
            d = {**d, **{k: d_view[k] for k in ("sections", "facts", "stats", "viewing_version", "is_current")}}
readonly_view = not d["is_current"]

status = d["review_status"]
rd = d["readiness"]
st.divider()
h1, h2 = st.columns([4, 1])
h1.markdown(f"### {d['title']}")
h1.caption(f"{did} · 项目 {d['project_id']} · 分析批 {'、'.join(d['run_ids'])} · 当前版本 v{d['current_version']}")
h2.markdown(f"##### {layout.status_text(status)}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("事实（溯源）", d["stats"]["fact_count"])
m2.metric("业务结论句", d["stats"]["fact_sentences"])
m3.metric("待补充字段", d["stats"]["missing_fields"])
m4.metric("未解决问题", rd["open_issue_count"])

# ---------------- ③ 审核操作区 ----------------
if not me:
    st.info("审核类操作需要先在左侧栏填写“操作人”（仅为操作人记录，不是电子签名）。", icon=":material/badge:")
with st.container(border=True):
    st.markdown("**审核操作**")
    if readonly_view:
        st.info(f"正在查看历史版本 v{view_ver}，只读。请切换到当前版本 v{d['current_version']} 操作。")
    elif status == "draft":
        st.write("草稿已生成，尚未开始审核。开始审核后可修订叙述、留意见，并给出通过 / 退回结论。")
        if st.button("开始审核", type="primary", disabled=not me):
            if post(f"/api/reports/{did}/transition", {"action": "start_review"}, "已开始审核。"):
                st.rerun()
    elif status == "changes_requested":
        ok, evs = api.call("GET", f"/api/reports/{did}/events")
        last = next((e for e in reversed(evs) if e["action"] == "request_changes"), None) if ok else None
        if last:
            st.warning(f"退回意见（{last['operator']}，{last['ts'][:16]}）：{last['comment']}")
        st.write("按意见在“修订”页签处理后重新提交。")
        if st.button("修订完成，重新提交审核", type="primary", disabled=not me):
            if post(f"/api/reports/{did}/transition", {"action": "resubmit"}, "已重新提交审核。"):
                st.rerun()
    elif status == "in_review":
        for b in rd["blockers"]:
            st.error(f"不能审核通过：{b['message']}", icon=":material/block:")
        opinion = st.text_area("审核意见（通过 / 退回都必须填写）", key=f"opinion_{did}", height=90)
        st.markdown("**确认项**")
        confirms = [k for k, label in rd["required_confirmations"].items() if st.checkbox(label, key=f"cf_{did}_{k}")]
        acks: dict[str, str] = {}
        if rd["missing_to_acknowledge"]:
            st.markdown("**待补充字段处理说明**")
            st.caption("这里只记录“为什么仍保留待补充”，不会补齐数据；报告里仍显示“[待补充: …]”，须回到源数据补齐后重新生成。")
            for f in rd["missing_to_acknowledge"]:
                acks[f] = st.text_input(f"「{f}」", key=f"ack_{did}_{f}", placeholder="处理说明（必填）")
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("审核通过", type="primary", disabled=not me or bool(rd["blockers"])):
            if post(f"/api/reports/{did}/transition", {"action": "approve", "comment": opinion,
                                                       "confirmations": confirms, "missing_ack": acks}, "审核通过。现在可以导出 DOCX。"):
                st.rerun()
        if b2.button("退回修改", disabled=not me):
            if post(f"/api/reports/{did}/transition", {"action": "request_changes", "comment": opinion}, "已退回修改。"):
                st.rerun()
    else:  # reviewed
        st.success("审核通过（操作人记录）。人工审核通过不等于正式 QA 批准，正式报告须按质量体系签署。")
        key = f"xl_{did}_{d['current_version']}"
        if st.button("生成导出文件", type="primary", disabled=not me, help="导出会写入审核记录与审计日志"):
            ok, content = api.get_bytes(f"/api/reports/{did}/download")
            if ok:
                st.session_state[key] = content
            else:
                st.error(f"导出失败：{content}")
        if key in st.session_state:
            st.download_button("下载 DOCX（最终审核版本）", st.session_state[key],
                               file_name=f"{did}-v{d['current_version']}-reviewed.docx", mime=DOCX_MIME)

# ---------------- 内容页签 ----------------
t_prev, t_edit, t_cmt, t_hist = st.tabs(["预览", "修订", f"意见与问题（{len(d['comments'])}）", "版本与记录"])

blocks_flat = [(s["title"], b) for s in d["sections"] for b in s["blocks"]]
label_of = {b["bid"]: (b["text"] if b["type"] == "sentence" else b["caption"]) for _, b in blocks_flat}


def block_html(b: dict) -> str:
    body = html.escape(b["text"])
    body = _PH.sub(lambda m: f"<mark>{m.group(0)}</mark>", body)
    tags = f"<span class='src-tag'>[{','.join(b['tags'])}]</span>" if b["tags"] else ""
    chips = ""
    if b["edited"]:
        chips += "<span class='src-tag'>人工新增</span>" if b["added"] else "<span class='src-tag'>人工修订</span>"
    if b["open_issues"]:
        chips += f"<span class='src-tag' style='color:#c00000'>未解决问题 {b['open_issues']}</span>"
    css = "block-flag" if b["flag"] else ("block-edited" if b["edited"] else "")
    return f"<div class='{css}' style='margin:.15rem 0'>{body}{tags}{chips}</div>"


with t_prev:
    only_attention = st.toggle("只看需要关注的块（已修订 / 无来源 / 有未解决问题）", key=f"attn_{did}")
    for s in d["sections"]:
        shown = [b for b in s["blocks"] if not only_attention or (
            b["type"] == "sentence" and (b["edited"] or b["flag"] or b["open_issues"]))]
        if only_attention and not shown:
            continue
        st.markdown(f"#### {s['title']}")
        for b in shown:
            if b["type"] == "table":
                st.caption(b["caption"])
                st.dataframe(pd.DataFrame(b["rows"], columns=b["header"]), width="stretch", hide_index=True)
                st.caption(f"数据来源：{b['source']}")
                continue
            st.markdown(block_html(b), unsafe_allow_html=True)
            if b["flag"]:
                st.error(f"无法对应数据来源，须处理后才能审核通过：{b['flag']}")
            if b["edited"] and b["original_text"]:
                with st.expander("查看生成时原文"):
                    st.write(b["original_text"])

with t_edit:
    st.caption("只能修订叙述性文字。表格、事实表、QC / ISR 判定与规则结果不可在此改写；“[待补充: …]”不能被删除或用文字伪装为已补齐。"
               "修订后的业务结论必须仍能对应数据来源，否则会被标记并阻止审核通过。")
    if readonly_view or status == "reviewed":
        st.info("当前版本不可修订（历史版本或已审核通过）。")
    else:
        sentences = [(sec, b) for sec, b in blocks_flat if b["type"] == "sentence"]
        only_edit = st.toggle("只列出可修订的句子", value=True, key=f"oe_{did}")
        opts = [b["bid"] for _, b in sentences if b["editable"] or not only_edit]
        bid = st.selectbox("选择句子", opts, key=f"blk_{did}",
                           format_func=lambda i: f"{i}｜{next(sec for sec, b in blocks_flat if b['bid'] == i)}｜{label_of[i][:40]}")
        blk = next(b for _, b in sentences if b["bid"] == bid)
        fact_opts = {f["key"]: f"{f['tag']} {f['label']}" for f in d["facts"]}
        with st.form(f"edit_{did}_{bid}"):
            new_text = st.text_area("文字", value=blk["text"], height=110)
            keys = blk["fact_keys"]
            if blk["kind"] == "fact":
                keys = st.multiselect("数据来源（事实）", list(fact_opts), default=blk["fact_keys"],
                                      format_func=fact_opts.get)
            note = st.text_input("修订原因（必填）")
            if st.form_submit_button("保存修订", disabled=not (me and blk["editable"])):
                if post(f"/api/reports/{did}/blocks/{bid}/edit",
                        {"base_version": d["current_version"], "text": new_text, "fact_keys": keys if blk["kind"] == "fact" else None,
                         "note": note}, "已保存修订，生成新版本。"):
                    st.rerun()
        if not blk["editable"]:
            st.caption("该句为“待补充”占位句，不可修订。")
        h = api.health() or {}
        if h.get("llm_enabled") and blk["editable"]:
            skey = f"sug_{did}_{bid}"
            if st.button("AI 润色建议", key=f"sugbtn_{bid}", disabled=not me,
                         help="把当前文字发给大模型（已脱敏）；只给建议，不改报告；采纳后仍作为人工修订留痕并重新校验。"):
                ok_s, sug = api.call("POST", f"/api/reports/{did}/blocks/{bid}/suggest")
                if ok_s:
                    st.session_state[skey] = sug
                else:
                    api.show_error(st, sug)
            sug = st.session_state.get(skey)
            if sug:
                if sug["used"]:
                    st.info(f"建议文字：{sug['suggestion']}", icon=":material/auto_awesome:")
                    if st.button("采纳（生成新版本，记入修订）", key=f"adopt_{bid}", disabled=not me):
                        if post(f"/api/reports/{did}/blocks/{bid}/edit",
                                {"base_version": d["current_version"], "text": sug["suggestion"],
                                 "fact_keys": blk["fact_keys"] if blk["kind"] == "fact" else None,
                                 "note": "采纳大模型润色建议（经人工确认）"}, "已采纳建议，生成新版本。"):
                            st.session_state.pop(skey, None)
                            st.rerun()
                else:
                    st.caption(f"没有可用的建议（{sug['reason']}）；原文保持不变。")
        with st.expander("在此句之后新增一句"):
            with st.form(f"add_{did}_{bid}"):
                nk = st.radio("类型", ["static", "fact"], horizontal=True,
                              format_func={"static": "说明性文字（不含数字）", "fact": "业务结论（须挂数据来源）"}.get)
                nt = st.text_area("文字", height=90)
                nkeys = st.multiselect("数据来源（事实，仅业务结论）", list(fact_opts), format_func=fact_opts.get)
                nn = st.text_input("新增原因（必填）")
                if st.form_submit_button("新增", disabled=not me):
                    if post(f"/api/reports/{did}/blocks",
                            {"base_version": d["current_version"], "after_block": bid, "kind": nk, "text": nt,
                             "fact_keys": nkeys if nk == "fact" else [], "note": nn}, "已新增，生成新版本。"):
                        st.rerun()

with t_cmt:
    if status != "reviewed" and not readonly_view:
        with st.form(f"cmt_{did}", clear_on_submit=True):
            scope = st.selectbox("针对", [None] + [b["bid"] for _, b in blocks_flat], key=f"scope_{did}",
                                 format_func=lambda i: "整份报告" if i is None else f"{i}｜{label_of[i][:36]}")
            ck = st.radio("类型", ["comment", "issue"], horizontal=True,
                          format_func={"comment": "意见（不阻止通过）", "issue": "问题（解决前阻止通过）"}.get)
            ct = st.text_area("内容", height=80)
            if st.form_submit_button("提交", disabled=not me):
                if post(f"/api/reports/{did}/comments", {"kind": ck, "text": ct, "block_id": scope}, "已记录。"):
                    st.rerun()
    if not d["comments"]:
        layout.empty("还没有审核意见。")
    for c in reversed(d["comments"]):
        with st.container(border=True):
            tag = ":red-badge[问题]" if c["kind"] == "issue" else ":gray-badge[意见]"
            state = ":green-badge[已处理]" if c["status"] == "resolved" else (":orange-badge[未解决]" if c["kind"] == "issue" else "")
            st.markdown(f"{tag} {state}　{c['created_by']} · {c['created_at'][:16]} · v{c['version']}"
                        + (f" · 针对 {c['block_id']}" if c["block_id"] else " · 整份报告"))
            st.write(c["text"])
            if c["status"] == "resolved":
                st.caption(f"处理说明（{c['resolved_by']}，{c['resolved_at'][:16]}）：{c['resolution']}")
            elif status != "reviewed" and not readonly_view:
                r1, r2 = st.columns([4, 1])
                res_text = r1.text_input("处理说明", key=f"res_{c['id']}", label_visibility="collapsed", placeholder="处理说明（必填）")
                if r2.button("标记已处理", key=f"resb_{c['id']}", disabled=not me):
                    if post(f"/api/reports/{did}/comments/{c['id']}/resolve", {"resolution": res_text}, "已处理。"):
                        st.rerun()

with t_hist:
    st.markdown("#### 版本")
    if ok_v:
        st.dataframe(pd.DataFrame([{"版本": f"v{v['version']}", "时间": v["created_at"][:19].replace("T", " "), "操作人": v["operator"],
                                    "类型": CHG_ZH[v["change_type"]],
                                    "说明": v["note"], "当前": "是" if v["is_current"] else ""} for v in vers]),
                     width="stretch", hide_index=True)
    st.markdown("#### 审核记录")
    ok_e, evs = api.call("GET", f"/api/reports/{did}/events")
    if ok_e:
        st.dataframe(pd.DataFrame([{
            "时间": e["ts"][:19].replace("T", " "), "操作人": e["operator"], "动作": ACTION_ZH.get(e["action"], e["action"]),
            "状态变化": f"{api.REVIEW_STATUS_ZH.get(e['from_status'], '—')} → {api.REVIEW_STATUS_ZH.get(e['to_status'], '—')}"
                        if e["to_status"] else "—",
            "版本": f"v{e['version']}", "意见 / 说明": e["comment"]} for e in evs]), width="stretch", hide_index=True)
    st.caption("“操作人”为操作人记录，不是电子签名。")
