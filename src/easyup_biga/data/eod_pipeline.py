"""P3-4/P3-5 integrated EOD pipeline: one fetch, immutable bars + tradability."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.persistence import security_universe_at
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

    @property
    def status(self) -> DatasetStatus:
        statuses = {self.daily_bars.status, self.tradability.status}
        if DatasetStatus.FAILED in statuses:
            return DatasetStatus.FAILED
        if DatasetStatus.QUARANTINED in statuses:
            return DatasetStatus.QUARANTINED
        if DatasetStatus.PARTIAL in statuses:
            return DatasetStatus.PARTIAL
        return DatasetStatus.COMPLETE


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
    trad_result = tradability.publish(
        records,
        trade_date,
        db_path=db_path,
        data_root=data_root,
        new_revision=new_revision,
    )
    return EodBundleResult(
        trade_date=trade_date,
        daily_bars=bars_result,
        tradability=trad_result,
        universe_count=len(universe),
        normalized_bar_count=len(bars),
    )
