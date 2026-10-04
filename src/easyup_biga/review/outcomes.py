"""P4-4 deterministic OutcomeSet build, maturity and revision.

This module contains no network access.  It consumes the frozen ReviewSubject, point-in-time
calendar facts and point-in-time DatasetSnapshots selected through the Phase-3 control plane.
Each new horizon or upstream data revision appends a new OutcomeSet; old results are never
updated in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from easyup_biga.persistence.review import (
    latest_outcome_set,
    latest_outcome_set_as_of,
    load_review_subject,
    save_outcome_bundle,
)
from easyup_biga.timebase import cn_iso, parse_aware

from .anchors import (
    AnchorResolution,
    AnchorResolutionError,
    HorizonSchedule,
    resolve_anchor,
    resolve_horizon_schedule,
)
from .contracts import (
    OutcomeMetric,
    OutcomeSet,
    canonical_json,
    content_sha256,
    horizon_trading_days,
    outcome_set_id_for,
)
from .metric_algorithms import calculate_target_metrics as _calculate_target_metrics
from .registry import OutcomePolicyDefinition, get_outcome_policy
from .targets import TargetBar, TargetResolution, TargetResolver

__all__ = [
    "OutcomeBuildResult",
    "build_outcome",
    "calculate_target_metrics",
]

_PRIMARY_TARGETS = ("index.sh000001", "index.sz399106", "market.index_blend.v1")


@dataclass(frozen=True, slots=True)
class OutcomeBuildResult:
    review_subject_id: str
    outcome_set_id: str | None
    created: bool
    status: str
    data_version: int | None
    matured_horizons: tuple[str, ...]
    reason: str | None = None


def _aware(value: str, *, name: str) -> datetime:
    return parse_aware(value, name=name)


def _metric_payload(metric: OutcomeMetric) -> dict[str, Any]:
    return {
        "target_id": metric.target_id,
        "horizon": metric.horizon,
        "observed_trade_date": metric.observed_trade_date,
        "metric_name": metric.metric_name,
        "value": float(metric.value),
        "unit": metric.unit,
        "source_snapshot_ids": list(metric.source_snapshot_ids),
        "coverage_ratio": float(metric.coverage_ratio),
    }


def _candidate_payload(
    *,
    review_subject_id: str,
    outcome_policy_id: str,
    anchor_trade_date: str,
    status: str,
    manifest: Mapping[str, Any],
    metrics: tuple[OutcomeMetric, ...],
) -> dict[str, Any]:
    return {
        "review_subject_id": review_subject_id,
        "outcome_policy_id": outcome_policy_id,
        "anchor_trade_date": anchor_trade_date,
        "status": status,
        "manifest": dict(manifest),
        "metrics": [
            _metric_payload(metric)
            for metric in sorted(metrics, key=lambda x: (x.target_id, x.horizon, x.metric_name))
        ],
    }


def _latest_payload(existing: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "review_subject_id": str(existing["review_subject_id"]),
        "outcome_policy_id": str(existing["outcome_policy_id"]),
        "anchor_trade_date": str(existing["anchor_trade_date"]),
        "status": str(existing["status"]),
        "manifest": dict(existing["manifest"]),
        "metrics": [
            {
                "target_id": str(item["target_id"]),
                "horizon": str(item["horizon"]),
                "observed_trade_date": str(item["observed_trade_date"]),
                "metric_name": str(item["metric_name"]),
                "value": float(item["value"]),
                "unit": str(item["unit"]),
                "source_snapshot_ids": list(item["source_snapshot_ids"]),
                "coverage_ratio": float(item["coverage_ratio"]),
            }
            for item in sorted(
                existing.get("metrics") or (),
                key=lambda x: (x["target_id"], x["horizon"], x["metric_name"]),
            )
        ],
    }


def calculate_target_metrics(
    target_id: str,
    horizon: str,
    bars: tuple[TargetBar, ...],
    *,
    expected_days: int,
    metric_algorithm_id: str = "market-index-outcome-synchronized-close-v1",
) -> tuple[OutcomeMetric, ...]:
    """Compatibility wrapper over the versioned metric-algorithm registry."""
    return _calculate_target_metrics(
        target_id,
        horizon,
        bars,
        expected_days=expected_days,
        metric_algorithm_id=metric_algorithm_id,
    )


def _available_horizons(
    schedule: HorizonSchedule,
    targets: TargetResolution,
    policy_horizons: tuple[str, ...],
) -> tuple[str, ...]:
    """Longest contiguous mature prefix; H5 never appears without H1/H3."""
    matured: list[str] = []
    blend_by_day = {bar.trade_date: bar for bar in targets.bars_for("market.index_blend.v1")}
    for horizon in policy_horizons:
        wanted = horizon_trading_days(horizon)
        if horizon not in schedule.horizon_trade_dates or len(schedule.open_trade_dates) < wanted:
            break
        required = schedule.open_trade_dates[:wanted]
        if any(day not in blend_by_day for day in required):
            break
        matured.append(horizon)
    return tuple(matured)


def _metrics_for_horizons(
    schedule: HorizonSchedule,
    targets: TargetResolution,
    matured: tuple[str, ...],
    policy: OutcomePolicyDefinition,
) -> tuple[OutcomeMetric, ...]:
    metrics: list[OutcomeMetric] = []
    for horizon in matured:
        wanted = horizon_trading_days(horizon)
        required_days = set(schedule.open_trade_dates[:wanted])
        for target_id in _PRIMARY_TARGETS:
            series = tuple(
                bar for bar in targets.bars_for(target_id) if bar.trade_date in required_days
            )
            calculated = calculate_target_metrics(
                target_id,
                horizon,
                series,
                expected_days=wanted,
                metric_algorithm_id=policy.metric_algorithm_id,
            )
            metrics.extend(item for item in calculated if item.metric_name in policy.metric_ids)
    return tuple(metrics)


def _blocking_snapshot(
    schedule: HorizonSchedule,
    targets: TargetResolution,
    *,
    up_to_horizon: str | None,
) -> tuple[str, str, str] | None:
    """Return (trade_date,status,snapshot_id) when a newer revision blocks trusted data."""
    if up_to_horizon is None:
        days = schedule.open_trade_dates[:1]
    else:
        days = schedule.open_trade_dates[: horizon_trading_days(up_to_horizon)]
    for day in days:
        state = targets.snapshot_states.get(day)
        if state is None:
            continue
        status = str(state.get("status") or "")
        if status and status != "COMPLETE":
            return day, status, str(state.get("snapshot_id") or "")
    return None


def build_outcome(
    review_subject_id: str,
    knowledge_cutoff: str,
    *,
    path=None,
    data_root="data",
    outcome_policy_id: str = "market-h1-h3-h5-h20-v1",
    target_resolver: TargetResolver | None = None,
) -> OutcomeBuildResult:
    """Build or advance one subject's OutcomeSet at an explicit point-in-time cutoff."""
    knowledge_cutoff = cn_iso(knowledge_cutoff, name="knowledge_cutoff")
    cutoff_dt = _aware(knowledge_cutoff, name="knowledge_cutoff")
    subject = load_review_subject(review_subject_id, path=path)
    if subject is None:
        raise KeyError(f"ReviewSubject not found: {review_subject_id}")
    if subject["eligibility"] != "ELIGIBLE":
        return OutcomeBuildResult(
            review_subject_id, None, False, "INELIGIBLE", None, (),
            reason=str(subject.get("exclusion_code") or "SUBJECT_INELIGIBLE"),
        )
    decision_dt = _aware(str(subject["decision_at"]), name="decision_at")
    if cutoff_dt < decision_dt:
        raise ValueError("knowledge_cutoff cannot precede decision_at")

    policy = get_outcome_policy(outcome_policy_id)
    latest = latest_outcome_set(review_subject_id, policy.policy_id, path=path)
    if latest is not None and cutoff_dt < _aware(
        str(latest["knowledge_cutoff"]), name="latest knowledge_cutoff"
    ):
        historical = latest_outcome_set_as_of(
            review_subject_id, policy.policy_id, knowledge_cutoff, path=path
        )
        if historical is None:
            return OutcomeBuildResult(
                review_subject_id, None, False, "NOT_MATURE", None, (),
                reason="HISTORICAL_AS_OF_NOT_PERSISTED",
            )
        return OutcomeBuildResult(
            review_subject_id=review_subject_id,
            outcome_set_id=str(historical["outcome_set_id"]),
            created=False,
            status=str(historical["status"]),
            data_version=int(historical["data_version"]),
            matured_horizons=tuple(historical["manifest"].get("matured_horizons") or ()),
            reason="HISTORICAL_AS_OF_REUSED",
        )

    try:
        anchor: AnchorResolution = resolve_anchor(subject, path=path)
        schedule = resolve_horizon_schedule(
            anchor.anchor_trade_date,
            knowledge_cutoff,
            path=path,
            outcome_policy_id=policy.policy_id,
        )
    except AnchorResolutionError as exc:
        return OutcomeBuildResult(
            review_subject_id, None, False, "INELIGIBLE", None, (), reason=exc.code
        )

    resolver = target_resolver or TargetResolver()
    targets = resolver.resolve(
        anchor,
        schedule,
        db_path=path,
        data_root=data_root,
        target_policy_id=str(subject["target_policy_id"]),
    )
    matured = _available_horizons(schedule, targets, policy.horizons)

    latest_matured = tuple((latest or {}).get("manifest", {}).get("matured_horizons") or ())
    first_h1_block = _blocking_snapshot(schedule, targets, up_to_horizon="H1")
    if latest is None and not matured and first_h1_block is not None:
        status = "QUARANTINED"
        metrics = ()
        blocked = first_h1_block
        block_reason = {
            "code": "LATEST_REVISION_NOT_COMPLETE",
            "trade_date": blocked[0],
            "snapshot_status": blocked[1],
            "snapshot_id": blocked[2],
        }
    else:
        status = ""
        metrics = ()
        block_reason = None

    must_preserve = latest_matured[-1] if latest_matured else None
    blocked = _blocking_snapshot(schedule, targets, up_to_horizon=must_preserve)
    if status != "QUARANTINED" and blocked is not None and latest is not None:
        status = "QUARANTINED"
        metrics: tuple[OutcomeMetric, ...] = ()
        block_reason = {
            "code": "LATEST_REVISION_NOT_COMPLETE",
            "trade_date": blocked[0],
            "snapshot_status": blocked[1],
            "snapshot_id": blocked[2],
        }
    elif status != "QUARANTINED":
        if not matured:
            return OutcomeBuildResult(
                review_subject_id,
                None if latest is None else str(latest["outcome_set_id"]),
                False,
                "NOT_MATURE",
                None if latest is None else int(latest["data_version"]),
                latest_matured,
                reason="H1_NOT_MATURE",
            )
        status = "COMPLETE" if matured[-1] == "H20" else "PARTIAL"
        metrics = _metrics_for_horizons(schedule, targets, matured, policy)
        block_reason = None

    source_ids = tuple(
        dict.fromkeys(
            snapshot_id
            for metric in metrics
            for snapshot_id in metric.source_snapshot_ids
        )
    )
    schedule_manifest = schedule.to_dict()
    # OutcomeSet.knowledge_cutoff already records the build cutoff.  Keeping that moving
    # timestamp inside the content hash would manufacture a new revision on every retry even
    # when the exact snapshots/metrics are unchanged.
    schedule_manifest.pop("knowledge_cutoff", None)
    manifest = {
        "anchor": anchor.to_dict(),
        "schedule": schedule_manifest,
        "target_policy_id": str(subject["target_policy_id"]),
        "outcome_policy_id": policy.policy_id,
        "metric_algorithm": policy.metric_algorithm_ref(),
        "matured_horizons": list(matured if status != "QUARANTINED" else latest_matured),
        "source_snapshot_ids": list(source_ids),
        "deferred_targets": dict(targets.deferred_targets),
        "blocking_reason": block_reason,
        "metric_semantics": {
            "market.index_blend.v1": {
                "return": "anchor-open-to-horizon-close",
                "mfe": "synchronized-daily-close-path",
                "mae": "synchronized-daily-close-path",
            },
            "direct_indices": {"mfe": "daily-intraday-high", "mae": "daily-intraday-low"},
        },
    }
    candidate_payload = _candidate_payload(
        review_subject_id=review_subject_id,
        outcome_policy_id=policy.policy_id,
        anchor_trade_date=anchor.anchor_trade_date,
        status=status,
        manifest=manifest,
        metrics=metrics,
    )
    candidate_hash = content_sha256(candidate_payload)

    if latest is not None and content_sha256(_latest_payload(latest)) == candidate_hash:
        return OutcomeBuildResult(
            review_subject_id=review_subject_id,
            outcome_set_id=str(latest["outcome_set_id"]),
            created=False,
            status=str(latest["status"]),
            data_version=int(latest["data_version"]),
            matured_horizons=tuple(latest["manifest"].get("matured_horizons") or ()),
            reason="UNCHANGED",
        )

    version = 1 if latest is None else int(latest["data_version"]) + 1
    supersedes = None if latest is None else str(latest["outcome_set_id"])
    outcome = OutcomeSet(
        outcome_set_id=outcome_set_id_for(review_subject_id, policy.policy_id, version),
        review_subject_id=review_subject_id,
        outcome_policy_id=policy.policy_id,
        anchor_trade_date=anchor.anchor_trade_date,
        knowledge_cutoff=knowledge_cutoff,
        status=status,
        data_version=version,
        manifest=manifest,
        content_sha256=candidate_hash,
        supersedes_outcome_set_id=supersedes,
    )
    save_outcome_bundle(outcome, metrics, path=path)
    return OutcomeBuildResult(
        review_subject_id=review_subject_id,
        outcome_set_id=outcome.outcome_set_id,
        created=True,
        status=status,
        data_version=version,
        matured_horizons=tuple(manifest["matured_horizons"]),
        reason=None,
    )
