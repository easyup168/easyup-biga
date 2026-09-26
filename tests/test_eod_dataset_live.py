"""P3-4 · EOD 数据集**真的跑得到底** —— 从一次 provider 响应到 DuckDB 读回。

覆盖 / 不覆盖
-------------
- 覆盖：`run_eod_bundle` 一路走通 Security Master → 归一化 → 质量 → Parquet →
  DatasetSnapshot → `query_eod_as_of` 读回；以及修订走新 `data_version`
- **不覆盖**：真实出网取数（那是 `network` 标记的事，本仓库默认禁网）；
  可交易性的发布（它今天不发布，见 `eod_pipeline` 模块头）

🔴 这个文件为什么必须存在
-------------------------
上一轮把 P3-4/P3-5 的四个 dataset 模块合进来，四个模块的测试**全绿** ——
因为那些测试测的是 `normalize` / `derive` / `write_parquet_rows` 这类
**不经过注册表**的下层函数。而 `cn.equity.daily_bars` 当时根本没进注册表，
`run()` 一调就在 `get_dataset()` 抛。

> **「有测试」和「有能跑到底的路径」是两件事。**

⇒ 这里的判据一律打在**跑完之后库里/盘上有什么**，不打在函数存在性上。
"""
from __future__ import annotations

import json

import pytest

from easyup_biga.data.analytics import query_eod_as_of, query_eod_between
from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.data.datasets.security_master import (
    SecurityMasterQualityPolicy,
    SecurityMasterService,
)
from easyup_biga.data.eod_pipeline import run_eod_bundle
from easyup_biga.persistence import (
    connect,
    data_run_state,
    init_schema,
    load_dataset_snapshot,
)
from easyup_biga.providers.eastmoney_eod import EodFetchResult
from easyup_biga.providers.eastmoney_security_master import parse_security_master_pages

pytest.importorskip("duckdb", reason="Parquet 面读写走 duckdb")

RETRIEVED = "2026-09-25T16:00:00+08:00"
TRADE_DATE = "20260925"

#: 与 Security Master 同一批票 —— 两者对不上的话 coverage_ratio 就没有意义。
MASTER_ROWS = [
    {"f12": "600000", "f14": "浦发银行", "f13": 1, "f26": 19991110},
    {"f12": "688001", "f14": "华兴源创", "f13": 1, "f26": 20190722},
    {"f12": "000001", "f14": "平安银行", "f13": 0, "f26": 19910403},
    {"f12": "300001", "f14": "特锐德", "f13": 0, "f26": 20091030},
    {"f12": "430047", "f14": "诺思兰德", "f13": 0, "f26": 20201124},
]


def _bar_row(code: str, close: float, *, suspended: bool = False):
    """东财 clist 的一行。停牌票没有价格字段 —— 用 '-' 表示，和真实响应一致。"""
    if suspended:
        return {"f12": code, "f14": "x", "f17": "-", "f15": "-", "f16": "-", "f2": "-",
                "f5": "-", "f6": "-", "f3": "-", "f4": "-", "f18": "-"}
    return {
        "f12": code, "f14": "x",
        "f17": close - 0.2, "f15": close + 0.3, "f16": close - 0.4, "f2": close,
        "f18": close - 0.1, "f5": 1_000_000, "f6": 12_345_678.0,
        "f4": 0.1, "f3": 0.9,
    }


def _seed_universe(db):
    init_schema(db)

    def _fetch():
        pages = [{"rc": 0, "data": {"total": 5, "diff": MASTER_ROWS[:2]}},
                 {"rc": 0, "data": {"total": 5, "diff": MASTER_ROWS[2:]}}]
        return parse_security_master_pages(
            pages, [json.dumps(p, ensure_ascii=False) for p in pages],
            retrieved_at=RETRIEVED)

    SecurityMasterService(
        path=db, fetcher=_fetch,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    ).sync(as_of_date=TRADE_DATE, trigger_id="test-eod")


def _fetched(rows, *, declared=None):
    return EodFetchResult(
        rows=tuple(rows),
        raw_text=json.dumps({"rc": 0, "data": {"diff": list(rows)}}, ensure_ascii=False),
        retrieved_at=RETRIEVED,
        declared_total=len(rows) if declared is None else declared,
    )


def _run(db, tmp_path, rows, **kw):
    return run_eod_bundle(
        TRADE_DATE, db_path=db, data_root=str(tmp_path / "data"),
        fetcher=lambda: _fetched(rows), **kw)


