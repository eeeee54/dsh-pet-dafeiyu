"""本地知识库：文档导入 → 切片 → 向量索引 → 检索增强。

## 与记忆的区别

| | 记忆（`pet/chat/memory*`） | 知识库（本模块） |
|---|---|---|
| 数据来源 | 由对话自动提炼 | 用户主动导入的文档 |
| 结构 | 短句条目（fact/lesson/topic） | 长文切片（chunk） |
| 召回目的 | 「记得这个人」 | 「查得到这份资料」 |
| 生命周期 | 会衰减、会归档 | 常驻，手动维护 |

两者共用同一套向量编码（`memory_semantic.encode`），所以语义空间一致，
可以在一次对话里同时注入而不会互相打架。

## 切片策略

1. 优先按 Markdown 标题/编号切节
2. 单节超长按句子切，保留重叠，避免答案被切断在边界
3. 每片前面拼文档标题——「标题即主题」的查询（"调优手册里怎么说"）靠它命中
4. 过短碎片向后合并，防止碎渣污染索引

## 存储

``<data>/kb/docs.json`` + ``<data>/kb/chunks.json``，原子写。
向量随切片一起落盘，读取时不需要重新编码（否则打开就是几秒卡顿）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .memory_semantic import cosine, encode

DEFAULT_CHUNK_SIZE = 420
DEFAULT_OVERLAP = 60
MIN_CHUNK_CHARS = 40
_HEADING = re.compile(r"^(?:#{1,4}\s+|([一二三四五六七八九十]+、)|(\d+[.、]))", re.MULTILINE)


@dataclass
class KbChunk:
    id: str
    doc_id: str
    title: str
    text: str
    tags: list[str] = field(default_factory=list)
    order: int = 0
    vector: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KbChunk":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class KbDocument:
    id: str
    title: str
    tags: list[str] = field(default_factory=list)
    source: str = ""
    chunk_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KbDocument":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


def split_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """把长文本切成检索单元（标题 > 句子 > 合并碎片）。"""
    body = str(text or "").replace("\r\n", "\n").strip()
    if not body:
        return []

    starts = [m.start() for m in _HEADING.finditer(body)]
    if starts:
        sections: list[str] = []
        if starts[0] > 0:
            sections.append(body[:starts[0]])
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(body)
            sections.append(body[start:end])
    else:
        sections = [body]

    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append(section)
            continue
        buffer = ""
        for sentence in re.split(r"(?<=[。！？!?\n])", section):
            if not sentence:
                continue
            if len(buffer) + len(sentence) > chunk_size and buffer:
                chunks.append(buffer)
                buffer = (buffer[-overlap:] if overlap else "") + sentence
            else:
                buffer += sentence
        if buffer.strip():
            chunks.append(buffer)

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_CHARS:
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)
    return [c.strip() for c in merged if c.strip()]


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


class KnowledgeBase:
    """线程安全的知识库；所有写操作走原子替换。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self.docs: list[KbDocument] = []
        self.chunks: list[KbChunk] = []
        self.load()

    # ---------------------------------------------------------------- 持久化
    @property
    def docs_path(self) -> Path:
        return self.root / "kb" / "docs.json"

    @property
    def chunks_path(self) -> Path:
        return self.root / "kb" / "chunks.json"

    def load(self) -> None:
        with self._lock:
            self.docs = []
            self.chunks = []
            for path, cls, target in ((self.docs_path, KbDocument, "docs"),
                                      (self.chunks_path, KbChunk, "chunks")):
                if not path.exists():
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for item in raw.get("items", []):
                    try:
                        getattr(self, target).append(cls.from_dict(item))
                    except (TypeError, ValueError):
                        continue

    def save(self) -> None:
        with self._lock:
            _atomic_write(self.docs_path, {"version": 1, "items": [d.to_dict() for d in self.docs]})
            _atomic_write(self.chunks_path, {"version": 1, "items": [c.to_dict() for c in self.chunks]})

    # ---------------------------------------------------------------- 导入
    def add_document(self, title: str, text: str, tags: Iterable[str] | None = None,
                     source: str = "", chunk_size: int = DEFAULT_CHUNK_SIZE) -> KbDocument:
        title = (str(title or "").strip() or "未命名")[:80]
        pieces = split_text(text, chunk_size=chunk_size)
        doc = KbDocument(id=f"doc_{uuid.uuid4().hex[:12]}", title=title,
                         tags=[str(t) for t in (tags or [])], source=str(source),
                         chunk_count=len(pieces))
        new_chunks: list[KbChunk] = []
        for index, piece in enumerate(pieces):
            # 标题拼进正文：让「某文档里怎么讲」这类查询能命中
            content = f"{title}：{piece}" if piece else title
            new_chunks.append(KbChunk(
                id=f"chk_{uuid.uuid4().hex[:12]}", doc_id=doc.id, title=title,
                text=content, tags=list(doc.tags), order=index,
                vector=list(encode(content))))
        with self._lock:
            self.docs.append(doc)
            self.chunks.extend(new_chunks)
            self.save()
        return doc

    def add_file(self, path: str | Path, tags: Iterable[str] | None = None) -> KbDocument | None:
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return self.add_document(p.stem, text, tags=tags, source=str(p))

    def add_directory(self, directory: str | Path, pattern: str = "*.md",
                      tags: Iterable[str] | None = None) -> list[KbDocument]:
        added: list[KbDocument] = []
        for p in sorted(Path(directory).glob(pattern)):
            doc = self.add_file(p, tags=tags)
            if doc is not None:
                added.append(doc)
        return added

    # ---------------------------------------------------------------- 管理
    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            before = len(self.docs)
            self.docs = [d for d in self.docs if d.id != doc_id]
            self.chunks = [c for c in self.chunks if c.doc_id != doc_id]
            if len(self.docs) == before:
                return False
            self.save()
            return True

    def update_tags(self, doc_id: str, tags: Iterable[str]) -> bool:
        with self._lock:
            for doc in self.docs:
                if doc.id == doc_id:
                    doc.tags = [str(t) for t in tags]
                    for chunk in self.chunks:
                        if chunk.doc_id == doc_id:
                            chunk.tags = list(doc.tags)
                    self.save()
                    return True
        return False

    def rebuild_index(self) -> int:
        with self._lock:
            for chunk in self.chunks:
                chunk.vector = list(encode(chunk.text))
            self.save()
            return len(self.chunks)

    def clear(self) -> int:
        with self._lock:
            count = len(self.docs)
            self.docs, self.chunks = [], []
            self.save()
            return count

    # ---------------------------------------------------------------- 检索
    def search(self, query: str, top_k: int = 4, tags: Iterable[str] | None = None,
               min_similarity: float = 0.18) -> list[tuple[float, KbChunk]]:
        query = str(query or "").strip()
        if not query:
            return []
        query_vec = encode(query)
        tag_filter = {str(t) for t in (tags or [])}
        with self._lock:
            chunks = list(self.chunks)
        scored: list[tuple[float, KbChunk]] = []
        for chunk in chunks:
            if tag_filter and not (tag_filter & set(chunk.tags)):
                continue
            vec = chunk.vector or list(encode(chunk.text))
            sim = cosine(tuple(vec), query_vec)
            if sim >= min_similarity:
                scored.append((sim, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:max(0, int(top_k))]

    def build_context_block(self, query: str, top_k: int = 3,
                            char_limit: int = 900) -> str:
        """渲染成可注入 prompt 的段落；无命中返回空串。"""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        lines = ["【知识库里有这些相关内容】"]
        used = 0
        for _score, chunk in hits:
            body = chunk.text
            prefix = f"{chunk.title}："
            if body.startswith(prefix):
                body = body[len(prefix):]
            entry = f"- [{chunk.title}] {body[:280]}"
            if used + len(entry) > char_limit:
                break
            lines.append(entry)
            used += len(entry)
        if len(lines) == 1:
            return ""
        lines.append("（可以引用这些内容，但要转述成自己的话，不要说「根据资料库」。）")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 统计
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "documents": len(self.docs),
                "chunks": len(self.chunks),
                "chars": sum(len(c.text) for c in self.chunks),
                "docs": [{"id": d.id, "title": d.title, "tags": d.tags,
                          "chunks": d.chunk_count} for d in self.docs],
            }
