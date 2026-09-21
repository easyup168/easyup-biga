"""数值围栏 —— 把「垃圾值」和「行情」分开的**共享**判据。

为什么单独一个模块
------------------
`market_calc.py` 早就有一条围栏（`PCT_ABS_LIMIT`，注释写着
「这个阈值不是为了抓行情，是为了抓**垃圾值**」）。

但它只长在那一个文件里。外部评审 F5 在 `technical_calc.py` 上构造了
一根坏 tick：60 日窗口内第 91 根的 `low` 改成 `0.01`（**正数**，不触发
任何「≤0」守卫、不会 `ZeroDivisionError`），结果：

    verdict: PASS completed / missing: [] / warnings: []
    dist_to_low60_pct = 31189900.0        # 3118.99 万 %

> 🔴 **单文件打补丁、没抽成共享校验** —— 评审的原话。
> 同一类问题在另一处重演，因为第一次修的时候没人问「还有哪些地方是同样的结构」。

⚠️ 真实世界更危险的不是这种离谱值，是**偏离没那么大**的坏值
（本项目记录过腾讯量纲 ×100、延迟源垃圾值等真实先例）——
那种情况下算出的百分比是一个**看起来完全合理的错误数字**，直接印上卡面。
所以围栏要卡在「物理上不可能」，不是「看着不像」。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：单个数值的量级是否物理上可能
- **不覆盖**：这个数好不好、算不算强势 —— 那是 agent 的判断（约定 S-2）
"""

from __future__ import annotations

import math
from typing import Iterable, Protocol

__all__ = [
    "INDEX_PCT_LIMIT", "BOARD_PCT_LIMIT", "HL_SANITY_FACTOR",
    "is_valid_price", "implausible_bars",
]

#: 指数单日涨跌幅。A 股的物理上限远小于它，指数更不可能接近。
INDEX_PCT_LIMIT = 20.0

#: 板块单日涨跌幅。比指数紧 —— 板块是成分股的加权，不可能比个股涨停还多。
#: ⚠️ 与上面**故意不同**。两个数原本在两个文件里各叫 `PCT_ABS_LIMIT`，
#:    看起来像抄漏了；分开命名是为了让「不同」变成一个明示的决定。
BOARD_PCT_LIMIT = 15.0

#: 一根 K 线的 high/low 相对窗口中位收盘价的允许倍数。
#:
#: 🔴 为什么定得这么松：它要抓的是垃圾值，不是行情。
#:    指数 60 日内腰斩（factor=2）是可能的；差 5 倍不是行情，是坏数据。
#:    定紧了会在极端行情里报红，而**永远报警的检查会被忽略**
#:    —— 这是本项目在成交量交叉校验上已经踩过的教训。
HL_SANITY_FACTOR = 5.0


class _Bar(Protocol):
    high: float
    low: float
    close: float


def is_valid_price(x: float | None) -> bool:
    """是不是一个有效价格：有限、且为正。

    `None` / `nan` / `inf` / `≤0` 全部不是。
    ⚠️ `0.01` **是**有效价格 —— 单看一个数判不出它是坏 tick，
    要靠 `implausible_bars()` 放到窗口里比。
    """
    return x is not None and math.isfinite(x) and x > 0


def implausible_bars(bars: Iterable[_Bar],
                     factor: float = HL_SANITY_FACTOR) -> list[str]:
    """挑出窗口里量级不可能的 K 线，返回人话描述（空列表 = 都正常）。

    判据是**相对窗口中位收盘价**，不是绝对阈值 ——
    绝对阈值要为每个标的各定一份，而中位数自带量纲。

    🔴 用中位数不用均值：一根 `low=0.01` 会把均值拉走，
    而拉走均值之后它自己就显得没那么离谱了。
    """
    bs = list(bars)
    if not bs:
        return []
    closes = sorted(b.close for b in bs if is_valid_price(b.close))
    if not closes:
        return ["整个窗口没有一根有效收盘价"]
    mid = closes[len(closes) // 2]

    out = []
    for i, b in enumerate(bs):
        for name, v in (("high", b.high), ("low", b.low), ("close", b.close)):
            if not is_valid_price(v):
                out.append(f"第 {i + 1} 根的 {name}={v!r} 不是有效价格")
            elif v > mid * factor or v < mid / factor:
                out.append(f"第 {i + 1} 根的 {name}={v}，与窗口中位价 {mid} "
                           f"相差超过 {factor} 倍")
        if is_valid_price(b.high) and is_valid_price(b.low) and b.high < b.low:
            out.append(f"第 {i + 1} 根的 high={b.high} < low={b.low}")
    return out
