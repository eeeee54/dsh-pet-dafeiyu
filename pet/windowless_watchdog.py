# -*- coding: utf-8 -*-
"""无窗口看门狗：关掉桌宠后进程别赖着不走。

## 要解决什么

``setQuitOnLastWindowClosed(False)`` 让"关窗口 ≠ 退进程"。于是：

1. 用户关掉桌宠窗口 → 进程还在，窗口没了
2. 用户双击快捷方式 → 被 ``overlay-instance.lock`` 拒绝，静默退出
3. 现象就是"快捷方式打不开了"（本轮已加"双击唤出"缓解，但窗口真被销毁时唤不出）

所以还需要**自愈**：连续一段时间没有任何可见窗口，自己退出，下次双击就能起来。

## 分寸

不能太激进——托盘常驻是上游设计，用户可能就是想让它缩着。
默认 180 秒（3 分钟）足够从托盘恢复；配 ``quit_without_window_seconds=0`` 可关掉。

只数**顶层可见窗口**（含设置页、对话窗），不是只数桌宠本体：用户开着设置页
改东西时不该被判定为"无窗口"。
"""

from __future__ import annotations

import logging
import time

LOGGER = logging.getLogger(__name__)

#: 默认容忍"没有任何可见窗口"的秒数
DEFAULT_QUIT_WITHOUT_WINDOW_SECONDS = 180.0
#: 检查间隔（秒）
POLL_INTERVAL_SECONDS = 15.0


def _is_windows() -> bool:
    """平台判断抽成函数：测试要能替换它（sys.platform 是只读属性）。"""
    import sys as _sys
    return _sys.platform == "win32"


def _win32_has_visible_window(pid: int) -> bool | None:
    """用 Win32 判断该进程有没有可见的顶层窗口；拿不到就返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    try:
        user32 = ctypes.WinDLL("user32")
    except OSError:
        return None
    found = {"visible": False}
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        # 零尺寸的窗口在屏幕上什么都看不到，不算"用户能用"
        if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
            return True
        found["visible"] = True
        return False  # 找到一个就停
    try:
        user32.EnumWindows(enum_proc(_cb), 0)
    except Exception:
        return None
    return found["visible"]


def has_visible_toplevel(app) -> bool:
    """进程里还有没有用户能看见的顶层窗口。

    Windows 优先用 Win32 判断：overlay 拓扑的合成分身窗带
    ``WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE``，
    Qt 的 ``isVisible()`` 对它返回 False（实测），按 Qt 判会把正常运行的
    桌宠误判成"无窗口"然后退掉。

    其他平台（或 Win32 探测失败）退回 Qt 的 ``topLevelWidgets()``。
    ``app`` 可以是 QApplication，也可以是实现 ``topLevelWidgets()`` 的替身。
    """
    import os

    if _is_windows():
        probed = _win32_has_visible_window(os.getpid())
        if probed is not None:
            return probed

    widgets_getter = getattr(app, "topLevelWidgets", None)
    if not callable(widgets_getter):
        try:
            from PySide6.QtWidgets import QApplication
            widgets_getter = QApplication.topLevelWidgets
        except ImportError:
            return True  # 无 Qt 时不做判断，别误杀
    try:
        widgets = widgets_getter()
    except Exception:
        return True
    for widget in widgets:
        try:
            if widget is None or not widget.isVisible():
                continue
            width = widget.width() if callable(getattr(widget, "width", None)) else 1
            height = widget.height() if callable(getattr(widget, "height", None)) else 1
            if width <= 0 or height <= 0:
                continue
            return True
        except Exception:
            continue
    return False


class WindowlessWatchdog:
    """记录"从什么时候开始没有可见窗口"，超时就请宿主退出。"""

    def __init__(self, timeout_seconds: float = DEFAULT_QUIT_WITHOUT_WINDOW_SECONDS,
                 poll_interval: float = POLL_INTERVAL_SECONDS):
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.poll_interval = max(1.0, float(poll_interval))
        self._since: float | None = None

    @property
    def enabled(self) -> bool:
        return self.timeout_seconds > 0

    def reset(self) -> None:
        self._since = None

    def tick(self, app, *, now: float | None = None) -> bool:
        """检查一次；返回 True 表示该退出了（调用方负责真的退）。

        Args:
            app: QApplication（用于枚举顶层窗口）
            now: 可注入的时间源（测试用）
        """
        if not self.enabled:
            return False
        moment = time.monotonic() if now is None else float(now)
        if has_visible_toplevel(app):
            if self._since is not None:
                LOGGER.info("窗口恢复可见，取消退出倒计时")
            self._since = None
            return False
        if self._since is None:
            self._since = moment
            LOGGER.info("当前没有可见窗口，%.0f 秒后若仍未恢复将自动退出",
                        self.timeout_seconds)
            return False
        elapsed = moment - self._since
        if elapsed >= self.timeout_seconds:
            LOGGER.warning(
                "已连续 %.0f 秒没有可见窗口（阈值 %.0f 秒），自动退出以免"
                "「看不见又启动不了」：下次双击快捷方式可正常启动",
                elapsed, self.timeout_seconds)
            return True
        return False
