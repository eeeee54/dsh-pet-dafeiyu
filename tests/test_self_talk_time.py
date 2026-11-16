# -*- coding: utf-8 -*-
"""自说自话的时段选句与去重：按时间挑应景台词，且不连着说同一句。

对应需求「给桌宠增加适时语音」：
* 台词要在合适的时候说合适的话（早晨不说"熬夜伤身"）
* 池子小时也不能反复念同一句
* 用户自定义的台词（纯字符串列表）同样吃这套逻辑，不需要迁移
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pet.window_alerts import (_RECENT_LIMIT, _time_bucket, pick_text_by_time,
                               reset_recent_texts)


@pytest.fixture(autouse=True)
def _clean_recent():
    """每个用例都从空记忆开始，避免互相污染。"""
    reset_recent_texts()
    yield
    reset_recent_texts()


def at(hour: int) -> dt.datetime:
    return dt.datetime(2026, 10, 26, hour, 0)


class TestTimeBucket:
    @pytest.mark.parametrize("hour,bucket", [
        (5, "morning"), (8, "morning"), (10, "morning"),
        (11, "noon"), (13, "noon"),
        (14, "afternoon"), (18, "afternoon"),
        (19, "evening"), (22, "evening"),
        (23, "night"), (2, "night"), (4, "night"),
    ])
    def test_bucket_boundaries(self, hour, bucket):
        assert _time_bucket(hour) == bucket


class TestTimeAwarePicking:
    def test_morning_prefers_morning_lines(self):
        # 全池都是早晨句：测"该时段是否优先"，不受"应景<=2 并入通用池"影响
        texts = ["早上好，起床啦。", "早餐吃了吗。", "早安，太阳出来了。"]
        picks = [pick_text_by_time(texts, now=at(8)) for _ in range(20)]
        assert all(any(k in p for k in ("早上", "早餐", "太阳", "早安")) for p in picks)

    def test_night_prefers_night_lines(self):
        texts = ["熬夜伤身，早点睡吧。", "夜深了，晚安。", "该睡了，别熬了。"]
        picks = [pick_text_by_time(texts, now=at(2)) for _ in range(20)]
        assert all(any(k in p for k in ("睡", "熬夜", "晚安", "夜")) for p in picks)

    def test_no_cross_bucket_leak(self):
        """早晨池不该出现深夜句，反之亦然（这是真实修过的 bug）。"""
        texts = [
            "早上好，起床啦。", "早餐吃了吗。",          # 早晨
            "熬夜伤身，早点睡吧。", "晚安。",            # 深夜
            "记得喝水。",                                # 通用
        ]
        morning_picks = [pick_text_by_time(texts, now=at(8)) for _ in range(30)]
        night_picks = [pick_text_by_time(texts, now=at(2)) for _ in range(30)]
        night_words = ("睡", "熬夜", "晚安")
        morning_words = ("早上", "早餐")
        assert not any(w in p for p in morning_picks for w in night_words), \
            "早晨不该说深夜台词"
        assert not any(w in p for p in night_picks for w in morning_words), \
            "深夜不该说早晨台词"

    def test_falls_back_when_no_fitting_line(self):
        texts = ["完全无关的一句。", "另一句无关的。"]
        picks = [pick_text_by_time(texts, now=at(8)) for _ in range(10)]
        assert all(p in texts for p in picks)

    def test_bias_one_always_fitting(self):
        # 用 3 句应景台词，避免触发"应景句 <= 2 时并入通用池"的合并分支
        texts = ["早上好。", "起床啦。", "早安，太阳出来了。"]
        picks = [pick_text_by_time(texts, now=at(8), bias=1.0) for _ in range(10)]
        assert all(any(k in p for k in ("早上", "起床", "早安", "太阳")) for p in picks)


class TestNoAdjacentRepeat:
    def test_no_adjacent_repeat_full_pool(self):
        texts = [f"第{i}句。" for i in range(10)]
        picks = [pick_text_by_time(texts, now=at(12)) for _ in range(30)]
        assert all(a != b for a, b in zip(picks, picks[1:]))

    def test_two_line_pool_alternates(self):
        # 两句都属于傍晚池，这样测的是"去重"本身，不掺时段偏好
        texts = ["收工啦。", "下班啦。"]
        picks = [pick_text_by_time(texts, now=at(20)) for _ in range(10)]
        assert all(a != b for a, b in zip(picks, picks[1:])), \
            "只有两句时也必须交替，不能连说"

    def test_single_line_pool_does_not_crash(self):
        picks = [pick_text_by_time(["只有这一句。"], now=at(20)) for _ in range(3)]
        assert picks == ["只有这一句。"] * 3

    def test_empty_pool_returns_empty(self):
        assert pick_text_by_time([], now=at(12)) == ""
        assert pick_text_by_time(None, now=at(12)) == ""

    def test_recent_memory_is_bounded(self):
        texts = [f"第{i}句。" for i in range(10)]
        for _ in range(20):
            pick_text_by_time(texts, now=at(12))
        from pet.window_alerts import _RECENT_TEXTS
        assert len(_RECENT_TEXTS) <= _RECENT_LIMIT

    def test_reset_clears_memory(self):
        from pet.window_alerts import _RECENT_TEXTS
        pick_text_by_time(["a。", "b。"], now=at(12))
        assert _RECENT_TEXTS
        reset_recent_texts()
        assert not _RECENT_TEXTS


class TestDefaultLineBank:
    def test_default_bank_size(self):
        from pet.config import DEFAULT_SELF_TALK_TEXTS as T
        assert len(T) >= 50, "台词太少会很快重复完"
        assert len(set(T)) == len(T), "不应有完全重复的台词"

    def test_each_bucket_has_enough_lines(self):
        """每个时段至少 8 句：少于这个数，"避开最近 3 句"会很快掏空池子。"""
        from pet.config import DEFAULT_SELF_TALK_TEXTS as T
        from pet.window_alerts import _TIME_HINTS
        for bucket, hints in _TIME_HINTS.items():
            fitting = [t for t in T if any(k in t for k in hints)]
            assert len(fitting) >= 8, f"{bucket} 时段只有 {len(fitting)} 句，不够"

    def test_hints_avoid_ambiguous_single_chars(self):
        """禁止「早」「晚」「午」这类歧义单字：它们会把「早点睡」「这么晚还不睡」
        「下午」误判成早晨/傍晚/中午（真实踩过的坑）。

        「吃」「饭」「饿」是例外——它们是吃饭这件事的核心词，
        没有更短又准确的替代，且不会跨到别的时段。
        """
        from pet.window_alerts import _TIME_HINTS
        # 「吃」「饭」「饿」是吃饭的核心词；「睡」「夜」「困」是深夜的核心词。
        # 这些没有更短又准确的替代，且不会把别时段的句子拉进来。
        allowed_single = {"吃", "饭", "饿", "睡", "夜", "困"}
        for bucket, hints in _TIME_HINTS.items():
            for hint in hints:
                if len(hint) < 2:
                    assert hint in allowed_single, \
                        f"{bucket} 的关键词「{hint}」太宽，容易跨时段误判"
