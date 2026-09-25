"""P3-12 · point-in-time 与修订链审计。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：两个问题 ——「这份 EvidenceSet 今天还指着**当初那一份**吗」、
  「这个分区的多次修订是不是一条线性、递增、互不覆盖的链」
- **不覆盖**：字节级重算（那是 `integrity.py`）与回放清单构建（`replay.py`）

🔴 分区键的 JSON 编码走 `persistence.partition_key_json`，不在这里再写一遍
------------------------------------------------------------------------
本模块要用分区键去 `WHERE partition_key_json=?`。那个字符串必须和写侧**逐字节
相同**，否则查询返回零行 —— 而零行在下面会被读成「这个分区没有快照」，
听起来像个结论，其实是键编码对不上。⇒ 静默 fail-open（R-3），不是报错。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from easyup_biga.persistence import (
    connect,
    list_evidence_set_datasets,
    load_dataset_snapshot,
    partition_key_json,
)

from .replay import build_replay_bundle


@dataclass(frozen=True, slots=True)
class LineageAudit:
    ok: bool
    checks: tuple[str, ...]
    errors: tuple[str, ...]


def audit_revision_chain(
    dataset_id: str, partition_key: dict[str, str], *, path=None
) -> LineageAudit:
    """核对某个分区的修订链：线性、`data_version` 递增、物理 uri 不复用。"""
    key_json = partition_key_json(partition_key)
    checks: list[str] = []
    errors: list[str] = []
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT snapshot_id,data_version,supersedes_snapshot_id,manifest_json,content_sha256 "
            "FROM dataset_snapshots WHERE dataset_id=? AND partition_key_json=? "
            "ORDER BY data_version,created_at",
            (dataset_id, key_json),
        ).fetchall()
        partition_rows = {
            str(row["partition_id"]): dict(row)
            for row in conn.execute(
                "SELECT partition_id,storage_uri,content_sha256,data_version,supersedes_partition_id "
                "FROM dataset_partitions WHERE dataset_id=? AND partition_key_json=?",
                (dataset_id, key_json),
            ).fetchall()
        }
    if not rows:
        return LineageAudit(False, (), (f"该分区没有任何快照：{dataset_id} {key_json}",))

    previous_snapshot_id: str | None = None
    previous_version = 0
    seen_uris: set[str] = set()
    for row in rows:
        sid = str(row["snapshot_id"])
        version = int(row["data_version"])
        if version <= previous_version:
            errors.append(f"{sid} 的 data_version 没有递增：{version} <= {previous_version}")
        if previous_snapshot_id is None:
            if row["supersedes_snapshot_id"] is not None:
                errors.append(f"首个修订却声称取代了 {row['supersedes_snapshot_id']}")
        elif str(row["supersedes_snapshot_id"] or "") != previous_snapshot_id:
            errors.append(
                f"修订链在 {sid} 断了：supersedes={row['supersedes_snapshot_id']} "
                f"应为 {previous_snapshot_id}")
        manifest = json.loads(str(row["manifest_json"]))
        pids = tuple(str(x) for x in manifest.get("partition_ids") or ())
        if not pids:
            errors.append(f"快照 {sid} 没有任何分区")
        for pid in pids:
            part = partition_rows.get(pid)
            if part is None:
                errors.append(f"快照 {sid} 引用了不存在的分区 {pid}")
                continue
            if int(part["data_version"]) != version:
                errors.append(f"分区 {pid} 的 data_version 与快照 {sid} 不一致")
            uri = str(part["storage_uri"])
            if uri in seen_uris:
                errors.append(f"两个修订复用了同一个物理 uri：{uri}")
            seen_uris.add(uri)
        previous_snapshot_id = sid
        previous_version = version
    checks.append(f"线性修订链：{len(rows)} 个快照")
    checks.append(f"物理血缘互不复用：{len(seen_uris)} 个 uri")
    return LineageAudit(not errors, tuple(checks), tuple(errors))


def audit_evidence_set_point_in_time(
    evidence_set_id: str,
    *,
    path=None,
    data_root: str = "data",
) -> LineageAudit:
    """核对一份 EvidenceSet 仍然冻结，且没有混进它 cutoff 之后的数据。

    🔴 这里要防的是**向前滑动**：同一个 dataset 后来出了 v2 修订，而历史
    EvidenceSet 悄悄开始解析到新的那份。那不会报错，只会让同一个决策在
    今天回放出另一个答案 —— 复盘因此永远追不上事故当时看到的东西。
    """
    checks: list[str] = []
    errors: list[str] = []
    try:
        bundle = build_replay_bundle(evidence_set_id, path=path, data_root=data_root)
    except Exception as exc:
        return LineageAudit(False, (), (str(exc),))

    # 一次查出来，不在循环里反复查库
    linked = {
        str(row["dataset_id"]): str(row["snapshot_id"])
        for row in list_evidence_set_datasets(evidence_set_id, path=path)
    }
    cutoff = bundle.knowledge_cutoff
    for dataset_id, ref in sorted(bundle.datasets.items()):
        if cutoff is not None and ref.knowledge_cutoff > cutoff:
            errors.append(
                f"{dataset_id} 快照的 cutoff {ref.knowledge_cutoff} 晚于 EvidenceSet 的 {cutoff}")
        if linked.get(dataset_id) != ref.snapshot_id:
            errors.append(
                f"{dataset_id} 的冻结引用漂了：库里 {linked.get(dataset_id)} != {ref.snapshot_id}")
        if load_dataset_snapshot(ref.snapshot_id, path=path) is None:
            errors.append(f"快照消失了：{ref.snapshot_id}")
    checks.append(f"精确冻结的 dataset 引用：{len(bundle.datasets)} 个")
    checks.append(f"旧 raw 引用已重算：{len(bundle.legacy_raw_snapshot_ids)} 条")
    if cutoff is not None:
        checks.append(f"knowledge_cutoff 已生效：{cutoff}")
    return LineageAudit(not errors, tuple(checks), tuple(errors))
