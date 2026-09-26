"""派生数据集的 raw 血缘 —— 引用上游，不自造。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`upstream_artifact_ids` 这条发布路径的行为与它的 fail-closed 边界
- **不覆盖**：派生值本身算得对不对（那是各 dataset 的 `derive`/`quality`）
"""
from __future__ import annotations

import pytest

from easyup_biga.data.contracts import DatasetStatus
from easyup_biga.persistence import connect


def _bundle(tmp_path, monkeypatch):
    from tests.test_eod_dataset_live import (
        MASTER_ROWS, _bar_row, _run, _seed_universe,
    )

    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    _seed_universe(db)
    rows = [_bar_row(r["f12"], 10.0 + i) for i, r in enumerate(MASTER_ROWS)]
    return db, _run(db, tmp_path, rows)


def test_可交易性的raw血缘指向日线那一条(tmp_path, monkeypatch):
    """🔴 这条曾经是**自己证明自己**。

    旧实现把自己的输出重新序列化当 raw ⇒ `content_sha256` 是对输出算的。
    回放去校验它，校验的是「我写下的等于我写下的」，
    与真正的来源（行情源那次 EOD 响应）毫无关系。

    而日线走的是真响应 —— **同一次取数，两个数据集两套口径**。
    """
    pytest.importorskip("duckdb")
    db, result = _bundle(tmp_path, monkeypatch)
    assert result.tradability.status is DatasetStatus.COMPLETE

    with connect(db, readonly=True) as conn:
        lineage = {
            str(row["dataset_id"]): row["raw_artifact_id"]
            for row in conn.execute(
                "SELECT dataset_id,raw_artifact_id FROM dataset_partitions")
        }
    assert lineage["cn.security.tradability"] is not None
    assert lineage["cn.security.tradability"] == lineage["cn.equity.daily_bars"], (
        "可交易性的 raw 血缘没有指向日线那一条 —— 它又在自证了")


def test_可交易性不再造自己的raw工件(tmp_path, monkeypatch):
    """引用 ≠ 复制。复制会让那份 raw 的 provider_id 写成 `derived_biga`，
    而字节其实是行情源给的 —— 读的人会以为这个 provider 返回过这些东西。
    """
    pytest.importorskip("duckdb")
    db, _result = _bundle(tmp_path, monkeypatch)
    with connect(db, readonly=True) as conn:
        owners = [str(r["dataset_id"]) for r in
                  conn.execute("SELECT dataset_id FROM raw_artifacts")]
    assert "cn.security.tradability" not in owners, owners
    assert not list(tmp_path.glob("data/raw/cn_security_tradability/**/*")), (
        "盘上又多出一份 tradability 自己的 raw 文件")


def test_指向不存在的上游要响亮失败(tmp_path):
    """⚠️ 指向空气的血缘比没有血缘更糟 —— 它会让人以为查得到。"""
    from easyup_biga.data.snapshots import DatasetSnapshotService, SnapshotPublishRequest
    from easyup_biga.persistence import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    request = SnapshotPublishRequest(
        dataset_id="cn.security.tradability", job_id="j",
        partition_key={"trade_date": "20260925"}, trigger_id="t",
        provider_id="derived_biga", raw_artifacts=(),
        upstream_artifact_ids=("raw-does-not-exist",),
        storage_format="parquet", storage_uri=str(tmp_path / "x.parquet"),
        content_sha256="a" * 64, row_count=1, as_of="20260925",
        knowledge_cutoff="2026-09-25T00:00:00+08:00", quality_metrics={"row_count": 1},
    )
    with pytest.raises(ValueError, match="指向空气的血缘"):
        DatasetSnapshotService(path=db).publish(request)


