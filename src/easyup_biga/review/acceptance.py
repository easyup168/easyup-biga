"""P4-9 acceptance evidence and Code Ready / Evidence Ready separation.

Acceptance facts live in a hash-chained JSONL ledger. PASS entries cannot be manufactured by
an arbitrary status flag: regression and restore/replay PASS must come from verifier result
objects produced by this module.
"""
from __future__ import annotations

import ast
import importlib.metadata
import hashlib
import json
import os
import subprocess
import sys
import tomllib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from easyup_biga.data.finalizer import _code_checks as phase3_code_checks
from easyup_biga.data.replay import network_forbidden
from easyup_biga.domain import now_cn
from easyup_biga.persistence.review import (
    latest_measurement_run,
    list_measurement_results,
    list_measurement_run_subjects,
    load_outcome_set,
)
from easyup_biga.persistence.schema import SCHEMA_VERSION

from .measurement import expected_measurement_analysis_keys
from .metric_algorithms import get_metric_algorithm
from .registry import OUTCOME_POLICY_REGISTRY, get_measurement_policy
from .reports import build_measurement_report

__all__ = [
    "FULL_REGRESSION",
    "RESTORE_REPLAY",
    "Phase4AcceptanceEvent",
    "Phase4Readiness",
    "RegressionResult",
    "RestoreReplayResult",
    "code_checks",
    "current_source_evidence",
    "evaluate_evidence_ready",
    "load_acceptance_events",
    "record_regression_result",
    "record_restore_replay_result",
    "run_full_regression",
    "verify_restored_measurement_run",
]

FULL_REGRESSION = "FULL_REGRESSION"
RESTORE_REPLAY = "MEASUREMENT_RESTORE_REPLAY"
_EVENT_TYPES = frozenset({FULL_REGRESSION, RESTORE_REPLAY})
_CONFIRMATORY_KEYS = frozenset({
    "P4-H01", "P4-H02:return", "P4-H02:mae", "P4-H03", "P4-H04", "P4-H05"
})
_REQUIRED_BATCH5_MODULES = (
    "src/easyup_biga/review/reports.py",
    "src/easyup_biga/review/jobs.py",
    "src/easyup_biga/review/cli.py",
    "src/easyup_biga/review/acceptance.py",
    "src/easyup_biga/review/finalizer.py",
    "bin/biga-review",
    "deploy/openclaw/review-outcomes-biga.service",
    "deploy/openclaw/review-outcomes-biga.timer",
    "deploy/openclaw/measurement-biga.service",
    "deploy/openclaw/measurement-biga.timer",
)


@dataclass(frozen=True, slots=True)
class Phase4AcceptanceEvent:
    event_type: str
    status: str
    at: str
    detail: dict[str, Any]
    previous_hash: str | None
    event_hash: str


@dataclass(frozen=True, slots=True)
class RegressionResult:
    passed: bool
    returncode: int
    command: tuple[str, ...]
    stdout_sha256: str
    repo_root: str
    source_tree_sha256: str
    project_version: str
    schema_version: int
    python_version: str
    dependency_fingerprint: str
    test_selection_sha256: str
    git_commit: str


@dataclass(frozen=True, slots=True)
class RestoreReplayResult:
    passed: bool
    measurement_run_id: str
    expected_report_sha256: str
    actual_report_sha256: str | None
    restored_db: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Phase4Readiness:
    ready: bool
    measurement_run_id: str | None
    conclusion: str | None
    checks: tuple[str, ...]
    errors: tuple[str, ...]


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _payload(event_type: str, status: str, at: str, detail: dict[str, Any], previous_hash: str | None):
    return {
        "event_type": event_type,
        "status": status,
        "at": at,
        "detail": detail,
        "previous_hash": previous_hash,
    }


def load_acceptance_events(path: Path | str) -> list[Phase4AcceptanceEvent]:
    file = Path(path)
    if not file.exists():
        return []
    out: list[Phase4AcceptanceEvent] = []
    previous: str | None = None
    for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        payload = _payload(
            str(row["event_type"]), str(row["status"]), str(row["at"]),
            dict(row.get("detail") or {}),
            None if row.get("previous_hash") is None else str(row["previous_hash"]),
        )
        if payload["event_type"] not in _EVENT_TYPES:
            raise ValueError(f"Phase 4 acceptance ledger line {line_no}: unknown event")
        actual = _hash(payload)
        if str(row.get("event_hash") or "") != actual:
            raise ValueError(f"Phase 4 acceptance ledger line {line_no}: hash mismatch")
        if payload["previous_hash"] != previous:
            raise ValueError(f"Phase 4 acceptance ledger line {line_no}: chain broken")
        out.append(Phase4AcceptanceEvent(**payload, event_hash=actual))
        previous = actual
    return out


