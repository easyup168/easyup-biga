"""sector-calc 行为测试 —— 全部离线。

重点是一条：**板块榜全为 0 不是「所有板块都平盘」，是这一天还没开始。**
而榜单按涨跌幅排序，全 0 时「第一名」是任意的一行 ——
照着它说「今日领涨板块是 X」完全是编造。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "sector-calc" / "scripts"
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(SCRIPTS))

import _sources as sources  # noqa: E402
from _contract import CN_TZ  # noqa: E402

spec = importlib.util.spec_from_file_location("sector_calc", SCRIPTS / "sector_calc.py")
sc = importlib.util.module_from_spec(spec)
sys.modules["sector_calc"] = sc
spec.loader.exec_module(sc)

TRADE_DATE = "20260918"


def board(name, pct, inflow=1e8, leader="某某股份"):
    return sources.Board(code="BK" + name[:4], name=name, pct=pct,
                         main_inflow=inflow, advance=3, decline=1, leader=leader)


def result(kind="industry", boards=None, total=None):
    bs = boards if boards is not None else [
        board("半导体设备", 4.83), board("医疗服务", 2.1), board("白酒", 0.5),
        board("银行", -0.3), board("地产", -1.8)]
    return sources.BoardResult(kind=kind, total=total or len(bs), boards=bs,
                               raw={"pages": []})


def daily():
    bars = [sources.DailyBar(day=d, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)
            for d in ("20260917", TRADE_DATE)]
    return sources.IndexDaily(symbol="sh000001", bars=bars, raw=[{}])


@pytest.fixture()
def wired(monkeypatch):
    plan: dict = {"industry": result("industry"), "concept": result("concept"),
                  "date": daily()}

    def fake_boards(kind):
        v = plan[kind]
        if isinstance(v, Exception):
            raise v
        return v

    def fake_daily(symbol, **kw):
        v = plan["date"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(sc, "fetch_boards", fake_boards)
    monkeypatch.setattr(sc, "fetch_index_daily", fake_daily)
    monkeypatch.setattr(sc, "now_cn",
                        lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
    return plan


def build(**kw):
    kw.setdefault("break_source", set())
    kw.setdefault("store", False)
    kw.setdefault("task_id", "BIGA-20260918-001")
    return sc.build_verdict(**kw)


class TestHappyPath:
    def test_完整时是PASS(self, wired):
        v = build()
        assert (v.status, v.verdict) == ("completed", "PASS")

    def test_字段数与confidence分母一致(self, wired):
        v = build()
        assert len(v.result) == sc._EXPECTED_FIELDS and v.confidence == 1.0

    def test_榜单按涨跌幅降序(self, wired):
        top = build().result["industry_top"]
        assert [x["name"] for x in top[:2]] == ["半导体设备", "医疗服务"]
        assert top == sorted(top, key=lambda x: x["pct"], reverse=True)

    def test_上涨占比是板块层面的(self, wired):
        """与 market 的 advance_ratio（个股层面）不是同一个事实。"""
        assert build().result["industry_advance_ratio"] == round(3 / 5, 4)

    def test_主力净流入榜按钱排不按涨幅排(self, wired):
        wired["industry"] = result("industry", [
            board("涨得多钱少", 9.0, inflow=1e7),
            board("涨得少钱多", 0.4, inflow=9e9)])
        assert build().result["main_inflow_top"][0]["name"] == "涨得少钱多"


class TestPreSession:
    """🔴 这一条是整个 skill 的重点。"""

    def test_全为零时进missing而不是报平盘(self, wired):
        wired["industry"] = result("industry", [board(f"板块{i}", 0.0, 0.0, None)
                                                for i in range(20)])
        v = build()
        m = [x for x in v.missing if x.code == "sector.board.pre_session"]
        assert m, "全 0 必须被识别为盘前，而不是当成真实行情"
        assert "不是「所有板块都平盘」" in m[0]
        assert "industry_top" not in v.result, "此时榜单排序无意义，不许产出"

    def test_有一个非零就不算盘前(self, wired):
        bs = [board(f"板块{i}", 0.0, 0.0, None) for i in range(19)]
        bs.append(board("唯一有变化的", 0.3))
        wired["industry"] = result("industry", bs)
        assert "industry_top" in build().result

    def test_盘前时核心缺失所以是UNKNOWN(self, wired):
        for k in ("industry", "concept"):
            wired[k] = result(k, [board(f"b{i}", 0.0, 0.0, None) for i in range(5)])
        assert build().verdict == "UNKNOWN"


class TestGuards:
    def test_涨跌幅超出量级进missing(self, wired):
        wired["industry"] = result("industry", [board("异常板块", 87.5), board("正常", 1.0)])
        v = build()
        assert any(x.code == "sector.board.out_of_range" for x in v.missing)
        assert "industry_top" not in v.result

    def test_没有交易日则全部作废(self, wired):
        wired["date"] = sources.SourceError("挂了")
        v = build()
        assert v.result == {} and v.verdict == "UNKNOWN"
        assert any("trade_date" in x.code for x in v.missing)

    def test_交易日与market同源(self):
        """两者都取自 sina 日线 —— risk 才核得出「大家说的是同一天」。"""
        src = (SCRIPTS / "sector_calc.py").read_text(encoding="utf-8")
        assert "sh000001" in src and "fetch_index_daily" in src

    def test_板块榜无日期必须发警告(self, wired):
        assert any("不返回交易日字段" in w for w in build().warnings)

    def test_领涨股缺失只是警告不是缺失(self, wired):
        wired["industry"] = result("industry", [board("无领涨", 3.0, leader=None),
                                                board("正常", 1.0)])
        v = build()
        assert any("领涨股" in w for w in v.warnings)
        assert "industry_top" in v.result


class TestScopeNotMissing:
    def test_持续性不出现在missing里(self, wired):
        """🔴 范围外 ≠ 数据缺失。持续性每次都缺，塞进去会淹没真正的缺失。"""
        assert not any("持续" in str(m) for m in build().missing)


class TestR3Paths:
    @pytest.mark.parametrize("broken,expect", [
        (set(), "PASS"),
        ({"concept"}, "WARNING"),
        ({"industry"}, "UNKNOWN"),
        ({"date"}, "UNKNOWN"),
    ])
    def test_三条路径(self, wired, broken, expect):
        assert build(break_source=broken).verdict == expect

    def test_有缺失就绝不是PASS(self, wired):
        for b in ({"industry"}, {"concept"}, {"date"}):
            v = build(break_source=b)
            assert not (v.verdict == "PASS" and v.missing)
