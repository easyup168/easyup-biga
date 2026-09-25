"""P3-16 · 上线验收证据账本 —— 哈希链式 JSONL。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：把**真实发生过**的验收事件一条条记下来，并让任何事后修改都露馅；
  以及按这些事件算出「Phase 3 的上线闸门过了没有」
- **不覆盖**：判断某次演练算不算过 —— 那是 `drills.py`，它读真实运行痕迹

🔴 为什么要哈希链，而不是一张表
-------------------------------
这份账本的读者是**未来的自己**，用途是回答「当初凭什么宣布过了」。
一个能被无痕编辑的记录回答不了这个问题 —— 它只能证明「现在有人认为过了」。
链把每条的哈希塞进下一条，改中间任何一行，后面全部对不上。

⚠️ 它不是防黑客的，是防**自己**的：三个月后补一条 PASS 把闸门凑齐，
比伪造签名容易得多，也更可能真的发生。

⚠️ 写入用「整份重写 + 原子替换」，不是 `O_APPEND`
-------------------------------------------------
看起来违反仓库的「只追加」原则，其实相反：追加一半断电会留下**半行 JSON**，
那会让整份账本从断点起不可解析；原子替换要么全到要么没到。
只追加这件事由**哈希链**保证，不是由写法保证。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from easyup_biga.domain import now_cn

#: 连续交易日的门槛。定在 5 是因为它要跨一个完整交易周。
REQUIRED_EOD_DAYS = 5

#: 六项演练。名字与 `drills.py` 里 `DrillResult.name` 一一对应 —— 别各写一套。
DRILL_TYPES = (
    "PRIMARY_FALLBACK",
    "QUARANTINED",
    "REVISION_V2",
    "RAW_TAMPER",
    "SOURCE_ZIP_REPLAY",
    "DUCKDB_RUNTIME",
)
ACCEPTANCE_EVENT_TYPES = frozenset({"EOD_COMPLETE", *DRILL_TYPES})


@dataclass(frozen=True, slots=True)
class AcceptanceEvent:
    event_type: str
    status: str
    at: str
    trade_date: str | None
    detail: dict[str, Any]
    previous_hash: str | None
    event_hash: str


@dataclass(frozen=True, slots=True)
class AcceptanceStatus:
    passed: bool
    eod_days: tuple[str, ...]
    drills: dict[str, bool]
    errors: tuple[str, ...]


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _event_payload(
    event_type: str,
    status: str,
    at: str,
    trade_date: str | None,
    detail: dict[str, Any],
    previous_hash: str | None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "status": status,
        "at": at,
        "trade_date": trade_date,
        "detail": detail,
        "previous_hash": previous_hash,
    }


def load_acceptance_events(path: Path | str) -> list[AcceptanceEvent]:
    """读账本并**逐行验链**。任何一行被改过就抛，不返回「大部分是好的」。"""
    file = Path(path)
    if not file.exists():
        return []
    events: list[AcceptanceEvent] = []
    expected_previous: str | None = None
    for line_no, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        payload = _event_payload(
            str(row["event_type"]),
            str(row["status"]),
            str(row["at"]),
            None if row.get("trade_date") is None else str(row["trade_date"]),
            dict(row.get("detail") or {}),
            None if row.get("previous_hash") is None else str(row["previous_hash"]),
        )
        actual = _hash(payload)
        if str(row.get("event_hash") or "") != actual:
            raise ValueError(f"验收账本第 {line_no} 行 hash mismatch —— 这一行被改过")
        if payload["previous_hash"] != expected_previous:
            raise ValueError(f"验收账本第 {line_no} 行链断了 —— 前面有行被删或被插")
        events.append(AcceptanceEvent(**payload, event_hash=actual))
        expected_previous = actual
    return events


def record_acceptance_event(
    path: Path | str,
    event_type: str,
    *,
    status: str = "PASS",
    trade_date: str | None = None,
    detail: dict[str, Any] | None = None,
    at: str | None = None,
) -> AcceptanceEvent:
    """追加一条验收事件。写之前先验整条链 —— 链坏了就不让再往上加。"""
    if event_type not in ACCEPTANCE_EVENT_TYPES:
        raise ValueError(
            f"未知的验收事件类型 {event_type!r}，可用：{sorted(ACCEPTANCE_EVENT_TYPES)}")
    if status not in {"PASS", "FAIL"}:
        raise ValueError("status 只能是 PASS 或 FAIL")
    if event_type == "EOD_COMPLETE" and (
        trade_date is None or len(trade_date) != 8 or not trade_date.isdigit()
    ):
        raise ValueError("EOD_COMPLETE 必须带 trade_date=YYYYMMDD")
    file = Path(path)
    prior = load_acceptance_events(file)
    previous_hash = prior[-1].event_hash if prior else None
    # 🔴 北京时间。全仓唯一的 UTC 例外在 _store/runtime.py 的读取边界，不在这里。
    timestamp = at or now_cn().isoformat()
    payload = _event_payload(event_type, status, timestamp, trade_date, detail or {}, previous_hash)
    row = dict(payload, event_hash=_hash(payload))

    file.parent.mkdir(parents=True, exist_ok=True)
    existing = file.read_bytes() if file.exists() else b""
    data = existing + _canonical(row) + b"\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, file)
    finally:
        if tmp.exists():
            tmp.unlink()
    return AcceptanceEvent(**payload, event_hash=row["event_hash"])


def _has_consecutive_trading_days(
    dates: list[str],
    trading_days_between: Callable[[str, str], Iterable[str]] | None,
) -> bool:
    """是否存在连续 `REQUIRED_EOD_DAYS` 个**交易日**都记了 EOD。

    🔴 没有日历就返回 False，不猜。
    「周一到周五就是连续 5 天」在 A 股是错的（春节、国庆、调休），
    而猜出来的连续性会让闸门在**没真跑满**的情况下开 —— 这是 R-3 的
    fail-open 形状：算不出来必须说算不出来。
    """
    unique = sorted(set(dates))
    if len(unique) < REQUIRED_EOD_DAYS or trading_days_between is None:
        return False
    for i in range(len(unique) - REQUIRED_EOD_DAYS + 1):
        window = unique[i: i + REQUIRED_EOD_DAYS]
        if tuple(window) == tuple(trading_days_between(window[0], window[-1])):
            return True
    return False


def evaluate_acceptance(
    events: Iterable[AcceptanceEvent],
    *,
    trading_days_between: Callable[[str, str], Iterable[str]] | None = None,
) -> AcceptanceStatus:
    """按账本里的事件算闸门状态。没有证据 = 不过，不是「待定」。"""
    items = tuple(events)
    errors: list[str] = []
    passed_eod_dates = [
        str(e.trade_date)
        for e in items
        if e.event_type == "EOD_COMPLETE" and e.status == "PASS" and e.trade_date
    ]
    if not _has_consecutive_trading_days(passed_eod_dates, trading_days_between):
        errors.append(f"没有证据表明连续 {REQUIRED_EOD_DAYS} 个交易日都跑通了 EOD")
    drills = {
        kind: any(e.event_type == kind and e.status == "PASS" for e in items)
        for kind in DRILL_TYPES
    }
    errors.extend(f"缺少通过的演练：{kind}" for kind, ok in drills.items() if not ok)
    return AcceptanceStatus(
        passed=not errors,
        eod_days=tuple(sorted(set(passed_eod_dates))),
        drills=drills,
        errors=tuple(errors),
    )


def trading_days_from_db(db_path=None) -> Callable[[str, str], tuple[str, ...]]:
    """用控制面里的**真实交易日历**判定连续性（`fact_trading_calendar`，批 L）。"""
    from easyup_biga.persistence import connect

    def _between(start: str, end: str) -> tuple[str, ...]:
        with connect(db_path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT trade_date,is_open FROM fact_trading_calendar "
                "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date,retrieved_at DESC",
                (start, end),
            ).fetchall()
        # 同一天可能有多条（日历本身会被重采）；按 retrieved_at 倒序取第一条 = 最新那份
        latest: dict[str, int] = {}
        for row in rows:
            latest.setdefault(str(row["trade_date"]), int(row["is_open"]))
        return tuple(day for day in sorted(latest) if latest[day] == 1)

    return _between
