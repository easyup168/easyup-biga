"""Data Platform —— 数据集/数据源名册与领域契约（Phase 3）。

设计与 14 条适配裁定见 `docs/design/phase-3-data-platform.md`。

本包在 P3-0 只有**声明**，没有执行：Store / Job Runner / Quality 在 P3-1 之后
才进来。这不是"先搭好架子"，而是名册本身已经有消费方（`bin/biga-data`）——
一个没有消费方的注册表就是 L-1 死配置，本仓库最优先防范的失败模式。
"""

from __future__ import annotations

from .contracts import (
    EXIT_CANCELLED,
    DataIssue,
    DatasetDefinition,
    DataStatus,
    ProviderDefinition,
    exit_code_for,
)
from .provider_registry import (
    PROVIDER_IDS,
    PROVIDER_REGISTRY,
    PROVIDERS,
    datasets_of,
    get_provider,
)
from .registry import DATASET_IDS, DATASET_REGISTRY, DATASETS, get_dataset

__all__ = [
    "DATASET_IDS",
    "DATASET_REGISTRY",
    "DATASETS",
    "EXIT_CANCELLED",
    "PROVIDER_IDS",
    "PROVIDER_REGISTRY",
    "PROVIDERS",
    "DataIssue",
    "DataStatus",
    "DatasetDefinition",
    "ProviderDefinition",
    "datasets_of",
    "exit_code_for",
    "get_dataset",
    "get_provider",
]
