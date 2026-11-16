# -*- coding: utf-8 -*-
"""诊断：全新 base 目录下 chat_settings() 的结构。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from pet.config import Config

with tempfile.TemporaryDirectory() as tmp:
    c = Config(base=Path(tmp))
    cs = c.chat_settings()
    print("类型:", type(cs).__name__)
    print("providers:", type(getattr(cs, "providers", None)).__name__,
          list(getattr(cs, "providers", {}) or {}))
    ac = cs.active_config
    print("active_config:", type(ac).__name__, repr(ac)[:80])
