"""Rebuildable DuckDB analytics over the immutable Parquet data plane."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .file_store import _duckdb, _sql_path

if TYPE_CHECKING:
    import duckdb  # noqa: F401


def query_eod_between(
    *,
    data_root: Path | str = "data",
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Query latest COMPLETE-style EOD revisions across days.

    The physical data plane keeps every ``data_version`` in a separate immutable
    directory.  For analytical convenience this query selects the highest physical
    revision for each trade date; replay continues to read the exact URI frozen in a
    DatasetSnapshot instead of using this latest-view helper.
    """
    if len(start_date) != 8 or not start_date.isdigit():
        raise ValueError("start_date must be YYYYMMDD")
    if len(end_date) != 8 or not end_date.isdigit() or end_date < start_date:
        raise ValueError("end_date must be YYYYMMDD and >= start_date")

    pattern = (
        Path(data_root)
        / "lake"
        / "cn_equity_daily_bars"
        / "schema_version=1"
        / "trade_date=*"
        / "data_version=*"
        / "part-*.parquet"
    )
    # DuckDB raises on an empty glob; an empty range is a normal analytical result.
    if not list(Path(data_root).glob(
        "lake/cn_equity_daily_bars/schema_version=1/"
        "trade_date=*/data_version=*/part-*.parquet"
    )):
        return []

    db = _duckdb()
    con = db.connect(database=":memory:")
    try:
        # ``filename=true`` avoids relying on Hive partition type inference.
        # data_version is parsed only for selecting the newest immutable revision.
        sql = (
            "WITH versioned AS ("
            " SELECT *, CAST(regexp_extract(filename, "
            "'data_version=([0-9]+)', 1) AS BIGINT) AS _data_version"
            f" FROM read_parquet('{_sql_path(pattern)}', union_by_name=true, filename=true)"
            " WHERE trade_date BETWEEN ? AND ?"
            "), latest AS ("
            " SELECT *, MAX(_data_version) OVER (PARTITION BY trade_date) AS _latest_version"
            " FROM versioned"
            ") SELECT * EXCLUDE(filename, _data_version, _latest_version) FROM latest"
            " WHERE _data_version = _latest_version"
        )
        args: list[Any] = [start_date, end_date]
        ids = tuple(dict.fromkeys(instrument_ids or ()))
        if ids:
            sql += " AND instrument_id IN (" + ",".join("?" for _ in ids) + ")"
            args.extend(ids)
        sql += " ORDER BY trade_date, instrument_id"
        cur = con.execute(sql, args)
        columns = [item[0] for item in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _eod_snapshot_uris_as_of(*, db_path=None, knowledge_cutoff: str) -> dict[str, tuple[str, str]]:
    """trade_date → (snapshot_id, parquet uri)，只取在 cutoff 当时**可见**的那份。"""
    import json

    from easyup_biga.persistence import connect, load_dataset_partition

    from .contracts import DatasetStatus

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT snapshot_id,partition_key_json,knowledge_cutoff,data_version,created_at,"
            "manifest_json FROM dataset_snapshots WHERE dataset_id='cn.equity.daily_bars' "
            "AND status=? AND knowledge_cutoff<=? ORDER BY knowledge_cutoff,data_version,created_at",
            (DatasetStatus.COMPLETE.value, knowledge_cutoff),
        ).fetchall()
    chosen: dict[str, tuple[str, str, int, str, str]] = {}
    for row in rows:
        trade_date = str(json.loads(str(row["partition_key_json"])).get("trade_date") or "")
        if not trade_date:
            continue
        pids = tuple(str(x) for x in json.loads(str(row["manifest_json"])).get("partition_ids") or ())
        if len(pids) != 1:
            continue
        part = load_dataset_partition(pids[0], path=db_path)
        if part is None or str(part["storage_uri"]).startswith("biga+sqlite://"):
            continue
        rank = (str(row["knowledge_cutoff"]), int(row["data_version"]), str(row["created_at"]))
        prev = chosen.get(trade_date)
        if prev is None or rank > (prev[1], prev[2], prev[3]):
            chosen[trade_date] = (str(row["snapshot_id"]), *rank, str(part["storage_uri"]))
    return {day: (item[0], item[4]) for day, item in chosen.items()}


