"""证据的两个时间量 —— 取数滞后 vs 年龄（批 R，评审 E-19）。

`Evidence.staleness_sec`（取回时刻 − 数据时刻）曾经是唯一的「新鲜度」数字，
名字读起来像「数据有多旧」，于是 `risk_check` 把它当年龄报给人看，标签写的是
「最旧证据的年龄(秒)」。**两个问题，一个数字，一个错名字。**

这里钉死三件事：
  1. 两者确实是不同的量 —— 一份三天前的数据，取数滞后可以只有 2 秒
  2. 问年龄必须说清「相对哪一刻」（`age_at`），不能偷偷锚在 `now_cn()` 上
  3. 旧名字不能复活
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import AgentVerdict, Evidence, MissingItem, now_cn  # noqa: E402

TASK = "BIGA-20260924-001"


def _ev(*, as_of, retrieved) -> Evidence:
    return Evidence(field="f", source="probe:x", value=1, as_of=as_of,
                    retrieved_at=retrieved, calc_version="v1", raw_hash="a" * 64)


class TestE19两个量不是一回事:
    def test_三天前的数据取数滞后可以只有两秒(self):
        """🔴 本条就是旧名字骗人的全部内容：`staleness_sec` 叫「陈旧度」，
        但一份三天前冻结的快照只要当初抓得快，它就报 2 —— 看起来无比新鲜。"""
        now = now_cn()
        as_of = now - timedelta(days=3)
        e = _ev(as_of=as_of, retrieved=as_of + timedelta(seconds=2))
        assert e.source_lag_sec == 2
        assert e.age_at(now) == pytest.approx(3 * 86400, abs=2)

    def test_取数滞后是差值对共模错位免疫(self):
        """F15：as_of 与 retrieved_at 一起错位 60 秒，取数滞后纹丝不动。"""
        now = now_cn()
        a = _ev(as_of=now - timedelta(hours=2), retrieved=now - timedelta(hours=2) + timedelta(seconds=60))
        b = _ev(as_of=now - timedelta(days=9), retrieved=now - timedelta(days=9) + timedelta(seconds=60))
        assert a.source_lag_sec == b.source_lag_sec == 60
        assert a.age_at(now) != b.age_at(now)   # 年龄分得出来，取数滞后分不出来


class TestE19问年龄必须说清基准:
    def test_age_at按给定时刻算(self):
        as_of = datetime(2026, 9, 20, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        e = _ev(as_of=as_of, retrieved=as_of + timedelta(seconds=5))
        assert e.age_at(as_of + timedelta(seconds=3600)) == 3600

    def test_age_at拒绝naive时刻(self):
        e = _ev(as_of=now_cn() - timedelta(hours=1), retrieved=now_cn())
        with pytest.raises(ValueError):
            e.age_at(datetime(2026, 9, 24, 10, 0))

    def test_age_at拒绝非datetime(self):
        e = _ev(as_of=now_cn() - timedelta(hours=1), retrieved=now_cn())
        with pytest.raises(TypeError):
            e.age_at("2026-09-24T10:00:00+08:00")

    def test_age_sec就是以此刻为基准的age_at(self):
        e = _ev(as_of=now_cn() - timedelta(hours=1), retrieved=now_cn())
        assert e.age_sec == pytest.approx(e.age_at(now_cn()), abs=2)


class TestE19聚合口径:
    def _v(self, evs) -> AgentVerdict:
        return AgentVerdict(task_id=TASK, agent="risk", status="completed", verdict="PASS",
                            result={"f": 1}, data_completeness=1.0, evidence=tuple(evs))

    def test_max_source_lag取最大滞后(self):
        now = now_cn()
        v = self._v([_ev(as_of=now - timedelta(seconds=x + 10), retrieved=now - timedelta(seconds=10))
                     for x in (30, 900)])
        assert v.max_source_lag_sec == 900

    def test_无证据返回负一而不是零(self):
        """0 会被读成「非常新鲜」。算不出来就要看得出来（R-3）。"""
        v = AgentVerdict(task_id=TASK, agent="risk", status="failed", verdict="UNKNOWN",
                         result={}, data_completeness=0.0, evidence=(),
                         missing=(MissingItem("上游一条证据都没有", "risk.upstream.no_evidence"),))
        assert v.max_source_lag_sec == -1
        assert v.max_age_at(now_cn()) == -1

    def test_max_age_at按给定时刻算(self):
        now = now_cn()
        v = self._v([_ev(as_of=now - timedelta(seconds=s), retrieved=now) for s in (60, 7200)])
        assert v.max_age_at(now) == pytest.approx(7200, abs=2)


class TestE19旧名字不许复活:
    def test_Evidence没有staleness_sec(self):
        assert not hasattr(Evidence, "staleness_sec"), (
            "`Evidence.staleness_sec` 回来了 —— 这个名字把「取数滞后」说成「陈旧度」，"
            "正是 E-19 要拆掉的那个坑。要滞后用 source_lag_sec，要年龄用 age_at(基准时刻)。")

    def test_AgentVerdict没有max_staleness_sec(self):
        assert not hasattr(AgentVerdict, "max_staleness_sec")

    def test_news自己那个同名字段不受影响(self):
        """⚠️ `news_scan` 的 result 里有个 `staleness_sec`，意思是「距最新一条」——
        与证据的取数滞后是两回事。改名只动契约层，不许顺手「统一」掉它。"""
        src = (REPO / "skills/news-scan/scripts/news_scan.py").read_text()
        # 判据只看「字段名 + 标签」这对组合还在不在 —— 不钉整行，
        # 否则任何给 add() 加参数的改动都会把它弄红（批 3 加 kind= 时就发生过）。
        assert 'add("staleness_sec", stale, "距最新一条(秒)"' in src
