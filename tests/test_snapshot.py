"""SnapshotCoordinator 行为测试 —— 全部离线（设计文档 §6 批 D-I）。

🔴 这些测试**不许联网**（`conftest.py` 的禁网围栏兜底）。真实抓取被换成一个
**计数桩**：它返回固定的合法日线，并记下自己被调了几次 —— 「冻结一次、多处读」
里那个「一次」是不是真的一次，靠的就是这个计数。

本文件的组织：**批 D-I 的五道探针各一组，每组至少一条证明它会红**。
守卫写了却没见它红过，和没写是一回事（`dev-workflow` 第 3 条 / L-13）。

  P1  三个消费者读同一个 symbol，底层抓取只发生 1 次
  P2  三份结果是同一份数据切出来的（同一次抓取），不是偶然相等
  P3  一次决策、两个指数代码 ⇒ raw_market_snapshot 至多 2 行（不是 6 行）
  P4  evidence_sets 第一次真的写行后，UPDATE/DELETE 仍被触发器拒
  P5  给 evidence_set_id 能反查出它冻了哪几行 raw（manifest 形状的判据）

另加：`parse_index_daily` 抽取是空操作（重构信号）、read 端 fail-closed。
"""

from __future__ import annotations

import pathlib
import sys
from datetime import date, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _snapshot import MANIFEST_KIND, SnapshotCoordinator, SnapshotReadError  # noqa: E402
from _sources import DailyBar, SourceError, parse_index_daily  # noqa: E402
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    init_schema,
    load_evidence_set,
    load_raw_snapshot,
)

# 三个消费者今天各自要的根数（实测值）。technical 是 120，**不是**分发提示词
# 顺手写的 25 —— 它决定了冻结时 bars 至少要取 120（见 freeze 的 docstring）。
SECTOR_BARS, MARKET_BARS, TECHNICAL_BARS = 2, 25, 120
MAX_BARS = TECHNICAL_BARS


def _rows(symbol: str, n: int) -> list[dict]:
    """造 n 行**升序、无重复日期**的新浪日线原始响应（dict 数组，值可为数）。

    不同 symbol 的点位错开一个常量偏移 —— 这样 P2「同一份数据切出来」不是靠
    「两个 symbol 恰好数值相等」蒙混过关，而是真的同一行。
    """
    off = 0.0 if symbol == "sh000001" else 1000.0
    base = date(2026, 3, 2)
    return [
        {
            "day": (base + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": round(3000.0 + off + i * 0.5, 3),
            "high": round(3010.0 + off + i * 0.5, 3),
            "low": round(2990.0 + off + i * 0.5, 3),
            "close": round(3000.0 + off + i * 0.5, 3),
            "volume": 10_000_000 + i,
        }
        for i in range(n)
    ]


class _Coord:
    """fixture 返回体：coordinator + 计数 + 库路径，一次取齐。"""

    def __init__(self, coord, calls, db):
        self.coord = coord
        self.calls = calls
        self.db = db


@pytest.fixture()
def env(tmp_path, monkeypatch) -> _Coord:
    db = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(db))
    init_schema(db)

    calls = {"n": 0, "log": []}

    def fake_fetch(symbol, *, bars):
        calls["n"] += 1
        calls["log"].append((symbol, bars))
        return parse_index_daily(symbol, _rows(symbol, bars))

    coord = SnapshotCoordinator(fetcher=fake_fetch, path=db)
    return _Coord(coord, calls, db)


# ─────────────────────────────────────────────── 重构信号：parse 抽取是空操作


class TestParseExtraction:
    """`parse_index_daily` 是从 `fetch_index_daily` 抽出来的纯函数。

    抽取只是把「网络」和「解析」分开，解析逻辑一字未改 —— 下面既锁正常解析，
    也锁每一条形状校验仍在（证明抽取没顺手丢掉某个 raise）。
    """

    def test_正常解析(self):
        d = parse_index_daily("sh000001", _rows("sh000001", 3))
        assert [b.day for b in d.bars] == ["20260302", "20260303", "20260304"]
        assert d.bars[-1].close == 3001.0 and d.bars[-1].volume == 10_000_002
        assert isinstance(d.bars[0], DailyBar)
        assert d.trade_date == "20260304"

    def test_fetch仍然委托给parse(self, monkeypatch):
        """离线证明 fetch → parse 的接线：桩掉 get_json（网络），parse 保持真。

        如果 fetch 不再走 parse（比如有人把解析逻辑抄回 fetch 里），这条不会
        直接红，但 `parse` 的那些校验测试就成了没有生产调用方的摆设 ——
        所以这里显式钉住「fetch 拿到响应后交给 parse」这条边。
        """
        from _sources import sina as sina_mod
        monkeypatch.setattr(sina_mod, "get_json", lambda url, **kw: _rows("sh000001", 4))
        d = sina_mod.fetch_index_daily("sh000001", bars=4)
        assert len(d.bars) == 4 and d.trade_date == "20260305"

    @pytest.mark.parametrize("payload,frag", [
        ({"not": "a list"}, "不是数组"),
        ([], "空数组"),
        ([{"day": "2026-03-02", "open": 1, "high": 1, "low": 1, "close": 1}], "缺字段"),
        ([{"day": "bad", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}], "无法解析日期"),
    ])
    def test_形状校验仍在(self, payload, frag):
        with pytest.raises(SourceError, match=frag):
            parse_index_daily("sh000001", payload)

    def test_非升序被拒(self):
        rows = _rows("sh000001", 3)
        rows.reverse()
        with pytest.raises(SourceError, match="未按日期升序"):
            parse_index_daily("sh000001", rows)

    def test_重复日期被拒(self):
        rows = _rows("sh000001", 3)
        rows[1]["day"] = rows[0]["day"]
        with pytest.raises(SourceError, match="重复日期"):
            parse_index_daily("sh000001", rows)


