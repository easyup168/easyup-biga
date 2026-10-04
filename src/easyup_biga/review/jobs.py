"""P4-8 idempotent offline jobs for review catch-up and measurement reporting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from easyup_biga.data.replay import network_forbidden
from easyup_biga.domain import now_cn
from easyup_biga.timebase import parse_aware
from easyup_biga.persistence.review import (
    append_measurement_run_event,
    latest_outcome_set,
    list_measurement_run_subjects,
    list_review_subjects,
    load_measurement_run,
)

from .measurement import run_measurement
from .outcomes import build_outcome
from .reports import MeasurementReport, build_measurement_report, write_measurement_report
from .reviews import build_review
from .subjects import ReviewSubjectSyncResult, sync_review_subjects

__all__ = [
    "MeasurementJobResult",
    "ReviewCatchupResult",
    "ReviewStatusSnapshot",
    "run_measurement_job",
    "run_review_catchup",
    "status_snapshot",
]


@dataclass(frozen=True, slots=True)
class ReviewCatchupResult:
    subject_sync: ReviewSubjectSyncResult
    subjects_considered: int
    outcomes_created: int
    reviews_created: int
    not_ready: int
    quarantined: int


@dataclass(frozen=True, slots=True)
class MeasurementJobResult:
    measurement_run_id: str
    results: int
    included: int
    excluded: int
    underpowered: bool
    conclusion: str
    report_sha256: str
    json_path: str
    markdown_path: str


@dataclass(frozen=True, slots=True)
class ReviewStatusSnapshot:
    eligible_subjects: int
    ineligible_subjects: int
    no_outcome: int
    h5_pending: int
    quarantined: int
    complete_h20: int
    measurement_runs: int
    latest_measurement_run_id: str | None
    latest_measurement_state: str | None
    latest_conclusion: str | None


def _parse_aware(value: str) -> datetime:
    return parse_aware(value, name="as_of cutoff")


def run_review_catchup(
    as_of_cutoff: str,
    *,
    path=None,
    data_root: str = "data",
    from_record_id: int | None = None,
    decision_from: str | None = None,
    decision_to: str | None = None,
    limit: int | None = None,
) -> ReviewCatchupResult:
    """Offline catch-up: capture subjects -> mature outcomes -> build per-card reviews."""
    cutoff = _parse_aware(as_of_cutoff)
    with network_forbidden():
        synced = sync_review_subjects(
            path=path,
            limit=limit,
            from_record_id=from_record_id,
            decision_from=decision_from,
            decision_to=decision_to,
        )
        eligible = [
            row for row in list_review_subjects(path=path, eligibility="ELIGIBLE")
            if _parse_aware(str(row["decision_at"])) <= cutoff
        ]
        outcomes_created = reviews_created = not_ready = quarantined = 0
        for subject in eligible:
            sid = str(subject["review_subject_id"])
            outcome = build_outcome(sid, as_of_cutoff, path=path, data_root=data_root)
            if outcome.created:
                outcomes_created += 1
            if outcome.status in {"NOT_MATURE", "INELIGIBLE"}:
                not_ready += 1
            if outcome.status == "QUARANTINED":
                quarantined += 1
            if outcome.outcome_set_id is not None:
                review = build_review(sid, path=path, outcome_set_id=outcome.outcome_set_id)
                if review.created:
                    reviews_created += 1
        return ReviewCatchupResult(
            subject_sync=synced,
            subjects_considered=len(eligible),
            outcomes_created=outcomes_created,
            reviews_created=reviews_created,
            not_ready=not_ready,
            quarantined=quarantined,
        )


def _run_state(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    events = list(run.get("events") or ())
    return None if not events else str(events[-1]["to_state"])


def run_measurement_job(
    as_of_cutoff: str,
    *,
    path=None,
    code_version: str,
    output_dir: Path | str = "data/reports/phase4",
    measurement_policy_id: str = "p4-market-signal-v1",
    equity_ew_resolver=None,
) -> MeasurementJobResult:
    """Freeze/compute one MeasurementRun, write deterministic reports, then mark COMPLETE."""
    _parse_aware(as_of_cutoff)
    kwargs: dict[str, Any] = {
        "path": path,
        "code_version": code_version,
        "measurement_policy_id": measurement_policy_id,
    }
    if equity_ew_resolver is not None:
        kwargs["equity_ew_resolver"] = equity_ew_resolver
    execution = None
    try:
        with network_forbidden():
            execution = run_measurement(as_of_cutoff, **kwargs)
            report: MeasurementReport = build_measurement_report(execution.measurement_run_id, path=path)
            json_path, md_path = write_measurement_report(report, output_dir=output_dir)
        run = load_measurement_run(execution.measurement_run_id, path=path)
        state = _run_state(run)
        if state == "COMPUTED":
            append_measurement_run_event(
                execution.measurement_run_id,
                "REPORTED",
                detail={
                    "report_sha256": report.report_sha256,
                    "json_path": str(json_path),
                    "markdown_path": str(md_path),
                },
                path=path,
            )
            state = "REPORTED"
        if state == "REPORTED":
            append_measurement_run_event(
                execution.measurement_run_id,
                "COMPLETE",
                detail={"report_sha256": report.report_sha256},
                path=path,
            )
        return MeasurementJobResult(
            measurement_run_id=execution.measurement_run_id,
            results=execution.result_count,
            included=execution.included_days,
            excluded=execution.excluded_subjects,
            underpowered=execution.underpowered,
            conclusion=report.conclusion,
            report_sha256=report.report_sha256,
            json_path=str(json_path),
            markdown_path=str(md_path),
        )
    except Exception as exc:
        if execution is not None:
            try:
                run = load_measurement_run(execution.measurement_run_id, path=path)
                if _run_state(run) not in {"COMPLETE", "FAILED"}:
                    append_measurement_run_event(
                        execution.measurement_run_id,
                        "FAILED",
                        detail={"error_code": type(exc).__name__, "message": str(exc)},
                        path=path,
                    )
            except Exception:
                pass
        raise


def status_snapshot(*, path=None) -> ReviewStatusSnapshot:
    eligible = list_review_subjects(path=path, eligibility="ELIGIBLE")
    ineligible = list_review_subjects(path=path, eligibility="INELIGIBLE")
    no_outcome = h5_pending = quarantined = complete_h20 = 0
    for subject in eligible:
        latest = latest_outcome_set(
            str(subject["review_subject_id"]), "market-h1-h3-h5-h20-v1", path=path
        )
        if latest is None:
            no_outcome += 1
            continue
        if str(latest["status"]) == "QUARANTINED":
            quarantined += 1
            continue
        matured = set((latest.get("manifest") or {}).get("matured_horizons") or ())
        if "H5" not in matured:
            h5_pending += 1
        if "H20" in matured and str(latest["status"]) == "COMPLETE":
            complete_h20 += 1

    from easyup_biga.persistence.review import latest_measurement_run, list_measurement_runs
    from .reports import latest_measurement_report

    runs = list_measurement_runs(path=path)
    latest = latest_measurement_run(path=path)
    report = latest_measurement_report(path=path) if latest is not None else None
    return ReviewStatusSnapshot(
        eligible_subjects=len(eligible),
        ineligible_subjects=len(ineligible),
        no_outcome=no_outcome,
        h5_pending=h5_pending,
        quarantined=quarantined,
        complete_h20=complete_h20,
        measurement_runs=len(runs),
        latest_measurement_run_id=None if latest is None else str(latest["measurement_run_id"]),
        latest_measurement_state=_run_state(latest),
        latest_conclusion=None if report is None else report.conclusion,
    )