def test_两种血缘必须二选一(tmp_path):
    """同时给 = 既声称引用上游、又要落一份自己的 raw。

    分区上只有**一条** `raw_artifact_id` 外键 ⇒ 必然有一个是摆设，
    而「哪个是摆设」由实现细节决定，不由调用方决定。
    """
    from easyup_biga.data.contracts import RawArtifact
    from easyup_biga.data.snapshots import DatasetSnapshotService, SnapshotPublishRequest
    from easyup_biga.persistence import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    artifact = RawArtifact(
        artifact_id="raw-x", dataset_id="cn.security.tradability",
        provider_id="derived_biga", request_fingerprint="f",
        body_uri="data/raw/x", body_sha256="b" * 64, size_bytes=1,
        retrieved_at="2026-09-25T00:00:00+08:00", content_type="application/json",
        compression="gzip", as_of="20260925", available_at="2026-09-25T00:00:00+08:00",
    )
    common = dict(
        dataset_id="cn.security.tradability", job_id="j",
        partition_key={"trade_date": "20260925"}, trigger_id="t",
        provider_id="derived_biga", storage_format="parquet",
        storage_uri=str(tmp_path / "x.parquet"), content_sha256="a" * 64,
        row_count=1, as_of="20260925",
        knowledge_cutoff="2026-09-25T00:00:00+08:00", quality_metrics={"row_count": 1},
    )
    service = DatasetSnapshotService(path=db)
    with pytest.raises(ValueError, match="二选一"):
        service.publish(SnapshotPublishRequest(
            raw_artifacts=(artifact,), upstream_artifact_ids=("raw-x",), **common))
    with pytest.raises(ValueError, match="二选一"):
        service.publish(SnapshotPublishRequest(
            raw_artifacts=(), upstream_artifact_ids=(), **common))


def test_派生发布不许再传raw_text(tmp_path):
    from easyup_biga.data.publication import DatasetRowPublisher
    from easyup_biga.persistence import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    with pytest.raises(ValueError, match="不该再传 raw_text"):
        DatasetRowPublisher(db_path=db, data_root=str(tmp_path / "data")).publish(
            dataset_id="cn.security.tradability", job_id="j",
            provider_id="derived_biga", partition_key={"trade_date": "20260925"},
            rows=[{"a": 1}], as_of="20260925",
            raw_text="{}", upstream_artifact_ids=("raw-x",))


def test_可交易性自己不许再声明上游为空(tmp_path):
    """判据打在 dataset 模块上：调用方忘了传，要当场红，不要悄悄退回自造。"""
    from easyup_biga.data.datasets import tradability

    with pytest.raises(ValueError, match="必须声明它派生自哪份原始响应"):
        tradability.publish((), "20260925", upstream_artifact_ids=())


def test_收盘情绪也不许自证(tmp_path):
    """同一个毛病的第二处 —— 修可交易性时发现的。

    生产路径上喂给它的那个 dict 是 `decision_client` **自己从股池算出来的
    计数**（`up.total` / `broken.total / denom` / `max(streaks)`）⇒
    raw 存的是我们的中间结果，不是任何人发给我们的字节。
    """
    from easyup_biga.data.datasets import emotion_close

    class _C:
        def collect(self, trade_date):
            return {"limit_up_count": 1}

    with pytest.raises(ValueError, match="必须声明它派生自哪份原始响应"):
        emotion_close.run(_C(), "20260925", upstream_artifact_ids=())


def test_收盘情绪的血缘指向股池那一条(tmp_path, monkeypatch):
    """判据打在**冻结之后库里有什么**，不在「有没有传参数」。"""
    pytest.importorskip("pyarrow")
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set
    from easyup_biga.providers.eastmoney import PoolResult

    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-emo", decision_id=None,
                      manifest={"kind": "t"}, path=db)

    def _pool(name, trade_date, **kw):
        return PoolResult(pool=name, requested_date=trade_date, qdate=trade_date,
                          total=3, rows=[{"lbc": 2}], raw={"ok": 1}, raw_text='{"ok":1}')

    monkeypatch.setattr(dc, "_fetch_pool", _pool)
    client = dc.DecisionDataClient(db_path=db, data_root="data")
    assert client.freeze_required(
        "es-emo", ["cn.market.limit_pool"], trade_date="20260925").complete

    with connect(db, readonly=True) as conn:
        lineage = {str(r["dataset_id"]): r["raw_artifact_id"] for r in
                   conn.execute("SELECT dataset_id,raw_artifact_id FROM dataset_partitions")}
        owners = [str(r["dataset_id"]) for r in
                  conn.execute("SELECT dataset_id FROM raw_artifacts")]
    assert lineage["cn.market.emotion_close"] == lineage["cn.market.limit_pool"]
    assert "cn.market.emotion_close" not in owners, owners
