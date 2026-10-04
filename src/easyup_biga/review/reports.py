"""P4-8 deterministic MeasurementReport projection.

The report is a pure projection of a frozen MeasurementRun.  It never fetches data and it
never mutates statistical facts.  Human Markdown and machine JSON share one canonical payload
so a formatting change cannot silently change conclusion semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from easyup_biga.persistence.review import (
    latest_measurement_run,
    list_measurement_results,
    list_measurement_run_subjects,
    load_measurement_run,
    load_outcome_set,
)

from .contracts import canonical_json, content_sha256
from .registry import get_measurement_policy

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "MeasurementReport",
    "build_measurement_report",
    "latest_measurement_report",
    "render_markdown",
    "write_measurement_report",
]

REPORT_SCHEMA_VERSION = "phase4.measurement-report.v1"
_CONFIRMATORY_KEYS = (
    "P4-H01", "P4-H02:return", "P4-H02:mae", "P4-H03", "P4-H04", "P4-H05"
)
_EXPECTED_SIGN = {
    "P4-H01": 1,
    "P4-H02:return": -1,
    "P4-H02:mae": -1,
    "P4-H03": 1,
    "P4-H04": 1,
    "P4-H05": 1,
}


@dataclass(frozen=True, slots=True)
class MeasurementReport:
    measurement_run_id: str
    conclusion: str
    payload: Mapping[str, Any]
    report_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload) | {"report_sha256": self.report_sha256}


def _confirmatory_conclusion(results: list[dict[str, Any]], policy) -> tuple[str, list[str]]:
    by_key = {str(row["analysis_key"]): row for row in results}
    reasons: list[str] = []
    missing = [key for key in _CONFIRMATORY_KEYS if key not in by_key]
    if missing:
        return "INCONCLUSIVE", [f"MISSING_CONFIRMATORY:{key}" for key in missing]

    confirm = [by_key[key] for key in _CONFIRMATORY_KEYS]
    if any(bool(row.get("underpowered")) for row in confirm):
        return "INCONCLUSIVE", ["UNDERPOWERED_CONFIRMATORY"]
    if any(row.get("effect") is None for row in confirm):
        return "INCONCLUSIVE", ["MISSING_EFFECT"]

    alpha = 1.0 - float(policy.confidence_level)
    for row in confirm:
        key = str(row["analysis_key"])
        sign = _EXPECTED_SIGN[key]
        effect = float(row["effect"])
        ci_low = row.get("ci_low")
        ci_high = row.get("ci_high")
        corrected = (row.get("result") or {}).get("corrected_p_value")
        if sign * effect <= 0:
            reasons.append(f"DIRECTION_MISMATCH:{key}")
            continue
        if corrected is None or float(corrected) >= alpha:
            reasons.append(f"NOT_SIGNIFICANT:{key}")
            continue
        if ci_low is None or ci_high is None:
            reasons.append(f"MISSING_CI:{key}")
            continue
        if sign > 0 and float(ci_low) <= 0:
            reasons.append(f"CI_CROSSES_ZERO:{key}")
        if sign < 0 and float(ci_high) >= 0:
            reasons.append(f"CI_CROSSES_ZERO:{key}")

    # Lagged labels are the explicit pre-declared placebo.  A significant lagged placebo can
    # explain the same separation as a slow market-state restatement.
    for hyp in ("P4-H01", "P4-H03", "P4-H04", "P4-H05"):
        placebo = by_key.get(f"PLACEBO-LAG:{hyp}")
        if placebo is None:
            return "INCONCLUSIVE", reasons + [f"MISSING_PLACEBO:{hyp}"]
        if bool(placebo.get("underpowered")):
            return "INCONCLUSIVE", reasons + [f"UNDERPOWERED_PLACEBO:{hyp}"]
        corrected = (placebo.get("result") or {}).get("corrected_p_value")
        if corrected is not None and float(corrected) < alpha:
            reasons.append(f"LAGGED_PLACEBO_EXPLAINS:{hyp}")

    # Equity-EW is a robustness endpoint. Missing/underpowered robustness is uncertainty,
    # while an opposite mature effect is a cross-target conflict.
    robustness_uncertain = False
    for hyp in ("P4-H01", "P4-H04"):
        row = by_key.get(f"ROBUST-EQUITY-EW:{hyp}")
        if row is None or bool(row.get("underpowered")) or row.get("effect") is None:
            robustness_uncertain = True
            continue
        if float(row["effect"]) <= 0:
            return "INCONCLUSIVE", reasons + [f"TARGET_CONFLICT:{hyp}"]
    if robustness_uncertain:
        return "INCONCLUSIVE", reasons + ["ROBUSTNESS_COVERAGE_INSUFFICIENT"]

    if reasons:
        return "NOT_SUPPORTED", reasons
    return "SUPPORTED", ["ALL_CONFIRMATORY_RULES_MET"]


def build_measurement_report(
    measurement_run_id: str,
    *,
    path=None,
) -> MeasurementReport:
    run = load_measurement_run(measurement_run_id, path=path)
    if run is None:
        raise KeyError(f"MeasurementRun not found: {measurement_run_id}")
    results = list_measurement_results(measurement_run_id, path=path)
    links = list_measurement_run_subjects(measurement_run_id, path=path)
    policy = get_measurement_policy(str(run["measurement_policy_id"]))

    conclusion, reasons = _confirmatory_conclusion(results, policy)
    included = [row for row in links if bool(row["included"])]
    excluded = [row for row in links if not bool(row["included"])]
    exclusion_counts: dict[str, int] = {}
    for row in excluded:
        code = str(row.get("reason_code") or "UNKNOWN")
        exclusion_counts[code] = exclusion_counts.get(code, 0) + 1

    outcome_versions: list[dict[str, Any]] = []
    for outcome_id in sorted({str(row["outcome_set_id"]) for row in included if row.get("outcome_set_id")}):
        outcome = load_outcome_set(outcome_id, path=path)
        if outcome is None:
            raise RuntimeError(f"frozen report lineage disappeared: {outcome_id}")
        outcome_versions.append({
            "outcome_set_id": outcome_id,
            "data_version": int(outcome["data_version"]),
            "knowledge_cutoff": str(outcome["knowledge_cutoff"]),
            "content_sha256": str(outcome["content_sha256"]),
        })

    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "measurement_run_id": measurement_run_id,
        "measurement_policy_id": str(run["measurement_policy_id"]),
        "as_of_cutoff": str(run["as_of_cutoff"]),
        "code_version": str(run["code_version"]),
        "cohort_manifest_sha256": str(run["cohort_manifest_sha256"]),
        "conclusion": conclusion,
        "conclusion_reasons": reasons,
        "sample": {
            "subjects_total": len(links),
            "included": len(included),
            "excluded": len(excluded),
            "distinct_anchor_trade_dates": len({str(row["anchor_trade_date"]) for row in included}),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "min_days": int(policy.min_days),
            "min_group_days": int(policy.min_group_days),
        },
        "data_versions": outcome_versions,
        "results": results,
        "limitations": [
            "statistical support is not an instruction to trade",
            "DecisionCard remains human decision support; Phase 4 never auto-modifies the model",
        ],
    }
    return MeasurementReport(
        measurement_run_id=measurement_run_id,
        conclusion=conclusion,
        payload=payload,
        report_sha256=content_sha256(payload),
    )


def latest_measurement_report(*, path=None) -> MeasurementReport | None:
    run = latest_measurement_run(path=path)
    if run is None:
        return None
    return build_measurement_report(str(run["measurement_run_id"]), path=path)


def render_markdown(report: MeasurementReport) -> str:
    p = report.payload
    sample = p["sample"]
    lines = [
        f"# Phase 4 Measurement Report — {report.measurement_run_id}",
        "",
        f"- Conclusion: **{p['conclusion']}**",
        f"- As-of cutoff: `{p['as_of_cutoff']}`",
        f"- Code version: `{p['code_version']}`",
        f"- Cohort hash: `{p['cohort_manifest_sha256']}`",
        f"- Report hash: `{report.report_sha256}`",
        f"- Included days: **{sample['distinct_anchor_trade_dates']}** / subjects {sample['included']}",
        f"- Excluded subjects: **{sample['excluded']}**",
        "",
        "## Conclusion reasons",
    ]
    lines.extend(f"- {reason}" for reason in p["conclusion_reasons"])
    lines += ["", "## Confirmatory results", "", "| Analysis | Effect | 95% CI | corrected p | n | underpowered |", "|---|---:|---:|---:|---:|---|"]
    for row in p["results"]:
        if str(row["analysis_key"]) not in _CONFIRMATORY_KEYS:
            continue
        result = row.get("result") or {}
        ci = "—" if row.get("ci_low") is None else f"[{float(row['ci_low']):.6g}, {float(row['ci_high']):.6g}]"
        cp = result.get("corrected_p_value")
        n = "/".join(f"{k}:{v}" for k, v in row["n"].items())
        lines.append(
            f"| {row['analysis_key']} | {('—' if row.get('effect') is None else f'{float(row['effect']):.6g}')} | "
            f"{ci} | {('—' if cp is None else f'{float(cp):.6g}')} | {n} | {bool(row['underpowered'])} |"
        )
    lines += ["", "## Exclusions"]
    counts = sample["exclusion_counts"]
    lines.extend(["- none"] if not counts else [f"- {k}: {v}" for k, v in counts.items()])
    lines += ["", "## Interpretation boundary", "", "This report measures historical separation. It does not authorize automated trading or model self-modification.", ""]
    return "\n".join(lines)


def write_measurement_report(
    report: MeasurementReport,
    *,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{report.measurement_run_id}.json"
    md_path = root / f"{report.measurement_run_id}.md"
    json_path.write_text(canonical_json(report.to_dict()) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
