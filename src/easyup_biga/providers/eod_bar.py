"""全市场日线的 **provider 中立行形状**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：各家 provider 取回来的当日全市场截面，归一化**之前**长什么样
- **不覆盖**：代码 → 交易所的判断。那条规则在本仓库已经有两份实现
  （`security_master._exchange_and_board` / `eod_daily_bars._inst`），
  这里**不新增第三份** —— 见下

🔴 为什么要有这一层（与 `security_listing.py` 同一个理由）
--------------------------------------------------------
归一化层原来直接读东财的字段名（`f17` / `f15` / `f16` / `f2` / `f12` …）。
加第二个源的那一刻，要么让新 provider 把字段伪装成 `f12`，
要么在归一化层加「这是哪家」的分支。两条都错。

⚠️ 已知的 L-3，**本次没有一并收口，写在这里免得被当成不存在**
------------------------------------------------------------
「6 位代码 → 哪个交易所」这个判断，本仓库今天有两份手写实现：

1. `data/datasets/security_master.py:_exchange_and_board`（带板块粒度）
2. `data/datasets/eod_daily_bars.py:_inst` + `datasets/tradability.py:_provider_iid`
   （只要交易所后缀，且这两处逐字相同 —— 其实是同一份抄了两遍）

同机另一套长期运行的实例给这件事做过一次普查：同一个判断曾有**二十多处**
独立实现，其中大半是错的，失败形状是**静默取到另一只真标的**
（沪深号段有重叠）。它最后收成一份两位号段表。

⇒ 本仓库迟早要做同样的收口。没有放进这一轮，是因为它会同时动
  universe 与日线两条生产路径，而这一轮的目标是**把日线的源换对**。
  不写下来的话，下一个读到这两份实现的人会以为它们各有理由。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["EodBar", "EodFetchResult"]


@dataclass(frozen=True, slots=True)
class EodBar:
    """一只证券当日的行情行 —— 只做搬运，不做判断。

    ⚠️ 字段一律保留 provider 给的**原始值**（可能是字符串、可能是 `"-"`），
    空值判定与类型转换留给归一化层 —— 那里只有一份实现。
    """

    symbol: Any
    name: Any = None
    open: Any = None
    high: Any = None
    low: Any = None
    close: Any = None
    prev_close: Any = None
    #: 成交量。⚠️ **单位由 provider 决定**，适配器负责换算成「股」
    volume: Any = None
    #: 成交额（元）
    amount: Any = None
    change_amount: Any = None
    change_percent: Any = None


@dataclass(frozen=True, slots=True)
class EodFetchResult:
    rows: tuple[EodBar, ...]
    raw_text: str
    retrieved_at: str
    #: 源自报的总数。没有就填收到的行数 —— 但那会让
    #: 「自报总数 vs 实收行数」这道交叉校验**天然失效**，
    #: 用哪个源就要知道自己还剩几道校验。
    declared_total: int
    #: 🔴 这次数据属于哪个交易日。
    #:
    #: 快照型端点（东财 clist / 新浪行情节点）给不出它 ⇒ `None`，
    #: 于是只能在**当日收盘后**发布（`eod_pipeline._verify_eod_date` 的判据）。
    #: 历史型适配器（如交易所/通达信盘后包）可以显式声明，从而支持补历史日。
    effective_trade_date: str | None = None
    #: source 前缀（`em` / `sina`），不是注册表里的 provider id。
    provider_id: str = "em"
