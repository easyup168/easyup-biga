"""P3-14 · 存储完整性与架构边界守卫。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：把一份已发布的 DatasetSnapshot 从快照一路重算到 raw 字节；
  以及一条 AST 判据 —— 生产 Specialist 不许直接 import 采数适配器
- **不覆盖**：修订链的形状（`lineage_audit.py`）、回放清单（`replay.py`）
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from easyup_biga.persistence import (
    load_dataset_partition,
    load_dataset_snapshot,
    load_raw_artifact,
)

from .contracts import DatasetStatus
from .file_store import FileStore
from .replay import SQLITE_URI_SCHEME, ReplayIntegrityError

#: 生产 skill 不许出现的 import 前缀 —— 采数适配器的两套摆放位置。
PROVIDER_IMPORT_PREFIXES = ("easyup_biga.providers", "_sources")


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    checks: tuple[str, ...]
    errors: tuple[str, ...]


def audit_snapshot_integrity(
    snapshot_id: str, *, path=None, data_root: str = "data"
) -> IntegrityReport:
    """把一份 DatasetSnapshot 重算到 raw 工件。"""
    checks: list[str] = []
    errors: list[str] = []
    snapshot = load_dataset_snapshot(snapshot_id, path=path)
    if snapshot is None:
        return IntegrityReport(False, (), (f"快照不存在：{snapshot_id}",))
    if snapshot["status"] != DatasetStatus.COMPLETE.value:
        errors.append(f"快照不是 COMPLETE：{snapshot['status']}")
    fs = FileStore(data_root)
    partition_ids = tuple(str(x) for x in snapshot["manifest"].get("partition_ids") or ())
    if not partition_ids:
        errors.append("快照没有任何分区")
    for pid in partition_ids:
        part = load_dataset_partition(pid, path=path)
        if part is None:
            errors.append(f"分区不存在：{pid}")
            continue
        if part["dataset_id"] != snapshot["dataset_id"]:
            errors.append(f"分区的 dataset 对不上：{pid}")
        if part["partition_key"] != snapshot["partition_key"]:
            errors.append(f"分区键与快照不一致：{pid}")
        if int(part["data_version"]) != int(snapshot["data_version"]):
            errors.append(f"分区与快照的 data_version 不一致：{pid}")
        uri = str(part["storage_uri"])
        if not uri.startswith(SQLITE_URI_SCHEME):
            try:
                fs.verify_file_hash(uri, str(part["content_sha256"]))
                checks.append(f"分区哈希已重算：{pid}")
            except Exception as exc:
                errors.append(str(exc))
        raw_id = part.get("raw_artifact_id")
        if not raw_id:
            continue
        artifact = load_raw_artifact(str(raw_id), path=path)
        if artifact is None:
            errors.append(f"raw 工件不存在：{raw_id}")
            continue
        raw_uri = str(artifact["body_uri"])
        if not raw_uri.startswith(SQLITE_URI_SCHEME):
            try:
                fs.verify_file_hash(raw_uri, str(artifact["body_sha256"]))
                checks.append(f"raw 哈希已重算：{raw_id}")
            except Exception as exc:
                errors.append(str(exc))
    return IntegrityReport(not errors, tuple(checks), tuple(errors))


def specialist_provider_imports(
    repo: Path | str, specialist_paths: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    """AST 扫出每个 skill 直接 import 的采数模块。**只报事实，不判对错。**

    🔴 判断留给调用方，是因为「对错」随里程碑变：P3-6 之前这些直连是现状，
    之后它必须归零。一个把现状写死成 FAIL 的守卫，从第一天起就是红的 ——
    而长期红的守卫等于没有守卫，它只训练人忽略输出。
    """
    root = Path(repo)
    out: dict[str, tuple[str, ...]] = {}
    for rel in specialist_paths:
        source = root / rel
        found: list[str] = []
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(PROVIDER_IMPORT_PREFIXES):
                    found.append(module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(PROVIDER_IMPORT_PREFIXES):
                        found.append(alias.name)
        out[rel] = tuple(sorted(set(found)))
    return out


def audit_specialist_provider_boundary(
    repo: Path | str, specialist_paths: Iterable[str]
) -> IntegrityReport:
    """P3-6 的出口判据：生产 Specialist 零直连采数模块。

    ⚠️ **今天必然是红的** —— 五个 skill 还在直接 `from _sources import ...`，
    那正是 P3-6 要迁的东西。所以它的消费方不是 CI 的绿灯，是 P3-6 的验收。
    在那之前，用 `specialist_provider_imports()` 盯住「别再多一个」。
    """
    checks: list[str] = []
    errors: list[str] = []
    for rel, modules in specialist_provider_imports(repo, specialist_paths).items():
        for module in modules:
            errors.append(f"{rel}: 直连采数模块 {module}")
        checks.append(f"已扫描：{rel}")
    return IntegrityReport(not errors, tuple(checks), tuple(errors))


def require_integrity(report: IntegrityReport) -> None:
    if not report.ok:
        raise ReplayIntegrityError("; ".join(report.errors))
