# -*- coding: utf-8 -*-
"""诊断 chat_settings() 的返回结构。"""
import sys

sys.path.insert(0, ".")
from pet.config import Config

c = Config()
cs = c.chat_settings()
print("chat_settings 类型:", type(cs).__name__)
print("有 active_config 属性:", hasattr(cs, "active_config"))
try:
    ac = cs.active_config
    print("active_config 类型:", type(ac).__name__)
except Exception as exc:
    print("active_config 访问失败:", type(exc).__name__, exc)
print("providers 类型:", type(getattr(cs, "providers", None)).__name__)
print("semantic_weight 原始值:", repr(c.get("chat_memory_semantic_weight")))
