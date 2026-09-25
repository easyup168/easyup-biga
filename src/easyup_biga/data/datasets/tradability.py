"""P3-5 daily tradability derived from Security Master + full-market EOD response."""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from easyup_biga.data.contracts import DataIssue, DatasetStatus

from ..records import TradabilityRecord, TradabilityStatus
from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.security.tradability"
PROVIDER_ID = "derived-biga"
JOB_ID = "tradability-sync"


def _provider_iid(row: Mapping[str, Any]) -> str | None:
    raw = row.get("f12")
    if raw in (None, "", "-"):
        return None
    code = str(raw).zfill(6)
    if code.startswith(("43", "83", "87", "88", "92")):
        return code + ".BJ"
    if code.startswith(("6", "68")):
        return code + ".SH"
    return code + ".SZ"


def _has_trade_price(row: Mapping[str, Any]) -> bool:
    return all(row.get(key) not in (None, "", "-") for key in ("f17", "f15", "f16", "f2"))


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
    db_path=None,
    data_root="data",
    new_revision: bool = False,
) -> PublishResult:
    status, metrics, issues = quality(records)
    raw = json.dumps([item.to_dict() for item in records], ensure_ascii=False, sort_keys=True)
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        provider_id=PROVIDER_ID,
        partition_key={"trade_date": trade_date},
        raw_text=raw,
        rows=[item.to_dict() for item in records],
        as_of=trade_date,
        quality_status=status,
        quality_metrics=metrics,
        quality_issues=issues,
        new_revision=new_revision,
    )
