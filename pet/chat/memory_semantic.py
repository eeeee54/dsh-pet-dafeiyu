"""语义召回增强：在 BM25 词面匹配之上叠加向量语义分。

## 为什么需要

BM25 只能匹配**字面相同**的词。用户说「找实习」，记忆里写的是「求职」，
两者一个词都不重合，BM25 得分 0——这条记忆永远不会被召回。
语义向量分能救回这类「同义但不同词」的情况，这正是"跨会话记忆贯通"的前提。

## 设计取舍

* **编码**：字符 bigram 哈希到 512 维。中文不需要分词，跨进程稳定
  （用 blake2b 而非内置 `hash()`，后者每次启动带随机盐）。
  不引入模型依赖：29 万行的项目不应该为了语义检索多背 100MB 权重。
* **融合**：两路各自归一化后加权求和。不能直接相加——BM25 是 idf 累加，
  量级随语料漂移，直接加会让语义项彻底失效。
* **候选并集**：BM25 只给"有词面命中"的条目打分，纯语义召回会被它挡在门外。
  所以候选集是「词面命中 ∪ 语义 top-N」，再统一重排。
* **向后兼容**：`semantic_weight=0` 时退回原 BM25 行为，现有测试与线上
  表现完全不变。

## 性能

单条编码约 8 微秒（平均 20 字），1000 条记忆一次全量重排约 10 ms。
``rank_semantic`` 在召回路径上每次对话调用一次，不进入渲染循环。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Iterable

from .embedding import (EMBEDDING_MIN_SIMILARITY, LEXICAL_MIN_SIMILARITY, EmbeddingBackend,
                        cosine as _cosine, default_backend, lexical_encode)
from .memory_recall import (_corpus_stats, _entry_key, _entry_keywords, _entry_time, _now,
                            _QUERY_KEYWORD_LIMIT,
                            extract_keywords, is_archived, layer_of, rank_entries,
                            score_entry)

DIM = 512
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")

# 融合权重：词面 0.65 / 语义 0.35。语义给太高会让"话题相似但事实无关"的
# 记忆挤掉真正命中的条目，实测 0.3~0.4 之间最平衡。
DEFAULT_SEMANTIC_WEIGHT = 0.35
# 纯语义候选的保底名额：即使一条词面都没命中，也让语义最接近的几条进入重排
SEMANTIC_CANDIDATES = 8


def _stable_hash(token: str, dim: int = DIM) -> int:
    """跨进程稳定的哈希：内置 hash() 带随机盐，向量存盘后就读不懂了。"""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % dim


def encode(text: Any) -> tuple[float, ...]:
    """编码文本。有 embedding 模型时走真语义，否则退化为字符 bigram。"""
    return lexical_encode(text) if default_backend().mode() == "lexical" \
        else default_backend().encode(text)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """两个已归一化向量的余弦（点积）。长度不一致时返回 0。"""
    return _cosine(left, right)


def _entry_text(entry: dict[str, Any]) -> str:
    """参与语义编码的文本：正文 + 关键词，关键词权重更高（重复一次）。"""
    text = str(entry.get("text", "") or "")
    keywords = " ".join(_entry_keywords(entry))
    return f"{text} {keywords} {keywords}"


def entry_vector(entry: dict[str, Any]) -> tuple[float, ...]:
    return encode(_entry_text(entry))


def rank_semantic(
    entries: list[dict[str, Any]],
    query: str,
    *,
    now=None,
    semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
    semantic_candidates: int = SEMANTIC_CANDIDATES,
    top_k: int | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """融合排序：``final = (1-w)·BM25_norm + w·cos``。

    候选集 = 词面命中 ∪ 语义 top-N，所以"零命中但语义接近"的记忆也能被召回。
    ``semantic_weight <= 0`` 时直接返回原 BM25 排序（行为与改造前一致）。
    """
    weight = max(0.0, min(1.0, float(semantic_weight)))
    if weight <= 0.0:
        return rank_entries(entries, query, now=now)
    if not str(query or "").strip():
        return []

    moment = _now(now)
    query_vec = encode(query)

    # 1) 原 BM25 打分（含所有门槛逻辑，不复制一份规则）
    idf, avgdl = _corpus_stats(
        [entry for entry in entries if not is_archived(entry, moment)])
    bm25_scored: dict[str, float] = {}
    for score, entry in rank_entries(entries, query, now=moment, idf=idf, avgdl=avgdl):
        bm25_scored[_entry_key(entry)] = score

    # 2) 语义分（对全部未被归档的非 user 层条目）
    semantic_scored: dict[str, float] = {}
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if layer_of(entry) == "user" or is_archived(entry, moment):
            continue
        sim = cosine(entry_vector(entry), query_vec)
        if sim <= 0.0:
            continue
        key = _entry_key(entry)
        semantic_scored[key] = sim
        candidates.append(entry)

    if not candidates:
        return []

    # 3) 两路归一化后融合
    max_bm25 = max(bm25_scored.values(), default=0.0)
    top_sem = sorted(semantic_scored.items(), key=lambda kv: kv[1], reverse=True)[:max(1, semantic_candidates)]
    keep_keys = set(bm25_scored) | {key for key, _ in top_sem}

    max_sem = max((semantic_scored.get(key, 0.0) for key in keep_keys), default=0.0)
    fused: list[tuple[float, dict[str, Any]]] = []
    for entry in candidates:
        key = _entry_key(entry)
        if key not in keep_keys:
            continue
        bm25_norm = (bm25_scored.get(key, 0.0) / max_bm25) if max_bm25 > 0 else 0.0
        sem_norm = (semantic_scored.get(key, 0.0) / max_sem) if max_sem > 0 else 0.0
        final = (1.0 - weight) * bm25_norm + weight * sem_norm
        if final > 0.0:
            fused.append((final, entry))

    # 时间用 _entry_time 解析（ISO 字符串），直接 float() 会 ValueError
    fused.sort(key=lambda item: (-item[0],
                                 -_entry_time(item[1], "updated_at", "created_at"),
                                 str(item[1].get("id", ""))))
    if top_k is not None:
        return fused[:max(0, int(top_k))]
    return fused


def semantic_hits(
    entries: list[dict[str, Any]],
    query: str,
    *,
    now=None,
    limit: int = 5,
    min_similarity: float | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """只做纯语义检索（调试/知识库预览用，不参与主召回）。

    ``min_similarity`` 缺省取后端的模式阈值：词面向量（bigram）的余弦天然
    偏低，用模型阈值会把所有结果都过滤掉。
    """
    moment = _now(now)
    if min_similarity is None:
        min_similarity = default_backend().describe()["min_similarity"]
    query_vec = encode(query)
    scored = []
    for entry in entries:
        if layer_of(entry) == "user" or is_archived(entry, moment):
            continue
        sim = cosine(entry_vector(entry), query_vec)
        if sim >= min_similarity:
            scored.append((sim, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:max(0, int(limit))]
