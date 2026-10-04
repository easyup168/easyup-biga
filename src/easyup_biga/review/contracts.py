"""Immutable Phase 4 Review & Measurement contracts.

P4-1 establishes the value objects and identity vocabulary before engines exist.
The objects are intentionally storage-friendly: every historical rule is referenced
by stable policy id, and every payload that participates in replay has deterministic
canonical JSON / SHA-256 semantics.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Literal, Mapping

from easyup_biga.domain._freeze import deep_freeze, thaw
from easyup_biga.timebase import cn_iso

__all__ = [
    "Conclusion",
    "Eligibility",
    "MeasurementResult",
    "MeasurementRun",
    "MeasurementState",
    "OutcomeMetric",
    "OutcomeSet",
    "OutcomeStatus",
    "ReviewLabel",
    "ReviewRecord",
    "HORIZONS",
    "REVIEW_LABELS",
    "ReviewStatus",
    "ReviewSubject",
    "SubjectType",
    "canonical_json",
    "content_sha256",
    "horizon_trading_days",
    "measurement_cohort_payload",
    "measurement_cohort_sha256",
    "measurement_result_payload",
    "measurement_run_id_for",
    "outcome_set_id_for",
    "review_content_payload",
    "review_id_for",
    "review_subject_id_for",
]

SubjectType = Literal["ONLINE_CARD", "REPLAY_CARD"]
Eligibility = Literal["ELIGIBLE", "INELIGIBLE"]
OutcomeStatus = Literal["PARTIAL", "COMPLETE", "QUARANTINED", "INELIGIBLE"]
ReviewStatus = Literal["COMPLETE", "INSUFFICIENT_DATA", "UNSCORABLE"]
ReviewLabel = Literal[
    "SUPPORTS", "OPPOSES", "FLAT", "ABSTAIN", "ABSTAIN_SYSTEM", "UNSCORABLE"
]
MeasurementState = Literal[
    "CREATED", "COHORT_FROZEN", "COMPUTED", "REPORTED", "COMPLETE", "FAILED"
]
Conclusion = Literal["SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"]

_SUBJECT_TYPES = frozenset({"ONLINE_CARD", "REPLAY_CARD"})
_ELIGIBILITY = frozenset({"ELIGIBLE", "INELIGIBLE"})
_OUTCOME_STATUS = frozenset({"PARTIAL", "COMPLETE", "QUARANTINED", "INELIGIBLE"})
_REVIEW_STATUS = frozenset({"COMPLETE", "INSUFFICIENT_DATA", "UNSCORABLE"})
#: ReviewLabel 词表的**唯一手写处**。
#:
#: 🔴 这份清单原本在三个地方各写了一遍：这里、schema v30 的 CHECK、
#:    以及 `registry.ReviewPolicyDefinition` 里一句 `required = {...}` ——
#:    而第三份**漏了 `ABSTAIN_SYSTEM`**。于是「系统/数据原因的 BLOCK
#:    单独归一类、不进方向比较」这条设计（设计文档 §10.3）在契约层允许、
#:    在存储层允许，却会被 policy 构造函数当场拒掉。
#:    ⇒ 现在 registry 从这里派生，schema 的 CHECK 由测试对账。
REVIEW_LABELS: tuple[str, ...] = (
    "SUPPORTS", "OPPOSES", "FLAT", "ABSTAIN", "ABSTAIN_SYSTEM", "UNSCORABLE",
)
_REVIEW_LABEL = frozenset(REVIEW_LABELS)
#: Horizon 词表的**唯一手写处**（schema 的 CHECK 是 SQL 文本，派生不了，由测试对账）。
HORIZONS: tuple[str, ...] = ("H1", "H3", "H5", "H20")
_HORIZONS = frozenset(HORIZONS)


def horizon_trading_days(horizon: str) -> int:
    """`H<N>` ⇒ N 个交易日（含 anchor 当日）。

    🔴 **从名字派生，不另写一张表。** 外部实现把这个语义手写了两份：
    `outcomes._HORIZON_SIZE = {"H1": 1, ...}`（几天）与
    `anchors.horizon_index = {"H1": 0, ...}`（第几个下标，= 天数 - 1）。
    两份都是 `policy.horizons` 的孪生，而 `anchors` 那份漏一个 key 时
    **不报错** —— 它的推导式是 `for h, i in horizon_index.items() if h in policy.horizons`，
    policy 里有、表里没有的那个 horizon 会被**静默丢掉**，
    症状是「这个 horizon 永远不成熟」，而不是任何一条报错。
    """
    if not isinstance(horizon, str) or not horizon.startswith("H") or not horizon[1:].isdigit():
        raise ValueError(
            f"horizon 必须形如 H<正整数>（H1/H3/H5/H20…），收到 {horizon!r}。"
            "新增 horizon 只要名字合这个形状就自动可用，不需要再改一张映射表。"
        )
    days = int(horizon[1:])
    if days < 1:
        raise ValueError(f"horizon 天数必须 >= 1，收到 {horizon!r}")
    return days
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    """Strict deterministic JSON used by Phase 4 hashes and persisted manifests."""
    return json.dumps(
        thaw(value), ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    )


def content_sha256(value: Any) -> str:
    """SHA-256 of canonical JSON (or exact UTF-8 text when ``value`` is ``str``)."""
    blob = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _sha(name: str, value: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 64-char sha256")
    return value


def review_subject_id_for(
    decision_record_id: int, *, target_policy_id: str, anchor_policy_id: str
) -> str:
    """确定性身份 = （哪一行卡，在哪一对 policy 下冻结）。

    🔴 **policy 对进身份，不是只有 record_id。** 设计文档 §9.1 的唯一键就是这个
    三元组，理由在 AnchorPolicy 自己的说明里：09:20 那个 cutoff「不是永恒真理，
    未来如需调整，发布新的 AnchorPolicy ID，旧结果不重算」。

    身份里不带 policy 的话，同一张卡在 v2 policy 下就**没有第二个 id 可用**，
    而 `review_subjects` 是 append-only —— 那天只能重建表。
    这里多花 8 个十六进制字符，换掉的是一次动历史的迁移。

    ⚠️ 后缀取哈希而不是把 policy id 原样拼进去：policy id 是自由文本
    （`market.index_targets.v1` 里有点号），拼进去会让 id 的**分段**取决于
    policy id 里有没有分隔符 —— 那是按字符串形状做结构（L-13）。
    """
    rid = int(decision_record_id)
    if rid <= 0:
        raise ValueError("decision_record_id must be positive")
    _text("target_policy_id", target_policy_id)
    _text("anchor_policy_id", anchor_policy_id)
    suffix = content_sha256([target_policy_id, anchor_policy_id])[:8]
    return f"P4SUB-{rid:012d}-{suffix}"


def outcome_set_id_for(review_subject_id: str, outcome_policy_id: str, data_version: int) -> str:
    """Deterministic stable identity for one append-only outcome revision."""
    _text("review_subject_id", review_subject_id)
    _text("outcome_policy_id", outcome_policy_id)
    version = int(data_version)
    if version < 1:
        raise ValueError("data_version must be >= 1")
    digest = hashlib.sha256(
        f"{review_subject_id}|{outcome_policy_id}|{version}".encode("utf-8")
    ).hexdigest()[:24]
    return f"P4OUT-{digest}-V{version:04d}"


def review_id_for(review_subject_id: str, outcome_set_id: str, review_policy_id: str) -> str:
    """Deterministic identity for one immutable per-card review revision."""
    _text("review_subject_id", review_subject_id)
    _text("outcome_set_id", outcome_set_id)
    _text("review_policy_id", review_policy_id)
    digest = hashlib.sha256(
        f"{review_subject_id}|{outcome_set_id}|{review_policy_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"P4REV-{digest}"


def review_content_payload(
    *,
    review_subject_id: str,
    outcome_set_id: str,
    review_policy_id: str,
    review_status: str,
    review_label: str,
    utility_score: float | None,
    primary_horizon: str,
    detail: Mapping[str, Any],
    supersedes_review_id: str | None,
) -> dict[str, Any]:
    """Canonical ReviewRecord payload used for deterministic content hashing."""
    return {
        "review_subject_id": review_subject_id,
        "outcome_set_id": outcome_set_id,
        "review_policy_id": review_policy_id,
        "review_status": review_status,
        "review_label": review_label,
        "utility_score": None if utility_score is None else float(utility_score),
        "primary_horizon": primary_horizon,
        "detail": dict(detail),
        "supersedes_review_id": supersedes_review_id,
    }


def measurement_cohort_payload(entries: Any) -> list[dict[str, Any]]:
    """Canonical frozen cohort manifest used by MeasurementRun identity."""
    normalized: list[dict[str, Any]] = []
    for raw in entries:
        item = dict(raw)
        normalized.append({
            "review_subject_id": str(item["review_subject_id"]),
            "outcome_set_id": None if item.get("outcome_set_id") is None else str(item["outcome_set_id"]),
            "anchor_trade_date": None if item.get("anchor_trade_date") is None else str(item["anchor_trade_date"]),
            "analysis_group": None if item.get("analysis_group") is None else str(item["analysis_group"]),
            "included": bool(item["included"]),
            "reason_code": None if item.get("reason_code") is None else str(item["reason_code"]),
        })
    return sorted(normalized, key=lambda item: item["review_subject_id"])


def measurement_cohort_sha256(entries: Any) -> str:
    return content_sha256(measurement_cohort_payload(entries))


def measurement_run_id_for(
    measurement_policy_id: str,
    as_of_cutoff: str,
    random_seed: int,
    code_version: str,
    cohort_manifest_sha256: str,
) -> str:
    """Deterministic identity for one frozen MeasurementRun."""
    for name, value in (("measurement_policy_id", measurement_policy_id),
                        ("as_of_cutoff", as_of_cutoff), ("code_version", code_version)):
        _text(name, value)
    canonical_cutoff = cn_iso(as_of_cutoff, name="as_of_cutoff")
    _sha("cohort_manifest_sha256", cohort_manifest_sha256)
    digest = hashlib.sha256(
        f"{measurement_policy_id}|{canonical_cutoff}|{int(random_seed)}|{code_version}|"
        f"{cohort_manifest_sha256}".encode("utf-8")
    ).hexdigest()[:24]
    return f"P4MEAS-{digest}"


def measurement_result_payload(
    *,
    measurement_run_id: str,
    analysis_key: str,
    dimension: str,
    groups: tuple[str, ...],
    target_id: str,
    horizon: str,
    metric_name: str,
    n: Mapping[str, int],
    effect: float | None,
    ci_low: float | None,
    ci_high: float | None,
    p_value: float | None,
    method: str,
    underpowered: bool,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "measurement_run_id": measurement_run_id,
        "analysis_key": analysis_key,
        "dimension": dimension,
        "groups": list(groups),
        "target_id": target_id,
        "horizon": horizon,
        "metric_name": metric_name,
        "n": {str(k): int(v) for k, v in sorted(dict(n).items())},
        "effect": None if effect is None else float(effect),
        "ci_low": None if ci_low is None else float(ci_low),
        "ci_high": None if ci_high is None else float(ci_high),
        "p_value": None if p_value is None else float(p_value),
        "method": method,
        "underpowered": bool(underpowered),
        "result": dict(result),
    }



@dataclass(frozen=True, slots=True)
class ReviewSubject:
    review_subject_id: str
    decision_record_id: int
    decision_id: str
    subject_type: SubjectType
    decision_at: str
    card_status: str
    model_ref: str
    target_policy_id: str
    anchor_policy_id: str
    manifest: Mapping[str, Any]
    source_card_sha256: str
    eligibility: Eligibility
    exclusion_code: str | None = None
    run_id: str | None = None
    evidence_set_id: str | None = None

    def __post_init__(self) -> None:
        _text("review_subject_id", self.review_subject_id)
        if self.review_subject_id != review_subject_id_for(
            self.decision_record_id,
            target_policy_id=self.target_policy_id,
            anchor_policy_id=self.anchor_policy_id,
        ):
            raise ValueError(
                "review_subject_id 必须由 (decision_record_id, target_policy_id, "
                "anchor_policy_id) 派生 —— 用 review_subject_id_for() 生成，不要手拼"
            )
        _text("decision_id", self.decision_id)
        if self.subject_type not in _SUBJECT_TYPES:
            raise ValueError(f"unsupported subject_type {self.subject_type!r}")
        if not isinstance(self.decision_at, str):
            raise ValueError("decision_at must be a string")
        _text("card_status", self.card_status)
        _text("model_ref", self.model_ref)
        _text("target_policy_id", self.target_policy_id)
        _text("anchor_policy_id", self.anchor_policy_id)
        _sha("source_card_sha256", self.source_card_sha256)
        if self.eligibility not in _ELIGIBILITY:
            raise ValueError(f"unsupported eligibility {self.eligibility!r}")
        if self.eligibility == "ELIGIBLE" and not self.decision_at.strip():
            raise ValueError("ELIGIBLE subject requires non-empty decision_at")
        if self.decision_at.strip():
            try:
                object.__setattr__(self, "decision_at", cn_iso(self.decision_at, name="decision_at"))
            except ValueError:
                if self.eligibility == "ELIGIBLE":
                    raise
        if self.eligibility == "ELIGIBLE" and self.exclusion_code is not None:
            raise ValueError("ELIGIBLE subject cannot have exclusion_code")
        if self.eligibility == "INELIGIBLE" and not self.exclusion_code:
            raise ValueError("INELIGIBLE subject requires exclusion_code")
        for name, value in (("run_id", self.run_id), ("evidence_set_id", self.evidence_set_id)):
            if value is not None:
                _text(name, value)
        object.__setattr__(self, "manifest", deep_freeze(dict(self.manifest)))
        # Fail before storage if manifest contains NaN / non-JSON values.
        canonical_json(self.manifest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_subject_id": self.review_subject_id,
            "decision_record_id": self.decision_record_id,
            "decision_id": self.decision_id,
            "subject_type": self.subject_type,
            "decision_at": self.decision_at,
            "run_id": self.run_id,
            "evidence_set_id": self.evidence_set_id,
            "card_status": self.card_status,
            "model_ref": self.model_ref,
            "target_policy_id": self.target_policy_id,
            "anchor_policy_id": self.anchor_policy_id,
            "manifest": thaw(self.manifest),
            "source_card_sha256": self.source_card_sha256,
            "eligibility": self.eligibility,
            "exclusion_code": self.exclusion_code,
        }


@dataclass(frozen=True, slots=True)
class OutcomeSet:
    outcome_set_id: str
    review_subject_id: str
    outcome_policy_id: str
    anchor_trade_date: str
    knowledge_cutoff: str
    status: OutcomeStatus
    data_version: int
    manifest: Mapping[str, Any]
    content_sha256: str
    supersedes_outcome_set_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("outcome_set_id", "review_subject_id", "outcome_policy_id",
                     "anchor_trade_date", "knowledge_cutoff"):
            _text(name, getattr(self, name))
        object.__setattr__(
            self, "knowledge_cutoff", cn_iso(self.knowledge_cutoff, name="knowledge_cutoff")
        )
        if self.status not in _OUTCOME_STATUS:
            raise ValueError(f"unsupported outcome status {self.status!r}")
        if self.data_version < 1:
            raise ValueError("data_version must be >= 1")
        _sha("content_sha256", self.content_sha256)
        object.__setattr__(self, "manifest", deep_freeze(dict(self.manifest)))
        canonical_json(self.manifest)


@dataclass(frozen=True, slots=True)
class OutcomeMetric:
    target_id: str
    horizon: str
    observed_trade_date: str
    metric_name: str
    value: float
    unit: str
    source_snapshot_ids: tuple[str, ...] = dc_field(default_factory=tuple)
    coverage_ratio: float = 1.0

    def __post_init__(self) -> None:
        _text("target_id", self.target_id)
        if self.horizon not in _HORIZONS:
            raise ValueError(f"unsupported horizon {self.horizon!r}")
        _text("observed_trade_date", self.observed_trade_date)
        _text("metric_name", self.metric_name)
        _text("unit", self.unit)
        if not 0.0 <= float(self.coverage_ratio) <= 1.0:
            raise ValueError("coverage_ratio must be in [0,1]")
        object.__setattr__(self, "source_snapshot_ids", tuple(self.source_snapshot_ids))


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    review_id: str
    review_subject_id: str
    outcome_set_id: str
    review_policy_id: str
    review_status: ReviewStatus
    review_label: ReviewLabel
    primary_horizon: str
    detail: Mapping[str, Any]
    content_sha256: str
    utility_score: float | None = None
    supersedes_review_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("review_id", "review_subject_id", "outcome_set_id", "review_policy_id"):
            _text(name, getattr(self, name))
        if self.review_id != review_id_for(
            self.review_subject_id, self.outcome_set_id, self.review_policy_id
        ):
            raise ValueError(
                "review_id 必须由 (review_subject_id, outcome_set_id, review_policy_id) 派生"
                " —— 用 review_id_for() 生成，不要手拼"
            )
        if self.review_status not in _REVIEW_STATUS:
            raise ValueError(f"unsupported review_status {self.review_status!r}")
        if self.review_label not in _REVIEW_LABEL:
            raise ValueError(f"unsupported review_label {self.review_label!r}")
        if self.primary_horizon not in _HORIZONS:
            raise ValueError(f"unsupported primary_horizon {self.primary_horizon!r}")
        _sha("content_sha256", self.content_sha256)
        if self.utility_score is not None:
            score = float(self.utility_score)
            # NaN != NaN 是判 NaN 的标准写法；±inf 单独列，因为它们**等于自己**。
            if score != score or score in (float("inf"), float("-inf")):
                raise ValueError("utility_score 有值时必须是有限数（NaN/±inf 都不行）")
        object.__setattr__(self, "detail", deep_freeze(dict(self.detail)))
        canonical_json(self.detail)


@dataclass(frozen=True, slots=True)
class MeasurementRun:
    measurement_run_id: str
    measurement_policy_id: str
    as_of_cutoff: str
    random_seed: int
    code_version: str
    cohort_manifest_sha256: str

    def __post_init__(self) -> None:
        for name in ("measurement_run_id", "measurement_policy_id", "as_of_cutoff", "code_version"):
            _text(name, getattr(self, name))
        object.__setattr__(self, "as_of_cutoff", cn_iso(self.as_of_cutoff, name="as_of_cutoff"))
        _sha("cohort_manifest_sha256", self.cohort_manifest_sha256)
        expected_id = measurement_run_id_for(
            self.measurement_policy_id,
            self.as_of_cutoff,
            self.random_seed,
            self.code_version,
            self.cohort_manifest_sha256,
        )
        if self.measurement_run_id != expected_id:
            raise ValueError(
                "measurement_run_id 必须由 (policy, as_of_cutoff, seed, code_version, "
                "cohort_hash) 派生 —— 用 measurement_run_id_for() 生成，不要手拼。"
                "身份里带 cohort_hash 的意义是：换了样本就换 id，"
                "而不是让同一个 run id 指向两份不同的 cohort。"
            )


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    measurement_run_id: str
    analysis_key: str
    dimension: str
    groups: tuple[str, ...]
    target_id: str
    horizon: str
    metric_name: str
    n: Mapping[str, int]
    method: str
    result: Mapping[str, Any]
    content_sha256: str
    effect: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    underpowered: bool = False

    def __post_init__(self) -> None:
        for name in ("measurement_run_id", "analysis_key", "dimension", "target_id",
                     "metric_name", "method"):
            _text(name, getattr(self, name))
        if self.horizon not in _HORIZONS:
            raise ValueError(f"unsupported horizon {self.horizon!r}")
        if not self.groups or len(self.groups) != len(set(self.groups)):
            raise ValueError("groups must be non-empty and unique")
        clean_n = {str(k): int(v) for k, v in dict(self.n).items()}
        if set(clean_n) != set(self.groups) or any(v < 0 for v in clean_n.values()):
            raise ValueError("n must contain one non-negative count for every group")
        for name in ("effect", "ci_low", "ci_high", "p_value"):
            value = getattr(self, name)
            if value is not None:
                value = float(value)
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"{name} must be finite when present")
                if name == "p_value" and not 0.0 <= value <= 1.0:
                    raise ValueError("p_value must be in [0,1]")
        if self.ci_low is not None and self.ci_high is not None and self.ci_low > self.ci_high:
            raise ValueError("ci_low must be <= ci_high")
        _sha("content_sha256", self.content_sha256)
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "n", deep_freeze(clean_n))
        object.__setattr__(self, "result", deep_freeze(dict(self.result)))
        canonical_json(self.result)
