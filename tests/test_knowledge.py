# -*- coding: utf-8 -*-
"""知识库（v2h）：切片、导入、检索、标签过滤、原子持久化。

与「记忆」不同，知识库是用户主动导入的文档，只由用户增删，不参与衰减。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pet.chat.knowledge import (DEFAULT_CHUNK_SIZE, KnowledgeBase, KbChunk, KbDocument,
                                split_text)


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(tmp_path)


class TestSplit:
    def test_heading_sections_split(self):
        para = "这一段说明该章节的具体内容，用于占位。" * 3
        text = f"# 第一章\n{para}\n# 第二章\n{para}"
        chunks = split_text(text, chunk_size=500)
        assert len(chunks) >= 2
        assert any("第一章" in c for c in chunks)
        assert any("第二章" in c for c in chunks)

    def test_short_sections_merged(self):
        chunks = split_text("# 甲\n一句话。\n# 乙\n一句话。", chunk_size=500)
        assert len(chunks) == 1, "过短碎片应合并，避免污染索引"

    def test_long_text_split_with_overlap(self):
        chunks = split_text("这是一句话。" * 200, chunk_size=200, overlap=40)
        assert len(chunks) > 3
        assert all(len(c) <= 400 for c in chunks)

    def test_empty_returns_empty(self):
        assert split_text("") == []
        assert split_text("   \n\n  ") == []

    def test_custom_chunk_size_respected(self):
        chunks = split_text("句子。" * 300, chunk_size=100, overlap=10)
        assert all(len(c) <= 200 for c in chunks)


class TestImport:
    def test_add_document_creates_chunks(self, kb: KnowledgeBase):
        doc = kb.add_document("Spark 调优", "数据倾斜时给热点 key 加随机前缀。" * 5,
                              tags=["大数据"])
        assert doc.chunk_count >= 1
        assert kb.stats()["documents"] == 1
        assert kb.stats()["chunks"] == doc.chunk_count

    def test_chunk_carries_title_prefix(self, kb: KnowledgeBase):
        kb.add_document("记忆体系", "短期记忆归档为片段。")
        assert any(c.text.startswith("记忆体系：") for c in kb.chunks)

    def test_chunk_has_vector(self, kb: KnowledgeBase):
        kb.add_document("向量", "内容。" * 20)
        assert all(len(c.vector) > 0 for c in kb.chunks)

    def test_add_file(self, kb: KnowledgeBase, tmp_path: Path):
        p = tmp_path / "笔记.md"
        p.write_text("# 标题\n正文内容。" * 10, encoding="utf-8")
        doc = kb.add_file(p, tags=["笔记"])
        assert doc is not None and doc.title == "笔记"

    def test_add_missing_file_returns_none(self, kb: KnowledgeBase):
        assert kb.add_file("/definitely/not/exists.md") is None

    def test_add_directory(self, kb: KnowledgeBase, tmp_path: Path):
        for i in range(3):
            (tmp_path / f"d{i}.md").write_text(f"文档{i}的内容。" * 10, encoding="utf-8")
        added = kb.add_directory(tmp_path, pattern="*.md")
        assert len(added) == 3
        assert kb.stats()["documents"] == 3


class TestSearch:
    def test_search_returns_hits(self, kb: KnowledgeBase):
        kb.add_document("Spark 调优手册",
                        "数据倾斜时可以对热点 key 加随机前缀做两阶段聚合。\n"
                        "小表广播可以避免 shuffle。" * 3)
        hits = kb.search("数据倾斜怎么处理", top_k=2)
        assert hits
        assert "倾斜" in hits[0][1].text

    def test_empty_query_returns_empty(self, kb: KnowledgeBase):
        kb.add_document("x", "内容。" * 20)
        assert kb.search("   ") == []

    def test_tag_filter(self, kb: KnowledgeBase):
        kb.add_document("A 文档", "关于向量检索的内容。" * 8, tags=["rag"])
        kb.add_document("B 文档", "关于前端布局的内容。" * 8, tags=["web"])
        hits = kb.search("内容", top_k=5, tags=["rag"])
        assert hits
        assert all("rag" in h[1].tags for h in hits)

    def test_min_similarity_filters(self, kb: KnowledgeBase):
        kb.add_document("无关", "完全不相干的内容。" * 10)
        assert kb.search("量子纠缠", min_similarity=0.9) == []

    def test_top_k_limits(self, kb: KnowledgeBase):
        for i in range(5):
            kb.add_document(f"文档{i}", "共同主题的内容。" * 10, tags=["t"])
        assert len(kb.search("共同主题", top_k=2)) <= 2

    def test_context_block(self, kb: KnowledgeBase):
        kb.add_document("调优手册", "数据倾斜用加盐打散。" * 10)
        block = kb.build_context_block("数据倾斜")
        assert "知识库" in block
        assert "不要说「根据资料库」" in block

    def test_context_block_empty_when_no_hit(self, kb: KnowledgeBase):
        assert kb.build_context_block("完全无关的查询词") == ""


class TestPersist:
    def test_reload_keeps_documents(self, tmp_path: Path):
        kb = KnowledgeBase(tmp_path)
        kb.add_document("持久化", "这条要能重新读出来。" * 5)
        again = KnowledgeBase(tmp_path)
        assert again.stats()["documents"] == 1
        assert again.chunks[0].vector, "向量应随磁盘一起恢复"

    def test_atomic_write_no_tmp_left(self, tmp_path: Path):
        kb = KnowledgeBase(tmp_path)
        kb.add_document("原子", "内容。" * 10)
        leftovers = [p for p in (tmp_path / "kb").iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    def test_corrupt_file_tolerated(self, tmp_path: Path):
        (tmp_path / "kb").mkdir(parents=True, exist_ok=True)
        (tmp_path / "kb" / "docs.json").write_text("{ not json", encoding="utf-8")
        kb = KnowledgeBase(tmp_path)     # 不应抛异常
        assert kb.stats()["documents"] == 0

    def test_delete_document(self, kb: KnowledgeBase):
        doc = kb.add_document("待删", "一些内容。" * 10)
        assert kb.delete_document(doc.id) is True
        assert kb.stats()["documents"] == 0
        assert kb.stats()["chunks"] == 0
        assert kb.delete_document("nope") is False

    def test_update_tags(self, kb: KnowledgeBase):
        doc = kb.add_document("改标签", "内容。" * 10, tags=["a"])
        assert kb.update_tags(doc.id, ["b", "c"]) is True
        assert all(set(c.tags) == {"b", "c"} for c in kb.chunks)

    def test_rebuild_index(self, kb: KnowledgeBase):
        kb.add_document("重建", "内容。" * 20)
        assert kb.rebuild_index() == len(kb.chunks)

    def test_clear(self, kb: KnowledgeBase):
        kb.add_document("清空", "内容。" * 10)
        assert kb.clear() == 1
        assert kb.stats()["documents"] == 0

    def test_stats_shape(self, kb: KnowledgeBase):
        kb.add_document("统计", "内容。" * 10, tags=["x"])
        stats = kb.stats()
        assert set(stats) >= {"documents", "chunks", "chars", "docs"}
        assert stats["docs"][0]["title"] == "统计"
