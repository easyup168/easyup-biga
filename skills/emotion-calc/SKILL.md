---
name: emotion-calc
description: A 股情绪事实计算 —— 涨停/跌停/炸板家数、炸板率、最高板、连板梯队、封板质量、涨跌家数、情绪分。只产出事实与分数，**不做周期归类**（那是 Emotion Agent 的事）。任一项算不出来一律进 missing[]，绝不填 0。
---

# emotion-calc —— A 股情绪事实计算

产出一份合法的 `AgentVerdict`（`skills/_contract/` 唯一实现），每个数字都附带
`source` / `as_of` / `retrieved_at` 的 `Evidence`。

---

## 🔴 边界：这个 skill 不做判断

| 归本 skill（Python） | 归 Emotion Agent（LLM） |
|---|---|
| 涨停 / 跌停 / 炸板家数 | 这算不算「亢奋期」 |
| 炸板率、最高板、连板梯队分布 | 炸板率 24% 在当前位置是否算健康 |
| 全天未炸板占比、涨跌家数 | 要不要提示追高风险 |
| 情绪分（确定性公式） | 这个分数此刻该不该信 |

理由：判断逻辑一旦散进 skill，就会产生**第二套口径** ——
同一个「强不强」在 skill 里算一遍、在 agent 的 prompt 里又算一遍，
两边慢慢漂开，而且漂开时不会报错。

情绪周期的判断口径写在 `agents/emotion/AGENTS.md`，不在这里。

---

## 用法

```bash
# 最近一个交易日（宽松模式：接受数据源给出的最新交易日）
python3 skills/emotion-calc/scripts/emotion_calc.py

# 指定交易日（严格模式：数据源返回的日期对不上即进 missing[]）
python3 skills/emotion-calc/scripts/emotion_calc.py --date 20260918

# 附人类可读摘要（走 stderr，stdout 仍是纯 JSON）
python3 skills/emotion-calc/scripts/emotion_calc.py --render

# 演练数据源中断 —— 验证 UNKNOWN ≠ PASS
python3 skills/emotion-calc/scripts/emotion_calc.py --break-source limit_up
```

**退出码**：`0` 完整 · `2` 有缺失但核心可用 · `3` 核心缺失。

---

## 输出字段

| 字段 | 含义 | 来源 |
|---|---|---|
| `trade_date` | 交易日（🔴 取自数据源的 `qdate`，不是请求日期） | `em:push2ex/qdate` |
| `limit_up_count` | 涨停家数 | 涨停池 |
| `limit_down_count` | 跌停家数 | 跌停池 |
| `broken_board_count` | 炸板家数 | 炸板池 |
| `broken_rate` | 炸板率 = 炸板 / (涨停 + 炸板) | 派生 |
| `max_streak` | 最高板 | 涨停池 `lbc` |
| `streak_2plus_count` | 连板家数（≥2 板） | 涨停池 |
| `streak_ladder` | 连板梯队分布 `{板数: 家数}` | 涨停池 |
| `seal_never_broken_rate` | 全天未炸板占比（封板质量） | 涨停池 `zbc` |
| `advance_count` / `decline_count` / `flat_count` | 涨 / 跌 / 平家数 | 全市场 |
| `emotion_score` | 情绪分 0–100（**参考值，未验证**） | 派生 |

⚠️ `emotion_score` 用的是业内常见的参考公式，**本项目尚未验证它的区分力**。
它不参与任何归类，也不单独上 Decision Card。要等测量阶段做过安慰剂检验才算数。

---

## 🔴 数据源的静默陷阱（实测 2026-09-19）

涨停池接口对**任何**日期参数都返回 `rc=0`，从不报错：

| 请求 date | 返回 qdate | tc | 实际含义 |
|---|---|---|---|
| `20260918`（交易日） | `20260918` | 78 | ✅ 正确 |
| `20260920`（周日） | `20260918` | 78 | ⚠️ 静默返回上一交易日 |
| `20260101`（元旦） | `20260918` | **0** | ⚠️ 你会记下「元旦涨停 0 家」 |

第三行是最坏的一种：**返回了一个看似合理的数字，而它描述的根本不是你问的那天。**

由此定下本 skill 的铁规则：

> `as_of` 永远取自 `qdate`（数据自己声明的日期），**绝不取自「我请求的日期」**。
> 严格模式下两者不符 ⇒ 该字段进 `missing[]`。

同样因为这个字段，本 skill **直接打 HTTP 而不用现成的 Python 封装库** ——
那类库把 `qdate` 整理掉了，且它们不承诺 API 稳定（同类系统记录过两次静默失效数月）。

---

## 缺失即缺失

任何一项算不出来都进 `missing[]` 并显示在 Decision Card 上。
**绝不填 0、绝不跳过、绝不用昨天的值顶替。**

`verdict` 表达的是**数据完整度**，不是市场判断：

| verdict | 条件 |
|---|---|
| `PASS` | 无任何缺失 |
| `WARNING` | 有缺失，但核心三项（涨停家数 / 炸板率 / 最高板）齐备 |
| `UNKNOWN` | 核心三项有缺 |

---

## 依赖

Python 3.12 标准库 + 本仓 `skills/_contract` / `skills/_store`。**无第三方依赖。**
