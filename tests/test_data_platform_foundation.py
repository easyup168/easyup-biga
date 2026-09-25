"""Data Platform 地基（P3-1）—— 元数据落库与整条血缘。

取自外部 P3-0/P3-1 实现包的冒烟测试，适配本仓库的注册表 API 后合入。
它验的是 `Data Run → Provider Attempt → Raw → Partition → Quality → Snapshot
→ EvidenceSet` 这条链能真的走通并读回。

覆盖 / 不覆盖
-------------
- 覆盖：schema v23–v26 建表、Data Run 的 CAS 转移、整条血缘的写入与读回
- **不覆盖**：注册表本身的守卫（见 `test_data_registry.py`）、
  Parquet / 质量策略内容（P3-4）
"""
from __future__ import annotations

from easyup_biga.data import (
    DATASET_REGISTRY,
    DataJobRun,
    DataRunState,
    DatasetPartition,
    DatasetSnapshot,
    DatasetStatus,
    DatasetLink,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderRole,
    QualityReport,
    RawArtifact,
    new_data_run_id,
    new_dataset_snapshot_id,
    new_partition_id,
    new_quality_report_id,
    new_raw_artifact_id,
)
from easyup_biga.domain import new_evidence_set_id, now_cn
from easyup_biga.persistence import (
    SCHEMA_VERSION,
    DataRunTransitionError,
    connect,
    data_run_state,
    find_dataset_snapshot,
    init_schema,
    link_evidence_set_dataset,
    list_evidence_set_datasets,
    open_data_run,
    record_provider_attempt,
    save_dataset_partition,
    save_dataset_snapshot,
    save_evidence_set,
    save_quality_report,
    save_raw_artifact,
    transition_data_run,
)


def _run() -> DataJobRun:
    return DataJobRun(
        data_run_id=new_data_run_id(),
        job_id="index-daily-foundation-smoke",
        dataset_id="cn.index.daily_bars",
        # 🔴 切片键随注册表：index_daily 按 evidence_set_id 切（一次冻结一个分区）。
        partition_key={"evidence_set_id": "ES-P31-SMOKE"},
        requested_data_version=1,
        trigger_id="test-trigger",
        created_at=now_cn().isoformat(),
    )


def test_只激活已有生产持久链的数据集():
    """P3-0 的范围判据：只注册现有持久链的两个。

    🔴 这条会随 P3-6 的每次迁移 PR 而改 —— 那是**有意的**：改这条测试就是在
    声明「又有一个数据集归平台管了」，逼人做一次明确的动作，而不是往注册表里
    悄悄加一行。
    """
    assert set(DATASET_REGISTRY) == {"cn.trading_calendar", "cn.index.daily_bars"}


def test_schema到v26且七张地基表都在(tmp_path):
    db = tmp_path / "biga.db"
    assert init_schema(db) == SCHEMA_VERSION == 26
    with connect(db, readonly=True) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "data_job_runs", "data_run_events", "provider_attempts", "raw_artifacts",
        "dataset_partitions", "quality_reports", "dataset_snapshots", "evidence_set_datasets",
    } <= tables


def test_data_run的CAS转移(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    run = _run()
    open_data_run(run, path=db)
    assert data_run_state(run.data_run_id, path=db) == DataRunState.RECEIVED.value
    transition_data_run(run.data_run_id, "RECEIVED", "FETCHING", path=db)
    assert data_run_state(run.data_run_id, path=db) == DataRunState.FETCHING.value
    try:
        transition_data_run(run.data_run_id, "RECEIVED", "FETCHING", path=db)
    except DataRunTransitionError:
        pass
    else:
        raise AssertionError("陈旧的 expected_state 必须被拒 —— CAS 没生效")


def test_整条血缘走通_raw到证据集(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    run = _run()
    open_data_run(run, path=db)
    now = now_cn().isoformat()

    raw = RawArtifact(
        artifact_id=new_raw_artifact_id(), dataset_id=run.dataset_id, provider_id="sina",
        request_fingerprint="request-sha", body_uri="data/raw/test.json.gz",
        body_sha256="a" * 64, size_bytes=123, retrieved_at=now,
        content_type="application/json", compression="gzip", as_of=now, available_at=now,
    )
    save_raw_artifact(raw, path=db)
    record_provider_attempt(
        ProviderAttempt(
            data_run_id=run.data_run_id, provider_id="sina", role=ProviderRole.PRIMARY,
            attempt_no=1, status=ProviderAttemptStatus.SUCCEEDED,
            started_at=now, finished_at=now, elapsed_ms=12, artifact_id=raw.artifact_id,
        ),
        path=db,
    )

    partition = DatasetPartition(
        partition_id=new_partition_id(), dataset_id=run.dataset_id,
        partition_key=run.partition_key, schema_version=1, data_version=1,
        storage_format="json", storage_uri="data/lake/test.json",
        content_sha256="b" * 64, row_count=120, provider_id="sina",
        raw_artifact_id=raw.artifact_id,
    )
    save_dataset_partition(partition, path=db)
    quality = QualityReport(
        quality_report_id=new_quality_report_id(), data_run_id=run.data_run_id,
        dataset_id=run.dataset_id, partition_id=partition.partition_id,
        status=DatasetStatus.COMPLETE, policy_id="cn-index-daily-bridge-v1",
        metrics={"rows": 120}, checked_at=now,
    )
    save_quality_report(quality, path=db)
    snapshot = DatasetSnapshot(
        snapshot_id=new_dataset_snapshot_id(), dataset_id=run.dataset_id,
        partition_key=run.partition_key, as_of=now, knowledge_cutoff=now,
        status=DatasetStatus.COMPLETE, schema_version=1, data_version=1,
        partition_ids=(partition.partition_id,), quality_report_id=quality.quality_report_id,
        content_sha256="c" * 64,
    )
    save_dataset_snapshot(snapshot, path=db)
    assert find_dataset_snapshot(
        run.dataset_id, dict(run.partition_key), status=DatasetStatus.COMPLETE, path=db
    )["snapshot_id"] == snapshot.snapshot_id

    esid = new_evidence_set_id()
    save_evidence_set(
        evidence_set_id=esid, decision_id=None, manifest={"kind": "p3-smoke"}, path=db
    )
    link_evidence_set_dataset(
        DatasetLink(esid, run.dataset_id, snapshot.snapshot_id), path=db
    )
    assert list_evidence_set_datasets(esid, path=db)[0]["snapshot_id"] == snapshot.snapshot_id
