# -*- coding: utf-8 -*-
"""输入框随内容自动增高（纯函数，便于测试）。

## 为什么不用 Qt 的字体度量

``QPlainTextEdit.document().size().height()`` 依赖字体度量：在没有字体的
环境（``QT_QPA_PLATFORM=offscreen``、系统缺字体目录、精简运行时）里恒为 0，
高度永远不涨——实测就是这样，改完看起来"没生效"。

所以这里只做**纯文本估算**：数换行，再按"约多少字折一行"补上折行。
不依赖 Qt、不依赖字体，结果确定，能写断言。

## 三处输入框

气泡输入框（``quick_chat``）、完整对话窗（``widgets``）、经典窗
（``legacy_widgets``）各有一套尺寸，但增高规则一样，共用这里的函数。
"""

from __future__ import annotations

#: 气泡宽度下的大致折行字数（中文按 1 个字符计）
DEFAULT_CHARS_PER_LINE = 38
#: 每多一行的像素增量
DEFAULT_LINE_STEP = 17


def estimate_lines(text: str, chars_per_line: int = DEFAULT_CHARS_PER_LINE) -> int:
    """估算渲染所需行数：换行算一行，超长行按字数折行。"""
    if not text or not text.strip():
        return 1
    limit = max(1, int(chars_per_line))
    lines = 0
    for raw in text.split("\n"):
        lines += max(1, (len(raw) + limit - 1) // limit)
    return max(1, lines)


def autosize_height(text: str, min_height: int, max_height: int,
                    chars_per_line: int = DEFAULT_CHARS_PER_LINE,
                    line_step: int = DEFAULT_LINE_STEP) -> int:
    """按内容算目标高度，夹在 ``[min_height, max_height]`` 之间。"""
    low = int(min_height)
    high = max(low, int(max_height))
    lines = estimate_lines(text, chars_per_line)
    desired = low + (lines - 1) * int(line_step)
    return max(low, min(high, desired))
