"""P3-4/P3-5 EOD bundle: one provider response -> bars + tradability snapshots.

P3-R2 closes three runtime gaps: tradability is now a real published dataset, EOD
refuses non-trading/unverifiable dates, and the provider response may never be relabelled
as an arbitrary historical trade date.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.domain import CN_TZ
from easyup_biga.persistence import is_trading_day, security_universe_at
from easyup_biga.providers.tradetime import MARKET_CLOSE, holiday_fallback
from easyup_biga.providers.eastmoney_eod import EodFetchResult, fetch_eod_snapshot

from .datasets import eod_daily_bars, tradability
from .publication import PublishResult


@dataclass(frozen=True, slots=True)
class EodBundleResult:
    trade_date: str
    daily_bars: PublishResult
    tradability: PublishResult
    universe_count: int
    normalized_bar_count: int
    tradability_counts: Mapping[str, int]

    @property
    def status(self) -> DatasetStatus:
        if self.daily_bars.status is DatasetStatus.COMPLETE and self.tradability.status is DatasetStatus.COMPLETE:
            return DatasetStatus.COMPLETE
        for status in (DatasetStatus.FAILED, DatasetStatus.QUARANTINED, DatasetStatus.PARTIAL):
            if status in {self.daily_bars.status, self.tradability.status}:
                return status
        return self.daily_bars.status


def _verify_eod_date(trade_date: str, fetched: EodFetchResult, *, db_path=None) -> None:
    requested = date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
    known = is_trading_day(trade_date, path=db_path)
    if known is None:
        known = holiday_fallback(requested)
    if known is not True:
        reason = "known closed" if known is False else "calendar coverage missing"
        raise RuntimeError(f"refuse EOD publish for {trade_date}: {reason}")

    retrieved = datetime.fromisoformat(fetched.retrieved_at).astimezone(CN_TZ)
    effective = fetched.effective_trade_date
    if effective is None:
        # Live clist is a snapshot endpoint, not a historical API.  The only safe
        # inference is same-day after close; anything else needs an adapter that
        # explicitly declares the effective trade date.
        if retrieved.date() == requested and retrieved.time() >= MARKET_CLOSE:
            effective = trade_date
    if effective != trade_date:
        raise RuntimeError(
            f"EOD effective date cannot be proven: requested={trade_date} "
            f"effective={effective!r} retrieved_at={fetched.retrieved_at}")


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
    _verify_eod_date(trade_date, fetched, db_path=db_path)
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
    if bars_result.status is DatasetStatus.COMPLETE:
        # 🔴 把**日线那次发布的 raw 血缘**传下去。可交易性是从同一份行情源
        #    响应推出来的，它没有自己的采集动作 ⇒ 引用上游那条，不另造一份。
        #    （曾经它把自己的输出重新序列化当 raw —— 那让哈希变成自己证明自己。）
        tradability_result = tradability.publish(
            records, trade_date,
            upstream_artifact_ids=bars_result.raw_artifact_ids,
            db_path=db_path, data_root=data_root,
            new_revision=new_revision,
        )
    else:
        # The derived dataset must not become COMPLETE when the source EOD bundle was
        # rejected.  Do not materialize a second data plane from a source run that did
        # not pass its own quality gate.
        tradability_result = PublishResult(bars_result.status, "", None, None)
    return EodBundleResult(
        trade_date=trade_date,
        daily_bars=bars_result,
        tradability=tradability_result,
        universe_count=len(universe),
        normalized_bar_count=len(bars),
        tradability_counts=dict(Counter(item.status.value for item in records)),
    )
