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


def test_Phase3收口后所有必需dataset都已激活():
    """Phase 3 必需集必须存在；Phase 4+ 新数据集可以合法扩展全局 Registry。"""
    # 🔴 **显式手写，不从 `REQUIRED_DATASETS` 派生。**
    #    P4-G0 的版本写的是 `set(DATASET_REGISTRY) == set(REQUIRED_DATASETS)`，
    #    那让两张表互相印证 —— 而这条守卫的全部价值在于它是**独立**的一份：
    #    派生之后，「注册表里多了一个」与「闸门要求里多了一个」会互相解释，
    #    没有任何一处还在逼人做那个明确的动作。
    assert {
        # ── Phase 3 之前就在生产链上的 ──────────────────────────────
        "cn.trading_calendar",
        "cn.index.daily_bars",
        # P3-3。⚠️ 与上面两个**不同**：它们的生产链在跑，这一个的 provider
        # 尚未在本机探活成功。注册它是因为整条链已建成且有读路径；
        # 取不到数时 Data Run 会 FAILED、不发布快照 —— fail-closed。
        "cn.security_master",
        # ── P3-4 / P3-5：EOD 与它的派生 ─────────────────────────────
        "cn.equity.daily_bars",
        "cn.security.tradability",
        "cn.equity.adjustment_factors",
        "cn.market.emotion_close",
        # ── P3-6：五条盘中 direct feed，按 evidence_set_id 冻结 ──────
        #    它们的读取方是 `data.decision_client:DecisionDataClient`，
        #    由编排器在 Stage 1 之前冻进 EvidenceSet，specialist 只读冻结快照。
        "cn.index.realtime_quote",
        "cn.market.breadth",
        "cn.sector.board_snapshot",
        "cn.market.limit_pool",
        "cn.news.flash",
    } <= set(DATASET_REGISTRY)


def test_schema到v27且八张地基表都在(tmp_path):
    db = tmp_path / "biga.db"
    assert init_schema(db) == SCHEMA_VERSION == 27
    with connect(db, readonly=True) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "data_job_runs", "data_run_events", "provider_attempts", "raw_artifacts",
        "dataset_partitions", "quality_reports", "dataset_snapshots", "evidence_set_datasets",
        "fact_security_master",
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


# ── L-3：账本流程只有一份实现 ──────────────────────────────────────────────
def test_只有一处走完整的账本流程():
    """🔴 `DataRun → RawArtifact → Partition → Quality → Snapshot` 只能有一份实现。

    两份实现意味着给状态机加一个格子、或改幂等判据，要改两处 ——
    而**漏改是静默的**：一个数据集用新规则发布、另一个用旧的，两边都不报错，
    直到某天有人对着两个数据集问「为什么它俩的修订行为不一样」。

    实测踩过两次：
    · 外部 P3-4 的 `Phase3Publisher` 与既有服务逐项相同地各走一遍（2026-09-26 合并时消除）
    · `datasets/security_master.py` 自己又走了**第三遍**（同日消除）——
      它是我自己上一轮合进来的，当轮没看出来。
      **L-3 最容易在 grep 共同调用时现形，不是在读 diff 时。**

    判据是 AST：谁**真的调用**了那两个只该由发布服务调的写入函数。
    不用字符串扫描 —— docstring 与注释里提到它们是正常的（本文件就提了）。
    """
    import ast
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    OWNER = "src/easyup_biga/data/snapshots.py"
    LEDGER_ONLY = {"save_dataset_snapshot", "save_dataset_partition", "save_quality_report"}
    offenders: list[str] = []
    for path in sorted((repo / "src").rglob("*.py")) + sorted((repo / "skills").rglob("*.py")):
        rel = str(path.relative_to(repo))
        if rel == OWNER or rel.startswith("src/easyup_biga/persistence/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", None))
            if name in LEDGER_ONLY:
                offenders.append(f"{rel}:{node.lineno} 调用了 {name}()")
    assert not offenders, (
        "账本写入只该由 `data/snapshots.py::DatasetSnapshotService` 做，这些地方自己走了一遍：\n"
        + "".join(f"  · {o}\n" for o in offenders)
        + "  要发布数据就调那个服务；它写不进你要的物理落点 ⇒ 给它传 `materialize` 回调。")
