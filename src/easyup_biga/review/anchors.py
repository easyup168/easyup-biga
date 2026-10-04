"""P4-3 deterministic market-time anchor resolution.

The anchor is part of the evaluation contract, not a convenience lookup.  It is therefore
resolved from *point-in-time* trading-calendar facts and carries the exact fact lineage used
for the decision.  The outcome build may run days later, but it must not rewrite the anchor
with calendar information that was not available when the DecisionCard was created.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from easyup_biga.persistence import connect

from .contracts import horizon_trading_days
from .registry import (
    AnchorPolicyDefinition,
    OutcomePolicyDefinition,
    get_anchor_policy,
    get_outcome_policy,
)

__all__ = [
    "AnchorResolution",
    "AnchorResolutionError",
    "HorizonSchedule",
    "resolve_anchor",
    "resolve_horizon_schedule",
]


class AnchorResolutionError(RuntimeError):
    """The frozen calendar cannot resolve the requested anchor/schedule honestly."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _CalendarFact:
    fact_id: int
    trade_date: str
    is_open: bool
    source: str
    retrieved_at: str
    raw_snapshot_id: int | None


@dataclass(frozen=True, slots=True)
class AnchorResolution:
    anchor_policy_id: str
    anchor_trade_date: str
    decision_at: str
    decision_calendar_cutoff: str
    calendar_fact_ids: tuple[int, ...]
    calendar_raw_snapshot_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_policy_id": self.anchor_policy_id,
            "anchor_trade_date": self.anchor_trade_date,
            "decision_at": self.decision_at,
            "decision_calendar_cutoff": self.decision_calendar_cutoff,
            "calendar_fact_ids": list(self.calendar_fact_ids),
            "calendar_raw_snapshot_ids": list(self.calendar_raw_snapshot_ids),
        }


