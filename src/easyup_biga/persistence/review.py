"""Phase 4 落库边界。

P4-1/P4-2 写 ReviewSubject；P4-4 激活 v29 的 outcome 两张表；P4-5 激活 v30 的
review_records；P4-6/P4-7 激活 v31 的 measurement_* 四张。
全部只追加，且在写边界上核对精确血缘。

🔴 **本文件有两处本仓库裁定，合并外部增量包时容易被整份覆盖掉**
（见 `docs/design/phase-4-review-measurement.md` §1.1）：
  1. `list_uncaptured_decision_records()` 按 **policy 三元组** join，不是只按 record_id
  2. `save_review_subject()` 的存在性查询同样按三元组
两处都不是风格问题：按 record_id 单独判的话，发布新 policy 之后补采会
**一条都不返回**，看起来像「没有欠账」。
⚠️ 已经被推平过**四次**（Batch 2/3/4/5 各一次）。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

from easyup_biga.domain import now_cn
from easyup_biga.timebase import cn_iso, parse_aware
from easyup_biga.review.contracts import (
    MeasurementResult,
    MeasurementRun,
    OutcomeMetric,
    OutcomeSet,
    ReviewRecord,
    ReviewSubject,
    canonical_json,
    content_sha256,
    measurement_cohort_sha256,
    measurement_result_payload,
    review_content_payload,
)
from easyup_biga.review.registry import (
    get_anchor_policy,
    get_measurement_policy,
    get_outcome_policy,
    get_review_policy,
    get_target_policy,
)

from .db import connect

__all__ = [
    "PHASE4_MIN_SCHEMA",
    "Phase4SchemaNotReady",
    "ReviewStoreConflict",
    "require_phase4_schema",
    "append_measurement_run_event",
    "create_measurement_run",
    "latest_outcome_set",
    "latest_outcome_set_as_of",
    "list_outcome_sets",
    "latest_review_record",
    "list_measurement_results",
    "list_measurement_runs",
    "list_measurement_run_subjects",
    "list_review_records",
    "list_review_subjects",
    "list_uncaptured_decision_records",
    "load_measurement_run",
    "latest_measurement_run",
    "retry_failed_measurement_run",
    "load_outcome_set",
    "load_review_record",
    "load_review_subject",
    "save_measurement_results",
    "save_outcome_bundle",
    "save_review_record",
    "save_review_subject",
]


#: Phase 4 的表从 v28 起建，全部齐了是 v31。
PHASE4_MIN_SCHEMA = 31


class Phase4SchemaNotReady(RuntimeError):
    """库还没迁到 Phase 4 需要的版本。

    🔴 单独一个类型，理由与 `StoreNotInitialised` 同源：**「还没装好」和
    「装坏了」不该报同一种脸色。** 不加这层的话，`biga-review status`
    在一个 v27 的库上吐的是

        sqlite3.OperationalError: no such table: review_subjects

    —— 它既没说清楚该做什么，也让人以为是代码 bug。实测 2026-09-26 生产库
    正是 v27（Phase 3 迁移跑过，Phase 4 的还没跑）。
    """


def require_phase4_schema(path: pathlib.Path | str | None = None) -> int:
    """确认库已迁到 v31，否则抛 `Phase4SchemaNotReady` 并指路。

    ⚠️ 判据是 `PRAGMA user_version`（**结构**），不是去 catch
    `OperationalError` 再看它的文案（**字符串形状** = L-13）。
    表名改一次，按文案判的那种写法就会静默失效。
    """
    with connect(path, readonly=True) as conn:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current < PHASE4_MIN_SCHEMA:
        raise Phase4SchemaNotReady(
            f"这个库的 schema 是 v{current}，Phase 4 需要 v{PHASE4_MIN_SCHEMA}。\n"
            "  迁移（只追加，不改 v1–v27 的历史表）：\n"
            "    python3 -c \"import sys; sys.path.insert(0,'src'); "
            "from easyup_biga.persistence import init_schema; print(init_schema())\"\n"
            "  ⚠️ 生产库先备份：cp data/biga.db data/biga.db.pre-v31.bak"
        )
    return current


class ReviewStoreConflict(RuntimeError):
    """An immutable Phase 4 identity already exists with different content."""


def _subject_row_to_dict(row) -> dict[str, Any]:
    out = dict(row)
    out["manifest"] = json.loads(out.pop("subject_manifest_json"))
    return out


def load_review_subject(
    review_subject_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM review_subjects WHERE review_subject_id=?", (review_subject_id,)
        ).fetchone()
    return None if row is None else _subject_row_to_dict(row)


def list_review_subjects(
    *, path: pathlib.Path | str | None = None, eligibility: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM review_subjects"
    args: tuple[Any, ...] = ()
    if eligibility is not None:
        sql += " WHERE eligibility=?"
        args = (eligibility,)
    sql += " ORDER BY decision_record_id"
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_subject_row_to_dict(row) for row in rows]


def list_uncaptured_decision_records(
    *,
    path: pathlib.Path | str | None = None,
    limit: int | None = None,
    target_policy_id: str,
    anchor_policy_id: str,
    from_record_id: int | None = None,
    decision_from: str | None = None,
    decision_to: str | None = None,
) -> list[dict[str, Any]]:
    """还没有在**这对 policy 下**冻结过 ReviewSubject 的 decision 行。

    🔴 「未捕获」是相对于 policy 对的，不是绝对的。唯一键是三元组
    （见 schema v28 的说明）⇒ 同一张卡在 AnchorPolicy v2 下**仍然未捕获**，
    而按 record_id 单独 join 会把它判成「已经处理过」并静默跳过。
    那是最难发现的一类错：补采一个新 policy 时它一条都不返回，看起来像「没有欠账」。

    `from_record_id` / `decision_from` / `decision_to` 是 P4-8 的 backfill 范围限定 ——
    它们只**缩小**范围，不改变「未捕获」的判据。
    """
    get_target_policy(target_policy_id)
    get_anchor_policy(anchor_policy_id)
    sql = (
        "SELECT d.record_id,d.decision_id,d.replay_of,d.status,d.model_ref,d.card_json,"
        "d.generated_at,d.run_id,d.evidence_set_id,d.created_at "
        "FROM decision_records d LEFT JOIN review_subjects s "
        "ON s.decision_record_id=d.record_id AND s.target_policy_id=? "
        "AND s.anchor_policy_id=? WHERE s.review_subject_id IS NULL"
    )
    args: list[Any] = [target_policy_id, anchor_policy_id]
    if from_record_id is not None:
        if int(from_record_id) <= 0:
            raise ValueError("from_record_id must be positive")
        sql += " AND d.record_id>=?"
        args.append(int(from_record_id))
    lower = None if decision_from is None else parse_aware(decision_from, name="decision_from")
    upper = None if decision_to is None else parse_aware(decision_to, name="decision_to")
    if lower is not None and upper is not None and upper < lower:
        raise ValueError("decision_to must be >= decision_from")
    sql += " ORDER BY d.record_id"
    if limit is not None and int(limit) <= 0:
        raise ValueError("limit must be positive")
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if lower is not None or upper is not None:
            try:
                generated = parse_aware(str(item["generated_at"]), name="generated_at")
            except ValueError:
                continue
            if lower is not None and generated < lower:
                continue
            if upper is not None and generated > upper:
                continue
        out.append(item)
        if limit is not None and len(out) >= int(limit):
            break
    return out


def _same_subject(existing: dict[str, Any], subject: ReviewSubject) -> bool:
    expected = subject.to_dict()
    for key in (
        "review_subject_id", "decision_record_id", "decision_id", "subject_type",
        "decision_at", "run_id", "evidence_set_id", "card_status", "model_ref",
        "target_policy_id", "anchor_policy_id", "source_card_sha256", "eligibility",
        "exclusion_code",
    ):
        if key == "decision_at":
            try:
                if parse_aware(str(existing.get(key)), name="stored decision_at") != parse_aware(
                    str(expected.get(key)), name="candidate decision_at"
                ):
                    return False
            except ValueError:
                if existing.get(key) != expected.get(key):
                    return False
        elif existing.get(key) != expected.get(key):
            return False
    return canonical_json(existing.get("manifest")) == canonical_json(expected.get("manifest"))


def save_review_subject(
    subject: ReviewSubject, *, path: pathlib.Path | str | None = None
) -> str:
    """Persist exactly one subject for a DecisionCard record; retries are idempotent.

    The write boundary re-reads the source row and verifies its exact card bytes.  A caller
    cannot forge a ReviewSubject for a different Card or silently recapture a mutated Card.
    """
    if not isinstance(subject, ReviewSubject):
        raise TypeError(f"save_review_subject expects ReviewSubject, got {type(subject).__name__}")
    get_target_policy(subject.target_policy_id)
    get_anchor_policy(subject.anchor_policy_id)
    manifest_json = canonical_json(subject.manifest)

    try:
        with connect(path) as conn:
            source = conn.execute(
                "SELECT record_id,decision_id,replay_of,status,model_ref,card_json,generated_at,"
                "run_id,evidence_set_id FROM decision_records WHERE record_id=?",
                (subject.decision_record_id,),
            ).fetchone()
            if source is None:
                raise ValueError(
                    f"decision_record_id {subject.decision_record_id} does not exist"
                )
            expected_type = "REPLAY_CARD" if source["replay_of"] is not None else "ONLINE_CARD"
            checks = {
                "decision_id": source["decision_id"],
                "subject_type": expected_type,
                "decision_at": (
                    cn_iso(str(source["generated_at"]), name="source generated_at")
                    if subject.eligibility == "ELIGIBLE" else str(source["generated_at"])
                ),
                "run_id": source["run_id"],
                "evidence_set_id": source["evidence_set_id"],
                "card_status": source["status"],
                "model_ref": source["model_ref"],
                "source_card_sha256": content_sha256(str(source["card_json"])),
            }
            for name, expected in checks.items():
                if getattr(subject, name) != expected:
                    raise ValueError(
                        f"ReviewSubject {name} disagrees with decision_records: "
                        f"{getattr(subject, name)!r} != {expected!r}"
                    )
            existing_row = conn.execute(
                "SELECT * FROM review_subjects WHERE decision_record_id=? "
                "AND target_policy_id=? AND anchor_policy_id=?",
                (
                    subject.decision_record_id,
                    subject.target_policy_id,
                    subject.anchor_policy_id,
                ),
            ).fetchone()
            if existing_row is not None:
                existing = _subject_row_to_dict(existing_row)
                if _same_subject(existing, subject):
                    return str(existing["review_subject_id"])
                raise ReviewStoreConflict(
                    f"decision_record_id {subject.decision_record_id} 在 policy 对 "
                    f"({subject.target_policy_id}, {subject.anchor_policy_id}) 下已冻结过，"
                    "且内容不同 —— 原卡被改写过，或身份被手拼出来了。"
                    "查 review_subjects 里那一行的 source_card_sha256 与 decision_records 对不对得上。"
                )
            conn.execute(
                "INSERT INTO review_subjects "
                "(review_subject_id,decision_record_id,decision_id,subject_type,decision_at,run_id,"
                "evidence_set_id,card_status,model_ref,target_policy_id,anchor_policy_id,"
                "subject_manifest_json,source_card_sha256,eligibility,exclusion_code,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    subject.review_subject_id, subject.decision_record_id, subject.decision_id,
                    subject.subject_type, subject.decision_at, subject.run_id,
                    subject.evidence_set_id, subject.card_status, subject.model_ref,
                    subject.target_policy_id, subject.anchor_policy_id, manifest_json,
                    subject.source_card_sha256, subject.eligibility, subject.exclusion_code,
                    now_cn().isoformat(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ReviewStoreConflict("ReviewSubject identity conflict") from exc
    return subject.review_subject_id


def _metric_row_to_dict(row) -> dict[str, Any]:
    out = dict(row)
    out["source_snapshot_ids"] = tuple(json.loads(out.pop("source_snapshot_ids_json")))
    return out


def _outcome_row_to_dict(row, metrics) -> dict[str, Any]:
    out = dict(row)
    out["manifest"] = json.loads(out.pop("manifest_json"))
    out["metrics"] = [_metric_row_to_dict(item) for item in metrics]
    return out


def load_outcome_set(
    outcome_set_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM outcome_sets WHERE outcome_set_id=?", (outcome_set_id,)
        ).fetchone()
        if row is None:
            return None
        metrics = conn.execute(
            "SELECT * FROM outcome_metrics WHERE outcome_set_id=? "
            "ORDER BY target_id,horizon,metric_name,outcome_metric_id",
            (outcome_set_id,),
        ).fetchall()
    return _outcome_row_to_dict(row, metrics)


def list_outcome_sets(
    review_subject_id: str,
    *,
    path: pathlib.Path | str | None = None,
    outcome_policy_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT outcome_set_id FROM outcome_sets WHERE review_subject_id=?"
    args: list[Any] = [review_subject_id]
    if outcome_policy_id is not None:
        sql += " AND outcome_policy_id=?"
        args.append(outcome_policy_id)
    sql += " ORDER BY data_version,outcome_set_id"
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [
        item
        for row in rows
        if (item := load_outcome_set(str(row["outcome_set_id"]), path=path)) is not None
    ]


def latest_outcome_set(
    review_subject_id: str,
    outcome_policy_id: str,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT outcome_set_id FROM outcome_sets WHERE review_subject_id=? "
            "AND outcome_policy_id=? ORDER BY data_version DESC,outcome_set_id DESC LIMIT 1",
            (review_subject_id, outcome_policy_id),
        ).fetchone()
    return None if row is None else load_outcome_set(str(row["outcome_set_id"]), path=path)


def _outcome_payload(outcome: OutcomeSet, metrics: tuple[OutcomeMetric, ...]) -> dict[str, Any]:
    return {
        "review_subject_id": outcome.review_subject_id,
        "outcome_policy_id": outcome.outcome_policy_id,
        "anchor_trade_date": outcome.anchor_trade_date,
        "status": outcome.status,
        "manifest": dict(outcome.manifest),
        "metrics": [
            {
                "target_id": metric.target_id,
                "horizon": metric.horizon,
                "observed_trade_date": metric.observed_trade_date,
                "metric_name": metric.metric_name,
                "value": float(metric.value),
                "unit": metric.unit,
                "source_snapshot_ids": list(metric.source_snapshot_ids),
                "coverage_ratio": float(metric.coverage_ratio),
            }
            for metric in sorted(metrics, key=lambda x: (x.target_id, x.horizon, x.metric_name))
        ],
    }


def save_outcome_bundle(
    outcome: OutcomeSet,
    metrics: tuple[OutcomeMetric, ...],
    *,
    path: pathlib.Path | str | None = None,
) -> str:
    """Atomically append one OutcomeSet plus all tall metrics.

    The boundary verifies sequential revision lineage, policy identity, the candidate hash,
    and every referenced DatasetSnapshot.  A metric cannot claim a quarantined/future
    snapshot as evidence.
    """
    if not isinstance(outcome, OutcomeSet):
        raise TypeError(f"save_outcome_bundle expects OutcomeSet, got {type(outcome).__name__}")
    if any(not isinstance(item, OutcomeMetric) for item in metrics):
        raise TypeError("metrics must contain only OutcomeMetric")
    outcome_policy = get_outcome_policy(outcome.outcome_policy_id)
    expected_hash = content_sha256(_outcome_payload(outcome, metrics))
    if outcome.content_sha256 != expected_hash:
        raise ValueError("OutcomeSet content_sha256 does not match canonical payload")

    try:
        with connect(path) as conn:
            subject = conn.execute(
                "SELECT review_subject_id,eligibility,target_policy_id FROM review_subjects "
                "WHERE review_subject_id=?",
                (outcome.review_subject_id,),
            ).fetchone()
            if subject is None:
                raise ValueError(f"unknown review_subject_id {outcome.review_subject_id}")
            if subject["eligibility"] != "ELIGIBLE":
                raise ValueError("cannot persist OutcomeSet for an INELIGIBLE ReviewSubject")
            target_policy = get_target_policy(str(subject["target_policy_id"]))
            allowed_targets = {item.target_id for item in target_policy.targets}

            expected_algorithm = outcome_policy.metric_algorithm_ref()
            manifest_algorithm = dict(outcome.manifest).get("metric_algorithm")
            if manifest_algorithm != expected_algorithm:
                raise ValueError(
                    "OutcomeSet metric_algorithm must exactly match the registered OutcomePolicy "
                    "formula fingerprint"
                )

            # Global first-write freeze: one OutcomePolicy id may never produce rows with two
            # different formulas, even across different ReviewSubjects.  Publishing a changed
            # formula therefore requires a new OutcomePolicy id (v2) while v1 remains replayable.
            frozen_row = conn.execute(
                "SELECT manifest_json FROM outcome_sets WHERE outcome_policy_id=? "
                "ORDER BY created_at ASC,outcome_set_id ASC LIMIT 1",
                (outcome.outcome_policy_id,),
            ).fetchone()
            if frozen_row is not None:
                frozen_manifest = json.loads(str(frozen_row["manifest_json"]))
                frozen_algorithm = frozen_manifest.get("metric_algorithm")
                if frozen_algorithm is None:
                    raise ValueError(
                        "existing OutcomePolicy rows have no formula fingerprint; "
                        "publish a migration/new Policy instead of mixing formulas"
                    )
                if frozen_algorithm != expected_algorithm:
                    raise ValueError(
                        "OutcomePolicy formula is already frozen by persisted OutcomeSets; "
                        "publish a new OutcomePolicy id"
                    )

            latest = conn.execute(
                "SELECT outcome_set_id,data_version,knowledge_cutoff,status,manifest_json "
                "FROM outcome_sets WHERE review_subject_id=? AND outcome_policy_id=? "
                "ORDER BY data_version DESC LIMIT 1",
                (outcome.review_subject_id, outcome.outcome_policy_id),
            ).fetchone()
            canonical_cutoff = cn_iso(outcome.knowledge_cutoff, name="OutcomeSet knowledge_cutoff")
            if latest is None:
                if outcome.data_version != 1 or outcome.supersedes_outcome_set_id is not None:
                    raise ValueError("first OutcomeSet must be data_version=1 with no supersedes")
            else:
                expected_version = int(latest["data_version"]) + 1
                if outcome.data_version != expected_version:
                    raise ValueError(
                        f"OutcomeSet data_version must be sequential: expected {expected_version}"
                    )
                if outcome.supersedes_outcome_set_id != str(latest["outcome_set_id"]):
                    raise ValueError("OutcomeSet must supersede the current latest revision")
                if parse_aware(canonical_cutoff, name="new OutcomeSet knowledge_cutoff") < parse_aware(
                    str(latest["knowledge_cutoff"]), name="previous OutcomeSet knowledge_cutoff"
                ):
                    raise ValueError("OutcomeSet knowledge_cutoff cannot move backwards")
                previous_manifest = json.loads(str(latest["manifest_json"]))
                old_horizons = tuple(previous_manifest.get("matured_horizons") or ())
                new_horizons = tuple(dict(outcome.manifest).get("matured_horizons") or ())
                if not set(old_horizons).issubset(set(new_horizons)):
                    raise ValueError("OutcomeSet matured horizons cannot regress")
                if str(latest["status"]) == "COMPLETE" and outcome.status == "PARTIAL":
                    raise ValueError("a COMPLETE OutcomeSet cannot be superseded by PARTIAL")

            seen_metric_keys: set[tuple[str, str, str]] = set()
            source_ids: set[str] = set()
            for metric in metrics:
                key = (metric.target_id, metric.horizon, metric.metric_name)
                if key in seen_metric_keys:
                    raise ValueError(f"duplicate OutcomeMetric key: {key}")
                seen_metric_keys.add(key)
                if metric.target_id not in allowed_targets:
                    raise ValueError(f"OutcomeMetric target not in frozen target policy: {metric.target_id}")
                if metric.metric_name not in outcome_policy.metric_ids:
                    raise ValueError(
                        f"OutcomeMetric {metric.metric_name!r} is not allowed by "
                        f"{outcome_policy.policy_id}"
                    )
                source_ids.update(metric.source_snapshot_ids)
            if source_ids:
                q = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    "SELECT snapshot_id,status,knowledge_cutoff FROM dataset_snapshots "
                    f"WHERE snapshot_id IN ({q})",
                    tuple(sorted(source_ids)),
                ).fetchall()
                found = {str(row["snapshot_id"]): row for row in rows}
                missing = sorted(source_ids - set(found))
                if missing:
                    raise ValueError(f"OutcomeMetric references missing snapshots: {missing}")
                for snapshot_id, row in found.items():
                    if str(row["status"]) != "COMPLETE":
                        raise ValueError(f"OutcomeMetric snapshot {snapshot_id} is not COMPLETE")
                    if parse_aware(
                        str(row["knowledge_cutoff"]), name="snapshot knowledge_cutoff"
                    ) > parse_aware(canonical_cutoff, name="OutcomeSet knowledge_cutoff"):
                        raise ValueError(f"OutcomeMetric snapshot {snapshot_id} is from the future")

            if outcome.status == "QUARANTINED":
                blocking = dict(outcome.manifest).get("blocking_reason") or {}
                blocking_id = str(blocking.get("snapshot_id") or "")
                if not blocking_id:
                    raise ValueError("QUARANTINED OutcomeSet requires blocking snapshot lineage")
                blocking_row = conn.execute(
                    "SELECT status,knowledge_cutoff FROM dataset_snapshots WHERE snapshot_id=?",
                    (blocking_id,),
                ).fetchone()
                if blocking_row is None:
                    raise ValueError(f"blocking snapshot does not exist: {blocking_id}")
                if str(blocking_row["status"]) == "COMPLETE":
                    raise ValueError("QUARANTINED OutcomeSet blocking snapshot cannot be COMPLETE")
                if parse_aware(
                    str(blocking_row["knowledge_cutoff"]), name="blocking snapshot knowledge_cutoff"
                ) > parse_aware(canonical_cutoff, name="OutcomeSet knowledge_cutoff"):
                    raise ValueError("blocking snapshot is from the future")

            conn.execute(
                "INSERT INTO outcome_sets "
                "(outcome_set_id,review_subject_id,outcome_policy_id,anchor_trade_date,"
                "knowledge_cutoff,status,data_version,manifest_json,content_sha256,"
                "supersedes_outcome_set_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    outcome.outcome_set_id,
                    outcome.review_subject_id,
                    outcome.outcome_policy_id,
                    outcome.anchor_trade_date,
                    canonical_cutoff,
                    outcome.status,
                    outcome.data_version,
                    canonical_json(outcome.manifest),
                    outcome.content_sha256,
                    outcome.supersedes_outcome_set_id,
                    now_cn().isoformat(),
                ),
            )
            for metric in metrics:
                conn.execute(
                    "INSERT INTO outcome_metrics "
                    "(outcome_set_id,target_id,horizon,observed_trade_date,metric_name,value,unit,"
                    "source_snapshot_ids_json,coverage_ratio,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        outcome.outcome_set_id,
                        metric.target_id,
                        metric.horizon,
                        metric.observed_trade_date,
                        metric.metric_name,
                        float(metric.value),
                        metric.unit,
                        canonical_json(list(metric.source_snapshot_ids)),
                        float(metric.coverage_ratio),
                        now_cn().isoformat(),
                    ),
                )
    except sqlite3.IntegrityError as exc:
        raise ReviewStoreConflict("OutcomeSet identity/version conflict") from exc
    return outcome.outcome_set_id


def _review_row_to_dict(row) -> dict[str, Any]:
    out = dict(row)
    out["detail"] = json.loads(out.pop("detail_json"))
    return out


def load_review_record(
    review_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM review_records WHERE review_id=?", (review_id,)
        ).fetchone()
    return None if row is None else _review_row_to_dict(row)


def list_review_records(
    review_subject_id: str,
    *,
    path: pathlib.Path | str | None = None,
    review_policy_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.review_id FROM review_records r "
        "JOIN outcome_sets o ON o.outcome_set_id=r.outcome_set_id "
        "WHERE r.review_subject_id=?"
    )
    args: list[Any] = [review_subject_id]
    if review_policy_id is not None:
        sql += " AND r.review_policy_id=?"
        args.append(review_policy_id)
    sql += " ORDER BY o.data_version,r.review_id"
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [
        item
        for row in rows
        if (item := load_review_record(str(row["review_id"]), path=path)) is not None
    ]


def latest_review_record(
    review_subject_id: str,
    review_policy_id: str,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT r.review_id FROM review_records r "
            "JOIN outcome_sets o ON o.outcome_set_id=r.outcome_set_id "
            "WHERE r.review_subject_id=? AND r.review_policy_id=? "
            "ORDER BY o.data_version DESC,r.review_id DESC LIMIT 1",
            (review_subject_id, review_policy_id),
        ).fetchone()
    return None if row is None else load_review_record(str(row["review_id"]), path=path)


def _primary_metric_row(
    conn, outcome_set_id: str, target_id: str, horizon: str, metric_name: str
):
    return conn.execute(
        "SELECT value,coverage_ratio FROM outcome_metrics WHERE outcome_set_id=? "
        "AND target_id=? AND horizon=? AND metric_name=?",
        (outcome_set_id, target_id, horizon, metric_name),
    ).fetchone()


def save_review_record(
    review: ReviewRecord, *, path: pathlib.Path | str | None = None
) -> str:
    """Persist one deterministic per-card review with exact OutcomeSet lineage.

    Review is a human-readable projection, never the statistical authority.  The write
    boundary therefore verifies the referenced subject/outcome/policy, canonical hash,
    primary H5 metric availability for COMPLETE reviews, and append-only supersession.
    """
    if not isinstance(review, ReviewRecord):
        raise TypeError(f"save_review_record expects ReviewRecord, got {type(review).__name__}")
    policy = get_review_policy(review.review_policy_id)
    if review.primary_horizon != policy.primary_horizon:
        raise ValueError("ReviewRecord primary_horizon disagrees with ReviewPolicy")
    expected_hash = content_sha256(
        review_content_payload(
            review_subject_id=review.review_subject_id,
            outcome_set_id=review.outcome_set_id,
            review_policy_id=review.review_policy_id,
            review_status=review.review_status,
            review_label=review.review_label,
            utility_score=review.utility_score,
            primary_horizon=review.primary_horizon,
            detail=review.detail,
            supersedes_review_id=review.supersedes_review_id,
        )
    )
    if review.content_sha256 != expected_hash:
        raise ValueError("ReviewRecord content_sha256 does not match canonical payload")

    try:
        with connect(path) as conn:
            existing = conn.execute(
                "SELECT * FROM review_records WHERE review_id=?", (review.review_id,)
            ).fetchone()
            if existing is not None:
                current = _review_row_to_dict(existing)
                if current["content_sha256"] == review.content_sha256:
                    return review.review_id
                raise ReviewStoreConflict(f"review_id {review.review_id} already exists with different content")

            subject = conn.execute(
                "SELECT eligibility FROM review_subjects WHERE review_subject_id=?",
                (review.review_subject_id,),
            ).fetchone()
            if subject is None:
                raise ValueError(f"unknown review_subject_id {review.review_subject_id}")
            if str(subject["eligibility"]) != "ELIGIBLE":
                raise ValueError("cannot persist ReviewRecord for an INELIGIBLE ReviewSubject")

            outcome = conn.execute(
                "SELECT review_subject_id,status,data_version FROM outcome_sets WHERE outcome_set_id=?",
                (review.outcome_set_id,),
            ).fetchone()
            if outcome is None:
                raise ValueError(f"unknown outcome_set_id {review.outcome_set_id}")
            if str(outcome["review_subject_id"]) != review.review_subject_id:
                raise ValueError("ReviewRecord outcome_set_id belongs to a different subject")
            if str(outcome["status"]) == "QUARANTINED":
                if review.review_status != "UNSCORABLE" or review.review_label != "UNSCORABLE":
                    raise ValueError("QUARANTINED OutcomeSet can only produce an UNSCORABLE ReviewRecord")
                if review.utility_score is not None:
                    raise ValueError("QUARANTINED OutcomeSet ReviewRecord cannot have utility_score")

            if review.review_status == "COMPLETE":
                primary_return = _primary_metric_row(
                    conn, review.outcome_set_id, policy.primary_target_id, policy.primary_horizon, "return"
                )
                if primary_return is None:
                    raise ValueError("COMPLETE ReviewRecord requires primary H5 return metric")
                if review.review_label in {"SUPPORTS", "OPPOSES", "FLAT"}:
                    if review.utility_score is None:
                        raise ValueError("directional COMPLETE ReviewRecord requires utility_score")
                elif review.review_label in {"ABSTAIN", "ABSTAIN_SYSTEM"}:
                    if review.utility_score is not None:
                        raise ValueError("abstention ReviewRecord cannot have utility_score")
                else:
                    raise ValueError("COMPLETE ReviewRecord has invalid review_label")
            else:
                if review.review_label != "UNSCORABLE":
                    raise ValueError("non-COMPLETE ReviewRecord must use UNSCORABLE label")
                if review.utility_score is not None:
                    raise ValueError("non-COMPLETE ReviewRecord cannot have utility_score")

            latest = conn.execute(
                "SELECT r.review_id,o.data_version FROM review_records r "
                "JOIN outcome_sets o ON o.outcome_set_id=r.outcome_set_id "
                "WHERE r.review_subject_id=? AND r.review_policy_id=? "
                "ORDER BY o.data_version DESC,r.review_id DESC LIMIT 1",
                (review.review_subject_id, review.review_policy_id),
            ).fetchone()
            if latest is None:
                if review.supersedes_review_id is not None:
                    raise ValueError("first ReviewRecord cannot supersede another review")
            else:
                if int(outcome["data_version"]) <= int(latest["data_version"]):
                    raise ValueError("ReviewRecord cannot be appended for an older OutcomeSet revision")
                if review.supersedes_review_id != str(latest["review_id"]):
                    raise ValueError("ReviewRecord must supersede the latest review revision")

            conn.execute(
                "INSERT INTO review_records "
                "(review_id,review_subject_id,outcome_set_id,review_policy_id,review_status,"
                "review_label,utility_score,primary_horizon,detail_json,content_sha256,"
                "supersedes_review_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    review.review_id, review.review_subject_id, review.outcome_set_id,
                    review.review_policy_id, review.review_status, review.review_label,
                    review.utility_score, review.primary_horizon, canonical_json(review.detail),
                    review.content_sha256, review.supersedes_review_id, now_cn().isoformat(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ReviewStoreConflict("ReviewRecord identity/lineage conflict") from exc
    return review.review_id


# ---------------------------------------------------------------------------
# P4-6/P4-7 Measurement persistence
# ---------------------------------------------------------------------------

def latest_outcome_set_as_of(
    review_subject_id: str,
    outcome_policy_id: str,
    as_of_cutoff: str,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    """Latest immutable OutcomeSet visible at a Beijing-time aware cutoff.

    Legacy rows may carry another explicit UTC offset, so selection parses timestamps instead
    of relying on SQLite's lexical TEXT ordering.
    """
    cutoff = parse_aware(as_of_cutoff, name="as_of_cutoff")
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT outcome_set_id,knowledge_cutoff,data_version,created_at FROM outcome_sets "
            "WHERE review_subject_id=? AND outcome_policy_id=?",
            (review_subject_id, outcome_policy_id),
        ).fetchall()
    visible = [
        row for row in rows
        if parse_aware(str(row["knowledge_cutoff"]), name="outcome knowledge_cutoff") <= cutoff
    ]
    if not visible:
        return None
    row = max(
        visible,
        key=lambda item: (
            parse_aware(str(item["knowledge_cutoff"]), name="outcome knowledge_cutoff"),
            int(item["data_version"]),
            parse_aware(str(item["created_at"]), name="outcome created_at"),
            str(item["outcome_set_id"]),
        ),
    )
    return load_outcome_set(str(row["outcome_set_id"]), path=path)


def _measurement_run_row(row, events) -> dict[str, Any]:
    out = dict(row)
    out["events"] = []
    for event in events:
        item = dict(event)
        item["detail"] = None if item.get("detail_json") is None else json.loads(item.pop("detail_json"))
        out["events"].append(item)
    return out


def load_measurement_run(
    measurement_run_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM measurement_runs WHERE measurement_run_id=?", (measurement_run_id,)
        ).fetchone()
        if row is None:
            return None
        events = conn.execute(
            "SELECT * FROM measurement_run_events WHERE measurement_run_id=? ORDER BY seq",
            (measurement_run_id,),
        ).fetchall()
    return _measurement_run_row(row, events)


def list_measurement_runs(
    *, path: pathlib.Path | str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """Newest-first MeasurementRun summaries, each with its immutable event history."""
    sql = "SELECT measurement_run_id FROM measurement_runs ORDER BY created_at DESC,measurement_run_id DESC"
    args: tuple[Any, ...] = ()
    if limit is not None:
        if int(limit) <= 0:
            raise ValueError("limit must be positive")
        sql += " LIMIT ?"
        args = (int(limit),)
    with connect(path, readonly=True) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        item for row in rows
        if (item := load_measurement_run(str(row["measurement_run_id"]), path=path)) is not None
    ]


def latest_measurement_run(
    *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """Return the run with the latest business as-of cutoff, not the last inserted row."""
    rows = list_measurement_runs(path=path)
    if not rows:
        return None
    return max(
        rows,
        key=lambda item: (
            parse_aware(str(item["as_of_cutoff"]), name="measurement as_of_cutoff"),
            parse_aware(str(item["created_at"]), name="measurement created_at"),
            str(item["measurement_run_id"]),
        ),
    )


def list_measurement_run_subjects(
    measurement_run_id: str, *, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM measurement_run_subjects WHERE measurement_run_id=? "
            "ORDER BY COALESCE(anchor_trade_date,''),review_subject_id",
            (measurement_run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_measurement_run(
    run: MeasurementRun,
    cohort_entries: list[dict[str, Any]],
    *,
    path: pathlib.Path | str | None = None,
) -> bool:
    """Atomically persist run identity, CREATED/COHORT_FROZEN events and frozen cohort."""
    if not isinstance(run, MeasurementRun):
        raise TypeError(f"create_measurement_run expects MeasurementRun, got {type(run).__name__}")
    policy = get_measurement_policy(run.measurement_policy_id)
    if int(run.random_seed) != int(policy.random_seed):
        raise ValueError("MeasurementRun random_seed disagrees with MeasurementPolicy")
    expected_hash = measurement_cohort_sha256(cohort_entries)
    if run.cohort_manifest_sha256 != expected_hash:
        raise ValueError("MeasurementRun cohort_manifest_sha256 does not match frozen cohort")

    try:
        with connect(path) as conn:
            existing = conn.execute(
                "SELECT * FROM measurement_runs WHERE measurement_run_id=?",
                (run.measurement_run_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    str(existing["measurement_policy_id"]) == run.measurement_policy_id
                    and str(existing["as_of_cutoff"]) == run.as_of_cutoff
                    and int(existing["random_seed"]) == int(run.random_seed)
                    and str(existing["code_version"]) == run.code_version
                    and str(existing["cohort_manifest_sha256"]) == run.cohort_manifest_sha256
                )
                if not same:
                    raise ReviewStoreConflict(
                        f"measurement_run_id {run.measurement_run_id} already exists with different content"
                    )
                stored = conn.execute(
                    "SELECT review_subject_id,outcome_set_id,anchor_trade_date,analysis_group,"
                    "included,reason_code FROM measurement_run_subjects WHERE measurement_run_id=?",
                    (run.measurement_run_id,),
                ).fetchall()
                stored_entries = [dict(row) for row in stored]
                if measurement_cohort_sha256(stored_entries) != run.cohort_manifest_sha256:
                    raise ReviewStoreConflict("stored MeasurementRun cohort hash drift")
                return False

            conn.execute(
                "INSERT INTO measurement_runs "
                "(measurement_run_id,measurement_policy_id,as_of_cutoff,random_seed,code_version,"
                "cohort_manifest_sha256,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    run.measurement_run_id, run.measurement_policy_id, run.as_of_cutoff,
                    int(run.random_seed), run.code_version, run.cohort_manifest_sha256,
                    now_cn().isoformat(),
                ),
            )
            at = now_cn().isoformat()
            conn.execute(
                "INSERT INTO measurement_run_events "
                "(measurement_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
                (run.measurement_run_id, 1, None, "CREATED", at, None),
            )
            for item in sorted(cohort_entries, key=lambda x: str(x["review_subject_id"])):
                subject_id = str(item["review_subject_id"])
                subject = conn.execute(
                    "SELECT review_subject_id,anchor_policy_id,target_policy_id "
                    "FROM review_subjects WHERE review_subject_id=?",
                    (subject_id,),
                ).fetchone()
                if subject is None:
                    raise ValueError(f"measurement cohort references unknown subject {subject_id}")
                if str(subject["anchor_policy_id"]) != policy.subject_anchor_policy_id:
                    raise ValueError("measurement cohort subject anchor policy mismatch")
                if str(subject["target_policy_id"]) != policy.subject_target_policy_id:
                    raise ValueError("measurement cohort subject target policy mismatch")
                outcome_id = item.get("outcome_set_id")
                if outcome_id is not None:
                    outcome = conn.execute(
                        "SELECT review_subject_id,outcome_policy_id FROM outcome_sets "
                        "WHERE outcome_set_id=?",
                        (str(outcome_id),),
                    ).fetchone()
                    if outcome is None:
                        raise ValueError(f"measurement cohort references unknown OutcomeSet {outcome_id}")
                    if str(outcome["review_subject_id"]) != subject_id:
                        raise ValueError("measurement cohort OutcomeSet belongs to another subject")
                    if str(outcome["outcome_policy_id"]) != policy.outcome_policy_id:
                        raise ValueError("measurement cohort OutcomeSet policy mismatch")
                included = 1 if bool(item["included"]) else 0
                reason = item.get("reason_code")
                if included and reason is not None:
                    raise ValueError("included measurement subject cannot have reason_code")
                if not included and not reason:
                    raise ValueError("excluded measurement subject requires reason_code")
                conn.execute(
                    "INSERT INTO measurement_run_subjects "
                    "(measurement_run_id,review_subject_id,outcome_set_id,anchor_trade_date,"
                    "analysis_group,included,reason_code,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run.measurement_run_id, subject_id,
                        None if outcome_id is None else str(outcome_id),
                        item.get("anchor_trade_date"), item.get("analysis_group"),
                        included, reason, at,
                    ),
                )
            conn.execute(
                "INSERT INTO measurement_run_events "
                "(measurement_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
                (
                    run.measurement_run_id, 2, "CREATED", "COHORT_FROZEN", now_cn().isoformat(),
                    canonical_json({
                        "cohort_manifest_sha256": run.cohort_manifest_sha256,
                        "subjects": len(cohort_entries),
                        "included": sum(bool(item["included"]) for item in cohort_entries),
                    }),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ReviewStoreConflict("MeasurementRun identity/cohort conflict") from exc
    return True


_ALLOWED_MEASUREMENT_TRANSITIONS = {
    "COHORT_FROZEN": {"COMPUTED", "FAILED"},
    "COMPUTED": {"REPORTED", "COMPLETE", "FAILED"},
    "REPORTED": {"COMPLETE", "FAILED"},
    "CREATED": {"COHORT_FROZEN", "FAILED"},
}


def append_measurement_run_event(
    measurement_run_id: str,
    to_state: str,
    *,
    detail: dict[str, Any] | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """Append a legal state transition; terminal history is never rewritten."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT seq,to_state FROM measurement_run_events WHERE measurement_run_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (measurement_run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"MeasurementRun not found: {measurement_run_id}")
        current = str(row["to_state"])
        if current == to_state:
            return int(row["seq"])
        if current in {"COMPLETE", "FAILED"}:
            raise ValueError(f"MeasurementRun is terminal: {current}")
        if to_state not in _ALLOWED_MEASUREMENT_TRANSITIONS.get(current, set()):
            raise ValueError(f"illegal MeasurementRun transition {current} -> {to_state}")
        seq = int(row["seq"]) + 1
        conn.execute(
            "INSERT INTO measurement_run_events "
            "(measurement_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
            (
                measurement_run_id, seq, current, to_state, now_cn().isoformat(),
                None if detail is None else canonical_json(detail),
            ),
        )
    return seq


