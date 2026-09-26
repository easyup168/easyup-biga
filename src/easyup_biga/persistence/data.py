"""Data Platform 的元数据落库 —— Data Run / Raw / Partition / Quality / Snapshot。

取自外部 P3-0/P3-1 实现包，**逐个函数审过**后合入，API 适配到本仓库的注册表
（`get_dataset` / `get_provider` / `datasets_of`），错误消息按「报错要指路」改写。

覆盖 / 不覆盖
-------------
- 覆盖：v23–v26 那七张表的写入与读回，以及写入前的引用完整性核对
- **不覆盖**：Parquet / Raw 文件本身的读写（P3-4）、质量策略的内容（P3-4）

🔴 为什么写入前要在**代码里**再核一遍引用完整性
------------------------------------------------
SQLite 的外键默认是关的，而这一层的错配大多不是「指向不存在的行」，是
「指向了存在但不该指的那一行」—— 快照引用了另一个 dataset 的分区、质量报告
挂在别的 run 上。这类错外键管不着，而它们**一旦落库就永久留在追加式存储里**。

⇒ `save_dataset_snapshot` 会逐条核对被引分区的 `dataset_id` /
  `partition_key_json` / `schema_version` / `data_version` 与快照自称的一致；
  `link_evidence_set_dataset` 只接受 `COMPLETE` 的快照。

⚠️ 本模块**依赖** `easyup_biga.data` 的注册表。这条依赖方向
（persistence → data）是有意的：注册表是纯声明、无 IO，而写库前必须先知道
「这个 dataset 是谁、它的 schema 第几版」。反向依赖才是要避免的。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

from easyup_biga.data import (
    DATA_RUN_INITIAL_STATE,
    DATA_RUN_LEGAL_TRANSITIONS,
    DataJobRun,
    DatasetPartition,
    DatasetSnapshot,
    DatasetStatus,
    DatasetLink,
    ProviderAttempt,
    QualityReport,
    RawArtifact,
    canonical_partition_key,
    get_dataset,
    get_provider,
    datasets_of,
)
from easyup_biga.domain import now_cn

from .db import assert_snapshot_linkable, connect


class DataRunTransitionError(RuntimeError):
    pass


class UnknownDataRun(RuntimeError):
    pass


class DataStoreConflict(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def partition_key_json(value: Any) -> str:
    """分区键 → 落库用的规范 JSON。**唯一口径。**

    🔴 公开它是因为读侧（血缘审计、演练）也要用同一个字符串去 `WHERE
    partition_key_json=?`。外部实现里读侧自己又 `json.dumps(...)` 了两遍 ——
    形状一样、参数一样，所以今天对得上；而它们对不上的那天，查询只会返回
    **零行**，被读成「这个分区没有修订链」，不是「键编码变了」。
    ⇒ 那是静默 fail-open（R-3），不是报错。
    """
    return _json(canonical_partition_key(value))


#: 历史私名，写侧 20 余处在用 —— 与 `partition_key_json` 是同一个函数，不是两套。
_partition_json = partition_key_json


def _check_partition_keys(dataset_id: str, partition_key: Any) -> None:
    """实际用的分区键，必须**恰好**是注册表声明的那几个。

    🔴 这条让 `DatasetDefinition.partition_keys` 从装饰品变成承重件。
    两版外部实现里它都只是被声明、从没被核对过 —— 于是
    `{"symbol": ..., "as_of": ...}` 和 `{"trade_date": ...}` 可以同时写进
    同一个 dataset，`UNIQUE(dataset_id, partition_key_json, data_version)`
    **不会报错**（键不同 ⇒ JSON 不同 ⇒ 两行），而它们描述的是同一片数据。
    这正是「同一份数据悄悄存了两份」的入口。
    """
    declared = set(get_dataset(dataset_id).partition_keys)
    actual = set(canonical_partition_key(partition_key))
    if actual != declared:
        raise ValueError(
            f"{dataset_id} 的分区键对不上注册表：声明 {sorted(declared)}，"
            f"实际 {sorted(actual)}。\n"
            f"  要改切片方式就改 data/registry.py 的 partition_keys，"
            f"不要在写入侧临时换一套键。")


def _latest_event(conn: sqlite3.Connection, data_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT seq,to_state FROM data_run_events WHERE data_run_id=? ORDER BY seq DESC LIMIT 1",
        (data_run_id,),
    ).fetchone()


def open_data_run(run: DataJobRun, *, path: pathlib.Path | str | None = None) -> str:
    get_dataset(run.dataset_id)
    _check_partition_keys(run.dataset_id, run.partition_key)
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO data_job_runs "
                "(data_run_id,job_id,dataset_id,partition_key_json,requested_data_version,trigger_id,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run.data_run_id, run.job_id, run.dataset_id, _partition_json(run.partition_key),
                 run.requested_data_version, run.trigger_id, run.created_at),
            )
            conn.execute(
                "INSERT INTO data_run_events "
                "(data_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
                (run.data_run_id, 1, None, DATA_RUN_INITIAL_STATE, now_cn().isoformat(), None),
            )
        except sqlite3.IntegrityError as exc:
            raise DataStoreConflict(f"data_run_id already exists: {run.data_run_id}") from exc
    return run.data_run_id


def transition_data_run(
    data_run_id: str,
    expected_state: str,
    next_state: str,
    *,
    detail: dict[str, Any] | None = None,
    path: pathlib.Path | str | None = None,
) -> str:
    if (expected_state, next_state) not in DATA_RUN_LEGAL_TRANSITIONS:
        raise DataRunTransitionError(f"illegal data run transition: {expected_state} -> {next_state}")
    with connect(path) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        row = _latest_event(conn, data_run_id)
        if row is None:
            raise UnknownDataRun(data_run_id)
        seq, current = int(row["seq"]), str(row["to_state"])
        if current != expected_state:
            raise DataRunTransitionError(
                f"CAS failed: data run is at {current}, expected {expected_state}"
            )
        try:
            conn.execute(
                "INSERT INTO data_run_events "
                "(data_run_id,seq,from_state,to_state,at,detail_json) VALUES (?,?,?,?,?,?)",
                (data_run_id, seq + 1, expected_state, next_state, now_cn().isoformat(),
                 _json(detail) if detail is not None else None),
            )
        except sqlite3.IntegrityError as exc:
            raise DataRunTransitionError("CAS failed: concurrent transition won") from exc
    return next_state


def data_run_state(data_run_id: str, *, path: pathlib.Path | str | None = None) -> str | None:
    with connect(path, readonly=True) as conn:
        row = _latest_event(conn, data_run_id)
    return str(row["to_state"]) if row else None


def data_run_events(data_run_id: str, *, path: pathlib.Path | str | None = None) -> list[dict[str, Any]]:
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT seq,from_state,to_state,at,detail_json FROM data_run_events "
            "WHERE data_run_id=? ORDER BY seq",
            (data_run_id,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        raw_detail = item.pop("detail_json")
        item["detail"] = json.loads(raw_detail) if raw_detail else None
        out.append(item)
    return out


def save_raw_artifact(artifact: RawArtifact, *, path: pathlib.Path | str | None = None) -> str:
    get_dataset(artifact.dataset_id)
    get_provider(artifact.provider_id)
    if artifact.dataset_id not in datasets_of(artifact.provider_id):
        raise ValueError(
            f"{artifact.provider_id} 没有被登记为 {artifact.dataset_id} 的数据源。\n"
            f"  它当前服务：{datasets_of(artifact.provider_id)}\n"
            f"  要新增就去 data/registry.py 给那个 dataset 加 provider 角色。")
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO raw_artifacts "
            "(artifact_id,dataset_id,provider_id,request_fingerprint,body_uri,body_sha256,size_bytes,"
            "content_type,compression,as_of,available_at,retrieved_at,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (artifact.artifact_id, artifact.dataset_id, artifact.provider_id,
             artifact.request_fingerprint, artifact.body_uri, artifact.body_sha256,
             artifact.size_bytes, artifact.content_type, artifact.compression, artifact.as_of,
             artifact.available_at, artifact.retrieved_at, now_cn().isoformat()),
        )
    return artifact.artifact_id


def load_raw_artifact(
    artifact_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """按 id 取一条原始工件的元数据（不读 body —— body 在文件面或 raw 表里）。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM raw_artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
    return None if row is None else dict(row)


