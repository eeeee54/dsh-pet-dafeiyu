# -*- coding: utf-8 -*-
"""知识库注入链路测试：payload 透传 → prompt 拼接 → 开关生效。

对应需求「知识库检索结果与记忆、当前上下文统一编排后送入大模型」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.chat.knowledge import KnowledgeBase
from pet.chat.memory import MemoryPayload
from pet.chat.models import ChatMessage, ChatSettings
from pet.chat.prompt import PromptBuilder


class Config:
    """最小配置桩：只实现 get，够 EmbeddingBackend 与知识库判断用。"""

    def __init__(self, values: dict | None = None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    base = KnowledgeBase(tmp_path)
    base.add_document("Spark 调优手册",
                      "数据倾斜时给热点 key 加随机前缀，再做两阶段聚合。" * 4)
    base.add_document("无关文档", "今天天气不错，适合出门散步。" * 6)
    return base


class TestKnowledgeInjection:
    def test_context_block_found(self, kb: KnowledgeBase):
        block = kb.build_context_block("数据倾斜怎么处理")
        assert block
        assert "知识库" in block

    def test_context_block_empty_when_no_match(self, kb: KnowledgeBase):
        assert kb.build_context_block("量子纠缠与贝尔不等式") == ""

    def test_payload_carries_knowledge_text(self):
        payload = MemoryPayload(knowledge_text="【知识库】某内容")
        assert payload.knowledge_text == "【知识库】某内容"

    def test_prompt_includes_knowledge_text(self):
        builder = PromptBuilder()
        payload = MemoryPayload(knowledge_text="【知识库里有这些相关内容】\n- X 说明")
        messages = builder.build_messages(
            ChatSettings(), "shenshen",
            [ChatMessage("user", "之前聊过什么")],
            "数据倾斜怎么办",
            memory_payload=payload)
        system = messages[0]["content"]
        assert "知识库里有这些相关内容" in system

    def test_prompt_omits_when_empty(self):
        builder = PromptBuilder()
        payload = MemoryPayload()
        with_kb = builder.build_messages(
            ChatSettings(), "shenshen", [ChatMessage("user", "问")], "问",
            memory_payload=payload)
        without = builder.build_messages(
            ChatSettings(), "shenshen", [ChatMessage("user", "问")], "问")
        assert with_kb[0]["content"] == without[0]["content"], \
            "空知识库时 system 必须与没有 payload 时一致"

    def test_knowledge_after_memory_in_prompt(self):
        """顺序：记忆快照 → 知识库 → 召回段，保证模型先看"这个人"再看"资料"。"""
        builder = PromptBuilder()
        payload = MemoryPayload(text="【关于用户的记忆】\n- 用户是学生",
                                knowledge_text="【知识库】\n- 资料一段")
        messages = builder.build_messages(
            ChatSettings(), "shenshen", [ChatMessage("user", "问")], "问",
            memory_payload=payload)
        system = messages[0]["content"]
        assert system.index("关于用户的记忆") < system.index("知识库")
