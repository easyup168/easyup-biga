"""P3-4 full-market unadjusted EOD daily bars."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from easyup_biga.data.contracts import DataIssue, DatasetStatus
from easyup_biga.providers.eastmoney_eod import EodFetchResult, fetch_eod_snapshot

from ..records import DailyBar
from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.equity.daily_bars"
PROVIDER_ID = "eastmoney-eod"
JOB_ID = "eod-daily-bars"


def _inst(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("43", "83", "87", "88", "92")):
        return code + ".BJ"
    if code.startswith(("6", "68")):
        return code + ".SH"
    return code + ".SZ"


def normalize(
    rows: Iterable[Mapping[str, Any]], trade_date: str, retrieved_at: str
) -> tuple[DailyBar, ...]:
    out: list[DailyBar] = []
    for row in rows:
        values = [row.get(key) for key in ("f17", "f15", "f16", "f2")]
        # A listed-but-suspended security legitimately has no OHLC.  P3-5 records
        # that state in cn.security.tradability; raw OHLC stays honest and absent.
        if any(value in (None, "", "-") for value in values):
            continue
        out.append(
            DailyBar(
                _inst(row["f12"]),
                trade_date,
                float(row["f17"]),
                float(row["f15"]),
                float(row["f16"]),
                float(row["f2"]),
                None if row.get("f18") in (None, "", "-") else float(row["f18"]),
                0.0 if row.get("f5") in (None, "", "-") else float(row["f5"]),
                0.0 if row.get("f6") in (None, "", "-") else float(row["f6"]),
                None if row.get("f4") in (None, "", "-") else float(row["f4"]),
                None if row.get("f3") in (None, "", "-") else float(row["f3"]),
                retrieved_at,
                retrieved_at,
                PROVIDER_ID,
            )
        )
    return tuple(sorted(out, key=lambda item: item.instrument_id))


def quality(
    bars: tuple[DailyBar, ...],
    declared_total: int,
    *,
    expected_open: int | None = None,
) -> tuple[DatasetStatus, dict, tuple[DataIssue, ...]]:
    bad = sum(
        1
        for bar in bars
        if bar.high < max(bar.open, bar.close)
        or bar.low > min(bar.open, bar.close)
        or bar.high < bar.low
        or bar.volume_shares < 0
        or bar.amount_cny < 0
    )
    dup = len(bars) - len({bar.instrument_id for bar in bars})
    issues: list[DataIssue] = []
    if bad:
        issues.append(DataIssue("data.quality.invalid_ohlc", "CRITICAL", f"invalid bars={bad}"))
    if dup:
        issues.append(DataIssue("data.quality.duplicate_key", "CRITICAL", f"duplicates={dup}"))

    denominator = expected_open if expected_open is not None else declared_total
    coverage = len(bars) / denominator if denominator else 0.0
    if coverage < 0.95:
        issues.append(
            DataIssue(
                "data.quality.coverage_below_threshold",
                "ERROR",
                f"coverage={coverage:.4f}",
            )
        )
    status = (
        DatasetStatus.QUARANTINED
        if bad or dup
        else DatasetStatus.PARTIAL
        if coverage < 0.95
        else DatasetStatus.COMPLETE
    )
    return (
        status,
        {
            "declared_total": declared_total,
            "expected_open": denominator,
            "bar_count": len(bars),
            "coverage_ratio": coverage,
            "invalid_ohlc_count": bad,
            "duplicate_count": dup,
        },
        tuple(issues),
    )


def publish_result(
    result: EodFetchResult,
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
    expected_open: int | None = None,
) -> PublishResult:
    bars = normalize(result.rows, trade_date, result.retrieved_at)
    status, metrics, issues = quality(
        bars, result.declared_total, expected_open=expected_open
    )
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        provider_id=PROVIDER_ID,
        partition_key={"trade_date": trade_date},
        raw_text=result.raw_text,
        rows=[bar.to_dict() for bar in bars],
        as_of=trade_date,
        quality_status=status,
        quality_metrics=metrics,
        quality_issues=issues,
        new_revision=new_revision,
    )


def run(
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
    fetcher=fetch_eod_snapshot,
) -> PublishResult:
    date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
    result: EodFetchResult = fetcher()
    return publish_result(
        result,
        trade_date,
        db_path=db_path,
        data_root=data_root,
        new_revision=new_revision,
    )
