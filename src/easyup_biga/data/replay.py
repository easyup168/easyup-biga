"""P3-11 · 确定性离线回放 —— 只走 EvidenceSet 里**已经冻结**的那串 id。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：从一个 `evidence_set_id` 出发，把 EvidenceSet → DatasetSnapshot →
  DatasetPartition → RawArtifact 这条链**原样**走一遍，并在能重算哈希的地方
  重算一遍
- **不覆盖**：把这些字节重新喂给 agent 跑出一张新 Card。那是回放的消费方，
  不是回放本身

🔴 本模块**不解析「最新」，也不选 provider**
--------------------------------------------
回放里任何一次「取当前最新的那份」都会让结果随时间漂移，而漂移**不报错** ——
它只是某天给出另一个答案。所以这里只接受显式 id，取不到就抛，不回退到最新。

⚠️ `network_forbidden()` 能挡住什么
----------------------------------
挡住**本进程内**的出站 socket（`connect` / `connect_ex` / `create_connection`），
所以任何 HTTP 库都跑不掉。挡不住的：子进程、已经建好的连接、以及读本地文件。
⇒ 它是一道**回归围栏**（哪天有人往回放路径里塞了一次取数，当场红），
不是「本函数在密码学意义上离线」的证明。这两件事别混着说。
"""
from __future__ import annotations

import contextlib
import socket
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from easyup_biga.persistence import (
    list_evidence_set_datasets,
    load_dataset_partition,
    load_dataset_snapshot,
    load_evidence_set,
    load_raw_artifact,
    load_raw_snapshot,
    payload_sha256,
    raw_text_sha256,
)

from .contracts import DatasetStatus
from .file_store import FileStore

#: 旧的 SQLite raw 行在 `storage_uri` / `body_uri` 里的写法。
LEGACY_RAW_URI_PREFIX = "biga+sqlite://raw_market_snapshot/"
#: 任何指回控制面 SQLite 行的 uri —— 它的字节完整性由 raw 侧的哈希兜底。
SQLITE_URI_SCHEME = "biga+sqlite://"


class ReplayIntegrityError(RuntimeError):
    """冻结的回放血缘缺失、可变、或哈希对不上。"""


class NetworkForbiddenError(RuntimeError):
    """回放路径上出现了出站连接。"""


@dataclass(frozen=True, slots=True)
class ReplayDatasetRef:
    dataset_id: str
    snapshot_id: str
    partition_ids: tuple[str, ...]
    knowledge_cutoff: str
    data_version: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ReplayBundle:
    evidence_set_id: str
    manifest_version: str
    knowledge_cutoff: str | None
    datasets: Mapping[str, ReplayDatasetRef]
    legacy_raw_snapshot_ids: tuple[int, ...]


def _verify_legacy_raw(snapshot_id: int, expected_sha: str | None, *, path=None) -> str:
    """重算一条旧 raw 行的哈希，并与它自己存的、以及 manifest 里的对齐。"""
    row = load_raw_snapshot(snapshot_id, path=path)
    if row is None:
        raise ReplayIntegrityError(f"旧 raw 快照不存在：{snapshot_id}")
    raw_text = row.get("raw_text")
    if raw_text is not None:
        actual = raw_text_sha256(raw_text)
    elif row.get("payload") is not None:
        actual = payload_sha256(row["payload"])
    else:
        raise ReplayIntegrityError(f"旧 raw 快照 {snapshot_id} 没有可回放的正文")
    stored = str(row.get("content_sha256") or "")
    if stored and stored != actual:
        raise ReplayIntegrityError(
            f"旧 raw 快照哈希漂移：id={snapshot_id} 存的={stored} 实算={actual}")
    if expected_sha and expected_sha != actual:
        raise ReplayIntegrityError(
            f"EvidenceSet manifest 哈希漂移：id={snapshot_id} manifest={expected_sha} 实算={actual}")
    return actual


def _verify_raw_artifact(artifact: dict[str, Any], *, path=None, fs: FileStore) -> None:
    uri = str(artifact["body_uri"])
    expected = str(artifact["body_sha256"])
    if uri.startswith(LEGACY_RAW_URI_PREFIX):
        _verify_legacy_raw(int(uri.rsplit("/", 1)[-1]), expected, path=path)
        return
    fs.verify_file_hash(uri, expected)


