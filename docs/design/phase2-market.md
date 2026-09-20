# Phase 2 · 2.1 —— Market Agent 与「并行被证明过」

> 步骤级设计文档。结构性问题仍以 `architecture.md` 为 SSOT；
> 本文只覆盖 2.1 这一步的取舍与判据。

---

## 0. 这一步的交付物不是 market

市场数据本身是 emotion 的复制。**2.1 唯一的新机制是 Stage 1 真并行 ——
而这件事至今零次实测**：`maxConcurrent: 6` 配了，但仓库里只有一个 specialist。

⇒ 设计围绕「怎么**证明**并行」来定，market 是载体。
验收判据见 §5，它比 market 本身重要。

---

## 1. 🔴 先解决边界重叠，再写代码

上游文档 `source-design-v1.md` §4 把**同一批事实同时派给了两个 agent**：

| | 上游给 market | 上游给 emotion |
|---|---|---|
| 重叠 | 涨停/跌停、炸板、连板高度、**市场宽度** | 涨停、跌停、炸板率、最高板、连板、**上涨/下跌家数** |

`architecture.md` §3.1（我们的 SSOT）已经切开了：**宽度归 market**，
涨跌停/炸板/连板归 emotion。但 `emotion-calc` **现在就在算宽度**
（`advance_count` / `decline_count` / `flat_count` 三条 Evidence）。
一个 agent 时无所谓，第二个 agent 上线的当天就是冲突。

### 为什么不能「两个都算」

宽度端点是**不带日期字段的实时快照**（`sources.py` 的 docstring 早写明了）。
两个 agent 并行各调一次，落到同一张 Card 上就是**同一个字段两个不同的值**，
而且两个都带着推断出来的 `as_of`。

L-3 的原话：「错法全是静默的 —— 返回一个看似合理的错值」。

实测坐实了这一点：**周日请求宽度端点，返回的数与上一交易日逐位相同**
（`4277/1173/180`）—— 它把最后一个交易日的快照冻在那里反复发，不报错、不标日期。

### 裁定

**宽度归 market，`emotion-calc` 删掉那三条。**
情绪分公式只用 `limit_up_count` / `max_streak` / `broken_rate`，
删掉零成本（Evidence 13 → 10，`confidence` 分母 15 → 12）。

> 通用原则：**同一个事实只能有一个生产方。**
> 两个消费方各算一遍，不会报错，只会在某天悄悄给出两个数。

---

## 2. 数据源：探测推翻了最省事的方案

原计划是「给宽度那次请求多加几个字段，一次拿到宽度 + 点位 + 成交额」。
实测不行 —— 下面每一行都是真跑过的：

| 源 | 实测结果 |
|---|---|
| `push2delay` 加 `f43,f170,f48` | 宽度对（`1756/519/77`），但**指数字段全是垃圾**：最新 `0.0`、涨跌幅 **`-29971017728.0`** |
| `push2` / `push2his`（行情） | **直接断连 / 返回 0 字节**（`sources.py` 已记录过同一现象） |
| `push2his` kline | 一次返回 483KB 后被截断 —— **`lmt=25` 被忽略，它给了全历史** |
| 新浪 `s_` 摘要 | 可用，但**不带日期** |
| 腾讯 `qt.gtimg.cn` | 可用，且**自带时间戳**（字段 `[30]` = `20260918161402`） |
| 新浪日线 `getKLineData` | 可用，**每行自带 `day`**，`datalen=25` 真的只给 25 行 |

那个 `-29,971,017,728%` 是这类失败的标准形状：
**`rc=0`、字段齐全、类型正确 —— 任何「是不是数字」的检查都会放过它。**

### 选型

```
新浪日线   → 脊梁：权威交易日 + 点位 + 涨跌幅 + 量能基线（同一单位）
腾讯       → 成交额 + 自带时间戳，用来与脊梁交叉校验日期
push2delay → 只用来取宽度（它唯一做对的事）
```

### ⚠️ 单位陷阱（已实测）

新浪日线 `volume` 是**股**（`48571250700`），腾讯 `[6]` 是**手**（`485712507`），
差 100 倍，**乘 100 后完全相等**。

⇒ 量能基线与今日量必须取自**同一个源**；跨源做比值就是静默错 100 倍。
⇒ 反过来，这个恒等式成了一条免费的双源一致性校验（见 §3 守卫 4）。

---

## 3. 实现

### 3.1 `skills/_sources/` —— 共享采集层（新增）

market 需要 emotion 那套 HTTP 客户端：3 次重试 + 退避 + UA/Referer + 备选主机链。
抄一份就是两套重试策略慢慢漂开。

