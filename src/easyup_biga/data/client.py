"""数据层的取数边界 —— **provider 选择在这里，不在 agent / CLI 里**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：按注册表声明的 PRIMARY → FALLBACK 链路取数，并把**每一次尝试**
  （含失败）交还给调用方；今天只有交易日历走它
- **不覆盖**：五条盘中 direct feed（P3-6 的主体，它们要先有 dataset 定义）；
  也不覆盖落库 —— 各 provider 的 `refresh_*` 自己写，本层只决定**叫谁**

🔴 为什么需要这一层
-------------------
`cn.trading_calendar` 在注册表里早就写着 `fallback_providers=("szse",)`，
而 `bin/biga-calendar` **写死了 sina 一个源** —— 主源挂掉就整条失败，
声明中的那条降级路径从来没被走过。

> 一条声明了却没有实现的降级路径，比没声明更糟：
> 读注册表的人会以为这件事已经有人管了。

这正是设计 v1 那条「Provider 选择集中在 Data 层，不进 Agent」要防的形状 ——
把「用哪个源」这个决定留在调用点，就等于每个调用点各自决定一次。

⚠️ **降级不等价，这一点必须说在前面**
-------------------------------------
新浪那个端点一次返回全量，**含交易所已公布的未来排期**（实测到次年年末）；
深交所那个是按月取的，只覆盖**已公布的月份**。所以降级之后：

- `is_trading_day("今天")` 仍然答得出 ✅
- 「下个月某天开不开市」可能答不出 ⇒ 落回 `market_is_open` 的兜底层

调用方拿到 `CalendarRefresh.degraded` 就该知道这件事，而不是看到 `ok` 就放心。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Callable

from easyup_biga.domain import now_cn

from .failover import ProviderExecutionAttempt, execute_with_fallback

CALENDAR_DATASET = "cn.trading_calendar"


@dataclass(frozen=True, slots=True)
class CalendarRefresh:
    """一次日历刷新的结果。**带着是谁干的**，不只是成没成。"""

    provider_id: str
    rows_written: int
    coverage_start: str
    coverage_end: str
    attempts: tuple[ProviderExecutionAttempt, ...] = field(default=())

    @property
    def degraded(self) -> bool:
        """主源没顶住，这次是备用源给的 ⇒ 覆盖范围可能变窄。见模块头。"""
        return any(not a.succeeded for a in self.attempts)


def _months_between(start: datetime.date, end: datetime.date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _szse_range(*, back_days: int, path: Any, fetcher: Any = None) -> tuple[int, str, str]:
    """深交所是**按月**取的 ⇒ 备用路径要自己把窗口铺成月份序列。

    🔴 只铺到**当月**为止，不往未来铺：交易所逐步公布排期，问一个还没公布的
    月份会失败，而那种失败会把整次降级拖垮 —— 用「还没公布」去判定「源挂了」
    是错的。代价就是模块头说的那条：降级之后未来排期可能答不出。
    """
    from easyup_biga.providers.szse import refresh_trading_calendar as _refresh

    today = now_cn().date()
    start = today - datetime.timedelta(days=int(back_days))
    written = 0
    for year, month in _months_between(start, today):
        cal = _refresh(year, month, fetcher=fetcher, path=path)
        written += len(cal.days)
    return written, start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def refresh_trading_calendar(
    *,
    back_days: int = 730,
    path: Any = None,
    fetchers: dict[str, Callable[[], tuple[int, str, str]]] | None = None,
) -> CalendarRefresh:
    """按注册表的链路刷新交易日历。调用方**不指定**用哪个源。

    `fetchers` 只为测试而存在 —— 与 `SnapshotCoordinator` 注入 `fetcher` 同一招
    （L-12：只替换最外层出网边界，不 mock 被测逻辑本身）。
    """
    if fetchers is None:
        from easyup_biga.providers.sina_calendar import (
            refresh_trading_calendar as _sina,
        )

        fetchers = {
            "sina_calendar": lambda: _sina(back_days=back_days, path=path),
            "szse": lambda: _szse_range(back_days=back_days, path=path),
        }

    result = execute_with_fallback(CALENDAR_DATASET, fetchers)
    written, start, end = result.value
    return CalendarRefresh(
        provider_id=result.provider_id,
        rows_written=int(written),
        coverage_start=str(start),
        coverage_end=str(end),
        attempts=result.attempts,
    )
