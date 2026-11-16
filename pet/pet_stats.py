# -*- coding: utf-8 -*-
"""桌宠的三围与成长：等级 / 经验 / 金币 / 饱腹 / 水分 / 心情。

## 参考与取舍

[dsh-pet-clove](https://github.com/forget6610/dsh-pet-clove) 有一套完整的养成
（等级、金币、背包、每日任务、成就、商店 30 件）。我们**只取数据层**：

* 保留：等级/经验/金币、三项状态（饱腹/水分/心情）及其随时间衰减
* 不做：背包、商店、任务列表、成就、面板 UI —— 那是另一个游戏的体量

## 为什么状态要"随时间衰减"

不衰减的话，数值永远是满的，"养成"就退化成"看一眼数字"。衰减让"回来看看它"
这件事有意义。衰减按**真实经过的秒数**算（不是按 tick 次数），所以关机一晚上
回来它会饿——这是设计意图。

## 边界

所有数值夹在 ``[0, 100]``，金币/经验不为负。升级公式用递增曲线，避免早期
一秒一级。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_STATE_FILE = "pet_stats.json"

#: 每小时自然消耗
HUNGER_DECAY_PER_HOUR = 4.0
THIRST_DECAY_PER_HOUR = 6.0
MOOD_DECAY_PER_HOUR = 3.0

#: 各项上限
MAX_VALUE = 100.0

#: 单次交互的增量
FEED_HUNGER = 30.0
FEED_THIRST = 5.0      # 吃东西也补一点水
DRINK_THIRST = 35.0
PET_MOOD = 8.0         # 摸头
PET_EXP = 2            # 摸头给的经验

#: 升级所需经验（level → 升级门槛），随等级递增
def exp_for_level(level: int) -> int:
    level = max(1, int(level))
    return 50 + (level - 1) * 30


def _clamp(value: float, low: float = 0.0, high: float = MAX_VALUE) -> float:
    return max(low, min(high, float(value)))


@dataclass
class PetStats:
    level: int = 1
    exp: int = 0
    coins: int = 0
    hunger: float = 80.0
    thirst: float = 80.0
    mood: float = 80.0
    #: 上次结算时间（epoch 秒）。0 = 还没结算过
    updated_at: float = 0.0
    #: 认识第几天
    met_at: float = 0.0
    #: 今日已摸头次数（每日重置）
    pets_today: int = 0
    pets_date: str = ""

    # ------------------------------------------------------------ 生命迹象
    def mood_label(self) -> str:
        """给菜单看的一句话状态。"""
        if self.hunger < 25:
            return "饿得没力气了…"
        if self.thirst < 25:
            return "好渴，想喝水…"
        if self.mood >= 75:
            return "心情很好～"
        if self.mood >= 45:
            return "还行吧。"
        if self.mood >= 25:
            return "有点无聊…"
        return "不太开心，陪陪我？"

    def days_known(self, now: float | None = None) -> int:
        moment = time.time() if now is None else float(now)
        if not self.met_at:
            return 1
        return max(1, int((moment - self.met_at) // 86400) + 1)

    # ------------------------------------------------------------ 衰减
    def apply_decay(self, now: float | None = None) -> float:
        """按真实经过时间衰减三围，返回经过的小时数。"""
        moment = time.time() if now is None else float(now)
        if not self.updated_at:
            self.updated_at = moment
            return 0.0
        hours = max(0.0, (moment - self.updated_at) / 3600.0)
        if hours <= 0:
            return 0.0
        self.hunger = _clamp(self.hunger - HUNGER_DECAY_PER_HOUR * hours)
        self.thirst = _clamp(self.thirst - THIRST_DECAY_PER_HOUR * hours)
        self.mood = _clamp(self.mood - MOOD_DECAY_PER_HOUR * hours)
        self.updated_at = moment
        return hours

    # ------------------------------------------------------------ 交互
    def feed(self) -> str:
        self.hunger = _clamp(self.hunger + FEED_HUNGER)
        self.thirst = _clamp(self.thirst + FEED_THIRST)
        self.mood = _clamp(self.mood + 3.0)
        return "吃得好饱～"

    def drink(self) -> str:
        self.thirst = _clamp(self.thirst + DRINK_THIRST)
        self.mood = _clamp(self.mood + 2.0)
        return "咕嘟咕嘟，舒服。"

    def pet(self, now: float | None = None) -> str:
        """摸头：加心情 + 少量经验。"""
        moment = time.time() if now is None else float(now)
        today = time.strftime("%Y-%m-%d", time.localtime(moment))
        if self.pets_date != today:
            self.pets_date = today
            self.pets_today = 0
        self.pets_today += 1
        self.mood = _clamp(self.mood + PET_MOOD)
        leveled = self.add_exp(PET_EXP)
        return "升级啦！" if leveled else "嘿嘿…"

    def add_exp(self, amount: int) -> bool:
        """加经验；返回是否升级。"""
        self.exp += max(0, int(amount))
        leveled = False
        while self.exp >= exp_for_level(self.level):
            self.exp -= exp_for_level(self.level)
            self.level += 1
            self.coins += 20 * self.level     # 升级奖励
            leveled = True
        return leveled

    # ------------------------------------------------------------ 持久化
    def to_dict(self) -> dict:
        data = asdict(self)
        data["_version"] = 1
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> "PetStats":
        if not isinstance(raw, dict):
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in allowed}
        stats = cls()
        for key, value in clean.items():
            try:
                if key in ("level", "exp", "coins", "pets_today"):
                    setattr(stats, key, max(0, int(value)))
                elif key in ("hunger", "thirst", "mood"):
                    setattr(stats, key, _clamp(value))
                elif key in ("updated_at", "met_at"):
                    setattr(stats, key, float(value or 0.0))
                elif key == "pets_date":
                    setattr(stats, key, str(value or ""))
            except (TypeError, ValueError):
                continue
        if not stats.met_at:
            stats.met_at = time.time()
        return stats


def load(root: Path) -> PetStats:
    path = Path(root) / _STATE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stats = PetStats()
        stats.met_at = time.time()
        return stats
    return PetStats.from_dict(raw)


def save(root: Path, stats: PetStats) -> None:
    path = Path(root) / _STATE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        LOGGER.debug("保存养成数据失败", exc_info=True)


def describe(stats: PetStats, now: float | None = None) -> str:
    """多行状态文本（右键菜单/气泡用）。"""
    moment = time.time() if now is None else float(now)
    need = exp_for_level(stats.level)
    return (
        f"Lv.{stats.level}　经验 {stats.exp}/{need}"
        f"　金币 {stats.coins}\n"
        f"饱腹 {stats.hunger:.0f}　水分 {stats.thirst:.0f}　心情 {stats.mood:.0f}\n"
        f"{stats.mood_label()}　·　相识第 {stats.days_known(moment)} 天"
    )
