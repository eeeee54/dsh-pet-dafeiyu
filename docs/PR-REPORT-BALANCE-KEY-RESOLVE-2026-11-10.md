# 余额查不到「未配置 API Key」的根因修复（Key 解析 + 消费统计端点）— 2026-11-10

> **基线**：本工作区是**没有 `.git` 的源码副本**，没有 commit sha / `git diff --numstat`；
> 增删行数按文件总行数变化给出。
> **日期**：2026-11-10　**范围**：5 个文件（实现 2、测试 3）
> **关联**：[`PR-REPORT-BALANCE-ENDPOINT-SETTINGS-2026-11-10.md`](PR-REPORT-BALANCE-ENDPOINT-SETTINGS-2026-11-10.md)（余额专用端点那三行）、
> [`PR-REPORT-BALANCE-TODAY-USAGE-2026-11-10.md`](PR-REPORT-BALANCE-TODAY-USAGE-2026-11-10.md)（今日已用）。
> **用户现场**：桌宠冒泡「余额查询失败：未配置 API Key」，点菜单也只是这一句。

## 一、核心特性

**这不是配置问题，是代码缺陷**——而且自「余额专用端点」上线起就一直在：

1. 凭据默认存系统钥匙串，``chat_settings().active_config.api_key`` **恒为空串**
   （配置里只剩 ``api_key_ref``）；真正的 Key 要通过 ``Config.resolve_api_key()`` 去读。
2. `balance.resolve_balance_endpoint()` 在"跟随聊天 provider"这条分支里**直接返回**
   ``provider.api_key``（空串），没有走 ``resolve_api_key``。
3. 调用方（`show_balance` / `_quiet_balance_refresh`）先自己把 Key 解析好，然后
   ``if resolved_base:`` 用解析器的返回值**整个覆盖**——而 ``resolved_base`` 只要
   有 active provider 就恒为真。

三条一叠加：**每次都把一把空 Key 交给 `fetch_balance`**，于是永远停在
「余额查询失败：未配置 API Key」。与"余额专用端点"配没配无关（用户根本没配，
走的就是回退分支）。

**为什么之前的用例没抓到**：`resolve_balance_endpoint` 是在 2026-11-10 之前的某一刀
落地的，**它自己一条用例都没有**（`tests/test_balance.py` 里 24 例全是
`fetch_balance` / 峰谷 / 格式化），而 `test_island_shell_wiring` 里的余额用例把
``_balance_worker`` 换成了桩，绕过了真实的端点/Key 解析。

**同一条链上另两处一并收口**：

4. **消费统计（`agent_cost_enabled`）的启用判据**只看聊天 provider 的 URL 里有没有
   ``deepseek.com``，查询时也直接用 ``provider.base_url``：于是「聊天走 Ollama、余额
   配 DeepSeek」这种**余额专用端点存在的意义**的配置下，开关开着却永远没数字；
5. `tests/test_island_shell_wiring.py` 的余额用例拿 ``base_url="http://x"`` 当 provider，
   自从 ``balance_supported`` 只认 deepseek 起就必红（过期夹具）。

**红线 / 不变量**：

1. 配了 ``balance_base_url`` 就走专用端点 + 专用 Key（**不**回落到聊天 Key）；
   没配就跟随聊天 provider，且 Key 必须来自 ``resolve_api_key``（钥匙串优先）；
2. 解析函数**不许**在主线程每次状态切换时读钥匙串（`_deepseek_provider` 只比 URL）；
3. 消费者（查看余额 / 岛卡片 / 自动刷新 / 消费统计）共用同一个解析入口，不许各自
   拼 ``provider.base_url``。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| [`pet/island/balance.py`](../pet/island/balance.py) | `443 → 451` 行（**+8 / −0**） | `resolve_balance_endpoint` 的回退分支改成「``provider.api_key`` 为空就 ``config.resolve_api_key(provider)``」；`fetch_balance` 的空 Key 报错文案补成「未配置 API Key（聊天 Key 与「余额 API Key」都没有）」，让用户知道两处都要看。 |
