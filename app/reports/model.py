"""报告中间表示：事实（Fact）、句子、表格、章节。

构建（builder）→ 校验（checks）→ 渲染（docx_render）三步分离：渲染层不做任何计算。
"""
from dataclasses import dataclass, field

MISSING_PREFIX = "[待补充: "


def placeholder(field_name: str) -> str:
    return f"{MISSING_PREFIX}{field_name}]"


@dataclass
class Fact:
    key: str
    label: str
    value: object              # 原始值（None 表示缺失）
    display: str | None        # 写入报告的文本；缺失时为 None
    source_table: str
    source_rows: list[str]
    rule_id: str | None = None
    unit: str = ""
    index: int = 0             # 附录编号 F<index>

    @property
    def tag(self) -> str:
        return f"F{self.index}"

    def to_dict(self) -> dict:
        return {"tag": self.tag, "key": self.key, "label": self.label, "value": self.value,
                "display": self.display, "unit": self.unit, "source_table": self.source_table,
                "source_rows": self.source_rows, "rule_id": self.rule_id}


class FactStore:
    def __init__(self):
        self._facts: dict[str, Fact] = {}

    def add(self, key: str, label: str, value, *, source_table: str, source_rows: list[str],
            rule_id: str | None = None, unit: str = "", display: str | None = None) -> Fact:
        if key in self._facts:
            raise KeyError(f"事实重复：{key}")
        if display is None and value is not None:
            display = str(value)
        f = Fact(key, label, value, display, source_table, source_rows, rule_id, unit, len(self._facts) + 1)
        self._facts[key] = f
        return f

    def get(self, key: str) -> Fact:
        return self._facts[key]

    def __contains__(self, key: str) -> bool:
        return key in self._facts

    def all(self) -> list[Fact]:
        return list(self._facts.values())


@dataclass
class Sentence:
    """kind: fact=业务结论（必须有事实来源）；static=说明性文字（不得含数字）；placeholder=缺项占位。"""
    text: str
    kind: str
    fact_keys: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    bid: str = ""                      # 块编号，跨版本稳定，供审核意见与编辑引用
    edited: bool = False               # 人工修订过（original_text 保留生成时原文）
    original_text: str | None = None
    flag: str | None = None            # 非空 = 修订后无法对应来源，须处理后才能审核通过


@dataclass
class TableBlock:
    caption: str
    source: str
    header: list[str]
    rows: list[list[str]]
    bid: str = ""


@dataclass
class Section:
    title: str
    blocks: list = field(default_factory=list)  # Sentence | TableBlock


@dataclass
class ReportDoc:
    kind: str
    title: str
    project_id: str
    run_ids: list[str]
    sections: list[Section]
    facts: FactStore
    missing_fields: list[str]
    rule_ids: list[str]

    def sentences(self) -> list[Sentence]:
        return [b for s in self.sections for b in s.blocks if isinstance(b, Sentence)]


# ---------- 序列化：审核流程保存与还原报告内容（事实表另存于 ReportDraft.facts_json，审核期间不可变） ----------
def assign_block_ids(doc: ReportDoc) -> None:
    n = 0
    for sec in doc.sections:
        for blk in sec.blocks:
            n += 1
            if not blk.bid:
                blk.bid = f"B{n:03d}"


def doc_to_content(doc: ReportDoc) -> dict:
    sections = []
    for sec in doc.sections:
        blocks = []
        for b in sec.blocks:
            if isinstance(b, TableBlock):
                blocks.append({"type": "table", "bid": b.bid, "caption": b.caption, "source": b.source,
                               "header": b.header, "rows": b.rows})
            else:
                blocks.append({"type": "sentence", "bid": b.bid, "text": b.text, "kind": b.kind,
                               "fact_keys": b.fact_keys, "missing": b.missing, "edited": b.edited,
                               "original_text": b.original_text, "flag": b.flag})
        sections.append({"title": sec.title, "blocks": blocks})
    return {"kind": doc.kind, "title": doc.title, "project_id": doc.project_id, "run_ids": doc.run_ids,
            "missing_fields": doc.missing_fields, "rule_ids": doc.rule_ids, "sections": sections}


def facts_from_dicts(facts_json: list[dict]) -> FactStore:
    store = FactStore()
    for f in facts_json:                       # facts_json 按编号顺序保存，重新登记即可得到相同的 F<index>
        store.add(f["key"], f["label"], f["value"], source_table=f["source_table"],
                  source_rows=f["source_rows"], rule_id=f["rule_id"], unit=f["unit"], display=f["display"])
        if f["display"] is None:
            store.get(f["key"]).display = None
    return store


def doc_from_content(content: dict, facts_json: list[dict]) -> ReportDoc:
    sections = []
    for sec in content["sections"]:
        blocks = []
        for b in sec["blocks"]:
            if b["type"] == "table":
                blocks.append(TableBlock(b["caption"], b["source"], b["header"], b["rows"], b["bid"]))
            else:
                blocks.append(Sentence(b["text"], b["kind"], list(b["fact_keys"]), list(b["missing"]), b["bid"],
                                       b["edited"], b["original_text"], b["flag"]))
        sections.append(Section(sec["title"], blocks))
    return ReportDoc(content["kind"], content["title"], content["project_id"], content["run_ids"], sections,
                     facts_from_dicts(facts_json), content["missing_fields"], content["rule_ids"])
