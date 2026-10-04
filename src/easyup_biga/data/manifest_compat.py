"""EvidenceSet manifest v1 / v2 的兼容判据。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：一份 manifest **在结构上**能不能被回放读懂
- **不覆盖**：它引用的快照是否真的存在、哈希对不对 —— 那要查库，在 `replay.py`

⚠️ 文件名不叫 `compat.py`（外部实现的名字）：那个名字不说明「什么东西的
兼容」，而本仓库里「兼容」可能指 schema 迁移、契约版本、manifest 三件事。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: 回放能读懂的 manifest 版本。新增版本必须同时补上面的结构判据。
SUPPORTED_MANIFEST_VERSIONS = frozenset({"1", "2"})


@dataclass(frozen=True, slots=True)
class ManifestCompatibility:
    version: str
    replayable: bool
    errors: tuple[str, ...]


def validate_evidence_manifest(manifest: Mapping[str, Any]) -> ManifestCompatibility:
    """v1（`symbols` 指向 raw 行）与 v2（`datasets` 指向 DatasetSnapshot）都接受。

    🔴 缺 `manifest_version` 判成 "1" 而不是报错：v1 是在这个字段存在之前
    写出来的，库里那些行**永远**不会有它。把「旧」判成「坏」会让历史决策
    集体变成不可回放 —— 而它们本来是可以回放的。
    """
    version = str(manifest.get("manifest_version") or "1")
    errors: list[str] = []
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        return ManifestCompatibility(version, False, (f"不支持的 manifest_version={version!r}",))
    symbols = manifest.get("symbols") or {}
    if symbols and not isinstance(symbols, Mapping):
        errors.append("symbols 必须是映射")
    if isinstance(symbols, Mapping):
        for symbol, entry in symbols.items():
            if not isinstance(entry, Mapping) or "snapshot_id" not in entry:
                errors.append(f"旧式条目不可回放（没有 snapshot_id）：{symbol}")
    if version == "2":
        datasets = manifest.get("datasets") or {}
        if not isinstance(datasets, Mapping):
            errors.append("manifest v2 的 datasets 必须是映射")
        else:
            for dataset_id, entry in datasets.items():
                if not isinstance(entry, Mapping) or not entry.get("snapshot_id"):
                    errors.append(f"manifest v2 条目没有 snapshot_id：{dataset_id}")
    return ManifestCompatibility(version, not errors, tuple(errors))
