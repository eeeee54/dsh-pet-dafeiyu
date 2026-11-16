# -*- coding: utf-8 -*-
"""聊天气泡的桌宠头像（圆形裁剪）。

## 为什么要它

气泡里的头像一直是 ``hide()`` 状态，消息列表只剩纯文字，视觉上很素。
桌宠本体有立绘，聊天气泡却"无名无脸"，缺角色感。

这里给一个统一的取图入口：优先用应用图标旁边的大肥鱼头像
（``assets/dafeiyu_256.png``），裁成圆形返回。找不到就返回空 QPixmap，
调用方自己退回首字母 —— 不能因为缺一张图让聊天窗崩掉。

三处气泡（``widgets`` / ``quick_chat`` / ``legacy_widgets``）共用。
"""

from __future__ import annotations

from pathlib import Path

#: 头像尺寸（逻辑像素）
AVATAR_SIZE = 34
#: 候选文件名（按优先级）
CANDIDATES = ("dafeiyu_256.png", "dafeiyu.png", "icon.ico")

_cache: dict[tuple[str, int], object] = {}


def _assets_root() -> Path:
    return Path(__file__).resolve().parents[2] / "assets"


def load_pet_avatar(size: int = AVATAR_SIZE):
    """返回圆形桌宠头像 QPixmap；不可用时返回空 QPixmap。"""
    key = ("pet", int(size))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPainterPath, QPixmap

    root = _assets_root()
    source = None
    for name in CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            source = candidate
            break
    if source is None:
        empty = QPixmap()
        _cache[key] = empty
        return empty

    raw = QPixmap(str(source))
    if raw.isNull():
        _cache[key] = raw
        return raw

    side = min(raw.width(), raw.height())
    square = raw.copy((raw.width() - side) // 2, (raw.height() - side) // 2, side, side)
    scaled = square.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    # 圆形裁剪：把方图裁成圆，气泡里看起来像头像而不是贴图
    rounded = QPixmap(scaled.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addEllipse(0, 0, scaled.width(), scaled.height())
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    _cache[key] = rounded
    return rounded


def has_pet_avatar() -> bool:
    return not load_pet_avatar().isNull()