def record_provider_attempt(attempt: ProviderAttempt, *, path: pathlib.Path | str | None = None) -> int:
    get_provider(attempt.provider_id)
    with connect(path) as conn:
        run = conn.execute(
            "SELECT dataset_id FROM data_job_runs WHERE data_run_id=?", (attempt.data_run_id,)
        ).fetchone()
        if run is None:
            raise UnknownDataRun(attempt.data_run_id)
        if run["dataset_id"] not in datasets_of(attempt.provider_id):
            raise ValueError(
                f"{attempt.provider_id} 没有被登记为 {run['dataset_id']} 的数据源 —— "
                f"它当前服务 {datasets_of(attempt.provider_id)}")
        cur = conn.execute(
            "INSERT INTO provider_attempts "
            "(data_run_id,provider_id,provider_role,attempt_no,status,started_at,finished_at,elapsed_ms,"
            "artifact_id,error_code,error_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (attempt.data_run_id, attempt.provider_id, attempt.role.value, attempt.attempt_no,
             attempt.status.value, attempt.started_at, attempt.finished_at, attempt.elapsed_ms,
             attempt.artifact_id, attempt.error_code, attempt.error_detail),
        )
    return int(cur.lastrowid)


def save_dataset_partition(
    partition: DatasetPartition, *, path: pathlib.Path | str | None = None
) -> str:
    definition = get_dataset(partition.dataset_id)
    _check_partition_keys(partition.dataset_id, partition.partition_key)
    if partition.schema_version != definition.schema_version:
        raise ValueError("partition schema_version does not match registry")
    get_provider(partition.provider_id)
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO dataset_partitions "
                "(partition_id,dataset_id,partition_key_json,schema_version,data_version,storage_format,"
                "storage_uri,content_sha256,row_count,provider_id,raw_artifact_id,"
                "supersedes_partition_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (partition.partition_id, partition.dataset_id, _partition_json(partition.partition_key),
                 partition.schema_version, partition.data_version, partition.storage_format,
                 partition.storage_uri, partition.content_sha256, partition.row_count,
                 partition.provider_id, partition.raw_artifact_id,
                 partition.supersedes_partition_id, now_cn().isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise DataStoreConflict("dataset partition identity/version conflict") from exc
    return partition.partition_id


