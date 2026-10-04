"""P4-6/P4-7 deterministic Measurement engine.

The engine freezes one canonical ONLINE_CARD per anchor day, binds exact OutcomeSet ids,
then runs pre-registered comparisons, day-level permutation/bootstrap, placebo and robustness.
It never calls a Provider and never mutates Card/Outcome/Review history.
"""
from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from easyup_biga.domain import STANCE_VOCAB
from easyup_biga.timebase import cn_iso, parse_aware
from easyup_biga.persistence.review import (
    append_measurement_run_event,
    create_measurement_run,
    latest_outcome_set_as_of,
    list_measurement_results,
    list_measurement_run_subjects,
    list_review_subjects,
    load_measurement_run,
    load_outcome_set,
    load_review_subject,
    retry_failed_measurement_run,
    save_measurement_results,
)

from .contracts import (
    MeasurementResult,
    MeasurementRun,
    content_sha256,
    measurement_cohort_sha256,
    measurement_result_payload,
    measurement_run_id_for,
)
from .placebo import bh_fdr_adjust, bootstrap_effect_ci, holm_adjust, lagged_labels, permutation_test
from .registry import get_hypothesis, get_measurement_policy
from .robustness import EquityEWResult, resolve_equity_ew_h5

__all__ = [
    "FrozenCohort",
    "MeasurementExecution",
    "expected_measurement_analysis_keys",
    "freeze_measurement_cohort",
    "measurement_run_underpowered",
    "run_measurement",
]

_OUTCOME_POLICY_ID = "market-h1-h3-h5-h20-v1"
_PRIMARY_TARGET = "market.index_blend.v1"
_PRIMARY_HORIZON = "H5"
_CONFIRMATORY_KEYS = frozenset({
    "P4-H01", "P4-H02:return", "P4-H02:mae", "P4-H03", "P4-H04", "P4-H05"
})
_LAGGED_PLACEBO_KEYS = frozenset({
    "PLACEBO-LAG:P4-H01", "PLACEBO-LAG:P4-H03",
    "PLACEBO-LAG:P4-H04", "PLACEBO-LAG:P4-H05",
})
_REGIME_KEYS = frozenset(
    f"ROBUST-REGIME:{hyp}:{regime}"
    for hyp in ("P4-H01", "P4-H04")
    for regime in ("H1_UP", "H1_NONPOS")
)
_EQUITY_EW_KEYS = frozenset({"ROBUST-EQUITY-EW:P4-H01", "ROBUST-EQUITY-EW:P4-H04"})
_BASELINE_KEYS = frozenset({"BASELINE:H5:return"})


def expected_measurement_analysis_keys(
    measurement_policy_id: str = "p4-market-signal-v1",
) -> frozenset[str]:
    """Exact non-exploratory result family frozen by the selected MeasurementPolicy."""
    policy = get_measurement_policy(measurement_policy_id)
    return frozenset(policy.required_analysis_keys)


_GENERATED_REQUIRED_KEYS = (
    _BASELINE_KEYS | _CONFIRMATORY_KEYS | _LAGGED_PLACEBO_KEYS | _REGIME_KEYS | _EQUITY_EW_KEYS
)
if expected_measurement_analysis_keys() != _GENERATED_REQUIRED_KEYS:
    raise RuntimeError(
        "MeasurementPolicy required_analysis_keys disagrees with the implemented v1 result family"
    )


def measurement_run_underpowered(results: Iterable[Any], policy, included_days: int) -> bool:
    """One operational underpowered rule used by both first execution and idempotent reuse.

    Confirmatory inference controls the run status.  Exploratory/robustness limitations stay
    visible in their own result rows but do not change the same run's exit code on retry.
    """
    rows: dict[str, Any] = {}
    for item in results:
        if isinstance(item, Mapping):
            key = item.get("analysis_key")
        else:
            key = getattr(item, "analysis_key", None)
        if not key:
            raise ValueError("measurement result is missing analysis_key")
        rows[str(key)] = item
    if included_days < policy.min_days or not _CONFIRMATORY_KEYS.issubset(rows):
        return True
    for key in _CONFIRMATORY_KEYS:
        item = rows[key]
        if isinstance(item, Mapping):
            value = bool(item.get("underpowered"))
        else:
            value = bool(getattr(item, "underpowered"))
        if value:
            return True
    return False
