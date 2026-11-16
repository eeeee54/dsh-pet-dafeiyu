# -*- coding: utf-8 -*-
"""情绪状态机——借鉴 openhanako-labs/oc-pet 的 ``_set_surface_emotion``。

## 为什么要「优先级 + 迟滞」

纯"最后写入者赢"的情绪会横跳：屏幕检测说 curious、对话说 happy、定时器说
thinking —— 用户看到的是一张脸每秒变三次。oc-pet 用两道闸挡住：

1. **来源优先级**：``dialog``（用户直接对话）> ``event``（完成/报错）>
   ``ambient``（屏幕/时间）。低优先级不能盖掉正在显示的高优先级非中性情绪。
2. **最小驻留**（hysteresis）：进入非中性情绪后至少停 8 秒，期间只允许更高
   优先级打断，或者回到 ``neutral``。

## 情绪怎么来

* 用户发消息 → ``thinking``（source=dialog，等他/想问题）
* 回复完成 → 按内容粗判 ``happy`` / ``surprised`` / ``neutral``（source=dialog）
* 自说自话 → 不主动改情绪（避免自己把自己逗笑）

## 情绪影响什么

当前只影响**自说自话的台词选择**：``happy`` 偏好轻松句、``sad`` 偏好安慰句。
气泡配色和立绘是后续的事（帧序列没有表情参数）。
"""

from __future__ import annotations

import time
from enum import Enum

#: 最小驻留（秒）——防横跳。参照 oc-pet 的 EMOTION_MIN_DWELL_MS = 8000
MIN_DWELL_SECONDS = 8.0

#: 情绪过期（秒）：非中性情绪放着不动会自然回到 neutral
DEFAULT_TTL_SECONDS = 180.0


class Emotion(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    THINKING = "thinking"
    SURPRISED = "surprised"


#: 来源优先级（数字越大越强势）
SOURCE_PRIORITY = {
    "dialog": 3,
    "event": 2,
    "ambient": 1,
}

#: 情绪 → 台词关键词偏好（自说自话选句时用）
EMOTION_HINTS: dict[str, tuple[str, ...]] = {
    Emotion.HAPPY.value: ("好", "开心", "休息", "奖励", "干完", "顺", "元气"),
    Emotion.SAD.value: ("累", "歇", "别硬撑", "辛苦", "慢", "睡"),
    Emotion.THINKING.value: ("bug", "代码", "文档", "想", "工作"),
    Emotion.SURPRISED.value: ("咦", "什么", "奇怪", "居然"),
    Emotion.NEUTRAL.value: (),
}


def guess_emotion_from_reply(text: str) -> str:
    """从回复文本粗判情绪（不调模型，纯关键词）。

    宁可保守：判不出来一律 neutral —— 乱给情绪比不给更糟。
    """
    body = str(text or "")
    if not body.strip():
        return Emotion.NEUTRAL.value
    happy_words = ("哈哈", "开心", "太好了", "棒", "😊", "😄", "！", "～")
    sad_words = ("抱歉", "遗憾", "可惜", "难过", "叹气", "唉")
    surprise_words = ("居然", "竟然", "没想到", "哇", "诶", "咦")
    if any(word in body for word in surprise_words):
        return Emotion.SURPRISED.value
    if any(word in body for word in sad_words):
        return Emotion.SAD.value
    if any(word in body for word in happy_words):
        return Emotion.HAPPY.value
    return Emotion.NEUTRAL.value


class EmotionState:
    """进程内情绪状态（线程安全靠调用方：只在 GUI 线程用）。"""

    def __init__(self, *, min_dwell: float = MIN_DWELL_SECONDS,
                 ttl: float = DEFAULT_TTL_SECONDS):
        self.min_dwell = max(0.0, float(min_dwell))
        self.ttl = max(0.0, float(ttl))
        self.emotion = Emotion.NEUTRAL.value
        self.source = "neutral"
        self._entered_at = 0.0

    # ---------------------------------------------------------------- 读
    def current(self, *, now: float | None = None) -> str:
        """当前情绪（顺带处理自然过期）。"""
        moment = time.monotonic() if now is None else float(now)
        if self.emotion != Emotion.NEUTRAL.value and self.ttl > 0:
            if moment - self._entered_at >= self.ttl:
                self.emotion = Emotion.NEUTRAL.value
                self.source = "neutral"
        return self.emotion

    # ---------------------------------------------------------------- 写
    def set(self, emotion: str, *, source: str = "dialog",
            now: float | None = None) -> tuple[bool, str]:
        """设置情绪。返回 (是否接受, 拒绝原因)。"""
        moment = time.monotonic() if now is None else float(now)
        target = str(emotion or Emotion.NEUTRAL.value).strip().lower()
        if target not in {e.value for e in Emotion}:
            target = Emotion.NEUTRAL.value

        # 回到中性永远允许
        if target == Emotion.NEUTRAL.value:
            changed = self.emotion != target
            self.emotion = target
            self.source = "neutral"
            self._entered_at = moment
            return True, ""

        cur = self.current(now=moment)
        new_prio = SOURCE_PRIORITY.get(source, 1)
        cur_prio = SOURCE_PRIORITY.get(self.source, 0)

        # 迟滞：非中性之间切换，驻留不够且不更高优先级 → 忽略。
        # **同一来源内的流转不受限**：对话里 thinking → happy 是自然过程
        # （用户发消息时进 thinking，4 秒后回复到达就该变 happy），
        # 拿 8 秒迟滞去挡它只会让情绪永远停在思考中。
        same_source = source == self.source
        if cur != Emotion.NEUTRAL.value and cur != target and not same_source:
            dwell = moment - self._entered_at
            if dwell < self.min_dwell and new_prio <= cur_prio:
                return False, f"驻留中（{dwell:.1f}s < {self.min_dwell:.0f}s）且优先级不高"

        # 低优先级不能盖掉高优先级的非中性情绪
        if cur != Emotion.NEUTRAL.value and new_prio < cur_prio:
            return False, f"低优先级（{source} < {self.source}）"

        self.emotion = target
        self.source = source
        self._entered_at = moment
        return True, ""

    # ---------------------------------------------------------------- 台词
    def preferred_hints(self) -> tuple[str, ...]:
        return EMOTION_HINTS.get(self.current(), ())


#: 进程内单例：对话与自说自话共用同一份情绪
_SHARED: EmotionState | None = None


def shared_state() -> EmotionState:
    global _SHARED
    if _SHARED is None:
        _SHARED = EmotionState()
    return _SHARED