# ── 主路径 ──────────────────────────────────────────────────────────────────
def test_一次取数跑到底_快照与Parquet都真的落了(tmp_path):
    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]

    result = _run(db, tmp_path, rows)

    assert result.status == DatasetStatus.COMPLETE
    assert result.normalized_bar_count == 5
    assert result.universe_count == 5
    assert data_run_state(result.daily_bars.data_run_id, path=db) == "COMPLETED"

    snap = load_dataset_snapshot(result.daily_bars.snapshot_id, path=db)
    assert snap is not None
    assert snap["dataset_id"] == "cn.equity.daily_bars"
    assert snap["partition_key"] == {"trade_date": TRADE_DATE}
    assert snap["data_version"] == 1

    # 账本四张表各留了痕 —— 不是只写了个快照行
    with connect(db, readonly=True) as conn:
        n = lambda t, w: conn.execute(  # noqa: E731
            f"SELECT COUNT(*) FROM {t} WHERE dataset_id='cn.equity.daily_bars'"
            + (" AND 1" if not w else "")).fetchone()[0]
        assert n("raw_artifacts", None) == 1
        assert n("dataset_partitions", None) == 1
        assert n("dataset_snapshots", None) == 1


def test_落下去的Parquet能被查回来且内容对得上(tmp_path):
    """🔴 判据打在**读回来的数值**上。写成功不等于读得出来。"""
    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]
    _run(db, tmp_path, rows)

    got = query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE)
    assert len(got) == 5
    closes = {r["instrument_id"]: r["close"] for r in got}
    assert closes["600000.SH"] == pytest.approx(10.0)
    assert closes["430047.BJ"] == pytest.approx(14.0)


def test_as_of查询带回血缘标注(tmp_path):
    db = tmp_path / "biga.db"
    _seed_universe(db)
    _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])

    snap_id = load_dataset_snapshot(
        _latest_snapshot_id(db), path=db)["snapshot_id"]
    got = query_eod_as_of(
        db_path=db, knowledge_cutoff="2099-01-01T00:00:00+08:00",
        start_date=TRADE_DATE, end_date=TRADE_DATE)
    assert len(got) == 5
    assert {r["_snapshot_id"] for r in got} == {snap_id}


def test_cutoff之前的查询看不到这次发布(tmp_path):
    """PIT 的全部意义：当时还没发布的东西，回头查不该看见。"""
    db = tmp_path / "biga.db"
    _seed_universe(db)
    _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])

    assert query_eod_as_of(
        db_path=db, knowledge_cutoff="2020-01-01T00:00:00+08:00",
        start_date=TRADE_DATE, end_date=TRADE_DATE) == []


def _latest_snapshot_id(db):
    with connect(db, readonly=True) as conn:
        return conn.execute(
            "SELECT snapshot_id FROM dataset_snapshots WHERE dataset_id='cn.equity.daily_bars'"
            " ORDER BY data_version DESC LIMIT 1").fetchone()[0]


# ── 幂等与修订 ──────────────────────────────────────────────────────────────
def test_同日重跑不重复发布(tmp_path):
    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]
    first = _run(db, tmp_path, rows)
    second = _run(db, tmp_path, rows)

    assert second.daily_bars.reused is True
    assert second.daily_bars.snapshot_id == first.daily_bars.snapshot_id
    with connect(db, readonly=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_snapshots "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()[0] == 1


def test_修订走新版本且不改写旧分区(tmp_path):
    """🔴 修订必须是**新增**，老那份的 Parquet 一个字节都不能动。"""
    db = tmp_path / "biga.db"
    _seed_universe(db)
    _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])

    with connect(db, readonly=True) as conn:
        v1_uri = conn.execute(
            "SELECT storage_uri FROM dataset_partitions "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()[0]
    v1_bytes = __import__("pathlib").Path(v1_uri).read_bytes()

    corrected = [_bar_row(r["f12"], 99.0 + i) for i, r in enumerate(MASTER_ROWS)]
    second = _run(db, tmp_path, corrected, new_revision=True)

    snap = load_dataset_snapshot(second.daily_bars.snapshot_id, path=db)
    assert snap["data_version"] == 2
    assert snap["supersedes_snapshot_id"] is not None
    assert __import__("pathlib").Path(v1_uri).read_bytes() == v1_bytes, "旧修订被改写了"

    # 跨日查询取最新修订
    got = query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE)
    assert {r["close"] for r in got} == {99.0, 100.0, 101.0, 102.0, 103.0}


