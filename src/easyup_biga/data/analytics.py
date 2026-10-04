"""Rebuildable DuckDB analytics over the immutable Parquet data plane."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from easyup_biga.timebase import cn_iso, parse_aware

from .file_store import _duckdb, _sql_path
from .registry import get_dataset

if TYPE_CHECKING:
    import duckdb  # noqa: F401


def _validate_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    """YYYYMMDD 且 end >= start。日期区间校验的**唯一实现**。

    ⚠️ 合 batch2 时这个函数差点没被带进来 —— 外部包里它是 batch1 版本就有的，
    而本仓库 batch1 把校验内联进了 `_query_as_of`。新函数 import 得通、
    调用才 NameError。**「import 过了」不等于「跑得通」。**
    """
    if len(start_date) != 8 or not start_date.isdigit():
        raise ValueError("start_date must be YYYYMMDD")
    if len(end_date) != 8 or not end_date.isdigit() or end_date < start_date:
        raise ValueError("end_date must be YYYYMMDD and >= start_date")
    return start_date, end_date


def resolve_dataset_snapshots_as_of(
    dataset_id: str,
    *,
    db_path=None,
    knowledge_cutoff: str,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, Any]]:
    """cutoff 当时每个 trade_date **最新的那份快照，不论什么状态**。

    🔴 与 `_snapshot_uris_as_of` 的区别必须说清楚，否则它们是同一张表上的两套口径：

    | | `_snapshot_uris_as_of` | 这一条 |
    |---|---|---|
    | 过滤 | `status = COMPLETE` | 不过滤状态 |
    | 一天有 v1=COMPLETE、v2=QUARANTINED 时 | 返回 **v1**（静默降级） | 返回 **v2**（并告诉你它不是 COMPLETE） |

    ⇒ Phase 4 的读取方必须**先看状态、再决定读不读**：`TargetResolver` 用这一条选出
    「哪一份是最新的」，再拿它的 `snapshot_id` 与行上的 `_snapshot_id` 对账 ——
    对不上就整天不用。这样一次新的 QUARANTINED 修订会**挡住**旧的 COMPLETE，
    而不是被它盖过去。

    ⚠️ 没有直接把 `_snapshot_uris_as_of` 改成严格版：那会改变 Phase 3 已上线的
    `query_eod_as_of` 语义，而 Phase 3 正在走 Live Acceptance。两条的关系由
    `test_两条快照选择器的口径差异是有意的` 钉住，不靠读者记得。

    时间比较解析成 aware datetime 再比，不靠 SQLite 的字面序 ——
    两行 `knowledge_cutoff` 的时区偏移不同时，字面序会给出错的答案。
    """
    import json

    from easyup_biga.persistence import connect

    start_date, end_date = _validate_date_range(start_date, end_date)
    get_dataset(dataset_id)
    cutoff_dt = parse_aware(knowledge_cutoff, name="knowledge_cutoff")
    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT snapshot_id,partition_key_json,knowledge_cutoff,status,data_version,created_at "
            "FROM dataset_snapshots WHERE dataset_id=? "
            "ORDER BY knowledge_cutoff,data_version,created_at,snapshot_id",
            (dataset_id,),
        ).fetchall()
    chosen: dict[str, tuple[tuple[object, int, object, str], dict[str, Any]]] = {}
    for row in rows:
        row_cutoff = parse_aware(str(row["knowledge_cutoff"]), name="dataset snapshot knowledge_cutoff")
        created = parse_aware(str(row["created_at"]), name="dataset snapshot created_at")
        if row_cutoff > cutoff_dt:
            continue
        trade_date = str(json.loads(str(row["partition_key_json"])).get("trade_date") or "")
        if not trade_date or not (start_date <= trade_date <= end_date):
            continue
        item = {
            "snapshot_id": str(row["snapshot_id"]),
            "trade_date": trade_date,
            "knowledge_cutoff": cn_iso(str(row["knowledge_cutoff"]), name="snapshot knowledge_cutoff"),
            "status": str(row["status"]),
            "data_version": int(row["data_version"]),
            "created_at": str(row["created_at"]),
        }
        rank = (row_cutoff, item["data_version"], created, item["snapshot_id"])
        prev = chosen.get(trade_date)
        if prev is None or rank > prev[0]:
            chosen[trade_date] = (rank, item)
    return {day: value[1] for day, value in chosen.items()}


def query_eod_between(
    *,
    db_path=None,
    data_root: Path | str = "data",
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Query the latest COMPLETE EOD revision across days via the control plane.

    P3-R2 deliberately stopped scanning ``lake/``.  A physical Parquet file can exist
    without a committed DatasetSnapshot after a process crash; such an orphan must be
    invisible to every supported reader.  Therefore this latest-view helper first asks
    SQLite which COMPLETE snapshot is current for each trade date, then reads only those
    immutable URIs.  Replay/as-of queries use the same control-plane rule with a cutoff.
    """
    _ = data_root  # URI 来自控制面的分区记录；保留形参只为不动既有调用方
    return _query_latest_between(
        "cn.equity.daily_bars",
        db_path=db_path,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=instrument_ids,
    )


