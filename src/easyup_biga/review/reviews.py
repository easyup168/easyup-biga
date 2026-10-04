"""P4-5 deterministic per-card ReviewRecord engine.

Review is a readable projection over frozen ReviewSubject + OutcomeSet facts.  It is not a
statistical conclusion and it never calls an Agent or a Provider.  Continuous OutcomeMetric
values remain authoritative; the label is only a policy-versioned summary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from easyup_biga.persistence.review import (
    latest_outcome_set,
    latest_review_record,
    load_outcome_set,
    load_review_record,
    load_review_subject,
    save_review_record,
)

from .contracts import (
    ReviewRecord,
    content_sha256,
    review_content_payload,
    review_id_for,
)
from .registry import get_review_policy

__all__ = ["ReviewBuildResult", "build_review"]

DEFAULT_OUTCOME_POLICY_ID = "market-h1-h3-h5-h20-v1"
DEFAULT_REVIEW_POLICY_ID = "directional-review-v1"


@dataclass(frozen=True, slots=True)
class ReviewBuildResult:
    review_subject_id: str
    outcome_set_id: str | None
    review_id: str | None
    created: bool
    review_status: str
    review_label: str | None
    reason: str | None = None


def _primary_metrics(
    outcome: Mapping[str, Any], target_id: str, horizon: str
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in outcome.get("metrics") or ():
        if str(item.get("target_id")) != target_id or str(item.get("horizon")) != horizon:
            continue
        name = str(item.get("metric_name") or "")
        if not name:
            continue
        selected[name] = {
            "value": float(item["value"]),
            "unit": str(item["unit"]),
            "observed_trade_date": str(item["observed_trade_date"]),
            "coverage_ratio": float(item["coverage_ratio"]),
            "source_snapshot_ids": list(item.get("source_snapshot_ids") or ()),
        }
    return selected


def _direction(card_status: str, risk_veto: bool) -> tuple[int | None, str | None]:
    if card_status == "BUY":
        return 1, None
    if card_status == "AVOID":
        return -1, None
    if card_status == "BLOCK" and risk_veto:
        return -1, None
    if card_status == "WAIT":
        return None, "ABSTAIN"
    if card_status == "BLOCK":
        return None, "ABSTAIN_SYSTEM"
    return None, "UNSCORABLE"


def _review_summary(
    subject: Mapping[str, Any],
    outcome: Mapping[str, Any],
    review_policy_id: str,
) -> tuple[str, str, float | None, dict[str, Any]]:
    policy = get_review_policy(review_policy_id)
    subject_manifest = dict(subject.get("manifest") or {})
    risk_veto = bool(subject_manifest.get("risk_veto"))
    card_status = str(subject["card_status"])
    primary = _primary_metrics(outcome, policy.primary_target_id, policy.primary_horizon)
    primary_return = primary.get("return")

    base_detail: dict[str, Any] = {
        "card": {
            "status": card_status,
            "risk_veto": risk_veto,
            "stances": dict(subject_manifest.get("stances") or {}),
            "missing_codes": list(subject_manifest.get("missing_codes") or ()),
        },
        "outcome": {
            "status": str(outcome["status"]),
            "data_version": int(outcome["data_version"]),
            "knowledge_cutoff": str(outcome["knowledge_cutoff"]),
            "anchor_trade_date": str(outcome["anchor_trade_date"]),
            "content_sha256": str(outcome["content_sha256"]),
        },
        "primary": {
            "target_id": policy.primary_target_id,
            "horizon": policy.primary_horizon,
            "metrics": primary,
        },
        "rule": {
            "noise_band_ratio": float(policy.noise_band_ratio),
            "continuous_metrics_are_authority": True,
        },
    }

    if str(outcome["status"]) == "QUARANTINED":
        base_detail["reason"] = "OUTCOME_QUARANTINED"
        base_detail["blocking_reason"] = dict((outcome.get("manifest") or {}).get("blocking_reason") or {})
        return "UNSCORABLE", "UNSCORABLE", None, base_detail

    if primary_return is None:
        base_detail["reason"] = "UNDERMATURE_H5"
        return "INSUFFICIENT_DATA", "UNSCORABLE", None, base_detail

    direction, abstain_label = _direction(card_status, risk_veto)
    if abstain_label == "UNSCORABLE":
        base_detail["reason"] = "UNSUPPORTED_CARD_STATUS"
        return "UNSCORABLE", "UNSCORABLE", None, base_detail
    if abstain_label is not None:
        base_detail["direction"] = None
        base_detail["reason"] = "NO_DIRECTIONAL_COMPARISON"
        return "COMPLETE", abstain_label, None, base_detail

    observed_return = float(primary_return["value"])
    utility_score = float(direction) * observed_return
    band = float(policy.noise_band_ratio)
    if utility_score > band:
        label = "SUPPORTS"
    elif utility_score < -band:
        label = "OPPOSES"
    else:
        label = "FLAT"
    base_detail["direction"] = int(direction)
    base_detail["utility_definition"] = "direction * primary_h5_return"
    return "COMPLETE", label, utility_score, base_detail


def build_review(
    review_subject_id: str,
    *,
    path=None,
    outcome_set_id: str | None = None,
    outcome_policy_id: str = DEFAULT_OUTCOME_POLICY_ID,
    review_policy_id: str = DEFAULT_REVIEW_POLICY_ID,
) -> ReviewBuildResult:
    """Build/reuse one deterministic ReviewRecord for one OutcomeSet revision."""
    subject = load_review_subject(review_subject_id, path=path)
    if subject is None:
        raise KeyError(f"ReviewSubject not found: {review_subject_id}")
    if str(subject["eligibility"]) != "ELIGIBLE":
        return ReviewBuildResult(
            review_subject_id, None, None, False, "UNSCORABLE", None,
            reason=str(subject.get("exclusion_code") or "SUBJECT_INELIGIBLE"),
        )

    outcome = (
        load_outcome_set(outcome_set_id, path=path)
        if outcome_set_id is not None
        else latest_outcome_set(review_subject_id, outcome_policy_id, path=path)
    )
    if outcome is None:
        return ReviewBuildResult(
            review_subject_id, None, None, False, "INSUFFICIENT_DATA", None,
            reason="NO_OUTCOME_SET",
        )
    if str(outcome["review_subject_id"]) != review_subject_id:
        raise ValueError("OutcomeSet belongs to a different ReviewSubject")

    policy = get_review_policy(review_policy_id)
    rid = review_id_for(review_subject_id, str(outcome["outcome_set_id"]), policy.policy_id)
    existing = load_review_record(rid, path=path)
    if existing is not None:
        return ReviewBuildResult(
            review_subject_id,
            str(outcome["outcome_set_id"]),
            rid,
            False,
            str(existing["review_status"]),
            str(existing["review_label"]),
            reason="REUSED",
        )

    latest = latest_review_record(review_subject_id, policy.policy_id, path=path)
    supersedes = None if latest is None else str(latest["review_id"])
    review_status, review_label, utility_score, detail = _review_summary(
        subject, outcome, policy.policy_id
    )
    payload = review_content_payload(
        review_subject_id=review_subject_id,
        outcome_set_id=str(outcome["outcome_set_id"]),
        review_policy_id=policy.policy_id,
        review_status=review_status,
        review_label=review_label,
        utility_score=utility_score,
        primary_horizon=policy.primary_horizon,
        detail=detail,
        supersedes_review_id=supersedes,
    )
    record = ReviewRecord(
        review_id=rid,
        review_subject_id=review_subject_id,
        outcome_set_id=str(outcome["outcome_set_id"]),
        review_policy_id=policy.policy_id,
        review_status=review_status,
        review_label=review_label,
        utility_score=utility_score,
        primary_horizon=policy.primary_horizon,
        detail=detail,
        content_sha256=content_sha256(payload),
        supersedes_review_id=supersedes,
    )
    save_review_record(record, path=path)
    return ReviewBuildResult(
        review_subject_id,
        record.outcome_set_id,
        record.review_id,
        True,
        record.review_status,
        record.review_label,
    )
