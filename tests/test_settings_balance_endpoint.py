# -*- coding: utf-8 -*-
"""「余额与服务状态」组里的余额专用端点三行（2026-11-10）。

背景：``GET /user/balance`` 只存在于 DeepSeek 开放平台，而余额查询**默认跟随聊天
provider**（``balance.resolve_balance_endpoint`` 在 ``balance_base_url`` 留空时回退
到 chat 的 base_url + api_key）。聊天换到 Ollama / 通义之后，余额就只剩一句
「当前服务商无余额接口」。``config.json`` 里早就有 ``balance_base_url`` /
``balance_api_key_ref`` 两个键、读方也一直在用，但设置页没有入口——用户只能手改
配置文件（2026-11-10 实机现场：provider ``deeepseek`` 指向
``http://127.0.0.1:11434/v1``，余额永远查不到）。

本文件钉住这三行（地址 / Key / 测试按钮）的行为契约：

1. 三行挂在「余额与服务状态」组里，初值与占位文案跟着配置；
2. 保存 round trip：地址写 ``balance_base_url``；Key 进系统钥匙串
   （``balance/deepseek``）、引用写 ``balance_api_key_ref``，输入框清空、磁盘无明文；
3. 清空地址 = 回到「跟随聊天 provider」（键写空串，不是保留旧值）；
4. 「测试余额」按**表单当前值**查询，并把结果显示在行内说明里（成功/失败都显示）；
5. 服务商没有余额接口时立刻给出人话，一个请求都不发；
6. 灵动岛模块未激活时这一组不挂出 ⇒ 控件快照**绝不**写回配置（否则打开一次设置
   就把用户手配的余额端点抹成空串）。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

import pet.island.balance as balance_mod  # noqa: E402
import pet.modern_settings_dialog as settings_mod  # noqa: E402
from pet import feature_activation as fa  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.features import MIGRATION_BASELINE  # noqa: E402
from pet.modern_settings_dialog import SettingRow  # noqa: E402
from pet.settings_widgets import SettingsSection  # noqa: E402

app = QApplication.instance() or QApplication([])

ENDPOINT_ROW = "balance_endpoint"
KEY_ROW = "balance_key"
TEST_ROW = "balance_test"
GROUP_TITLE = "余额与服务状态"


class _FakeStore:
    """最小钥匙串替身：**类级**共享一份字典（产品代码每次 new 一个实例）。"""

    stored: dict[str, str] = {}

    def get(self, ref):
        return type(self).stored.get(ref, "")

    def set(self, ref, value):
        type(self).stored[ref] = value
        return True


@pytest.fixture(autouse=True)
def _clean_state():
    fa.reset_snapshot_for_tests()
    _FakeStore.stored.clear()
    yield
    fa.reset_snapshot_for_tests()
    _FakeStore.stored.clear()


def _dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)
    monkeypatch.setattr(dialog.ai_page, "_secret_store_type", _FakeStore)
    return dialog


def _row(dialog, key) -> SettingRow:
    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def _group_of(row: SettingRow) -> SettingsSection:
    widget = row.parentWidget()
    while widget is not None and not isinstance(widget, SettingsSection):
        widget = widget.parentWidget()
    assert widget is not None, "行没有挂在任何设置组里"
    return widget


def _group_title(section: SettingsSection) -> str:
    label = section.findChild(QLabel, "sectionTitle")
    return label.text() if label is not None else ""


def _wait_for_balance_test(dialog) -> None:
    """等测试线程收尾 + 把排队回来的信号跑掉（不固定 sleep 猜时序）。"""
    thread = dialog.ai_page._balance_test_thread
    if thread is not None:
        thread.join(15.0)
    app.processEvents()
    app.processEvents()


# —— ① 三行真的挂进「余额与服务状态」组 ————————————————————————

def test_group_shows_address_key_then_self_test_in_that_order(tmp_path, monkeypatch):
    """组内顺序：地址 → Key → 自检。宿主按 findChildren 收行，创建顺序即显示顺序。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        group = _group_of(_row(dialog, ENDPOINT_ROW))
        names = [row.objectName() for row in group.rows]
        assert names[-3:] == [
            f"settingRow_{ENDPOINT_ROW}",
            f"settingRow_{KEY_ROW}",
            f"settingRow_{TEST_ROW}",
        ], names
    finally:
        dialog.close()


