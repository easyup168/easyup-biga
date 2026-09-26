"""P3-4 · EOD 管线：一次取数 → 归一化日线 → 不可变 Parquet 分区。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：一次 provider 响应同时喂给「日线归一化」与「可交易性推导」，
  **只发布日线**，可交易性作为覆盖率的分母参与质量判定
- **不覆盖**：把可交易性发布成独立数据集 —— 见下

🔴 为什么 `cn.security.tradability` 今天不发布
---------------------------------------------
`DatasetDefinition` 的契约写死了：**答不出谁读它，这条就还不该进册**
（零消费方 = L-1 死配置，由 `test_没有零消费方的已激活dataset` 钉住）。
可交易性今天唯一的用途就在本模块内 —— 推出「应该有多少只票开盘」，
给日线的 `coverage_ratio` 当分母。**它被算出来、当场用掉，从没被读回过。**

而它原本那条发布路径还有更具体的问题：`tradability.publish()` 把
**派生结果重新序列化**当 raw 存（`json.dumps([r.to_dict() for r in records])`）。
回放去校验那份 raw，校验的是「我刚写的文件还是我刚写的样子」——
它证明不了任何关于出处的事，而 Parquet 分区里存的又是同一批行。

> 裁定 16 那句正好适用：**凑出来的溯源比没有溯源更糟，它会让人以为查得到。**

⇒ 等它真有读取方（筛选 / 复盘要区分「停牌」与「缺数据」）再进册，
  那时它的 raw 应当指向**同一份 provider 响应**，而不是自己的输出。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.persistence import security_universe_at
from easyup_biga.providers.eastmoney_eod import EodFetchResult, fetch_eod_snapshot

from .datasets import eod_daily_bars, tradability
from .publication import PublishResult


@dataclass(frozen=True, slots=True)
class EodBundleResult:
    trade_date: str
    daily_bars: PublishResult
    universe_count: int
    normalized_bar_count: int
    #: 当日推出的可交易性汇总。**没有发布成数据集** —— 见模块头。
    #: 留在结果里是因为它是 `coverage_ratio` 的分母，读日志的人要看得见它。
    tradability_counts: Mapping[str, int]

    @property
    def status(self) -> DatasetStatus:
        return self.daily_bars.status


def run_eod_bundle(
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
    fetcher=fetch_eod_snapshot,
) -> EodBundleResult:
    """Run P3-4/P3-5 against the same Provider response.

    Security Master is point-in-time resolved at the Provider retrieval timestamp.
    An empty universe is a hard configuration/data-lineage error: silently deriving
    tradability from the EOD response itself would defeat P3-3's identity SSOT.
    """
    date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
    fetched: EodFetchResult = fetcher()
    universe = security_universe_at(fetched.retrieved_at, path=db_path)
    if not universe:
        raise RuntimeError(
            "P3 EOD bundle requires a COMPLETE Security Master snapshot visible at "
            f"{fetched.retrieved_at}; run security-master-sync first"
        )

    bars = eod_daily_bars.normalize(fetched.rows, trade_date, fetched.retrieved_at)
    records = tradability.derive(
        universe,
        [bar.to_dict() for bar in bars],
        trade_date,
        fetched.retrieved_at,
        provider_rows=fetched.rows,
    )
    open_or_unknown = sum(
        item.status in {tradability.TradabilityStatus.OPEN, tradability.TradabilityStatus.UNKNOWN}
        for item in records
    )

    bars_result = eod_daily_bars.publish_result(
        fetched,
        trade_date,
        db_path=db_path,
        data_root=data_root,
        new_revision=new_revision,
        expected_open=open_or_unknown,
    )
    return EodBundleResult(
        trade_date=trade_date,
        daily_bars=bars_result,
        universe_count=len(universe),
        normalized_bar_count=len(bars),
        tradability_counts=dict(Counter(item.status.value for item in records)),
    )
