"""新浪交易日历 —— 解码器、刷新链、以及 `market_is_open` 真的查它。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：压缩序列解码（离线 fixture）、未实现格式的报错、刷新落库、
  兜底层的过期守卫、`market_is_open` 三层的接线
- **不覆盖**：真实抓取（联网）。fixture 是一次真实响应的原样保存

🔴 为什么值得这么多条
---------------------
`fact_trading_calendar` 在此之前是**空表**（原生产方走深交所，本机连不通），
于是 `market_is_open()` 恒走 weekday 回退。2026-09-25（中秋）实测撞到后果：
`news` 按「今天」报交易日 20260925，日线类报 20260924，`risk` 因交易日不一致
弃权 —— 而那天根本不是交易日。

解码器是**读懂一段 17KB 混淆 JS 之后自己重写的**，所以它必须被逐字节钉住。
"""

from __future__ import annotations

import datetime
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from easyup_biga.providers.http import SourceError  # noqa: E402
from easyup_biga.providers.sina_calendar import (  # noqa: E402
    SOURCE, decode_series, parse_datelist, refresh_trading_calendar)
from easyup_biga.providers import tradetime  # noqa: E402
from _store import connect, init_schema, is_trading_day  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "sina-klc-td-sh.txt"

#: fixture 解出来的那份名单的形状。🔴 数字是**实测**，不是估计：
#: 与通行实现（把那段 JS 丢进 JS 引擎跑）的解码结果逐一比对过，完全相同。
#: 那个实现另外手工补了一天 1992-05-04（它自己加的，不在编码里），故差 1 条。
_EXPECT_COUNT = 8796
_EXPECT_FIRST = datetime.date(1990, 12, 19)
_EXPECT_LAST = datetime.date(2026, 12, 31)


@pytest.fixture()
def days():
    return parse_datelist(FIXTURE.read_text(encoding="utf-8"))


class TestDecoder:
    def test_fixture真的被git跟踪(self):
        """没有它，本文件在别人机器上会整片 error 而不是 fail。"""
        assert FIXTURE.exists(), f"{FIXTURE} 不在 —— 解码器就没有离线判据了"

    def test_解出条数与边界(self, days):
        assert len(days) == _EXPECT_COUNT
        assert days[0] == _EXPECT_FIRST
        assert days[-1] == _EXPECT_LAST

    def test_单调递增且无重复(self, days):
        assert days == sorted(days), "解出来的日期不是递增的 —— 游程走错了"
        assert len(set(days)) == len(days)

    def test_没有周末(self, days):
        bad = [d for d in days if d.weekday() >= 5]
        assert not bad, f"名单里混进了周末：{bad[:5]}"

    @pytest.mark.parametrize("day,expect", [
        ("2026-09-24", True),    # 中秋前一天
        ("2026-09-25", False),   # 🔴 中秋 —— 这一天就是整件事的起因
        ("2026-09-26", False),   # 周六
        ("2026-09-28", True),    # 节后第一个交易日
        ("2026-09-30", True),    # 国庆前最后一个交易日
        ("2026-10-01", False),   # 国庆
        ("2026-10-07", False),   # 国庆最后一天
        ("2026-10-08", True),    # 🔴 复市日 —— 手抄版本把它错判成休市
    ])
    def test_关键日期判对(self, days, day, expect):
        assert (datetime.date.fromisoformat(day) in set(days)) is expect

    def test_未实现的格式要报出id(self):
        """这是一族格式，日历只是其中一支。撞到别的支要说清是哪一支。"""
        # 伪造流首：format id = 1479（日 OHLCV），本模块没实现
        bs_head = _encode_head(1479)
        with pytest.raises(SourceError, match="1479"):
            decode_series(bs_head)

    def test_响应形状不对时报错而不是返回空(self):
        with pytest.raises(SourceError):
            parse_datelist("完全不是那个东西")
        with pytest.raises(SourceError, match="空的"):
            parse_datelist('var datelist="";')


