"""technical-calc 行为测试 —— 全部离线。

指标算错**不会报错**，只会给出一个看起来合理的数 ——
所以这里对每个指标都用可手算的构造数据验证，不只验「有没有值」。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "technical-calc" / "scripts"
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(SCRIPTS))

import _sources as sources  # noqa: E402
from _contract import CN_TZ  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "technical_calc", SCRIPTS / "technical_calc.py")
tc = importlib.util.module_from_spec(spec)
sys.modules["technical_calc"] = tc
spec.loader.exec_module(tc)

TRADE_DATE = "20260918"


def series(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    bars = [sources.DailyBar(day=f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                             open=c, high=h, low=lo, close=c, volume=100)
            for i, (c, h, lo) in enumerate(zip(closes, highs, lows))]
    bars[-1] = sources.DailyBar(day=TRADE_DATE, open=bars[-1].open,
                                high=bars[-1].high, low=bars[-1].low,
                                close=bars[-1].close, volume=bars[-1].volume)
    return sources.IndexDaily(symbol="sh000001", bars=bars, raw=[{}] * n)


@pytest.fixture()
def wired(monkeypatch):
    plan: dict = {"daily": series([3000 + i for i in range(120)])}
    monkeypatch.setattr(tc, "fetch_index_daily",
                        lambda symbol, **kw: (_ for _ in ()).throw(plan["daily"])
                        if isinstance(plan["daily"], Exception) else plan["daily"])
    monkeypatch.setattr(tc, "now_cn",
                        lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
    return plan


def build(**kw):
    kw.setdefault("break_source", set())
    kw.setdefault("store", False)
    kw.setdefault("task_id", "BIGA-20260918-001")
    return tc.build_verdict(**kw)


class TestIndicators:
    def test_ma可手算(self, wired):
        r = build().result
        # 等差序列 3000..3119，最后 5 根均值 = (3115+...+3119)/5
        assert r["ma5"] == pytest.approx(sum(range(3115, 3120)) / 5)
        assert r["ma20"] == pytest.approx(sum(range(3100, 3120)) / 20)

    def test_单调上涨时均线多头排列(self, wired):
        assert build().result["ma_order"] == "ma5>ma10>ma20>ma60"

    def test_单调下跌时均线空头排列(self, wired):
        wired["daily"] = series([3200 - i for i in range(120)])
        assert build().result["ma_order"] == "ma60>ma20>ma10>ma5"

    def test_一路上涨时rsi为100(self, wired):
        """区间内一天都没跌 —— 这是真的 100，不是除零错误。"""
        assert build().result["rsi14"] == 100.0

    def test_一路下跌时rsi接近0(self, wired):
        wired["daily"] = series([3200 - i for i in range(120)])
        assert build().result["rsi14"] == 0.0

    def test_横盘时rsi为50(self, wired):
        wired["daily"] = series([3000.0] * 120)
        assert build().result["rsi14"] == 50.0

    def test_单调上涨时DIF为正(self, wired):
        assert build().result["macd_dif"] > 0

    def test_匀速上涨时macd柱趋近零(self, wired):
        """⚠️ 这条曾经写错过：以为「上涨就该柱为正」。

        完全线性的序列里 DIF 与 DEA 收敛到同一个常数，柱必然趋近 0 ——
        **柱衡量的是加速度，不是方向。** 测试抓到的是写测试的人的误解。
        """
        assert abs(build().result["macd_hist"]) < 0.5

    def test_加速上涨时macd柱为正(self, wired):
        wired["daily"] = series([3000 + i * i * 0.02 for i in range(120)])
        assert build().result["macd_hist"] > 0

    def test_距高低点方向正确(self, wired):
        r = build().result
        assert r["dist_to_high60_pct"] <= 0, "收盘不可能高于区间最高"
        assert r["dist_to_low60_pct"] >= 0, "收盘不可能低于区间最低"

    def test_收盘相对ma20(self, wired):
        r = build().result
        assert r["price_vs_ma20_pct"] == pytest.approx(
            (r["close"] / r["ma20"] - 1) * 100, abs=0.01)


class TestInsufficientBars:
    """🔴 不足就不给，**不用更短的窗口凑一个看起来像的数**。"""

    def test_不足60根时ma60进missing(self, wired):
        wired["daily"] = series([3000 + i for i in range(40)])
        v = build()
        assert "ma60" not in v.result and "ma20" in v.result
        assert any(x.code == "technical.ma.insufficient_bars" for x in v.missing)

    def test_不足35根时macd进missing(self, wired):
        wired["daily"] = series([3000 + i for i in range(30)])
        v = build()
        assert "macd_dif" not in v.result
        assert any(x.code == "technical.macd.insufficient_bars" for x in v.missing)

    def test_不足15根时rsi进missing(self, wired):
        wired["daily"] = series([3000 + i for i in range(10)])
        v = build()
        assert "rsi14" not in v.result
        assert any(x.code == "technical.rsi.insufficient_bars" for x in v.missing)

    def test_根数不足时不是PASS(self, wired):
        wired["daily"] = series([3000 + i for i in range(40)])
        assert build().verdict != "PASS"


class TestGuards:
    def test_收盘为零进missing(self, wired):
        wired["daily"] = series([0.0] * 120)
        v = build()
        assert "close" not in v.result
        assert any(x.code == "technical.close.invalid_value" for x in v.missing)

    def test_数据源挂了是UNKNOWN(self, wired):
        wired["daily"] = sources.SourceError("连接被重置")
        v = build()
        assert v.verdict == "UNKNOWN" and v.result == {}

    def test_字段数与confidence分母一致(self, wired):
        v = build()
        assert len(v.result) == tc._EXPECTED_FIELDS and v.confidence == 1.0

    def test_只做上证不做深证(self):
        """范围外 ≠ 数据缺失：不做深证是设计选择，不该出现在 missing 里。"""
        v_src = (SCRIPTS / "technical_calc.py").read_text(encoding="utf-8")
        assert "sz399106" not in v_src

    def test_不做个股这件事写在了脚本里(self):
        src = (SCRIPTS / "technical_calc.py").read_text(encoding="utf-8")
        assert "不做个股" in src, "为什么不做个股要写下来，否则下一个人会以为是漏了"


class TestR3Paths:
    def test_三条路径(self, wired):
        assert build().verdict == "PASS"
        wired["daily"] = series([3000 + i for i in range(40)])
        assert build().verdict == "WARNING"
        assert build(break_source={"daily"}).verdict == "UNKNOWN"


class TestF5BadTickInWindow:
    """外部评审 F5：60 日窗口里**任意一根**坏 tick 都会污染高低点距离。

    🔴 这个类存在的理由，是一次探针失败：
    `tests/test_sanity_fence.py` 已经把围栏函数本身测得很细，
    但把 `technical_calc` 里那行调用删掉之后，**一条测试都没红** ——
    围栏被测了，「它有没有被用上」没被测。

    > 测函数，和测「产品线上真的走了这个函数」，是两件事。
    """

    def test_窗口内坏tick导致报缺失而不是荒谬数字(self, wired):
        closes = [3000 + i for i in range(120)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        lows[90] = 0.01          # 正数，不触发任何「≤0」守卫
        wired["daily"] = series(closes, highs, lows)

        v = build()
        codes = [m.code for m in v.missing]
        assert "technical.range.bad_bars" in codes, codes
        assert "dist_to_low60_pct" not in v.result
        assert "dist_to_high60_pct" not in v.result

    def test_干净数据照常给出两个距离(self, wired):
        r = build().result
        assert "dist_to_high60_pct" in r and "dist_to_low60_pct" in r
        assert abs(r["dist_to_low60_pct"]) < 100, "量级应当是个位到两位数的百分比"
