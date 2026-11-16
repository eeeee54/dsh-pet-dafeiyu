# 今日已用（余额差值记账 + 泡泡第二行）— 2026-11-10

> **基线**：本工作区是**没有 `.git` 的源码副本**（`git rev-parse` 报
> `not a git repository`），因此没有 commit sha 与 `git diff --numstat`；增删行数按
> 文件总行数变化给出。
> **日期**：2026-11-10　**范围**：4 个文件（实现 2、测试 1、文档 1）
> **上游参考**：[`RYANFFY/deepseek-dafeiyu-pet`](https://github.com/RYANFFY/deepseek-dafeiyu-pet)
> 的 `usage.json` 记账口径（MIT，见其 README「余额挂件（新增）」一节）；
> 用量接口的原始参考仍是 [`MeteorNOX/DeepSeek-Balance-Whale-Widget`](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget)。
> **关联**：[`PR-REPORT-BALANCE-ENDPOINT-SETTINGS-2026-11-10.md`](PR-REPORT-BALANCE-ENDPOINT-SETTINGS-2026-11-10.md)（同一天那三行设置）。

## 一、核心特性

**问题**：余额只有"还剩多少"，没有"今天用了多少"。用户真正想知道的是后者——
"这一小时我花了多少钱"。DeepSeek 开放平台**没有公开的今日用量接口**（用量页要登录态），
所以只能记账。

**做法（借上游那套）**：不读平台用量页、不要额外令牌，**把相邻两次余额读数之差
记成当天用量**，写 `usage.json`；余额气泡多一行「今日已用 ¥x.xx」。

记账有四条边界，缺一条数字就是错的：

| # | 边界 | 判据 | 不处理会怎样 |
|---|---|---|---|
| 1 | **跨天归零** | 按**北京时间**切日（与峰谷提示同一条口径） | 昨天剩的差额算进今天 |
| 2 | **换 Key / 换账号只重置基准** | 账本按 `source_id`（provider 键 `id\|base_url\|key_digest`）记账 | 两个账号的余额差被当成"今天花了" |
| 3 | **一次掉太多算平台侧变动** | **60 秒内掉 ≥ ¥20**：赠送额度整块到期、退款、换套餐都会这样 | 送的钱到期会被记成"今天花了 20 块" |
| 4 | **充值只更新基准** | 余额变多：不动已用量 | 充值后被当成"花了负数"或把新余额记成消耗 |

**用户看到的变化**：

```
余额 ¥21.41（充值 ¥21.41 / 赠送 ¥0.00）
今日已用 ¥0.28
```

（下面是原有的峰谷副标题，不动；平台侧变动那次多一行「（余额少了 ¥20.00，看着不像
用掉的，这次没算进今日已用）」。）

**红线 / 不变量**：

1. **不新增任何配置键**——账本是运行时数据（`usage.json`），不是设置；
2. **余额查询本身绝不受影响**：账本读写/损坏/并发失败一律降级为"这次没有今日已用"，
   不允许把一次成功的余额查询变成失败；
3. 缓存命中（30 秒内存 / 文件缓存）与直接查询**看到同一份文案**——`usage` 跟着
   payload 一起进缓存；
4. 灵动岛卡片、消费统计（`agent_cost_enabled`）的行为一个字不动。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| [`pet/island/usage.py`](../pet/island/usage.py) | **新文件 140 行** | 记账本体：`record(path, total=…, source_id=…, now=…)` 返回 `{"used","note","reset"}`；`append_usage_line(text, used, note)` 负责"泡泡第二行"的纯拼装；`format_used_line` / `beijing_day` / `load_state` / `save_state`。模块级 `threading.Lock` 串起读-改-写（余额查询有显式/静默两个线程可以同时回来），落盘走 tmp+replace 原子写。 |
| [`pet/app.py`](../pet/app.py) | `5517 → 5549` 行（**+32 / −0**） | ① `_balance_worker`：查询成功后调 `usage.record(...)`，把 `usage` / `usage_note` 塞进 payload；**记账整段包在 try/except 里**（只用 `logging.debug` 记，绝不外抛）；② `_write_balance_file_cache` / `_read_balance_file_cache`：把 `usage` / `usage_note` 一起存取，缓存命中路径不缺这一行；③ `_show_balance_payload`：正文按需拼上第二行（纯函数在 usage 模块里）。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| [`tests/test_balance_today_usage.py`](../tests/test_balance_today_usage.py) | **新文件 264 行 / 16 例** | 四条边界各一例（首次读数立基准、小额扣减、充值只移基准、突发大额不计、慢速大额仍计）、跨天归零、换账号只重置基准、账本损坏降级、金额非法不建文件、**三线程并发不丢用量**、`append_usage_line` 的四种形态、真 `_show_balance_payload` 的第二行、真 `_balance_worker` 的记账 + 文件缓存回读、**记账抛异常时余额查询照常成功**。 |

### 文档

| 文件 | 增删 | 改动意图 |
|---|---|---|
| [`README.md`](../README.md) | `768 → 770` 行（**+9 / −7**） | 「DeepSeek 余额（Chat 版）」一节补两段：余额专用端点（地址/Key/测试）、今日已用的四条边界；并把上游参考补上 `RYANFFY/deepseek-dafeiyu-pet`。 |

### 明确没搬的东西（避免评审以为漏了）

上游还有：**校准今日已用…**（按平台用量页手填）、**数字滚动动画**、**余额常显**、
**多余额来源**、**每轮 token 统计**。这次只做「账本 + 第二行」，理由是：

- 校准需要新的设置行/对话框 + 一份 `calibrateLog`，属独立一刀；
- 数字滚动要动气泡渲染（我们气泡是自绘 + 页式分页），风险与收益不成比例；
- 余额常显可由灵动岛「信息栏 = 余额」覆盖；
- 多余额来源要改 `resolve_balance_endpoint` 的"一对"语义；
- 每轮 token 统计我们已有 `agent_cost_enabled`（余额差值口径）。

## 三、实现要点

**① 记账只有一个接缝**。所有余额查询（菜单点击、点击桌宠、自动刷新、岛卡片展开）
最终都走 `AppShell._balance_worker`，所以记账放在那里，四个入口自动都有；
`_show_balance_payload` 是三条展示路径（直查 / 内存缓存 / 文件缓存）的唯一出口，
第二行也只在那里拼。

**② 账本绑定"账号身份"而不是只看金额**。`source_id` 用的是 AppShell 已有的
provider 键 `id|base_url|key_digest`，与余额缓存同一套身份——换 Key 立刻体现为
"基准重置"，而不是把两个账号的余额差算成用量。

**③ 阈值含等号 + 先按分取整**。`26.30 - 6.30` 在浮点里是 `19.999999999999996`，
直接用 `>` 会让"正好掉 ¥20"一会儿算用量、一会儿不算（写测试时真踩到了）。
现在先 `round(delta, 2)` 再判 `>= SUDDEN_DROP_AMOUNT`。

**④ 降级优先级明确**：`save_state` 吞 `OSError` 只记 debug；`record` 对空/非法金额
直接返回空 `used` 且**不建文件**；worker 里再包一层 try/except。三层都失败时，
用户看到的还是一条正常的余额气泡，只是没有第二行。

**⑤ 原子写 + 互斥**：`usage.json` 与 `balance_cache.json` 同款 tmp+replace；
读-改-写整段在一把模块级 `Lock` 里，两个查询线程同时回来不会互相覆盖（用例用
三线程真跑验证：最终 `used > 0` 且 `total` 落在某个真实读数上）。

## 四、性能分析

**方法（可复现）**：`python .scratch/balance-usage-20261110/perf_usage.py`
（Windows 11 / Python 3.12.14 / 工作区内目录，n=200 次 `record`、2000 次
`append_usage_line`，三遍取范围）。

| 指标 | 实测 | 归属 |
|---|---|---|
| 一次 `record`（读 + 原子写 + replace，账本 99 字节） | **1.996 ~ 2.096 ms**（n=200 × 3 遍；冷目录第一次实测到过 8.075 ms） | 新增；只在**余额查询成功后**跑一次 |
| `append_usage_line`（泡泡第二行拼装） | **0.64 ~ 0.67 µs**（n=2000 × 3 遍） | 新增；每次冒泡一次 |
| 账本文件大小 | **99 字节** | — |
| 新增定时器 / 线程 / 网络 | **0 / 0 / 0** | 记账跑在既有的 `pet-balance` worker 线程里 |

**结论**：

1. **稳态开销**：0。没有余额查询就没有任何记账动作；没有新定时器。
2. **新增路径成本与触发频率**：每次"真的发出去了"的余额查询 +2 ms（用户点一次 /
   自动刷新一次 / 岛卡片展开一次；30 秒缓存以内的重复查询不触发），**在后台线程**，
   不占 GUI 线程；相对同一次查询的网络往返（实测 100~300 ms）是 ~1%。
3. **系统调用 / 网络 / 磁盘**：每次查询多 1 次读 + 1 次写 + 1 次 rename，共 ~300 字节；
   无网络、无线程。
4. **内存**：一个 99 字节 dict，进 payload 后随缓存对象一起被替换；无常驻增长。

## 五、实机运行记录

**① 真机端到端（真接口 + 真账本，用本机 Key，只读；Key 从环境变量取，不进仓库）**

命令：`set DEEPSEEK_API_KEY=… & python .scratch/balance-usage-20261110/e2e_usage.py`
（`AppShell.__new__` 桩 + 真 `AppShell._balance_worker` + 真
`https://api.deepseek.com/user/balance`）：

```
query1: ok=True text='余额 ¥20.66（充值 ¥20.66 / 赠送 ¥0.00）' usage='0.00' note=None
query2: ok=True text='余额 ¥20.66（充值 ¥20.66 / 赠送 ¥0.00）' usage='0.00' note=None
usage.json: {"version": 1, "day": "2026-11-10", "used": 0.0, "total": 20.66,
             "source": "|https://api.deepseek.com|e2e", "at": 1794299206.15}
cache keys: ['info', 'provider', 'text', 'ts', 'usage', 'usage_note'] cache.usage: 0.00
```

证明：真接口能查、账本真落盘、**文件缓存里也带着 usage**（缓存命中路径不会少一行）。
两次读数相同 ⇒ 0.00（没有差额就不编数字）。

**② 用法边界（单测里真跑，命令见§六）**

- 账本损坏（`{not-json`）→ 下一次读数按 0.00 重来，不抛异常；
- 金额非法 / 空 → **不建文件**（`assert not path.exists()`）；
- 三线程同时写 → 用量不回滚、基准落在某个真实读数上；
- 记账函数被替换成抛 `OSError` → 余额气泡照常出，只是没有第二行。

**③ 视觉**

气泡第二行的实际渲染走的是既有 `PetSpeechBubble`（正文 `\n` 换行，第二行在小字号
副标题之上）。本机 Qt 运行时**没有自带字体**（`QFontDatabase: Cannot find font
directory …`），离屏截图只能证明"两行都在"，不能证明字号与换行观感——真实观感需要
在有字体的机器上看一眼（`QT_QPA_FONTDIR=C:\Windows\Fonts` 可以临时补上，
本报告§五①的输出就是在该设置下取的）。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/island/usage.py pet/app.py tests/test_balance_today_usage.py` | **All checks passed** |
| 新用例 | `pytest -q tests/test_balance_today_usage.py` | **16 passed** |
| 相关族 | `pytest -q tests/test_island_shell_wiring.py tests/test_feature_modules_gating.py tests/test_balance_today_usage.py` | **114 passed / 2 failed**（两条既有失败，见下） |
| 懒加载 + schema | `pytest -q tests/test_app_lazy_imports.py tests/test_config_schema.py tests/test_settings_balance_endpoint.py tests/test_balance_today_usage.py` | 92 passed / **4 failed（既有）** |
| 文档门 | `pytest -q tests/test_pr_report_discipline.py` | **231 passed**（本报告登记后复跑） |

**既有失败（与本刀无关，逐条给了归属）**：

| 用例 | 失败点 | 归属 |
|---|---|---|
| `test_config_schema.py` ×4（`test_defaults_snapshot_matches_current` / `test_reload_whitelist_snapshot_matches_current` / `test_every_defaults_key_is_whitelisted_or_special_cased` / `test_special_cased_keys_are_the_only_difference`） | 快照与当前键集差一批 `chat_memory_*` 键 | 对话记忆那一批键的 schema 快照没跟上（本刀**没有新增任何配置键**） |
| `test_feature_modules_gating.py::test_vision_hotkeys_not_registered_when_inactive` | 多一条 `voice_input: None` | 全局快捷键注册表 |
| `test_island_shell_wiring.py::test_quiet_balance_refresh_paths` | 缓存命中那条断言拿到「当前服务商无余额接口」 | **测试已过期**：它给的 provider 是 `base_url="http://x"`，`balance_supported()` 自「余额专用端点」那刀起就只认含 `deepseek` 的地址，所以第 2 段（缓存命中）必然先被这道闸拦住。本刀没有改 `_quiet_balance_refresh`，也没有改 `balance_supported`。 |

**断言有效性（消融）**：把 `record` 换回"直接 `used += delta`"（去掉阈值与换账号判断）
→ 突发大额与换账号两例红；把 `>=` 改回 `>` → "正好掉 ¥20"那例红（实测浮点差
`19.999999999999996`）；把 worker 里的 try/except 去掉 → 记账抛异常那例红。

## 七、已知限制与后续

1. **没有"校准"入口**：上游可以按平台用量页手填校准，我们暂时没有；差值口径在
   "同一分钟里既充值又消费"等极端情况下会和平台对不上。
2. **只在气泡显示**：灵动岛的余额槽是定宽单行（样本 `余额 ¥88888.88`），加第二行会
   破坏宽度契约，所以岛卡片不显示今日已用。
3. **多账号合并**：换账号只重置基准、不清零当天的数（当天已经花掉的钱还是那些），
   如果一天里来回切两个账号，`今日已用` 是两个账号当天的合计。
4. **赠送额度到期**只能靠"60 秒内掉 ≥ ¥20"这条启发式识别；小额到期（比如剩 ¥3 到期）
   会混进用量。
5. 上游的**数字滚动 / 余额常显 / 多余额来源 / 每轮 token 统计**都没有搬（理由见§二末）。

## 八、风险与回滚

- **影响面**：只影响余额气泡正文（多一行）与配置目录里一个新文件 `usage.json`；
  余额查询、缓存键、岛卡片、消费统计的行为未变。
- **配置迁移**：无（没有新键、没有默认值变化）。老用户第一次查询时账本按"首次读数"
   建基准，`今日已用` 从 0.00 开始。
- **回滚**：撤掉 `pet/island/usage.py` 与 `pet/app.py` 的三处接线即可；`usage.json`
  留在配置目录里也不会被旧版本读取（无害，可手删）。
- **失败模式**：账本损坏/不可写/并发冲突 → 降级为"没有第二行"，余额照常。
