---
name: market-calc
description: A 股市场状态事实计算 —— 指数点位/涨跌幅、两市成交额、量能比、涨跌家数。输出合法 AgentVerdict。不做判断性归类。
---

# market-calc

回答「市场现在是什么状态」的**事实部分**。不选股，不下结论。

## 用法

```bash
cd ~/.openclaw-biga/workspace            # 🔴 必须先 cd —— 脚本用仓库根定位 skills/
python3 skills/market-calc/scripts/market_calc.py --render
```

| 参数 | 作用 |
|---|---|
| （无） | 宽松模式：取数据源给出的最近一个交易日 |
| `--date YYYYMMDD` | 严格模式：数据源给的日期对不上即进 `missing[]` |
| `--break-source NAME` | 演练：人为中断 `sina_sh` / `sina_sz` / `tencent` / `breadth` |
| `--no-store` | 不写 `raw_market_snapshot` |
| `--render` | stderr 附人类可读摘要（stdout 仍是纯 JSON） |

退出码：`0` 数据完整 · `2` 有缺失但核心可用 · `3` 核心缺失。

## 产出字段（15 个）

| field | 含义 | 源 |
|---|---|---|
| `trade_date` | 交易日 | 新浪日线（**权威**） |
| `sh_close` / `sh_pct` | 上证点位 / 涨跌幅(%) | 新浪日线 |
| `sz_close` / `sz_pct` | 深证综指点位 / 涨跌幅(%) | 新浪日线 |
| `turnover_sh` / `turnover_sz` / `turnover_total` | 成交额（亿元） | 腾讯行情 |
| `volume_total` / `volume_ma20` / `volume_ratio` | 量能（亿股，均量不含今日） | 新浪日线 |
| `advance_count` / `decline_count` / `flat_count` / `advance_ratio` | 涨跌家数与上涨占比 | 东财 ulist |

## 🔴 这个 skill 不回答的问题

它给事实，**不给归类**。下面这些由 Market Agent 依据自己的 `AGENTS.md` 来说：

- 这算不算「放量上涨」
- 2.07 万亿在当前位置是高是低
- 缩量反弹要不要提示风险
- 宽度与指数背离说明什么

判断逻辑一旦散进 skill，就会产生第二套口径 —— 同一个「强不强」在 skill 里
算一遍、在 agent 的 prompt 里又算一遍，两边慢慢漂开，而且漂开时不会报错。

## 🔴 涨跌家数归 market，不归 emotion（裁定 15）

它的端点是**不带日期的实时快照**。两个 agent 并行各调一次，
同一张 Card 上就会出现同一个字段两个值，而两个都带着推断出来的 `as_of`。

## 五条守卫

每条都对应一次真见过的失败形状，缺任何一项都进 `missing[]`，**绝不填 0**：

1. 点位 ≤ 0 或 |涨跌幅| > 20% —— 东财延迟源实测返回过 `最新=0.0`、`涨跌幅=-29971017728.0`
2. 腾讯时间戳的日期 ≠ 新浪日线末行 —— 两个独立源说的不是同一天，不挑一个用
3. 日线不足 21 根 —— 量能进 `missing`，不拿更短的窗口凑
4. 腾讯量×100 与新浪量偏离 > 1% —— 新浪按**股**、腾讯按**手**，跨源做比值是静默错 100 倍
5. 涨跌家数端点无日期 —— 发 `warning` 说明 `as_of` 是推断的

设计取舍见 [`docs/design/phase2.md`](../../docs/design/phase2.md) §3.2，施工过程见[教程第 11 章](../../docs/tutorial/11-second-specialist.md)。
