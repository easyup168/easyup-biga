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
sys.path.insert(0, str(REPO / "skills"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# 采集层已抽到共享包 `skills/_sources/`（2.1 第 1 步）。
# 局部名仍叫 `sources`，让本文件其余引用零改动 ——
# 这次重构的信号是「124 条测试一条不变」，改测试就把信号弄脏了。
import _sources as sources  # noqa: E402

ec = _load("emotion_calc")

QDATE = "20260918"


def pool(name: str, total: int, rows: list[dict] | None = None, qdate: str = QDATE):
    return sources.PoolResult(
        pool=name, requested_date=QDATE, qdate=qdate, total=total,
        rows=rows if rows is not None else [{"lbc": 1, "zbc": 0}] * total,
        raw={"rc": 0, "data": {"tc": total,
                               "qdate": int(qdate) if qdate else None}},
    )


@pytest.fixture()
def wired(monkeypatch):
    """把采集层换成可控的桩。返回一个可改的 plan。"""
    plan: dict = {
        "limit_up": pool("limit_up", 78, [{"lbc": n, "zbc": z} for n, z in
                                          [(1, 1)] * 66 + [(2, 0)] * 8 + [(3, 0)] * 2 + [(4, 0)] * 2]),
        "broken_board": pool("broken_board", 25),
        "limit_down": pool("limit_down", 0, []),
    }

    def fake_pool(name, date, **kw):
        v = plan.get(name)
        if isinstance(v, Exception):
            raise v
        # 🔴 桩必须遵守自己的入参。如果这里不把 requested_date 换成真正传进来的
        # date，`date_matches` 就永远为真，严格模式那组测试会因为错误的原因通过。
        return dataclasses.replace(v, requested_date=date)

    monkeypatch.setattr(ec, "fetch_pool", fake_pool)
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
        assert r["trade_date"] == QDATE

    def test_不再产出涨跌家数(self):
        """裁定 15：涨跌家数归 market。这里守住它不会悄悄长回来。"""
        import inspect
        src = inspect.getsource(ec)
        for field in ("advance_count", "decline_count", "flat_count"):
            assert field not in src, f"{field} 又出现在 emotion-calc 里了"

    def test_字段数与confidence分母一致(self, wired):
        """数据齐备时 confidence 必须正好 1.0。

        移出涨跌家数之前这里的分母是 15、实际只有 13 个字段 ——
        **数据完整时也只读到 0.87，「完整」这件事永远表达不出来**。
        """
        v = build()
        assert len(v.result) == ec._EXPECTED_FIELDS
        assert v.confidence == 1.0

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
        v = build(break_source={"limit_up", "broken_board", "limit_down"})
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

    def test_没有可信交易日时全部作废并说出来(self, wired):
        """定不了日期就给不了 as_of —— 必须说出来，不能当没发生。"""
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = sources.SourceError("挂了")
        v = build()
        assert v.result == {}
        assert any("没有任何股池返回可用的交易日" in m for m in v.missing)


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


class TestIntradayAsOf:
    """🔴 盘中跑不能崩 —— 这是 Phase 1 埋的 bug，2.1 修的。

    原来的换算把交易日一律当成「当日 15:00 收盘」。交易日盘中 10:00 采数据时，
    `as_of`(今天15:00) > `retrieved_at`(今天10:00)，契约层直接 `ValueError`，
    **整个 skill 崩掉，连一条 missing 都留不下**。

    Phase 1 没撞上，纯粹因为那几天的实测都在收盘后或非交易日跑 ——
    而 BigA 是短线系统，盘中才是主场景。
    """

    @staticmethod
    def _at(hhmm: tuple[int, int], day: str = "20260921"):
        """把 now_cn 钉死在某个时刻，并让股池报告同一天。"""
        from datetime import datetime

        from _contract import CN_TZ
        d = datetime.strptime(day, "%Y%m%d").date()
        return datetime(d.year, d.month, d.day, hhmm[0], hhmm[1], tzinfo=CN_TZ)

    def test_盘中不崩且as_of退回取回时刻(self, wired, monkeypatch):
        today = "20260921"
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = dataclasses.replace(wired[k], qdate=today)
        monkeypatch.setattr(ec, "now_cn", lambda: self._at((10, 0), today))

        v = build()   # 不指定 date ⇒ 宽松模式，目标日就是「今天」

        assert v.evidence, "盘中应当照常产出证据，而不是抛异常"
        for e in v.evidence:
            assert e.as_of <= e.retrieved_at, f"{e.field} 的 as_of 晚于 retrieved_at"
        assert any("尚未收盘" in w for w in v.warnings), \
            "盘中快照必须标出来 —— 不标就会被当成全天结果"

    def test_收盘后仍按收盘时刻(self, wired, monkeypatch):
        today = "20260921"
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = dataclasses.replace(wired[k], qdate=today)
        monkeypatch.setattr(ec, "now_cn", lambda: self._at((16, 0), today))

        v = build()

        assert all(e.as_of.hour == 15 and e.as_of.minute == 0 for e in v.evidence)
        assert not any("尚未收盘" in w for w in v.warnings)


class TestTradeTimeHelper:
    """换算本身的分支 —— 它是两个 skill 共用的判据，单独钉死。"""

    @staticmethod
    def _dt(day: str, h: int, m: int = 0):
        from datetime import datetime

        from _contract import CN_TZ
        d = __import__("datetime").datetime.strptime(day, "%Y%m%d").date()
        return datetime(d.year, d.month, d.day, h, m, tzinfo=CN_TZ)

    def test_过去的交易日取收盘(self):
        a, w = sources.as_of_for_trade_date(
            "20260918", retrieved_at=self._dt("20260920", 20))
        assert (a.hour, a.minute) == (15, 0) and w is None

    def test_未来的交易日不静默接受(self):
        rt = self._dt("20260921", 10)
        a, w = sources.as_of_for_trade_date("20260922", retrieved_at=rt)
        assert a == rt and w and "晚于当前日期" in w

    def test_四个分支都给出合法的as_of(self):
        rt = self._dt("20260921", 10)
        for td in ("20260918", "20260921", "20260922"):
            a, _ = sources.as_of_for_trade_date(td, retrieved_at=rt)
            assert a <= rt, f"{td} 算出的 as_of 晚于 retrieved_at —— 契约层会拒绝"

    def test_非法日期格式抛错(self):
        with pytest.raises(ValueError):
            sources.as_of_for_trade_date("2026-09-18", retrieved_at=self._dt("20260921", 10))


class TestPreSessionZeros:
    """🔴 盘中/盘前三个池全为 0 —— 这是「还没形成」，不是「涨停 0 家」。

    实测 2026-09-21 周一 09:05：股池 `tc=0` 而 `qdate=今天`。
    当成事实上卡，读者看到的是「冰点」这种极端读数。
    """

    def test_全零且未收盘时不作为事实(self, wired, monkeypatch):
        from datetime import datetime

        from _contract import CN_TZ
        today = "20260921"
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = pool(k, 0, [], qdate=today)
        monkeypatch.setattr(ec, "now_cn",
                            lambda: datetime(2026, 9, 21, 9, 5, tzinfo=CN_TZ))
        v = build()
        assert v.result == {}, "尚未形成的数据不许作为事实产出"
        assert any(m.code == "emotion.pool.not_yet_formed" for m in v.missing)
        assert v.verdict == "UNKNOWN"

    def test_收盘后全零仍按事实处理(self, wired, monkeypatch):
        """收盘后真出现全 0 是另一回事（多半是数据源问题），不套这条守卫。"""
        from datetime import datetime

        from _contract import CN_TZ
        today = "20260921"
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = pool(k, 0, [], qdate=today)
        monkeypatch.setattr(ec, "now_cn",
                            lambda: datetime(2026, 9, 21, 16, 0, tzinfo=CN_TZ))
        v = build()
        assert not any(m.code == "emotion.pool.not_yet_formed" for m in v.missing)

    def test_历史交易日全零不套这条守卫(self, wired, monkeypatch):
        """查一个过去的交易日，时段早就结束了。"""
        from datetime import datetime

        from _contract import CN_TZ
        monkeypatch.setattr(ec, "now_cn",
                            lambda: datetime(2026, 9, 21, 9, 5, tzinfo=CN_TZ))
        for k in ("limit_up", "broken_board", "limit_down"):
            wired[k] = pool(k, 0, [], qdate="20260918")
        v = build()
        assert not any(m.code == "emotion.pool.not_yet_formed" for m in v.missing)