def test_修订后两条查询必须给同一个答案(tmp_path):
    """🔴 这条钉的是 2026-09-26 修掉的那个 bug，形状是裁定 15 的「悄悄给出两个数」。

    发布服务原本的幂等判据忽略 `data_version`：分区只要已有 COMPLETE，
    请求 v2 也原样退回 v1。而调用方是**先写 Parquet、再进账本**的 ⇒
    v2 的文件落了盘、控制面只认 v1。两条查询走不同的路：

    · `query_eod_between` 扫盘取最高 data_version → v2
    · `query_eod_as_of`   走控制面               → v1

    实测同一个交易日拿到两套价格，**两边都不报错**。
    ⇒ 判据必须是「两条路的答案相等」，不是「各自能跑通」。
    """
    db = tmp_path / "biga.db"
    _seed_universe(db)
    _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])
    _run(db, tmp_path, [_bar_row(r["f12"], 99.0 + i) for i, r in enumerate(MASTER_ROWS)],
         new_revision=True)

    scanned = {r["instrument_id"]: r["close"] for r in query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE)}
    controlled = {r["instrument_id"]: r["close"] for r in query_eod_as_of(
        db_path=db, knowledge_cutoff="2099-01-01T00:00:00+08:00",
        start_date=TRADE_DATE, end_date=TRADE_DATE)}
    assert scanned == controlled, "扫盘与控制面给出了两套数"
    assert set(scanned.values()) == {99.0, 100.0, 101.0, 102.0, 103.0}

    # 盘上确实有两个版本（旧的没被删），只是两条路都选了 v2
    versions = sorted(
        p.parent.name for p in (tmp_path / "data" / "lake").rglob("*.parquet"))
    assert versions == ["data_version=000001", "data_version=000002"]


def test_凭空发v2被当场拒绝(tmp_path):
    """修订必须接在当前有效快照后面 —— 不许从中间开始一条链。"""
    from easyup_biga.data.contracts import RawArtifact, new_raw_artifact_id
    from easyup_biga.data.snapshots import DatasetSnapshotService, SnapshotPublishRequest
    from easyup_biga.persistence import DataStoreConflict

    db = tmp_path / "biga.db"
    _seed_universe(db)
    raw_id = new_raw_artifact_id()
    with pytest.raises(DataStoreConflict, match="没有任何 COMPLETE 的前一版"):
        DatasetSnapshotService(path=db).publish(SnapshotPublishRequest(
            dataset_id="cn.equity.daily_bars", job_id="eod-daily-bars",
            partition_key={"trade_date": TRADE_DATE}, trigger_id="t",
            provider_id="eastmoney_eod",
            raw_artifacts=(RawArtifact(
                artifact_id=raw_id, dataset_id="cn.equity.daily_bars",
                provider_id="eastmoney_eod", request_fingerprint="x",
                body_uri="file:///dev/null", body_sha256="0" * 64, size_bytes=1,
                retrieved_at=RETRIEVED, content_type="application/json",
                compression="gzip", as_of=TRADE_DATE, available_at=RETRIEVED),),
            storage_format="parquet", storage_uri="/tmp/x.parquet",
            content_sha256="0" * 64, row_count=1, as_of=TRADE_DATE,
            knowledge_cutoff=RETRIEVED, quality_metrics={"row_count": 1},
            data_version=2,
        ))


# ── 质量与诚实性 ────────────────────────────────────────────────────────────
def test_停牌票不产生假的OHLC行(tmp_path):
    """停牌票没有价格 ⇒ 日线里**不该有它** —— 不是补 0，也不是抄昨收。"""
    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(MASTER_ROWS[0]["f12"], 10.0, suspended=True)]
    rows += [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS[1:], start=1)]

    result = _run(db, tmp_path, rows)

    assert result.normalized_bar_count == 4
    got = query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE)
    assert "600000.SH" not in {r["instrument_id"] for r in got}
    # 停牌数进了汇总，读日志的人看得见分母为什么小了
    assert result.tradability_counts.get("SUSPENDED") == 1


def test_没有Security_Master就拒绝跑(tmp_path):
    """🔴 空 universe 不许静默用 EOD 响应顶替 —— 那会让身份 SSOT 失效。"""
    db = tmp_path / "biga.db"
    init_schema(db)
    with pytest.raises(RuntimeError, match="Security Master"):
        _run(db, tmp_path, [_bar_row("600000", 10.0)])


# ── 两阶段提交（⓪-c）────────────────────────────────────────────────────────
def test_账本写不进去时_lake里什么都不会多出来(tmp_path, monkeypatch):
    """🔴 「先账本、后落 lake」的全部意义。

    先文件后账本的话，崩在中间会在 lake 里留一个**控制面不认、而扫盘查询
    读得到**的孤儿分区 —— 同一交易日两套数，都不报错（哑的）。
    反过来崩，留下的是一条指向不存在文件的账本行 —— 回放与完整性审计
    当场抛（响的）。这条钉住前者不会发生。
    """
    import easyup_biga.data.snapshots as snap_mod

    db = tmp_path / "biga.db"
    _seed_universe(db)
    real = snap_mod.save_dataset_snapshot
    monkeypatch.setattr(snap_mod, "save_dataset_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    with pytest.raises(RuntimeError, match="ledger down"):
        _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])

    lake = tmp_path / "data" / "lake"
    assert list(lake.rglob("*.parquet")) == [], "lake 里留下了孤儿分区"
    staging = tmp_path / "data" / "_staging"
    assert list(staging.glob("*")) == [], "暂存区没清干净"
    assert real is not None     # 引用留着，monkeypatch 退出时会自动还原


