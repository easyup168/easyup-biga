"""`bin/biga-review` — Phase 4 operations CLI.

Exit codes are intentionally three-state:
0 = command succeeded / conclusion generated;
1 = code, integrity, or runtime failure;
2 = NOT_READY / UNDERPOWERED / immature horizon / evidence not ready.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from easyup_biga.domain import now_cn
from easyup_biga.persistence.db import StoreNotInitialised
from easyup_biga.persistence.review import (
    Phase4SchemaNotReady,
    latest_outcome_set,
    latest_review_record,
    list_review_subjects,
    require_phase4_schema,
)

from .acceptance import (
    code_checks,
    record_regression_result,
    record_restore_replay_result,
    run_full_regression,
    verify_restored_measurement_run,
)
from .finalizer import evaluate_phase4_release, write_code_ready_marker
from .jobs import run_measurement_job, run_review_catchup, status_snapshot
from .outcomes import build_outcome
from .reports import latest_measurement_report, render_markdown
from .reviews import build_review
from .subjects import sync_review_subjects

PASS, FAIL, NOT_READY = 0, 1, 2


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _code_version() -> str:
    data = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _emit(value: Any, *, as_json: bool = False) -> None:
    if as_json:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif hasattr(value, "__dataclass_fields__"):
            value = asdict(value)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return
    if hasattr(value, "__dataclass_fields__"):
        for key, item in asdict(value).items():
            print(f"{key}: {item}")
    else:
        print(value)


def _aware_date(value: str, *, end: bool = False) -> str:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("date must be YYYYMMDD")
    stamp = f"{value[:4]}-{value[4:6]}-{value[6:]}T{'23:59:59' if end else '00:00:00'}+08:00"
    datetime.fromisoformat(stamp)
    return stamp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="biga-review")
    p.add_argument("--db", default="data/biga.db")
    p.add_argument("--data-root", default="data")
    p.add_argument("--report-dir", default="data/reports/phase4")
    p.add_argument("--ledger", default="data/phase4_acceptance.jsonl")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    subjects = sub.add_parser("subjects")
    subjects_sub = subjects.add_subparsers(dest="action", required=True)
    sync = subjects_sub.add_parser("sync")
    sync.add_argument("--from-record-id", type=int)
    sync.add_argument("--limit", type=int)

    outcomes = sub.add_parser("outcomes")
    outcomes_sub = outcomes.add_subparsers(dest="action", required=True)
    ob = outcomes_sub.add_parser("build")
    ob.add_argument("--pending", action="store_true", required=True)
    ob.add_argument("--as-of")

    reviews = sub.add_parser("reviews")
    reviews_sub = reviews.add_subparsers(dest="action", required=True)
    rb = reviews_sub.add_parser("build")
    rb.add_argument("--pending", action="store_true", required=True)

    measure = sub.add_parser("measure")
    measure_sub = measure.add_subparsers(dest="action", required=True)
    mr = measure_sub.add_parser("run")
    mr.add_argument("--policy", default="p4-market-signal-v1")
    mr.add_argument("--as-of")

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="action", required=True)
    latest = report_sub.add_parser("latest")
    latest.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    sub.add_parser("status")
    catchup = sub.add_parser("catchup")
    catchup.add_argument("--as-of")
    catchup.add_argument("--from-record-id", type=int)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("decision_id")
    inspect.add_argument("--record-id", type=int)

    backfill = sub.add_parser("backfill")
    backfill.add_argument("--from", dest="date_from", required=True)
    backfill.add_argument("--to", dest="date_to", required=True)
    backfill.add_argument("--offline", action="store_true", required=True)
    backfill.add_argument("--as-of")

    verify = sub.add_parser("verify")
    mode = verify.add_mutually_exclusive_group()
    mode.add_argument("--code-only", action="store_true")
    mode.add_argument("--live", action="store_true")
    verify.add_argument("--run-full-regression", action="store_true")
    verify.add_argument("--restored-db")
    verify.add_argument("--measurement-run-id")

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--write-marker")
    return p


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else _repo_root() / p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    db = _path(a.db)
    # Static code verification must work in a clean checkout before any DB exists.
    # Every command that reads/writes Phase-4 state still fails closed on schema < v31.
    code_only_without_db = (
        a.cmd == "verify" and bool(a.code_only) and not bool(a.restored_db)
    )
    if not code_only_without_db:
        try:
            require_phase4_schema(db)
        except (StoreNotInitialised, Phase4SchemaNotReady) as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return FAIL
    data_root = str(_path(a.data_root))
    report_dir = _path(a.report_dir)
    ledger = _path(a.ledger)
    try:
        if a.cmd == "subjects":
            got = sync_review_subjects(path=db, from_record_id=a.from_record_id, limit=a.limit)
            _emit(got, as_json=a.json); return PASS

        if a.cmd == "outcomes":
            cutoff = a.as_of or now_cn().isoformat()
            created = pending = quarantined = 0
            for subject in list_review_subjects(path=db, eligibility="ELIGIBLE"):
                got = build_outcome(str(subject["review_subject_id"]), cutoff, path=db, data_root=data_root)
                created += int(got.created)
                pending += int(got.status == "NOT_MATURE")
                quarantined += int(got.status == "QUARANTINED")
            payload = {"created": created, "not_ready": pending, "quarantined": quarantined, "as_of": cutoff}
            _emit(payload, as_json=a.json)
            return NOT_READY if (pending or quarantined) else PASS

        if a.cmd == "reviews":
            created = pending = 0
            for subject in list_review_subjects(path=db, eligibility="ELIGIBLE"):
                got = build_review(str(subject["review_subject_id"]), path=db)
                created += int(got.created)
                pending += int(got.review_status == "INSUFFICIENT_DATA")
            _emit({"created": created, "not_ready": pending}, as_json=a.json)
            return NOT_READY if pending else PASS

        if a.cmd == "catchup":
            cutoff = a.as_of or now_cn().isoformat()
            got = run_review_catchup(cutoff, path=db, data_root=data_root, from_record_id=a.from_record_id)
            _emit(got, as_json=a.json)
            return NOT_READY if (got.not_ready or got.quarantined) else PASS

        if a.cmd == "measure":
            cutoff = a.as_of or now_cn().isoformat()
            got = run_measurement_job(
                cutoff, path=db, code_version=_code_version(), output_dir=report_dir,
                measurement_policy_id=a.policy,
            )
            _emit(got, as_json=a.json)
            return NOT_READY if got.underpowered else PASS

        if a.cmd == "report":
            got = latest_measurement_report(path=db)
            if got is None:
                print("no MeasurementRun", file=sys.stderr); return NOT_READY
            if getattr(a, "json", False):
                _emit(got.to_dict(), as_json=True)
            else:
                print(render_markdown(got), end="")
            return PASS

        if a.cmd == "status":
            got = status_snapshot(path=db)
            _emit(got, as_json=a.json)
            return NOT_READY if (
                got.no_outcome or got.h5_pending or got.quarantined
                or not got.latest_measurement_run_id
            ) else PASS

        if a.cmd == "inspect":
            subjects = [s for s in list_review_subjects(path=db) if s["decision_id"] == a.decision_id]
            if a.record_id is not None:
                subjects = [s for s in subjects if int(s["decision_record_id"]) == a.record_id]
            if not subjects:
                print("ReviewSubject not found", file=sys.stderr); return NOT_READY
            rows = []
            for subject in subjects:
                sid = str(subject["review_subject_id"])
                rows.append({
                    "subject": subject,
                    "latest_outcome": latest_outcome_set(sid, "market-h1-h3-h5-h20-v1", path=db),
                    "latest_review": latest_review_record(sid, "directional-review-v1", path=db),
                })
            _emit(rows, as_json=True if a.json else False); return PASS

        if a.cmd == "backfill":
            start, end = _aware_date(a.date_from), _aware_date(a.date_to, end=True)
            cutoff = a.as_of or now_cn().isoformat()
            got = run_review_catchup(
                cutoff, path=db, data_root=data_root, decision_from=start, decision_to=end,
            )
            _emit(got, as_json=a.json)
            return NOT_READY if (got.not_ready or got.quarantined) else PASS

        if a.cmd == "verify":
            if a.run_full_regression:
                result = run_full_regression(_repo_root())
                record_regression_result(ledger, result)
                _emit(result, as_json=a.json)
                if not result.passed:
                    return FAIL
            if a.restored_db:
                report = latest_measurement_report(path=db)
                if report is None:
                    return NOT_READY
                run_id = a.measurement_run_id or report.measurement_run_id
                expected = report.report_sha256
                if run_id != report.measurement_run_id:
                    from .reports import build_measurement_report
                    expected = build_measurement_report(run_id, path=db).report_sha256
                replay = verify_restored_measurement_run(run_id, expected, restored_db=_path(a.restored_db))
                record_restore_replay_result(ledger, replay)
                _emit(replay, as_json=a.json)
                if not replay.passed:
                    return FAIL
            if a.code_only:
                checks, errors = code_checks(_repo_root())
                _emit({"checks": checks, "errors": errors}, as_json=a.json)
                return FAIL if errors else PASS
            status = evaluate_phase4_release(repo=_repo_root(), db_path=db, acceptance_ledger=ledger)
            _emit(status, as_json=a.json)
            if status.code_errors:
                return FAIL
            return PASS if status.evidence_ready else NOT_READY

        if a.cmd == "finalize":
            status = evaluate_phase4_release(repo=_repo_root(), db_path=db, acceptance_ledger=ledger)
            if a.write_marker:
                write_code_ready_marker(status, _path(a.write_marker))
            _emit(status, as_json=a.json)
            return PASS if status.code_ready else NOT_READY

        raise RuntimeError(f"unsupported command {a.cmd}")
    except (KeyError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return FAIL


if __name__ == "__main__":
    raise SystemExit(main())
