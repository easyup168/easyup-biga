"""P3-4 full-market unadjusted EOD daily bars."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from easyup_biga.data.contracts import DataIssue, DatasetStatus
from easyup_biga.providers.eastmoney_eod import fetch_eod_snapshot
from easyup_biga.providers.eod_bar import EodBar, EodFetchResult
from easyup_biga.providers.sina_eod import (
    fetch_eod_snapshot as sina_fetch_eod_snapshot,
)

from ..records import DailyBar
from ..failover import ProviderExecutionAttempt, execute_with_fallback
from ..publication import DatasetRowPublisher, PublishResult

DATASET_ID = "cn.equity.daily_bars"
#: 🔴 **主源换成了新浪**（2026-09-26）。换源的依据不是「东财那天挂了」，
#:    而是两条长期证据：同机另一套长期运行的实例每天的全市场日线走的就是
#:    这个端点；以及公开的 A 股数据源目录把东财标为「共用同一套风控、
#:    IP 被封会成片失联」。我们这次就撞上了成片失联。
#:    ⚠️ 口径差异（自报总数 / 成交量单位 / 停牌股是否返回）写在
#:      `providers/sina_eod.py` 的模块头里。
PROVIDER_ID = "sina_eod"
FALLBACK_PROVIDER_ID = "eastmoney_eod"
JOB_ID = "eod-daily-bars"


def _inst(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("43", "83", "87", "88", "92")):
        return code + ".BJ"
    if code.startswith(("6", "68")):
        return code + ".SH"
    return code + ".SZ"


def _f(value: Any, default: float | None = None) -> float | None:
    return default if value in (None, "", "-") else float(value)


def normalize(
    rows: Iterable[EodBar],
    trade_date: str,
    retrieved_at: str,
    *,
    provider_id: str = PROVIDER_ID,
) -> tuple[DailyBar, ...]:
    """把 **provider 中立行** 归一成日线。

    🔴 入参从「东财的字段名 dict」改成了 `EodBar`（2026-09-26）——
    与 `security_master` 同一个理由：加第二个源时，让新 provider 把字段
    伪装成 `f17`，或者在这里加「这是哪家」的分支，两条都错。

    ⚠️ `provider_id` 现在是**参数**，不是模块常量：日线有主源与备用源，
    而溯源必须写**实际供数的那家** —— 写死主源会让降级过的那天
    在库里看起来像正常的一天。
    """
    out: list[DailyBar] = []
    for row in rows:
        # A listed-but-suspended security legitimately has no OHLC.  P3-5 records
        # that state in cn.security.tradability; raw OHLC stays honest and absent.
        if any(value in (None, "", "-") for value in
               (row.open, row.high, row.low, row.close)):
            continue
        out.append(
            DailyBar(
                _inst(row.symbol),
                trade_date,
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                _f(row.prev_close),
                _f(row.volume, 0.0),
                _f(row.amount, 0.0),
                _f(row.change_amount),
                _f(row.change_percent),
                retrieved_at,
                retrieved_at,
                provider_id,
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


def fetch_with_fallback() -> tuple[
    EodFetchResult, str, tuple[tuple[str, str, bool, str | None], ...]
]:
    """按注册表顺序取全市场日线，返回 `(结果, 实际供数方, 真实尝试链)`。

    🔴 尝试链要带出去：主源失败那一半必须落进 `provider_attempts`，
    否则「降级过的那天」在库里和正常的一天长得一模一样。
    """
    attempts: list[ProviderExecutionAttempt] = []
    outcome = execute_with_fallback(
        DATASET_ID,
        {
            PROVIDER_ID: sina_fetch_eod_snapshot,
            FALLBACK_PROVIDER_ID: fetch_eod_snapshot,
        },
        on_attempt=attempts.append,
    )
    return (
        outcome.value,
        outcome.provider_id,
        tuple((a.provider_id, a.role.value, a.succeeded, a.error) for a in attempts),
    )


def publish_result(
    result: EodFetchResult,
    trade_date: str,
    *,
    db_path=None,
    data_root="data",
    new_revision: bool = False,
    expected_open: int | None = None,
    served_by: str = PROVIDER_ID,
    failover_attempts: tuple[tuple[str, str, bool, str | None], ...] = (),
) -> PublishResult:
    bars = normalize(result.rows, trade_date, result.retrieved_at,
                     provider_id=served_by)
    status, metrics, issues = quality(
        bars, result.declared_total, expected_open=expected_open
    )
    return DatasetRowPublisher(db_path=db_path, data_root=data_root).publish(
        dataset_id=DATASET_ID,
        job_id=JOB_ID,
        # 🔴 写**实际供数方**，不是 dataset 的 primary。
        provider_id=served_by,
        failover_attempts=failover_attempts,
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
    fetcher=None,
) -> PublishResult:
    date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
    result, served_by, attempts = (
        (fetcher(), PROVIDER_ID, ()) if fetcher is not None else fetch_with_fallback())
    return publish_result(
        result,
        trade_date,
        db_path=db_path,
        data_root=data_root,
        new_revision=new_revision,
        served_by=served_by,
        failover_attempts=attempts,
    )
