"""P4-G0 · Phase 3 Completion & Readiness Closure.

These tests are the hand-off contract between Phase 3 and Phase 4.  They deliberately
check architecture boundaries, not only that twelve names appear in a registry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from easyup_biga.data.decision_client import (
    DIRECT_DATASETS,
    DecisionDataClient,
    FrozenDataset,
)
from easyup_biga.data.finalizer import REQUIRED_DATASETS, SPECIALIST_PATHS, _code_checks
from easyup_biga.data.integrity import audit_specialist_provider_boundary
from easyup_biga.data.provider_registry import role_of
from easyup_biga.data.registry import DATASET_REGISTRY
from easyup_biga.domain import STAGE1_AGENTS, required_datasets_for, required_datasets_for_agents

REPO = Path(__file__).resolve().parents[1]


def test_phase3_required_datasets_are_exactly_registered():
    assert set(DATASET_REGISTRY) == set(REQUIRED_DATASETS)


def test_p36_direct_feeds_have_real_primary_bindings():
    expected = {
        "cn.index.realtime_quote": "tencent",
        "cn.market.breadth": "eastmoney",
        "cn.sector.board_snapshot": "eastmoney",
        "cn.market.limit_pool": "eastmoney",
        "cn.news.flash": "sina_news",
    }
    assert DIRECT_DATASETS == frozenset(expected)
    for dataset_id, provider_id in expected.items():
        assert DATASET_REGISTRY[dataset_id].primary_provider == provider_id
        assert role_of(dataset_id, provider_id).value == "PRIMARY"


def test_p37_agent_registry_is_the_required_dataset_ssot():
    expected = {
        "market": ("cn.index.daily_bars", "cn.index.realtime_quote", "cn.market.breadth"),
        "sector": ("cn.index.daily_bars", "cn.sector.board_snapshot"),
        "news": ("cn.news.flash",),
        "technical": ("cn.index.daily_bars",),
        "emotion": ("cn.market.limit_pool",),
    }
    assert {a: required_datasets_for(a) for a in STAGE1_AGENTS} == expected
    union = required_datasets_for_agents(STAGE1_AGENTS)
    assert union == (
        "cn.index.daily_bars",
        "cn.index.realtime_quote",
        "cn.market.breadth",
        "cn.sector.board_snapshot",
        "cn.news.flash",
        "cn.market.limit_pool",
    )
    assert set(union) <= set(DATASET_REGISTRY)


def test_p36_specialists_have_zero_provider_imports():
    report = audit_specialist_provider_boundary(REPO, SPECIALIST_PATHS)
    assert report.ok, report.errors


def test_phase3_code_gate_includes_p36_and_p37_guards():
    checks, errors = _code_checks(REPO)
    assert errors == []
    assert any("P3-6 Specialist Provider 边界已收口" in x for x in checks)
    assert any("P3-7 AgentRegistry" in x for x in checks)


def test_decision_client_reconstructs_frozen_quote_without_network(monkeypatch, tmp_path):
    client = DecisionDataClient(db_path=tmp_path / "unused.db", data_root=str(tmp_path / "data"))
    frozen = FrozenDataset(
        "cn.index.realtime_quote", "ds-test", "a" * 64,
        ({
            "code": "sh000001", "name": "上证指数", "last": 3900.0,
            "prev_close": 3888.0, "volume_hand": 123, "amount_wan": 456.0,
            "quoted_at": "20260925145959", "raw": "raw-line",
        },),
    )
    monkeypatch.setattr(client, "_frozen", lambda esid, dsid: frozen)
    quotes, raw_hash = client.read_index_quote("es-test")
    assert quotes["sh000001"].last == 3900.0
    assert quotes["sh000001"].trade_date == "20260925"
    assert raw_hash == "a" * 64


def test_decision_client_does_not_hide_programming_or_storage_errors(monkeypatch, tmp_path):
    """Only provider/data-quality errors may degrade; code/storage failures must explode."""
    import easyup_biga.data.decision_client as dc

    client = DecisionDataClient(db_path=tmp_path / "unused.db", data_root=str(tmp_path / "data"))
    monkeypatch.setattr(dc, "resolve_evidence_set_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(client, "_freeze_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        client.freeze_required("es-test", ["cn.news.flash"], trade_date="20260925")


# ── 真机跑出来的那个洞（2026-09-26）──────────────────────────────────────
def test_冻结失败也要在数据平台账本里留痕(tmp_path, monkeypatch):
    """🔴 这条是**第一次真机跑 P3-6** 才暴露出来的，离线测不出来。

    那次三个 eastmoney dataset 冻结失败：卡上的 `missing` 报对了、编排器的
    run 事件也记了 `dataset_errors`，而数据平台自己的账本
    （`data_job_runs` / `provider_attempts`）**一行都没有** ——
    `_freeze_one()` 在 `publish()` 之前就抛了，DataRun 根本没开过。

    后果不只是「日志少一行」：

    · 「源挂了」和「今天没跑」在数据平台里长得一模一样
    · 🔴 `drills.provider_fallback_drill()` 读的就是 `provider_attempts`
      ⇒ 失败的取数从不落表，「真实 Primary→Fallback 演练」对这 5 个 dataset
      **结构上取不到证据**（主源失败那一半永远不出现）

    ⚠️ 判据打在「失败之后库里有什么」，不在「有没有 except」。
    """
    import easyup_biga.data.decision_client as dc
    from easyup_biga.providers.http import SourceError
    from _store import init_schema
    from easyup_biga.persistence import connect

    db = tmp_path / "biga.db"
    init_schema(db)
    monkeypatch.setattr(dc, "_fetch_breadth",
                        lambda: (_ for _ in ()).throw(SourceError("全部备选主机失败")))
    client = dc.DecisionDataClient(db_path=db, data_root=str(tmp_path / "data"))

    result = client.freeze_required("es-test", ["cn.market.breadth"], trade_date="20260926")

    # 降级本身仍然是预期行为
    assert not result.complete
    assert "SourceError" in result.errors["cn.market.breadth"]

    with connect(db, readonly=True) as conn:
        run = conn.execute(
            "SELECT data_run_id FROM data_job_runs WHERE dataset_id='cn.market.breadth'"
        ).fetchone()
        assert run is not None, "冻结失败没有留下 DataRun —— 「源挂了」与「今天没跑」分不开"
        assert conn.execute(
            "SELECT to_state FROM data_run_events WHERE data_run_id=? ORDER BY seq DESC LIMIT 1",
            (run["data_run_id"],)).fetchone()[0] == "FAILED"
        attempt = conn.execute(
            "SELECT provider_id,status,error_code,error_detail FROM provider_attempts "
            "WHERE data_run_id=?", (run["data_run_id"],)).fetchone()
        assert attempt is not None, (
            "没有失败的 ProviderAttempt —— provider_fallback_drill 永远看不到主源失败那一半")
        assert attempt["provider_id"] == "eastmoney"
        assert attempt["status"] == "FAILED_RETRYABLE"
        assert "全部备选主机失败" in attempt["error_detail"]


def test_留痕失败不许盖住原始的取数失败(tmp_path, monkeypatch):
    """探针方向：账本写不进去时，抛出来的必须仍是「源挂了」。

    盖住的话，排查方向会从「去看 provider」跑偏到「去看数据库」。
    """
    import easyup_biga.data.decision_client as dc
    from easyup_biga.providers.http import SourceError
    from _store import init_schema

    db = tmp_path / "biga.db"
    init_schema(db)
    monkeypatch.setattr(dc, "_fetch_breadth",
                        lambda: (_ for _ in ()).throw(SourceError("源挂了")))
    client = dc.DecisionDataClient(db_path=db, data_root=str(tmp_path / "data"))
    monkeypatch.setattr(
        type(client.publisher._service), "record_fetch_failure",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("账本也挂了")))

    result = client.freeze_required("es-test", ["cn.market.breadth"], trade_date="20260926")
    assert "源挂了" in result.errors["cn.market.breadth"]
    assert "账本也挂了" not in result.errors["cn.market.breadth"]
