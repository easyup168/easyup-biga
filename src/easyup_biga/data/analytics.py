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
