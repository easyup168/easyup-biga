from __future__ import annotations

import json

import pytest

from easyup_biga.data import DatasetStatus
from easyup_biga.data.datasets.security_master import (
    Exchange,
    SecurityBoard,
    SecurityMasterQualityPolicy,
    SecurityMasterService,
    normalize_security_master_rows,
)
from easyup_biga.persistence import (
    SCHEMA_VERSION,
    connect,
    data_run_state,
    find_security_master_snapshot_at,
    init_schema,
    load_security_master_records,
    security_at,
    security_universe_at,
)
from easyup_biga.providers import SourceError, parse_security_master_pages

RETRIEVED = "2026-09-25T16:00:00+08:00"


ROWS = [
    {"f12": "600000", "f14": "浦发银行", "f13": 1, "f26": 19991110},
    {"f12": "688001", "f14": "华兴源创", "f13": 1, "f26": 20190722},
    {"f12": "000001", "f14": "平安银行", "f13": 0, "f26": 19910403},
    {"f12": "300001", "f14": "特锐德", "f13": 0, "f26": 20091030},
    {"f12": "430047", "f14": "诺思兰德", "f13": 0, "f26": 20201124},
]


def _payload(rows, *, total=5):
    return {"rc": 0, "data": {"total": total, "diff": rows}}