```
skills/_sources/http.py       get_json() + SourceError   （从 sources.py 原样搬）
skills/_sources/eastmoney.py  fetch_pool / fetch_breadth
skills/_sources/sina.py       fetch_index_daily(symbol, n)
skills/_sources/tencent.py    fetch_index_quote(codes)
```

`emotion-calc/scripts/sources.py` 删除，改为从 `_sources` 导入。

⚠️ 这动的是 Phase 1 **已验收**的代码，是有意识的。
**第二个消费方出现的那一刻就是抽取的时刻** —— 晚一步就有两份了。

### 3.2 `skills/market-calc/`

| field | 含义 | 源 | `as_of` |
|---|---|---|---|
| `trade_date` | 交易日 | sina kline 末行 `day` | **权威** |
| `sh_close` / `sh_pct` | 上证点位 / 涨跌幅 | sina kline（pct 由 close/prev_close 算） | trade_date |
| `sz_close` / `sz_pct` | 深证综指 | 同上 | trade_date |
| `turnover_sh` / `_sz` / `_total` | 成交额（亿元） | tencent `[37]` | 腾讯自带时间戳 |
| `volume_ma20` / `volume_ratio` | 量能 = 今日量 / 20 日均量 | sina kline（同单位） | trade_date |
| `advance_count` / `decline_count` / `flat_count` | 宽度 | em:push2delay | trade_date（推断 + warning） |
| `advance_ratio` | 涨跌比 | derived | trade_date |

**五条守卫 —— 每条都对应探测里真见过的失败形状**：

1. 点位 ≤ 0 或涨跌幅 ∉ `[-20, 20]` ⇒ 该条进 `missing`，**不填 0**
2. 腾讯时间戳的日期 ≠ sina 末行 `day` ⇒ `missing`「两源不同日」
3. kline 行数 < 21 ⇒ `volume_ma20` 进 `missing`，**不用 15 天凑一个均值**
4. 腾讯量 × 100 ≠ sina 量 ⇒ `missing`「单位口径变了」
5. 宽度端点无日期 ⇒ `warning`（与 emotion 当时同款）

`--break-source` 演练开关照 emotion 同款，保证 R-3 三条路径（PASS/WARNING/UNKNOWN）可复现。
`status`/`verdict` 口径同 emotion：`core = {trade_date, sh_close, turnover_total}`。

### 3.3 agent `market` 与配置

- `agents add market --workspace <repo>/agents/market`
- 清掉脚手架五件套：`.git` / `BOOTSTRAP.md` / `SOUL.md` / `IDENTITY.md` / `USER.md`（教程 06）
- `AGENTS.md` 含「拿单日数据**说不了**什么」同款一节 + 禁止算术 + **脚本调用自带 `cd`**（教程 10 的坑）
- config 三处：`main.allowAgents:["emotion","market"]`、
  `agentToAgent.allow:["main","emotion","market"]`、补上缺失的 `announceTimeoutMs: 120000`

---

## 4. 明确不做

涨跌幅分布桶（要全市场 5000+ 只快照）/ 历史回补 / Stage 2 /
market 读 emotion 的 verdict（互读就变串行）。

---

## 5. 🔴 并行证明 —— 2.1 的真验收

`tools/verify/latency_report.py` 加 `--parallel-check`，
从 `read_turns()` 取每个 agent 的轮次区间：

- **判据：两个 specialist 的 `[start, end]` 必须相交。**
- 报三个数：`stage1_wall` / `sum(individual)` / `overlap_seconds`

> ⚠️ **判据不能是「总时长变短了」** —— 那可能只是这次网络快。
> **区间相交是结构性证据，快慢不是。**

Supervisor 的 `AGENTS.md` 必须写死：两个 `sessions_spawn` 在**同一条消息**里发出。
分两轮发就是串行，**而日志照样全绿** —— 与 Phase 1 那三个「以慢表现的 bug」同一族。

同时记一个 Stage 3 的数据点：verdict 从 1 条变 2 条，合成轮涨了多少。
Phase 2 的延迟预算要靠这条斜率重推（`architecture.md` §10.1）。

---

## 6. 测试与教程

- `tests/test_market_calc.py` —— 离线 fixture，五条守卫各一条「会红」的探针
- `tests/test_field_single_producer.py` —— **同一个 field 只能有一个生产方**
  （防 `advance_count` 哪天又冒出第二处）
- 教程第 11 章《第二个 Specialist：边界划在哪，以及怎么证明它们真的并行》
