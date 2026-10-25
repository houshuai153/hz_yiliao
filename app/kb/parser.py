"""知识文档解析与切分。保留文档 ID / 版本 / 章节 ID / 标题 / 来源类型。

约束：目前仓库没有可核验的官方原文，因此只接受 is_demo=true 的教学演示摘要，
章节 ID 必须是 DEMO-* 且带文档声明的 section_prefix。
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SECTION_RE = re.compile(r"^## \[([^\]]+)\][ \t]*(.+?)[ \t]*$", re.M)
SECTION_ID_RE = re.compile(r"^DEMO-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}$")
ALLOWED_SOURCE_TYPES = {"demo_summary", "demo_sop"}
MAX_CHUNK_CHARS = 400


class KnowledgeFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SectionData:
    section_id: str
    doc_id: str
    version: str
    heading: str
    text: str
    position: int
    source_type: str
    is_demo: bool


@dataclass(frozen=True)
class DocumentData:
    doc_id: str
    version: str
    title: str
    source_type: str
    is_demo: bool
    disclaimer: str
    content_sha256: str
    sections: tuple[SectionData, ...]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    section_id: str
    doc_id: str
    version: str
    heading: str
    source_type: str
    is_demo: bool
    text: str


def parse_text(raw: str, label: str = "<memory>") -> DocumentData:
    norm = raw.replace("\r\n", "\n")
    sha = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    if not norm.startswith("---\n"):
        raise KnowledgeFormatError(f"{label}: 缺少 YAML 头")
    end = norm.find("\n---\n", 4)
    if end < 0:
        raise KnowledgeFormatError(f"{label}: YAML 头未闭合")
    meta = yaml.safe_load(norm[4:end]) or {}
    body = norm[end + 5:]
    for key in ("doc_id", "section_prefix", "version", "title", "source_type", "is_demo", "disclaimer"):
        if key not in meta:
            raise KnowledgeFormatError(f"{label}: YAML 头缺少 {key}")
    if meta["source_type"] not in ALLOWED_SOURCE_TYPES:
        raise KnowledgeFormatError(f"{label}: source_type 必须是 {sorted(ALLOWED_SOURCE_TYPES)}")
    if meta["is_demo"] is not True:
        raise KnowledgeFormatError(f"{label}: 当前仅接受 is_demo=true 的教学演示材料")
    prefix = str(meta["section_prefix"])

    matches = list(SECTION_RE.finditer(body))
    if not matches:
        raise KnowledgeFormatError(f"{label}: 未找到任何 `## [DEMO-xxx-nnn] 标题` 章节")
    sections, seen = [], set()
    for i, m in enumerate(matches):
        sid, heading = m.group(1), m.group(2)
        if not SECTION_ID_RE.match(sid) or not sid.startswith(prefix + "-"):
            raise KnowledgeFormatError(f"{label}: 章节 ID {sid!r} 必须是以 {prefix}- 开头的 DEMO-* 编号")
        if sid in seen:
            raise KnowledgeFormatError(f"{label}: 章节 ID 重复 {sid}")
        seen.add(sid)
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[m.end():stop].strip()
        if not text:
            raise KnowledgeFormatError(f"{label}: 章节 {sid} 内容为空")
        sections.append(SectionData(
            sid, str(meta["doc_id"]), str(meta["version"]), heading, text, i,
            meta["source_type"], True,
        ))
    return DocumentData(
        str(meta["doc_id"]), str(meta["version"]), str(meta["title"]), meta["source_type"],
        True, str(meta["disclaimer"]).strip(), sha, tuple(sections),
    )


def parse_dir(directory: str | Path) -> list[DocumentData]:
    docs = [parse_text(p.read_text(encoding="utf-8"), p.name) for p in sorted(Path(directory).glob("*.md"))]
    ids = [d.doc_id for d in docs]
    if len(ids) != len(set(ids)):
        raise KnowledgeFormatError("doc_id 重复")
    all_sections = [s.section_id for d in docs for s in d.sections]
    if len(all_sections) != len(set(all_sections)):
        raise KnowledgeFormatError("跨文档章节 ID 重复")
    return docs


def chunk_section(section: SectionData, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """章节不超过上限则整体一块；否则按段落合并，块 ID 为 `章节ID#序号`，章节 ID 始终保留。"""
    def mk(cid: str, text: str) -> Chunk:
        return Chunk(cid, section.section_id, section.doc_id, section.version, section.heading,
                     section.source_type, section.is_demo, text)

    if len(section.text) <= max_chars:
        return [mk(section.section_id, section.text)]
    parts, cur = [], ""
    for para in (p.strip() for p in section.text.split("\n\n") if p.strip()):
        if cur and len(cur) + len(para) + 2 > max_chars:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    if len(parts) == 1:
        return [mk(section.section_id, parts[0])]
    return [mk(f"{section.section_id}#{i + 1}", t) for i, t in enumerate(parts)]