| [`pet/agent_link/__init__.py`](../pet/agent_link/__init__.py) | `6303 → 6318` 行（**+15 / −0**） | `_deepseek_provider` 的判据改看**解析后**的端点（``balance_base_url`` 优先，否则聊天 provider），但**只比 URL、不读钥匙串**（它跑在主线程的状态切换上）；`_query_cost_balance` 改用 `resolve_balance_endpoint` 拿到的那一对去请求（原来直接打 ``provider.base_url``）。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| [`tests/test_balance.py`](../tests/test_balance.py) | `384 → 476` 行（**+92 / −0**，4 例） | 端点/Key 解析的四条契约：①回退时必须给**钥匙串里那把**（这就是本次回归）；②配了专用端点就用专用 Key、不回落到聊天 Key；③自定义 ``balance_api_key_ref`` 生效；④没有 active provider 时给一对空值、不抛。 |
| [`tests/test_agent_link.py`](../tests/test_agent_link.py) | `6500 → 6569` 行（**+69 / −0**，3 例） | 消费统计闸门：①聊天走 Ollama + 余额配 DeepSeek → 可用（原来被关掉）；②两边都不支持 → 保持关闭；③闸门**不读钥匙串**（用假 `SecretStore` 记录调用，断言 0 次）。 |
| [`tests/test_island_shell_wiring.py`](../tests/test_island_shell_wiring.py) | `473 → 477` 行（**+4 / −0**） | 过期夹具修正：provider 地址从 ``http://x`` 改成 ``https://api.deepseek.com``（缓存命中的语义未变，只是不再先被 ``balance_supported`` 拦下）。 |

### 未改

- `balance_supported` / `fetch_balance` 的请求形态、缓存键、气泡文案、峰谷、今日已用
  全部未动；`Config.resolve_api_key` 未动。

## 三、实现要点

**① 单点修，四处受益**。四个余额消费者（菜单/点击、岛卡片、自动刷新、消费统计）都
经过 `resolve_balance_endpoint`，所以只改它一处；调用方原有的「解析结果覆盖」逻辑
反而变成了正确的合并（解析器现在给的是真 Key）。

**② 主线程不碰钥匙串**。`_deepseek_provider` 每次状态切换都会被调（`working`/`idle`），
所以它只做 URL 比较；Key 的读取挪进 `_query_cost_balance` 的 worker 线程。
实测一次钥匙串读取 **0.155 ms**（n=50），但仍然是系统调用，不该放在每次状态切换上。

**③ 保留"专用端点不复用聊天 Key"**：拿 DashScope 的 Key 去问 DeepSeek 只会 401，
那比"未配置 API Key"更难诊断；所以专用端点分支仍然只读 `balance/deepseek`。

## 四、性能分析

**方法（可复现）**：`python .scratch/balance-key-resolve-20261110/probe_resolve.py`
（读用户真实配置 + 真 `/user/balance`，只读）；钥匙串计时用
`SecretStore().get('provider/api-3')` × 50。

| 指标 | 实测 | 归属 |
|---|---|---|
| `SecretStore().get()`（Windows 凭据库） | **0.155 ms**（n=50） | 既有；解析器回退分支最多多读一次 |
| `resolve_balance_endpoint` 新增的工作 | 一次 `resolve_api_key`（≤0.155 ms）+ 一次 `urlparse` | 新增；**只在查询余额时**（worker 线程或用户动作） |
| 主线程新增系统调用 | **0**（`_deepseek_provider` 只比 URL） | — |
| 消费统计的请求地址 | 由聊天 provider 改为**解析后端点** | 行为修正，无额外成本 |

**结论**：① 稳态开销 0（没有余额查询就没有解析）；② 新增路径是"每次查询多一次钥匙串
读取"，≤0.2 ms，相对网络往返可忽略；③ 没有新线程/定时器/文件；④ 主线程不增加系统调用。