def load_dataset_partition(
    partition_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """按 id 取一个分区。`partition_key` 已解回 dict —— 与 `load_dataset_snapshot` 同形。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM dataset_partitions WHERE partition_id=?", (partition_id,)
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["partition_key"] = json.loads(out.pop("partition_key_json"))
    return out


def save_quality_report(report: QualityReport, *, path: pathlib.Path | str | None = None) -> str:
    get_dataset(report.dataset_id)
    with connect(path) as conn:
        run = conn.execute(
            "SELECT dataset_id FROM data_job_runs WHERE data_run_id=?", (report.data_run_id,)
        ).fetchone()
        if run is None:
            raise UnknownDataRun(report.data_run_id)
        if run["dataset_id"] != report.dataset_id:
            raise ValueError("quality report dataset differs from data run")
        conn.execute(
            "INSERT INTO quality_reports "
            "(quality_report_id,data_run_id,dataset_id,partition_id,status,policy_id,metrics_json,"
            "issues_json,checked_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (report.quality_report_id, report.data_run_id, report.dataset_id, report.partition_id,
             report.status.value, report.policy_id, _json(dict(report.metrics)),
             _json([item.to_dict() for item in report.issues]),
             report.checked_at or now_cn().isoformat()),
        )
    return report.quality_report_id


def save_dataset_snapshot(
    snapshot: DatasetSnapshot, *, path: pathlib.Path | str | None = None
) -> str:
    definition = get_dataset(snapshot.dataset_id)
    _check_partition_keys(snapshot.dataset_id, snapshot.partition_key)
    if snapshot.schema_version != definition.schema_version:
        raise ValueError("snapshot schema_version does not match registry")
    key_json = _partition_json(snapshot.partition_key)
    with connect(path) as conn:
        quality = conn.execute(
            "SELECT dataset_id FROM quality_reports WHERE quality_report_id=?",
            (snapshot.quality_report_id,),
        ).fetchone()
        if quality is None or quality["dataset_id"] != snapshot.dataset_id:
            raise ValueError("snapshot quality report missing or belongs to another dataset")
        placeholders = ",".join("?" for _ in snapshot.partition_ids)
        rows = conn.execute(
            "SELECT partition_id,dataset_id,partition_key_json,schema_version,data_version "
            f"FROM dataset_partitions WHERE partition_id IN ({placeholders})",
            tuple(snapshot.partition_ids),
        ).fetchall()
        if len(rows) != len(snapshot.partition_ids):
            raise ValueError("snapshot references a missing partition")
        for row in rows:
            if (row["dataset_id"] != snapshot.dataset_id
                    or row["partition_key_json"] != key_json
                    or int(row["schema_version"]) != snapshot.schema_version
                    or int(row["data_version"]) != snapshot.data_version):
                raise ValueError("snapshot and partition lineage disagree")
        try:
            conn.execute(
                "INSERT INTO dataset_snapshots "
                "(snapshot_id,dataset_id,partition_key_json,as_of,knowledge_cutoff,status,schema_version,"
                "data_version,manifest_json,content_sha256,quality_report_id,supersedes_snapshot_id,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot.snapshot_id, snapshot.dataset_id, key_json, snapshot.as_of,
                 snapshot.knowledge_cutoff, snapshot.status.value, snapshot.schema_version,
                 snapshot.data_version, _json({"partition_ids": list(snapshot.partition_ids)}),
                 snapshot.content_sha256, snapshot.quality_report_id,
                 snapshot.supersedes_snapshot_id, now_cn().isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise DataStoreConflict("dataset snapshot identity/version conflict") from exc
    return snapshot.snapshot_id


def load_dataset_snapshot(snapshot_id: str, *, path: pathlib.Path | str | None = None) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute("SELECT * FROM dataset_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["partition_key"] = json.loads(out.pop("partition_key_json"))
    out["manifest"] = json.loads(out.pop("manifest_json"))
    return out


def find_dataset_partition(
    dataset_id: str,
    partition_key: dict[str, str],
    data_version: int,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    """按「逻辑分区 + 版本」找分区行。**给崩溃重试用的。**

    🔴 为什么需要它：发布是「登记分区 → 落物理文件 → 出快照」三步。
    崩在最后一步会留下「分区行 + 文件都在，但没有快照」的状态 ——
    这时重试会撞 `UNIQUE(dataset_id, partition_key_json, data_version)`，
    而那条冲突的**真实含义**是「上次跑到一半」，不是「这份数据已经发布过」。
    分不清这两者，运维就只剩「手工删库」一条路。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT partition_id FROM dataset_partitions "
            "WHERE dataset_id=? AND partition_key_json=? AND data_version=?",
            (dataset_id, _partition_json(partition_key), int(data_version)),
        ).fetchone()
    return load_dataset_partition(row["partition_id"], path=path) if row else None