def _encode_head(fmt: int) -> str:
    """造一个只有流首（12 bit format id + 6 bit flags）的合法 base64 串。"""
    alphabet = ("".join(chr(65 + i) for i in range(26))
                + "".join(chr(97 + i) for i in range(26))
                + "".join(chr(48 + i) for i in range(10)) + "+/")
    bits = [(fmt >> k) & 1 for k in range(12)] + [0] * 6
    syms = [sum(b << k for k, b in enumerate(bits[i:i + 6]))
            for i in range(0, len(bits), 6)]
    return "".join(alphabet[v] for v in syms)


class TestRefresh:
    """刷新链：抓 → 落 raw → 归一化进 fact_trading_calendar。**注入 fetcher，不联网。**"""

    @pytest.fixture()
    def db(self, tmp_path, monkeypatch):
        p = tmp_path / "biga.db"
        monkeypatch.setenv("BIGA_DB_PATH", str(p))
        init_schema(p)
        return p

    @staticmethod
    def _fetcher():
        raw = FIXTURE.read_text(encoding="utf-8")
        return parse_datelist(raw), raw

    def test_落库之后休市日也在表里(self, db):
        """🔴 只落交易日是不够的。

        日历源只给交易日名单；若只写 `is_open=1` 的行，`is_trading_day()`
        对休市日返回的是 `None`（没覆盖到）而不是 `False`，消费方会回退到
        weekday —— 这次刷新等于白做，而且**看起来是成功的**。
        """
        now = datetime.datetime(2026, 9, 25, 12, 0, tzinfo=tradetime.CN_TZ)
        n, start, end = refresh_trading_calendar(
            back_days=30, fetcher=self._fetcher, now=now, path=db)
        assert n > 0 and end == "20261231"
        assert is_trading_day("20260925", path=db) is False, "中秋没被落成休市日"
        assert is_trading_day("20260924", path=db) is True
        assert is_trading_day("20261008", path=db) is True, "国庆复市日被落错了"
        with connect(db, readonly=True) as c:
            closed = c.execute("SELECT COUNT(*) FROM fact_trading_calendar "
                               "WHERE is_open=0").fetchone()[0]
        assert closed > 0, "一行 is_open=0 都没有 —— 休市日全成了『没覆盖到』"

    def test_raw层留了原始响应(self, db):
        now = datetime.datetime(2026, 9, 25, 12, 0, tzinfo=tradetime.CN_TZ)
        refresh_trading_calendar(back_days=10, fetcher=self._fetcher,
                                 now=now, path=db)
        with connect(db, readonly=True) as c:
            row = c.execute("SELECT source, raw_text FROM raw_market_snapshot "
                            "WHERE source=?", (SOURCE,)).fetchone()
        assert row is not None, "没落 raw —— 这份日历事后无法回放"
        assert row["raw_text"].startswith("var datelist="), "raw 不是原始响应"


