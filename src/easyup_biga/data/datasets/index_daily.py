"""把已在生产的 Phase-2 `index_daily` 冻结，桥接成 Phase-3 的 DatasetSnapshot 血缘。

取自外部 P3-2 实现包并审过。**这里不抓任何行情** —— 真实抓取仍然只发生在
`SnapshotCoordinator` 里一次，本适配器只是在**同一批不可变字节**上再发布一份
标准血缘视图。

🔴 它做的是真校验，不是橡皮图章
--------------------------------
发布之前逐个 symbol 核对：冻结的 raw 行真的存在、且它的 `content_sha256`
与 EvidenceSet manifest 里记的**逐字相同**。对不上就抛，不发布。
—— 一份「登记了但对不上原始字节」的血缘，比没有血缘更糟。

⚠️ `body_uri` 指向既有的 SQLite raw 行（`biga+sqlite://raw_market_snapshot/<id>`），
不复制字节、不建第二份 raw。P3-4 引入文件数据面之后，新数据集才会有真正的
文件 URI；这条桥不回头改写历史。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from easyup_biga.data.contracts import RawArtifact, new_raw_artifact_id
from easyup_biga.data.provider_registry import ProviderNotRegistered, provider_for_source
from easyup_biga.data.snapshots import (
    DatasetSnapshotService,
    SnapshotPublishRequest,
)
from easyup_biga.persistence import load_raw_snapshot

DATASET_ID = "cn.index.daily_bars"
JOB_ID = "index-daily-snapshot-bridge"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_size(snapshot: Mapping[str, Any]) -> int:
    raw_text = snapshot.get("raw_text")
    if raw_text is not None:
        return len(str(raw_text).encode("utf-8"))
    return len(_canonical_bytes(snapshot.get("payload")))


@dataclass(frozen=True, slots=True)
class IndexDailyPublishResult:
    snapshot_id: str
    data_run_id: str
    content_sha256: str
    as_of: str
    knowledge_cutoff: str


class IndexDailyDatasetBridge:
    def __init__(self, *, path=None, service: DatasetSnapshotService | None = None) -> None:
        self._path = path
        self._service = service or DatasetSnapshotService(path=path)

    def publish(
        self,
        *,
        evidence_set_id: str,
        decision_id: str | None,
        run_id: str | None,
        entries: Mapping[str, Mapping[str, Any]],
    ) -> IndexDailyPublishResult:
        if not entries:
            raise ValueError("index_daily bridge requires at least one frozen symbol")

        artifacts: list[RawArtifact] = []
        lineage: dict[str, dict[str, Any]] = {}
        providers: set[str] = set()
        as_of_values: list[str] = []
        retrieved_values: list[str] = []
        row_count = 0

        for symbol in sorted(entries):
            entry = entries[symbol]
            legacy_id = int(entry["snapshot_id"])
            raw = load_raw_snapshot(legacy_id, path=self._path)
            if raw is None:
                raise ValueError(f"legacy raw_market_snapshot {legacy_id} is missing")
            expected_hash = str(entry["content_sha256"])
            actual_hash = str(raw["content_sha256"])
            if actual_hash != expected_hash:
                raise ValueError(
                    f"legacy snapshot hash drift for {symbol}: manifest={expected_hash}, raw={actual_hash}"
                )

            # 🔴 按 source 反查**适配器级** provider_id，不取前缀。
            #    外部实现写的是 `.split(":", 1)[0]` —— 对指数日线恰好相等，
            #    对交易日历（`sina:` → 实际 `sina_calendar`）就错。
            provider_id = provider_for_source(DATASET_ID, str(raw["source"]))
            # 🔴 manifest 自报的 provider 必须与**落库的 raw 自己说的**一致。
            #    外部实现只看 `entry.get("provider_id") or raw["source"]` 的前缀，
            #    于是一个自报 provider_id="probe"、而 source 仍是 `sina:` 的
            #    fetcher 会被静默当成 sina 发布 —— manifest 说一套、血缘说另一套，
            #    而血缘的全部价值就在于它说的是真的。
            declared = entry.get("provider_id")
            if declared and str(declared) != provider_id:
                raise ProviderNotRegistered(
                    f"{symbol}: manifest 自报 provider_id={declared!r}，"
                    f"而落库 raw 的 source={raw['source']!r} 解析出 {provider_id!r}。\n"
                    f"  两者必须一致 —— 血缘不能描述一个与 raw 层不符的出处。")
            providers.add(provider_id)
            retrieved_at = str(raw["retrieved_at"])
            as_of = str(raw["as_of"])
            retrieved_values.append(retrieved_at)
            as_of_values.append(as_of)
            row_count += int(entry.get("bar_count") or len(raw.get("payload") or ()))

            request_fp = _sha256({
                "dataset_id": DATASET_ID,
                "symbol": symbol,
                "legacy_snapshot_id": legacy_id,
                "content_sha256": actual_hash,
            })
            artifact = RawArtifact(
                artifact_id=new_raw_artifact_id(),
                dataset_id=DATASET_ID,
                provider_id=provider_id,
                request_fingerprint=request_fp,
                body_uri=f"biga+sqlite://raw_market_snapshot/{legacy_id}",
                body_sha256=actual_hash,
                size_bytes=_raw_size(raw),
                retrieved_at=retrieved_at,
                content_type="application/json",
                compression=None,
                as_of=as_of,
                available_at=retrieved_at,
            )
            artifacts.append(artifact)
            lineage[symbol] = {
                "legacy_snapshot_id": legacy_id,
                "content_sha256": actual_hash,
                "provider_id": provider_id,
                "bar_count": int(entry.get("bar_count") or 0),
                "trade_date": entry.get("trade_date"),
            }

        if len(providers) != 1:
            raise ValueError(
                "P3-2 index_daily bridge expects one provider for one frozen bundle; "
                f"got {sorted(providers)}"
            )
        provider_id = next(iter(providers))
        bundle_hash = _sha256({
            "dataset_id": DATASET_ID,
            "evidence_set_id": evidence_set_id,
            "symbols": lineage,
        })
        as_of = max(as_of_values)
        knowledge_cutoff = max(retrieved_values)
        trigger_id = run_id or decision_id or evidence_set_id

        result = self._service.publish(
            SnapshotPublishRequest(
                dataset_id=DATASET_ID,
                job_id=JOB_ID,
                # P3-2 is a decision-scoped frozen bundle, not the historical EOD
                # partitioning introduced later in P3-4.
                partition_key={"evidence_set_id": evidence_set_id},
                trigger_id=trigger_id,
                provider_id=provider_id,
                raw_artifacts=tuple(artifacts),
                storage_format="sqlite_evidence_bundle",
                storage_uri=f"biga+sqlite://evidence_sets/{evidence_set_id}",
                content_sha256=bundle_hash,
                row_count=row_count,
                as_of=as_of,
                knowledge_cutoff=knowledge_cutoff,
                quality_metrics={
                    "symbol_count": len(entries),
                    "row_count": row_count,
                    "legacy_bridge": True,
                    "raw_hashes_verified": True,
                },
            )
        )
        return IndexDailyPublishResult(
            snapshot_id=result.snapshot_id,
            data_run_id=result.data_run_id,
            content_sha256=bundle_hash,
            as_of=as_of,
            knowledge_cutoff=knowledge_cutoff,
        )