## 五、实机运行记录

**① 修复前（读用户真实配置，复现用户截图那一句）**

```
active_provider: api-3
chat_base_url: https://api.deepseek.com/v1
chat_api_key_len: 0            ← 配置里没有明文 Key（凭据在钥匙串）
resolve_api_key_len: 35        ← 钥匙串里其实有
resolve_balance_endpoint -> base: https://api.deepseek.com/v1 | key len: 0   ← 缺陷
```

**② 修复后（同一条只读探针）**

```
active_provider: api-3
chat_base_url: https://api.deepseek.com/v1
chat_api_key_len: 0
resolve_api_key_len: 35
resolved_base_url: https://api.deepseek.com/v1
resolved_key_len: 35
balance_supported: True
balance_base_url: '' | balance_api_key_ref: ''
query_ok: True
bubble_text: '余额 ¥18.98（充值 ¥18.98 / 赠送 ¥0.00）'
usage: '0.00'
```

**③ 用户可见行为**：桌宠冒泡从「余额查询失败：未配置 API Key」变成上面那两行
（余额 + 今日已用）。**需要重启桌宠**（改动在 `pet/island/balance.py` 与
`pet/agent_link/__init__.py`，主进程启动时加载）。

**④ 边界**：仍没有配置任何 Key（聊天 Key 与「余额 API Key」都空）时，报错会明确写
「未配置 API Key（聊天 Key 与「余额 API Key」都没有）」，不再让人以为是余额专用 Key 的问题。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `ruff check pet/island/balance.py pet/agent_link/__init__.py tests/test_balance.py tests/test_agent_link.py` | **All checks passed** |
| 先红 | `pytest -q tests/test_balance.py` | **1 failed / 24 passed**（`..._falls_back_to_the_keychain_key`） |
| 先红（闸门） | `pytest -q tests/test_agent_link.py -k CostGate` | **2 failed / 1 passed** |
| 转绿 | `pytest -q tests/test_balance.py tests/test_agent_link.py tests/test_island_shell_wiring.py tests/test_settings_balance_endpoint.py tests/test_balance_today_usage.py` | **313 passed / 1 skipped**（修正夹具前：1 failed，即那条过期用例） |
| 修复过期夹具后 | `pytest -q tests/test_island_shell_wiring.py` | **11 passed** |

**断言有效性**：撤掉 `resolve_api_key` 回退 → ①那条红；撤掉 `_deepseek_provider` 的
`balance_base_url` 分支 → 闸门用例①红；把 `_deepseek_provider` 改成读钥匙串 →
"不读钥匙串"那条红（假 `SecretStore` 记录到调用）。

## 七、已知限制与后续

1. **仍然没有"不配 Key"的引导**：钥匙串为空时只在查询时报错，没有"去设置"的深链
   （右键菜单 →「DeepSeek 余额」与设置页是两处，用户得自己找）。
2. **消费统计的"有没有 Key"仍然静默**：`_query_cost_balance` 拿不到 Key 时直接返回
   （不冒泡、不提示），这是既有口径，本刀没改。
3. **专用端点 + 专用 Key 但 Key 没存过**：会报「未配置 API Key（…）」——设置页的
   「测试余额」按钮能提前发现，但没有保存时的强校验。

## 八、风险与回滚

- **影响面**：只改"取哪一对端点/Key"与消费统计的请求地址；请求形态、缓存、文案展示
  未动。
- **回滚**：撤掉 `pet/island/balance.py` 的 8 行与 `pet/agent_link/__init__.py` 的 15 行；
  测试同步撤掉即可。
- **失败模式**：解析器任何一步抛异常都返回 `("", "")`；调用方保留自己已解析的那一对
  （`if resolved_base:` 的旧逻辑仍在），不会因为解析失败而把"能查"变成"查不了"。