#: 预注册假设用到的 stance 分组。
#:
#: 🔴 **这些字面量是 `STANCE_VOCAB` 的孪生，必须在导入期对账。**
#:    坏掉时的症状极其安静：某个 stance 被改名之后，`_label()` 对所有卡返回
#:    `None` ⇒ 两个比较组**双双为空** ⇒ `_result()` 老老实实给出
#:    `effect=None, underpowered=True`，报告上看起来像「样本不够」，
#:    而真实原因是「词表对不上了」。这两件事的下一步完全不同。
#:
#: ⚠️ 只查**子集**关系，不查相等：`分化` / `无法判定` 本来就不属于任何一组，
#:    将来新增一个 stance 也应当默认落在组外（那是保守的）。
#:    这里要挡的是**改名与删除**。
_MARKET_POSITIVE = frozenset({"放量上涨", "缩量上涨"})
_MARKET_NEGATIVE = frozenset({"缩量调整", "放量下跌"})
_TECHNICAL_BULLISH = "多头"
_TECHNICAL_BEARISH = "空头"
_SECTOR_MAINLINE = "主线明确"
_SECTOR_BROAD_WEAK = "普跌无主线"

_STANCE_GROUPS: dict[str, frozenset[str]] = {
    "market": _MARKET_POSITIVE | _MARKET_NEGATIVE,
    "technical": frozenset({_TECHNICAL_BULLISH, _TECHNICAL_BEARISH}),
    "sector": frozenset({_SECTOR_MAINLINE, _SECTOR_BROAD_WEAK}),
}
for _agent, _group in _STANCE_GROUPS.items():
    _unknown = _group - set(STANCE_VOCAB[_agent])
    if _unknown:
        raise RuntimeError(
            f"预注册假设引用了 {_agent} 不存在的 stance：{sorted(_unknown)}。\n"
            f"  {_agent} 当前词表：{sorted(STANCE_VOCAB[_agent])}\n"
            "  ⇒ stance 被改名或删除了。改这里的分组常量**不是**唯一要做的事：\n"
            "     历史 ReviewSubject 里冻结的是旧词，换词表等于换了比较口径，\n"
            "     应当发新的 MeasurementPolicy id，而不是让旧 run 悄悄换含义。"
        )


@dataclass(frozen=True, slots=True)
class FrozenCohort:
    run: MeasurementRun
    entries: tuple[Mapping[str, Any], ...]
    created: bool


@dataclass(frozen=True, slots=True)
class MeasurementExecution:
    measurement_run_id: str
    created: bool
    result_count: int
    included_days: int
    excluded_subjects: int
    underpowered: bool


def _aware(value: str, name: str) -> datetime:
    return parse_aware(value, name=name)


def _h5_metric(outcome: Mapping[str, Any], metric_name: str) -> float | None:
    for metric in outcome.get("metrics") or ():
        if (
            str(metric.get("target_id")) == _PRIMARY_TARGET
            and str(metric.get("horizon")) == _PRIMARY_HORIZON
            and str(metric.get("metric_name")) == metric_name
        ):
            return float(metric["value"])
    return None


def _entry(subject: Mapping[str, Any], *, outcome=None, included=False, reason=None) -> dict[str, Any]:
    return {
        "review_subject_id": str(subject["review_subject_id"]),
        "outcome_set_id": None if outcome is None else str(outcome["outcome_set_id"]),
        "anchor_trade_date": None if outcome is None else str(outcome["anchor_trade_date"]),
        "analysis_group": str(subject.get("card_status") or "UNKNOWN"),
        "included": bool(included),
        "reason_code": reason,
    }


