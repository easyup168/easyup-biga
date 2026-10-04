"""Data Platform —— 数据集/数据源名册与领域契约（Phase 3）。

设计与 14 条适配裁定见 `docs/design/phase-3-data-platform.md`。

P3-0 是**声明**：两张名册 + 契约，消费方是 `bin/biga-data`。
P3-1 加上 **Data Run 状态机**与快照/分区/质量的记录类型，落库在
`easyup_biga.persistence.data`（那边依赖这里的注册表，方向是单向的）。

🔴 条目**按 Milestone 激活** —— 有真实生产者或消费者才进册。
一个没有消费方的注册表就是 L-1 死配置，本仓库最优先防范的失败模式。
"""

from __future__ import annotations

from .contracts import (
    EXIT_CANCELLED,
    DataIssue,
    DataJobRun,
    DatasetLink,
    DatasetPartition,
    DatasetProviderBinding,
    DatasetSnapshot,
    DatasetStatus,
    ProviderAttempt,
    ProviderAttemptStatus,
    ProviderRole,
    QualityReport,
    RawArtifact,
    canonical_partition_key,
    new_data_run_id,
    new_dataset_snapshot_id,
    new_partition_id,
    new_quality_report_id,
    new_raw_artifact_id,
    DatasetDefinition,
    DataRunStatus,
    ProviderDefinition,
    exit_code_for,
)
from .provider_registry import (
    PROVIDER_IDS,
    PROVIDER_REGISTRY,
    PROVIDERS,
    datasets_of,
    get_provider,
    uses_provider,
)
from .jobs import (
    DATA_RUN_INITIAL_STATE,
    DATA_RUN_LEGAL_TRANSITIONS,
    DATA_RUN_STATES,
    DATA_RUN_TERMINAL_STATES,
    DataRunState,
)
from .registry import DATASET_IDS, DATASET_REGISTRY, DATASETS, get_dataset

__all__ = [
    "DATA_RUN_INITIAL_STATE",
    "DATA_RUN_LEGAL_TRANSITIONS",
    "DATA_RUN_STATES",
    "DATA_RUN_TERMINAL_STATES",
    "DataJobRun",
    "DataRunState",
    "DatasetLink",
    "DatasetPartition",
    "DatasetProviderBinding",
    "DatasetSnapshot",
    "DatasetStatus",
    "ProviderAttempt",
    "ProviderAttemptStatus",
    "ProviderRole",
    "QualityReport",
    "RawArtifact",
    "canonical_partition_key",
    "new_data_run_id",
    "new_dataset_snapshot_id",
    "new_partition_id",
    "new_quality_report_id",
    "new_raw_artifact_id",
    "DATASET_IDS",
    "DATASET_REGISTRY",
    "DATASETS",
    "EXIT_CANCELLED",
    "PROVIDER_IDS",
    "PROVIDER_REGISTRY",
    "PROVIDERS",
    "DataIssue",
    "DataRunStatus",
    "DatasetDefinition",
    "ProviderDefinition",
    "datasets_of",
    "exit_code_for",
    "get_dataset",
    "get_provider",
    "uses_provider",
]
