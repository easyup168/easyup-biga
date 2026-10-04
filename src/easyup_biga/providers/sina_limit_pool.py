"""新浪涨停/跌停/炸板家数 —— `cn.market.limit_pool` 的 **FALLBACK**，从全市场快照**自算**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：把一份全市场截面数成涨停 / 跌停 / 炸板家数，并标出每只票**炸没炸过板**
- **不覆盖**：取数本身（复用 `sina_eod` 那一份，不另写一遍分页）、
  **连板次数**（单日截面里没有这个信息，见下）

🔴 为什么非要一个非东财的备胎
-----------------------------
`cn.market.limit_pool` 是最后一个单点源。2026-09-26 东财
`push2` 系四台主机同时 502，emotion 因此**整组 UNKNOWN**，
Decision Card 少了一整个维度。

东财自家还有一条路（付费 AI 接口 `mkapi2.dfcfs.com`，实测当天可用），
但它**和主源是同一家** —— 厂商级故障会一起挂。
⇒ 真正的备胎必须换一家。本适配器是新浪。

它和主源的根本差别：**主源报数，它数数**
-----------------------------------------
东财 `push2ex` 三个股池直接给 `tc`（家数）与每只票的 `lbc` / `zbc` ——
那是**它**判的。新浪不给股池，只给每只票的价格 ⇒ 我们自己判。

| | 东财（主源） | 本适配器（备用） |
|---|---|---|
| 涨停怎么判 | 源判的 | **我们按涨跌幅阈值判** |
| 连板次数 `lbc` | ✅ | ❌ **单日截面里没有** |
| 炸板次数 `zbc` | ✅ | ❌ **单日截面里也没有**，见下 |
| 口径 | 沪 + 深 + 京 | 沪 + 深 + 京 |
| raw | 那次股池响应 | 那次**全市场快照**响应 |

⇒ `row_fields` 是**空集**。两个字段都不声明，否则 emotion 会把
每只票当成首板、当成全天未炸板 —— 两条都是不报错的假事实。

涨跌停阈值是**按板块**的，而且带容差
------------------------------------
借鉴同机已有实例实盘校准过的判据：

    科创(68) / 创业(30)  → ±20%
    北交所(BSE)          → ±30%
    其余（含 ST）        → ±10%

⚠️ 两处细节是踩出来的，不是推出来的：

1. **阈值用 9.9 / 19.9 / 29.9，不用 10 / 20 / 30。**
   涨停价是按昨收**四舍五入到分**算的，所以实际涨幅经常不是整数
   （例如昨收 8.13 的票，涨停价 8.94，涨幅 9.96%）。
   卡在 10.0 上会漏掉一大批。

2. **ST 现在也是 10%。** 2026-07-06 新规：主板 ST 由 5% 提至 10%。
   ⇒ 不需要按名字判 ST 了。**这条将来若再改，是这个文件要跟的。**

🔴 为什么不给 `zbc`（炸板次数）—— 我第一版真的给了，而且是错的
---------------------------------------------------------------
第一版的判据是「收在涨停价、但 `low < 涨停价` ⇒ 炸过板」。

**那量的不是炸板，是「不是一字板」。** 除了开盘即封死的票，
几乎每只涨停股的最低价都低于涨停价 —— 尾盘封板的票开盘当然更低。

实测当场戳穿（20260924 同一天）：

    东财股池      未炸板 22 / 52 = 42%
    我那一版      未炸板  5 / 54 =  9%   ← 差了四倍多

「曾封板、后打开」需要**分时/逐笔**才判得出来，单日 OHLC 里没有这个信息。

> 这一条差点作为「全天未炸板占比」印上卡面。它不会报错 ——
> 只会给出一个看起来精确的、错四倍的百分比。

⇒ 不给。`row_fields` 留空集，让 emotion 明确说「本次供数方不提供」。

炸板**家数**仍然给，但口径不同
------------------------------
本适配器按「最高价触及涨停价、收盘没到」判 ——
那是「摸到过涨停」，而东财炸板池是「**曾封板**后打开」。
摸到但没形成封单的票会算进我们这份、不算进东财那份。

实测 14（本适配器）vs 10（东财），差异方向与这个解释一致。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from easyup_biga.domain import now_cn

from .eod_bar import EodBar
from .eastmoney import PoolResult
from .http import SourceError
from .instrument_segments import a_share_segment

__all__ = ["LIMIT_PCT", "count_limit_pools", "fetch_all_pools", "fetch_pool"]

#: 板块 → 涨跌幅上限。见模块头「阈值」一节。
LIMIT_PCT: dict[str, float] = {
    "STAR": 0.20,       # 科创板
    "CHINEXT": 0.20,    # 创业板
    "BSE": 0.30,        # 北交所
    "SSE_MAIN": 0.10,
    "SZSE_MAIN": 0.10,
}

#: 判「到了涨停」的容差：实际涨幅 ≥ 上限 − 这个数。
#: 0.1 个百分点足够吸收「涨停价四舍五入到分」带来的偏差。
_PCT_TOLERANCE = 0.1

#: 判「盘中破过板」的价格容差。浮点与分位四舍五入用。
_PRICE_TOLERANCE = 0.999


def _f(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limit_pct(bar: EodBar) -> float | None:
    """这只票今天的涨跌幅上限。认不出板块就返回 `None` —— **不猜 10%**。

    🔴 默认成 10% 的代价：科创/创业板的 20% 涨停会被当成"涨了 20%"漏掉，
    而北交所的 30% 更离谱。而且**不报错** —— 只是家数偏少。
    """
    code = (bar.symbol or "")[-6:]
    seg = a_share_segment(code, market=bar.market_hint)
    return LIMIT_PCT.get(seg.board) if seg else None


def count_limit_pools(rows: Sequence[EodBar], raw_text: str,
                      *, trade_date: str) -> dict[str, PoolResult]:
    """从一份全市场截面数出三个股池。**纯函数**，能对着已存的快照重放。

    Returns:
        `{"limit_up": …, "limit_down": …, "broken_board": …}`，
        三个 `PoolResult` 共用同一份 `raw_text`（它们出自同一次观测）。

    Raises:
        SourceError: 一只票都认不出板块 —— 那是快照形状变了，不是「今天没涨停」。
    """
    now = now_cn().isoformat()
    up_rows: list[dict[str, Any]] = []
    down = 0
    broken: list[dict[str, Any]] = []
    classified = 0

    for bar in rows:
        pct_limit = _limit_pct(bar)
        if pct_limit is None:
            continue
        classified += 1
        change = _f(bar.change_percent)
        close = _f(bar.close)
        if change is None or close is None or close <= 0:
            # 停牌 / 没成交 ⇒ 三个池都不计。**不是 0，是没有**。
            continue
        threshold = pct_limit * 100 - _PCT_TOLERANCE
        prev = _f(bar.prev_close)
        limit_price = prev * (1 + pct_limit) if prev and prev > 0 else None

        if change >= threshold:
            # 🔴 **不带 `zbc`。** 单日 OHLC 判不出「曾封板后打开」——
            #    见模块头那一节（第一版给了，实测差四倍）。
            up_rows.append({"code": bar.symbol, "name": bar.name})
        elif change <= -threshold:
            down += 1
        elif limit_price is not None:
            high = _f(bar.high)
            # 盘中摸到过涨停价、收盘没封住 ⇒ 炸板。
            if high and high >= limit_price * _PRICE_TOLERANCE:
                broken.append({"code": bar.symbol, "name": bar.name})

    if not classified:
        raise SourceError(
            f"新浪自算股池：{len(rows)} 行里一只票都认不出板块 —— "
            "快照的代码或市场字段可能变了，这不是「今天没涨停」")

    def _pool(name: str, items: list[dict[str, Any]], total: int,
              fields: set[str]) -> PoolResult:
        """`total` 单独传：跌停池我们只留了计数，没留明细行。

        ⚠️ 不写成 `len(items)` —— 那会让「没留明细」悄悄变成 `total=0`，
           而 `0` 是一个**合法的家数**。
        """
        return PoolResult(
            pool=name, requested_date=trade_date, qdate=trade_date,
            total=total, rows=items,
            raw={"derived_from": "sina_eod snapshot", "retrieved_at": now},
            raw_text=raw_text,
            # 🔴 只声明**真的有**的字段。`lbc` 单日截面给不出来。
            row_fields=frozenset(fields))

    return {
        "limit_up": _pool("limit_up", up_rows, len(up_rows), set()),
        "limit_down": _pool("limit_down", [], down, set()),
        "broken_board": _pool("broken_board", broken, len(broken), set()),
    }


def fetch_pool(pool: str, date: str, *, page_size: int = 500) -> PoolResult:
    """取一份全市场快照并数出指定的池。签名与东财那份保持一致。

    ⚠️ 复用 `sina_eod.fetch_eod_snapshot()` —— **不另写一遍分页**。
    分页的终止条件、页大小的实测依据、限流纪律都只该有一处。

    ⚠️ 代价：它要拉整个市场（约 1.7 MB / 3 页 / 8 秒），而主源是一次请求。
       这是降级路径可以接受的代价，但别把它当常规路径。
    """
    from .sina_eod import fetch_eod_snapshot

    if pool not in ("limit_up", "limit_down", "broken_board"):
        raise ValueError(f"未知的池名 {pool!r}")
    fetched = fetch_eod_snapshot()
    pools = count_limit_pools(
        fetched.rows, fetched.raw_text or json.dumps({}), trade_date=date)
    return pools[pool]


def fetch_all_pools(trade_date: str) -> dict[str, PoolResult]:
    """一次全市场快照 → 三个池。

    🔴 **不要逐个调 `fetch_pool`**：那会把整个市场拉三遍
    （约 5 MB / 24 秒），而三个池本来就出自**同一次观测** ——
    分三次抓还会让它们在时间上不一致（盘中尤其明显）。
    """
    from .sina_eod import fetch_eod_snapshot

    fetched = fetch_eod_snapshot()
    return count_limit_pools(
        fetched.rows, fetched.raw_text or json.dumps({}), trade_date=trade_date)