def _snapshot_uris_as_of(
    dataset_id: str, *, db_path=None, knowledge_cutoff: str
) -> dict[str, tuple[str, str]]:
    """trade_date → (snapshot_id, parquet uri)，只取在 cutoff 当时**可见**的那份。

    🔴 P3-R2 的不变量在这里，**对每一个按 trade_date 切片的数据集都成立**：
    先问 SQLite「这天哪一份快照是 COMPLETE」，再去读它那个不可变 URI；
    永远不扫 `lake/` 来推断事实。崩在两阶段提交中间留下的孤儿 Parquet
    因此对所有读取方都不可见。

    ⚠️ P4-0 让 `cn.index.eod_bars` **复用这同一个选择器**，而不是另写一条 ——
    第二条真相路径就是第二套口径，它会在某次崩溃之后与这条给出不同的答案。
    """
    import json

    from easyup_biga.persistence import connect, load_dataset_partition

    from .contracts import DatasetStatus

    get_dataset(dataset_id)  # 没注册的 id 在碰存储之前就炸，不静默返回空
    cutoff_dt = parse_aware(knowledge_cutoff, name="knowledge_cutoff")
    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT snapshot_id,partition_key_json,knowledge_cutoff,data_version,created_at,"
            "manifest_json FROM dataset_snapshots WHERE dataset_id=? AND status=?",
            (dataset_id, DatasetStatus.COMPLETE.value),
        ).fetchall()
    chosen: dict[str, tuple[tuple[object, int, object, str], str, str]] = {}
    for row in rows:
        row_cutoff = parse_aware(str(row["knowledge_cutoff"]), name="snapshot knowledge_cutoff")
        if row_cutoff > cutoff_dt:
            continue
        trade_date = str(json.loads(str(row["partition_key_json"])).get("trade_date") or "")
        if not trade_date:
            continue
        pids = tuple(str(x) for x in json.loads(str(row["manifest_json"])).get("partition_ids") or ())
        if len(pids) != 1:
            continue
        part = load_dataset_partition(pids[0], path=db_path)
        if part is None or str(part["storage_uri"]).startswith("biga+sqlite://"):
            continue
        rank = (
            row_cutoff,
            int(row["data_version"]),
            parse_aware(str(row["created_at"]), name="snapshot created_at"),
            str(row["snapshot_id"]),
        )
        prev = chosen.get(trade_date)
        if prev is None or rank > prev[0]:
            chosen[trade_date] = (rank, str(row["snapshot_id"]), str(part["storage_uri"]))
    return {day: (item[1], item[2]) for day, item in chosen.items()}


def _query_as_of(
    dataset_id: str,
    *,
    db_path=None,
    knowledge_cutoff: str,
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """按 cutoff 选快照、只读那些不可变 URI —— 所有 PIT 查询的唯一实现。"""
    start_date, end_date = _validate_date_range(start_date, end_date)
    chosen = {
        day: pair
        for day, pair in _snapshot_uris_as_of(
            dataset_id, db_path=db_path, knowledge_cutoff=knowledge_cutoff
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


def _query_latest_between(
    dataset_id: str,
    *,
    db_path=None,
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """「盘上最新」视图 —— 与 PIT 共用同一个选择器，cutoff 取一个到不了的未来。

    🔴 共用是有意的：两条各自实现的话，崩溃之后「最新视图」可能开始看见
    一份 PIT 看不见的孤儿文件，而那正是 P3-R2 要消灭的分歧。
    """
    return _query_as_of(
        dataset_id,
        db_path=db_path,
        knowledge_cutoff="9999-12-31T23:59:59+08:00",
        start_date=start_date,
        end_date=end_date,
        instrument_ids=instrument_ids,
    )


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
    return _query_as_of(
        "cn.equity.daily_bars",
        db_path=db_path,
        knowledge_cutoff=knowledge_cutoff,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=instrument_ids,
    )


def query_index_eod_between(
    *,
    db_path=None,
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """P4-0 · `cn.index.eod_bars` 的「盘上最新」视图。

    与 `query_eod_between` 的关系：同一个选择器、不同 dataset。
    Phase 4 的 Outcome **不走这一条** —— 它要的是可回放，必须用带 cutoff 的那条。
    这条留给运维排查（「今天那份到底发布了没有」）。
    """
    return _query_latest_between(
        "cn.index.eod_bars",
        db_path=db_path,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=instrument_ids,
    )


def query_index_eod_as_of(
    *,
    db_path=None,
    knowledge_cutoff: str,
    start_date: str,
    end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """P4-0 · `cn.index.eod_bars` 的 point-in-time 查询 —— Outcome 的唯一入口。

    cutoff 之后才发布的修订看不见，所以同一个 OutcomeSet 今天重算与下个月重算
    得到同一份数字。排序口径与 `query_eod_as_of` 共用，注意事项见那一条。
    """
    return _query_as_of(
        "cn.index.eod_bars",
        db_path=db_path,
        knowledge_cutoff=knowledge_cutoff,
        start_date=start_date,
        end_date=end_date,
        instrument_ids=instrument_ids,
    )


def query_tradability_as_of(
    *, db_path=None, knowledge_cutoff: str, start_date: str, end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """P4-7 · 可交易性的 point-in-time 读取 —— cutoff 之后的修订看不见。

    ⚠️ 与上面那个 `query_tradability(trade_date)` 的区别：那条按分区取**当前**
    有效的一份（运维视角），这条按 cutoff 取**当时**可见的那份（复盘视角）。
    Phase 4 的稳健性端点只许走这一条。
    """
    return _query_as_of(
        "cn.security.tradability", db_path=db_path, knowledge_cutoff=knowledge_cutoff,
        start_date=start_date, end_date=end_date, instrument_ids=instrument_ids,
    )


def query_adjustment_factors_as_of(
    *, db_path=None, knowledge_cutoff: str, start_date: str, end_date: str,
    instrument_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """P4-7 · 复权因子的 point-in-time 读取 —— cutoff 之后的修订看不见。"""
    return _query_as_of(
        "cn.equity.adjustment_factors", db_path=db_path, knowledge_cutoff=knowledge_cutoff,
        start_date=start_date, end_date=end_date, instrument_ids=instrument_ids,
    )


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