def _fetch_result(rows=ROWS, *, total=None, retrieved_at=RETRIEVED):
    midpoint = max(1, len(rows) // 2)
    pages = [
        _payload(rows[:midpoint], total=total or len(rows)),
        _payload(rows[midpoint:], total=total or len(rows)),
    ]
    texts = [json.dumps(page, ensure_ascii=False) for page in pages]
    return parse_security_master_pages(
        pages,
        texts,
        retrieved_at=retrieved_at,
    )


def test_parse_and_normalize_covers_sse_szse_bse():
    fetched = _fetch_result()
    records = normalize_security_master_rows(
        fetched.rows,
        provider_id=fetched.provider_id,
        raw_artifact_id="raw-test",
        retrieved_at=fetched.retrieved_at,
    )
    by_id = {item.instrument_id: item for item in records}

    assert by_id["600000.SH"].exchange == Exchange.SSE
    assert by_id["600000.SH"].board == SecurityBoard.SSE_MAIN
    assert by_id["688001.SH"].board == SecurityBoard.STAR
    assert by_id["000001.SZ"].exchange == Exchange.SZSE
    assert by_id["300001.SZ"].board == SecurityBoard.CHINEXT
    assert by_id["430047.BJ"].exchange == Exchange.BSE
    assert by_id["430047.BJ"].board == SecurityBoard.BSE


def test_parser_rejects_incomplete_pagination():
    page = _payload(ROWS[:2], total=5)
    with pytest.raises(SourceError, match="declared total=5"):
        parse_security_master_pages(
            [page],
            [json.dumps(page)],
            retrieved_at=RETRIEVED,
        )


def test_security_master_sync_publishes_point_in_time_universe(tmp_path):
    db = tmp_path / "biga.db"
    assert init_schema(db) == SCHEMA_VERSION == 27
    service = SecurityMasterService(
        path=db,
        fetcher=_fetch_result,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    )

    result = service.sync(as_of_date="20260925", trigger_id="test-security-master")

    assert result.status == DatasetStatus.COMPLETE
    assert result.snapshot_id
    assert result.row_count == 5
    assert data_run_state(result.data_run_id, path=db) == "COMPLETED"

    records = load_security_master_records(result.snapshot_id, path=db)
    assert [item["instrument_id"] for item in records] == sorted(
        ["600000.SH", "688001.SH", "000001.SZ", "300001.SZ", "430047.BJ"]
    )
    assert len(security_universe_at(RETRIEVED, path=db)) == 5
    assert len(security_universe_at(RETRIEVED, exchange="BSE", path=db)) == 1
    assert security_at("600000.SH", RETRIEVED, path=db)["name"] == "浦发银行"
    assert security_universe_at("2026-09-25T15:59:59+08:00", path=db) == []

    snapshot = find_security_master_snapshot_at(RETRIEVED, path=db)
    assert snapshot["snapshot_id"] == result.snapshot_id
    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_artifacts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fact_security_master").fetchone()[0] == 5


def test_security_master_sync_is_idempotent_per_date(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    calls = 0

    def fetcher():
        nonlocal calls
        calls += 1
        return _fetch_result()

    service = SecurityMasterService(
        path=db,
        fetcher=fetcher,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    )
    first = service.sync(as_of_date="20260925")
    second = service.sync(as_of_date="20260925")

    assert calls == 1
    assert second.reused is True
    assert second.snapshot_id == first.snapshot_id
    assert second.row_count == 5


def test_missing_exchange_is_quarantined(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    without_bse = ROWS[:-1]
    service = SecurityMasterService(
        path=db,
        fetcher=lambda: _fetch_result(without_bse, total=len(without_bse)),
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=4),
    )

    result = service.sync(as_of_date="20260925")

    assert result.status == DatasetStatus.QUARANTINED
    assert result.snapshot_id is None
    assert data_run_state(result.data_run_id, path=db) == "QUARANTINED"
    assert find_security_master_snapshot_at(RETRIEVED, path=db) is None

    # 🔴 隔离 ⇒ `materialize` 没跑 ⇒ `fact_security_master` 里一行都没有。
    #    此时**也不许有分区行**：它的 storage_uri 会声称
    #    `biga+sqlite://fact_security_master/<pid>` 底下有 N 行，而那里空空如也。
    #    一个指向不存在数据的分区，比没有分区更糟 —— 完整性审计会去重算它。
    #    ⚠️ 这与 Parquet 那条路**相反**：那边物理文件在进来之前就写好了，
    #    质量不过也要登记分区，因为那份数据真的存在，留痕才查得到。
    with connect(db, readonly=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_partitions "
            "WHERE dataset_id='cn.security_master'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_security_master").fetchone()[0] == 0
        # 但质量报告在 —— 「为什么被隔离」查得到
        report = conn.execute(
            "SELECT status,partition_id FROM quality_reports").fetchone()
        assert report["status"] == "QUARANTINED"
        assert report["partition_id"] is None


def test_security_master_rows_are_append_only(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    result = SecurityMasterService(
        path=db,
        fetcher=_fetch_result,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    ).sync(as_of_date="20260925")

    with pytest.raises(Exception, match="只追加"):
        with connect(db) as conn:
            conn.execute(
                "UPDATE fact_security_master SET name='changed' WHERE partition_id=?",
                (result.partition_id,),
            )


def test_security_master_revision_links_to_previous_version(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    first = SecurityMasterService(
        path=db,
        fetcher=lambda: _fetch_result(retrieved_at="2026-09-25T16:00:00+08:00"),
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    ).sync(as_of_date="20260925")
    second = SecurityMasterService(
        path=db,
        fetcher=lambda: _fetch_result(retrieved_at="2026-09-25T17:00:00+08:00"),
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5),
    ).sync(as_of_date="20260925", data_version=2)

    assert second.snapshot_id != first.snapshot_id
    with connect(db, readonly=True) as conn:
        snapshot = conn.execute(
            "SELECT data_version,supersedes_snapshot_id FROM dataset_snapshots "
            "WHERE snapshot_id=?",
            (second.snapshot_id,),
        ).fetchone()
        partition = conn.execute(
            "SELECT data_version,supersedes_partition_id FROM dataset_partitions "
            "WHERE partition_id=?",
            (second.partition_id,),
        ).fetchone()
    assert tuple(snapshot) == (2, first.snapshot_id)
    assert tuple(partition) == (2, first.partition_id)


def test_live_provider_cannot_backdate_current_universe():
    service = SecurityMasterService()
    with pytest.raises(ValueError, match="current universe"):
        service.sync(as_of_date="20000101")


# ── 账本收口之后新增的三条（2026-09-26）────────────────────────────────────
def test_materialize失败时不出快照(tmp_path, monkeypatch):
    """🔴 物理行没写进去，就不许有快照。

    `fact_security_master` 的行是由 `materialize` 回调在发布过程中写的。
    它抛了而快照照落的话，下游会读到一个指向**空表**的快照 ——
    而下游只认快照，于是「这次同步失败了」变成了「这次同步的结果是 0 只票」。
    """
    import easyup_biga.data.datasets.security_master as sm

    db = tmp_path / "biga.db"
    init_schema(db)
    monkeypatch.setattr(sm, "save_security_master_records",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk on fire")))
    service = SecurityMasterService(
        path=db, fetcher=_fetch_result,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5))
    with pytest.raises(RuntimeError, match="disk on fire"):
        service.sync(as_of_date="20260925")

    with connect(db, readonly=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_snapshots "
            "WHERE dataset_id='cn.security_master'").fetchone()[0] == 0
        # 但**留了痕**：分区行与失败的 run 都在，查得到为什么没发出来
        assert conn.execute(
            "SELECT COUNT(*) FROM dataset_partitions "
            "WHERE dataset_id='cn.security_master'").fetchone()[0] == 1
        assert conn.execute(
            "SELECT to_state FROM data_run_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0] == "FAILED"


def test_修订跳号被拒(tmp_path):
    """修订链不许跳号 —— 空洞事后查不出是「丢了一版」还是「本来就没有」。"""
    from easyup_biga.persistence import DataStoreConflict

    db = tmp_path / "biga.db"
    init_schema(db)
    service = SecurityMasterService(
        path=db, fetcher=_fetch_result,
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5))
    service.sync(as_of_date="20260925")
    with pytest.raises(DataStoreConflict, match="必须是 v2"):
        service.sync(as_of_date="20260925", data_version=5)


def test_取数失败也留下一次可查的run(tmp_path):
    """🔴 「源挂了」和「今天没跑」在库里不能长得一模一样。"""
    from easyup_biga.providers.http import SourceError

    db = tmp_path / "biga.db"
    init_schema(db)
    service = SecurityMasterService(
        path=db,
        fetcher=lambda: (_ for _ in ()).throw(SourceError("all hosts failed")),
        quality_policy=SecurityMasterQualityPolicy(minimum_rows=5))
    with pytest.raises(SourceError):
        service.sync(as_of_date="20260925")

    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_job_runs").fetchone()[0] == 1
        assert conn.execute(
            "SELECT to_state FROM data_run_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0] == "FAILED"
        attempt = conn.execute(
            "SELECT status,error_code FROM provider_attempts").fetchone()
        assert attempt["status"] == "FAILED_RETRYABLE"
        assert attempt["error_code"] == "data.provider.unavailable"
