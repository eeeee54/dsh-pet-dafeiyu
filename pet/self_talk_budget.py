# -*- coding: utf-8 -*-
"""自说自话的「打扰预算」——借鉴 openhanako-labs/oc-pet 的设计。

## 为什么需要

我们的自说自话是「每 1.5~4 分钟一句」，**没有日上限、没有深夜静默**：
挂着一天能自己嘀咕几百句，深夜三点还在说话。oc-pet 的
``proactive.daily_budget``（每日上限 + 按时段分配 + 深夜静默）正好补这个洞。

这里先落最小可用的一版：**每日上限 + 深夜静默**，时段分配留接口。

## 计数存在哪

``<data>/self_talk_budget.json``：``{"date": "2026-10-27", "count": 7}``。
跨天自动归零（按本地日期比较）。文件坏了当 0 处理——预算只是"少说两句"，
不值得为它让桌宠起不来。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)

#: 每日说话上限（0 = 不限）
DEFAULT_DAILY_LIMIT = 24
#: 深夜静默区间（本地时间，左闭右开）
DEFAULT_NIGHT_START = 0
DEFAULT_NIGHT_END = 8

_BUDGET_FILE = "self_talk_budget.json"


def _today() -> str:
    return date.today().isoformat()


def load_state(root: Path) -> dict:
    path = Path(root) / _BUDGET_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"date": _today(), "count": 0}
    if not isinstance(data, dict):
        return {"date": _today(), "count": 0}
    if data.get("date") != _today():
        return {"date": _today(), "count": 0}
    try:
        count = max(0, int(data.get("count") or 0))
    except (TypeError, ValueError):
        count = 0
    return {"date": _today(), "count": count}


def save_state(root: Path, state: dict) -> None:
    path = Path(root) / _BUDGET_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        LOGGER.debug("写自说自话预算失败", exc_info=True)


def is_night_silent(now: datetime, start_hour: int, end_hour: int) -> bool:
    """当前时刻是否落在深夜静默区间（支持跨零点，如 23 → 7）。"""
    hour = now.hour
    start_hour = int(start_hour) % 24
    end_hour = int(end_hour) % 24
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    # 跨零点：23:00-07:00
    return hour >= start_hour or hour < end_hour


def allow_speak(root: Path, *, daily_limit: int = DEFAULT_DAILY_LIMIT,
                night_silent: bool = True,
                night_start: int = DEFAULT_NIGHT_START,
                night_end: int = DEFAULT_NIGHT_END,
                now: datetime | None = None) -> tuple[bool, str]:
    """能不能说这一句。返回 (允许, 原因)。

    原因只在拒绝时有意义，便于日志排查「为什么今天不说话了」。
    """
    moment = now or datetime.now()
    if night_silent and is_night_silent(moment, night_start, night_end):
        return False, f"深夜静默（{night_start}:00-{night_end}:00）"
    limit = int(daily_limit or 0)
    if limit > 0:
        state = load_state(root)
        if state["count"] >= limit:
            return False, f"今日已达上限（{state['count']}/{limit}）"
    return True, ""


def record_speak(root: Path) -> int:
    """记一次说话，返回今天累计次数。"""
    state = load_state(root)
    state["count"] = int(state["count"]) + 1
    save_state(root, state)
    return state["count"]


def reset_today(root: Path) -> None:
    """把今天的计数清零（设置页「恢复默认」或手动重置用）。"""
    save_state(root, {"date": _today(), "count": 0})