@dataclass(frozen=True, slots=True)
class HorizonSchedule:
    outcome_policy_id: str
    anchor_trade_date: str
    knowledge_cutoff: str
    horizon_trade_dates: Mapping[str, str]
    open_trade_dates: tuple[str, ...]
    calendar_fact_ids: tuple[int, ...]
    calendar_raw_snapshot_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "horizon_trade_dates", MappingProxyType(dict(self.horizon_trade_dates)))
        object.__setattr__(self, "open_trade_dates", tuple(self.open_trade_dates))
        object.__setattr__(self, "calendar_fact_ids", tuple(self.calendar_fact_ids))
        object.__setattr__(self, "calendar_raw_snapshot_ids", tuple(self.calendar_raw_snapshot_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_policy_id": self.outcome_policy_id,
            "anchor_trade_date": self.anchor_trade_date,
            "knowledge_cutoff": self.knowledge_cutoff,
            "horizon_trade_dates": dict(self.horizon_trade_dates),
            "open_trade_dates": list(self.open_trade_dates),
            "calendar_fact_ids": list(self.calendar_fact_ids),
            "calendar_raw_snapshot_ids": list(self.calendar_raw_snapshot_ids),
        }


def _aware(value: str, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AnchorResolutionError("INVALID_TIMESTAMP", f"{name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnchorResolutionError("INVALID_TIMESTAMP", f"{name} must include timezone")
    return parsed


def _utc(value: str) -> datetime:
    return _aware(value, name="retrieved_at").astimezone(timezone.utc)


def _latest_calendar_facts_as_of(
    *, path, knowledge_cutoff: str, start_date: str | None = None
) -> dict[str, _CalendarFact]:
    """Return the newest fact for each date visible at ``knowledge_cutoff``.

    We intentionally compare parsed timestamps in Python instead of relying on lexical SQLite
    ordering across arbitrary UTC offsets.  The table is small (one row per natural day per
    revision), so the stronger time semantics are worth the tiny cost.
    """
    cutoff_dt = _aware(knowledge_cutoff, name="knowledge_cutoff").astimezone(timezone.utc)
    sql = (
        "SELECT fact_id,trade_date,is_open,source,retrieved_at,snapshot_id "
        "FROM fact_trading_calendar"
    )
    args: tuple[Any, ...] = ()
    if start_date is not None:
        sql += " WHERE trade_date>=?"
        args = (start_date,)
    sql += " ORDER BY trade_date,retrieved_at,fact_id"
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, args).fetchall()
    chosen: dict[str, tuple[datetime, int, _CalendarFact]] = {}
    for row in rows:
        try:
            retrieved = _utc(str(row["retrieved_at"]))
        except AnchorResolutionError:
            continue
        if retrieved > cutoff_dt:
            continue
        fact = _CalendarFact(
            fact_id=int(row["fact_id"]),
            trade_date=str(row["trade_date"]),
            is_open=bool(row["is_open"]),
            source=str(row["source"]),
            retrieved_at=str(row["retrieved_at"]),
            raw_snapshot_id=None if row["snapshot_id"] is None else int(row["snapshot_id"]),
        )
        rank = (retrieved, fact.fact_id)
        prev = chosen.get(fact.trade_date)
        if prev is None or rank > (prev[0], prev[1]):
            chosen[fact.trade_date] = (retrieved, fact.fact_id, fact)
    return {day: item[2] for day, item in chosen.items()}


def _lineage(facts: Iterable[_CalendarFact]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    items = tuple(facts)
    fact_ids = tuple(item.fact_id for item in items)
    raw = tuple(dict.fromkeys(item.raw_snapshot_id for item in items if item.raw_snapshot_id is not None))
    return fact_ids, raw


def _cutoff_time(policy: AnchorPolicyDefinition) -> time:
    hh, mm, ss = (int(part) for part in policy.cutoff_local_time.split(":"))
    return time(hh, mm, ss)


def resolve_anchor(
    subject: Mapping[str, Any],
    *,
    path=None,
    anchor_policy_id: str | None = None,
) -> AnchorResolution:
    """Resolve the subject's ex-ante anchor using only calendar facts known at decision time."""
    if str(subject.get("eligibility")) != "ELIGIBLE":
        raise AnchorResolutionError(
            "SUBJECT_INELIGIBLE",
            f"ReviewSubject {subject.get('review_subject_id')} is not eligible",
        )
    policy_id = anchor_policy_id or str(subject.get("anchor_policy_id") or "")
    policy = get_anchor_policy(policy_id)
    decision_at = str(subject.get("decision_at") or "")
    decision_dt = _aware(decision_at, name="decision_at")
    local_dt = decision_dt.astimezone(ZoneInfo(policy.timezone))
    local_day = local_dt.strftime("%Y%m%d")
    facts = _latest_calendar_facts_as_of(
        path=path, knowledge_cutoff=decision_at, start_date=local_day
    )
    today = facts.get(local_day)
    if today is None:
        raise AnchorResolutionError(
            "CALENDAR_NOT_KNOWN_AT_DECISION",
            f"no trading-calendar fact for {local_day} was visible at decision time",
        )

    scanned: list[_CalendarFact] = [today]
    before_cutoff = local_dt.timetz().replace(tzinfo=None) < _cutoff_time(policy)
    if today.is_open and before_cutoff:
        anchor = local_day
    else:
        anchor = ""
        for day in sorted(d for d in facts if d > local_day):
            item = facts[day]
            scanned.append(item)
            if item.is_open:
                anchor = day
                break
        if not anchor:
            raise AnchorResolutionError(
                "CALENDAR_NEXT_OPEN_NOT_KNOWN",
                f"no future open trading day was visible at decision time after {local_day}",
            )

    fact_ids, raw_ids = _lineage(scanned)
    return AnchorResolution(
        anchor_policy_id=policy.policy_id,
        anchor_trade_date=anchor,
        decision_at=decision_at,
        decision_calendar_cutoff=decision_at,
        calendar_fact_ids=fact_ids,
        calendar_raw_snapshot_ids=raw_ids,
    )


def resolve_horizon_schedule(
    anchor_trade_date: str,
    knowledge_cutoff: str,
    *,
    path=None,
    outcome_policy_id: str = "market-h1-h3-h5-h20-v1",
) -> HorizonSchedule:
    """Resolve H1/H3/H5/H20 dates from the PIT calendar visible at outcome build time."""
    policy: OutcomePolicyDefinition = get_outcome_policy(outcome_policy_id)
    facts = _latest_calendar_facts_as_of(
        path=path, knowledge_cutoff=knowledge_cutoff, start_date=anchor_trade_date
    )
    anchor_fact = facts.get(anchor_trade_date)
    if anchor_fact is None:
        raise AnchorResolutionError(
            "CALENDAR_ANCHOR_NOT_VISIBLE",
            f"anchor {anchor_trade_date} is not covered by PIT calendar at {knowledge_cutoff}",
        )
    if not anchor_fact.is_open:
        raise AnchorResolutionError(
            "CALENDAR_ANCHOR_REVISED_CLOSED",
            f"anchor {anchor_trade_date} is closed in PIT calendar at {knowledge_cutoff}",
        )

    # 🔴 下标从 horizon 名派生（H<N> ⇒ 第 N 个交易日 ⇒ 下标 N-1），不手写映射表。
    #    手写那份漏一个 key 时**不报错**：policy 里有、表里没有的 horizon
    #    会被静默丢掉，症状是「它永远不成熟」。
    horizon_index = {h: horizon_trading_days(h) - 1 for h in policy.horizons}
    open_days = tuple(day for day in sorted(facts) if facts[day].is_open)
    if not open_days or open_days[0] != anchor_trade_date:
        raise AnchorResolutionError(
            "CALENDAR_ANCHOR_SEQUENCE_INVALID",
            f"PIT calendar does not start with anchor open day {anchor_trade_date}",
        )
    horizon_dates = {
        horizon: open_days[index]
        for horizon, index in horizon_index.items()
        if len(open_days) > index
    }
    last_day = max(horizon_dates.values(), default=anchor_trade_date)
    lineage_facts = tuple(facts[day] for day in sorted(facts) if anchor_trade_date <= day <= last_day)
    fact_ids, raw_ids = _lineage(lineage_facts)
    return HorizonSchedule(
        outcome_policy_id=policy.policy_id,
        anchor_trade_date=anchor_trade_date,
        knowledge_cutoff=knowledge_cutoff,
        horizon_trade_dates=horizon_dates,
        open_trade_dates=tuple(day for day in open_days if day <= last_day),
        calendar_fact_ids=fact_ids,
        calendar_raw_snapshot_ids=raw_ids,
    )
