"""emotion-calc 行为测试 —— 全部离线。

🔴 这些测试**不许联网**。理由不是速度，是可信度：
依赖外网的测试会因为对方抖动而随机变红，几次之后所有人都会开始忽略它的结果 ——
一个被习惯性忽略的测试等于没有测试。

真实采集的验证靠手工跑一次（见教程 05 的「验证」小节），
以及 tests 里对**返回结构**的断言。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "emotion-calc" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sources = _load("sources")
ec = _load("emotion_calc")

QDATE = "20260918"


def pool(name: str, total: int, rows: list[dict] | None = None, qdate: str = QDATE):
    return sources.PoolResult(
        pool=name, requested_date=QDATE, qdate=qdate, total=total,
        rows=rows if rows is not None else [{"lbc": 1, "zbc": 0}] * total,
        raw={"rc": 0, "data": {"tc": total,
                               "qdate": int(qdate) if qdate else None}},
    )


def breadth(a=4277, d=1173, f=180):
    return sources.BreadthResult(advance=a, decline=d, flat=f, per_market=[], raw={"rc": 0})


@pytest.fixture()
def wired(monkeypatch):
    """把采集层换成可控的桩。返回一个可改的 plan。"""
    plan: dict = {
        "limit_up": pool("limit_up", 78, [{"lbc": n, "zbc": z} for n, z in
                                          [(1, 1)] * 66 + [(2, 0)] * 8 + [(3, 0)] * 2 + [(4, 0)] * 2]),
        "broken_board": pool("broken_board", 25),
        "limit_down": pool("limit_down", 0, []),
        "breadth": breadth(),
    }

    def fake_pool(name, date, **kw):
        v = plan.get(name)
        if isinstance(v, Exception):
            raise v
        # 🔴 桩必须遵守自己的入参。如果这里不把 requested_date 换成真正传进来的
        # date，`date_matches` 就永远为真，严格模式那组测试会因为错误的原因通过。
        return dataclasses.replace(v, requested_date=date)

    def fake_breadth():
        v = plan["breadth"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(ec, "fetch_pool", fake_pool)
    monkeypatch.setattr(ec, "fetch_breadth", fake_breadth)
    return plan


def build(**kw):
    kw.setdefault("date", None)
    kw.setdefault("break_source", set())
    kw.setdefault("store", False)
    kw.setdefault("task_id", "BIGA-20260918-001")
    return ec.build_verdict(**kw)


class TestHappyPath:
    def test_完整时是PASS(self, wired):
        v = build()
        assert (v.status, v.verdict) == ("completed", "PASS")
        assert v.missing == []

    def test_核心字段与派生值(self, wired):
        r = build().result
        assert r["limit_up_count"] == 78
        assert r["broken_board_count"] == 25
        assert r["limit_down_count"] == 0
        assert r["max_streak"] == 4
        assert r["streak_2plus_count"] == 12
        assert r["streak_ladder"] == {"1": 66, "2": 8, "3": 2, "4": 2}
        assert r["broken_rate"] == round(25 / 103, 4)
        assert r["seal_never_broken_rate"] == round(12 / 78, 4)
        assert r["advance_count"] == 4277
        assert r["trade_date"] == QDATE

    def test_每个result字段都有证据(self, wired):
        """契约铁律 3 —— 由 AgentVerdict 构造时强制，这里再从外部确认一次。"""
        v = build()
        assert set(v.result) <= {e.field for e in v.evidence}

    def test_as_of取自qdate而非请求日期(self, wired):
        v = build()
        for e in v.evidence:
            assert e.as_of.strftime("%Y%m%d") == QDATE
            assert e.as_of.tzinfo is not None
            assert e.as_of.hour == 15  # 收盘时刻

    def test_不产出周期归类(self, wired):
        """硬约束 S-2：skill 不做判断性归类。"""
        r = build().result
        banned = {"stage", "cycle", "phase", "情绪阶段", "judgment", "advice"}
        assert not (banned & set(r)), f"skill 不该输出判断字段: {banned & set(r)}"
        for v in r.values():
            assert not isinstance(v, str) or v in (QDATE,), \
                f"skill 的 result 不该出现自然语言判断: {v!r}"


class TestMissingPaths:
    """R-3：算不出来必须说算不出来。"""

    def test_核心源断开是UNKNOWN不是PASS(self, wired):
        v = build(break_source={"limit_up"})
        assert v.verdict == "UNKNOWN"
        assert v.verdict != "PASS"
        assert any("涨停家数" in m for m in v.missing)

    def test_炸板率缺一个池就算不出来(self, wired):
        v = build(break_source={"broken_board"})
        assert "broken_rate" not in v.result
        assert any("炸板率" in m for m in v.missing)
        # 🔴 关键：不是填 0，是根本没有这个键
        assert v.result.get("broken_rate") is None

    def test_情绪分依赖三项齐备(self, wired):
        v = build(break_source={"broken_board"})
        assert "emotion_score" not in v.result
        assert any("情绪分" in m for m in v.missing)

    def test_非核心源断开是WARNING(self, wired):
        v = build(break_source={"limit_down"})
        assert v.verdict == "WARNING"
        assert len(v.missing) == 1

    def test_数据源异常进missing而不是崩溃(self, wired):
        wired["limit_up"] = sources.SourceError("连接被重置")
        v = build()
        assert v.verdict == "UNKNOWN"
        assert any("连接被重置" in m for m in v.missing)

    def test_全断时result为空且missing非空(self, wired):
        v = build(break_source={"limit_up", "broken_board", "limit_down", "breadth"})
        assert v.result == {}
        assert v.evidence == []
        assert len(v.missing) >= 4

    def test_三档verdict都可达(self, wired):
        """防死分支：任何一档取不到，说明那段逻辑是恒假的（L-7）。"""
        levels = {
            build().verdict,
            build(break_source={"limit_down"}).verdict,
            build(break_source={"limit_up"}).verdict,
        }
        assert levels == {"PASS", "WARNING", "UNKNOWN"}


class TestDateDiscipline:
    """数据源会静默返回别的日期 —— 这组测试守着那道门。"""

    def test_严格模式下日期不符进missing(self, wired):
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = pool(k, 78, qdate="20260918")
        v = build(date="20260920")          # 请求周日
        assert v.result == {}
        assert all("20260920" in m and "20260918" in m
                   for m in v.missing if "请求" in m)

    def test_宽松模式下日期不符只是警告(self, wired):
        v = build(date=None)
        assert v.verdict == "PASS"
        assert v.missing == []

    def test_各池报告日期不一致时拒绝汇总(self, wired):
        """不同来源说的不是同一天，就不能当作同一天的事实汇总。"""
        wired["broken_board"] = pool("broken_board", 25, qdate="20260917")
        v = build()
        assert v.result == {}
        assert any("交易日不一致" in m for m in v.missing)

    def test_缺qdate时进missing(self, wired):
        wired["limit_up"] = pool("limit_up", 78, qdate=None)
        v = build()
        assert any("qdate" in m for m in v.missing)

    def test_涨跌家数无可信日期时不被静默丢弃(self, wired):
        """数据取到了但定不了日期 —— 必须说出来，不能当没发生。"""
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = sources.SourceError("挂了")
        v = build()
        assert any("涨跌家数" in m and "交易日" in m for m in v.missing)


class TestSources:
    """采集层：失败一律抛错，绝不返回兜底值。"""

    def test_未知池名(self):
        with pytest.raises(ValueError, match="未知的池名"):
            sources.fetch_pool("nope", "20260918")

    @pytest.mark.parametrize("bad", ["2026918", "abcdefgh", "", "20260918000"])
    def test_日期格式(self, bad):
        with pytest.raises(ValueError, match="YYYYMMDD"):
            sources.fetch_pool("limit_up", bad)

    def test_端点表与池名一致(self):
        assert set(sources.POOL_ENDPOINTS) == {"limit_up", "broken_board", "limit_down"}

    def test_date_matches语义(self):
        assert pool("limit_up", 1, qdate="20260918").date_matches is True
        assert sources.PoolResult(pool="limit_up", requested_date="20260920",
                                  qdate="20260918", total=1, rows=[{}],
                                  raw={}).date_matches is False


class TestLadder:
    def test_空池(self):
        assert ec._ladder([]) == {}

    def test_缺lbc按一板算(self):
        assert ec._ladder([{}, {"lbc": None}]) == {1: 2}

    def test_排序(self):
        assert list(ec._ladder([{"lbc": 3}, {"lbc": 1}, {"lbc": 2}])) == [1, 2, 3]
