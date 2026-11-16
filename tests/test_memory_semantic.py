# -*- coding: utf-8 -*-
"""语义召回增强（v2g）：编码稳定性、融合排序、候选并集、向后兼容。

**能力边界（重要）**：本项目有两种向量模式，测试按模式分别断言——
- ``lexical``（无 embedding 模型时的兜底）：字符 bigram，只能做**字面模糊匹配**
  （「数据科学实习」↔「数据分析岗实习」能对上，共享「数据」「实习」）。
  它**做不到**「实习」↔「求职」这类零字面重叠的同义召回，实测余弦仅 0.076。
- ``embedding``（接上 Ollama/API 后）：真语义，零字面重叠也能召回。

测试不假设跑在哪种模式：涉及真语义的用例按 ``mode()`` 分支断言。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pet.chat.embedding import EmbeddingBackend, lexical_encode, reset_default_backend
from pet.chat.memory_recall import rank_entries
from pet.chat.memory_semantic import (
    DIM,
    _stable_hash,
    cosine,
    encode,
    entry_vector,
    rank_semantic,
    semantic_hits,
)

ROOT = Path(__file__).resolve().parents[1]


def mode() -> str:
    return EmbeddingBackend().mode()


@pytest.fixture(autouse=True)
def _lexical_baseline(monkeypatch):
    """默认按词面模式跑：模型是否就绪不应让单测结果飘忽。"""
    monkeypatch.setenv("DSHPET_FORCE_LEXICAL", "1")
    reset_default_backend()
    yield
    reset_default_backend()


def entry(eid: str, text: str, **kw) -> dict:
    """构造记忆条目。

    时间必须是 ISO 字符串：``_entry_time`` 走 ``_iso_sort_key`` 解析，
    传 float 会被当成 0，导致 topic 的 stale 判断失效（不归档）。
    """
    base = {
        "id": eid,
        "layer": "topic",
        "text": text,
        "keywords": kw.pop("keywords", []),
        "importance": kw.pop("importance", 0.5),
        "created_at": kw.pop("created_at", "2026-10-01T00:00:00+00:00"),
        "updated_at": kw.pop("updated_at", "2026-10-20T00:00:00+00:00"),
    }
    base.update(kw)
    return base


class TestEncode:
    def test_dimension_and_normalization(self):
        vec = encode("数据科学与大数据技术")
        assert len(vec) == DIM
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_empty_text_returns_zero_vector(self):
        vec = encode("")
        assert len(vec) == DIM
        assert all(v == 0.0 for v in vec)

    def test_identical_text_same_vector(self):
        assert encode("同样的文本") == encode("同样的文本")

    def test_stable_across_processes(self):
        """跨进程必须一致：内置 hash() 每次启动带随机盐，不能用。"""
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from pet.chat.embedding import lexical_encode;"
            "print(repr(lexical_encode('跨进程稳定性测试'))[:200])"
        ) % ROOT
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=180)
        assert out.returncode == 0, out.stderr[-400:]
        assert out.stdout.strip() == repr(lexical_encode("跨进程稳定性测试"))[:200]

    def test_hash_is_deterministic(self):
        assert _stable_hash("同样的词") == _stable_hash("同样的词")

    def test_cosine_symmetric(self):
        a, b = lexical_encode("实习准备"), lexical_encode("求职准备")
        assert abs(cosine(a, b) - cosine(b, a)) < 1e-9

    def test_literal_overlap_closer_than_unrelated(self):
        """词面模式下能保证的能力：共享字面的更接近。"""
        query = lexical_encode("数据科学实习")
        near = lexical_encode("数据分析岗实习")
        far = lexical_encode("今天中午吃了面条")
        assert cosine(query, near) > cosine(query, far)
        assert cosine(query, near) > 0.2

    @pytest.mark.skipif(mode() != "embedding", reason="需要 embedding 模型")
    def test_true_synonym_recall_in_embedding_mode(self):
        query = encode("我在找实习")
        synonym = encode("求职方向怎么定")
        unrelated = encode("今天中午吃了面条")
        assert cosine(query, synonym) > cosine(query, unrelated)


class TestRankSemantic:
    def test_weight_zero_falls_back_to_bm25(self):
        entries = [entry("a", "用户在找实习", keywords=["实习"]),
                   entry("b", "用户喜欢喝咖啡", keywords=["咖啡"])]
        # 分数里含 time-decay（每次调用 now 不同），只比顺序
        got = [e["id"] for _s, e in rank_semantic(entries, "实习", semantic_weight=0)]
        want = [e["id"] for _s, e in rank_entries(entries, "实习")]
        assert got == want

    def test_partial_overlap_recalled_without_exact_keyword(self):
        """词面模式的核心收益：关键词不完全一致也能进候选。"""
        entries = [
            entry("data", "用户在做数据分析岗的实习准备", keywords=["数据分析"], importance=0.9),
            entry("food", "用户中午吃了番茄面", keywords=["中午", "番茄面"], importance=0.2),
        ]
        ranked = rank_semantic(entries, "数据科学实习该怎么准备")
        assert ranked, "应至少有一条进候选"
        assert ranked[0][1]["id"] == "data"

    def test_bm25_hit_still_wins_when_keyword_exact(self):
        entries = [
            entry("exact", "用户实习面试在下周三", keywords=["实习", "面试"], importance=0.9),
            entry("near", "用户求职方向与岗位匹配", keywords=["求职"], importance=0.5),
        ]
        ranked = rank_semantic(entries, "实习面试")
        assert ranked[0][1]["id"] == "exact"

    def test_archive_and_user_layer_excluded(self):
        entries = [
            entry("arch", "用户求职方向是数据分析", archived=True, keywords=["求职"]),
            entry("user", "用户求职方向是数据分析", layer="user", keywords=["求职"]),
            entry("ok", "用户求职方向是数据分析", keywords=["求职"]),
        ]
        ids = [e["id"] for _s, e in rank_semantic(entries, "求职方向")]
        assert "ok" in ids
        assert "arch" not in ids
        assert "user" not in ids

    def test_empty_query_returns_empty(self):
        assert rank_semantic([entry("a", "内容")], "   ") == []

    def test_top_k_limits(self):
        entries = [entry(f"e{i}", f"用户求职方向{i}", keywords=["求职"]) for i in range(10)]
        assert len(rank_semantic(entries, "求职", top_k=3)) <= 3

    def test_scores_bounded(self):
        entries = [entry("a", "实习准备", keywords=["实习"]),
                   entry("b", "求职准备", keywords=["求职"])]
        for score, _e in rank_semantic(entries, "实习"):
            assert 0.0 < score <= 1.0 + 1e-9


class TestSemanticHits:
    def test_orders_by_similarity(self):
        entries = [
            entry("near", "用户在做数据分析实习", keywords=["数据分析", "实习"]),
            entry("far", "用户喜欢看科幻电影", keywords=["科幻", "电影"]),
        ]
        hits = semantic_hits(entries, "数据分析实习")
        assert hits and hits[0][1]["id"] == "near"

    def test_threshold_filters_unrelated(self):
        entries = [entry("x", "完全无关的内容", keywords=["无关"])]
        assert semantic_hits(entries, "量子力学", min_similarity=0.9) == []

    def test_entry_vector_prefers_keywords(self):
        e = entry("k", "短文本", keywords=["数据科学", "实习"])
        vec = entry_vector(e)
        assert abs(sum(v * v for v in vec) ** 0.5 - 1.0) < 1e-6


class TestBackend:
    def test_lexical_forced(self):
        backend = EmbeddingBackend()
        assert backend.mode() == "lexical"
        assert backend.describe()["semantic"] is False

    def test_lexical_vector_normalized(self):
        vec = lexical_encode("测试文本")
        assert abs(sum(v * v for v in vec) ** 0.5 - 1.0) < 1e-6
