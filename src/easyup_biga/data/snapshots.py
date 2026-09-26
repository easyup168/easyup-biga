"""DatasetSnapshotService —— 通用的、不可变的快照发布服务。

取自外部 P3-2 实现包并审过。它只负责**元数据发布**，不碰 provider IO、
不做领域解析：各 dataset 的适配器（`data/datasets/*`）准备好已核验的
RawArtifact 引用，再调这里。

覆盖 / 不覆盖
-------------
- 覆盖：一次发布走完 `DataRun → RawArtifact → ProviderAttempt → Partition
  → Quality → Snapshot` 并落库，以及按**逻辑分区**幂等
- **不覆盖**：物理文件写入（Parquet 在 P3-4）、质量判据的实现
  （策略声明在 `data/quality.py`，具体判据随各自 Job 进来）

🔴 两处值得单独记的设计
-----------------------
1. **`raw_artifact_id` 只在恰好一个 artifact 时才填。** 一个 bundle 可以有多个
   raw（sh + sz），此时 `dataset_partitions.raw_artifact_id` 留空，由
   `provider_attempts` 承担一对多的血缘边 —— **不拿一个随便选的外键撒谎**。
   这与仓库「不硬凑：凑出来的溯源比没有溯源更糟」是同一条。
2. **失败时尽力把 DataRun 转 FAILED，但绝不掩盖原始异常。** 内层 `except`
   吞掉的只是「收尾动作也失败了」，原异常照常抛出去。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from easyup_biga.domain import now_cn
from easyup_biga.persistence import (
    DataStoreConflict,
    find_dataset_snapshot,
    open_data_run,
    record_provider_attempt,
    save_dataset_partition,
    save_dataset_snapshot,
    save_quality_report,
    save_raw_artifact,
    transition_data_run,
)

from .contracts import (
    DataJobRun,
    DataIssue,
    DatasetPartition,
    DatasetSnapshot,
    DatasetStatus,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderRole,
    QualityReport,
    RawArtifact,
    new_data_run_id,
    new_dataset_snapshot_id,
    new_partition_id,
    new_quality_report_id,
)
from .registry import get_dataset


@dataclass(frozen=True, slots=True)
class SnapshotPublishRequest:
    dataset_id: str
    job_id: str
    partition_key: Mapping[str, str]
    trigger_id: str
    provider_id: str
    raw_artifacts: Sequence[RawArtifact]
    storage_format: str
    storage_uri: str
    content_sha256: str
    row_count: int
    as_of: str
    knowledge_cutoff: str
    quality_metrics: Mapping[str, object]
    quality_issues: tuple[DataIssue, ...] = ()
    data_version: int = 1
    #: 🔴 质量裁定的结果。非 `COMPLETE` 时**发布分区与质量报告，但不出快照** ——
    #: 「数据有问题」和「什么都没发生」是两件事：前者要留下可审计的痕迹。
    quality_status: DatasetStatus = DatasetStatus.COMPLETE
    #: 修订：这次发布取代的是哪个分区/快照（`--new-revision`）。旧版本永不覆盖。
    supersedes_partition_id: str | None = None
    supersedes_snapshot_id: str | None = None
    #: 由调用方预先指定分区 id。**只有一种情况需要它**：物理落点的 uri 里含
    #: 分区 id（如 `biga+sqlite://fact_security_master/<pid>`）⇒ 调用方得先知道
    #: id 才拼得出 `storage_uri`。不给就由服务生成。
    partition_id: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotPublishResult:
    data_run_id: str
    snapshot_id: str
    partition_id: str
    quality_report_id: str
    as_of: str
    knowledge_cutoff: str
    reused: bool = False


class DatasetSnapshotService:
    """Publish one immutable DatasetSnapshot through the common Phase-3 ledger.

    It is intentionally small for P3-2: physical file writing belongs to later
    milestones.  Here ``storage_uri`` may point at an existing immutable legacy
    representation such as an EvidenceSet manifest.
    """

    def __init__(self, *, path=None) -> None:
        self._path = path

    def publish(
        self,
        request: SnapshotPublishRequest,
        *,
        materialize: Callable[[str], None] | None = None,
    ) -> SnapshotPublishResult:
        """把一份数据发布成 DatasetPartition + QualityReport（+ DatasetSnapshot）。

        `materialize` —— **物理行由本次发布写**时给它。回调收到 `partition_id`，
        负责把归一化后的行真正写进去（`fact_*` 表）。

        🔴 给不给它，决定了「质量没过时要不要登记分区」，而这不是随手的开关：

        - **不给**（Parquet / 旧 raw 行）：物理字节在进来之前就已经落盘了 ⇒
          质量没过也照样登记分区，因为那份数据**真的存在**，留痕才查得到。
        - **给**（`fact_*` 表）：物理行由回调写 ⇒ 质量没过就不写，
          于是也**不能**登记分区。否则那一行会声称
          `biga+sqlite://fact_security_master/<pid>` 底下有 N 行，而那里空空如也 ——
          一个指向不存在数据的分区，比没有分区更糟。
        """
        definition = get_dataset(request.dataset_id)
        if request.row_count < 0:
            raise ValueError("row_count must be >= 0")
        if not request.raw_artifacts:
            raise ValueError("at least one verified raw artifact is required")
        if any(a.dataset_id != request.dataset_id for a in request.raw_artifacts):
            raise ValueError("all raw artifacts must belong to the published dataset")
        if any(a.provider_id != request.provider_id for a in request.raw_artifacts):
            raise ValueError("P3-2 bundle requires one provider_id; mixed-provider publish is later work")

        # 幂等键是「逻辑分区 + 请求的 data_version」。P3-2 在插 EvidenceSet 行之前
        # 调它，所以重试桥接本身是安全的。
        #
        # 🔴 `>= request.data_version` 这一半是 2026-09-26 补的，原本只判
        #    `existing is not None` —— 于是**修订整体不可用**：该分区只要已经有
        #    一份 COMPLETE，请求 v2 也原样退回 v1。
        #
        #    后果不是「修订失败」那么简单。调用方（`DatasetRowPublisher`）是
        #    **先写 Parquet、再进账本**的，所以 v2 的文件已经落盘 ⇒ 盘上有 v2、
        #    控制面只认 v1。而两条查询走的是不同的路：
        #      · `query_eod_between` 扫盘取最高 data_version → 读到 v2
        #      · `query_eod_as_of`   走控制面              → 读到 v1
        #    实测同一个交易日拿到两套价格，**两边都不报错**。
        #    这正是裁定 15 要防的「某天悄悄给出两个数」。
        existing = find_dataset_snapshot(
            request.dataset_id,
            dict(request.partition_key),
            status=DatasetStatus.COMPLETE,
            path=self._path,
        )
        if existing is not None and int(existing["data_version"]) >= int(request.data_version):
            manifest = existing["manifest"]
            partition_ids = manifest.get("partition_ids", [])
            if not partition_ids:
                raise DataStoreConflict("existing snapshot has no partition lineage")
            return SnapshotPublishResult(
                data_run_id="",
                snapshot_id=str(existing["snapshot_id"]),
                partition_id=str(partition_ids[0]),
                quality_report_id=str(existing["quality_report_id"]),
                as_of=str(existing["as_of"]),
                knowledge_cutoff=str(existing["knowledge_cutoff"]),
                reused=True,
            )

        if request.data_version > 1:
            # 🔴 修订必须当场说清取代谁。等 `audit_revision_chain` 事后发现断链
            #    太晚了：那时两份快照都已经落库，而「哪份是当前有效的」
            #    已经答不出来。
            if existing is None:
                raise DataStoreConflict(
                    f"{request.dataset_id} 请求发布 v{request.data_version}，"
                    f"但该分区没有任何 COMPLETE 的前一版 —— 修订链会从中间开始。")
            if request.supersedes_snapshot_id != str(existing["snapshot_id"]):
                raise DataStoreConflict(
                    f"{request.dataset_id} 的修订没有指向当前有效快照："
                    f"声称取代 {request.supersedes_snapshot_id!r}，"
                    f"实际当前是 {existing['snapshot_id']!r}。")
            if int(request.data_version) != int(existing["data_version"]) + 1:
                raise DataStoreConflict(
                    f"{request.dataset_id} 的修订必须是 "
                    f"v{int(existing['data_version']) + 1}，收到 v{request.data_version}"
                    f" —— 跳号会在修订链上留一个查不到的空洞。")

        now = now_cn().isoformat()
        data_run_id = new_data_run_id()
        run = DataJobRun(
            data_run_id=data_run_id,
            job_id=request.job_id,
            dataset_id=request.dataset_id,
            partition_key=request.partition_key,
            requested_data_version=request.data_version,
            trigger_id=request.trigger_id,
            created_at=now,
        )
        open_data_run(run, path=self._path)
        state = "RECEIVED"

        try:
            transition_data_run(data_run_id, state, "FETCHING", path=self._path)
            state = "FETCHING"
            for attempt_no, artifact in enumerate(request.raw_artifacts, start=1):
                save_raw_artifact(artifact, path=self._path)
                record_provider_attempt(
                    ProviderAttempt(
                        data_run_id=data_run_id,
                        provider_id=request.provider_id,
                        role=ProviderRole.PRIMARY,
                        attempt_no=attempt_no,
                        status=ProviderAttemptStatus.SUCCEEDED,
                        started_at=artifact.retrieved_at,
                        finished_at=artifact.retrieved_at,
                        elapsed_ms=0,
                        artifact_id=artifact.artifact_id,
                    ),
                    path=self._path,
                )
            transition_data_run(data_run_id, state, "RAW_STORED", path=self._path)
            state = "RAW_STORED"

            transition_data_run(data_run_id, state, "NORMALIZING", path=self._path)
            state = "NORMALIZING"
            partition_id = request.partition_id or new_partition_id()

            def _register_partition() -> None:
                save_dataset_partition(
                    DatasetPartition(
                        partition_id=partition_id,
                        dataset_id=request.dataset_id,
                        partition_key=request.partition_key,
                        schema_version=definition.schema_version,
                        data_version=request.data_version,
                        storage_format=request.storage_format,
                        storage_uri=request.storage_uri,
                        content_sha256=request.content_sha256,
                        row_count=request.row_count,
                        provider_id=request.provider_id,
                        # 一个 bundle 可能有多个 RawArtifact。一对多那条血缘边是
                        # ProviderAttempt —— 不要拿其中任意一个当外键去撒谎。
                        raw_artifact_id=(request.raw_artifacts[0].artifact_id
                                         if len(request.raw_artifacts) == 1 else None),
                        supersedes_partition_id=request.supersedes_partition_id,
                    ),
                    path=self._path,
                )

            # 物理字节已经在了 ⇒ 现在就登记（质量不过也留痕）。
            # 要靠 materialize 才写 ⇒ 推迟到质量过了再登记，见 publish 的 docstring。
            deferred = materialize is not None
            if not deferred:
                _register_partition()

            transition_data_run(data_run_id, state, "VALIDATING", path=self._path)
            state = "VALIDATING"
            quality = QualityReport(
                quality_report_id=new_quality_report_id(),
                data_run_id=data_run_id,
                dataset_id=request.dataset_id,
                partition_id=None if deferred else partition_id,
                status=request.quality_status,
                policy_id=definition.quality_policy,
                metrics=dict(request.quality_metrics),
                issues=request.quality_issues,
                checked_at=now_cn().isoformat(),
            )
            save_quality_report(quality, path=self._path)

            # 🔴 质量没过 ⇒ 到此为止：分区与质量报告都留下了（可审计），
            #    但**不出快照**。下游只认快照 ⇒ 坏数据进不了决策，
            #    而「为什么没进」查得到。
            if request.quality_status is not DatasetStatus.COMPLETE:
                terminal = ("FAILED" if request.quality_status is DatasetStatus.FAILED
                            else request.quality_status.value)
                transition_data_run(data_run_id, state, terminal, path=self._path)
                return SnapshotPublishResult(
                    data_run_id=data_run_id, snapshot_id="",
                    partition_id="" if deferred else partition_id,
                    quality_report_id=quality.quality_report_id,
                    as_of=request.as_of, knowledge_cutoff=request.knowledge_cutoff,
                )

            transition_data_run(data_run_id, state, "PUBLISHING", path=self._path)
            state = "PUBLISHING"
            if deferred:
                _register_partition()
                # 🔴 先登记分区、再写行：materialize 抛了的话，整个 run 走 FAILED，
                #    而快照还没落 ⇒ 下游看不到它。分区行留着是可审计的痕迹。
                materialize(partition_id)
            snapshot = DatasetSnapshot(
                snapshot_id=new_dataset_snapshot_id(),
                dataset_id=request.dataset_id,
                partition_key=request.partition_key,
                as_of=request.as_of,
                knowledge_cutoff=request.knowledge_cutoff,
                status=DatasetStatus.COMPLETE,
                schema_version=definition.schema_version,
                data_version=request.data_version,
                partition_ids=(partition_id,),
                quality_report_id=quality.quality_report_id,
                content_sha256=request.content_sha256,
                supersedes_snapshot_id=request.supersedes_snapshot_id,
            )
            save_dataset_snapshot(snapshot, path=self._path)
            transition_data_run(data_run_id, state, "SNAPSHOT_CREATED", path=self._path)
            state = "SNAPSHOT_CREATED"
            transition_data_run(data_run_id, state, "COMPLETED", path=self._path)

            return SnapshotPublishResult(
                data_run_id=data_run_id,
                snapshot_id=snapshot.snapshot_id,
                partition_id=partition_id,
                quality_report_id=quality.quality_report_id,
                as_of=request.as_of,
                knowledge_cutoff=request.knowledge_cutoff,
            )
        except Exception:
            # Best-effort audit closure; never mask the original failure.
            try:
                transition_data_run(data_run_id, state, "FAILED", path=self._path)
            except Exception:
                pass
            raise


    def record_unpublishable(
        self,
        *,
        dataset_id: str,
        job_id: str,
        partition_key: Mapping[str, str],
        trigger_id: str,
        provider_id: str,
        raw_artifacts: Sequence[RawArtifact],
        status: DatasetStatus,
        quality_metrics: Mapping[str, object],
        quality_issues: tuple[DataIssue, ...] = (),
        data_version: int = 1,
    ) -> str:
        """这次取到了数据、但**发不出分区**（比如一行都没有）。返回 `data_run_id`。

        🔴 为什么不是「什么都不做」：raw 已经落盘了，provider 也真的被调用过。
        丢掉这段等于让「今天没数据」和「今天没跑」在库里长得一模一样 ——
        而排查时最先要区分的就是这两件事。

        ⚠️ 与 `publish()` 是**两条不同的流程**（这条没有分区、没有快照），
        不是它的副本。账本的写入仍然只由本类负责。
        """
        definition = get_dataset(dataset_id)
        now = now_cn().isoformat()
        data_run_id = new_data_run_id()
        open_data_run(
            DataJobRun(
                data_run_id=data_run_id, job_id=job_id, dataset_id=dataset_id,
                partition_key=partition_key, requested_data_version=data_version,
                trigger_id=trigger_id, created_at=now,
            ),
            path=self._path,
        )
        state = "RECEIVED"
        try:
            transition_data_run(data_run_id, state, "FETCHING", path=self._path)
            state = "FETCHING"
            for attempt_no, artifact in enumerate(raw_artifacts, start=1):
                save_raw_artifact(artifact, path=self._path)
                record_provider_attempt(
                    ProviderAttempt(
                        data_run_id=data_run_id, provider_id=provider_id,
                        role=ProviderRole.PRIMARY, attempt_no=attempt_no,
                        status=ProviderAttemptStatus.SUCCEEDED,
                        started_at=artifact.retrieved_at, finished_at=artifact.retrieved_at,
                        elapsed_ms=0, artifact_id=artifact.artifact_id,
                    ),
                    path=self._path,
                )
            transition_data_run(data_run_id, state, "RAW_STORED", path=self._path)
            state = "RAW_STORED"
            transition_data_run(data_run_id, state, "NORMALIZING", path=self._path)
            state = "NORMALIZING"
            transition_data_run(data_run_id, state, "VALIDATING", path=self._path)
            state = "VALIDATING"
            save_quality_report(
                QualityReport(
                    quality_report_id=new_quality_report_id(), data_run_id=data_run_id,
                    dataset_id=dataset_id, partition_id=None, status=status,
                    policy_id=definition.quality_policy,
                    metrics=dict(quality_metrics), issues=quality_issues,
                    checked_at=now_cn().isoformat(),
                ),
                path=self._path,
            )
            terminal = "FAILED" if status is DatasetStatus.FAILED else status.value
            transition_data_run(data_run_id, state, terminal, path=self._path)
            return data_run_id
        except Exception:
            try:
                transition_data_run(data_run_id, state, "FAILED", path=self._path)
            except Exception:
                pass
            raise
