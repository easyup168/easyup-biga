#!/usr/bin/env python3
"""Audited first production catch-up for Phase 4.

The tool is intentionally fail-closed.  It requires an explicit Beijing-time cutoff, proves
that no Outcome/Review/Measurement rows exist yet, creates a consistent VACUUM-INTO backup,
captures ReviewSubjects, runs the offline catch-up, and writes machine/human audit reports.

Use ``--apply`` to modify the database.  Without it the command performs preflight only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from easyup_biga.persistence import connect  # noqa: E402
from easyup_biga.persistence.review import require_phase4_schema  # noqa: E402
from easyup_biga.review.jobs import run_review_catchup, status_snapshot  # noqa: E402
from easyup_biga.review.registry import get_outcome_policy  # noqa: E402
from easyup_biga.review.subjects import sync_review_subjects  # noqa: E402
from easyup_biga.timebase import cn_iso  # noqa: E402

ZERO_REQUIRED = ("outcome_sets", "outcome_metrics", "review_records", "measurement_runs")
COUNT_TABLES = (
    "decision_records",
    "review_subjects",
    "outcome_sets",
    "outcome_metrics",
    "review_records",
    "measurement_runs",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _counts(db: Path) -> dict[str, int]:
    with connect(db, readonly=True) as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in COUNT_TABLES
        }


def _subject_breakdown(db: Path) -> dict[str, Any]:
    with connect(db, readonly=True) as conn:
        rows = conn.execute(
            "SELECT eligibility,COALESCE(exclusion_code,'ELIGIBLE') AS reason,COUNT(*) AS n "
            "FROM review_subjects GROUP BY eligibility,COALESCE(exclusion_code,'ELIGIBLE') "
            "ORDER BY eligibility,reason"
        ).fetchall()
        distinct_anchor_days = int(
            conn.execute(
                "SELECT COUNT(DISTINCT anchor_trade_date) FROM outcome_sets "
                "WHERE status IN ('PARTIAL','COMPLETE')"
            ).fetchone()[0]
        )
    reasons = {str(row["reason"]): int(row["n"]) for row in rows}
    return {
        "reasons": reasons,
        "total": sum(reasons.values()),
        "eligible": reasons.get("ELIGIBLE", 0),
        "ineligible": sum(v for k, v in reasons.items() if k != "ELIGIBLE"),
        "distinct_outcome_anchor_days": distinct_anchor_days,
    }


def _consistent_backup(db: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    with connect(db) as conn:
        conn.execute("VACUUM INTO ?", (str(dst),))


def _markdown(report: dict[str, Any]) -> str:
    pre = report["preflight_counts"]
    post = report.get("post_counts") or {}
    breakdown = report.get("subject_breakdown") or {}
    lines = [
        "# Phase 4 First Catch-up Audit",
        "",
        f"- Status: **{report['status']}**",
        f"- Database: `{report['database']}`",
        f"- As-of (Asia/Shanghai): `{report['as_of_cutoff']}`",
        f"- Source tree SHA-256: `{report['source_tree_sha256']}`",
        f"- Outcome policy: `{report['outcome_policy_id']}`",
        f"- Metric algorithm: `{report['metric_algorithm']['algorithm_id']}`",
        f"- Formula SHA-256: `{report['metric_algorithm']['formula_sha256']}`",
        f"- Implementation SHA-256: `{report['metric_algorithm']['implementation_sha256']}`",
        "",
        "## Preflight counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in pre.items())
    if report.get("backup"):
        lines += ["", "## Consistent pre-catchup backup", "", f"- Path: `{report['backup']}`", f"- SHA-256: `{report['backup_sha256']}`"]
    if report.get("subject_sync"):
        lines += ["", "## Initial ReviewSubject sync", ""]
        lines.extend(f"- {key}: {value}" for key, value in report["subject_sync"].items())
    if breakdown:
        lines += ["", "## ReviewSubject classification", ""]
        lines.extend(f"- {key}: {value}" for key, value in sorted(breakdown.get("reasons", {}).items()))
        lines.append(f"- TOTAL: {breakdown.get('total', 0)}")
    if report.get("catchup_result"):
        lines += ["", "## Catch-up result", ""]
        lines.extend(f"- {key}: {value}" for key, value in report["catchup_result"].items())
    if post:
        lines += ["", "## Post counts", ""]
        lines.extend(f"- {key}: {value}" for key, value in post.items())
    if report.get("review_status"):
        lines += ["", "## Review status", ""]
        lines.extend(f"- {key}: {value}" for key, value in report["review_status"].items())
    if report.get("error"):
        lines += ["", "## Error", "", f"`{report['error']}`"]
    lines.append("")
    return "\n".join(lines)


def run(*, db: Path, data_root: Path, as_of: str, output_dir: Path, apply: bool) -> dict[str, Any]:
    if not db.exists():
        raise FileNotFoundError(f"production database not found: {db}")
    require_phase4_schema(db)
    cutoff = cn_iso(as_of, name="first catchup as_of")
    policy = get_outcome_policy("market-h1-h3-h5-h20-v1")
    pre = _counts(db)
    nonzero = {table: pre[table] for table in ZERO_REQUIRED if pre[table] != 0}
    if nonzero:
        raise RuntimeError(
            "first catchup requires zero pre-existing Outcome/Review/Measurement rows: "
            + json.dumps(nonzero, sort_keys=True)
        )
    report: dict[str, Any] = {
        "status": "PREFLIGHT_OK" if not apply else "RUNNING",
        "database": str(db.resolve()),
        "data_root": str(data_root.resolve()),
        "as_of_cutoff": cutoff,
        "source_tree_sha256": _source_tree_sha256(REPO),
        "outcome_policy_id": policy.policy_id,
        "metric_algorithm": policy.metric_algorithm_ref(),
        "preflight_counts": pre,
        "apply": bool(apply),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    if not apply:
        return report

    backup = output_dir / "pre-catchup-v31.sqlite3"
    _consistent_backup(db, backup)
    report["backup"] = str(backup.resolve())
    report["backup_sha256"] = _sha256(backup)

    sync = sync_review_subjects(path=db)
    report["subject_sync"] = asdict(sync)
    report["subject_breakdown"] = _subject_breakdown(db)
    catchup = run_review_catchup(cutoff, path=db, data_root=str(data_root))
    report["catchup_result"] = {
        **asdict(catchup),
        "subject_sync": asdict(catchup.subject_sync),
    }
    report["post_counts"] = _counts(db)
    report["subject_breakdown"] = _subject_breakdown(db)
    report["review_status"] = asdict(status_snapshot(path=db))
    report["status"] = "COMPLETED_NOT_READY" if (catchup.not_ready or catchup.quarantined) else "COMPLETED"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--as-of", required=True, help="aware timestamp; persisted as Asia/Shanghai")
    parser.add_argument("--output-dir", default="data/audit/phase4-first-catchup")
    parser.add_argument("--apply", action="store_true", help="create backup and execute catchup")
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    try:
        report = run(
            db=Path(args.db),
            data_root=Path(args.data_root),
            as_of=args.as_of,
            output_dir=output_dir,
            apply=bool(args.apply),
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "first-catchup-audit.json"
        md_path = output_dir / "first-catchup-audit.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        md_path.write_text(_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        if report["status"] == "COMPLETED_NOT_READY":
            return 2
        return 0
    except Exception as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        report = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        (output_dir / "first-catchup-audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_dir / "first-catchup-audit.md").write_text(_markdown({
            **report,
            "database": str(Path(args.db)),
            "as_of_cutoff": args.as_of,
            "source_tree_sha256": "UNKNOWN",
            "outcome_policy_id": "UNKNOWN",
            "metric_algorithm": {"algorithm_id": "UNKNOWN", "formula_sha256": "UNKNOWN", "implementation_sha256": "UNKNOWN"},
            "preflight_counts": {},
        }), encoding="utf-8")
        print(report["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
