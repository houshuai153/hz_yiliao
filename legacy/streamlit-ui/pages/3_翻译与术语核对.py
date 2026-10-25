"""翻译与术语核对：只翻译登记的固定句型；未知句型不生成译文。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import api_client as api  # noqa: E402
import layout  # noqa: E402

STATUS_ZH = {"translated": "已翻译", "partial": "部分翻译", "unsupported": "不支持（未生成译文）", "checked": "已核对"}
STATUS_COLOR = {"translated": "green", "partial": "orange", "unsupported": "red", "checked": "blue"}
DIR_ZH = {"zh2en": "中译英", "en2zh": "英译中", "bilingual": "双语对照（自动判断语言）"}

layout.setup("翻译与术语核对", icon=":material/translate:")

ok, cap = api.call("GET", "/api/translate/capabilities")
if ok:
    st.caption(cap["note"])
    with st.expander(f"支持的句型（{len(cap['patterns'])} 个）与术语表说明"):
        st.caption(cap["glossary_note"])
        st.dataframe(pd.DataFrame(cap["patterns"]), width="stretch", hide_index=True)

direction = st.radio("方向", list(DIR_ZH), format_func=DIR_ZH.get, horizontal=True)
src_col, ref_col = st.columns(2)
text = src_col.text_area("原文（每行一个段落）", height=150)
reference = ref_col.text_area("现成译文（可选）", height=150, disabled=direction == "bilingual",
                              help="按行与原文一一对应；填写后只做术语核对，不翻译。")
if st.button("翻译 / 核对", type="primary") and text.strip():
    body = {"text": text, "direction": direction}
    if reference.strip() and direction != "bilingual":
        body["reference"] = reference
    with st.spinner("处理中…"):
        ok, res = api.call("POST", "/api/translate", json=body)
    if not ok:
        api.show_error(st, res)
    else:
        st.caption(f"统计：{res['stats']}")
        for w in res["document_warnings"]:
            st.error(f"{w['code']}：{w['message']}")
        for p in res["paragraphs"]:
            with st.container(border=True):
                st.markdown(f"**段落 {p['index'] + 1}**　:{STATUS_COLOR[p['status']]}-badge[{STATUS_ZH[p['status']]}]")
                a, b = st.columns(2)
                a.markdown(f"**原文**：{p['source']}")
                b.markdown(f"**译文**：{p['target'] if p['target'] is not None else '— （未生成）'}")
                for w in p["warnings"]:
                    {"error": st.error, "warning": st.warning, "info": st.info}[w["severity"]](f"{w['code']}：{w['message']}")
                if p["term_hits"]:
                    with st.expander(f"术语命中（{len(p['term_hits'])}）"):
                        st.dataframe(pd.DataFrame([{
                            "命中文本": h["matched_text"], "术语": h["term_id"], "推荐中文": h["expected"]["zh"],
                            "推荐英文": h["expected"]["en"], "缩写": h["expected"]["abbr"] or ""} for h in p["term_hits"]]),
                            width="stretch", hide_index=True)
        if res.get("bilingual_rows"):
            st.markdown("#### 双语对照")
            st.dataframe(pd.DataFrame(res["bilingual_rows"]), width="stretch", hide_index=True)
