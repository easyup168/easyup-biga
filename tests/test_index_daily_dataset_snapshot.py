"""P3-2 —— 把已在生产的 index_daily 冻结桥接成通用 DatasetSnapshot。

取自外部 P3-2 实现包的测试，**其中一条的断言被反过来了**（见
`test_自报provider与raw不符时fail_closed` 的 docstring）。

覆盖 / 不覆盖
-------------
- 覆盖：冻结时同步发布 DatasetSnapshot、EvidenceSet v2 manifest、
  血缘与主行同事务、出处不一致时 fail-closed
- **不覆盖**：现有 specialist 的读取行为（那是 `test_snapshot_wiring.py` 的事 ——
  P3-2 的红线正是「它们一个字节都不变」）
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from easyup_biga.application import SnapshotCoordinator
from easyup_biga.data.datasets import IndexDailyDatasetBridge
from easyup_biga.domain import new_evidence_set_id
from easyup_biga.persistence import (
    connect,
    init_schema,
    list_evidence_set_datasets,
    load_dataset_snapshot,
    load_evidence_set,
    save_evidence_set,
)
from easyup_biga.providers import parse_index_daily

TID = "BIGA-20260302-001"


def _rows(symbol: str, n: int) -> list[dict]:
    off = 0.0 if symbol == "sh000001" else 1000.0
    base = date(2026, 3, 2)
    return [
        {
            "day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": 3000.0 + off + i,
            "high": 3010.0 + off + i,
            "low": 2990.0 + off + i,
            "close": 3000.0 + off + i,
            "volume": 10_000_000 + i,
        }
        for i in range(n)
    ]


def _sina(symbol: str, *, bars: int):
    rows = _rows(symbol, bars)
    return parse_index_daily(symbol, rows, raw_text=json.dumps(rows))


def test_冻结同时发布通用DatasetSnapshot(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_sina, path=db)

    esid = coord.freeze_index_daily(TID, ["sh000001", "sz399106"], bars=120)

    es = load_evidence_set(esid, path=db)
    assert es is not None
    assert es["manifest"]["manifest_version"] == "2"
    ref = es["manifest"]["datasets"]["cn.index.daily_bars"]
    links = list_evidence_set_datasets(esid, path=db)
    assert links == [{
        "dataset_id": "cn.index.daily_bars",
        "snapshot_id": ref["snapshot_id"],
        "created_at": links[0]["created_at"],
    }]

    snap = load_dataset_snapshot(ref["snapshot_id"], path=db)
    assert snap is not None
    assert snap["dataset_id"] == "cn.index.daily_bars"
    assert snap["status"] == "COMPLETE"
    assert snap["partition_key"] == {"evidence_set_id": esid}
    assert snap["knowledge_cutoff"] == es["manifest"]["knowledge_cutoff"]

    # Existing Phase-2 reader remains the behavior source for specialists.
    assert len(coord.read_index_daily(esid, "sh000001", bars=25).bars) == 25
    assert len(coord.read_index_daily(esid, "sh000001", bars=120).bars) == 120

    with connect(db, readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_artifacts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM provider_attempts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM dataset_partitions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM quality_reports").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM dataset_snapshots").fetchone()[0] == 1


def test_证据集与血缘同事务_失败则两者都不落库(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    esid = new_evidence_set_id()

    with pytest.raises(ValueError, match="不存在"):
        save_evidence_set(
            evidence_set_id=esid,
            decision_id=None,
            manifest={"manifest_version": "2", "datasets": {}},
            dataset_snapshots={"cn.index.daily_bars": "dss-does-not-exist"},
            path=db,
        )

    assert load_evidence_set(esid, path=db) is None


def _foreign(symbol: str, *, bars: int):
    """自报一个数据平台没登记的 provider。

    `IndexDaily.source` 是**由 `provider_id` 派生的只读属性**，
    所以改 provider_id 就够了，`source` 会跟着变成 `em:kline/...`。
    """
    return replace(_sina(symbol, bars=bars), provider_id="em")


def test_调试缝_未登记的provider跳过血缘而不是报错(tmp_path):
    """注入 fetcher = 测试/调试缝。此时这次冻结不属于任何已注册 dataset。

    🔴 这条 seam 我一度整个删掉，理由是「生产路径与测试路径应当是同一条」。
    **那是错的**：批 E-20.2 的 `test_换个provider落库的source跟着变` 必须注入一个
    未注册的 provider 才能验「出处不会说谎」——删掉 seam 等于逼那条测试改用
    注册过的源，而那样它就测不到它要测的东西了。

    ⚠️ 外部实现这条 seam 的**条件**是对的，错的是它的**捕获范围**：
    `except KeyError:` 会把 `entry["snapshot_id"]` 缺失之类的结构性 bug
    一并咽掉（R-3）。这里只捕获 `ProviderNotRegistered`。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_foreign, path=db)
    esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)

    es = load_evidence_set(esid, path=db)
    # 没登记血缘 ⇒ manifest 就不是 v2。版本号描述的是**形状**。
    assert es["manifest"]["manifest_version"] == "1"
    assert "datasets" not in es["manifest"]
    assert list_evidence_set_datasets(esid, path=db) == []
    # 🔴 Phase-2 的冻结与读取完全不受影响 —— 这正是 P3-2 的红线。
    assert len(coord.read_index_daily(esid, "sh000001", bars=5).bars) == 5