def _append(path: Path | str, event_type: str, passed: bool, detail: dict[str, Any]) -> Phase4AcceptanceEvent:
    file = Path(path)
    prior = load_acceptance_events(file)
    previous = prior[-1].event_hash if prior else None
    payload = _payload(event_type, "PASS" if passed else "FAIL", now_cn().isoformat(), detail, previous)
    row = dict(payload, event_hash=_hash(payload))
    file.parent.mkdir(parents=True, exist_ok=True)
    existing = file.read_bytes() if file.exists() else b""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(existing + _canonical(row) + b"\n")
            fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, file)
    finally:
        tmp.unlink(missing_ok=True)
    return Phase4AcceptanceEvent(**payload, event_hash=row["event_hash"])


_SOURCE_DIRS = ("src", "tests", "tools", "bin", "deploy", "agents", "skills", ".github", "docs")
_SOURCE_TOP_FILES = (
    "pyproject.toml", "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md",
    "CONTRIBUTING.md", "TODO.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
)


def _source_tree_sha256(root: Path) -> str:
    files: list[Path] = []
    for dirname in _SOURCE_DIRS:
        base = root / dirname
        if not base.exists():
            continue
        files.extend(
            item for item in base.rglob("*")
            if item.is_file()
            and "__pycache__" not in item.parts
            and ".pytest_cache" not in item.parts
            and item.suffix not in {".pyc", ".pyo"}
            and not item.name.endswith(".lock")
        )
    files.extend(root / name for name in _SOURCE_TOP_FILES if (root / name).is_file())
    digest = hashlib.sha256()
    for item in sorted(set(files), key=lambda x: x.relative_to(root).as_posix()):
        rel = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big")); digest.update(rel)
        blob = item.read_bytes()
        digest.update(len(blob).to_bytes(8, "big")); digest.update(blob)
    return digest.hexdigest()


