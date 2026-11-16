# -*- coding: utf-8 -*-
"""共享记忆库：路径、镜像写入筛选、跨角色召回。

对应需求「打破会话与角色隔离边界，实现跨会话、跨时间维度的记忆复用」。
关键约束：只有 fact/lesson 层会跨角色共享，topic 层（角色专属语气/梗）不共享。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.chat.memory import (ChatMemory, MemoryPayload, memory_path, memory_payload,
                             shared_memory_path)
from pet.chat.memory_extract import _SHAREABLE_LAYERS, _mirror_to_shared


@pytest.fixture()
def tmpdir():
    with tempfile.TemporaryDirectory(prefix="dshpet-shared-") as d:
        yield Path(d)


class TestPaths:
    def test_shared_dir_uses_underscore_prefix(self, tmpdir: Path):
        p = shared_memory_path(tmpdir, "")
        assert p.name == "memory.json"
        assert p.parent.name == "_shared", "下划线前缀避免与真实角色 id 撞名"

    def test_role_and_shared_are_separate(self, tmpdir: Path):
        role = memory_path(tmpdir, "", "shenshen")
        shared = shared_memory_path(tmpdir, "")
        assert role != shared
        assert role.parent.name == "shenshen"

    def test_instance_suffix_applies_to_both(self, tmpdir: Path):
        a = memory_path(tmpdir, "dev", "shenshen")
        b = shared_memory_path(tmpdir, "dev")
        assert "dev" in str(a)
        assert "dev" in str(b)


class TestMirror:
    def test_only_fact_and_lesson_shared(self, tmpdir: Path):
        shared = shared_memory_path(tmpdir, "")
        ops = [
            {"op": "ADD", "layer": "fact", "text": "用户是数据科学专业"},
            {"op": "ADD", "layer": "lesson", "text": "不要在深夜催他睡觉"},
            {"op": "ADD", "layer": "topic", "text": "用户问过 Spark 调优"},
        ]
        _mirror_to_shared(ops, shared)
        entries = ChatMemory(shared).load()
        texts = [e.get("text", "") for e in entries]
        assert any("数据科学" in t for t in texts)
        assert any("深夜" in t for t in texts)
        assert not any("Spark 调优" in t for t in texts), "topic 层不应跨角色共享"

    def test_forget_not_mirrored(self, tmpdir: Path):
        shared = shared_memory_path(tmpdir, "")
        _mirror_to_shared([{"op": "FORGET", "layer": "fact", "text": "用户是学生"}], shared)
        assert ChatMemory(shared).load() == []

    def test_none_path_is_noop(self):
        _mirror_to_shared([{"op": "ADD", "layer": "fact", "text": "x"}], None)

    def test_shareable_layers_constant(self):
        assert _SHAREABLE_LAYERS == ("fact", "lesson")


class TestPayloadMerge:
    def test_shared_entries_marked(self, tmpdir: Path):
        role = ChatMemory(memory_path(tmpdir, "", "shenshen"))
        role.add("用户在这个角色这里说过的话")
        shared = ChatMemory(shared_memory_path(tmpdir, ""))
        shared.add("用户是数据科学专业的学生")

        payload = memory_payload(role, True, shared_memory=shared)
        scopes = [e.get("scope") for e in payload.entries]
        assert "shared" in scopes, "共享条目必须带 scope=shared 标记便于区分"
        assert all(e.get("scope") != "shared" or "数据科学" in e.get("text", "")
                   for e in payload.entries)

    def test_without_shared_same_as_before(self, tmpdir: Path):
        role = ChatMemory(memory_path(tmpdir, "", "shenshen"))
        role.add("用户是学生")
        with_shared = memory_payload(role, True, shared_memory=None)
        baseline = memory_payload(role, True)
        assert with_shared.entries == baseline.entries

    def test_shared_read_failure_tolerated(self, tmpdir: Path):
        role = ChatMemory(memory_path(tmpdir, "", "shenshen"))
        role.add("用户是学生")

        class Broken:
            def load(self):
                raise OSError("boom")

        payload = memory_payload(role, True, shared_memory=Broken())
        assert payload.entries, "共享库读失败不应让私有记忆丢失"

    def test_knowledge_text_passthrough(self, tmpdir: Path):
        role = ChatMemory(memory_path(tmpdir, "", "shenshen"))
        role.add("用户是学生")
        payload = memory_payload(role, True, knowledge_text="【知识库】某段资料")
        assert payload.knowledge_text == "【知识库】某段资料"

    def test_empty_entries_still_carries_knowledge(self, tmpdir: Path):
        role = ChatMemory(memory_path(tmpdir, "", "shenshen"))
        payload = memory_payload(role, True, knowledge_text="【知识库】资料")
        assert payload.entries == []
        assert payload.knowledge_text == "【知识库】资料", \
            "没有记忆时知识库仍应能注入"
