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

✅ 「6 位代码 → 交易所」已收口
------------------------------
那个判断曾在本仓库有三份手写实现，2026-09-26 收成一份：
`providers/instrument_segments.py`。逼出这次收口的正是通达信盘后包 ——
它按市场分文件，同一份数据里 840 个代码在两个市场都存在。
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
    #: 🔴 provider **声明**的市场（`sh`/`sz`/`bj`、东财 `f13` 等）。
    #:
    #: 给了它就以它为准 —— 号段规则只是「没人告诉你」时的回退。
    #: 实测（2026-09-24 的通达信盘后包）：同一份数据里 **840 个代码在两个
    #: 市场都存在**，最有名的是 `000001`（沪=上证指数 / 深=平安银行）。
    #: 按代码拍平会让上证指数的 3888 点变成平安银行的「股价」，而且不报错。
    market_hint: Any = None
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
