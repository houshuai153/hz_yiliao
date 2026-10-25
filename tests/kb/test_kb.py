"""知识库：解析、切分、导入、索引与检索（离线）。"""
import json

import pytest
from sqlalchemy import func, select

from app.bootstrap import initialize
from app.db.models import KBDocument, KBSection
from app.kb.indexer import load_index
from app.kb.parser import KnowledgeFormatError, chunk_section, parse_dir, parse_text
from app.kb.tokenize import tokenize
from app.settings import ROOT, Settings

HEADER = """---
doc_id: DEMO-T
section_prefix: DEMO-T
version: "1.0"
title: 测试文档
source_type: demo_summary
is_demo: true
disclaimer: 测试用演示材料
---
"""


def test_parse_real_knowledge_dir():
    docs = parse_dir(ROOT / "knowledge")
    assert {d.doc_id for d in docs} == {
        "DEMO-ICH-M10", "DEMO-ChP-9012", "DEMO-GLP",
        "DEMO-SOP-QC", "DEMO-SOP-ISR", "DEMO-SOP-DEV", "DEMO-SOP-REPORT",
    }
    for d in docs:
        assert d.is_demo and d.disclaimer and d.version
        assert d.source_type in {"demo_summary", "demo_sop"}
        for s in d.sections:
            assert s.section_id.startswith("DEMO-") and s.heading and s.text


def test_parse_rejects_non_demo_and_bad_ids():
    with pytest.raises(KnowledgeFormatError):
        parse_text(HEADER.replace("is_demo: true", "is_demo: false") + "## [DEMO-T-001] 标题\n内容")
    with pytest.raises(KnowledgeFormatError):  # 像真实条款编号的 ID 不被接受
        parse_text(HEADER + "## [7.3.2] 标题\n内容")
    with pytest.raises(KnowledgeFormatError):  # 前缀与头声明不符
        parse_text(HEADER + "## [DEMO-OTHER-001] 标题\n内容")
    with pytest.raises(KnowledgeFormatError):  # 重复
        parse_text(HEADER + "## [DEMO-T-001] 甲\n内容\n## [DEMO-T-001] 乙\n内容")
    with pytest.raises(KnowledgeFormatError):  # 空章节
        parse_text(HEADER + "## [DEMO-T-001] 甲\n")
    with pytest.raises(KnowledgeFormatError):  # 无 YAML 头
        parse_text("## [DEMO-T-001] 甲\n内容")


def test_chunking_keeps_section_id():
    long_text = "\n\n".join(f"第{i}段" + "测" * 150 for i in range(5))
    doc = parse_text(HEADER + f"## [DEMO-T-001] 长章节\n{long_text}\n## [DEMO-T-002] 短章节\n短内容")
    long_chunks = chunk_section(doc.sections[0])
    assert len(long_chunks) > 1
    assert all(c.section_id == "DEMO-T-001" for c in long_chunks)
    assert [c.chunk_id for c in long_chunks] == [f"DEMO-T-001#{i + 1}" for i in range(len(long_chunks))]
    assert all(len(c.text) <= 420 for c in long_chunks)
    short = chunk_section(doc.sections[1])
    assert [c.chunk_id for c in short] == ["DEMO-T-002"]


def test_tokenize_mixed():
    t = tokenize("LLOQ 水平 ±20%")
    assert "lloq" in t and "水平" in t and "20" in t and "水" in t


@pytest.fixture(scope="module")
def initialized(tmp_path_factory):
    d = tmp_path_factory.mktemp("kb")
    settings = Settings(
        database_url=f"sqlite:///{(d / 'kb.db').as_posix()}",
        index_dir=str(d / "index"),
        knowledge_dir=str(ROOT / "knowledge"),
    )
    first = initialize(settings)
    return settings, first


def test_initialize_idempotent(initialized):
    settings, first = initialized
    meta_before = (json.loads((__import__("pathlib").Path(settings.index_dir) / "meta.json").read_text(encoding="utf-8")))
    second = initialize(settings)
    assert first["seed"] == second["seed"]
    assert first["knowledge"] == second["knowledge"]
    assert first["index"]["corpus_sha256"] == second["index"]["corpus_sha256"] == meta_before["corpus_sha256"]
    from app.db.base import make_engine, make_session_factory
    with make_session_factory(make_engine(settings.database_url))() as s:
        assert s.scalar(select(func.count()).select_from(KBDocument)) == 7
        assert s.scalar(select(func.count()).select_from(KBSection)) == first["knowledge"]["sections"]


def test_retrieval_returns_source_metadata(initialized):
    settings, _ = initialized
    idx = load_index(settings.index_dir)
    for search in (idx.search_bm25, idx.search_vector):
        hits = search("ISR 已测样品再分析 差值阈值", 5)
        assert hits, search.__name__
        top_ids = [h.chunk.section_id for h in hits]
        assert any(i.startswith(("DEMO-SOP-ISR", "DEMO-M10-008", "DEMO-CHP9012-007")) for i in top_ids), top_ids
        for h in hits:
            c = h.chunk
            assert c.doc_id and c.version and c.section_id.startswith("DEMO-") and c.heading
            assert c.source_type in {"demo_summary", "demo_sop"} and c.is_demo is True


def test_retrieval_finds_expected_sections(initialized):
    settings, _ = initialized
    idx = load_index(settings.index_dir)
    cases = {
        "QC 失败的分析批应该怎么处理": "DEMO-SOP-QC-004",
        "偏差关闭需要什么条件": "DEMO-SOP-DEV-004",
        "报告中缺失的数据怎么处理": "DEMO-SOP-REPORT-003",
    }
    for q, expected in cases.items():
        assert expected in [h.chunk.section_id for h in idx.search_bm25(q, 3)], (q, "bm25")
        assert expected in [h.chunk.section_id for h in idx.search_vector(q, 5)], (q, "vector")


def test_vector_index_is_reproducible(tmp_path):
    outs = []
    for n in (1, 2):
        s = Settings(database_url=f"sqlite:///{(tmp_path / f'k{n}.db').as_posix()}",
                     index_dir=str(tmp_path / f"i{n}"), knowledge_dir=str(ROOT / "knowledge"))
        initialize(s)
        outs.append(load_index(s.index_dir).doc_vecs)
    assert (outs[0] == outs[1]).all()


def test_no_real_clause_numbers_in_knowledge():
    """知识库不得出现“第X条”式真实条款引用。"""
    import re
    for p in (ROOT / "knowledge").glob("*.md"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"第\s*\d+(\.\d+)*\s*条", text), p.name
        assert "教学演示" in text or "模拟内部" in text, p.name
