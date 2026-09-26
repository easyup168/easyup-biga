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