def test_质量不过时不落lake也不登记分区(tmp_path):
    """坏数据不进 lake —— 但原始响应留着，诊断材料不丢。"""
    db = tmp_path / "biga.db"
    _seed_universe(db)
    # declared_total 远大于实际取回 ⇒ 覆盖率判据不过
    result = run_eod_bundle(
        TRADE_DATE, db_path=db, data_root=str(tmp_path / "data"),
        fetcher=lambda: _fetched(
            [_bar_row(MASTER_ROWS[0]["f12"], 10.0)], declared=5000))

    assert result.status is not DatasetStatus.COMPLETE
    assert list((tmp_path / "data" / "lake").rglob("*.parquet")) == []
    assert list((tmp_path / "data" / "_staging").glob("*")) == []
    with connect(db, readonly=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_snapshots "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_partitions "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()[0] == 0
        # 🔴 原始响应在 —— 「为什么没过」查得到
        assert conn.execute(
            "SELECT COUNT(*) FROM raw_artifacts "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT status FROM quality_reports "
            "WHERE dataset_id='cn.equity.daily_bars'").fetchone()["status"] != "COMPLETE"


def test_暂存区不在lake里_扫盘查询绝对看不到(tmp_path):
    """判据打在**路径关系**上，不是文件名前缀 —— 后者是拿 glob 恰好不匹配当不变量。"""
    from easyup_biga.data.file_store import FileStore

    fs = FileStore(tmp_path / "data")
    staged = fs.stage_parquet_rows(
        "cn.equity.daily_bars", {"trade_date": TRADE_DATE},
        [{"instrument_id": "600000.SH", "trade_date": TRADE_DATE, "close": 10.0}],
        1, 1)
    lake = (tmp_path / "data" / "lake").resolve()
    assert not staged.staging.resolve().is_relative_to(lake)
    assert staged.target.resolve().is_relative_to(lake)
    assert not staged.target.exists(), "还没 commit 就已经在 lake 里了"
    assert query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE) == []

    staged.commit()
    assert staged.target.exists()
    assert len(query_eod_between(
        data_root=str(tmp_path / "data"), start_date=TRADE_DATE, end_date=TRADE_DATE)) == 1


def test_commit与abort都是幂等的(tmp_path):
    from easyup_biga.data.file_store import FileStore

    fs = FileStore(tmp_path / "data")
    staged = fs.stage_parquet_rows(
        "cn.equity.daily_bars", {"trade_date": TRADE_DATE},
        [{"instrument_id": "600000.SH", "trade_date": TRADE_DATE, "close": 10.0}], 1, 1)
    assert staged.commit() == staged.target
    assert staged.commit() == staged.target      # 再提交一次不炸
    staged.abort()                               # 提交之后 abort 不许删掉它
    assert staged.target.exists()


def test_落lake失败_留下的是响的失败而不是静默可见(tmp_path, monkeypatch):
    """🔴 两阶段的另一半：最后一步失败时，后果必须**吵**。

    文件没落进 lake，而快照已经落了 ⇒ 这是有意的：
    · 扫盘查询看不到它（lake 里没有）
    · 走控制面的查询会去读那个不存在的文件 —— **炸响**
    · 完整性审计重算哈希 —— **炸响**

    对照组是没有两阶段时的样子：文件在 lake、快照没落 ⇒ 扫盘读得到、
    控制面读不到，**两边都不报错**。那才是要消灭的东西。
    """
    from easyup_biga.data.file_store import StagedParquet
    from easyup_biga.data.integrity import audit_snapshot_integrity

    db = tmp_path / "biga.db"
    _seed_universe(db)
    monkeypatch.setattr(StagedParquet, "commit",
                        lambda self: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(RuntimeError, match="disk full"):
        _run(db, tmp_path, [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)])
    monkeypatch.undo()

    assert list((tmp_path / "data" / "lake").rglob("*.parquet")) == []
    snap_id = _latest_snapshot_id(db)

    # 完整性审计当场抓到
    report = audit_snapshot_integrity(snap_id, path=db, data_root=str(tmp_path / "data"))
    assert not report.ok
    assert any("unreadable" in e for e in report.errors), report.errors

    # run 的终态是 FAILED —— 不是悄悄 COMPLETED
    with connect(db, readonly=True) as conn:
        last = conn.execute(
            "SELECT to_state FROM data_run_events e JOIN data_job_runs r "
            "USING(data_run_id) WHERE r.dataset_id='cn.equity.daily_bars' "
            "ORDER BY e.seq DESC LIMIT 1").fetchone()[0]
    assert last == "FAILED"
