# -*- coding: utf-8 -*-
"""验证新增配置键已进入默认配置并被读取。"""
import sys

sys.path.insert(0, ".")
from pet.config import Config

keys = ("chat_memory_semantic_weight", "chat_memory_embed_provider",
        "chat_memory_knowledge_enabled")
c = Config()
for k in keys:
    print(f"{k}: {c.get(k, '<缺失>')!r}")
print("set/get 往返:")
c.set("chat_memory_semantic_weight", 0.35)
print("  after set:", c.get("chat_memory_semantic_weight"))
c.set("chat_memory_semantic_weight", 0.0)
