"""生成后自检：每条业务结论必须有来源，句中数字必须出自其来源事实。违反即生成失败（不放行）。"""
import re

from app.reports.model import FactStore, ReportDoc, Sentence

_PLACEHOLDER_RE = re.compile(r"\[待补充: [^\]]*\]")
_ID_RE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")   # PRJ-SM-001-R01、CAL-ACC、DEMO-API-101 之类的编号
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class ReportIntegrityError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("；".join(problems))
        self.problems = problems


def numbers_in(text: str) -> list[str]:
    """正文中的数字（去掉缺项占位符与各类编号后）。"""
    return _NUM_RE.findall(_ID_RE.sub("", _PLACEHOLDER_RE.sub("", text)))


def check_sentence(facts: FactStore, s: Sentence) -> list[str]:
    """单句自检，返回问题列表（空 = 通过）。生成自检与审核期间的修订校验共用同一套规则。"""
    preview = s.text[:40]
    if s.kind == "fact":
        if not s.fact_keys:
            return [f"业务结论缺少来源：{preview}"]
        missing = [k for k in s.fact_keys if k not in facts]
        if missing:
            return [f"引用了不存在的事实 {missing}：{preview}"]
        allowed = {n for k in s.fact_keys for n in _NUM_RE.findall(facts.get(k).display or "")}
        stray = [n for n in numbers_in(s.text) if n not in allowed]
        return [f"句中数字 {stray} 不在其来源事实中：{preview}"] if stray else []
    if s.kind == "static":
        return [f"说明性文字不得含数字：{preview}"] if _NUM_RE.search(s.text) else []
    if s.kind == "placeholder":
        return [] if _PLACEHOLDER_RE.search(s.text) else [f"占位句缺少“[待补充: …]”：{preview}"]
    return [f"未知句子类型 {s.kind}"]


def check_report(doc: ReportDoc) -> dict:
    problems: list[str] = []
    sentences = doc.sentences()
    n_fact = unsourced = 0
    for s in sentences:
        found = check_sentence(doc.facts, s)
        if s.kind == "fact":
            n_fact += 1
            if found and (not s.fact_keys or any(k not in doc.facts for k in s.fact_keys)):
                unsourced += 1
        problems.extend(found)
    if problems:
        raise ReportIntegrityError(problems)
    return {"sentence_count": len(sentences), "fact_sentences": n_fact, "unsourced_sentences": unsourced,
            "fact_count": len(doc.facts.all())}
