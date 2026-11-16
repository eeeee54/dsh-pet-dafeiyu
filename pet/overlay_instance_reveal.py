"""在被单实例门挡住时，尝试唤出已有实例的窗口。

## 为什么需要

`overlay_instance_gate` 保证只有一个进程；但 `setQuitOnLastWindowClosed(False)`
意味着"关掉窗口 ≠ 退出进程"。于是会出现这个死局：

1. 用户关掉桌宠窗口（或误触）→ 进程还在，窗口没了
2. 用户双击桌面快捷方式 → 新进程被单实例门拒绝，**静默退出**
3. 用户看到的现象就是"快捷方式打不开了"

托盘菜单里其实有「生成 / 恢复 slot0」，但托盘图标经常被折叠进隐藏区，
用户根本找不到。

## 做法

新进程被拒时，不立刻退出，先去找已存在实例的窗口：
* 找到 → `ShowWindow(SW_SHOW)` + `SetForegroundWindow`，用户立刻看见桌宠
* 找不到（窗口真被销毁了）→ 打印能看懂的话，提示去托盘恢复

只动 Windows；其他平台保持原样（直接退出）。
"""

from __future__ import annotations

import logging
import sys

LOGGER = logging.getLogger(__name__)

#: 桌宠窗口的标题前缀（`QApplication.setApplicationName(APP_DIR_NAME)`）
WINDOW_TITLE_HINT = "dsh-pet"


def _windows_window_candidates() -> list[int]:
    """枚举属于该进程链、标题含 dsh-pet 的顶层窗口。"""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetWindowTextW(hwnd, buf, length + 2)
        if WINDOW_TITLE_HINT in buf.value.lower():
            found.append(int(hwnd))
        return True

    try:
        user32.EnumWindows(enum_proc(_cb), 0)
    except Exception:
        LOGGER.debug("枚举窗口失败", exc_info=True)
    return found


def reveal_existing_instance() -> bool:
    """让已有实例的窗口显示出来；至少成功一个就返回 True。

    桌宠通常有两个顶层窗口（16×64 的托盘锚点窗 + 全屏 overlay 合成分身窗），
    **都要**恢复——只恢复小的那个，用户还是看不到鱼。
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    SW_SHOW = 5
    SW_RESTORE = 9
    shown = 0
    for hwnd in _windows_window_candidates():
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            else:
                user32.ShowWindow(hwnd, SW_SHOW)
            user32.SetForegroundWindow(hwnd)
            shown += 1
            LOGGER.info("已唤出正在运行的桌宠窗口: hwnd=%s", hwnd)
        except Exception:
            LOGGER.debug("唤出窗口失败 hwnd=%s", hwnd, exc_info=True)
    return shown > 0


def handle_second_launch(logger=None) -> int:
    """被单实例门拒绝后的收尾：先试唤出，再给出可执行的提示。

    返回进程退出码（0 = 已经让用户看到东西了）。
    """
    log = logger or LOGGER
    if reveal_existing_instance():
        return 0
    log.warning(
        "桌宠已在运行，但没找到可显示的窗口。请在系统托盘（可能折叠在 ^ 里）"
        "右键桌宠图标 → 「生成 / 恢复 slot0」；或者先结束残留进程再启动。"
    )
    # 把提示也打到 stdout，方便从命令行启动时直接看见
    print(
        "桌宠已在后台运行但没有窗口。\n"
        "  方法一：系统托盘（可能折叠在 ^ 箭头里）右键图标 → 「生成 / 恢复 slot0」\n"
        "  方法二：任务管理器结束 pythonw.exe，再双击快捷方式",
        file=sys.stderr,
    )
    return 0