def build_replay_bundle(
    evidence_set_id: str,
    *,
    path=None,
    data_root: str = "data",
    verify_hashes: bool = True,
) -> ReplayBundle:
    """从一份冻结的 EvidenceSet 建出精确的回放清单。

    manifest v1 仍然可读（走它的 `symbols` 引用）；v2 额外核对结构化的
    `evidence_set_datasets` 血缘与 manifest 自述是否一致。
    """
    evidence_set = load_evidence_set(evidence_set_id, path=path)
    if evidence_set is None:
        raise ReplayIntegrityError(f"未知的 evidence_set_id={evidence_set_id!r}")
    manifest = evidence_set.get("manifest") or {}
    manifest_version = str(manifest.get("manifest_version") or "1")
    legacy_ids: list[int] = []

    for symbol, entry in sorted((manifest.get("symbols") or {}).items()):
        if "snapshot_id" not in entry:
            raise ReplayIntegrityError(f"manifest 里的 {symbol!r} 没有 snapshot_id")
        sid = int(entry["snapshot_id"])
        legacy_ids.append(sid)
        if verify_hashes:
            _verify_legacy_raw(sid, entry.get("content_sha256"), path=path)

    fs = FileStore(data_root)
    datasets: dict[str, ReplayDatasetRef] = {}
    for link in list_evidence_set_datasets(evidence_set_id, path=path):
        dataset_id = str(link["dataset_id"])
        snapshot_id = str(link["snapshot_id"])
        snapshot = load_dataset_snapshot(snapshot_id, path=path)
        if snapshot is None:
            raise ReplayIntegrityError(f"DatasetSnapshot 不存在：{snapshot_id}")
        if snapshot["dataset_id"] != dataset_id:
            raise ReplayIntegrityError(
                f"EvidenceSet 的 dataset 对不上：link={dataset_id} snapshot={snapshot['dataset_id']}")
        if snapshot["status"] != DatasetStatus.COMPLETE.value:
            raise ReplayIntegrityError(
                f"回放只接受 COMPLETE 快照，{snapshot_id} 是 {snapshot['status']}")
        partition_ids = tuple(str(x) for x in snapshot["manifest"].get("partition_ids") or ())
        if not partition_ids:
            raise ReplayIntegrityError(f"快照没有任何分区：{snapshot_id}")

        for partition_id in partition_ids:
            part = load_dataset_partition(partition_id, path=path)
            if part is None:
                raise ReplayIntegrityError(f"DatasetPartition 不存在：{partition_id}")
            if part["dataset_id"] != dataset_id:
                raise ReplayIntegrityError(f"分区的 dataset 对不上：{partition_id}")
            if int(part["data_version"]) != int(snapshot["data_version"]):
                raise ReplayIntegrityError(f"分区与快照的 data_version 不一致：{partition_id}")
            if part["partition_key"] != snapshot["partition_key"]:
                raise ReplayIntegrityError(f"分区键与快照不一致：{partition_id}")

            if not verify_hashes:
                continue
            storage_uri = str(part["storage_uri"])
            # 指回 SQLite 行的分区：字节完整性由下面 raw artifact 的哈希兜底
            if not storage_uri.startswith(SQLITE_URI_SCHEME):
                fs.verify_file_hash(storage_uri, str(part["content_sha256"]))
            raw_id = part.get("raw_artifact_id")
            if not raw_id:
                continue
            artifact = load_raw_artifact(str(raw_id), path=path)
            if artifact is None:
                raise ReplayIntegrityError(f"RawArtifact 不存在：{raw_id}")
            _verify_raw_artifact(artifact, path=path, fs=fs)
            available = artifact.get("available_at") or artifact.get("retrieved_at")
            if available and str(available) > str(snapshot["knowledge_cutoff"]):
                raise ReplayIntegrityError(
                    f"回放里混进了未来数据：{raw_id} available_at={available} "
                    f"> cutoff={snapshot['knowledge_cutoff']}")

        datasets[dataset_id] = ReplayDatasetRef(
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            partition_ids=partition_ids,
            knowledge_cutoff=str(snapshot["knowledge_cutoff"]),
            data_version=int(snapshot["data_version"]),
            content_sha256=str(snapshot["content_sha256"]),
        )

    if manifest_version == "2":
        for dataset_id, entry in (manifest.get("datasets") or {}).items():
            expected = str((entry or {}).get("snapshot_id") or "")
            actual = datasets.get(dataset_id)
            if expected and (actual is None or actual.snapshot_id != expected):
                raise ReplayIntegrityError(
                    f"manifest v2 与结构化血缘不一致：{dataset_id} manifest 说 {expected}")

    cutoff = manifest.get("knowledge_cutoff")
    if cutoff is None and datasets:
        cutoff = max(item.knowledge_cutoff for item in datasets.values())
    return ReplayBundle(
        evidence_set_id=evidence_set_id,
        manifest_version=manifest_version,
        knowledge_cutoff=None if cutoff is None else str(cutoff),
        datasets=datasets,
        legacy_raw_snapshot_ids=tuple(legacy_ids),
    )


@contextlib.contextmanager
def network_forbidden() -> Iterator[None]:
    """本进程内出站连接一律抛 `NetworkForbiddenError`。见模块头「能挡住什么」。

    ⚠️ 与 `tests/conftest.py` 的默认禁网**不是同一层**：那条是测试基建的
    default-deny（所有 test 默认生效），这条是生产回放路径自己的围栏。
    嵌套安全 —— 退出时恢复的是进入时看到的那三个函数，不是「真的 socket」。
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def _blocked(*_args, **_kwargs):
        raise NetworkForbiddenError("确定性回放期间禁止联网")

    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[assignment]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[assignment]
        socket.create_connection = original_create  # type: ignore[assignment]


def offline_replay_bundle(
    evidence_set_id: str, *, path=None, data_root: str = "data"
) -> ReplayBundle:
    """`build_replay_bundle` + 禁网围栏。回放的默认入口。"""
    with network_forbidden():
        return build_replay_bundle(evidence_set_id, path=path, data_root=data_root)
