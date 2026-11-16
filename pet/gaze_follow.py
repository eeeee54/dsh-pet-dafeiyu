# -*- coding: utf-8 -*-
"""鼠标注视：让桌宠左右翻转"看向"鼠标。

## 设计取舍

oc-pet 有完整的视线跟随（含 Live2D 面部参数）。我们是**帧序列**，没有"眼神"
这一层，只能做最粗的那一维：**左右朝向**（``sprite.facing``）。

与其硬塞一个"看起来在转头"的效果，不如把它做成**默认关闭**的可选项：
* 桌宠正在拖拽 / 播放交互动画时不动 facing（会打架）
* 鼠标离桌宠很近时不动（否则在桌宠身上微动就来回翻，很跳）
* 距离阈值 + 迟滞：只有鼠标明显偏到一侧才翻

## 为什么单独成模块

``overlay_shell`` 已经 5000+ 行，把"该不该翻"这类纯判断塞进去既难测也难读。
这里只做决策，不碰 Qt 对象。
"""

from __future__ import annotations

#: 鼠标距离桌宠中心多远才算"明显在另一侧"（像素）
DEFAULT_DEAD_ZONE_PX = 60
#: 两次翻转的最小间隔（秒）——防抖
DEFAULT_MIN_INTERVAL = 0.8


def decide_facing(*, pet_center_x: float, mouse_x: float,
                  current_facing: str,
                  dead_zone: float = DEFAULT_DEAD_ZONE_PX) -> str:
    """根据鼠标相对桌宠的横向位置决定朝向。

    * 鼠标落在死区内 → 保持当前朝向（不抖）
    * 明显偏左 → ``left``；明显偏右 → ``right``
    """
    delta = float(mouse_x) - float(pet_center_x)
    if abs(delta) < float(dead_zone):
        return current_facing if current_facing in ("left", "right") else "right"
    return "left" if delta < 0 else "right"


class GazeFollower:
    """节流 + 死区的注视决策器（不含任何 Qt 依赖）。"""

    def __init__(self, *, dead_zone: float = DEFAULT_DEAD_ZONE_PX,
                 min_interval: float = DEFAULT_MIN_INTERVAL):
        self.dead_zone = max(0.0, float(dead_zone))
        self.min_interval = max(0.0, float(min_interval))
        # 初值用 -inf：第一次判断必须放行（否则 t=0 会被自己的节流挡住）
        self._last_flip_at = float("-inf")

    def should_update(self, now: float) -> bool:
        """距上次翻转够久才允许再判断（降低无谓的 facing 写入）。"""
        return (now - self._last_flip_at) >= self.min_interval

    def next_facing(self, *, pet_center_x: float, mouse_x: float,
                    current_facing: str, now: float) -> str:
        """返回（可能不变的）朝向；不满足节流时原样返回。"""
        if not self.should_update(now):
            return current_facing
        decided = decide_facing(
            pet_center_x=pet_center_x, mouse_x=mouse_x,
            current_facing=current_facing, dead_zone=self.dead_zone)
        if decided != current_facing:
            self._last_flip_at = now
        return decided

    def reset(self) -> None:
        # 初值用 -inf：第一次判断必须放行（否则 t=0 会被自己的节流挡住）
        self._last_flip_at = float("-inf")