class TestFallbackLayer:
    """第 3 层兜底：`market_is_open` 在日历表没覆盖到时问它。"""

    def test_覆盖区间外弃权而不是乐观(self):
        """🔴 硬编码表烂掉时的失败形状就是「不在假期表里 ⇒ 是交易日」。

        过期守卫把它换成 `None`（本层不敢答），交给 weekday 回退。
        """
        beyond = tradetime._COVER_THROUGH + datetime.timedelta(days=1)
        assert tradetime.holiday_fallback(beyond) is None

    @pytest.mark.parametrize("day,expect", [
        ("2026-09-25", False), ("2026-09-28", True),
        ("2026-10-01", False), ("2026-10-08", True),
        ("2026-09-26", False),                       # 周六
    ])
    def test_区间内判对(self, day, expect):
        assert tradetime.holiday_fallback(
            datetime.date.fromisoformat(day)) is expect

    def test_兜底表与权威源一致(self):
        """🔴 手抄必然出错，所以这条把表与**数据**对一遍。

        第一版 `_HOLIDAYS` 是照着另一份同类系统的假期表抄的，把国庆后的复市日
        `20261008` 抄成了休市日。而它发生在写完「硬编码表会烂」那段注释之后
        十分钟内 —— 判据只能是「从数据导出」，不能是「仔细一点」。
        """
        days = set(parse_datelist(FIXTURE.read_text(encoding="utf-8")))
        cover_from = min(datetime.date.fromisoformat("2026-09-25"),
                         tradetime._COVER_THROUGH)
        derived, cur = set(), cover_from
        while cur <= tradetime._COVER_THROUGH:
            if cur.weekday() < 5 and cur not in days:
                derived.add(cur.strftime("%Y%m%d"))
            cur += datetime.timedelta(days=1)
        assert tradetime._HOLIDAYS == derived, (
            "兜底表与权威日历对不上 —— 重新导出："
            "python3 tools/verify/dump_holiday_fallback.py --fixture")

    def test_兜底表到期前会提醒(self):
        """⚠️ **这条会按期变红，那是它的用途。**

        兜底表覆盖到 `_COVER_THROUGH`；交易所通常在 11-12 月发次年日历。
        到期前 45 天变红，把「静默地烂」换成「按期响亮地提醒」。
        """
        left = (tradetime._COVER_THROUGH - datetime.date.today()).days
        assert left > 45, (
            f"交易日历兜底表还有 {left} 天到期（{tradetime._COVER_THROUGH}）。\n"
            "  这不是 bug，是到期提醒。做两件事：\n"
            "    1) python3 tools/verify/dump_holiday_fallback.py\n"
            "    2) 把输出贴进 providers/tradetime.py 的 _COVER_THROUGH/_HOLIDAYS\n"
            "  顺便跑一次 bin/biga-calendar 把 fact_trading_calendar 也刷新到次年。")


class TestWiredIntoMarketIsOpen:
    """接线：三层真的按顺序被问到。"""

    def test_表里说休市就休市(self, tmp_path, monkeypatch):
        p = tmp_path / "biga.db"
        monkeypatch.setenv("BIGA_DB_PATH", str(p))
        init_schema(p)
        now = datetime.datetime(2026, 9, 25, 13, 5, tzinfo=tradetime.CN_TZ)
        refresh_trading_calendar(back_days=30, fetcher=TestRefresh._fetcher,
                                 now=now, path=p)
        assert tradetime.market_is_open(now, path=p) is False, (
            "日历表说 2026-09-25 休市，market_is_open 却说开市")

    def test_表空时靠兜底层也答得对(self, tmp_path, monkeypatch):
        """这是本机的真实处境：刷新还没跑过。"""
        p = tmp_path / "biga.db"
        monkeypatch.setenv("BIGA_DB_PATH", str(p))
        init_schema(p)
        now = datetime.datetime(2026, 9, 25, 13, 5, tzinfo=tradetime.CN_TZ)
        assert is_trading_day("20260925", path=p) is None, "表应当是空的"
        assert tradetime.market_is_open(now, path=p) is False, (
            "表空 + 兜底表认识这天 ⇒ 必须答休市，不能退到 weekday 说开市")

    def test_三层都不认时仍回退weekday(self, tmp_path, monkeypatch):
        """R-3 的安全方向不许被这次改动改掉：查不到 ⇒ 当作可能开市。"""
        p = tmp_path / "biga.db"
        monkeypatch.setenv("BIGA_DB_PATH", str(p))
        init_schema(p)
        beyond = tradetime._COVER_THROUGH + datetime.timedelta(days=30)
        while beyond.weekday() >= 5:
            beyond += datetime.timedelta(days=1)
        now = datetime.datetime(beyond.year, beyond.month, beyond.day,
                                13, 5, tzinfo=tradetime.CN_TZ)
        assert tradetime.market_is_open(now, path=p) is True, (
            "三层都不认、又是工作日 ⇒ 必须当作可能开市（宁可多报 missing）")
