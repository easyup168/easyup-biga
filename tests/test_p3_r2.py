"""P3-R2 runtime reconciliation guards."""
from __future__ import annotations

import pytest

from easyup_biga.data.acceptance import record_drill_result
from easyup_biga.data.client import refresh_trading_calendar
from easyup_biga.data.contracts import (
    DatasetStatus,
    RawArtifact,
    new_raw_artifact_id,
)
from easyup_biga.data.datasets import adjustment_factors, emotion_close, tradability
from easyup_biga.data.drills import provider_fallback_drill
from easyup_biga.data.file_store import FileStoreError
from easyup_biga.data.finalizer import REQUIRED_DATASETS, _code_checks
from easyup_biga.data.producers import PRODUCER_REGISTRY, resolve_producer, validate_producer_binding
from easyup_biga.data.publication import DatasetRowPublisher
from easyup_biga.data.registry import DATASET_REGISTRY
from easyup_biga.data.snapshots import DatasetSnapshotService, SnapshotPublishRequest
from easyup_biga.domain import now_cn
from easyup_biga.persistence import init_schema
from easyup_biga.providers.http import SourceError

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_provider_ids_are_canonical_and_match_registry():
    assert tradability.PROVIDER_ID == DATASET_REGISTRY[tradability.DATASET_ID].primary_provider
    assert emotion_close.PROVIDER_ID == DATASET_REGISTRY[emotion_close.DATASET_ID].primary_provider
    assert adjustment_factors.CsvAdjustmentFactorProvider.provider_id == (
        DATASET_REGISTRY[adjustment_factors.DATASET_ID].primary_provider
    )


def test_every_phase3_required_dataset_has_resolvable_producer():
    assert set(REQUIRED_DATASETS) <= set(PRODUCER_REGISTRY)
    for dataset_id in REQUIRED_DATASETS:
        assert callable(resolve_producer(dataset_id))
        validate_producer_binding(dataset_id)


def test_phase3_code_gate_includes_runtime_producer_guard():
    checks, errors = _code_checks(REPO)
    assert not errors, errors
    assert any("Producer Runtime Gate" in item for item in checks)


def test_calendar_real_fallback_attempts_are_persisted_for_drill(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)

    result = refresh_trading_calendar(
        path=db,
        fetchers={
            "sina_calendar": lambda: (_ for _ in ()).throw(SourceError("primary down")),
            "szse": lambda: (31, "20260901", "20260930"),
        },
    )
    assert result.provider_id == "szse"
    assert result.data_run_id
    drill = provider_fallback_drill(result.data_run_id, path=db)
    assert drill.passed, drill.detail

    ledger = tmp_path / "acceptance.jsonl"
    event = record_drill_result(ledger, drill)
    assert event.status == "PASS"
    assert event.detail["verified_by"] == "easyup_biga.data.drills"


def test_complete_snapshot_with_missing_file_is_never_silently_reused(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    now = now_cn().isoformat()
    artifact = RawArtifact(
        artifact_id=new_raw_artifact_id(),
        dataset_id="cn.equity.daily_bars",
        provider_id="eastmoney_eod",
        request_fingerprint="f" * 64,
        body_uri=str(tmp_path / "raw.json.gz"),
        body_sha256="a" * 64,
        size_bytes=1,
        retrieved_at=now,
        content_type="application/json",
        compression="gzip",
        as_of="20260924",
        available_at=now,
    )
    DatasetSnapshotService(path=db).publish(
        SnapshotPublishRequest(
            dataset_id="cn.equity.daily_bars",
            job_id="fixture",
            partition_key={"trade_date": "20260924"},
            trigger_id="fixture",
            provider_id="eastmoney_eod",
            raw_artifacts=(artifact,),
            storage_format="parquet",
            storage_uri=str(tmp_path / "missing.parquet"),
            content_sha256="b" * 64,
            row_count=1,
            as_of="20260924",
            knowledge_cutoff=now,
            quality_metrics={"row_count": 1},
            quality_status=DatasetStatus.COMPLETE,
        )
    )

    with pytest.raises(FileStoreError, match="unreadable"):
        DatasetRowPublisher(db_path=db, data_root=tmp_path / "data").publish(
            dataset_id="cn.equity.daily_bars",
            job_id="retry",
            provider_id="eastmoney_eod",
            partition_key={"trade_date": "20260924"},
            raw_text="{}",
            rows=[{"instrument_id": "600000.SH", "trade_date": "20260924", "close": 10.0}],
            as_of="20260924",
        )


def test_legacy_run_eod_cli_也必须经过bundle真实性门禁():
    """旧命令名可以保留，但不能成为绕过交易日/effective-date 校验的后门。"""
    import inspect
    from easyup_biga.data import cli as cli_mod

    source = inspect.getsource(cli_mod)
    block = source[source.index('if cmd == "run-eod":'):source.index('if cmd == "run-eod-bundle":')]
    assert "run_eod_bundle" in block
    assert "datasets.eod_daily_bars" not in block


# ── 收养孤儿分区：内容必须一致（本仓库 2026-09-26 补）────────────────────
def test_收养孤儿分区时内容不一致要响亮失败(tmp_path, monkeypatch):
    """🔴 「上次跑到一半」与「同一个版本算出了两份数据」是两件事。

    崩溃重试收养上一次留下的分区，前提是**内容一致** ——
    哈希对不上说明这次算出来的东西和上次不是同一份，那是真冲突。
    不校验就收养，等于把两份不同的数据当成同一份，
    而 `content_sha256` 从此指着一份谁也没见过的东西。
    """
    import easyup_biga.data.snapshots as snap_mod
    from easyup_biga.persistence import DataStoreConflict, init_schema

    from tests.test_eod_dataset_live import (  # noqa: E402
        MASTER_ROWS, TRADE_DATE, _bar_row, _run, _seed_universe,
    )

    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]

    # 第一次：账本最后一步崩了 ⇒ 留下分区行 + 物理文件，没有快照
    real = snap_mod.save_dataset_snapshot
    monkeypatch.setattr(snap_mod, "save_dataset_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    with pytest.raises(RuntimeError, match="ledger down"):
        _run(db, tmp_path, rows)
    monkeypatch.setattr(snap_mod, "save_dataset_snapshot", real)

    # 重试时喂**不同的价格** ⇒ 同一个 v1，内容却变了
    other = [_bar_row(r["f12"], 77.0 + i) for i, r in enumerate(MASTER_ROWS)]
    with pytest.raises(DataStoreConflict, match="内容不同"):
        _run(db, tmp_path, other)


def test_内容一致时才收养(tmp_path, monkeypatch):
    """对照组：同样的输入重试，必须成功收养而不是撞唯一约束。"""
    import easyup_biga.data.snapshots as snap_mod
    from easyup_biga.data.contracts import DatasetStatus

    from tests.test_eod_dataset_live import (  # noqa: E402
        MASTER_ROWS, _bar_row, _run, _seed_universe,
    )

    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]
    real = snap_mod.save_dataset_snapshot
    monkeypatch.setattr(snap_mod, "save_dataset_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    with pytest.raises(RuntimeError):
        _run(db, tmp_path, rows)
    monkeypatch.setattr(snap_mod, "save_dataset_snapshot", real)

    assert _run(db, tmp_path, rows).daily_bars.status is DatasetStatus.COMPLETE