def query_eod_as_of(
    *,
    db_path=None,
    knowledge_cutoff: str,
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """P3-13 · 只用 `knowledge_cutoff` 当时可见的修订来查 —— 回测/复盘的入口。

    🔴 与 `query_eod_between` 的区别是**全部意义所在**：后者取盘上最新的那份，
    所以同一段回测今天跑和下个月跑会给出不同结果（中间来了一次修订），而且
    **不报错**。这条只认控制面里当时已经 COMPLETE 的快照，盘上更新的 Parquet
    文件即便存在也不读。

    ⚠️ 排序口径是 `(knowledge_cutoff, data_version, created_at)`。
    它假设「更正的那份 cutoff 也更晚」—— 对补采/回填是成立的。
    真出现「cutoff 更早、却是后发布的更正」那天，这里要改成按发布时刻排，
    而那需要先给快照落一个**发布时刻**字段；今天没有，所以不假装有。

    返回行上挂 `_snapshot_id` / `_knowledge_cutoff`，让消费方能说清这一行的来源。
    """
    if len(start_date) != 8 or not start_date.isdigit():
        raise ValueError("start_date must be YYYYMMDD")
    if len(end_date) != 8 or not end_date.isdigit() or end_date < start_date:
        raise ValueError("end_date must be YYYYMMDD and >= start_date")
    chosen = {
        day: pair
        for day, pair in _eod_snapshot_uris_as_of(
            db_path=db_path, knowledge_cutoff=knowledge_cutoff
        ).items()
        if start_date <= day <= end_date
    }
    if not chosen:
        return []
    source_list = "[" + ",".join(f"'{_sql_path(uri)}'" for _sid, uri in chosen.values()) + "]"
    db = _duckdb()
    con = db.connect(database=":memory:")
    try:
        sql = (f"SELECT * FROM read_parquet({source_list}, union_by_name=true) "
               "WHERE trade_date BETWEEN ? AND ?")
        args: list[Any] = [start_date, end_date]
        ids = tuple(dict.fromkeys(instrument_ids or ()))
        if ids:
            sql += " AND instrument_id IN (" + ",".join("?" for _ in ids) + ")"
            args.extend(ids)
        sql += " ORDER BY trade_date,instrument_id"
        cur = con.execute(sql, args)
        columns = [item[0] for item in cur.description]
        out = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()
    for row in out:
        row["_snapshot_id"] = chosen[str(row["trade_date"])][0]
        row["_knowledge_cutoff"] = knowledge_cutoff
    return out


def query_dataset_partition(
    dataset_id: str,
    partition_key: dict[str, str],
    *,
    db_path=None,
    data_root: Path | str = "data",
) -> list[dict[str, Any]]:
    """Read the latest COMPLETE immutable partition for a logical key."""
    from easyup_biga.persistence import find_dataset_snapshot, load_dataset_partition
    from .contracts import DatasetStatus
    from .file_store import FileStore

    snapshot = find_dataset_snapshot(
        dataset_id, partition_key, status=DatasetStatus.COMPLETE, path=db_path)
    if snapshot is None:
        return []
    pids = tuple(str(x) for x in snapshot["manifest"].get("partition_ids") or ())
    if len(pids) != 1:
        raise RuntimeError(f"{dataset_id} expected one partition, got {len(pids)}")
    part = load_dataset_partition(pids[0], path=db_path)
    if part is None:
        raise RuntimeError(f"missing DatasetPartition {pids[0]}")
    return FileStore(data_root).read_parquet_rows(str(part["storage_uri"]))


def query_tradability(trade_date: str, *, db_path=None, data_root: Path | str = "data"):
    return query_dataset_partition(
        "cn.security.tradability", {"trade_date": trade_date},
        db_path=db_path, data_root=data_root)


def query_adjustment_factors(trade_date: str, *, db_path=None, data_root: Path | str = "data"):
    return query_dataset_partition(
        "cn.equity.adjustment_factors", {"trade_date": trade_date},
        db_path=db_path, data_root=data_root)


def query_emotion_close(trade_date: str, *, db_path=None, data_root: Path | str = "data"):
    return query_dataset_partition(
        "cn.market.emotion_close", {"trade_date": trade_date},
        db_path=db_path, data_root=data_root)
