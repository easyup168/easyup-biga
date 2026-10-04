"""P4-0 recurring index EOD dataset used by Review & Measurement outcomes.

``cn.index.daily_bars`` remains the *decision-input* snapshot frozen per EvidenceSet.
Outcome measurement needs a different lifecycle: one immutable partition per trade date,
revisions through ``data_version``, and point-in-time cross-day queries.  P4-0 therefore
introduces ``cn.index.eod_bars`` instead of overloading the decision snapshot dataset.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from easyup_biga.data.contracts import DataIssue, DatasetStatus
from easyup_biga.domain import now_cn
from easyup_biga.providers.sina import IndexDaily, SINA_SYMBOLS, fetch_index_daily

from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.index.eod_bars"
PROVIDER_ID = "sina"
JOB_ID = "index-eod-bars"

#: Stable market identities used by Phase 4 TargetDefinition.
INDEX_INSTRUMENTS: Mapping[str, str] = {
    SINA_SYMBOLS["sh"]: "index.sh000001",
    SINA_SYMBOLS["sz"]: "index.sz399106",
}


@dataclass(frozen=True, slots=True)
class IndexEodBar:
    instrument_id: str
    source_symbol: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume_shares: int
    available_at: str
    retrieved_at: str
    provider_id: str = PROVIDER_ID

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _validate_trade_date(trade_date: str) -> str:
    value = str(trade_date)
    if len(value) != 8 or not value.isdigit():
        raise ValueError("trade_date must be YYYYMMDD")
    date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return value


def normalize(
    fetched: Mapping[str, IndexDaily], trade_date: str, retrieved_at: str
) -> tuple[IndexEodBar, ...]:
    """Select exactly ``trade_date`` from each expected index response.

    Missing target-day rows are *not* substituted with the latest available day.  Doing so
    would silently shift the outcome horizon across a holiday/provider lag boundary.  The
    quality layer marks missing components PARTIAL instead.
    """
    day = _validate_trade_date(trade_date)
    out: list[IndexEodBar] = []
    for symbol, instrument_id in INDEX_INSTRUMENTS.items():
        result = fetched.get(symbol)
        if result is None:
            continue
        if result.symbol != symbol:
            raise ValueError(f"fetch result symbol mismatch: expected={symbol} got={result.symbol}")
        if result.provider_id != PROVIDER_ID:
            raise ValueError(
                f"{symbol}: expected provider {PROVIDER_ID!r}, got {result.provider_id!r}"
            )
        matches = [bar for bar in result.bars if bar.day == day]
        if len(matches) > 1:
            raise ValueError(f"{symbol}: duplicate EOD rows for {day}")
        if not matches:
            continue
        bar = matches[0]
        out.append(
            IndexEodBar(
                instrument_id=instrument_id,
                source_symbol=symbol,
                trade_date=day,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume_shares=int(bar.volume),
                available_at=retrieved_at,
                retrieved_at=retrieved_at,
            )
        )
    return tuple(sorted(out, key=lambda item: item.instrument_id))


def quality(
    bars: tuple[IndexEodBar, ...],
) -> tuple[DatasetStatus, dict[str, Any], tuple[DataIssue, ...]]:
    expected_ids = frozenset(INDEX_INSTRUMENTS.values())
    actual_ids = [bar.instrument_id for bar in bars]
    duplicate_count = len(actual_ids) - len(set(actual_ids))
    missing = sorted(expected_ids - set(actual_ids))
    unexpected = sorted(set(actual_ids) - expected_ids)
    invalid = sum(
        1
        for bar in bars
        if (
            bar.high < max(bar.open, bar.close)
            or bar.low > min(bar.open, bar.close)
            or bar.high < bar.low
            or min(bar.open, bar.high, bar.low, bar.close) <= 0
            or bar.volume_shares < 0
        )
    )

    issues: list[DataIssue] = []
    if duplicate_count:
        issues.append(
            DataIssue(
                "data.quality.duplicate_key",
                "CRITICAL",
                f"duplicate index rows={duplicate_count}",
            )
        )
    if invalid:
        issues.append(
            DataIssue(
                "data.quality.invalid_ohlc",
                "CRITICAL",
                f"invalid index bars={invalid}",
            )
        )
    if unexpected:
        issues.append(
            DataIssue(
                "data.provider.schema_changed",
                "CRITICAL",
                f"unexpected index identities={unexpected}",
            )
        )
    if missing:
        issues.append(
            DataIssue(
                "data.quality.coverage_below_threshold",
                "ERROR",
                f"missing index components={missing}",
            )
        )

    if duplicate_count or invalid or unexpected:
        status = DatasetStatus.QUARANTINED
    elif missing:
        status = DatasetStatus.PARTIAL
    else:
        status = DatasetStatus.COMPLETE

    return (
        status,
        {
            "expected_count": len(expected_ids),
            "row_count": len(bars),
            "coverage_ratio": len(set(actual_ids) & expected_ids) / len(expected_ids),
            "missing_instruments": missing,
            "unexpected_instruments": unexpected,
            "invalid_ohlc_count": invalid,
            "duplicate_count": duplicate_count,
        },
        tuple(issues),
    )


def _raw_bundle_text(fetched: Mapping[str, IndexDaily]) -> str:
    """Preserve both exact provider response texts in one adapter-level raw bundle.

    The publisher currently records one RawArtifact per published partition.  This dataset
    performs two Sina requests (SH + SZ), so the artifact is a deterministic JSON envelope
    containing the exact response text when available, otherwise the parsed raw payload used
    by offline fixtures.  No source bytes are discarded.
    """
    bundle: dict[str, Any] = {"provider_id": PROVIDER_ID, "responses": {}}
    responses = bundle["responses"]
    for symbol in sorted(fetched):
        item = fetched[symbol]
        responses[symbol] = {
            "source": item.source,
            "adapter_version": item.adapter_version,
            "raw_text": item.raw_text,
            "raw_payload": None if item.raw_text is not None else item.raw,
        }
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def publish_results(
    fetched: Mapping[str, IndexDaily],
    trade_date: str,
    retrieved_at: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
) -> PublishResult:
    bars = normalize(fetched, trade_date, retrieved_at)
    status, metrics, issues = quality(bars)
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        provider_id=PROVIDER_ID,
        partition_key={"trade_date": _validate_trade_date(trade_date)},
        raw_text=_raw_bundle_text(fetched),
        rows=[bar.to_dict() for bar in bars],
        as_of=trade_date,
        quality_status=status,
        quality_metrics=metrics,
        quality_issues=issues,
        new_revision=new_revision,
    )


def run(
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
    bars: int = 40,
    fetcher: Callable[..., IndexDaily] = fetch_index_daily,
) -> PublishResult:
    """Fetch both Phase-4 index targets and publish one immutable trade-date partition.

    ``bars`` is intentionally larger than one: running after a weekend/holiday or catching up
    a recent missed EOD can still select the requested trade date without making a second API
    contract.  The requested date must be present exactly; no "latest row" substitution.
    """
    day = _validate_trade_date(trade_date)
    fetched = {
        symbol: fetcher(symbol, bars=bars)
        for symbol in INDEX_INSTRUMENTS
    }
    retrieved_at = now_cn().isoformat()
    return publish_results(
        fetched,
        day,
        retrieved_at,
        db_path=db_path,
        data_root=data_root,
        new_revision=new_revision,
    )
