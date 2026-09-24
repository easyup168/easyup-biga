"""采集出处的三件事 —— 时刻、来源、指纹（批 R，评审 E-20）。全部离线。

三条守的是同一类毛病：**落库的出处描述的是调用方的假设，不是发生过的事实**。

  20.1  `retrieved_at` 记在抓取**完成**之后，不是发起之前
  20.2  `source` / `provider_id` / `adapter_version` 由 provider 声明，调用方不拼
  20.3  读回 raw 时重算指纹并比对 —— 从不被检验的指纹只是让人放心
"""

from __future__ import annotations

import json
import pathlib
import time
from dataclasses import replace
from datetime import date, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import now_cn  # noqa: E402
from _snapshot import SnapshotCoordinator, SnapshotReadError  # noqa: E402
from _sources import parse_index_daily  # noqa: E402
from _store import connect, init_schema, payload_sha256  # noqa: E402

TID = "BIGA-20260302-001"


def _rows(symbol: str, n: int) -> list[dict]:
    base = date(2026, 3, 2)
    return [{"day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
             "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
             "close": 3000.0 + i, "volume": 10_000_000 + i} for i in range(n)]


def _fake(symbol, *, bars):
    rows = _rows(symbol, bars)
    return parse_index_daily(symbol, rows, raw_text=json.dumps(rows))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


def _raw_row(db, snapshot_id: int) -> dict:
    with connect(db, readonly=True) as c:
        return dict(c.execute(
            "SELECT * FROM raw_market_snapshot WHERE snapshot_id=?", (snapshot_id,)).fetchone())


class TestE20_1取回时刻记在抓取之后:
    def test_retrieved_at不早于抓取完成的时刻(self, db):
        """🔴 以前 `retrieved = now_cn()` 写在 `_fetch(...)` **上面** —— 抓取耗时
        全被算进「数据有多新」。一次 30 秒的慢响应，落库的 retrieved_at 比真正
        拿到数据早 30 秒，证据因此显得更新鲜。偏差是**单向的**：只会高估。
        """
        done: list = []

        def slow(symbol, *, bars):
            time.sleep(0.05)          # 让「发起」与「完成」可分辨
            d = _fake(symbol, bars=bars)
            done.append(now_cn())     # 抓取真正完成的时刻
            return d

        coord = SnapshotCoordinator(fetcher=slow, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)
        sid = coord.frozen_snapshot_ids(esid)[0]
        import datetime as _dt
        recorded = _dt.datetime.fromisoformat(_raw_row(db, sid)["retrieved_at"])
        assert recorded >= done[0], (
            f"retrieved_at={recorded.isoformat()} 早于抓取完成 {done[0].isoformat()} "
            f"—— 记的是「我打算去抓」的时刻，不是「我拿到了」的时刻")


class TestE20_2出处由provider声明:
    def test_换个provider落库的source跟着变(self, db):
        """fetcher 是可注入的。以前 source 在调用方写死 `sina:kline/...`，
        于是**任何**替换后的数据源都被记成 sina —— 一条会说谎的出处。"""
        def other(symbol, *, bars):
            return replace(_fake(symbol, bars=bars), provider_id="probe", adapter_version="9")

        coord = SnapshotCoordinator(fetcher=other, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)
        row = _raw_row(db, coord.frozen_snapshot_ids(esid)[0])
        assert row["source"] == "probe:kline/sh000001"
        assert not row["source"].startswith("sina:")

    def test_manifest记下provider与解析版本(self, db):
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)
        from _store import load_evidence_set
        entry = load_evidence_set(esid, path=db)["manifest"]["symbols"]["sh000001"]
        assert entry["provider_id"] == "sina"
        assert entry["adapter_version"]

    def test_默认provider没被改掉(self, db):
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=5)
        assert _raw_row(db, coord.frozen_snapshot_ids(esid)[0])["source"] == "sina:kline/sh000001"


class TestE20_3读回时校验指纹:
    def test_正常快照读得通(self, db):
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=10)
        assert len(coord.read_index_daily(esid, "sh000001", bars=5).bars) == 5

    def test_指纹对不上就fail_closed(self, db):
        """raw 层只追加（触发器挡 UPDATE），所以这里直接插一行指纹不对的 ——
        模拟「库被动过」或「哈希口径变了而历史行没跟着走」。"""
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        esid = coord.freeze_index_daily(TID, ["sh000001"], bars=10)
        sid = coord.frozen_snapshot_ids(esid)[0]
        with connect(db) as c:
            c.execute(
                "INSERT INTO raw_market_snapshot"
                " (source,as_of,retrieved_at,payload_json,raw_text,content_sha256,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                ("sina:kline/sh000001", now_cn().isoformat(), now_cn().isoformat(),
                 json.dumps(_rows("sh000001", 10)), "篡改过的文本", "b" * 64,
                 now_cn().isoformat()))
            bad = c.execute("SELECT MAX(snapshot_id) FROM raw_market_snapshot").fetchone()[0]
        with connect(db) as c:
            man = json.loads(c.execute(
                "SELECT manifest_json FROM evidence_sets WHERE evidence_set_id=?",
                (esid,)).fetchone()[0])
        man["symbols"]["sh000001"]["snapshot_id"] = bad
        with pytest.raises(SnapshotReadError) as ei:
            coord._entry = lambda e, s: man["symbols"][s]   # 只改指路，不动 raw 层
            coord.read_index_daily(esid, "sh000001", bars=5)
        assert "指纹对不上" in str(ei.value)
        assert sid != bad

    def test_v13之前的旧行走旧口径也验得过(self, db):
        """`raw_text` 为 NULL 的历史行用 `payload_sha256` 验 —— 实测生产库 321 行
        全部一致。**不放行、也不误杀**：两种口径各自都是真校验。"""
        payload = _rows("sh000001", 10)
        with connect(db) as c:
            c.execute(
                "INSERT INTO raw_market_snapshot"
                " (source,as_of,retrieved_at,payload_json,raw_text,content_sha256,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                ("sina:kline/sh000001", now_cn().isoformat(), now_cn().isoformat(),
                 json.dumps(payload), None, payload_sha256(payload), now_cn().isoformat()))
            sid = c.execute("SELECT MAX(snapshot_id) FROM raw_market_snapshot").fetchone()[0]
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        coord._entry = lambda e, s: {"snapshot_id": sid}
        assert len(coord.read_index_daily("any", "sh000001", bars=5).bars) == 5

    def test_旧行指纹不对同样被拒(self, db):
        payload = _rows("sh000001", 10)
        with connect(db) as c:
            c.execute(
                "INSERT INTO raw_market_snapshot"
                " (source,as_of,retrieved_at,payload_json,raw_text,content_sha256,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                ("sina:kline/sh000001", now_cn().isoformat(), now_cn().isoformat(),
                 json.dumps(payload), None, "c" * 64, now_cn().isoformat()))
            sid = c.execute("SELECT MAX(snapshot_id) FROM raw_market_snapshot").fetchone()[0]
        coord = SnapshotCoordinator(fetcher=_fake, path=db)
        coord._entry = lambda e, s: {"snapshot_id": sid}
        with pytest.raises(SnapshotReadError):
            coord.read_index_daily("any", "sh000001", bars=5)
