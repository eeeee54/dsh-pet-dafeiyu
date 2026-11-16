# 余额专用端点设置行（「余额接口地址 / 余额 API Key / 测试余额」）— 2026-11-10

> **基线**：本工作区是**没有 `.git` 的源码副本**（`git rev-parse` 报
> `not a git repository`），因此本报告给不出 commit sha 与 `git diff --numstat`；
> 增删行数按「文件总行数变化」给出，另有逐文件说明。
> **日期**：2026-11-10　**范围**：4 个文件（实现 2、测试 2）
> **关联**：[`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md) 的 2026-11-10 条；
> 余额查询链本身见 [`../pet/island/balance.py`](../pet/island/balance.py)。
> **同日配套**：[`PR-REPORT-BALANCE-TODAY-USAGE-2026-11-10.md`](PR-REPORT-BALANCE-TODAY-USAGE-2026-11-10.md)
> （余额气泡的「今日已用」第二行与 `usage.json` 记账）。

## 一、核心特性

**问题（不是"查询坏了"，是"配不了"）**：`GET /user/balance` 只有 DeepSeek 开放平台
有，而余额查询默认跟随**聊天** provider（`balance.resolve_balance_endpoint` 在
`balance_base_url` 留空时回退到 chat 的 base_url + api_key）。`config.json` 里早就有
`balance_base_url` / `balance_api_key_ref` 两个键、读方也一直在用，**但设置页没有
入口**——「余额与服务状态」这一组当时只有自动刷新与峰谷文案。于是"聊天走 Ollama /
通义，余额还想看 DeepSeek"这件事只能手改配置文件。

实机现场（2026-11-10 用户报「为什么没办法实时提示余额」）：聊天 provider 指着
`http://127.0.0.1:11434/v1`（本机 Ollama），余额就永远只有一句
「当前服务商无余额接口」。

**变化**：`AI 与对话 → 余额与服务状态` 组末尾多三行：

| # | 能力 | 说明 |
|---|---|---|
| 1 | 余额接口地址 | 写 `balance_base_url`。**留空 = 跟随聊天 provider**（旧行为逐位不变）；填 `https://api.deepseek.com` 就能「聊天走别家、余额看 DeepSeek」。 |
| 2 | 余额 API Key | 与聊天 Key **分开**存进系统钥匙串（条目 `balance/deepseek`），配置文件里只有引用、没有明文；输入框不回显已存值。 |
| 3 | 测试余额 | 按**表单当前值**同步一次余额，结果显示在这一行的说明里（成功绿色、失败红色），不发请求前先判"这家有没有余额接口"。 |

**红线 / 不变量**：

1. **不新增任何配置键**——`balance_base_url` / `balance_api_key_ref` 自 v4.4 起就在
   `Config._initial_data`、reload 白名单与 `_initial_data` 快照里，本刀只补 UI 与写回；
2. `resolve_balance_endpoint` 的判定语义与 `fetch_balance` 的请求形态**一个字没改**；
3. 地址留空时的回退路径（老用户与默认配置）逐位不变；
4. 灵动岛模块未激活时这一组不挂出，其控件值**绝不写回配置**；
5. Key 不进日志、不进配置文件、不进控件回显。

## 二、修改文件说明

### 实现

| 文件 | 增减 | 改动意图 |
|---|---|---|
| [`pet/chat/ai_settings_page.py`](../pet/chat/ai_settings_page.py) | `1251 → 1390` 行（**+139 / −0**） | ① 类上新增 `balance_test_done = Signal(bool, str)`；② `__init__` 里造三行（地址 `_line_edit`、Key `_line_edit(password=True)`、"测试余额" `QPushButton` + `SettingRow`），**不进本页布局**（宿主按 `settingRow_balance_` 前缀认领，见§三）；③ 五个方法：`balance_rows()`、`_balance_test_targets()`、`_run_balance_test()`、`_balance_test_worker()`、`_on_balance_test_done()`、`_save_balance_endpoint()`；④ `save()` 末尾一行 `self._save_balance_endpoint()`。 |
| [`pet/settings_default_marks.py`](../pet/settings_default_marks.py) | `280 → 282` 行（**+2 / −0**） | `_ROW_DEFAULT_PATHS` 加 `"balance_endpoint": "balance_base_url"`：这一行的出厂默认是空串，能与配置直接比，因此"填了地址"会点亮「已修改」标记与单项「改回默认」。Key 行**不登记**（凭据字段，控件里永远是空的，与 `web_search_key` 同口径）。 |

### 测试

| 文件 | 增减 | 覆盖 |
|---|---|---|
| [`tests/test_settings_balance_endpoint.py`](../tests/test_settings_balance_endpoint.py) | **新文件 285 行 / 11 例** | ① 三行真的挂在「余额与服务状态」组里、且顺序是 地址→Key→测试；② 初值跟着配置；③ 保存 round trip（地址进配置、Key 进钥匙串且**磁盘无明文**、输入框清空、重开不回显）；④ 清空地址 = 回退聊天 provider（写空串，不是保留旧值）；⑤ 测试按钮走表单端点 + 表单 Key；⑥ 表单没填 Key 时回退钥匙串里那把；⑦ Ollama 地址 → 立刻给"没有公开的余额接口"且一个请求都不发；⑧ 灵动岛模块未激活 → 绝不把控件快照写回；⑨ 默认值标记；⑩ 搜索「余额接口 / 钥匙串 / 测试余额」命中。 |
| [`tests/test_settings_default_marks.py`](../tests/test_settings_default_marks.py) | `482 → 484` 行（**+2 / −0**） | `NO_DEFAULT_ROWS` 快照加 `balance_key`（凭据字段，没有可比默认值）。这是该文件"新增四类控件行必须显式归类"的既有机器门，不加就红。 |

### 未改动（明确写出，避免评审以为漏了）

- `pet/island/balance.py`：`balance_supported` / `resolve_balance_endpoint` /
  `fetch_balance` 一字未动——这一刀是"给它配入口"，不是"改它的判定"。
- `pet/app.py` 的四条余额消费路径（`show_balance` / `_quiet_balance_refresh` /
  `_apply_balance_timer` / 消费统计）未动。
- `pet/modern_settings_dialog.py` 未动：`claim_prefix("balance_")` 本来就会认领任何
  `settingRow_balance_*`，加行不需要宿主写代码（也顺带守住了它的行数预算）。

## 三、实现要点

**① 行不进本页布局，靠宿主认领**。`modern_settings_dialog._rebuild_domain_navigation`
在域装配时用 `claim_prefix("balance_")` 把 `settingRow_balance_*` 收进
「余额与服务状态」组；行必须**是对话框的后代**（`findChildren` 能看见）但**不在任何
已挂布局里**（否则会同时出现在 AI 页里）。所以三行以 `parent=self` 构造、只挂在
`self._balance_rows` 上。**踩过的坑**：第一版忘了传 `parent`，行成了无父顶层对象，
`findChild` 与 `claim_prefix` 都看不见，10 例里 9 例红。

**② 显示顺序 = 创建顺序**。`claim_prefix` 按 `findChildren` 的树序收行，不是按
`_balance_rows` 的列表顺序；"测试余额"行因此必须**最后**创建，否则它会插到
地址与 Key 前面（实测第一版顺序是 测试→地址→Key）。用例
`test_group_shows_address_key_then_self_test_in_that_order` 钉住这条，防止以后有人
"顺手"把按钮行提前造。

**③ 说明文案不能塞长 URL**。`SettingRow` 的说明是自动换行 `QLabel`，它的
`minimumSizeHint` 取**最长不可断词**——正文里放 `https://api.deepseek.com` 会把 AI
域的最小宽度顶到 **484px**，720 宽窗口下内容比视口（454px）宽 30px，
`test_settings_sidebar_and_chrome` 的两条宽度契约用例立刻红。修法：URL 挪进输入框
占位文案（占位文案不参与 `QLineEdit.sizeHint`），正文只留中文。实测 484 → **454**。

**④ 写回带模块闸**。`_save_balance_endpoint` 先看 `feature_activation.is_active(
"dynamic_island")`：模块未激活时整组不挂出，行只是构造期快照，无条件写回会把用户在
别处配好的端点抹成空串。`test_rows_are_not_written_back_when_the_island_module_is_inactive`
把这条钉死（构造对话框 → 期间外部写盘 → `_write_config` → 端点仍在）。

**⑤ 测试按钮 = 后台线程 + 队列信号**。`fetch_balance` 有 10s 超时，必须在 worker 线程
里跑；结果经 `balance_test_done`（`_safe_emit`，对话框可能在飞行中被销毁）回到主线程。
目标端点按"表单优先"取：地址留空 = 整对回退聊天 provider；地址填了 = **只**用余额
专用 Key（拿 DashScope 的 Key 去问 DeepSeek 只会 401「Authorization Failed」，那是最难
诊断的一种失败）；表单没填 Key 就回退钥匙串里 `balance_api_key_ref`（默认
`balance/deepseek`）。

**⑥ 结果显示在行内说明里，不是新建 QLabel**。`SettingRow` 的说明只收字符串——同页
「连接测试」行把结果写进一个**没挂进任何布局**的 `QLabel`（`self.test_result`），所以
那行其实从来没显示过结果（既有缺陷，见§七）。本行改为 `_on_balance_test_done` 时
直接 `self._balance_test_row.hint_label.setText(message)`，成功绿、失败红。

## 四、性能分析

**方法（可复现）**：`python .scratch/balance-endpoint-settings-20261110/probe_shot.py`
（本机 = Windows 11 / Python 3.12.14 / PySide6 6.12.0 / `QT_QPA_PLATFORM=offscreen`
+ `QT_QPA_FONTDIR=C:\Windows\Fonts`）；宽度消融同目录的 `probe_width.py`（构建对话框 →
量 `settingsDomain_ai` 与滚动视口宽 → `setParent(None)` 拆掉三行 → 再量）。

| 指标 | 实测 | 归属 |
|---|---|---|
| AI 设置页整页构建（含三行，5 次均值） | **27.33 ms** | 既有路径；本刀只加 3 行 |
| 单个 `SettingRow`（+3 控件）构造 | **0.319 ms**（n=200） | 新增；×3 ≈ **0.96 ms**（≈整页的 3.5%） |
| AI 域最小宽度（含三行 / 不含三行） | **484px / 454px** → 改文案后 **454px / 454px** | 本刀引入并修复 |
| 「测试余额」点击到结果回填 | 1 次 HTTP GET，10s 超时上限；不点时 **0** | 新增（用户动作级） |
| 稳态（设置窗开着但没点测试） | 0 个定时器、0 个线程、0 次网络 | 本刀不新增 |

**结论**：

1. **稳态开销**：零。三行只是静态控件；没有定时器、没有后台线程、不读钥匙串
   （已存 Key **不回显**，所以构造期一次 `SecretStore.get` 都不发）。
2. **新增路径成本与触发频率**：只有"用户点测试余额"这一个动作会起线程 + 一次
   `GET /user/balance`；设置窗构建多 ~1 ms（一次性）。
3. **系统调用 / 网络 / 磁盘 / 线程**：构造期零新增；保存时多一次
   `SecretStore.set`（仅当用户真的填了 Key）+ 两个 `config.set`；没有新增文件。
4. **内存**：三个控件 + 三行 QFrame，量级 < 10 KiB；没有缓存、没有常驻引用
   （行由对话框持有，关窗即销毁）。

## 五、实机运行记录

**① 根因/前提的现场复现（用户机器上的真实余额接口）**

同一把 Key 直连官方接口（命令：`Invoke-WebRequest`，2026-11-10 10:50 前后）：

```
https://api.deepseek.com/user/balance      -> 200 {"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"6.30",...}]}
https://api.deepseek.com/v1/user/balance   -> 200 （同上）
```

`/v1` 前缀不是问题 ⇒ 余额查不到的原因在**端点/凭据之外**：要么聊天 provider 不是
DeepSeek（本机就是 `127.0.0.1:11434`），要么提示开关没开。本次补的是前者缺的入口。

**② 用户可见行为的确认（离屏真实窗口渲染）**

真对话框（`ModernSettingsDialog` + 真 `Config`）在 980×1000 渲染，滚到组尾：

![余额与服务状态组（含三行）](../../.scratch/balance-endpoint-settings-20261110/group-bottom.png)

- 组内 8 行、顺序 = 自动刷新 → 时段提示 → 高峰文本 → 空闲文本 → 峰谷颜色 →
  **余额接口地址 → 余额 API Key → 测试余额**；
- 占位文案 `例：https://api.deepseek.com` 完整显示（第一版被裁成
  `…；留…`，已缩短）；「测试余额」按钮在行右侧；
- **本机 Qt 运行时没有自带字体**（`QFontDatabase: Cannot find font directory …`），
  默认渲染全是豆腐块，截图前设了 `QT_QPA_FONTDIR=C:\Windows\Fonts`
  ——这条也解释了为什么别的机器上看到的字样与本机截图不同。
- 整组截图（含 5 行既有设置）：`../../.scratch/balance-endpoint-settings-20261110/group-top.png`。

**③ 边界与失败路径（实机观察）**

- 端点填 `http://127.0.0.1:11434/v1`（本机 Ollama，真实在线）→ 点测试余额，
  行内立刻变成「查询失败：127.0.0.1:11434 没有公开的余额接口，余额查询只对 DeepSeek
  开放平台有效」，**没有发出任何请求**（`fetch_balance` 在 `balance_supported` 处早退）；
- 端点填 DeepSeek、Key 留空且钥匙串里没有 → 「查询失败：未配置 API Key」；
- 钥匙串不可用（`SecretStore.set` 返回 False）→ 弹「安全存储不可用…」，**不写**
  `balance_api_key_ref`、**不清**输入框（写了引用却读不到 Key 只会让人以为存好了）。

**④ 无法自动验证的能力**

- **真实钥匙串写入**：单测一律用内存替身（`_FakeStore`）。本机 `provider/api-3` 里那把
  Key 是产品真实写入的（`sha256` 前 12 位 `b8ffa62c97ba`、长度 35），只能证明**既有**
  通道可用，不能证明本刀新增的 `balance/deepseek` 条目已被真实写入——我没有在用户机器
  上执行"填写并保存"这一步（那会改用户的真实凭据）。
- **macOS / Linux 真实 GUI**：未跑，不推断。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/chat/ai_settings_page.py pet/settings_default_marks.py tests/test_settings_balance_endpoint.py tests/test_settings_default_marks.py` | **All checks passed** |
| 先红（实现前） | `pytest -q tests/test_settings_balance_endpoint.py` | **9 failed / 1 passed**（唯一绿的是"模块未激活不写回"，无行时天然成立） |
| 聚焦（实现后） | 同上 | **11 passed** |
| 受影响族 ① | `pytest -q tests/test_settings_default_marks.py tests/test_settings_page_guide.py tests/test_architecture.py tests/test_settings_ia_first_cut.py tests/test_settings_compat_layering.py tests/test_settings_search_aliases.py` | **112 passed** |
| 受影响族 ② | `pytest -q tests/test_settings_sidebar_and_chrome.py tests/test_settings_balance_endpoint.py` | **30 passed**（宽度契约两条：修文案前红、修后绿） |
| 受影响族 ③（宽） | `pytest -q tests/test_feature_modules_gating.py tests/test_chat_subsystem.py tests/test_requested_regressions.py tests/test_settings_sidebar_and_chrome.py tests/test_settings_and_resources.py tests/test_settings_balance_endpoint.py` | **344 passed / 7 failed / 2 skipped**（7 条见下「既有失败」） |
| 文档门 | `pytest -q tests/test_pr_report_discipline.py` | **231 passed** |
| 全量 | `pytest -q` | **未跑完**（见下「全量为什么没跑完」） |

**既有失败（7 条，均与本刀无关，逐条给了证据）**：

| 用例 | 失败断言 | 为什么与本刀无关 |
|---|---|---|
| `test_feature_modules_gating.py::test_vision_hotkeys_not_registered_when_inactive` | 绑定期望 `{"open_chat": "ctrl+alt+c"}`，实际多一条 `{'voice_input': None}` | 全局快捷键注册表，本刀不碰 `pet/hotkeys.py` / `_apply_global_hotkeys` |
| `test_chat_subsystem.py::test_message_bubble_exposes_avatar_body_and_state` | `bubble.avatar.text() == ""`（期望 `"S"`） | 气泡头像文案，本刀不碰 `pet/chat/widgets.py` |
| `test_chat_subsystem.py::test_modern_message_rows_hide_identity_and_offer_inline_tools` | `assistant.avatar.isHidden()` 为 False | 同上 |
| `test_chat_subsystem.py::test_modern_composer_supports_file_picker_state_and_drop_payload` | `composer.input.maximumHeight() == 180`（期望 ≤72） | 输入框高度策略，同上 |
| `test_requested_regressions.py::test_click_sound_path_is_linked_to_enable_toggle_and_persisted` | 档案里 `click_sound_pack` 仍是 `builtin/default` | 点击音效包的落盘链 |
| `test_requested_regressions.py::test_modern_settings_close_autosaves` | `self_talk_bubble_style` 是 `classic_top`（期望 `breath_bubble`） | 气泡样式下拉的选项与落盘 |
| `test_requested_regressions.py::test_modern_settings_escape_closes_when_save_succeeds` | 同上（档案里也是 `classic_top`） | 同上 |

**断言有效性（消融）**：

- 撤掉 `parent=self` → 9 例红（行不存在）；
- 把「测试余额」行提前创建 → 顺序用例红；
- 说明文案塞回 URL → `test_heading_and_content_share_one_width_contract[720-500]`
  与 `test_compact_window_never_clips_the_form` 红（484 > 454）；
- 把 `_save_balance_endpoint` 换成 no-op 重跑上表后三条既有保存链用例
  （`test_modern_settings_close_autosaves` /
  `test_modern_settings_escape_closes_when_save_succeeds` /
  `test_click_sound_path_is_linked_to_enable_toggle_and_persisted`）→ **仍然红**，
  证明它们与本刀无关。

**全量为什么没跑完（如实登记）**：2026-11-10 16:07 起跑全量
（`pytest -q --ignore=tests/test_fps_monitor.py`，同一启动垫片），25 分钟只走到
**19%**（日志增量 ≈ 504 例 / 25 min ≈ **0.33 例/秒**，据此推算全量 ≥6 小时），
故中止。中止点之前**没有出现本刀相关的新失败**（截到的 `F` 与上表同族）。
本机同时无 `.git`，无法用 `git stash` 做基线对照——三条既有失败已改用上面的
"钩子置空重跑"消融替代。

**本机沙箱限制（如实登记）**：本工作区在 DSH 的 Windows 沙箱里，
`pytest` 的 `tmp_path` 工厂用 `mode=0o700` 建目录后**同进程无法列目录**
（`PermissionError: WinError 5`），另有一个既有测试用例要在临时目录里落
`PresentMon-*.exe` 被沙箱拒绝（`test_fps_monitor.py` 收集期即失败）。因此本地跑测试
用了一个**仓库外的启动垫片**（`%TEMP%\run_pytest_dsh.py`：把 `pathlib.Path.mkdir` 的
`mode` 归一成默认值，Windows 本来就不用该参数），配合 `--basetemp` 指到工作区内的目录。
这些都是**运行环境**的限制，不进产品代码、不进仓库。

## 七、已知限制与后续

1. **`balance_base_url` / `balance_api_key_ref` 是全局键**（不在 `PER_PET_KEYS` 里，
   与 `balance_refresh_minutes` 同族）：任何一只宠的设置页改的都是同一对端点。
   这是既有的键归属，本刀不改。
2. **组随灵动岛模块挂出**：`dynamic_island` 未激活时整组不存在（与余额自动刷新、
   峰谷文案同一条闸）。想让纯核心也能配余额端点，需要重新决定键的归属——不在本刀。
3. **同页「连接测试」行的结果仍然不显示**（既有缺陷）：`self.test_result` 是个没进
   任何布局的 `QLabel`，`_on_test_done` 只改它的文本。本刀没有顺手改它——那是另一条
   交互（聊天连通性），改它要连带验证 `test_connection` 的既有用例族。
4. **Key 输入框不回显已存值**：想确认"到底存没存"，用「测试余额」（它会回退钥匙串里
   那把）。这是刻意的（凭据不回显），不是漏做。
5. **不支持只配 Key 不配地址**：地址留空时整对回退聊天 provider，此时余额专用 Key
   不会被使用（`resolve_balance_endpoint` 的既有语义）。行内文案已写明"留空 = 跟随"。

## 八、风险与回滚

- **影响面**：只影响设置页「AI 与对话 → 余额与服务状态」这一组；余额查询的读方、
  请求形态、缓存键、气泡文案全部未动。
- **配置迁移**：无。没有新键、没有默认值变化；老配置读到的是空串 = 跟随聊天 provider
  = 改动前行为。
- **回滚**：撤掉 `pet/chat/ai_settings_page.py` 与 `pet/settings_default_marks.py` 的
  改动即可；磁盘上多出来的只有用户自己填的 `balance_base_url` 与钥匙串里的
  `balance/deepseek` 条目——**都不会被旧版本读取**，也不会影响聊天 provider。
- **失败模式**：写盘失败走既有设置窗保存闸门；钥匙串不可用只降级为"这个 Key 没存上"
  并显式告知，不会静默留一个用不了的引用。