def freeze_measurement_cohort(
    as_of_cutoff: str,
    *,
    path=None,
    code_version: str,
    measurement_policy_id: str = "p4-market-signal-v1",
) -> FrozenCohort:
    """Freeze canonical day-level cohort and exact OutcomeSet ids at an explicit cutoff."""
    as_of_cutoff = cn_iso(as_of_cutoff, name="as_of_cutoff")
    cutoff = _aware(as_of_cutoff, "as_of_cutoff")
    policy = get_measurement_policy(measurement_policy_id)
    subjects = list_review_subjects(path=path)
    provisional: list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    entries: list[dict[str, Any]] = []

    for subject in subjects:
        # Historical as-of replay must be stable even after genuinely future Cards arrive.
        # Parse/order time before type/eligibility filtering so future replay/legacy rows do
        # not change an already reproducible historical cohort hash.
        try:
            decision_at = _aware(str(subject.get("decision_at") or ""), "decision_at")
        except ValueError:
            entries.append(_entry(subject, reason="INVALID_TIMESTAMP"))
            continue
        if decision_at > cutoff:
            continue
        if str(subject.get("subject_type")) != policy.canonical_subject_type:
            entries.append(_entry(subject, reason="REPLAY_EXCLUDED"))
            continue
        if (
            str(subject.get("anchor_policy_id")) != policy.subject_anchor_policy_id
            or str(subject.get("target_policy_id")) != policy.subject_target_policy_id
        ):
            entries.append(_entry(subject, reason="SUBJECT_POLICY_MISMATCH"))
            continue
        if str(subject.get("eligibility")) != "ELIGIBLE":
            entries.append(_entry(subject, reason=str(subject.get("exclusion_code") or "SUBJECT_INELIGIBLE")))
            continue
        outcome = latest_outcome_set_as_of(
            str(subject["review_subject_id"]), policy.outcome_policy_id, as_of_cutoff, path=path
        )
        if outcome is None:
            entries.append(_entry(subject, reason="NO_COMPLETE_SNAPSHOT"))
            continue
        if str(outcome.get("status")) == "QUARANTINED":
            entries.append(_entry(subject, outcome=outcome, reason="OUTCOME_QUARANTINED"))
            continue
        if _h5_metric(outcome, "return") is None:
            entries.append(_entry(subject, outcome=outcome, reason="UNDERMATURE_HORIZON"))
            continue
        candidate = _entry(subject, outcome=outcome, included=True)
        provisional.append((candidate, subject, outcome))

    # Canonicalization: one independent unit per anchor day.  Latest decision_at wins;
    # exact timestamp ties are broken by largest immutable decision_record_id.
    by_day: dict[str, list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]]] = {}
    for item in provisional:
        by_day.setdefault(str(item[0]["anchor_trade_date"]), []).append(item)
    for day in sorted(by_day):
        items = by_day[day]
        chosen = max(
            items,
            key=lambda item: (
                _aware(str(item[1]["decision_at"]), "decision_at"),
                int(item[1]["decision_record_id"]),
            ),
        )
        entries.append(chosen[0])
        for item in items:
            if item is chosen:
                continue
            excluded = dict(item[0])
            excluded["included"] = False
            excluded["reason_code"] = "NONCANONICAL_SAME_DAY"
            entries.append(excluded)

    cohort_hash = measurement_cohort_sha256(entries)
    run = MeasurementRun(
        measurement_run_id=measurement_run_id_for(
            policy.policy_id, as_of_cutoff, policy.random_seed, code_version, cohort_hash
        ),
        measurement_policy_id=policy.policy_id,
        as_of_cutoff=as_of_cutoff,
        random_seed=policy.random_seed,
        code_version=code_version,
        cohort_manifest_sha256=cohort_hash,
    )
    created = create_measurement_run(run, entries, path=path)
    return FrozenCohort(run=run, entries=tuple(entries), created=created)