def test_three_rows_are_mounted_in_the_balance_and_status_group(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for key in (ENDPOINT_ROW, KEY_ROW, TEST_ROW):
            row = _row(dialog, key)
            assert _group_title(_group_of(row)) == GROUP_TITLE, (
                f"{key} 必须挂进「{GROUP_TITLE}」组（余额专用端点与自动刷新同一组）")
        assert _row(dialog, ENDPOINT_ROW).control.text() == ""
        assert _row(dialog, ENDPOINT_ROW).control.placeholderText(), "留空时的语义要写在占位文案里"
        assert _row(dialog, KEY_ROW).control.text() == "", "Key 不回落显示已存值（不回显凭据）"
        assert _row(dialog, TEST_ROW).control.text() == "测试余额"
        # 页面自报的三行（宿主 claim 的是同一批控件，两处口径必须一致）
        assert [r.objectName() for r in dialog.ai_page.balance_rows()] == [
            f"settingRow_{ENDPOINT_ROW}",
            f"settingRow_{KEY_ROW}",
            f"settingRow_{TEST_ROW}",
        ]
    finally:
        dialog.close()


def test_endpoint_row_starts_from_the_configured_value(tmp_path, monkeypatch):
    cfg = Config(base=tmp_path)
    cfg.set("balance_base_url", "https://api.deepseek.com")
    cfg.save()
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        assert _row(dialog, ENDPOINT_ROW).control.text() == "https://api.deepseek.com"
    finally:
        dialog.close()


# —— ②③ 保存语义：地址进配置、Key 进钥匙串、清空即回退 ————————————

def test_endpoint_and_key_save_round_trip(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        _row(dialog, ENDPOINT_ROW).control.setText("https://api.deepseek.com")
        _row(dialog, KEY_ROW).control.setText("sk-balance-only")
        assert dialog._write_config() is True
        assert dialog.ai_page.balance_key.text() == "", "保存后必须清空 Key 输入框"
    finally:
        dialog.close()

    cfg = Config(base=tmp_path)
    assert cfg.get("balance_base_url") == "https://api.deepseek.com"
    assert cfg.get("balance_api_key_ref") == balance_mod.DEFAULT_BALANCE_KEY_REF
    assert _FakeStore.stored[balance_mod.DEFAULT_BALANCE_KEY_REF] == "sk-balance-only"
    on_disk = cfg.path.read_text(encoding="utf-8")
    assert "sk-balance-only" not in on_disk, "凭据不许落明文"
    assert json.loads(on_disk)["balance_base_url"] == "https://api.deepseek.com"

    reopened = _dialog(tmp_path, monkeypatch)
    try:
        assert _row(reopened, ENDPOINT_ROW).control.text() == "https://api.deepseek.com"
        assert _row(reopened, KEY_ROW).control.text() == "", "重开也不回显凭据"
    finally:
        reopened.close()


def test_clearing_the_endpoint_falls_back_to_the_chat_provider(tmp_path, monkeypatch):
    """留空 = 跟随聊天 provider，所以空串必须真的写回（不是保留旧地址）。"""
    cfg = Config(base=tmp_path)
    cfg.set("balance_base_url", "https://api.deepseek.com")
    cfg.save()
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        _row(dialog, ENDPOINT_ROW).control.setText("")
        assert dialog._write_config() is True
    finally:
        dialog.close()
    assert Config(base=tmp_path).get("balance_base_url") == ""


# —— ④ 测试按钮：按表单值查询、结果落在行内说明里 ————————————————

def test_test_button_queries_the_form_endpoint_and_shows_the_result(tmp_path, monkeypatch):
    calls: list[tuple] = []

    def fake_fetch(base_url, api_key, timeout=10.0, verify_ssl=True):
        calls.append((base_url, api_key, verify_ssl))
        return {"is_available": True, "total": "6.30", "granted": "0.00", "topped_up": "6.30"}

    monkeypatch.setattr(balance_mod, "fetch_balance", fake_fetch)
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        _row(dialog, ENDPOINT_ROW).control.setText("https://api.deepseek.com")
        _row(dialog, KEY_ROW).control.setText("sk-from-form")
        _row(dialog, TEST_ROW).control.click()
        _wait_for_balance_test(dialog)
        assert calls == [("https://api.deepseek.com", "sk-from-form", True)]
        assert _row(dialog, TEST_ROW).hint_label.text() == "余额 ¥6.30（充值 ¥6.30 / 赠送 ¥0.00）"
        assert _row(dialog, TEST_ROW).control.isEnabled(), "查询结束必须恢复按钮"
        assert _row(dialog, TEST_ROW).control.text() == "测试余额"
    finally:
        dialog.close()


def test_test_button_falls_back_to_the_saved_keychain_entry(tmp_path, monkeypatch):
    """表单不填 Key（已存过）时回退钥匙串，否则默认场景每次都误报「未配置 API Key」。"""
    seen: list[tuple] = []
    monkeypatch.setattr(
        balance_mod, "fetch_balance",
        lambda base_url, api_key, timeout=10.0, verify_ssl=True:
        seen.append((base_url, api_key)) or {"total": "1.00", "granted": "", "topped_up": ""},
    )
    _FakeStore.stored[balance_mod.DEFAULT_BALANCE_KEY_REF] = "sk-from-keychain"
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        _row(dialog, ENDPOINT_ROW).control.setText("https://api.deepseek.com")
        _row(dialog, TEST_ROW).control.click()
        _wait_for_balance_test(dialog)
        assert seen == [("https://api.deepseek.com", "sk-from-keychain")]
    finally:
        dialog.close()


def test_test_button_reports_a_provider_without_a_balance_api(tmp_path, monkeypatch):
    """Ollama / 通义没有 /user/balance：一个请求都不发，直接说人话。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        _row(dialog, ENDPOINT_ROW).control.setText("http://127.0.0.1:11434/v1")
        _row(dialog, KEY_ROW).control.setText("ollama")
        _row(dialog, TEST_ROW).control.click()
        _wait_for_balance_test(dialog)
        message = _row(dialog, TEST_ROW).hint_label.text()
        assert "没有公开的余额接口" in message, message
        assert "127.0.0.1:11434" in message, "要指出是哪个地址没有余额接口"
        assert _row(dialog, TEST_ROW).control.isEnabled()
    finally:
        dialog.close()


# —— ⑥ 灵动岛模块未激活：这一组不挂出，控件值不许写回 ————————————

def test_rows_are_not_written_back_when_the_island_module_is_inactive(tmp_path, monkeypatch):
    fa.freeze_snapshot_for_tests(
        [fid for fid in MIGRATION_BASELINE if fid != "dynamic_island"])
    cfg = Config(base=tmp_path)
    cfg.save()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)
    try:
        # 打开设置窗期间外部（手改 config.json / 另一个设置窗）写下了端点
        dialog.config.set("balance_base_url", "https://api.deepseek.com")
        dialog.config.save()
        assert dialog._write_config() is True
    finally:
        dialog.close()
    assert Config(base=tmp_path).get("balance_base_url") == "https://api.deepseek.com", (
        "模块未激活时控件只是构造了、没有挂出，写回空串会抹掉用户手配的端点")


# —— ⑦ 默认值标记与搜索 ————————————————————————————————

def test_endpoint_row_has_a_comparable_default(tmp_path, monkeypatch):
    from pet import settings_default_marks

    assert settings_default_marks._ROW_DEFAULT_PATHS[ENDPOINT_ROW] == "balance_base_url"
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog, ENDPOINT_ROW)
        assert row.default_reset_button.isHidden(), "默认态不出「已修改」入口"
        row.control.setText("https://api.deepseek.com")
        app.processEvents()
        assert not row.default_reset_button.isHidden()
    finally:
        dialog.close()


def test_rows_are_reachable_by_search(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for query in ("余额接口", "钥匙串", "测试余额"):
            dialog.search_edit.setText("")
            app.processEvents()
            dialog.search_edit.setText(query)
            app.processEvents()
            matched = {row.objectName() for row in dialog._search_matches}
            assert matched & {
                f"settingRow_{ENDPOINT_ROW}",
                f"settingRow_{KEY_ROW}",
                f"settingRow_{TEST_ROW}",
            }, f"搜「{query}」应当命中余额端点组里的行"
    finally:
        dialog.close()
