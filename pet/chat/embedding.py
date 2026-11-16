"""向量编码：真语义（embedding 模型）与本地兜底（字符 bigram）双后端。

## 为什么需要两层

「语义相似」有两种含义，能力差别很大：

* **字面模糊匹配**：`数据科学实习` ↔ `数据分析岗实习` 能对上（共享「数据」「实习」）。
  字符 bigram 哈希就能做，零依赖、5 微秒一条、离线可用。
* **真语义**：`实习` ↔ `求职` 也能对上，靠的是模型学到的词向量关系。
  这必须用 embedding 模型（Ollama 本地跑，或远端 API）。

本地 bigram **做不到第二层**——实测「实习怎么找」与「求职方向是数据分析岗」
余弦只有 0.076（字面几乎不重叠）。所以本模块把两者分开：

* 有 embedding 模型 → 用它，`mode() == "embedding"`
* 没有 → 用 bigram，`mode() == "lexical"`，并在 UI 上明确标注能力边界

## 为什么把 query 和文档分开编码

embedding 模型通常有非对称检索建议（query 加指令前缀）。这里保留
``encode_query`` / ``encode_document`` 两个入口，本地后端下二者等价，
接入模型后可以分别定制而不用改调用方。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

DIM = 512
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")

# 本地 bigram 的余弦普遍偏低（字面重叠少就趋近 0），阈值必须比模型低得多。
LEXICAL_MIN_SIMILARITY = 0.05
EMBEDDING_MIN_SIMILARITY = 0.35


def _stable_hash(token: str, dim: int = DIM) -> int:
    """跨进程稳定的哈希：内置 hash() 带随机盐，向量存盘后就读不懂了。"""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % dim


def _bigram_tokens(text: str) -> list[str]:
    raw = str(text or "").lower()
    tokens = [w for w in _WORD.findall(raw) if len(w) > 1]
    run = ""
    for ch in raw:
        if _CJK.match(ch):
            run += ch
            continue
        if len(run) >= 2:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        elif len(run) == 1:
            tokens.append(run)
        run = ""
    if len(run) >= 2:
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    elif len(run) == 1:
        tokens.append(run)
    return tokens


def lexical_encode(text: Any) -> tuple[float, ...]:
    """字符 bigram 哈希向量（L2 归一化）。"""
    vec = [0.0] * DIM
    for token in _bigram_tokens(text):
        vec[_stable_hash(token)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return tuple(vec)
    return tuple(v / norm for v in vec)


def _l2_normalize(values: list[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0.0:
        return tuple(values)
    return tuple(v / norm for v in values)


def cosine(left: tuple[float, ...] | list[float],
           right: tuple[float, ...] | list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


class EmbeddingBackend:
    """可替换的向量后端。

    :param config: 支持 ``memory.embed_provider``（auto/ollama/api/lexical）、
        ``memory.ollama_url``、``memory.ollama_embed_model``、
        ``memory.api_base_url``、``memory.api_key``、``memory.api_embed_model``
    """

    def __init__(self, config: Any = None):
        self.cfg = config
        self._mode: str | None = None
        self._model = ""
        self._dim = DIM
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, ...]] = {}
        self._probe_failed_at = 0.0

    # ---------------------------------------------------------------- 探测
    def _get(self, key: str, default: Any) -> Any:
        """读配置，同时兼容两套键名。

        主配置用 ``chat_memory_embed_provider``（与既有 ``chat_memory_*`` 一族一致）；
        这里把 ``memory.*`` 形式也认下来，方便测试与未来迁移。
        """
        if self.cfg is None:
            return default
        aliases = {
            "memory.embed_provider": ("chat_memory_embed_provider", "memory.embed_provider"),
            "memory.ollama_url": ("chat_memory_ollama_url", "memory.ollama_url"),
            "memory.ollama_embed_model": ("chat_memory_ollama_embed_model",
                                          "memory.ollama_embed_model"),
            "memory.api_base_url": ("chat_memory_api_base_url", "memory.api_base_url"),
            "memory.api_key": ("chat_memory_api_key", "memory.api_key"),
            "memory.api_key_ref": ("chat_memory_api_key_ref", "memory.api_key_ref"),
            "memory.api_embed_model": ("chat_memory_api_embed_model",
                                       "memory.api_embed_model"),
        }
        # Config.reload() 只合并 chat 段，顶层键不会从磁盘回读（实测：
        # 手写进 config.json 的 chat_memory_api_base_url 用 cfg.get 读不到）。
        # 所以这里在 cfg.get 之外再看一眼磁盘上的原始 JSON。
        disk = self._disk_values()
        for name in aliases.get(key, (key,)):
            # 磁盘优先：cfg.get 会把内置默认值也返回（如 api_embed_model 的
            # text-embedding-3-small），非空默认值会挡住用户在文件里写的真实值。
            if name in disk:
                raw = disk[name]
                if raw not in (None, "", [], {}):
                    return raw
            try:
                value = self.cfg.get(name, None)
            except Exception:
                value = None
            if value not in (None, "", [], {}):
                return value
        return default

    def api_key(self) -> str:
        """向量服务的 Key：配置文件优先，其次系统钥匙串。

        Key 放钥匙串（而不是 config.json 明文）更安全；这里的 ref 由
        `chat_memory_api_key_ref` 指定，默认 `memory/embed-api`。
        """
        value = str(self._get("memory.api_key", "") or "").strip()
        if value:
            return value
        ref = str(self._get("memory.api_key_ref", "memory/embed-api") or "").strip()
        if not ref:
            return ""
        try:
            from .models import SecretStore
            return (SecretStore().get(ref) or "").strip()
        except Exception:
            return ""

    def _disk_values(self) -> dict:
        """直接读配置文件原文（带缓存），兜住 Config 不回读的顶层键。"""
        cached = getattr(self, "_disk_cache", None)
        if cached is not None:
            return cached
        values: dict = {}
        try:
            import json
            from pathlib import Path as _Path
            path = getattr(self.cfg, "path", None)
            if path and _Path(path).is_file():
                values = json.loads(_Path(path).read_text(encoding="utf-8"))
                if not isinstance(values, dict):
                    values = {}
        except Exception:
            values = {}
        self._disk_cache = values
        return values

    def mode(self) -> str:
        """返回 ``"embedding"`` 或 ``"lexical"``。"""
        # 测试与排障用的强制开关：让单测结果不随"本机是否装了模型"飘忽
        if os.environ.get("DSHPET_FORCE_LEXICAL"):
            return "lexical"
        with self._lock:
            if self._mode is not None:
                return self._mode
            provider = str(self._get("memory.embed_provider", "auto")).lower()
            if provider == "lexical":
                self._mode = "lexical"
                return self._mode
            if provider in ("ollama", "auto") and self._probe_ollama():
                self._mode = "embedding"
                return self._mode
            if provider == "api" and self._probe_api():
                self._mode = "embedding"
                return self._mode
            self._mode = "lexical"
            return self._mode

    def _probe_ollama(self) -> bool:
        """探测本机 Ollama 是否有 embedding 模型。"""
        base = str(self._get("memory.ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        preferred = str(self._get("memory.ollama_embed_model", "bge-m3"))
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return False
        names = [str(m.get("name", "")) for m in tags.get("models", [])]
        for name in names:
            if name.split(":")[0] == preferred.split(":")[0]:
                self._model = name
                return True
        # 退而求其次：任意 embedding 类模型
        for name in names:
            if any(k in name.lower() for k in ("embed", "bge", "nomic", "gte", "m3e")):
                self._model = name
                return True
        return False

    def _probe_api(self) -> bool:
        base = str(self._get("memory.api_base_url", "")).strip()
        key = self.api_key()
        if not base or not key:
            return False
        self._model = str(self._get("memory.api_embed_model", "text-embedding-3-small"))
        return True

    def describe(self) -> dict[str, Any]:
        mode = self.mode()
        return {"mode": mode, "model": self._model, "dim": self._dim,
                "semantic": mode == "embedding",
                "min_similarity": (EMBEDDING_MIN_SIMILARITY if mode == "embedding"
                                   else LEXICAL_MIN_SIMILARITY)}

    # ---------------------------------------------------------------- 编码
    def encode(self, text: str) -> tuple[float, ...]:
        return self.encode_documents([text])[0]

    def encode_query(self, text: str) -> tuple[float, ...]:
        return self.encode(text)

    def encode_document(self, text: str) -> tuple[float, ...]:
        return self.encode(text)

    def encode_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        if self.mode() == "lexical":
            return [lexical_encode(t) for t in texts]

        results: list[tuple[float, ...] | None] = [None] * len(texts)
        pending: list[int] = []
        with self._lock:
            for index, text in enumerate(texts):
                key = hashlib.sha1(str(text).encode("utf-8")).hexdigest()
                cached = self._cache.get(key)
                if cached is not None:
                    results[index] = cached
                else:
                    pending.append(index)

        if pending:
            for index in pending:
                text = texts[index]
                vec = self._embed_remote(text)
                if vec is None:
                    # 远端失效：整体降级，保证召回不中断
                    with self._lock:
                        self._mode = "lexical"
                    return [lexical_encode(t) for t in texts]
                results[index] = vec
                with self._lock:
                    if len(self._cache) > 4096:
                        self._cache.clear()
                    self._cache[hashlib.sha1(str(text).encode("utf-8")).hexdigest()] = vec

        return [r if r is not None else lexical_encode(texts[i])
                for i, r in enumerate(results)]

    def _embed_remote(self, text: str) -> tuple[float, ...] | None:
        provider = str(self._get("memory.embed_provider", "auto")).lower()
        if provider == "api":
            return self._embed_api(text)
        vec = self._embed_ollama(text)
        return vec if vec is not None else None

    def _embed_ollama(self, text: str) -> tuple[float, ...] | None:
        base = str(self._get("memory.ollama_url", "http://127.0.0.1:11434")).rstrip("/")
        payload = json.dumps({"model": self._model, "prompt": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        embedding = data.get("embedding") or []
        if not embedding:
            return None
        self._dim = len(embedding)
        return _l2_normalize([float(v) for v in embedding])

    def _embed_api(self, text: str) -> tuple[float, ...] | None:
        base = str(self._get("memory.api_base_url", "")).rstrip("/")
        key = self.api_key()
        payload = json.dumps({"model": self._model, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/embeddings", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            embedding = data["data"][0]["embedding"]
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                IndexError, TimeoutError):
            return None
        self._dim = len(embedding)
        return _l2_normalize([float(v) for v in embedding])


_DEFAULT_BACKEND: EmbeddingBackend | None = None
_DEFAULT_LOCK = threading.Lock()


def default_backend(config: Any = None) -> EmbeddingBackend:
    """进程级共享后端：避免每个调用点各自探测一次 Ollama。"""
    global _DEFAULT_BACKEND
    with _DEFAULT_LOCK:
        if _DEFAULT_BACKEND is None or config is not None:
            _DEFAULT_BACKEND = EmbeddingBackend(config)
        return _DEFAULT_BACKEND


def reset_default_backend() -> None:
    """测试用：丢弃缓存的后端。"""
    global _DEFAULT_BACKEND
    with _DEFAULT_LOCK:
        _DEFAULT_BACKEND = None