# ─────────────────────────────────────────────────── P1 · 冻结一次，多处读


class TestFreezeOnceReadMany:
    def test_P1_三个消费者读同一symbol只抓一次(self, env):
        """🔴 P1：freeze 抓一次，三个消费者按 2/25/120 各读一次，底层抓取仍是 1 次。

        它守的是：read **不重抓**。如果谁把 read 改成 `fetch_index_daily(bars=M)`，
        计数会从 1 跳到 4，这条立刻红。
        """
        c = env.coord
        esid = c.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=MAX_BARS)
        assert env.calls["n"] == 1, "freeze 对一个 symbol 应只抓一次"

        c.read_index_daily(esid, "sh000001", bars=SECTOR_BARS)
        c.read_index_daily(esid, "sh000001", bars=MARKET_BARS)
        c.read_index_daily(esid, "sh000001", bars=TECHNICAL_BARS)
        assert env.calls["n"] == 1, (
            f"三个消费者读完，底层抓取应仍是 1 次，实测 {env.calls['n']} 次 —— "
            "read 不该联网，它从冻结的 raw 切片")

    def test_P2_三份结果是同一份数据切出来的(self, env):
        """🔴 P2：sector 的 2 根 == market/technical 的最后 2 根，逐字段相同；
        且三份都指向**同一行** raw —— 是同一次抓取，不是三份偶然相等。
        """
        c = env.coord
        esid = c.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=MAX_BARS)
        s2 = c.read_index_daily(esid, "sh000001", bars=SECTOR_BARS)
        s25 = c.read_index_daily(esid, "sh000001", bars=MARKET_BARS)
        s120 = c.read_index_daily(esid, "sh000001", bars=TECHNICAL_BARS)

        # DailyBar 是 frozen dataclass，== 是逐字段比较
        assert list(s2.bars) == list(s25.bars[-SECTOR_BARS:]) == list(s120.bars[-SECTOR_BARS:])
        assert len(s2.bars) == 2 and len(s25.bars) == 25 and len(s120.bars) == 120

        # 「同一次抓取」的结构证明：sh000001 在这次冻结里只有一行 raw
        es = load_evidence_set(esid, path=env.db)
        entry = es["manifest"]["symbols"]["sh000001"]
        rows_for_sh = _count_raw(env.db, "sina:kline/sh000001")
        assert rows_for_sh == 1, "同一次抓取只该落一行 raw"
        # 三个 read 都是从 entry.snapshot_id 那一行切的（read 的唯一数据源）
        assert isinstance(entry["snapshot_id"], int)


# ─────────────────────────────────────────────────────────── P3 · 落盘行数


class TestRawRowCount:
    def test_P3_两个代码至多两行(self, env):
        """🔴 P3：一次决策、两个指数代码 ⇒ raw 至多 2 行。

        今天三个 skill 各自抓（3 × 2 = 最多 6 行）。冻结之后，无论多少消费者、
        各要多少根，落盘只有「symbol 数」这么多行。
        """
        c = env.coord
        esid = c.freeze_index_daily(
            "BIGA-20260302-001", ["sh000001", "sz399106"], bars=MAX_BARS)

        # 模拟所有消费者读一遍（sector 只读 sh、market/technical 读各自的）
        c.read_index_daily(esid, "sh000001", bars=SECTOR_BARS)
        c.read_index_daily(esid, "sh000001", bars=MARKET_BARS)
        c.read_index_daily(esid, "sz399106", bars=MARKET_BARS)
        c.read_index_daily(esid, "sh000001", bars=TECHNICAL_BARS)

        assert _count_raw(env.db) == 2, "两个指数代码，冻结只该落 2 行 raw，不是 6"
        assert env.calls["n"] == 2, "两个 symbol，底层抓取共 2 次"

    def test_重复symbol只抓一次(self, env):
        c = env.coord
        c.freeze_index_daily(
            "BIGA-20260302-001", ["sh000001", "sh000001", "sz399106"], bars=MARKET_BARS)
        assert env.calls["n"] == 2, "去重后 sh000001 只抓一次"
        assert _count_raw(env.db) == 2


