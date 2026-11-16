# -*- coding: utf-8 -*-
"""诊断：为什么语义召回返回空。"""
import sys

sys.path.insert(0, ".")
from pet.chat.memory_recall import _entry_keywords, extract_keywords
from pet.chat.memory_semantic import cosine, encode, entry_vector, semantic_hits

e = {"id": "near", "layer": "topic", "text": "用户求职方向是数据分析岗",
     "keywords": ["求职", "数据分析"], "importance": 0.5, "updated_at": 1.7e9}
print("entry keywords:", _entry_keywords(e))
print("query keywords:", extract_keywords("实习怎么找"))
print("entry vec norm:", round(sum(v * v for v in entry_vector(e)) ** 0.5, 4))
print("query vec norm:", round(sum(v * v for v in encode("实习怎么找")) ** 0.5, 4))
print("cos:", round(cosine(entry_vector(e), encode("实习怎么找")), 4))
print("hits:", semantic_hits([e], "实习怎么找", min_similarity=0.0))
