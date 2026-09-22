"""批 D-II 接线：Specialist 改口读冻结快照。全部离线（禁网围栏兜底）。

D-I 建了「冻结一次、多处读」的机制；这一批把 market/sector/technical 的
`fetch_index_daily` 调用点真正切到 `read_index_daily`。这里钉住五道探针：

  P1  三个 skill 给同一个 evidence_set_id，日线证据的 raw_hash 都指向**同一份**冻结数据
  P2  risk 的 CROSS_CHECK 判据改成「raw_hash 是否相同」后：共享则不报、退回独立抓取则报
  P3  不给 --evidence-set-id 时仍自己联网抓（手工调试路径没被连坐拦掉）
  P4  读不同根数（sector 2 / technical 120）的 raw_hash 相同 —— 是冻结集登记的整份指纹
  P5  坏 evidence_set_id → fail-closed（SnapshotReadError），绝不静默退回独立抓取
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import date, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import STANCE_VOCAB, AgentVerdict, Evidence, now_cn  # noqa: E402
from _snapshot import SnapshotCoordinator, SnapshotReadError  # noqa: E402
from _sources import parse_index_daily  # noqa: E402
from _store import connect, init_schema, payload_sha256  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


market = _load("market_calc", "skills/market-calc/scripts/market_calc.py")
sector = _load("sector_calc", "skills/sector-calc/scripts/sector_calc.py")
technical = _load("technical_calc", "skills/technical-calc/scripts/technical_calc.py")
risk = _load("risk_check", "skills/risk-check/scripts/risk_check.py")

TID = "BIGA-20260302-001"


def _rows(symbol: str, n: int) -> list[dict]:
    off = 0.0 if symbol == "sh000001" else 1000.0
    base = date(2026, 3, 2)
    return [{"day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
             "open": 3000.0 + off + i * 0.5, "high": 3010.0 + off + i * 0.5,
             "low": 2990.0 + off + i * 0.5, "close": 3000.0 + off + i * 0.5,
             "volume": 10_000_000 + i} for i in range(n)]


def _fake_daily(symbol, *, bars):
    return parse_index_daily(symbol, _rows(symbol, bars))


def _rh(v: AgentVerdict, field: str) -> str | None:
    return next(e.raw_hash for e in v.evidence if e.field == field)


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    """tmp 库 + 一个已冻结 sh/sz@120 的 evidence set。返回 (esid, db, H_sh)。"""
    db = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(db))
    init_schema(db)
    coord = SnapshotCoordinator(fetcher=_fake_daily, path=db)
    esid = coord.freeze_index_daily(TID, ["sh000001", "sz399106"], bars=120)
    return esid, db, coord.frozen_content_sha256(esid, "sh000001")


# ─────────────────────────────────────────────── P1 · 三条日线证据同源


class TestSameFrozenSet:
    def test_P1_三个skill的日线证据指向同一份(self, frozen):
        """🔴 P1：market/sector/technical 给同一个 esid，它们各自的日线 Evidence
        的 raw_hash 都等于冻结集里 sh000001 那份 content_sha256 —— 三条真的指向
        同一份，不是三条分别看起来合理。

        非日线源（腾讯/涨跌家数/板块榜）全断掉，只留冻结日线：那些不在这一批范围内，
        断了不影响「日线证据是否同源」这条判据。
        """
        esid, _db, H = frozen
        mv = market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                  store=False, task_id=TID, evidence_set_id=esid)
        sv = sector.build_fact_bundle(break_source={"industry", "concept"},
                                  store=False, task_id=TID, evidence_set_id=esid)
        tv = technical.build_fact_bundle(break_source=set(), store=False,
                                     task_id=TID, evidence_set_id=esid)
        assert _rh(mv, "sh_close") == H
        assert _rh(tv, "close") == H
        assert _rh(sv, "trade_date") == H

    def test_三者报告同一个交易日(self, frozen):
        """同一份冻结数据 ⇒ 交易日必然一致（这正是共享要保证的）。"""
        esid, _db, _H = frozen
        mv = market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                  store=False, task_id=TID, evidence_set_id=esid)
        sv = sector.build_fact_bundle(break_source={"industry", "concept"},
                                  store=False, task_id=TID, evidence_set_id=esid)
        tv = technical.build_fact_bundle(break_source=set(), store=False,
                                     task_id=TID, evidence_set_id=esid)
        assert mv.result["trade_date"] == sv.result["trade_date"] == tv.result["trade_date"]

    def test_读冻结时不重复落盘raw(self, frozen):
        """三个 skill 读冻结（store=True）不该再往 raw_market_snapshot 写行 ——
        freeze 已经落了 2 行，读只是读。"""
        esid, db, _H = frozen
        before = _count_raw(db)
        market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                             store=True, task_id=TID, evidence_set_id=esid)
        technical.build_fact_bundle(break_source=set(), store=True,
                                task_id=TID, evidence_set_id=esid)
        assert _count_raw(db) == before, "读冻结不该新增 raw 行"


# ─────────────────────────────────────────── P4 · 不同根数，同一 raw_hash


class TestSharedHashAcrossBarCounts:
    def test_P4_读2根与读120根的raw_hash相同(self, frozen):
        """🔴 P4：sector 读 2 根、technical 读 120 根，raw_hash 却相同 —— 因为它取的是
        冻结集登记的**整份** content_sha256，不是对自己那一截重算。
        """
        esid, db, H = frozen
        coord = SnapshotCoordinator(path=db)
        s2 = coord.read_index_daily(esid, "sh000001", bars=2)
        s120 = coord.read_index_daily(esid, "sh000001", bars=120)
        assert len(s2.bars) == 2 and len(s120.bars) == 120
        # 两次读都用冻结集那份 hash（H）
        assert coord.frozen_content_sha256(esid, "sh000001") == H
        # 🔴 反证：如果各自对读到的那一截重算，会得到两个不同的哈希 ——
        #    这正是「不能对切片重算」的理由。
        assert payload_sha256(list(s2.raw)) != payload_sha256(list(s120.raw))


# ─────────────────────────────────── P2 · CROSS_CHECK 改判据后仍会红


def _up(agent, field, raw_hash):
    t = now_cn()
    stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
    ev = [
        Evidence(field=field, source="sina:kline/sh000001", value=3911.0,
                 as_of=t - timedelta(seconds=60), retrieved_at=t, raw_hash=raw_hash),
        Evidence(field="trade_date", source="sina:kline/sh000001", value="20260302",
                 as_of=t - timedelta(seconds=60), retrieved_at=t, raw_hash=raw_hash),
    ]
    return AgentVerdict(task_id=TID, agent=agent, status="completed", verdict="PASS",
                        result={field: 3911.0, "trade_date": "20260302"},
                        data_completeness=1.0, evidence=ev, warnings=[], missing=[],
                        stance=stance, elapsed_ms=1)


class TestCrossCheckJudge:
    @pytest.fixture()
    def wired(self, monkeypatch):
        store: dict[int, AgentVerdict] = {}
        monkeypatch.setattr(risk, "load_verdict", lambda vid: store.get(vid))
        monkeypatch.setattr(risk, "now_cn",
                            lambda: now_cn())
        return store

    def test_共享同一份则不报冲突(self, wired):
        wired[1] = _up("market", "sh_close", "HASH_SHARED")
        wired[2] = _up("technical", "close", "HASH_SHARED")
        v = risk.build_verdict(verdict_ids=[1, 2], store=False, task_id=TID)
        assert v.result["cross_check_conflict"] == []

    def test_P2_退回独立抓取_raw_hash不同_报红(self, wired):
        """🔴 P2：technical 没传 --evidence-set-id 悄悄退回独立抓取 ⇒ 它的 raw_hash
        出自另一份数据 ⇒ 与 market 的不同 ⇒ CROSS_CHECK 报红。
        改判据（值→raw_hash）之后**必须**证明它仍会红，否则不知道是不是又变成恒真。
        """
        wired[1] = _up("market", "sh_close", "HASH_FROZEN")
        wired[2] = _up("technical", "close", "HASH_INDEPENDENT_FETCH")
        v = risk.build_verdict(verdict_ids=[1, 2], store=False, task_id=TID)
        assert v.result["cross_check_conflict"], "raw_hash 不同却没报冲突"
        assert any(m.code == "risk.upstream.cross_check_conflict" for m in v.missing)

    def test_值相等也照报_守的不再是值(self, wired):
        """判据真的换了：两条 value 完全一样（都 3911.0），但 raw_hash 不同 —— 旧判据
        （比值）会放过，新判据（比 raw_hash）报红。这是「不再是恒真」的正面证据。"""
        wired[1] = _up("market", "sh_close", "HASH_A")
        wired[2] = _up("technical", "close", "HASH_B")  # value 相同、hash 不同
        v = risk.build_verdict(verdict_ids=[1, 2], store=False, task_id=TID)
        assert v.result["cross_check_conflict"]


# ─────────────────────────────────── P3 / P5 · 调试路径与 fail-closed


class TestDebugPathAndFailClosed:
    def test_P3_不给esid仍自己联网抓(self, tmp_path, monkeypatch):
        """🔴 P3：手工单跑某个 skill 调试（不传 --evidence-set-id）不被连坐拦掉 ——
        仍走 fetch_index_daily。"""
        monkeypatch.setenv("BIGA_DB_PATH", str(tmp_path / "biga.db"))
        calls = {"n": 0}

        def fake_fetch(symbol, *, bars):
            calls["n"] += 1
            return _fake_daily(symbol, bars=bars)

        monkeypatch.setattr(technical, "fetch_index_daily", fake_fetch)
        v = technical.build_fact_bundle(break_source=set(), store=False,
                                    task_id=TID, evidence_set_id=None)
        assert calls["n"] == 1, "不给 esid 就该自己抓"
        assert v.result.get("close")

    def test_P5_坏esid_fail_closed_不退回独立抓取(self, frozen, monkeypatch):
        """🔴 P5：给一个不存在的 evidence_set_id → SnapshotReadError 上抛（fail-closed），
        且**绝不**静默退回 fetch_index_daily。后者才是真正危险的（假装什么都对）。
        """
        _esid, _db, _H = frozen
        calls = {"n": 0}

        def fake_fetch(symbol, *, bars):
            calls["n"] += 1
            return _fake_daily(symbol, bars=bars)

        monkeypatch.setattr(technical, "fetch_index_daily", fake_fetch)
        with pytest.raises(SnapshotReadError):
            technical.build_fact_bundle(break_source=set(), store=False,
                                    task_id=TID, evidence_set_id="es-从来没冻过")
        assert calls["n"] == 0, "坏 esid 时绝不能退回独立抓取"

    def test_坏esid_market也不退回(self, frozen, monkeypatch):
        _esid, _db, _H = frozen
        calls = {"n": 0}

        def fake_fetch(symbol, *, bars):
            calls["n"] += 1
            return _fake_daily(symbol, bars=bars)

        monkeypatch.setattr(market, "fetch_index_daily", fake_fetch)
        with pytest.raises(SnapshotReadError):
            market.build_fact_bundle(date=None, break_source={"tencent", "breadth"},
                                 store=False, task_id=TID, evidence_set_id="es-无")
        assert calls["n"] == 0


def _count_raw(db) -> int:
    with connect(db, readonly=True) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM raw_market_snapshot").fetchone()[0])