# ─────────────────────────────────────────────── P4 · evidence_sets 只追加


class TestEvidenceSetAppendOnly:
    """🔴 P4：evidence_sets 的只追加触发器 v7 建表时就带了，
    `test_store.py::test_每张表都有只追加触发器` 动态发现、理论上自动覆盖。
    这一批第一次真的往这张表写行 —— 补一条直接的 UPDATE/DELETE 探针，确认覆盖
    对「有真实数据的行」也成立。
    """

    def test_P4_UPDATE被拒(self, env):
        env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(env.db) as conn:
                conn.execute("UPDATE evidence_sets SET decision_id='篡改'")

    def test_P4_DELETE被拒(self, env):
        env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(env.db) as conn:
                conn.execute("DELETE FROM evidence_sets")

    def test_同号不许写两次(self, env):
        """evidence_set_id 是主键 —— 铸出来的号不复用（save_evidence_set 报人话）。"""
        from _store import save_evidence_set
        save_evidence_set(evidence_set_id="es-dup", decision_id=None,
                          manifest={"kind": MANIFEST_KIND, "symbols": {}}, path=env.db)
        with pytest.raises(ValueError, match="已存在"):
            save_evidence_set(evidence_set_id="es-dup", decision_id=None,
                              manifest={"kind": MANIFEST_KIND, "symbols": {}}, path=env.db)


# ────────────────────────────────────────────────────────── P5 · 反查 manifest


class TestReverseQuery:
    def test_P5_从evidence_set反查到raw行(self, env):
        """🔴 P5：给 evidence_set_id，能真的走通到它冻的那几行 raw_market_snapshot。

        判据不是「manifest 里有个字段」，而是：反查出的 snapshot_id 逐个能在
        raw 层取到行，且它们的 source 正是这次冻的两个 symbol。
        """
        c = env.coord
        esid = c.freeze_index_daily(
            "BIGA-20260302-001", ["sh000001", "sz399106"], bars=MARKET_BARS)

        ids = c.frozen_snapshot_ids(esid)
        assert len(ids) == 2

        sources = set()
        for sid in ids:
            row = load_raw_snapshot(sid, path=env.db)
            assert row is not None, f"manifest 指向 snapshot_id={sid} 却取不到 raw 行"
            sources.add(row["source"])
        assert sources == {"sina:kline/sh000001", "sina:kline/sz399106"}

    def test_manifest带够反查所需的字段(self, env):
        esid = env.coord.freeze_index_daily(
            "BIGA-20260302-001", ["sh000001"], bars=MARKET_BARS)
        es = load_evidence_set(esid, path=env.db)
        assert es["decision_id"] == "BIGA-20260302-001"
        assert es["manifest"]["kind"] == MANIFEST_KIND
        assert es["manifest"]["frozen_bars"] == MARKET_BARS
        entry = es["manifest"]["symbols"]["sh000001"]
        assert set(entry) >= {"snapshot_id", "source", "content_sha256", "bar_count", "trade_date"}

    def test_反查不存在的号报错(self, env):
        with pytest.raises(SnapshotReadError, match="不存在"):
            env.coord.frozen_snapshot_ids("es-从来没有过")


# ───────────────────────────────────────────────────────── read 端 fail-closed


class TestReadFailClosed:
    def test_要的根数超过冻结的根数就报错(self, env):
        """🔴 冻了 5 根、读 6 根 —— 抛错，绝不静默返回 5 根（R-3 / L-2）。"""
        esid = env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        with pytest.raises(SnapshotReadError, match="读不出"):
            env.coord.read_index_daily(esid, "sh000001", bars=6)

    def test_恰好等于冻结根数可以读(self, env):
        esid = env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        assert len(env.coord.read_index_daily(esid, "sh000001", bars=5).bars) == 5

    def test_读没冻过的symbol报错(self, env):
        esid = env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        with pytest.raises(SnapshotReadError, match="不在 evidence set"):
            env.coord.read_index_daily(esid, "sz399106", bars=2)

    def test_读不存在的evidence_set报错(self, env):
        with pytest.raises(SnapshotReadError, match="不存在"):
            env.coord.read_index_daily("es-无", "sh000001", bars=2)

    def test_bars非正被拒(self, env):
        esid = env.coord.freeze_index_daily("BIGA-20260302-001", ["sh000001"], bars=5)
        with pytest.raises(ValueError, match="正整数"):
            env.coord.read_index_daily(esid, "sh000001", bars=0)

    def test_freeze空symbol被拒(self, env):
        with pytest.raises(ValueError, match="至少要一个 symbol"):
            env.coord.freeze_index_daily("BIGA-20260302-001", [], bars=5)


def _count_raw(db, source: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM raw_market_snapshot"
    args: tuple = ()
    if source is not None:
        sql += " WHERE source=?"
        args = (source,)
    with connect(db, readonly=True) as conn:
        return int(conn.execute(sql, args).fetchone()[0])