def _seed(base: int, key: str, salt: str) -> int:
    digest = hashlib.sha256(f"{base}|{key}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _median(values: list[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def _result(
    *, run_id: str, analysis_key: str, dimension: str, groups: tuple[str, str],
    target_id: str, metric_name: str, values_a: list[float], values_b: list[float],
    policy, seed: int, alternative: str, method: str, extra: Mapping[str, Any] | None = None,
) -> MeasurementResult:
    n = {groups[0]: len(values_a), groups[1]: len(values_b)}
    total_days = len(values_a) + len(values_b)
    underpowered = (
        total_days < policy.min_days
        or len(values_a) < policy.min_group_days
        or len(values_b) < policy.min_group_days
    )
    effect = ci_low = ci_high = p_value = None
    detail: dict[str, Any] = {
        "group_mean": {
            groups[0]: None if not values_a else float(statistics.fmean(values_a)),
            groups[1]: None if not values_b else float(statistics.fmean(values_b)),
        },
        "group_median": {groups[0]: _median(values_a), groups[1]: _median(values_b)},
        "alternative": alternative,
        "resampling_algorithm_id": policy.resampling_algorithm_id,
        "rng_algorithm_id": policy.rng_algorithm_id,
    }
    if values_a and values_b:
        perm = permutation_test(
            values_a, values_b, iterations=policy.permutation_iterations,
            seed=_seed(seed, analysis_key, "permutation"), alternative=alternative,
        )
        boot = bootstrap_effect_ci(
            values_a, values_b, iterations=policy.bootstrap_iterations,
            seed=_seed(seed, analysis_key, "bootstrap"), confidence_level=policy.confidence_level,
        )
        effect = perm.observed_effect
        ci_low, ci_high, p_value = boot.ci_low, boot.ci_high, perm.p_value
        detail["label_permutation_placebo"] = {
            "iterations": perm.iterations, "exact": perm.exact,
            "null_mean": perm.null_mean, "null_ci_low": perm.null_ci_low,
            "null_ci_high": perm.null_ci_high,
        }
        if metric_name == "return":
            detail["positive_rate"] = {
                groups[0]: sum(v > 0 for v in values_a) / len(values_a),
                groups[1]: sum(v > 0 for v in values_b) / len(values_b),
            }
    if extra:
        detail.update(dict(extra))
    payload = measurement_result_payload(
        measurement_run_id=run_id, analysis_key=analysis_key, dimension=dimension,
        groups=groups, target_id=target_id, horizon="H5", metric_name=metric_name, n=n,
        effect=effect, ci_low=ci_low, ci_high=ci_high, p_value=p_value, method=method,
        underpowered=underpowered, result=detail,
    )
    return MeasurementResult(
        measurement_run_id=run_id, analysis_key=analysis_key, dimension=dimension,
        groups=groups, target_id=target_id, horizon="H5", metric_name=metric_name, n=n,
        effect=effect, ci_low=ci_low, ci_high=ci_high, p_value=p_value, method=method,
        underpowered=underpowered, result=detail, content_sha256=content_sha256(payload),
    )


def _with_result_detail(
    item: MeasurementResult,
    updates: Mapping[str, Any],
    *,
    underpowered: bool | None = None,
) -> MeasurementResult:
    detail = dict(item.result)
    detail.update(dict(updates))
    final_underpowered = item.underpowered if underpowered is None else bool(underpowered)
    payload = measurement_result_payload(
        measurement_run_id=item.measurement_run_id, analysis_key=item.analysis_key,
        dimension=item.dimension, groups=item.groups, target_id=item.target_id,
        horizon=item.horizon, metric_name=item.metric_name, n=item.n, effect=item.effect,
        ci_low=item.ci_low, ci_high=item.ci_high, p_value=item.p_value, method=item.method,
        underpowered=final_underpowered, result=detail,
    )
    return MeasurementResult(
        measurement_run_id=item.measurement_run_id, analysis_key=item.analysis_key,
        dimension=item.dimension, groups=item.groups, target_id=item.target_id,
        horizon=item.horizon, metric_name=item.metric_name, n=item.n, effect=item.effect,
        ci_low=item.ci_low, ci_high=item.ci_high, p_value=item.p_value, method=item.method,
        underpowered=final_underpowered, result=detail, content_sha256=content_sha256(payload),
    )


