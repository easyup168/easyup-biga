"""`cn.security_master` 的 **provider 中立行形状**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：各家 provider 取回来的名单，归一化**之前**长什么样
- **不覆盖**：代码前缀 → 交易所/板块的判断。那是归一化层唯一的一份
  （`data/datasets/security_master.py:_exchange_and_board`），
  不能让每个 provider 各判一遍 —— 那是 L-3

🔴 为什么要有这一层
-------------------
归一化函数原来直接读东财的字段名（`f12` / `f14` / `f13` / `f26`）。
那在只有一个源的时候看不出问题，**加第二个源的那一刻就咬人**：

- 要么让新 provider 把自己的字段**伪装成** `f12`（读的人会以为它是东财）
- 要么给归一化层加一个「这是哪家」的分支（同一个判断两份实现）

两条都错。⇒ provider 自己负责翻译成这里的中立形状，归一化层只认这一种。

⚠️ `market_hint` / `list_date` 是**可空的**：不是每家都给。
   缺了就是缺了，写 `None`，不要拿代码前缀反推一个看起来合理的值 ——
   那正是 R-3 要防的「算不出来却给个值」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["SecurityListing", "SecurityMasterFetchResult"]


@dataclass(frozen=True, slots=True)
class SecurityListing:
    """一只证券在名单里的原始行 —— 只做搬运，不做判断。"""

    #: 6 位代码（provider 给什么写什么，补零留给归一化层）
    symbol: Any
    #: 简称
    name: Any
    #: provider 自己的市场标识。**只用于诊断**（归一化失败时印出来），
    #: 不参与交易所/板块判断 —— 那条规则按代码前缀走，只有一份。
    market_hint: Any = None
    #: provider 给的上市日原始值。没有就 `None`。
    #: ⚠️ 这个字段**可空**，且 `fact_security_master.list_date` 也可空、
    #:    `security_universe_at()` 不读它 ⇒ 缺它不影响 universe，
    #:    只会在质量指标里体现为 `missing_list_date_count`。
    list_date: Any = None


@dataclass(frozen=True, slots=True)
class SecurityMasterFetchResult:
    """一次完整的名单抓取结果。"""

    total: int
    rows: tuple[SecurityListing, ...]
    raw: Mapping[str, Any]
    raw_text: str
    retrieved_at: str
    #: 🔴 这是 **source 前缀**（`em` / `sina`），不是注册表里的 provider id。
    #:    前缀是站点级的、provider id 是适配器级的，两者不是一对一 ——
    #:    `sina` 与 `sina_calendar` 都写 `"sina:"`。
    provider_id: str = "em"
    adapter_version: str = "1"

    @property
    def source(self) -> str:
        return f"{self.provider_id}:security_master/current"
