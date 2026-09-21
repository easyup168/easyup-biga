"""market-calc 行为测试 —— 全部离线。

🔴 这些测试**不许联网**。理由不是速度，是可信度：
依赖外网的测试会因为对方抖动而随机变红，几次之后所有人都会开始忽略它的结果 ——
一个被习惯性忽略的测试等于没有测试。

真实采集的验证靠手工跑一次（见教程 11 的「验证」小节）。

本文件的组织方式：**五条守卫各有一组测试，每组至少一条证明它会红**。
守卫写了却从没见它红过，和没写是一回事。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "market-calc" / "scripts"
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(SCRIPTS))

import _sources as sources  # noqa: E402
from _contract import CN_TZ  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mc = _load("market_calc")

TRADE_DATE = "20260918"
#: 实测值（2026-09-18 收盘）。用真数据当桩，桩出问题时更容易看出来。
SH_CLOSE, SH_PREV, SH_VOL = 3911.871, 3875.60, 48571250700
SZ_CLOSE, SZ_PREV, SZ_VOL = 2512.627, 2471.39, 59715206355
SH_AMOUNT_WAN, SZ_AMOUNT_WAN = 99416945.0, 108293085.0


def bars(symbol: str, n: int, last_close: float, prev_close: float, last_vol: int):
    """造 n 根日线：前 n-2 根用 prev_close 填，倒数第二根 prev，最后一根 last。"""
    out = []
    for i in range(n):
        day = f"202608{i + 1:02d}" if i < 30 else f"202609{i - 29:02d}"
        out.append(sources.DailyBar(day=day, open=prev_close, high=prev_close,
                                    low=prev_close, close=prev_close, volume=last_vol))
    out[-2] = dataclasses.replace(out[-2], close=prev_close)
    out[-1] = dataclasses.replace(out[-1], day=TRADE_DATE, close=last_close,
                                  volume=last_vol)
    return sources.IndexDaily(symbol=symbol, bars=out, raw=[{}] * n)


def quote(code: str, amount_wan: float, vol_hand: int, at: str = TRADE_DATE + "161402"):
    return sources.IndexQuote(code=code, name=code, last=1.0, prev_close=1.0,
                              volume_hand=vol_hand, amount_wan=amount_wan,
                              quoted_at=at, raw="")


@pytest.fixture()
def wired(monkeypatch):
    """把三个数据源换成可控的桩。返回一个可改的 plan。"""
    plan: dict = {
        "sh000001": bars("sh000001", 25, SH_CLOSE, SH_PREV, SH_VOL),
        "sz399106": bars("sz399106", 25, SZ_CLOSE, SZ_PREV, SZ_VOL),
        "quotes": {
            "sh000001": quote("sh000001", SH_AMOUNT_WAN, SH_VOL // 100),
            "sz399106": quote("sz399106", SZ_AMOUNT_WAN, SZ_VOL // 100),
        },
        "breadth": sources.BreadthResult(advance=4277, decline=1173, flat=180,
                                         per_market=[], raw={"rc": 0}),
    }

    def fake_daily(symbol, **kw):
        v = plan.get(symbol)
        if isinstance(v, Exception):
            raise v
        return v

    def fake_quote(codes):
        v = plan["quotes"]
        if isinstance(v, Exception):
            raise v
        return {c: v[c] for c in codes if c in v}

    def fake_breadth():
        v = plan["breadth"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(mc, "fetch_index_daily", fake_daily)
    monkeypatch.setattr(mc, "fetch_index_quote", fake_quote)
    monkeypatch.setattr(mc, "fetch_breadth", fake_breadth)
    # 时间钉死在交易日收盘后，避免测试结果随运行时刻漂移
    monkeypatch.setattr(mc, "now_cn",
                        lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
    return plan


def build(**kw):
    kw.setdefault("date", None)
    kw.setdefault("break_source", set())
    kw.setdefault("store", False)
    kw.setdefault("task_id", "BIGA-20260918-001")
    return mc.build_verdict(**kw)


class TestHappyPath:
    def test_完整时是PASS(self, wired):
        v = build()
        assert (v.status, v.verdict) == ("completed", "PASS")
        assert v.missing == []

    def test_字段数与confidence分母一致(self, wired):
        """🔴 钉死 `_EXPECTED_FIELDS`。

        加了字段却忘了改这个常数，`confidence` 会 >1 而被契约层当场拒绝 ——
        那是在生产里炸。这条测试把它挪到 CI 里炸。
        """
        v = build()
        assert len(v.result) == mc._EXPECTED_FIELDS
        assert v.confidence == 1.0

    def test_三个源的值都落到了正确的字段(self, wired):
        r = build().result
        assert r["trade_date"] == TRADE_DATE
        assert r["sh_close"] == round(SH_CLOSE, 2)
        assert r["turnover_sh"] == round(SH_AMOUNT_WAN / 1e4, 2)
        assert r["turnover_total"] == round(
            (SH_AMOUNT_WAN + SZ_AMOUNT_WAN) / 1e4, 2)
        assert r["advance_count"] == 4277

    def test_每条证据的as_of都不晚于retrieved_at(self, wired):
        for e in build().evidence:
            assert e.as_of <= e.retrieved_at, e.field


class TestGuard1PriceSanity:
    """守卫 1 —— 东财延迟源实测返回过 最新=0.0 / 涨跌幅=-29971017728.0。"""

    def test_点位为零进missing(self, wired):
        wired["sh000001"] = bars("sh000001", 25, 0.0, SH_PREV, SH_VOL)
        v = build()
        assert "sh_close" not in v.result
        assert any("不是有效点位" in m for m in v.missing)

    def test_涨跌幅超出量级进missing而不是照收(self, wired):
        # prev=0.0001 ⇒ 涨跌幅天文数字，正是那条垃圾值的形状
        wired["sh000001"] = bars("sh000001", 25, SH_CLOSE, 0.0001, SH_VOL)
        v = build()
        assert "sh_pct" not in v.result and "sh_close" not in v.result
        assert any("超出 ±20.0%" in m for m in v.missing)

    def test_正常涨跌幅不被误伤(self, wired):
        v = build()
        assert v.result["sh_pct"] == pytest.approx(
            round((SH_CLOSE / SH_PREV - 1) * 100, 4))


class TestGuard2DateCrossCheck:
    """守卫 2 —— 两个独立源说的不是同一天时，不许挑一个用。"""

    def test_腾讯日期对不上进missing(self, wired):
        wired["quotes"] = {
            "sh000001": quote("sh000001", SH_AMOUNT_WAN, SH_VOL // 100, "20260917161402"),
            "sz399106": quote("sz399106", SZ_AMOUNT_WAN, SZ_VOL // 100, "20260917161402"),
        }
        v = build()
        assert "turnover_total" not in v.result
        assert any("不是同一天" in m for m in v.missing)

    def test_两市日线日期不一致则全部作废(self, wired):
        other = bars("sz399106", 25, SZ_CLOSE, SZ_PREV, SZ_VOL)
        other = dataclasses.replace(other, bars=other.bars[:-1] + [
            dataclasses.replace(other.bars[-1], day="20260917")])
        wired["sz399106"] = other
        v = build()
        assert any("交易日不一致" in m for m in v.missing)
        assert "sh_close" not in v.result


class TestGuard3VolumeBaseline:
    """守卫 3 —— 不足 21 根不拿更短的窗口凑一个均值。"""

    def test_日线不足21根时量能进missing(self, wired):
        wired["sh000001"] = bars("sh000001", 15, SH_CLOSE, SH_PREV, SH_VOL)
        v = build()
        assert "volume_ma20" not in v.result and "volume_ratio" not in v.result
        assert any("不用更短的窗口凑" in m for m in v.missing)

    def test_均量不含今日(self, wired):
        """把今天算进自己的基线会让放量被自己稀释。"""
        d = wired["sh000001"]
        today_vol = d.last.volume * 10
        wired["sh000001"] = dataclasses.replace(
            d, bars=d.bars[:-1] + [dataclasses.replace(d.bars[-1], volume=today_vol)])
        ma = mc._ma_volume(wired["sh000001"])
        assert ma == pytest.approx(SH_VOL), "20 日均量被今日的放量污染了"


class TestGuard4UnitCrossCheck:
    """守卫 4 —— 新浪按股、腾讯按手，差 100 倍。跨源做比值 = 静默错 100 倍。"""

    def test_单位口径不一致进missing(self, wired):
        # 腾讯直接给「股」而不是「手」⇒ ×100 后偏离 100 倍
        wired["quotes"]["sh000001"] = quote("sh000001", SH_AMOUNT_WAN, SH_VOL)
        v = build()
        assert "turnover_sh" not in v.result
        assert any("两源口径或单位已不一致" in m for m in v.missing)

    def test_微小偏离不误报(self, wired):
        """盘中两源刷新时刻不同必然有微小差异；
        要求逐位相等会造出一个盘中永远报红的检查。"""
        wired["quotes"]["sh000001"] = quote(
            "sh000001", SH_AMOUNT_WAN, int(SH_VOL / 100 * 1.005))
        assert "turnover_sh" in build().result


class TestGuard5BreadthHasNoDate:
    def test_必须发警告说明as_of是推断的(self, wired):
        assert any("不返回交易日字段" in w for w in build().warnings)

    def test_涨跌平全为零是尚未形成不是事实(self, wired):
        """🔴 三项全为 0 在任何真实交易时段都不可能。

        实测周一 09:05 盘前：数据源已清零，而它会被贴上**日线的交易日**
        （上一交易日）—— 于是 Card 上出现「9-18 上涨家数 0」，
        而那天真实是 4277。比「算不出来」更糟：它是一个**有日期的错值**。
        """
        wired["breadth"] = sources.BreadthResult(0, 0, 0, [], {"rc": 0})
        v = build()
        for f in ("advance_count", "decline_count", "flat_count", "advance_ratio"):
            assert f not in v.result, f"{f} 不该作为事实产出"
        assert any(m.code == "market.breadth.not_yet_formed" for m in v.missing)

    def test_没有恒假的分母分支(self):
        """加了 not_yet_formed 守卫之后，「分母为零」那条路永远走不到 —— L-7。"""
        src = (REPO / "skills/market-calc/scripts/market_calc.py").read_text(encoding="utf-8")
        assert "breadth_ratio.zero_denominator" not in src


class TestNoSilentDisappearance:
    """🔴 回归：字段不许既不产出、也不进 missing。

    早先的写法把整个 per-market 循环体挡在 `if d is None: continue` 后面，
    新浪日线一挂，来自腾讯的 `turnover_sh` **凭空消失** ——
    Card 上既看不到值，也看不到缺失项。这正是 R-3 要防的形状。
    """

    def test_日线缺失不连坐腾讯的成交额(self, wired):
        wired["sh000001"] = sources.SourceError("连接被重置")
        v = build()
        assert v.result.get("turnover_sh") == round(SH_AMOUNT_WAN / 1e4, 2)
        assert any("只有单源支撑" in w for w in v.warnings), \
            "少了交叉校验必须说出来，不能不声不响地给一个未经验证的值"

    def test_每个缺的核心字段都有对应的missing(self, wired):
        wired["quotes"] = sources.SourceError("挂了")
        v = build()
        assert "turnover_total" not in v.result
        assert any("成交额" in m for m in v.missing)


class TestR3Paths:
    """PASS / WARNING / UNKNOWN 三条路径都要可复现，且绝不出现「PASS + 有缺失」。"""

    @pytest.mark.parametrize("break_it,expect", [
        (set(), "PASS"),
        ({"breadth"}, "WARNING"),
        ({"tencent"}, "UNKNOWN"),
        ({"sina_sh"}, "UNKNOWN"),
    ])
    def test_三条路径(self, wired, break_it, expect):
        v = build(break_source=break_it)
        assert v.verdict == expect

    def test_有缺失就绝不是PASS(self, wired):
        for broken in ({"breadth"}, {"tencent"}, {"sina_sh"}, {"sina_sz"}):
            v = build(break_source=broken)
            assert not (v.verdict == "PASS" and v.missing), broken


class TestStrictDate:
    def test_指定日期对不上进missing(self, wired):
        v = build(date="20260917")
        assert any("请求 20260917" in m for m in v.missing)
        assert v.verdict == "UNKNOWN"

    def test_指定日期对得上照常(self, wired):
        assert build(date=TRADE_DATE).verdict == "PASS"
