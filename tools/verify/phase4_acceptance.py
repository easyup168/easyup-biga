#!/usr/bin/env python3
"""Phase 4 Review & Measurement gate.

Exit 0: requested gate passed.
Exit 1: code/integrity failure.
Exit 2: code shape is valid but evidence is not ready yet.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
VERIFY = pathlib.Path(__file__).resolve().parent
for item in (SRC, VERIFY):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

import _verdict as _v  # noqa: E402


def _emit(title: str, checks, errors) -> None:
    print(title)
    for item in checks:
        print(f"  ✅ {item}")
    for item in errors:
        print(f"  ❌ {item}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--db", default="data/biga.db")
    p.add_argument("--ledger", default="data/phase4_acceptance.jsonl")
    p.add_argument("--code-only", action="store_true")
    p.add_argument("--run-full-regression", action="store_true")
    p.add_argument("--restored-db")
    p.add_argument("--measurement-run-id")
    a = p.parse_args(argv)
    repo = pathlib.Path(a.repo).resolve()
    db = pathlib.Path(a.db); db = db if db.is_absolute() else repo / db
    ledger = pathlib.Path(a.ledger); ledger = ledger if ledger.is_absolute() else repo / ledger

    from easyup_biga.review.acceptance import (
        code_checks,
        record_regression_result,
        record_restore_replay_result,
        run_full_regression,
        verify_restored_measurement_run,
    )
    from easyup_biga.review.finalizer import evaluate_phase4_release
    from easyup_biga.review.reports import build_measurement_report, latest_measurement_report

    if a.run_full_regression:
        result = run_full_regression(repo)
        record_regression_result(ledger, result)
        if not result.passed:
            print(f"❌ full regression failed rc={result.returncode}")
            return _v.FAIL

    if a.restored_db:
        current = latest_measurement_report(path=db)
        if current is None:
            print("🔶 no MeasurementRun to compare")
            return _v.UNKNOWN
        run_id = a.measurement_run_id or current.measurement_run_id
        expected = build_measurement_report(run_id, path=db).report_sha256
        restored = pathlib.Path(a.restored_db)
        restored = restored if restored.is_absolute() else repo / restored
        result = verify_restored_measurement_run(run_id, expected, restored_db=restored)
        record_restore_replay_result(ledger, result)
        if not result.passed:
            print(f"❌ restore/replay failed: {result.error}")
            return _v.FAIL

    if a.code_only:
        checks, errors = code_checks(repo)
        _emit("✅ Phase 4 code-shape gate" if not errors else "❌ Phase 4 code-shape gate", checks, errors)
        print("  🔶 full regression / evidence readiness not evaluated")
        return _v.PASS if not errors else _v.FAIL

    # 🔴 evaluate_phase4_release 会查 Phase 4 的表。库没迁移时，不接这层
    #    就是一屏 `no such table` 的 traceback —— 那既不指路，也不是三态。
    from easyup_biga.persistence.db import StoreNotInitialised
    from easyup_biga.persistence.review import Phase4SchemaNotReady, require_phase4_schema
    try:
        require_phase4_schema(db)
    except (StoreNotInitialised, Phase4SchemaNotReady) as exc:
        print(f"❌ {type(exc).__name__}: {exc}")
        return _v.FAIL

    status = evaluate_phase4_release(repo=repo, db_path=db, acceptance_ledger=ledger)
    _emit(
        "✅ Phase 4 Code Ready" if status.code_ready else "❌ Phase 4 Code Ready not reached",
        status.checks,
        status.code_errors,
    )
    if status.evidence_ready:
        print(f"  ✅ Evidence Ready: {status.evidence_conclusion}")
    else:
        print("  🔶 Evidence not ready")
        for error in status.evidence_errors:
            print(f"     - {error}")
    if status.code_errors:
        return _v.FAIL
    return _v.PASS if status.evidence_ready else _v.UNKNOWN


if __name__ == "__main__":
    raise SystemExit(main())
