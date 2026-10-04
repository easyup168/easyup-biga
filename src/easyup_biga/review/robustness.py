"""P4-7 robustness endpoints computed only from point-in-time DatasetSnapshots."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from easyup_biga.data import analytics
from easyup_biga.data.contracts import DatasetStatus

__all__ = ["EquityEWResult", "resolve_equity_ew_h5"]


@dataclass(frozen=True, slots=True)
class EquityEWResult:
    available: bool
    value: float | None
    coverage_ratio: float
    eligible_count: int
    included_count: int
    source_snapshot_ids: tuple[str, ...]
    reason: str | None = None


def resolve_equity_ew_h5(
    outcome: Mapping[str, Any],
    *,
    db_path=None,
    data_root="data",
    equity_loader: Callable[..., list[dict[str, Any]]] | None = None,
    tradability_loader: Callable[..., list[dict[str, Any]]] | None = None,
    factor_loader: Callable[..., list[dict[str, Any]]] | None = None,
    state_loader: Callable[..., dict[str, dict[str, Any]]] | None = None,
) -> EquityEWResult:
    """Compute H5 anchor-open to H5-close adjusted equal-weight equity return.

    Universe = securities OPEN on the anchor day.  A security contributes only when anchor
    open, H5 close and both adjustment factors are present.  All three datasets must have a
    COMPLETE latest revision for every H5 day at the OutcomeSet knowledge cutoff; a newer
    quarantined/partial revision blocks robustness rather than falling back silently.
    """
    schedule = dict((outcome.get("manifest") or {}).get("schedule") or {})
    days = tuple(str(x) for x in (schedule.get("open_trade_dates") or ()))[:5]
    if len(days) < 5:
        return EquityEWResult(False, None, 0.0, 0, 0, (), "UNDERMATURE_H5")
    cutoff = str(outcome["knowledge_cutoff"])
    start, end = days[0], days[-1]
    state_loader = state_loader or analytics.resolve_dataset_snapshots_as_of
    source_ids: list[str] = []
    for dataset_id in (
        "cn.equity.daily_bars", "cn.security.tradability", "cn.equity.adjustment_factors"
    ):
        states = state_loader(
            dataset_id, db_path=db_path, knowledge_cutoff=cutoff,
            start_date=start, end_date=end,
        )
        for day in days:
            state = states.get(day)
            if state is None:
                return EquityEWResult(False, None, 0.0, 0, 0, (), f"MISSING_{dataset_id}:{day}")
            if str(state.get("status")) != DatasetStatus.COMPLETE.value:
                return EquityEWResult(
                    False, None, 0.0, 0, 0, (),
                    f"NON_COMPLETE_{dataset_id}:{day}:{state.get('status')}",
                )
            source_ids.append(str(state.get("snapshot_id") or ""))

    equity_loader = equity_loader or analytics.query_eod_as_of
    tradability_loader = tradability_loader or analytics.query_tradability_as_of
    factor_loader = factor_loader or analytics.query_adjustment_factors_as_of
    bars = equity_loader(
        db_path=db_path, knowledge_cutoff=cutoff, start_date=start, end_date=end
    )
    trad = tradability_loader(
        db_path=db_path, knowledge_cutoff=cutoff, start_date=start, end_date=end
    )
    factors = factor_loader(
        db_path=db_path, knowledge_cutoff=cutoff, start_date=start, end_date=end
    )
    by_bar = {(str(r["trade_date"]), str(r["instrument_id"])): r for r in bars}
    by_factor = {(str(r["trade_date"]), str(r["instrument_id"])): r for r in factors}
    anchor_open = {
        str(r["instrument_id"])
        for r in trad
        if str(r.get("trade_date")) == start and str(r.get("status")) == "OPEN"
    }
    returns: list[float] = []
    for iid in sorted(anchor_open):
        first = by_bar.get((start, iid))
        last = by_bar.get((end, iid))
        f0 = by_factor.get((start, iid))
        f5 = by_factor.get((end, iid))
        if first is None or last is None or f0 is None or f5 is None:
            continue
        base = float(first["open"]) * float(f0["adjustment_factor"])
        final = float(last["close"]) * float(f5["adjustment_factor"])
        if base <= 0 or final <= 0:
            continue
        returns.append(final / base - 1.0)
    eligible = len(anchor_open)
    included = len(returns)
    coverage = included / eligible if eligible else 0.0
    if not returns:
        return EquityEWResult(
            False, None, coverage, eligible, included,
            tuple(dict.fromkeys(x for x in source_ids if x)), "NO_COMPLETE_EQUITY_SAMPLE",
        )
    return EquityEWResult(
        True, float(statistics.fmean(returns)), coverage, eligible, included,
        tuple(dict.fromkeys(x for x in source_ids if x)), None,
    )