def find_dataset_snapshot(
    dataset_id: str,
    partition_key: dict[str, str],
    *,
    status: DatasetStatus | None = None,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    get_dataset(dataset_id)
    sql = "SELECT snapshot_id FROM dataset_snapshots WHERE dataset_id=? AND partition_key_json=?"
    args: list[Any] = [dataset_id, _partition_json(partition_key)]
    if status is not None:
        sql += " AND status=?"
        args.append(status.value)
    sql += " ORDER BY data_version DESC LIMIT 1"
    with connect(path, readonly=True) as conn:
        row = conn.execute(sql, tuple(args)).fetchone()
    return load_dataset_snapshot(row["snapshot_id"], path=path) if row else None


def link_evidence_set_dataset(
    link: DatasetLink, *, path: pathlib.Path | str | None = None
) -> int:
    get_dataset(link.dataset_id)
    with connect(path) as conn:
        if conn.execute(
            "SELECT 1 FROM evidence_sets WHERE evidence_set_id=?", (link.evidence_set_id,)
        ).fetchone() is None:
            raise ValueError("evidence set does not exist")
        # 🔴 与 `save_evidence_set` 共用同一份判据（`db.assert_snapshot_linkable`）。
        #    这三条原本在两处各写一遍 —— 同一条判据两处实现就是 L-3。
        assert_snapshot_linkable(conn, link.dataset_id, link.snapshot_id)
        try:
            cur = conn.execute(
                "INSERT INTO evidence_set_datasets "
                "(evidence_set_id,dataset_id,snapshot_id,created_at) VALUES (?,?,?,?)",
                (link.evidence_set_id, link.dataset_id, link.snapshot_id, now_cn().isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise DataStoreConflict("evidence set already has this dataset") from exc
    return int(cur.lastrowid)


def list_evidence_set_datasets(
    evidence_set_id: str, *, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT dataset_id,snapshot_id,created_at FROM evidence_set_datasets "
            "WHERE evidence_set_id=? ORDER BY dataset_id",
            (evidence_set_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# ─────────────────────────── Security Master（P3-3）的写入与 point-in-time 查询
#
# 取自外部 P3-3 实现包。查询先选「在给定 knowledge_cutoff 下可见的最新 COMPLETE
# 快照」，再只读属于那个冻结分区的行 —— 这是 point-in-time 的关键：
# **不能用今天的名单回答昨天的问题**。
def save_security_master_records(
    partition_id: str,
    records: Any,
    *,
    path: pathlib.Path | str | None = None,
) -> int:
    """Persist one immutable normalized Security Master partition.

    ``records`` are intentionally duck-typed to avoid coupling the generic
    persistence module to a dataset-specific dataclass at import time.
    """
    items = tuple(records)
    if not items:
        raise ValueError("security master partition cannot be empty")
    with connect(path) as conn:
        partition = conn.execute(
            "SELECT dataset_id,provider_id,raw_artifact_id "
            "FROM dataset_partitions WHERE partition_id=?",
            (partition_id,),
        ).fetchone()
        if partition is None:
            raise ValueError("security master partition does not exist")
        if partition["dataset_id"] != "cn.security_master":
            raise ValueError("partition is not cn.security_master")
        if partition["raw_artifact_id"] is None:
            raise ValueError("security master partition requires raw artifact lineage")

        now = now_cn().isoformat()
        rows = []
        for item in items:
            if item.provider_id != partition["provider_id"]:
                raise ValueError("security record provider differs from partition provider")
            if item.raw_artifact_id != partition["raw_artifact_id"]:
                raise ValueError("security record raw lineage differs from partition")
            rows.append(
                (
                    partition_id,
                    item.instrument_id,
                    item.symbol,
                    item.exchange.value,
                    item.name,
                    item.security_type.value,
                    item.board.value,
                    item.list_date,
                    item.delist_date,
                    item.status.value,
                    item.available_at,
                    item.retrieved_at,
                    item.provider_id,
                    item.raw_artifact_id,
                    now,
                )
            )
        try:
            conn.executemany(
                "INSERT INTO fact_security_master "
                "(partition_id,instrument_id,symbol,exchange,name,security_type,board,list_date,"
                "delist_date,status,available_at,retrieved_at,provider_id,raw_artifact_id,"
                "created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        except sqlite3.IntegrityError as exc:
            raise DataStoreConflict(
                "security master partition contains duplicate identities"
            ) from exc
    return len(rows)


def find_security_master_snapshot_at(
    knowledge_cutoff: str,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    """Return the latest COMPLETE Security Master visible at ``knowledge_cutoff``."""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT snapshot_id FROM dataset_snapshots "
            "WHERE dataset_id='cn.security_master' AND status=? "
            "AND knowledge_cutoff<=? "
            "ORDER BY knowledge_cutoff DESC,data_version DESC,created_at DESC LIMIT 1",
            (DatasetStatus.COMPLETE.value, knowledge_cutoff),
        ).fetchone()
    return load_dataset_snapshot(str(row["snapshot_id"]), path=path) if row else None


def load_security_master_records(
    snapshot_id: str,
    *,
    path: pathlib.Path | str | None = None,
) -> list[dict[str, Any]]:
    snapshot = load_dataset_snapshot(snapshot_id, path=path)
    if snapshot is None:
        raise ValueError(f"security master snapshot does not exist: {snapshot_id}")
    if snapshot["dataset_id"] != "cn.security_master":
        raise ValueError("snapshot is not cn.security_master")
    partition_ids = tuple(snapshot["manifest"].get("partition_ids") or ())
    if not partition_ids:
        raise ValueError("security master snapshot has no partition lineage")
    placeholders = ",".join("?" for _ in partition_ids)
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT instrument_id,symbol,exchange,name,security_type,board,list_date,delist_date,"
            "status,available_at,retrieved_at,provider_id,raw_artifact_id,partition_id "
            f"FROM fact_security_master WHERE partition_id IN ({placeholders}) "
            "ORDER BY instrument_id",
            partition_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def security_universe_at(
    knowledge_cutoff: str,
    *,
    exchange: str | None = None,
    status: str = "LISTED",
    path: pathlib.Path | str | None = None,
) -> list[dict[str, Any]]:
    snapshot = find_security_master_snapshot_at(knowledge_cutoff, path=path)
    if snapshot is None:
        return []
    rows = load_security_master_records(str(snapshot["snapshot_id"]), path=path)
    return [
        row
        for row in rows
        if row["status"] == status and (exchange is None or row["exchange"] == exchange)
    ]


def security_at(
    instrument_id: str,
    knowledge_cutoff: str,
    *,
    path: pathlib.Path | str | None = None,
) -> dict[str, Any] | None:
    for row in security_universe_at(knowledge_cutoff, status="LISTED", path=path):
        if row["instrument_id"] == instrument_id:
            return row
    return None
