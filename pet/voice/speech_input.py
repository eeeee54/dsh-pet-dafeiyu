"""语音输入：麦克风录音 → 通义 ASR → 文字。

## 为什么要自己实现

项目原本只有 TTS（说话），没有 STT（听）。要做"语音对话"，需要补上
录音与识别这两段。这里不引入本地 ASR 模型（whisper 要几百 MB 权重，
且这台机器拉不动），改用已有的通义 API：``qwen3-asr-flash``。

## 中文的关键

实测：不带提示时模型会把中文音频识别成英文
（"What a funny facial expression."）。必须用 system 消息显式要求
"转写成简体中文"，识别结果才正常（"我怎么把那个位置给搞了？"）。

## 录音策略

按「静音自动收尾」而不是固定时长：
* 至少录 0.8 秒（避免误触）
* 连续 1.2 秒低于阈值就认为说完了
* 最长 20 秒强制停止（防呆）
阈值用「相对峰值」而不是绝对值——不同麦克风增益差很多。
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
import threading
import time
import urllib.request
import wave
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16000
CHANNELS = 1
MIN_SECONDS = 0.8
MAX_SECONDS = 20.0
SILENCE_SECONDS = 1.2
BLOCK_MS = 100

ASR_MODEL = "qwen3-asr-flash"
ASR_SYSTEM = "请把这段语音转写成简体中文。只输出转写结果，不要解释。"
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _resolve_key(config) -> str:
    """取 ASR 用的 Key：钥匙串优先（与聊天同一个 provider）。"""
    try:
        from ..chat.models import SecretStore
        from ..config import Config
        cfg = config if config is not None else Config()
        prov = cfg.chat_settings().active_config
        key = SecretStore().get(prov.api_key_ref) or getattr(prov, "api_key", "")
        if key:
            return key.strip()
    except Exception:
        pass
    try:
        value = config.get("chat_memory_api_key", "") if config is not None else ""
        return str(value or "").strip()
    except Exception:
        return ""


class SpeechInput:
    """一次语音输入的生命周期：开始 → 自动收尾 → 转写。"""

    def __init__(self, config=None):
        self.cfg = config
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._recording = False

    # ---------------------------------------------------------------- 状态
    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import sounddevice as sd
        except ImportError:
            return False, "缺少 sounddevice（录音库）"
        try:
            info = sd.query_devices(kind="input")
            if not info or not info.get("max_input_channels"):
                return False, "没有可用的麦克风"
        except Exception as exc:
            return False, f"麦克风不可用：{exc}"
        return True, ""

    @property
    def recording(self) -> bool:
        return self._recording

    # ---------------------------------------------------------------- 录音
    def start(self, on_text: Callable[[str], None],
              on_error: Callable[[str], None] | None = None,
              on_state: Callable[[str], None] | None = None) -> bool:
        """开始一次语音输入；返回是否真的启动。"""
        ok, reason = self.available()
        if not ok:
            if on_error:
                on_error(reason)
            return False
        if self._thread and self._thread.is_alive():
            self._stop.set()
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_text, on_error, on_state),
            name="speech-input", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self, on_text, on_error, on_state) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            if on_error:
                on_error(f"录音库不可用：{exc}")
            return

        if on_state:
            on_state("listening")
        frames: list[bytes] = []
        peak_global = 0
        silence_run = 0.0
        started = time.monotonic()
        block = int(SAMPLE_RATE * BLOCK_MS / 1000)
        self._recording = True
        try:
            with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                   dtype="int16", blocksize=block) as stream:
                while not self._stop.is_set():
                    data, _overflowed = stream.read(block)
                    frames.append(bytes(data))
                    # 振幅统计（int16 → 峰值）
                    samples = memoryview(data).cast("h")
                    peak = max((abs(s) for s in samples), default=0)
                    peak_global = max(peak_global, peak)
                    elapsed = time.monotonic() - started
                    # 阈值取"已见峰值的 12%"，下限 300（底噪保护）
                    threshold = max(300, peak_global * 0.12)
                    if peak < threshold:
                        silence_run += BLOCK_MS / 1000
                    else:
                        silence_run = 0.0
                    if elapsed >= MIN_SECONDS and silence_run >= SILENCE_SECONDS:
                        break
                    if elapsed >= MAX_SECONDS:
                        break
        except Exception as exc:
            self._recording = False
            if on_error:
                on_error(f"录音失败：{exc}")
            return
        finally:
            self._recording = False

        if on_state:
            on_state("recognizing")
        wav = self._to_wav(frames)
        if len(wav) < 4000:
            if on_error:
                on_error("没录到声音")
            return
        text = self.transcribe(wav)
        if text is None:
            if on_error:
                on_error("识别失败（网络或服务异常）")
            return
        if not text.strip():
            if on_error:
                on_error("没听懂，再说一次？")
            return
        if on_state:
            on_state("done")
        try:
            on_text(text.strip())
        except Exception:
            pass

    @staticmethod
    def _to_wav(frames: list[bytes]) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(frames))
        return buf.getvalue()

    # ---------------------------------------------------------------- 转写
    def transcribe(self, wav: bytes, model: str = ASR_MODEL) -> str | None:
        key = _resolve_key(self.cfg)
        if not key:
            return None
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": ASR_SYSTEM}]},
                {"role": "user", "content": [{
                    "type": "input_audio",
                    "input_audio": {
                        "data": "data:audio/wav;base64," + base64.b64encode(wav).decode(),
                        "format": "wav",
                    },
                }]},
            ],
        }
        request = urllib.request.Request(
            f"{DASHSCOPE_BASE}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=45) as resp:
                data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"]
            return str(content or "").strip()
        except Exception:
            return None


def describe() -> dict:
    ok, reason = SpeechInput.available()
    return {"available": ok, "reason": reason}
