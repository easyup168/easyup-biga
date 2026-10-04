"""新浪涨跌家数 —— `cn.market.breadth` 的 **FALLBACK**，从全市场快照**自算**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：把一份全市场截面数成涨/跌/平家数
- **不覆盖**：取数本身（复用 `sina_eod` 那一份，不另写一遍分页）

🔴 它和主源的根本差别：**主源报数，它数数**
-------------------------------------------
东财 `ulist.np` 直接给 `f104/f105/f106`（涨/跌/平家数）—— 那是**它**数的。
新浪不给家数，只给每只票的涨跌幅 ⇒ 我们自己数。

这个差别必须显式记下来，因为它改变了这份数据的**性质**：

| | 东财（主源） | 本适配器（备用） |
|---|---|---|
| 家数从哪来 | 源报的 | **我们算的** |
| 口径 | 沪 + 深（`secids` 就两个指数） | **沪 + 深 + 京** |
| 平盘的定义 | 源的口径，我们不知道 | `changepercent == 0`，**我们定的** |
| raw | 那次接口响应 | 那次**全市场快照**响应 |

⚠️ 「我们算的」不等于「不可信」，但它要求两件事：
1. raw 必须是**算它所依据的那份快照**（我们收到的字节），不是算完的结果
2. 口径差异要写出来 —— 尤其是**多了北交所**（347 只 / 5568）

🔴 为什么不去掉北交所来"对齐"主源
---------------------------------
那是**硬凑**：为了让两个数看起来可比，而丢掉真实观测到的一部分市场。
本仓库的原则是「凑出来的溯源比没有溯源更糟」，这里同理 ——
差异应当**被声明**，不应当被抹平。

⚠️ 代价：它要拉整个市场（约 1.7 MB / 3 页 / 8 秒），而主源是一次请求。
   这是降级路径可以接受的代价，但别把它当常规路径。
   （实测过没有更便宜的替代：东财的 `ulist.np` 只存在于挂掉的那组 host，
   同花顺与百度都不给现成的家数。）
"""
from __future__ import annotations

from typing import Any, Sequence

from .eod_bar import EodBar
from .eastmoney import BreadthResult
from .http import SourceError

__all__ = ["count_breadth", "fetch_breadth"]

#: 每个市场前缀 → 人读的名字（对齐主源 `per_market` 的形状）。
_MARKET_NAMES = {"sh": "上海", "sz": "深圳", "bj": "北京"}


def count_breadth(rows: Sequence[EodBar], raw_text: str) -> BreadthResult:
    """数出涨/跌/平家数。**纯函数**，能对着已存的快照重放。

    🔴 「平」的定义是 `changepercent == 0` —— 那是**我们定的**，
    不是源给的。停牌票（没有涨跌幅）**不计入任何一档**，
    因为「没开盘」既不是涨也不是跌也不是平。

    ⚠️ 把停牌算进「平」会让停牌多的日子看起来像是市场很平静 ——
    那正是 R-3 要防的「算不出来却给个值」。
    """
    if not rows:
        raise SourceError("sina breadth: 快照是空的，数不出家数")

    buckets: dict[str, dict[str, int]] = {}
    counted = 0
    for row in rows:
        # 🔴 停牌的判据是**有没有价**，不是「涨跌幅是不是空」。
        #    停牌票的涨跌幅可能是 `0.000`，按涨跌幅判会把它算成**平盘** ——
        #    于是停牌多的日子看起来像市场很平静。那正是 R-3 要防的
        #    「算不出来却给个值」。
        if row.close in (None, "", "-"):
            continue
        value = row.change_percent
        if value in (None, "", "-"):
            continue
        try:
            pct = float(value)
        except (TypeError, ValueError) as exc:
            raise SourceError(
                f"sina breadth: {row.symbol} 的涨跌幅不是数字：{value!r}") from exc
        market = str(row.market_hint or "").lower()
        if market not in _MARKET_NAMES:
            raise SourceError(
                f"sina breadth: {row.symbol} 没有可识别的市场标识 {row.market_hint!r} —— "
                "家数必须能按市场拆开，否则与主源的 per_market 对不上")
        bucket = buckets.setdefault(
            market, {"advance": 0, "decline": 0, "flat": 0})
        bucket["advance" if pct > 0 else "decline" if pct < 0 else "flat"] += 1
        counted += 1

    if counted == 0:
        raise SourceError(
            "sina breadth: 整个快照没有一只票有涨跌幅 —— "
            "那不是「全市场停牌」，更可能是字段变了")

    per_market = [
        {"name": _MARKET_NAMES[m], "code": m, **buckets[m]}
        for m in ("sh", "sz", "bj") if m in buckets
    ]
    return BreadthResult(
        advance=sum(b["advance"] for b in buckets.values()),
        decline=sum(b["decline"] for b in buckets.values()),
        flat=sum(b["flat"] for b in buckets.values()),
        per_market=per_market,
        raw={"derived_from": "sina:hs_a snapshot", "counted": counted},
        # 🔴 raw 是**算它所依据的那份快照**，不是算完的结果。
        #    存结果当 raw = 自己证明自己（本仓库 v0.9.2 刚修掉一批）。
        raw_text=raw_text,
    )


def fetch_breadth() -> BreadthResult:
    """取一份全市场快照并数出家数。

    ⚠️ 复用 `sina_eod.fetch_eod_snapshot()` —— **不另写一遍分页**。
    分页的终止条件、页大小的实测依据、限流纪律都只该有一处。
    """
    from .sina_eod import fetch_eod_snapshot

    snapshot = fetch_eod_snapshot()
    return count_breadth(snapshot.rows, snapshot.raw_text)
