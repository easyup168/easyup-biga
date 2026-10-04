"""P4-2 ReviewSubject capture and historical backfill.

The online Decision pipeline is deliberately untouched.  This module asynchronously scans
persisted ``decision_records`` and freezes an evaluation projection exactly once per record.
"""
from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass
from typing import Any

from easyup_biga.domain import DecisionCard, VETO_STANCE
from easyup_biga.persistence.db import load_card_by_record_id
from easyup_biga.timebase import cn_iso, parse_aware
from easyup_biga.persistence.review import (
    list_uncaptured_decision_records,
    save_review_subject,
)

from .contracts import ReviewSubject, review_subject_id_for
from .registry import get_anchor_policy, get_target_policy

__all__ = [
    "ANCHOR_POLICY_ID_V1",
    "TARGET_POLICY_ID_V1",
    "ReviewSubjectSyncResult",
    "build_review_subject",
    "sync_review_subjects",
]

ANCHOR_POLICY_ID_V1 = "cn-a-share-next-executable-open-v1"
TARGET_POLICY_ID_V1 = "market.index_targets.v1"


@dataclass(frozen=True, slots=True)
class ReviewSubjectSyncResult:
    scanned: int
    created: int
    eligible: int
    ineligible: int


def _exact_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_aware_timestamp(value: str) -> bool:
    try:
        parse_aware(value, name="decision_at")
    except ValueError:
        return False
    return True


def _eligibility(row: dict[str, Any]) -> tuple[str, str | None]:
    if row.get("replay_of") is not None:
        return "INELIGIBLE", "REPLAY_EXCLUDED"
    if not _valid_aware_timestamp(str(row.get("generated_at") or "")):
        return "INELIGIBLE", "INVALID_TIMESTAMP"
    if not row.get("run_id") or not row.get("evidence_set_id"):
        return "INELIGIBLE", "LEGACY_LINEAGE_MISSING"
    return "ELIGIBLE", None


def _manifest(card: DecisionCard, row: dict[str, Any]) -> dict[str, Any]:
    stances = {v.agent: v.stance for v in card.verdicts}
    observed = sorted(v.agent for v in card.verdicts)
    # Never infer a historical expected roster from today's Registry.  Old Cards that did
    # not freeze expected_roster keep it UNKNOWN; otherwise backfill date would change facts.
    frozen_roster = None if card.expected_roster is None else list(card.expected_roster)
    frozen_absent = (
        None if frozen_roster is None else sorted(set(frozen_roster) - set(observed))
    )
    return {
        "decision_record_id": int(row["record_id"]),
        "decision_id": card.decision_id,
        "replay_of": row.get("replay_of"),
        "stances": stances,
        "verdict_levels": {v.agent: v.verdict for v in card.verdicts},
        "verdict_statuses": {v.agent: v.status for v in card.verdicts},
        "missing_codes": sorted({m.code for m in card.missing}),
        "expected_roster": frozen_roster,
        "observed_roster": observed,
        "absent_agents": frozen_absent,
        "risk_veto": any(v.agent == "risk" and v.stance == VETO_STANCE for v in card.verdicts),
        "input_verdict_refs": [r.to_dict() for r in card.input_verdict_refs],
        "lineage": {
            "run_id": row.get("run_id"),
            "evidence_set_id": row.get("evidence_set_id"),
        },
        "warnings": {
            "identity": card.identity_warning or None,
            "restate": card.restate_warning or None,
            "roster": (card.roster_warning or None) if frozen_roster is not None else None,
            "provenance": card.provenance_warning or None,
        },
    }


def build_review_subject(
    decision_row: dict[str, Any],
    *,
    path: pathlib.Path | str | None = None,
    anchor_policy_id: str = ANCHOR_POLICY_ID_V1,
    target_policy_id: str = TARGET_POLICY_ID_V1,
) -> ReviewSubject:
    """Build an immutable projection from one exact ``decision_records`` row."""
    get_anchor_policy(anchor_policy_id)
    get_target_policy(target_policy_id)
    card_json = str(decision_row["card_json"])
    # Contract objects are loaded only through the persistence read boundary.  Business code
    # never decodes card_json itself; the exact text is retained solely for source hashing.
    card = load_card_by_record_id(int(decision_row["record_id"]), path=path)
    if card is None:
        raise ValueError(f"decision_record_id {decision_row['record_id']} disappeared during capture")
    eligibility, exclusion_code = _eligibility(decision_row)
    return ReviewSubject(
        review_subject_id=review_subject_id_for(
            int(decision_row["record_id"]),
            target_policy_id=target_policy_id,
            anchor_policy_id=anchor_policy_id,
        ),
        decision_record_id=int(decision_row["record_id"]),
        decision_id=str(decision_row["decision_id"]),
        subject_type="REPLAY_CARD" if decision_row.get("replay_of") is not None else "ONLINE_CARD",
        decision_at=(
            cn_iso(str(decision_row["generated_at"]), name="decision_at")
            if eligibility == "ELIGIBLE" else str(decision_row["generated_at"])
        ),
        run_id=decision_row.get("run_id"),
        evidence_set_id=decision_row.get("evidence_set_id"),
        card_status=str(decision_row["status"]),
        model_ref=str(decision_row["model_ref"]),
        target_policy_id=target_policy_id,
        anchor_policy_id=anchor_policy_id,
        manifest=_manifest(card, decision_row),
        source_card_sha256=_exact_text_sha256(card_json),
        eligibility=eligibility,
        exclusion_code=exclusion_code,
    )


def sync_review_subjects(
    *,
    path: pathlib.Path | str | None = None,
    limit: int | None = None,
    anchor_policy_id: str = ANCHOR_POLICY_ID_V1,
    target_policy_id: str = TARGET_POLICY_ID_V1,
    from_record_id: int | None = None,
    decision_from: str | None = None,
    decision_to: str | None = None,
) -> ReviewSubjectSyncResult:
    """Capture uncaptured Cards; safe to rerun and suitable for historical backfill.

    ⚠️ 「未捕获」相对于传进来的这对 policy —— 发布新 policy 之后重跑，
    会把全部历史卡在新 policy 下再冻结一份（旧的那份原样留着，不重算）。
    """
    rows = list_uncaptured_decision_records(
        path=path,
        limit=limit,
        target_policy_id=target_policy_id,
        anchor_policy_id=anchor_policy_id,
        from_record_id=from_record_id,
        decision_from=decision_from,
        decision_to=decision_to,
    )
    eligible = 0
    ineligible = 0
    for row in rows:
        subject = build_review_subject(
            row,
            path=path,
            anchor_policy_id=anchor_policy_id,
            target_policy_id=target_policy_id,
        )
        save_review_subject(subject, path=path)
        if subject.eligibility == "ELIGIBLE":
            eligible += 1
        else:
            ineligible += 1
    return ReviewSubjectSyncResult(
        scanned=len(rows), created=len(rows), eligible=eligible, ineligible=ineligible
    )
