"""P3-5 adjustment factors as an independent point-in-time dataset.

Raw OHLC is never rewritten or pre-adjusted.  Consumers join the factor dataset at a
known cutoff when they explicitly need adjusted prices (screening/backtest/review).
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from easyup_biga.data.contracts import DataIssue, DatasetStatus

from ..records import AdjustmentFactorRecord
from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.equity.adjustment_factors"
JOB_ID = "adjustment-factor-sync"


class AdjustmentFactorProvider(Protocol):
    provider_id: str

    def load(self, trade_date: str) -> tuple[AdjustmentFactorRecord, ...]: ...


def _trade_date(value: str) -> str:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("trade_date must be YYYYMMDD")
    date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return value


class CsvAdjustmentFactorProvider:
    """Deterministic local-file adapter used until a production factor API is chosen."""

    provider_id = "csv-adjustment"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self, trade_date: str) -> tuple[AdjustmentFactorRecord, ...]:
        wanted = _trade_date(trade_date)
        out: list[AdjustmentFactorRecord] = []
        seen: set[str] = set()
        with self.path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {
                "instrument_id",
                "trade_date",
                "adjustment_factor",
                "available_at",
            }
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"adjustment CSV missing columns: {sorted(missing)}")
            for row in reader:
                if row["trade_date"] != wanted:
                    continue
                instrument_id = row["instrument_id"].strip()
                if not instrument_id:
                    raise ValueError("adjustment factor instrument_id is empty")
                if instrument_id in seen:
                    raise ValueError(f"duplicate adjustment factor: {instrument_id}/{wanted}")
                factor = float(row["adjustment_factor"])
                if factor <= 0:
                    raise ValueError(
                        f"adjustment_factor must be positive: {instrument_id}={factor}"
                    )
                available_at = row["available_at"].strip()
                datetime.fromisoformat(available_at)
                seen.add(instrument_id)
                out.append(
                    AdjustmentFactorRecord(
                        instrument_id,
                        wanted,
                        factor,
                        available_at,
                        self.provider_id,
                    )
                )
        return tuple(sorted(out, key=lambda item: item.instrument_id))


def _quality(rows: tuple[AdjustmentFactorRecord, ...]):
    invalid = [item.instrument_id for item in rows if item.adjustment_factor <= 0]
    duplicate_count = len(rows) - len({item.instrument_id for item in rows})
    issues: list[DataIssue] = []
    if invalid:
        issues.append(
            DataIssue(
                "data.quality.adjustment_factor_nonpositive",
                "CRITICAL",
                f"instruments={invalid[:10]}",
            )
        )
    if duplicate_count:
        issues.append(
            DataIssue(
                "data.quality.duplicate_key",
                "CRITICAL",
                f"duplicates={duplicate_count}",
            )
        )
    status = (
        DatasetStatus.PARTIAL
        if not rows
        else DatasetStatus.QUARANTINED
        if issues
        else DatasetStatus.COMPLETE
    )
    return status, {
        "row_count": len(rows),
        "duplicate_count": duplicate_count,
        "invalid_factor_count": len(invalid),
    }, tuple(issues)


def run(
    provider: AdjustmentFactorProvider,
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
) -> PublishResult:
    wanted = _trade_date(trade_date)
    rows = provider.load(wanted)
    status, metrics, issues = _quality(rows)
    raw = json.dumps(
        [item.to_dict() for item in rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        provider_id=provider.provider_id,
        partition_key={"trade_date": wanted},
        raw_text=raw,
        rows=[item.to_dict() for item in rows],
        as_of=wanted,
        quality_status=status,
        quality_metrics=metrics,
        quality_issues=issues,
        new_revision=new_revision,
    )
