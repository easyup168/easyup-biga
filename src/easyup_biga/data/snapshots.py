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
from typing import Mapping, Sequence

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

    def publish(self, request: SnapshotPublishRequest) -> SnapshotPublishResult:
        definition = get_dataset(request.dataset_id)
        if request.row_count < 0:
            raise ValueError("row_count must be >= 0")
        if not request.raw_artifacts:
            raise ValueError("at least one verified raw artifact is required")
        if any(a.dataset_id != request.dataset_id for a in request.raw_artifacts):
            raise ValueError("all raw artifacts must belong to the published dataset")
        if any(a.provider_id != request.provider_id for a in request.raw_artifacts):
            raise ValueError("P3-2 bundle requires one provider_id; mixed-provider publish is later work")

        # Idempotency by immutable logical partition.  P3-2 calls this before the
        # EvidenceSet row is inserted, so retrying the bridge itself is safe.
        existing = find_dataset_snapshot(
            request.dataset_id,
            dict(request.partition_key),
            status=DatasetStatus.COMPLETE,
            path=self._path,
        )
        if existing is not None:
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
            partition = DatasetPartition(
                partition_id=new_partition_id(),
                dataset_id=request.dataset_id,
                partition_key=request.partition_key,
                schema_version=definition.schema_version,
                data_version=request.data_version,
                storage_format=request.storage_format,
                storage_uri=request.storage_uri,
                content_sha256=request.content_sha256,
                row_count=request.row_count,
                provider_id=request.provider_id,
                # One bundle may have many RawArtifacts. ProviderAttempt is the
                # one-to-many lineage edge for P3-2; do not lie with one arbitrary FK.
                raw_artifact_id=(request.raw_artifacts[0].artifact_id
                                 if len(request.raw_artifacts) == 1 else None),
            )
            save_dataset_partition(partition, path=self._path)

            transition_data_run(data_run_id, state, "VALIDATING", path=self._path)
            state = "VALIDATING"
            quality = QualityReport(
                quality_report_id=new_quality_report_id(),
                data_run_id=data_run_id,
                dataset_id=request.dataset_id,
                partition_id=partition.partition_id,
                status=DatasetStatus.COMPLETE,
                policy_id=definition.quality_policy,
                metrics=dict(request.quality_metrics),
                issues=request.quality_issues,
                checked_at=now_cn().isoformat(),
            )
            save_quality_report(quality, path=self._path)

            transition_data_run(data_run_id, state, "PUBLISHING", path=self._path)
            state = "PUBLISHING"
            snapshot = DatasetSnapshot(
                snapshot_id=new_dataset_snapshot_id(),
                dataset_id=request.dataset_id,
                partition_key=request.partition_key,
                as_of=request.as_of,
                knowledge_cutoff=request.knowledge_cutoff,
                status=DatasetStatus.COMPLETE,
                schema_version=definition.schema_version,
                data_version=request.data_version,
                partition_ids=(partition.partition_id,),
                quality_report_id=quality.quality_report_id,
                content_sha256=request.content_sha256,
            )
            save_dataset_snapshot(snapshot, path=self._path)
            transition_data_run(data_run_id, state, "SNAPSHOT_CREATED", path=self._path)
            state = "SNAPSHOT_CREATED"
            transition_data_run(data_run_id, state, "COMPLETED", path=self._path)

            return SnapshotPublishResult(
                data_run_id=data_run_id,
                snapshot_id=snapshot.snapshot_id,
                partition_id=partition.partition_id,
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