def _project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _dependency_fingerprint(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = tuple(str(x) for x in data.get("project", {}).get("dependencies", ()))
    installed: dict[str, str] = {}
    for spec in declared:
        name = spec.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].strip()
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = "MISSING"
    payload = json.dumps(
        {"declared": declared, "installed": installed},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_commit(root: Path) -> str:
    if not (root / ".git").exists():
        return "UNAVAILABLE"
    got = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return got.stdout.strip() if got.returncode == 0 and got.stdout.strip() else "UNAVAILABLE"


def current_source_evidence(
    repo: Path | str,
    *,
    command: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    root = Path(repo).resolve()
    cmd = command or (sys.executable, "-m", "pytest", "-q")
    selection = json.dumps(list(cmd), ensure_ascii=False, separators=(",", ":"))
    return {
        "repo_root": str(root),
        "source_tree_sha256": _source_tree_sha256(root),
        "project_version": _project_version(root),
        "schema_version": int(SCHEMA_VERSION),
        "python_version": sys.version,
        "dependency_fingerprint": _dependency_fingerprint(root),
        "test_selection_sha256": hashlib.sha256(selection.encode("utf-8")).hexdigest(),
        "git_commit": _git_commit(root),
    }


def run_full_regression(
    repo: Path | str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> RegressionResult:
    root = Path(repo).resolve()
    cmd = (sys.executable, "-m", "pytest", "-q")
    got = runner(list(cmd), cwd=root, capture_output=True, text=True)
    text = (got.stdout or "") + "\n" + (got.stderr or "")
    evidence = current_source_evidence(root, command=cmd)
    return RegressionResult(
        passed=got.returncode == 0,
        returncode=int(got.returncode),
        command=cmd,
        stdout_sha256=hashlib.sha256(text.encode()).hexdigest(),
        **evidence,
    )


def record_regression_result(path: Path | str, result: RegressionResult) -> Phase4AcceptanceEvent:
    if not isinstance(result, RegressionResult):
        raise TypeError("FULL_REGRESSION PASS requires a RegressionResult produced by the verifier")
    if len(result.stdout_sha256) != 64 or len(result.source_tree_sha256) != 64:
        raise ValueError("regression proof contains an invalid sha256")
    current = current_source_evidence(result.repo_root, command=result.command)
    for key in (
        "source_tree_sha256", "project_version", "schema_version", "python_version",
        "dependency_fingerprint", "test_selection_sha256", "git_commit",
    ):
        if getattr(result, key) != current[key]:
            raise ValueError(f"regression proof no longer matches current source/environment: {key}")
    return _append(path, FULL_REGRESSION, result.passed, {
        "returncode": result.returncode,
        "command": list(result.command),
        "stdout_sha256": result.stdout_sha256,
        "repo_root": result.repo_root,
        "source_tree_sha256": result.source_tree_sha256,
        "project_version": result.project_version,
        "schema_version": result.schema_version,
        "python_version": result.python_version,
        "dependency_fingerprint": result.dependency_fingerprint,
        "test_selection_sha256": result.test_selection_sha256,
        "git_commit": result.git_commit,
        "verified_by": "easyup_biga.review.acceptance.run_full_regression",
    })


def verify_restored_measurement_run(
    measurement_run_id: str,
    expected_report_sha256: str,
    *,
    restored_db: Path | str,
) -> RestoreReplayResult:
    db = Path(restored_db).resolve()
    try:
        with network_forbidden():
            report = build_measurement_report(measurement_run_id, path=db)
        actual = report.report_sha256
        return RestoreReplayResult(
            passed=actual == expected_report_sha256,
            measurement_run_id=measurement_run_id,
            expected_report_sha256=expected_report_sha256,
            actual_report_sha256=actual,
            restored_db=str(db),
            error=None if actual == expected_report_sha256 else "REPORT_HASH_MISMATCH",
        )
    except Exception as exc:
        return RestoreReplayResult(
            passed=False, measurement_run_id=measurement_run_id,
            expected_report_sha256=expected_report_sha256, actual_report_sha256=None,
            restored_db=str(db), error=f"{type(exc).__name__}: {exc}",
        )


def record_restore_replay_result(path: Path | str, result: RestoreReplayResult) -> Phase4AcceptanceEvent:
    if not isinstance(result, RestoreReplayResult):
        raise TypeError("restore/replay PASS requires RestoreReplayResult from the verifier")
    return _append(path, RESTORE_REPLAY, result.passed, {
        "measurement_run_id": result.measurement_run_id,
        "expected_report_sha256": result.expected_report_sha256,
        "actual_report_sha256": result.actual_report_sha256,
        "restored_db": result.restored_db,
        "error": result.error,
        "verified_by": "easyup_biga.review.acceptance.verify_restored_measurement_run",
    })


def _review_import_violations(repo: Path) -> list[str]:
    errors: list[str] = []
    root = repo / "src/easyup_biga/review"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "sqlite3" or name.startswith("easyup_biga.providers"):
                    errors.append(f"{path.relative_to(repo)} imports forbidden {name}")
    return errors


def code_checks(repo: Path | str) -> tuple[list[str], list[str]]:
    root = Path(repo).resolve()
    checks, errors = phase3_code_checks(root)
    if SCHEMA_VERSION != 31:
        errors.append(f"Phase 4 schema must be v31, got v{SCHEMA_VERSION}")
    else:
        checks.append("Phase 4 schema v31")
    missing = [rel for rel in _REQUIRED_BATCH5_MODULES if not (root / rel).exists()]
    if missing:
        errors.append(f"P4-8/P4-9 modules missing: {missing}")
    else:
        checks.append(f"P4-8/P4-9 modules present: {len(_REQUIRED_BATCH5_MODULES)}")
    violations = _review_import_violations(root)
    if violations:
        errors.extend(violations)
    else:
        checks.append("review/ has no direct Provider/sqlite3 dependency")

    for policy_id, policy in OUTCOME_POLICY_REGISTRY.items():
        try:
            algorithm = get_metric_algorithm(policy.metric_algorithm_id)
        except KeyError as exc:
            errors.append(f"{policy_id}: {exc}")
            continue
        if policy.metric_algorithm_ref() != algorithm.manifest_ref():
            errors.append(f"{policy_id}: Outcome formula fingerprint drift")
        else:
            checks.append(
                f"{policy_id}: formula guard {policy.metric_algorithm_id} "
                f"{policy.metric_implementation_sha256[:12]}"
            )

    for name in ("review-outcomes-biga.service", "review-outcomes-biga.timer",
                 "measurement-biga.service", "measurement-biga.timer"):
        if not (name.endswith("-biga.service") or name.endswith("-biga.timer")):
            errors.append(f"invalid BigA systemd unit name: {name}")
    services = [root / "deploy/openclaw/review-outcomes-biga.service", root / "deploy/openclaw/measurement-biga.service"]
    for service in services:
        if service.exists():
            text = service.read_text(encoding="utf-8")
            if "WorkingDirectory=%h/.openclaw-biga/workspace" not in text:
                errors.append(f"{service.name} WorkingDirectory drift")
            if "SuccessExitStatus=2" not in text:
                errors.append(f"{service.name} must accept exit 2 as NOT_READY/UNDERPOWERED")
            if "bin/biga-review" not in text or " python " in text:
                errors.append(f"{service.name} must use BigA review CLI / python3 boundary")
    installer = root / "deploy/openclaw/install_review_timers.py"
    if installer.exists() and "--apply" not in installer.read_text(encoding="utf-8"):
        errors.append("review timer installer is not default dry-run")
    else:
        checks.append("Phase 4 systemd isolation/dry-run shape present")
    return checks, list(dict.fromkeys(errors))


def evaluate_evidence_ready(
    *,
    path=None,
    acceptance_ledger: Path | str = "data/phase4_acceptance.jsonl",
) -> Phase4Readiness:
    checks: list[str] = []
    errors: list[str] = []
    run = latest_measurement_run(path=path)
    if run is None:
        return Phase4Readiness(False, None, None, (), ("no MeasurementRun",))
    run_id = str(run["measurement_run_id"])
    state = str((run.get("events") or [{}])[-1].get("to_state") or "")
    if state != "COMPLETE":
        errors.append(f"latest MeasurementRun is {state}, not COMPLETE")
    else:
        checks.append("latest MeasurementRun COMPLETE")

    report = build_measurement_report(run_id, path=path)
    policy = get_measurement_policy(str(run["measurement_policy_id"]))
    links = list_measurement_run_subjects(run_id, path=path)
    included = [row for row in links if bool(row["included"])]
    days = {str(row["anchor_trade_date"]) for row in included}
    if len(days) < policy.min_days:
        errors.append(f"distinct anchor days {len(days)} < {policy.min_days}")
    else:
        checks.append(f"distinct anchor days >= {policy.min_days}")

    results = list_measurement_results(run_id, path=path)
    by_key = {str(row["analysis_key"]): row for row in results}
    missing = sorted(_CONFIRMATORY_KEYS - set(by_key))
    if missing:
        errors.append(f"missing confirmatory results: {missing}")
    for key in sorted(_CONFIRMATORY_KEYS & set(by_key)):
        row = by_key[key]
        if bool(row["underpowered"]):
            errors.append(f"{key} UNDERPOWERED")
        if any(int(v) < policy.min_group_days for v in row["n"].values()):
            errors.append(f"{key} group size below {policy.min_group_days}")
    required_keys = expected_measurement_analysis_keys(str(run["measurement_policy_id"]))
    missing_required = sorted(required_keys - set(by_key))
    if missing_required:
        errors.append(f"missing required measurement results: {missing_required}")
    else:
        checks.append("complete predeclared measurement result set")

    for link in included:
        outcome = load_outcome_set(str(link["outcome_set_id"]), path=path)
        metrics = [m for m in (outcome or {}).get("metrics") or ()
                   if m["target_id"] == "market.index_blend.v1" and m["horizon"] == "H5"]
        if not metrics or any(float(m["coverage_ratio"]) < 1.0 for m in metrics):
            errors.append(f"H5 primary coverage incomplete: {link['review_subject_id']}")
            break
    else:
        checks.append("primary H5 outcome coverage complete")

    events = load_acceptance_events(acceptance_ledger)
    replay_ok = any(
        e.event_type == RESTORE_REPLAY and e.status == "PASS"
        and e.detail.get("measurement_run_id") == run_id
        and e.detail.get("expected_report_sha256") == report.report_sha256
        and e.detail.get("actual_report_sha256") == report.report_sha256
        for e in events
    )
    if not replay_ok:
        errors.append("missing verified restore/replay for latest MeasurementRun")
    else:
        checks.append("restore/replay reproduced report hash")

    return Phase4Readiness(
        ready=not errors,
        measurement_run_id=run_id,
        conclusion=report.conclusion,
        checks=tuple(checks),
        errors=tuple(dict.fromkeys(errors)),
    )
