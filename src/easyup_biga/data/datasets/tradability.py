"""P3-5 daily tradability derived from Security Master + full-market EOD response."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from easyup_biga.data.contracts import DataIssue, DatasetStatus
from easyup_biga.providers.instrument_segments import a_share_segment

from ..records import TradabilityRecord, TradabilityStatus
from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.security.tradability"
PROVIDER_ID = "derived_biga"
JOB_ID = "tradability-sync"


def _provider_iid(row: Any) -> str | None:
    """🔴 判据来自**唯一**那份号段表，不在这里再抄一遍。

    这里曾经和 `eod_daily_bars._inst` 逐字相同 —— 那不是「两处各有理由」，
    是同一份抄了两遍。
    """
    segment = a_share_segment(getattr(row, "symbol", None),
                              market=getattr(row, "market_hint", None))
    if segment is None:
        return None
    suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}[segment.exchange]
    return f"{str(row.symbol).strip().zfill(6)}.{suffix}"


def _has_trade_price(row: Any) -> bool:
    return all(value not in (None, "", "-") for value in
               (row.open, row.high, row.low, row.close))


def derive(
    universe: Iterable[Mapping[str, Any]],
    bars: Iterable[Mapping[str, Any]],
    trade_date: str,
    available_at: str,
    *,
    provider_rows: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[TradabilityRecord, ...]:
    """Derive daily trading state without treating every absent bar as UNKNOWN.

    Full-market EOD rows include suspended/listed securities even when OHLC is absent.
    That lets P3-5 distinguish a real suspension from a Provider omission.  UNKNOWN is
    reserved for the latter and therefore remains a quality signal instead of silently
    becoming SUSPENDED.
    """
    bar_ids = {str(item["instrument_id"]) for item in bars}
    provider_by_id = {
        iid: row
        for row in (provider_rows or ())
        if (iid := _provider_iid(row)) is not None
    }
    out: list[TradabilityRecord] = []
    for security in universe:
        iid = str(security["instrument_id"])
        list_date = str(security.get("list_date") or "").replace("-", "")
        delist_date = str(security.get("delist_date") or "").replace("-", "")
        reason = None
        if list_date and list_date > trade_date:
            status = TradabilityStatus.NOT_LISTED
        elif delist_date and delist_date <= trade_date:
            status = TradabilityStatus.DELISTED
        elif iid in bar_ids:
            status = TradabilityStatus.OPEN
        elif iid in provider_by_id and not _has_trade_price(provider_by_id[iid]):
            status = TradabilityStatus.SUSPENDED
            reason = "provider_row_present_without_trade_price"
        else:
            status = TradabilityStatus.UNKNOWN
            reason = "security_master_member_missing_from_eod_provider"
        out.append(
            TradabilityRecord(
                iid,
                trade_date,
                status,
                suspension_reason=reason,
                available_at=available_at,
            )
        )
    return tuple(sorted(out, key=lambda item: item.instrument_id))


def quality(records: tuple[TradabilityRecord, ...]):
    unknown = sum(item.status == TradabilityStatus.UNKNOWN for item in records)
    status = DatasetStatus.COMPLETE if records and unknown == 0 else DatasetStatus.PARTIAL
    issues = () if unknown == 0 else (
        DataIssue(
            "data.quality.tradability_unknown",
            "ERROR",
            f"unknown_count={unknown}",
        ),
    )
    metrics = {
        "row_count": len(records),
        "open_count": sum(item.status == TradabilityStatus.OPEN for item in records),
        "suspended_count": sum(item.status == TradabilityStatus.SUSPENDED for item in records),
        "not_listed_count": sum(item.status == TradabilityStatus.NOT_LISTED for item in records),
        "delisted_count": sum(item.status == TradabilityStatus.DELISTED for item in records),
        "unknown_count": unknown,
    }
    return status, metrics, issues


def publish(
    records: tuple[TradabilityRecord, ...],
    trade_date: str,
    *,
    upstream_artifact_ids: tuple[str, ...],
    db_path=None,
    data_root="data",
    new_revision: bool = False,
) -> PublishResult:
    """发布可交易性。**必须**带上游 raw 血缘 —— 它是派生数据集。

    🔴 这里曾经把自己的输出重新序列化当 raw：

        raw = json.dumps([item.to_dict() for item in records])

    那让 `content_sha256` 变成对**输出**算的哈希 ⇒ 回放校验它等于自己证明
    自己，与真正的来源（行情源那次 EOD 响应）毫无关系。
    而日线走的是真响应 —— 同一次取数，两个数据集两套口径。

    ⚠️ 也不复制上游的字节到自己名下：那份 raw 的 `provider_id` 会写成
    `derived_biga`，读的人会以为这个 provider 返回过这些东西。
    **引用，不复制。**
    """
    if not upstream_artifact_ids:
        raise ValueError(
            "可交易性是派生数据集，必须声明它派生自哪份原始响应。"
            "调用方（eod_pipeline）应把日线那次发布的 raw_artifact_ids 传进来。")
    status, metrics, issues = quality(records)
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        provider_id=PROVIDER_ID,
        partition_key={"trade_date": trade_date},
        upstream_artifact_ids=upstream_artifact_ids,
        rows=[item.to_dict() for item in records],
        as_of=trade_date,
        quality_status=status,
        quality_metrics=metrics,
        quality_issues=issues,
        new_revision=new_revision,
    )
