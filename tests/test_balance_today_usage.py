# -*- coding: utf-8 -*-
"""今日已用（余额差值记账）与余额泡泡第二行。

对齐开源桌宠 ``deepseek-dafeiyu-pet`` 的 ``usage.json`` 口径：不读平台用量页、
不要额外令牌，只把**相邻两次余额读数之差**记成当天用量。因此四条边界必须钉死：

1. 跨天归零（北京时间），新的一天从当前余额重新起算；
2. 换 Key / 换账号只重置基准，不把两个账号的差额算成用量；
3. 一次掉太多（默认 60 秒内 > ¥20）算平台侧变动（赠送额度到期/退款），不计入并说明；
4. 充值（余额变多）只更新基准。

另外钉住接线：余额查询成功后记账并写进 payload（缓存命中路径也带得回来），
以及泡泡正文第二行确实是「今日已用 ¥x.xx」。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pet import app as app_mod  # noqa: E402
from pet.app import AppShell  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.island import balance as balance_mod  # noqa: E402
from pet.island import usage as usage_mod  # noqa: E402

app = QApplication.instance() or QApplication([])

BJ = ZoneInfo("Asia/Shanghai")
AT_10AM = datetime(2026, 11, 10, 10, 0, tzinfo=BJ)


def _ledger(tmp_path) -> Path:
    return tmp_path / usage_mod.USAGE_FILENAME


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# —— ① 记账四条边界 ————————————————————————————————

def test_first_reading_of_the_day_starts_from_zero(tmp_path):
    path = _ledger(tmp_path)
    result = usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    assert result["used"] == "0.00", "当天第一次读数只立基准，不算用量"
    assert result["reset"] is True
    state = _read(path)
    assert state["day"] == "2026-11-10"
    assert state["total"] == 6.30
    assert state["source"] == "acct-a"


def test_a_small_drop_is_counted_as_usage(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    result = usage_mod.record(path, total="6.02", source_id="acct-a",
                              now=AT_10AM + timedelta(minutes=5))
    assert result["used"] == "0.28"
    assert result["note"] == ""
    assert _read(path)["used"] == 0.28


def test_top_up_only_moves_the_baseline(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    usage_mod.record(path, total="5.90", source_id="acct-a", now=AT_10AM + timedelta(minutes=2))
    topped = usage_mod.record(path, total="30.00", source_id="acct-a",
                              now=AT_10AM + timedelta(minutes=3))
    assert topped["used"] == "0.40", "充值不减少已用量，也不把新余额记成消耗"
    again = usage_mod.record(path, total="29.90", source_id="acct-a",
                             now=AT_10AM + timedelta(minutes=9))
    assert again["used"] == "0.50", "充值后的消耗照常累计（按新基准）"


def test_a_sudden_huge_drop_is_not_usage_and_says_so(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="26.30", source_id="acct-a", now=AT_10AM)
    result = usage_mod.record(path, total="6.30", source_id="acct-a",
                              now=AT_10AM + timedelta(seconds=20))
    assert result["used"] == "0.00", "60 秒内掉 ¥20 以上判为平台侧变动"
    assert "没算进今日已用" in result["note"]
    assert "¥20.00" in result["note"], "说明里要写清掉了多少"


def test_a_slow_large_drain_is_still_usage(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="26.30", source_id="acct-a", now=AT_10AM)
    result = usage_mod.record(path, total="6.30", source_id="acct-a",
                              now=AT_10AM + timedelta(minutes=10))
    assert result["used"] == "20.00", "掉得多但掉得慢，就是真花掉了"
    assert result["note"] == ""


def test_new_day_resets_the_counter(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    usage_mod.record(path, total="6.00", source_id="acct-a", now=AT_10AM + timedelta(minutes=1))
    tomorrow = usage_mod.record(path, total="5.80", source_id="acct-a",
                                now=AT_10AM + timedelta(days=1))
    assert tomorrow["used"] == "0.00"
    assert tomorrow["reset"] is True
    assert _read(path)["day"] == "2026-11-11"
    assert _read(path)["total"] == 5.80


def test_switching_account_resets_the_baseline_only(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    usage_mod.record(path, total="6.00", source_id="acct-a", now=AT_10AM + timedelta(minutes=1))
    switched = usage_mod.record(path, total="99.00", source_id="acct-b",
                                now=AT_10AM + timedelta(minutes=2))
    assert switched["used"] == "0.30", "换账号不把两个账号的余额差算成用量"
    assert switched["reset"] is True
    assert _read(path)["source"] == "acct-b"


def test_corrupt_ledger_degrades_to_zero(tmp_path):
    path = _ledger(tmp_path)
    path.write_text("{not-json", encoding="utf-8")
    result = usage_mod.record(path, total="6.30", source_id="acct-a", now=AT_10AM)
    assert result["used"] == "0.00"
    assert _read(path)["day"] == "2026-11-10"


def test_missing_or_invalid_total_is_a_noop(tmp_path):
    path = _ledger(tmp_path)
    for total in ("", None, "  ", "不是金额"):
        result = usage_mod.record(path, total=total, source_id="acct-a", now=AT_10AM)
        assert result["used"] == ""
    assert not path.exists(), "没有可记的金额时不许凭空建账本"


def test_two_concurrent_readings_do_not_lose_a_drop(tmp_path):
    path = _ledger(tmp_path)
    usage_mod.record(path, total="10.00", source_id="acct-a", now=AT_10AM)
    errors: list = []

    def worker(delta: str, seconds: int):
        try:
            usage_mod.record(path, total=delta, source_id="acct-a",
                             now=AT_10AM + timedelta(seconds=seconds))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("9.90", 1)),
        threading.Thread(target=worker, args=("9.80", 2)),
        threading.Thread(target=worker, args=("9.70", 3)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)
    assert not errors
    state = _read(path)
    assert state["used"] > 0, "并发读改写不许把用量回滚掉"
    assert state["total"] in (9.90, 9.80, 9.70), "基准必须落在某个真实读数上"


# —— ② 泡泡第二行 ————————————————————————————————

def test_append_usage_line_puts_usage_on_its_own_line():
    text = usage_mod.append_usage_line("余额 ¥6.30（充值 ¥6.30 / 赠送 ¥0.00）", "0.28")
    assert text == "余额 ¥6.30（充值 ¥6.30 / 赠送 ¥0.00）\n今日已用 ¥0.28"


def test_append_usage_line_keeps_the_note_and_tolerates_missing_values():
    assert usage_mod.append_usage_line("余额 ¥6.30", "0.00") == "余额 ¥6.30\n今日已用 ¥0.00"
    assert usage_mod.append_usage_line("余额 ¥6.30", "") == "余额 ¥6.30"
    assert usage_mod.append_usage_line("余额 ¥6.30", None) == "余额 ¥6.30"
    noted = usage_mod.append_usage_line("余额 ¥6.30", "0.00", "余额少了 ¥20.00，看着不像用掉的")
    assert noted.endswith("（余额少了 ¥20.00，看着不像用掉的）")
    assert usage_mod.append_usage_line("", "0.28") == "今日已用 ¥0.28"


def test_show_balance_payload_renders_the_usage_line():
    """真 ``_show_balance_payload``：payload 带 usage 时，气泡正文多一行。"""

    class _Win(QWidget):
        def __init__(self):
            super().__init__()
            self.cfg = None
            self.bubbles: list[dict] = []

        def show_bubble(self, text, duration_ms=3200, subtitle=None, **_kwargs):
            self.bubbles.append({"text": text, "subtitle": subtitle})
            return True

    win = _Win()
    app_mod._show_balance_payload(win, {
        "text": "余额 ¥6.30（充值 ¥6.30 / 赠送 ¥0.00）",
        "info": {"total": "6.30"},
        "usage": "0.28",
    })
    assert win.bubbles, "必须真的冒泡"
    assert "今日已用 ¥0.28" in win.bubbles[0]["text"]
    assert win.bubbles[0]["text"].splitlines()[1] == "今日已用 ¥0.28"


# —— ③ 接线：查询成功 → 记账 → payload / 文件缓存都带得回来 ——————————

class _Bridge:
    def __init__(self):
        self.done = SimpleNamespace(emit=self._emit)
        self.results: list[tuple[bool, dict]] = []

    def _emit(self, ok, payload):
        self.results.append((ok, payload))


def _shell(tmp_path) -> AppShell:
    shell = AppShell.__new__(AppShell)
    shell.config = Config(base=tmp_path)
    shell._balance_cache = None
    shell._balance_cache_path = Path(shell.config.dir) / "balance_cache.json"
    shell._balance_busy = True
    shell._quiet_balance_busy = False
    return shell


def _run_worker(shell, bridge, total: str, provider_key: str) -> dict:
    def fake_fetch(base_url, api_key, timeout=10.0, verify_ssl=True):
        return {"is_available": True, "total": total, "granted": "0.00", "topped_up": total}

    original = balance_mod.fetch_balance
    balance_mod.fetch_balance = fake_fetch
    try:
        AppShell._balance_worker(
            shell, bridge, "https://api.deepseek.com", "sk-test", True, provider_key)
    finally:
        balance_mod.fetch_balance = original
    assert bridge.results, "worker 必须回一次结果"
    ok, payload = bridge.results[-1]
    assert ok is True
    return payload


def test_balance_worker_records_today_usage_into_payload_and_cache(tmp_path, monkeypatch):
    shell = _shell(tmp_path)
    ledger_path = Path(shell.config.dir) / usage_mod.USAGE_FILENAME

    first = _run_worker(shell, _Bridge(), "6.30", "acct-a")
    assert first["usage"] == "0.00", "当天第一次读数：今日已用 0.00（只立基准）"
    assert _read(ledger_path)["total"] == 6.30

    second = _run_worker(shell, _Bridge(), "6.02", "acct-a")
    assert second["usage"] == "0.28"

    # 文件缓存也带 usage：30 秒内缓存命中的那条路径不许少一行
    cached = shell._read_balance_file_cache("acct-a")
    assert cached is not None and cached["usage"] == "0.28"


def test_ledger_failure_never_breaks_the_balance_query(tmp_path, monkeypatch):
    """记账抛异常时，余额查询照常回结果，只是没有「今日已用」这一行。"""
    shell = _shell(tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("账本目录不可写")

    monkeypatch.setattr(usage_mod, "record", boom)
    payload = _run_worker(shell, _Bridge(), "6.30", "acct-a")
    assert payload["text"].startswith("余额 ¥6.30")
    assert "usage" not in payload
