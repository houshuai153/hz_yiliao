"""法规与 SOP 问答：答案为从演示知识库抽取的原句，带引用；证据不足时拒答。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402
import layout  # noqa: E402

layout.setup("法规与 SOP 问答", icon=":material/quiz:",
             subtitle="知识库全部是“教学演示摘要 / 模拟内部 SOP”，不是监管机构原文；答案由检索结果抽取，不由模型撰写。")

with st.form("ask", border=False):
    question = st.text_input("请输入问题", placeholder="例如：偏差在什么条件下可以关闭")
    asked = st.form_submit_button("提问", type="primary")

if asked and question.strip():
    with st.spinner("检索中…"):
        ok, res = api.call("POST", "/api/regulatory/ask", json={"question": question.strip()})
    if not ok:
        api.show_error(st, res)
    else:
        ans, meta = st.columns([3, 1])
        with ans:
            st.markdown("#### 回答")
            if res["refused"]:
                reason = res["refusal_reason"]
                st.warning(f"已拒答（{reason}）：{res['answer']}", icon=":material/block:")
                st.caption(f"拒答原因：{api.REFUSAL_ZH.get(reason, reason)}")
            else:
                with st.container(border=True):
                    st.markdown(res["answer"])
                if res.get("answer_mode") == "llm_polished":
                    st.markdown(":violet-badge[大模型整理措辞] 引用、confidence 与拒答判定仍由规则决定；请核对原句。")
                    with st.expander("查看未经模型改写的抽取式答案"):
                        st.markdown(res["extractive_answer"])
                elif res.get("llm"):
                    st.caption(f"已调用大模型但未采用其输出（{res['llm'].get('fallback_reason')}），显示的是规则抽取的答案。")
        with meta:
            st.markdown("#### 证据充分度")
            st.metric("confidence", res["confidence"])
            st.caption("confidence 是证据充分度启发式分数，不是答案正确的概率，未经校准")
            with st.expander("构成"):
                st.json(res["confidence_basis"])
        if res["citations"]:
            st.markdown("#### 引用证据")
            st.dataframe(pd.DataFrame([{
                "章节 ID": c["section_id"], "文档": c["doc_id"], "版本": c["version"], "标题": c["title"],
                "性质": c["label"], "引用原句": c["quote"]} for c in res["citations"]]),
                width="stretch", hide_index=True,
                column_config={"引用原句": st.column_config.TextColumn(width="large")})
        st.caption(f"request_id：{res['request_id']}")
elif asked:
    st.info("请先输入问题。")
