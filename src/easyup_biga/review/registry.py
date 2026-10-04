"""Phase 4 policy registries — versioned semantics for Review & Measurement.

P4-0 deliberately freezes *policy identity* before Phase 4 starts persisting ReviewSubject,
OutcomeSet, ReviewRecord or MeasurementRun rows.  Any rule that can change a historical
result gets a stable id here; changing semantics means adding a new version, never editing
v1 in place and silently recomputing history.

This module is the single handwritten source for:
- anchor policy
- target policy / target definitions
- outcome policy
- review policy
- measurement policy
- confirmatory hypotheses

The registries are declarations only.  P4-1+ will add the persisted domain contracts and
engines that consume them.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from easyup_biga.data.registry import DATASET_REGISTRY

from .contracts import REVIEW_LABELS
from .metric_algorithms import get_metric_algorithm

__all__ = [
    "ANCHOR_POLICY_REGISTRY",
    "HYPOTHESIS_REGISTRY",
    "MEASUREMENT_POLICY_REGISTRY",
    "OUTCOME_POLICY_REGISTRY",
    "REVIEW_POLICY_REGISTRY",
    "TARGET_REGISTRY",
    "AnchorPolicyDefinition",
    "HypothesisDefinition",
    "MeasurementPolicyDefinition",
    "OutcomePolicyDefinition",
    "ReviewPolicyDefinition",
    "TargetDefinition",
    "TargetPolicyDefinition",
    "get_anchor_policy",
    "get_hypothesis",
    "get_measurement_policy",
    "get_outcome_policy",
    "get_review_policy",
    "get_target",
    "get_target_policy",
]


def _require_text(name: str, value: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class AnchorPolicyDefinition:
    policy_id: str
    timezone: str
    cutoff_local_time: str
    trading_calendar_dataset_id: str
    mode: str
    entry_price_field: str

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        _require_text("timezone", self.timezone)
        _require_text("trading_calendar_dataset_id", self.trading_calendar_dataset_id)
        if self.mode != "next-executable-open":
            raise ValueError(f"unsupported anchor mode: {self.mode}")
        hhmmss = self.cutoff_local_time.split(":")
        if len(hhmmss) != 3 or any(not p.isdigit() for p in hhmmss):
            raise ValueError("cutoff_local_time must be HH:MM:SS")
        if self.entry_price_field != "open":
            raise ValueError("P4 v1 anchor entry_price_field must be open")


@dataclass(frozen=True, slots=True)
class TargetDefinition:
    target_id: str
    title: str
    dataset_ids: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    aggregation_rule: str
    role: str

    def __post_init__(self) -> None:
        _require_text("target_id", self.target_id)
        _require_text("title", self.title)
        if not self.dataset_ids:
            raise ValueError(f"{self.target_id}: dataset_ids must not be empty")
        if len(self.dataset_ids) != len(set(self.dataset_ids)):
            raise ValueError(f"{self.target_id}: dataset_ids contain duplicates")
        if len(self.instrument_ids) != len(set(self.instrument_ids)):
            raise ValueError(f"{self.target_id}: instrument_ids contain duplicates")
        _require_text("aggregation_rule", self.aggregation_rule)
        if self.role not in {"PRIMARY_COMPONENT", "PRIMARY_ENDPOINT", "ROBUSTNESS_ENDPOINT"}:
            raise ValueError(f"{self.target_id}: unsupported role {self.role!r}")


@dataclass(frozen=True, slots=True)
class TargetPolicyDefinition:
    policy_id: str
    targets: tuple[TargetDefinition, ...]

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        ids = [t.target_id for t in self.targets]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("target ids must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class OutcomePolicyDefinition:
    policy_id: str
    horizons: tuple[str, ...]
    metric_ids: tuple[str, ...]
    maturity_order: tuple[str, ...]
    revision_mode: str
    metric_algorithm_id: str
    metric_formula_sha256: str
    metric_implementation_sha256: str
    metric_fingerprint_scheme_id: str

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        if self.horizons != ("H1", "H3", "H5", "H20"):
            raise ValueError("P4 v1 horizons are fixed at H1/H3/H5/H20")
        if self.maturity_order != self.horizons:
            raise ValueError("maturity_order must match horizon order")
        if self.revision_mode != "append-only-supersede":
            raise ValueError("Outcome revisions must be append-only-supersede")
        if not self.metric_ids or len(self.metric_ids) != len(set(self.metric_ids)):
            raise ValueError("metric_ids must be non-empty and unique")
        algorithm = get_metric_algorithm(self.metric_algorithm_id)
        expected = algorithm.manifest_ref()
        if self.metric_formula_sha256 != expected["formula_sha256"]:
            raise ValueError("OutcomePolicy formula fingerprint does not match algorithm registry")
        if self.metric_implementation_sha256 != expected["implementation_sha256"]:
            raise ValueError("OutcomePolicy implementation fingerprint does not match algorithm registry")
        if self.metric_fingerprint_scheme_id != expected["fingerprint_scheme_id"]:
            raise ValueError("OutcomePolicy fingerprint scheme does not match algorithm registry")

    def metric_algorithm_ref(self) -> dict[str, str]:
        return {
            "algorithm_id": self.metric_algorithm_id,
            "formula_sha256": self.metric_formula_sha256,
            "implementation_sha256": self.metric_implementation_sha256,
            "fingerprint_scheme_id": self.metric_fingerprint_scheme_id,
        }


@dataclass(frozen=True, slots=True)
class ReviewPolicyDefinition:
    policy_id: str
    primary_target_id: str
    primary_horizon: str
    noise_band_ratio: float
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        _require_text("primary_target_id", self.primary_target_id)
        if self.primary_horizon != "H5":
            raise ValueError("P4 v1 review primary horizon is H5")
        if not (0 <= self.noise_band_ratio < 1):
            raise ValueError("noise_band_ratio must be in [0, 1)")
        # 🔴 从 `contracts.REVIEW_LABELS` 派生，不手抄一份 —— 手抄的那份
        #    2026-09-26 已经漏过 `ABSTAIN_SYSTEM` 一次（见那边的说明）。
        if set(self.labels) != set(REVIEW_LABELS):
            raise ValueError(
                f"review labels 必须与 contracts.REVIEW_LABELS 一致；"
                f"少了 {sorted(set(REVIEW_LABELS) - set(self.labels))}、"
                f"多了 {sorted(set(self.labels) - set(REVIEW_LABELS))}"
            )


@dataclass(frozen=True, slots=True)
class MeasurementPolicyDefinition:
    policy_id: str
    cohort_unit: str
    canonical_subject_type: str
    subject_anchor_policy_id: str
    subject_target_policy_id: str
    outcome_policy_id: str
    review_policy_id: str
    min_days: int
    min_group_days: int
    stable_review_days: int
    permutation_iterations: int
    bootstrap_iterations: int
    confidence_level: float
    confirmatory_correction: str
    exploratory_correction: str
    random_seed: int
    resampling_algorithm_id: str
    rng_algorithm_id: str
    equity_ew_min_symbols: int
    equity_ew_min_coverage_ratio: float
    equity_ew_min_eligible_days: int
    required_analysis_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("policy_id", self.policy_id)
        if self.cohort_unit != "anchor_trade_date":
            raise ValueError("P4 v1 inference unit is anchor_trade_date")
        if self.canonical_subject_type != "ONLINE_CARD":
            raise ValueError("P4 v1 canonical subject is ONLINE_CARD")
        for name in ("subject_anchor_policy_id", "subject_target_policy_id",
                     "outcome_policy_id", "review_policy_id"):
            _require_text(name, getattr(self, name))
        if not (0 < self.min_group_days <= self.min_days <= self.stable_review_days):
            raise ValueError("invalid minimum sample thresholds")
        if self.permutation_iterations <= 0 or self.bootstrap_iterations <= 0:
            raise ValueError("resampling iterations must be positive")
        if not (0 < self.confidence_level < 1):
            raise ValueError("confidence_level must be in (0, 1)")
        if self.confirmatory_correction != "holm":
            raise ValueError("confirmatory correction must be Holm")
        if self.exploratory_correction != "bh-fdr":
            raise ValueError("exploratory correction must be BH-FDR")
        # 算法与 RNG 的版本号进 policy —— 设计文档 §11.4：
        # 「算法与随机数生成器版本进入 policy」。换 RNG 会改变历史 p 值，
        # 所以它必须是**可以指认的**，而不是「当时的 Python 版本恰好是什么」。
        _require_text("resampling_algorithm_id", self.resampling_algorithm_id)
        _require_text("rng_algorithm_id", self.rng_algorithm_id)
        if self.equity_ew_min_symbols < 1:
            raise ValueError("equity_ew_min_symbols must be positive")
        if not 0.0 < self.equity_ew_min_coverage_ratio <= 1.0:
            raise ValueError("equity_ew_min_coverage_ratio must be in (0,1]")
        if self.equity_ew_min_eligible_days < 1:
            raise ValueError("equity_ew_min_eligible_days must be positive")
        if not self.required_analysis_keys or len(self.required_analysis_keys) != len(set(self.required_analysis_keys)):
            raise ValueError("required_analysis_keys must be non-empty and unique")
        if any(not str(key).strip() for key in self.required_analysis_keys):
            raise ValueError("required_analysis_keys must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class HypothesisDefinition:
    hypothesis_id: str
    comparison: str
    endpoint_target_id: str
    endpoint_horizon: str
    endpoint_metric: str
    expected_direction: str

    def __post_init__(self) -> None:
        _require_text("hypothesis_id", self.hypothesis_id)
        _require_text("comparison", self.comparison)
        _require_text("endpoint_target_id", self.endpoint_target_id)
        if self.endpoint_horizon != "H5":
            raise ValueError("P4 v1 confirmatory hypotheses use H5")
        _require_text("endpoint_metric", self.endpoint_metric)
        _require_text("expected_direction", self.expected_direction)


_ANCHOR_POLICIES = (
    AnchorPolicyDefinition(
        policy_id="cn-a-share-next-executable-open-v1",
        timezone="Asia/Shanghai",
        cutoff_local_time="09:20:00",
        trading_calendar_dataset_id="cn.trading_calendar",
        mode="next-executable-open",
        entry_price_field="open",
    ),
)

_TARGET_POLICIES = (
    TargetPolicyDefinition(
        policy_id="market.index_targets.v1",
        targets=(
            TargetDefinition(
                target_id="index.sh000001",
                title="上证指数",
                dataset_ids=("cn.index.eod_bars",),
                instrument_ids=("index.sh000001",),
                aggregation_rule="direct-ohlc",
                role="PRIMARY_COMPONENT",
            ),
            TargetDefinition(
                target_id="index.sz399106",
                title="深证综指",
                dataset_ids=("cn.index.eod_bars",),
                instrument_ids=("index.sz399106",),
                aggregation_rule="direct-ohlc",
                role="PRIMARY_COMPONENT",
            ),
            TargetDefinition(
                target_id="market.index_blend.v1",
                title="上证指数 / 深证综指 anchor-open 归一化 50/50 等权组合",
                dataset_ids=("cn.index.eod_bars",),
                instrument_ids=("index.sh000001", "index.sz399106"),
                aggregation_rule="normalize-each-at-anchor-open-then-equal-weight-50-50",
                role="PRIMARY_ENDPOINT",
            ),
            TargetDefinition(
                target_id="market.equity_ew.v1",
                title="当日可交易 A 股复权收益等权聚合",
                dataset_ids=(
                    "cn.equity.daily_bars",
                    "cn.security.tradability",
                    "cn.equity.adjustment_factors",
                ),
                instrument_ids=(),
                aggregation_rule="tradable-adjusted-return-equal-weight",
                role="ROBUSTNESS_ENDPOINT",
            ),
        ),
    ),
)

_OUTCOME_ALGORITHM_V1 = get_metric_algorithm("market-index-outcome-synchronized-close-v1")

_OUTCOME_POLICIES = (
    OutcomePolicyDefinition(
        policy_id="market-h1-h3-h5-h20-v1",
        horizons=("H1", "H3", "H5", "H20"),
        metric_ids=("return", "mfe", "mae", "coverage"),
        maturity_order=("H1", "H3", "H5", "H20"),
        revision_mode="append-only-supersede",
        metric_algorithm_id=_OUTCOME_ALGORITHM_V1.algorithm_id,
        metric_formula_sha256=_OUTCOME_ALGORITHM_V1.formula_sha256,
        metric_implementation_sha256=_OUTCOME_ALGORITHM_V1.implementation_sha256,
        metric_fingerprint_scheme_id=_OUTCOME_ALGORITHM_V1.fingerprint_scheme_id,
    ),
)

_REVIEW_POLICIES = (
    ReviewPolicyDefinition(
        policy_id="directional-review-v1",
        primary_target_id="market.index_blend.v1",
        primary_horizon="H5",
        noise_band_ratio=0.005,
        labels=REVIEW_LABELS,
    ),
)

_MEASUREMENT_POLICIES = (
    MeasurementPolicyDefinition(
        policy_id="p4-market-signal-v1",
        cohort_unit="anchor_trade_date",
        canonical_subject_type="ONLINE_CARD",
        subject_anchor_policy_id="cn-a-share-next-executable-open-v1",
        subject_target_policy_id="market.index_targets.v1",
        outcome_policy_id="market-h1-h3-h5-h20-v1",
        review_policy_id="directional-review-v1",
        min_days=30,
        min_group_days=10,
        stable_review_days=60,
        permutation_iterations=10_000,
        bootstrap_iterations=10_000,
        confidence_level=0.95,
        confirmatory_correction="holm",
        exploratory_correction="bh-fdr",
        # Design v1 says the seed is frozen.  P4-0 chooses the design-freeze date as
        # the concrete v1 seed; changing it requires a new MeasurementPolicy id.
        random_seed=20260926,
        resampling_algorithm_id="day-permutation-bootstrap-v1",
        rng_algorithm_id="python-random-mt19937-v1",
        equity_ew_min_symbols=100,
        equity_ew_min_coverage_ratio=0.80,
        equity_ew_min_eligible_days=10,
        required_analysis_keys=(
            "BASELINE:H5:return",
            "P4-H01", "P4-H02:return", "P4-H02:mae", "P4-H03", "P4-H04", "P4-H05",
            "PLACEBO-LAG:P4-H01", "PLACEBO-LAG:P4-H03",
            "PLACEBO-LAG:P4-H04", "PLACEBO-LAG:P4-H05",
            "ROBUST-REGIME:P4-H01:H1_UP", "ROBUST-REGIME:P4-H01:H1_NONPOS",
            "ROBUST-REGIME:P4-H04:H1_UP", "ROBUST-REGIME:P4-H04:H1_NONPOS",
            "ROBUST-EQUITY-EW:P4-H01", "ROBUST-EQUITY-EW:P4-H04",
        ),
    ),
)

_HYPOTHESES = (
    HypothesisDefinition(
        "P4-H01", "Card BUY vs AVOID (BLOCK separate)",
        "market.index_blend.v1", "H5", "return", "BUY > AVOID",
    ),
    HypothesisDefinition(
        "P4-H02", "risk veto vs non-veto",
        "market.index_blend.v1", "H5", "return+mae", "veto has lower return and worse MAE",
    ),
    HypothesisDefinition(
        "P4-H03", "market positive vs negative stance",
        "market.index_blend.v1", "H5", "return", "positive > negative",
    ),
    HypothesisDefinition(
        "P4-H04", "technical bullish vs bearish stance",
        "market.index_blend.v1", "H5", "return", "bullish > bearish",
    ),
    HypothesisDefinition(
        "P4-H05", "sector clear-mainline vs broad-weak/no-mainline",
        "market.index_blend.v1", "H5", "return", "clear-mainline > weak/no-mainline",
    ),
)


def _by_id(items, field: str) -> Mapping[str, object]:
    out = {getattr(item, field): item for item in items}
    if len(out) != len(items):
        raise RuntimeError(f"duplicate {field} in Phase 4 registry")
    return MappingProxyType(out)


ANCHOR_POLICY_REGISTRY = _by_id(_ANCHOR_POLICIES, "policy_id")
TARGET_REGISTRY = _by_id(_TARGET_POLICIES, "policy_id")
OUTCOME_POLICY_REGISTRY = _by_id(_OUTCOME_POLICIES, "policy_id")
REVIEW_POLICY_REGISTRY = _by_id(_REVIEW_POLICIES, "policy_id")
MEASUREMENT_POLICY_REGISTRY = _by_id(_MEASUREMENT_POLICIES, "policy_id")
HYPOTHESIS_REGISTRY = _by_id(_HYPOTHESES, "hypothesis_id")

for _measurement in MEASUREMENT_POLICY_REGISTRY.values():
    if _measurement.subject_anchor_policy_id not in ANCHOR_POLICY_REGISTRY:
        raise RuntimeError(f"measurement policy references unknown anchor policy: {_measurement.policy_id}")
    if _measurement.subject_target_policy_id not in TARGET_REGISTRY:
        raise RuntimeError(f"measurement policy references unknown target policy: {_measurement.policy_id}")
    if _measurement.outcome_policy_id not in OUTCOME_POLICY_REGISTRY:
        raise RuntimeError(f"measurement policy references unknown outcome policy: {_measurement.policy_id}")
    if _measurement.review_policy_id not in REVIEW_POLICY_REGISTRY:
        raise RuntimeError(f"measurement policy references unknown review policy: {_measurement.policy_id}")

_TARGETS_BY_ID = MappingProxyType({
    target.target_id: target
    for policy in TARGET_REGISTRY.values()
    for target in policy.targets
})
if sum(len(policy.targets) for policy in TARGET_REGISTRY.values()) != len(_TARGETS_BY_ID):
    raise RuntimeError("duplicate target_id in Phase 4 target registry")

# Import-time reference integrity is intentional: a policy that points at a dataset/target
# that is not active must fail closed before a measurement job can ever run.
for anchor in ANCHOR_POLICY_REGISTRY.values():
    if anchor.trading_calendar_dataset_id not in DATASET_REGISTRY:
        raise RuntimeError(
            f"anchor policy {anchor.policy_id} references inactive dataset "
            f"{anchor.trading_calendar_dataset_id}"
        )
for target in _TARGETS_BY_ID.values():
    missing = [dataset_id for dataset_id in target.dataset_ids if dataset_id not in DATASET_REGISTRY]
    if missing:
        raise RuntimeError(f"target {target.target_id} references inactive datasets {missing}")
for review in REVIEW_POLICY_REGISTRY.values():
    if review.primary_target_id not in _TARGETS_BY_ID:
        raise RuntimeError(f"review policy references unknown target {review.primary_target_id}")
for hypothesis in HYPOTHESIS_REGISTRY.values():
    if hypothesis.endpoint_target_id not in _TARGETS_BY_ID:
        raise RuntimeError(
            f"hypothesis {hypothesis.hypothesis_id} references unknown target "
            f"{hypothesis.endpoint_target_id}"
        )


def _get(registry: Mapping[str, object], item_id: str, kind: str):
    try:
        return registry[item_id]
    except KeyError:
        raise KeyError(f"unknown Phase 4 {kind} {item_id!r}; registered={list(registry)}") from None


def get_anchor_policy(policy_id: str) -> AnchorPolicyDefinition:
    return _get(ANCHOR_POLICY_REGISTRY, policy_id, "anchor policy")


def get_target_policy(policy_id: str) -> TargetPolicyDefinition:
    return _get(TARGET_REGISTRY, policy_id, "target policy")


def get_target(target_id: str) -> TargetDefinition:
    return _get(_TARGETS_BY_ID, target_id, "target")


def get_outcome_policy(policy_id: str) -> OutcomePolicyDefinition:
    return _get(OUTCOME_POLICY_REGISTRY, policy_id, "outcome policy")


def get_review_policy(policy_id: str) -> ReviewPolicyDefinition:
    return _get(REVIEW_POLICY_REGISTRY, policy_id, "review policy")


def get_measurement_policy(policy_id: str) -> MeasurementPolicyDefinition:
    return _get(MEASUREMENT_POLICY_REGISTRY, policy_id, "measurement policy")


def get_hypothesis(hypothesis_id: str) -> HypothesisDefinition:
    return _get(HYPOTHESIS_REGISTRY, hypothesis_id, "hypothesis")
