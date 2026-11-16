# -*- coding: utf-8 -*-
"""今日已用：本地记账（余额差值）。

**为什么是"记账"而不是"读平台用量"**：DeepSeek 开放平台只给 ``/user/balance``，
没有公开的"今日用量"接口（用量页要登录态）。所以只能像上游开源桌宠
（``deepseek-dafeiyu-pet`` 的 ``usage.json``）那样：**把相邻两次余额读数之差记成
当天用量**。这也决定了四条必须显式处理的边界——

1. **跨天归零**：按北京时间切日（与峰谷提示同一条时间口径），新的一天从当前余额
   重新起算，不把昨天剩下的差额算进来；
2. **换 Key / 换账号 / 换币种只重置基准**：两个账号的余额差不是用量。基准变了，
   ``used`` 保持不动（当天已经用掉的钱还是那些）；
3. **一次掉太多算平台侧变动**：赠送额度整块到期/被收回、退款、换套餐，都会让余额
   突然少一大截——默认 **60 秒内掉超过 ¥20** 判为平台变动，不计入今日已用，只回一句
   说明；
4. **充值（余额变多）只更新基准**：不把"充值后又用掉"的差额算成用量。

失败面：文件损坏、目录不可写、并发写——一律**降级为"这一次没有今日已用"**，
绝不能让余额查询本身失败（余额是主功能，用量只是附加信息）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

#: 记账文件名（放在配置目录里，与 ``balance_cache.json`` 同级）
USAGE_FILENAME = "usage.json"

#: "一次掉太多"的判据：窗口内掉这个金额（元）及以上就当作平台侧变动（含等号）
SUDDEN_DROP_WINDOW_SECONDS = 60.0
SUDDEN_DROP_AMOUNT = 20.0

_DAY_TZ = ZoneInfo("Asia/Shanghai")

#: 读-改-写整段互斥：余额查询有两个独立线程（显式查询 / 岛卡片静默刷新）可以同时回来
_LOCK = threading.Lock()


def beijing_day(now: datetime | None = None) -> str:
    """当前（或入参）北京时间所属日期，``YYYY-MM-DD``。naive 视为北京时间。"""
    if now is None:
        moment = datetime.now(_DAY_TZ)
    elif now.tzinfo is None:
        moment = now.replace(tzinfo=_DAY_TZ)
    else:
        moment = now.astimezone(_DAY_TZ)
    return moment.date().isoformat()


def _to_float(raw) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def load_state(path) -> dict:
    """读账本；损坏/不存在一律当空账本（不抛异常）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path, state: dict) -> bool:
    """原子写账本（同 ``balance_cache.json`` 的 tmp + replace）。

    返回是否写成功；失败只记 debug 日志——用量是附加信息，不许顶掉余额查询。
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{os.getpid()}.usage.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return True
    except OSError:
        logging.debug("今日已用账本写入失败", exc_info=True)
        return False


def record(path, *, total, source_id: str = "", now: datetime | None = None) -> dict:
    """记一次余额读数，返回 ``{"used": "0.28", "note": "", "reset": bool}``。

    ``total`` 是本次余额（字符串或数字，带不带 ¥ 都行）；``source_id`` 是这次读数
    所属的账号身份（AppShell 传的是 provider 键：``id|base_url|key_digest``），
    换账号时只重置基准。``used`` 为空串表示"这次没有可用的今日已用"。
    """
    amount = _to_float(str(total).replace("¥", "").replace("￥", "").strip())
    if amount is None:
        return {"used": "", "note": "", "reset": False}
    day = beijing_day(now)
    stamp = (now or datetime.now(_DAY_TZ)).timestamp()
    with _LOCK:
        state = load_state(path)
        used = _to_float(state.get("used")) or 0.0
        same_day = str(state.get("day", "") or "") == day
        same_source = str(state.get("source", "") or "") == str(source_id or "")
        previous = _to_float(state.get("total")) if same_day and same_source else None
        note = ""
        reset = False
        if not same_day:
            # 新的一天：基准重置，昨天的差额不带过来
            used = 0.0
            reset = True
        elif previous is not None:
            # 两位小数的余额相减会出现 19.999999999999996 这种噪声：先按「分」取整，
            # 否则「正好掉 ¥20」会一会儿算用量、一会儿不算。
            delta = round(previous - amount, 2)
            if delta >= SUDDEN_DROP_AMOUNT and (stamp - float(state.get("at", 0) or 0)) \
                    <= SUDDEN_DROP_WINDOW_SECONDS:
                note = f"余额少了 ¥{delta:.2f}，看着不像用掉的，这次没算进今日已用"
            elif delta > 0:
                used = max(0.0, round(used + delta, 2))
        elif same_day and not same_source:
            reset = True  # 换了账号：只重置基准
        save_state(path, {
            "version": 1,
            "day": day,
            "used": round(used, 2),
            "total": round(amount, 2),
            "source": str(source_id or ""),
            "at": stamp,
        })
    return {"used": f"{used:.2f}", "note": note, "reset": reset}


def format_used_line(used) -> str:
    """``今日已用 ¥0.28``；金额非法返回空串。"""
    value = _to_float(used)
    if value is None:
        return ""
    return f"今日已用 ¥{value:.2f}"


def append_usage_line(text: str, used, note: str = "") -> str:
    """把「今日已用」（以及可选的说明）接在余额正文后面。

    没有可用金额、也没有说明时原样返回。单独成函数是为了让"泡泡几行、每行写什么"
    能脱离 Qt 被测——真正上屏前还要过 ``format_balance_display`` 与气泡的自动换行。
    """
    lines = [str(text or "").strip()]
    lines.append(format_used_line(used))
    note_text = str(note or "").strip()
    if note_text:
        lines.append(f"（{note_text}）")
    body = [line for line in lines if line]
    return "\n".join(body)
