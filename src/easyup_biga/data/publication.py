"""行级数据集的发布入口 —— 写 raw 归档 + Parquet，然后**委托**给唯一的发布服务。

🔴 这里**不走账本**
-------------------
外部 P3-4 实现把这一层写成了 `DatasetRowPublisher`，它自己又完整走了一遍
`DataRun → RawArtifact → ProviderAttempt → Partition → Quality → Snapshot`
的账本流程 —— 与 `snapshots.py::DatasetSnapshotService.publish()` 逐项相同。

那是 **L-3**：同一条流程两份实现。将来给 Data Run 状态机加一个格子、或改幂等
判据，必须同时改两处，而**漏改是静默的**（一个 dataset 用新规则发布、另一个
用旧的，两边都不报错）。

而且它直接违反设计 v1 自己的风险 1 裁定：

> 如果 Phase 3 新写一个完全独立的 snapshot manager，会与现有
> `SnapshotCoordinator` / `EvidenceSet` / Replay 形成两条血缘。**裁定：禁止。**
> 通用层必须由现有 Vertical Slice 演化而来。

⇒ 本层只负责它**自己那份**责任：把原始响应落盘、把归一化后的行写成 Parquet、
  算出发布请求。账本由 `DatasetSnapshotService` 一处负责。

覆盖 / 不覆盖
-------------
- 覆盖：raw 文本归档、Parquet 分区写入、幂等与修订版本号的计算
- **不覆盖**：账本写入（`snapshots.py`）、质量判据本身（各 dataset 自己算完传进来）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from easyup_biga.data.contracts import (
    DataIssue,
    DatasetStatus,
    RawArtifact,
    new_raw_artifact_id,
)
from easyup_biga.data.provider_registry import role_of
from easyup_biga.data.registry import get_dataset
from easyup_biga.domain import now_cn
from easyup_biga.persistence import find_dataset_snapshot, load_dataset_partition

from .file_store import FileStore
from .snapshots import DatasetSnapshotService, SnapshotPublishRequest

__all__ = ["PublishResult", "DatasetRowPublisher"]


@dataclass(frozen=True, slots=True)
class PublishResult:
    status: DatasetStatus
    data_run_id: str
    snapshot_id: str | None
    partition_id: str | None
    reused: bool = False
    #: 这次发布落下的 RawArtifact id。**派生数据集要靠它接上游血缘** ——
    #: 没有它，下游只能自己再造一份 raw，而那份 raw 与真正的来源无关。
    #: ⚠️ 复用既有快照时也填：值取自既有分区的 `raw_artifact_id`，
    #:    否则「今天重跑」与「今天第一次跑」给下游的东西不一样。
    raw_artifact_ids: tuple[str, ...] = ()


class DatasetRowPublisher:
    """把「一批归一化后的行」发布成一个不可变 Parquet 分区 + DatasetSnapshot。"""

    def __init__(self, *, db_path=None, data_root="data") -> None:
        self.db_path = db_path
        self.fs = FileStore(data_root)
        self._service = DatasetSnapshotService(path=db_path)

    def publish(
        self,
        *,
        dataset_id: str,
        job_id: str,
        provider_id: str,
        partition_key: Mapping[str, str],
        rows: Sequence[Mapping[str, Any]],
        as_of: str,
        raw_text: str | None = None,
        upstream_artifact_ids: tuple[str, ...] = (),
        #: 真实的取数尝试链（含失败的）。不给就按「一个 artifact = 一次
        #: PRIMARY 成功」记 —— 见 `SnapshotPublishRequest.failover_attempts`。
        failover_attempts: tuple[tuple[str, str, bool, str | None], ...] = (),
        quality_status: DatasetStatus = DatasetStatus.COMPLETE,
        quality_metrics: Mapping[str, Any] | None = None,
        quality_issues: tuple[DataIssue, ...] = (),
        new_revision: bool = False,
    ) -> PublishResult:
        definition = get_dataset(dataset_id)
        role = role_of(dataset_id, provider_id)          # 未绑定就 fail closed
        key = dict(partition_key)

        existing = find_dataset_snapshot(
            dataset_id, key, status=DatasetStatus.COMPLETE, path=self.db_path)
        if existing is not None and not new_revision:
            # 普通重跑：已有 COMPLETE 的同分区 ⇒ 只有在物理对象仍然存在且
            # 哈希一致时才允许复用。否则这是一个“控制面说完成、数据面却坏了”
            # 的腐化快照，必须响亮失败，绝不能把 reused=True 当成成功。
            partition_ids = tuple(str(x) for x in existing["manifest"].get("partition_ids") or ())
            if len(partition_ids) != 1:
                raise RuntimeError(
                    f"existing COMPLETE snapshot {existing['snapshot_id']} has invalid partition lineage")
            part = load_dataset_partition(partition_ids[0], path=self.db_path)
            if part is None:
                raise RuntimeError(f"existing COMPLETE snapshot references missing partition {partition_ids[0]}")
            uri = str(part["storage_uri"])
            if not uri.startswith("biga+sqlite://"):
                self.fs.verify_file_hash(uri, str(part["content_sha256"]))
            # ⚠️ 复用也要把血缘带出去：下游（派生数据集）靠这个 id 接上游。
            #    不带的话，「今天重跑」会让下游拿到空的 upstream ——
            #    而那条路会静默退化成「自己造一份 raw」。
            reused_raw = str(part["raw_artifact_id"]) if part["raw_artifact_id"] else None
            return PublishResult(
                DatasetStatus.COMPLETE, "", str(existing["snapshot_id"]),
                partition_ids[0], reused=True,
                raw_artifact_ids=(reused_raw,) if reused_raw else ())

        version = int(existing["data_version"]) + 1 if existing else 1
        now = now_cn().isoformat()
        date_key = "-".join(str(v) for _, v in sorted(key.items()))
        trigger_id = f"{job_id}:{date_key}"

        # ── raw 归档（内容寻址，与账本无关，先落盘）──────────────────────
        #
        # 🔴 **派生数据集走另一条**：它没有采集动作，raw 是**上游那一条**。
        #    自己再造一份只有两种造法，两种都是错的 —— 见
        #    `SnapshotPublishRequest.upstream_artifact_ids` 的注释。
        if upstream_artifact_ids:
            if raw_text is not None:
                raise ValueError(
                    "派生发布（给了 upstream_artifact_ids）不该再传 raw_text —— "
                    "两者同时给意味着既声称引用上游、又要落一份自己的 raw，"
                    "而分区上只有一条 raw_artifact_id 外键，必然有一个是摆设。")
            raws: tuple[RawArtifact, ...] = ()
            out_artifact_ids = tuple(upstream_artifact_ids)
        else:
            if raw_text is None:
                raise ValueError(
                    "采集型发布必须带 raw_text（原始响应）；"
                    "派生型请改用 upstream_artifact_ids 指向上游那一条。")
            raw_id = new_raw_artifact_id()
            uri, stored_sha, size = self.fs.write_raw_text(
                dataset_id, provider_id, date_key, raw_id, raw_text)
            raws = (RawArtifact(
                artifact_id=raw_id, dataset_id=dataset_id, provider_id=provider_id,
                request_fingerprint=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                body_uri=uri, body_sha256=stored_sha, size_bytes=size,
                retrieved_at=now, content_type="application/json", compression="gzip",
                as_of=as_of, available_at=now,
            ),)
            out_artifact_ids = (raw_id,)

        # ── 一行都没有：发不出分区，但**必须留痕**（见服务里那条 docstring）──
        if not rows:
            terminal = (quality_status if quality_status in {
                DatasetStatus.PARTIAL, DatasetStatus.QUARANTINED, DatasetStatus.FAILED
            } else DatasetStatus.PARTIAL)
            run_id = self._service.record_unpublishable(
                dataset_id=dataset_id, job_id=job_id, partition_key=key,
                trigger_id=trigger_id, provider_id=provider_id, raw_artifacts=raws,
                status=terminal, quality_metrics=quality_metrics or {"row_count": 0},
                quality_issues=quality_issues, data_version=version,
            )
            return PublishResult(terminal, run_id, None, None,
                                 raw_artifact_ids=out_artifact_ids)

        # ── Parquet 分区：先写**暂存区**，进账本之后才放进 lake ─────────────
        #
        # 🔴 两阶段的理由见 `FileStore.stage_parquet_rows` 的 docstring。
        #    一句话：先文件后账本，崩在中间会留一个控制面不认、而扫盘查询
        #    读得到的孤儿分区 —— **哑的**；先账本后文件，崩在中间是一条指向
        #    不存在文件的账本行 —— 回放与完整性审计当场抛，**响的**。
        staged = self.fs.stage_parquet_rows(
            dataset_id, key, rows, definition.schema_version, version)
        try:
            result = self._service.publish(
                SnapshotPublishRequest(
                    dataset_id=dataset_id, job_id=job_id, partition_key=key,
                    trigger_id=trigger_id, provider_id=provider_id, raw_artifacts=raws,
                    upstream_artifact_ids=tuple(upstream_artifact_ids),
                    failover_attempts=tuple(failover_attempts),
                    storage_format="parquet", storage_uri=str(staged.target),
                    content_sha256=staged.sha256, row_count=staged.row_count,
                    as_of=as_of, knowledge_cutoff=now,
                    quality_metrics=dict(
                        quality_metrics or {"row_count": staged.row_count}),
                    quality_issues=quality_issues, data_version=version,
                    quality_status=quality_status,
                    supersedes_partition_id=(
                        str(existing["manifest"]["partition_ids"][0]) if existing else None),
                    supersedes_snapshot_id=(
                        str(existing["snapshot_id"]) if existing else None),
                ),
                # P3-R2：所有分析读取都改为“控制面先选 COMPLETE Snapshot，再读
                # 其 URI”，因此 lake 里即使出现一个崩溃遗留的孤儿文件也不可见。
                # 物理文件必须在快照之前完成并校验；commit 失败就**绝不出快照**。
                materialize=lambda _pid: staged.commit(),
                materialize_after_snapshot=False,
            )
        finally:
            # 质量没过 / 中途抛了 ⇒ 暂存文件丢弃，lake 里什么都不会多出来。
            # ⚠️ 丢掉的只是**归一化后**的行；原始响应已经作为 RawArtifact 落盘，
            #    诊断材料还在，坏数据照样查得到。
            staged.abort()
        _ = role      # 角色已在 role_of 里 fail-closed 校验过；账本里由服务记录
        return PublishResult(
            quality_status, result.data_run_id,
            result.snapshot_id or None, result.partition_id or None,
            raw_artifact_ids=out_artifact_ids)