def retry_failed_measurement_run(
    measurement_run_id: str,
    *,
    detail: dict[str, Any] | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """Append a controlled retry edge from FAILED back to COHORT_FROZEN.

    The cohort and deterministic run identity remain frozen; only execution is retried.  This
    avoids manufacturing a second statistical run while keeping every failed attempt visible.
    """
    with connect(path) as conn:
        row = conn.execute(
            "SELECT seq,to_state FROM measurement_run_events WHERE measurement_run_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (measurement_run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"MeasurementRun not found: {measurement_run_id}")
        if str(row["to_state"]) != "FAILED":
            raise ValueError("only a FAILED MeasurementRun may be retried")
        seq = int(row["seq"]) + 1
        payload = {"retry": True, "previous_terminal_state": "FAILED"}
        if detail:
            payload.update(dict(detail))
        conn.execute(
            "INSERT INTO measurement_run_events "
            "(measurement_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
            (
                measurement_run_id, seq, "FAILED", "COHORT_FROZEN", now_cn().isoformat(),
                canonical_json(payload),
            ),
        )
    return seq


def _measurement_result_row(row) -> dict[str, Any]:
    out = dict(row)
    out["groups"] = tuple(json.loads(out.pop("groups_json")))
    out["n"] = json.loads(out.pop("n_json"))
    out["result"] = json.loads(out.pop("result_json"))
    out["underpowered"] = bool(out["underpowered"])
    return out


def list_measurement_results(
    measurement_run_id: str, *, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM measurement_results WHERE measurement_run_id=? ORDER BY analysis_key",
            (measurement_run_id,),
        ).fetchall()
    return [_measurement_result_row(row) for row in rows]


def save_measurement_results(
    measurement_run_id: str,
    results: tuple[MeasurementResult, ...],
    *,
    path: pathlib.Path | str | None = None,
) -> int:
    """Atomically append deterministic MeasurementResult rows for a frozen cohort."""
    if any(not isinstance(item, MeasurementResult) for item in results):
        raise TypeError("results must contain only MeasurementResult")
    if len({item.analysis_key for item in results}) != len(results):
        raise ValueError("duplicate analysis_key in MeasurementResult batch")
    try:
        with connect(path) as conn:
            run = conn.execute(
                "SELECT measurement_run_id FROM measurement_runs WHERE measurement_run_id=?",
                (measurement_run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"MeasurementRun not found: {measurement_run_id}")
            existing_rows = conn.execute(
                "SELECT analysis_key,content_sha256 FROM measurement_results WHERE measurement_run_id=?",
                (measurement_run_id,),
            ).fetchall()
            if existing_rows:
                existing = {str(row["analysis_key"]): str(row["content_sha256"]) for row in existing_rows}
                candidate = {item.analysis_key: item.content_sha256 for item in results}
                if existing == candidate:
                    return 0
                raise ReviewStoreConflict("MeasurementRun already has different result rows")

            latest = conn.execute(
                "SELECT to_state FROM measurement_run_events WHERE measurement_run_id=? "
                "ORDER BY seq DESC LIMIT 1",
                (measurement_run_id,),
            ).fetchone()
            if latest is None or str(latest["to_state"]) != "COHORT_FROZEN":
                raise ValueError("MeasurementResults may only be first written from COHORT_FROZEN")

            for item in sorted(results, key=lambda x: x.analysis_key):
                if item.measurement_run_id != measurement_run_id:
                    raise ValueError("MeasurementResult belongs to another MeasurementRun")
                expected = content_sha256(measurement_result_payload(
                    measurement_run_id=item.measurement_run_id,
                    analysis_key=item.analysis_key,
                    dimension=item.dimension,
                    groups=item.groups,
                    target_id=item.target_id,
                    horizon=item.horizon,
                    metric_name=item.metric_name,
                    n=item.n,
                    effect=item.effect,
                    ci_low=item.ci_low,
                    ci_high=item.ci_high,
                    p_value=item.p_value,
                    method=item.method,
                    underpowered=item.underpowered,
                    result=item.result,
                ))
                if expected != item.content_sha256:
                    raise ValueError(f"MeasurementResult hash mismatch: {item.analysis_key}")
                conn.execute(
                    "INSERT INTO measurement_results "
                    "(measurement_run_id,analysis_key,dimension,groups_json,target_id,horizon,"
                    "metric_name,n_json,effect,ci_low,ci_high,p_value,method,underpowered,"
                    "result_json,content_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        measurement_run_id, item.analysis_key, item.dimension,
                        canonical_json(list(item.groups)), item.target_id, item.horizon,
                        item.metric_name, canonical_json(dict(item.n)), item.effect,
                        item.ci_low, item.ci_high, item.p_value, item.method,
                        1 if item.underpowered else 0, canonical_json(item.result),
                        item.content_sha256, now_cn().isoformat(),
                    ),
                )
            seq_row = conn.execute(
                "SELECT seq FROM measurement_run_events WHERE measurement_run_id=? ORDER BY seq DESC LIMIT 1",
                (measurement_run_id,),
            ).fetchone()
            seq = int(seq_row["seq"]) + 1
            conn.execute(
                "INSERT INTO measurement_run_events "
                "(measurement_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
                (
                    measurement_run_id, seq, "COHORT_FROZEN", "COMPUTED", now_cn().isoformat(),
                    canonical_json({"result_count": len(results)}),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ReviewStoreConflict("MeasurementResult identity conflict") from exc
    return len(results)
