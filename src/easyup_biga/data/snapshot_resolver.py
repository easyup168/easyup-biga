"""Point-in-time and EvidenceSet-exact DatasetSnapshot resolution."""
from __future__ import annotations

from typing import Iterable

from easyup_biga.timebase import cn_iso, parse_aware

from easyup_biga.data.contracts import DatasetStatus, DatasetLink
from easyup_biga.persistence import (
    connect,
    link_evidence_set_dataset,
    list_evidence_set_datasets,
    load_dataset_snapshot,
)

from .records import ResolvedSnapshot


def _resolved(snapshot: dict) -> ResolvedSnapshot:
    return ResolvedSnapshot(
        str(snapshot["dataset_id"]),
        str(snapshot["snapshot_id"]),
        str(snapshot["status"]),
        cn_iso(str(snapshot["knowledge_cutoff"]), name="snapshot knowledge_cutoff"),
        snapshot["partition_key"],
    )


def resolve_snapshot(
    dataset_id: str,
    knowledge_cutoff: str,
    *,
    allow_partial: bool = False,
    path=None,
) -> ResolvedSnapshot | None:
    allowed = [DatasetStatus.COMPLETE.value] + (
        [DatasetStatus.PARTIAL.value] if allow_partial else []
    )
    q = ",".join("?" for _ in allowed)
    cutoff = parse_aware(knowledge_cutoff, name="knowledge_cutoff")
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            f"SELECT snapshot_id,knowledge_cutoff,data_version,created_at FROM dataset_snapshots "
            f"WHERE dataset_id=? AND status IN ({q})",
            (dataset_id, *allowed),
        ).fetchall()
    visible = [
        row for row in rows
        if parse_aware(str(row["knowledge_cutoff"]), name="snapshot knowledge_cutoff") <= cutoff
    ]
    if not visible:
        return None
    row = max(
        visible,
        key=lambda item: (
            parse_aware(str(item["knowledge_cutoff"]), name="snapshot knowledge_cutoff"),
            int(item["data_version"]),
            parse_aware(str(item["created_at"]), name="snapshot created_at"),
            str(item["snapshot_id"]),
        ),
    )
    snapshot = load_dataset_snapshot(str(row["snapshot_id"]), path=path)
    return _resolved(snapshot) if snapshot is not None else None


def resolve_many(
    dataset_ids: Iterable[str],
    knowledge_cutoff: str,
    *,
    allow_partial: bool = False,
    path=None,
) -> dict[str, ResolvedSnapshot]:
    out: dict[str, ResolvedSnapshot] = {}
    for dataset_id in dataset_ids:
        item = resolve_snapshot(
            dataset_id, knowledge_cutoff, allow_partial=allow_partial, path=path
        )
        if item:
            out[dataset_id] = item
    return out


def resolve_evidence_set_snapshot(
    evidence_set_id: str, dataset_id: str, *, path=None
) -> ResolvedSnapshot | None:
    """Resolve the exact frozen snapshot; never fall forward to a newer revision."""
    row = next(
        (
            item
            for item in list_evidence_set_datasets(evidence_set_id, path=path)
            if item["dataset_id"] == dataset_id
        ),
        None,
    )
    if row is None:
        return None
    snapshot = load_dataset_snapshot(str(row["snapshot_id"]), path=path)
    if snapshot is None:
        raise RuntimeError(
            f"EvidenceSet {evidence_set_id} references missing snapshot {row['snapshot_id']}"
        )
    if snapshot["dataset_id"] != dataset_id:
        raise RuntimeError("EvidenceSet dataset link points to the wrong dataset")
    if snapshot["status"] != DatasetStatus.COMPLETE.value:
        raise RuntimeError("EvidenceSet links a non-COMPLETE snapshot")
    return _resolved(snapshot)


def bind_to_evidence_set(
    evidence_set_id: str, resolved: dict[str, ResolvedSnapshot], *, path=None
) -> None:
    existing = {
        str(row["dataset_id"]): str(row["snapshot_id"])
        for row in list_evidence_set_datasets(evidence_set_id, path=path)
    }
    for dataset_id, snapshot in sorted(resolved.items()):
        if dataset_id in existing:
            if existing[dataset_id] != snapshot.snapshot_id:
                raise RuntimeError(
                    f"EvidenceSet {evidence_set_id} already freezes {dataset_id}="
                    f"{existing[dataset_id]}; refusing replacement with {snapshot.snapshot_id}"
                )
            continue
        link_evidence_set_dataset(
            DatasetLink(evidence_set_id, dataset_id, snapshot.snapshot_id),
            path=path,
        )