def _records(run_id: str, *, path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for link in list_measurement_run_subjects(run_id, path=path):
        if not bool(link["included"]):
            continue
        subject = load_review_subject(str(link["review_subject_id"]), path=path)
        outcome = load_outcome_set(str(link["outcome_set_id"]), path=path)
        if subject is None or outcome is None:
            raise RuntimeError("frozen MeasurementRun lineage disappeared")
        metrics = {
            str(m["metric_name"]): float(m["value"])
            for m in outcome.get("metrics") or ()
            if str(m["target_id"]) == _PRIMARY_TARGET and str(m["horizon"]) == "H5"
        }
        h1 = next((
            float(m["value"]) for m in outcome.get("metrics") or ()
            if str(m["target_id"]) == _PRIMARY_TARGET and str(m["horizon"]) == "H1"
            and str(m["metric_name"]) == "return"
        ), None)
        rows.append({
            "anchor_trade_date": str(link["anchor_trade_date"]),
            "subject": subject, "outcome": outcome, "metrics": metrics, "h1_return": h1,
        })
    return sorted(rows, key=lambda r: r["anchor_trade_date"])


def _label(record: Mapping[str, Any], hypothesis: str) -> str | None:
    subject = record["subject"]
    manifest = dict(subject.get("manifest") or {})
    stances = dict(manifest.get("stances") or {})
    if hypothesis == "P4-H01":
        status = str(subject.get("card_status"))
        return status if status in {"BUY", "AVOID"} else None
    if hypothesis == "P4-H02":
        return "VETO" if bool(manifest.get("risk_veto")) else "NON_VETO"
    if hypothesis == "P4-H03":
        stance = stances.get("market")
        return "POSITIVE" if stance in _MARKET_POSITIVE else "NEGATIVE" if stance in _MARKET_NEGATIVE else None
    if hypothesis == "P4-H04":
        stance = stances.get("technical")
        if stance == _TECHNICAL_BULLISH:
            return "BULLISH"
        return "BEARISH" if stance == _TECHNICAL_BEARISH else None
    if hypothesis == "P4-H05":
        stance = stances.get("sector")
        if stance == _SECTOR_MAINLINE:
            return "MAINLINE"
        return "BROAD_WEAK" if stance == _SECTOR_BROAD_WEAK else None
    raise KeyError(hypothesis)


def _confirmatory(run_id: str, records: list[dict[str, Any]], policy) -> list[MeasurementResult]:
    specs = [
        ("P4-H01", "card_status", ("BUY", "AVOID"), "return", "greater"),
        ("P4-H02:return", "risk_veto", ("VETO", "NON_VETO"), "return", "less"),
        ("P4-H02:mae", "risk_veto", ("VETO", "NON_VETO"), "mae", "less"),
        ("P4-H03", "market_stance", ("POSITIVE", "NEGATIVE"), "return", "greater"),
        ("P4-H04", "technical_stance", ("BULLISH", "BEARISH"), "return", "greater"),
        ("P4-H05", "sector_stance", ("MAINLINE", "BROAD_WEAK"), "return", "greater"),
    ]
    out: list[MeasurementResult] = []
    for key, dimension, groups, metric, alternative in specs:
        hypothesis = key.split(":", 1)[0]
        definition = get_hypothesis(hypothesis)
        expected_metrics = tuple(str(definition.endpoint_metric).split("+"))
        if definition.endpoint_target_id != _PRIMARY_TARGET or definition.endpoint_horizon != "H5":
            raise RuntimeError(f"unsupported confirmatory endpoint registry drift: {hypothesis}")
        if metric not in expected_metrics:
            raise RuntimeError(f"measurement spec disagrees with Hypothesis Registry: {key}")
        a = [r["metrics"][metric] for r in records if _label(r, hypothesis) == groups[0] and metric in r["metrics"]]
        b = [r["metrics"][metric] for r in records if _label(r, hypothesis) == groups[1] and metric in r["metrics"]]
        out.append(_result(
            run_id=run_id, analysis_key=key, dimension=dimension, groups=groups,
            target_id=_PRIMARY_TARGET, metric_name=metric, values_a=a, values_b=b,
            policy=policy, seed=policy.random_seed, alternative=alternative,
            method="confirmatory-day-permutation-bootstrap-v1",
        ))
    # 🔴 `_CONFIRMATORY_KEYS` 与上面这张 specs 仍是两份手写清单。
    #    policy ↔ 模块常量那条对账在导入期做了，但**模块常量 ↔ 真正产出的 key**
    #    没人对过 —— 加一条 spec 而两张清单都不改时，导入期检查照样通过，
    #    那条分析只是悄悄不进 `required_analysis_keys`（于是 Evidence Ready 不要求它）。
    #    ⇒ 判据落在**真正产出的东西**上，跑到这里就必然被检查一次。
    produced = {r.analysis_key for r in out}
    if produced != set(_CONFIRMATORY_KEYS):
        raise RuntimeError(
            f"确认性分析产出的 key 与 _CONFIRMATORY_KEYS 不一致："
            f"多了 {sorted(produced - set(_CONFIRMATORY_KEYS))}、"
            f"少了 {sorted(set(_CONFIRMATORY_KEYS) - produced)}。\n"
            "  ⇒ 改了 specs 就要同步 `_CONFIRMATORY_KEYS` 与 MeasurementPolicy 的\n"
            "     `required_analysis_keys`；而改变确认性假设集合应当发新的\n"
            "     MeasurementPolicy id，不是原地改 v1。"
        )
    pvals = {r.analysis_key: r.p_value for r in out if r.p_value is not None}
    corrected = holm_adjust(pvals)
    return [
        _with_result_detail(r, {
            "correction": policy.confirmatory_correction,
            "corrected_p_value": corrected.get(r.analysis_key),
            "pre_registered": True,
        }) for r in out
    ]


def _lagged_placebo(run_id: str, records: list[dict[str, Any]], policy) -> list[MeasurementResult]:
    specs = [
        ("P4-H01", "card_status", ("BUY", "AVOID"), "return", "greater"),
        ("P4-H03", "market_stance", ("POSITIVE", "NEGATIVE"), "return", "greater"),
        ("P4-H04", "technical_stance", ("BULLISH", "BEARISH"), "return", "greater"),
        ("P4-H05", "sector_stance", ("MAINLINE", "BROAD_WEAK"), "return", "greater"),
    ]
    out: list[MeasurementResult] = []
    for hyp, dimension, groups, metric, alternative in specs:
        lag = lagged_labels((r["anchor_trade_date"], _label(r, hyp)) for r in records)
        a = [r["metrics"][metric] for r in records if lag.get(r["anchor_trade_date"]) == groups[0] and metric in r["metrics"]]
        b = [r["metrics"][metric] for r in records if lag.get(r["anchor_trade_date"]) == groups[1] and metric in r["metrics"]]
        key = f"PLACEBO-LAG:{hyp}"
        out.append(_result(
            run_id=run_id, analysis_key=key, dimension=f"lagged_{dimension}", groups=groups,
            target_id=_PRIMARY_TARGET, metric_name=metric, values_a=a, values_b=b,
            policy=policy, seed=policy.random_seed, alternative=alternative,
            method="lagged-label-placebo-v1", extra={"placebo": True, "lag_days": 1},
        ))
    pvals = {r.analysis_key: r.p_value for r in out if r.p_value is not None}
    corrected = bh_fdr_adjust(pvals)
    return [_with_result_detail(r, {
        "correction": policy.exploratory_correction,
        "corrected_p_value": corrected.get(r.analysis_key), "pre_registered": False,
    }) for r in out]


def _exploratory(run_id: str, records: list[dict[str, Any]], policy) -> list[MeasurementResult]:
    dimensions: list[tuple[str, Callable[[dict[str, Any]], str | None]]] = [
        ("emotion_stance", lambda r: str((r["subject"].get("manifest") or {}).get("stances", {}).get("emotion") or "") or None),
        ("news_stance", lambda r: str((r["subject"].get("manifest") or {}).get("stances", {}).get("news") or "") or None),
        ("model_ref", lambda r: str(r["subject"].get("model_ref") or "") or None),
        ("missing_any", lambda r: "MISSING" if (r["subject"].get("manifest") or {}).get("missing_codes") else "COMPLETE"),
        ("wait_split", lambda r: "WAIT" if str(r["subject"].get("card_status")) == "WAIT" else "NON_WAIT"),
        ("system_block_split", lambda r: "SYSTEM_BLOCK" if (
            str(r["subject"].get("card_status")) == "BLOCK"
            and not bool((r["subject"].get("manifest") or {}).get("risk_veto"))
        ) else "OTHER"),
    ]
    drafts: list[MeasurementResult] = []
    for dimension, getter in dimensions:
        labels = sorted({label for r in records if (label := getter(r)) is not None})
        if len(labels) < 2:
            continue
        for label in labels:
            a = [r["metrics"]["return"] for r in records if getter(r) == label and "return" in r["metrics"]]
            b = [r["metrics"]["return"] for r in records if getter(r) != label and getter(r) is not None and "return" in r["metrics"]]
            key = f"EXP:{dimension}:{label}"
            drafts.append(_result(
                run_id=run_id, analysis_key=key, dimension=dimension,
                groups=(label, "REST"), target_id=_PRIMARY_TARGET, metric_name="return",
                values_a=a, values_b=b, policy=policy, seed=policy.random_seed,
                alternative="two-sided", method="exploratory-day-permutation-bootstrap-v1",
                extra={"pre_registered": False},
            ))
    pvals = {r.analysis_key: r.p_value for r in drafts if r.p_value is not None}
    corrected = bh_fdr_adjust(pvals)
    return [_with_result_detail(r, {
        "correction": policy.exploratory_correction,
        "corrected_p_value": corrected.get(r.analysis_key),
    }) for r in drafts]


def _regime_robustness(run_id: str, records: list[dict[str, Any]], policy) -> list[MeasurementResult]:
    out: list[MeasurementResult] = []
    for regime, predicate in (("H1_UP", lambda x: x is not None and x > 0),
                              ("H1_NONPOS", lambda x: x is not None and x <= 0)):
        subset = [r for r in records if predicate(r.get("h1_return"))]
        for hyp, groups in (("P4-H01", ("BUY", "AVOID")), ("P4-H04", ("BULLISH", "BEARISH"))):
            a = [r["metrics"]["return"] for r in subset if _label(r, hyp) == groups[0] and "return" in r["metrics"]]
            b = [r["metrics"]["return"] for r in subset if _label(r, hyp) == groups[1] and "return" in r["metrics"]]
            out.append(_result(
                run_id=run_id, analysis_key=f"ROBUST-REGIME:{hyp}:{regime}",
                dimension=f"{hyp}_within_{regime}", groups=groups,
                target_id=_PRIMARY_TARGET, metric_name="return", values_a=a, values_b=b,
                policy=policy, seed=policy.random_seed, alternative="greater",
                method="matched-regime-robustness-v1", extra={"regime": regime, "pre_registered": False},
            ))
    pvals = {r.analysis_key: r.p_value for r in out if r.p_value is not None}
    corrected = bh_fdr_adjust(pvals)
    return [_with_result_detail(r, {
        "correction": policy.exploratory_correction,
        "corrected_p_value": corrected.get(r.analysis_key),
    }) for r in out]


def _equity_ew_robustness(
    run_id: str, records: list[dict[str, Any]], policy, *, path,
    resolver: Callable[..., EquityEWResult],
) -> list[MeasurementResult]:
    enriched: list[tuple[dict[str, Any], EquityEWResult]] = []
    unavailable_reasons: dict[str, int] = {}
    for record in records:
        got = resolver(record["outcome"], db_path=path)
        reason = got.reason
        if got.available and got.value is not None:
            if got.eligible_count < policy.equity_ew_min_symbols:
                reason = "MIN_SYMBOLS"
            elif got.coverage_ratio < policy.equity_ew_min_coverage_ratio:
                reason = "MIN_COVERAGE_RATIO"
            else:
                enriched.append((record, got))
                continue
        key = str(reason or "UNAVAILABLE")
        unavailable_reasons[key] = unavailable_reasons.get(key, 0) + 1
    out: list[MeasurementResult] = []
    for hyp, groups in (("P4-H01", ("BUY", "AVOID")), ("P4-H04", ("BULLISH", "BEARISH"))):
        a = [got.value for r, got in enriched if _label(r, hyp) == groups[0] and got.value is not None]
        b = [got.value for r, got in enriched if _label(r, hyp) == groups[1] and got.value is not None]
        source_ids = sorted({sid for _r, got in enriched for sid in got.source_snapshot_ids})
        coverage = [got.coverage_ratio for _r, got in enriched]
        draft = _result(
            run_id=run_id, analysis_key=f"ROBUST-EQUITY-EW:{hyp}",
            dimension=f"{hyp}_equity_ew", groups=groups,
            target_id="market.equity_ew.v1", metric_name="return",
            values_a=[float(x) for x in a], values_b=[float(x) for x in b], policy=policy,
            seed=policy.random_seed, alternative="greater", method="equity-ew-robustness-v1",
            extra={
                "source_snapshot_ids": source_ids,
                "available_days": len(enriched),
                "mean_coverage_ratio": None if not coverage else float(statistics.fmean(coverage)),
                "minimums": {
                    "symbols": policy.equity_ew_min_symbols,
                    "coverage_ratio": policy.equity_ew_min_coverage_ratio,
                    "eligible_days": policy.equity_ew_min_eligible_days,
                },
                "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
                "pre_registered": False,
            },
        )
        out.append(_with_result_detail(
            draft, {},
            underpowered=(
                draft.underpowered or len(enriched) < policy.equity_ew_min_eligible_days
            ),
        ))
    pvals = {r.analysis_key: r.p_value for r in out if r.p_value is not None}
    corrected = bh_fdr_adjust(pvals)
    return [_with_result_detail(r, {
        "correction": policy.exploratory_correction,
        "corrected_p_value": corrected.get(r.analysis_key),
    }) for r in out]


def _unconditional_baseline(run_id: str, records: list[dict[str, Any]], policy) -> MeasurementResult:
    values = [r["metrics"]["return"] for r in records if "return" in r["metrics"]]
    detail = {
        "mean": None if not values else float(statistics.fmean(values)),
        "median": None if not values else float(statistics.median(values)),
        "positive_rate": None if not values else sum(v > 0 for v in values) / len(values),
        "pre_registered": False,
        "purpose": "unconditional-market-baseline",
    }
    n = {"ALL": len(values)}
    payload = measurement_result_payload(
        measurement_run_id=run_id, analysis_key="BASELINE:H5:return",
        dimension="unconditional", groups=("ALL",), target_id=_PRIMARY_TARGET,
        horizon="H5", metric_name="return", n=n, effect=detail["mean"],
        ci_low=None, ci_high=None, p_value=None, method="unconditional-baseline-v1",
        underpowered=len(values) < policy.min_days, result=detail,
    )
    return MeasurementResult(
        measurement_run_id=run_id, analysis_key="BASELINE:H5:return",
        dimension="unconditional", groups=("ALL",), target_id=_PRIMARY_TARGET,
        horizon="H5", metric_name="return", n=n, effect=detail["mean"],
        ci_low=None, ci_high=None, p_value=None, method="unconditional-baseline-v1",
        underpowered=len(values) < policy.min_days, result=detail,
        content_sha256=content_sha256(payload),
    )


def run_measurement(
    as_of_cutoff: str,
    *,
    path=None,
    code_version: str,
    measurement_policy_id: str = "p4-market-signal-v1",
    equity_ew_resolver: Callable[..., EquityEWResult] = resolve_equity_ew_h5,
) -> MeasurementExecution:
    """Freeze/reuse a cohort, compute results, and leave the run at COMPUTED.

    A failed execution may retry the *same frozen cohort* through an append-only
    FAILED -> COHORT_FROZEN event.  No statistical identity or prior event is rewritten.
    """
    frozen = freeze_measurement_cohort(
        as_of_cutoff, path=path, code_version=code_version,
        measurement_policy_id=measurement_policy_id,
    )
    run_id = frozen.run.measurement_run_id
    run = load_measurement_run(run_id, path=path)
    events = list((run or {}).get("events") or ())
    state = None if not events else str(events[-1]["to_state"])
    existing = list_measurement_results(run_id, path=path)
    if state == "FAILED":
        retry_failed_measurement_run(
            run_id, detail={"existing_result_count": len(existing)}, path=path
        )
        state = "COHORT_FROZEN"
    links = list_measurement_run_subjects(run_id, path=path)
    included = sum(bool(row["included"]) for row in links)
    excluded = len(links) - included
    policy = get_measurement_policy(measurement_policy_id)
    if existing:
        if state == "COHORT_FROZEN":
            append_measurement_run_event(
                run_id, "COMPUTED", detail={"result_count": len(existing), "reused": True}, path=path
            )
        elif state not in {"COMPUTED", "REPORTED", "COMPLETE"}:
            raise RuntimeError(f"MeasurementRun has results in invalid state {state}")
        return MeasurementExecution(
            run_id, frozen.created, len(existing), included, excluded,
            measurement_run_underpowered(existing, policy, included),
        )

    if state != "COHORT_FROZEN":
        raise RuntimeError(f"MeasurementRun is not computable from state {state}")
    try:
        records = _records(run_id, path=path)
        results: list[MeasurementResult] = [_unconditional_baseline(run_id, records, policy)]
        results.extend(_confirmatory(run_id, records, policy))
        results.extend(_lagged_placebo(run_id, records, policy))
        results.extend(_exploratory(run_id, records, policy))
        results.extend(_regime_robustness(run_id, records, policy))
        results.extend(_equity_ew_robustness(
            run_id, records, policy, path=path, resolver=equity_ew_resolver
        ))
        save_measurement_results(run_id, tuple(results), path=path)
    except Exception as exc:
        current = load_measurement_run(run_id, path=path)
        current_events = list((current or {}).get("events") or ())
        current_state = None if not current_events else str(current_events[-1]["to_state"])
        if current_state not in {"COMPLETE", "FAILED"}:
            append_measurement_run_event(
                run_id, "FAILED",
                detail={"stage": "COMPUTE", "error_code": type(exc).__name__, "message": str(exc)},
                path=path,
            )
        raise
    return MeasurementExecution(
        run_id, frozen.created, len(results), included, excluded,
        measurement_run_underpowered(results, policy, included),
    )