def test_严格路径_未登记的provider直接抛(tmp_path):
    """不是调试缝时（显式给了 bridge），注册表查不到就**抛**，不静默跳过。

    判据用「显式注入 bridge」来表达「这不是调试缝」，而不是去跑默认 fetcher ——
    后者会真的出网，被 conftest 的禁网围栏拦住，红的原因就不是我们要验的那个了。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    coord = SnapshotCoordinator(
        fetcher=_foreign, path=db, dataset_bridge=IndexDailyDatasetBridge(path=db))
    with pytest.raises(KeyError, match="必须恰好一个"):
        coord.freeze_index_daily(TID, ["sh000001"], bars=5)


def test_manifest自报的provider与raw不符时_fail_closed(tmp_path):
    """桥自己的入参边界：`entries` 说一套、落库的 raw 说另一套。

    🔴 判据打在**它真正可达的那一处**。走 `freeze_index_daily` 时
    `IndexDaily.source` 是由 `provider_id` 派生的只读属性，两者不可能分叉 ——
    在那条路上这个检查是不可达的。而 `publish()` 是公开方法，`entries` 可以来自
    别处（比如从库里读回的 manifest），那才是它守的边界。

    ⚠️ 这一点本身就是个教训：**先找到判据可达的那一处，再写测试**。
    第一版把它写成走 coordinator，测出来的是另一条分支（源前缀未注册），
    而那条已经由上面一条覆盖了 —— 两条测试测同一件事，还都是绿的。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_sina, path=db)
    esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)

    es = load_evidence_set(esid, path=db)
    entries = dict(es["manifest"]["symbols"])
    entries["sh000001"] = {**entries["sh000001"], "provider_id": "szse"}

    bridge = IndexDailyDatasetBridge(path=db)
    with pytest.raises(KeyError, match="manifest 自报"):
        bridge.publish(evidence_set_id=new_evidence_set_id(), decision_id=None,
                       run_id=None, entries=entries)


def test_调试缝只咽未注册_不咽结构性KeyError(tmp_path, monkeypatch):
    """🔴 这条守的是外部实现真正的缺陷，不是它的 seam 条件。

    它的 seam 用 `except KeyError:` 兜底。而 `publish()` 里
    `entry["snapshot_id"]`、manifest 字段缺失等等**都会抛 KeyError** ——
    于是一个真正的结构性 bug，在调试缝上会被**静默咽掉**，冻结照常完成、
    manifest 悄悄退回 v1，没有任何地方报错。那正是 R-3 的形状。

    ⚠️ 判据必须打在这里，不能打在「严格路径会不会抛」上：
    把 `except ProviderNotRegistered` 放宽成 `except KeyError`，
    严格路径那条测试**照样绿**（它走的是 re-raise 分支）。
    实测：第一版探针就是这么落错地方的，弄坏了也不红。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_sina, path=db)   # fetcher 注入 ⇒ 调试缝开着

    def boom(**kwargs):
        raise KeyError("snapshot_id")       # 模拟 publish 内部的结构性 bug

    monkeypatch.setattr(coord._dataset_bridge, "publish", boom)
    with pytest.raises(KeyError, match="snapshot_id"):
        coord.freeze_index_daily(TID, ["sh000001"], bars=5)
