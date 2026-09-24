"""深交所交易日历 Provider（批 L）—— 解析 / 落库链 / 日历感知 market_is_open。

🔴 **不许联网**（conftest 的 socket 围栏兜底）。深交所响应一律用合成 fixture，
`refresh_trading_calendar` 一律注入桩 fetcher —— 联网那层（`fetch_trading_calendar`）
本身不在离线测试里跑（沿用既有禁网围栏，与 sina/eastmoney 同）。

探针对照：
* P1 —— TestParse：喂固定响应，断言「是否开市」解析正确；含全套 fail-closed。
* P2 —— TestRawPersisted：落盘 raw_text 与响应体逐字节相同、content_sha256 一致。
* P4 —— TestMarketOpenWithCalendar：有日历数据时 market_is_open 给正确答案。
* P5 —— TestFailDirectionUnchanged：无数据/超范围时回退，与改之前逐一相同。
* P6 —— TestNoCredentialDependency：选的是免鉴权源，套件不引入任何真实凭据。
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest


from _contract import CN_TZ  # noqa: E402
from _sources import szse  # noqa: E402
from _sources.szse import (  # noqa: E402
    CalendarDay,
    TradingCalendar,
    parse_trading_calendar,
    refresh_trading_calendar,
)
from _sources.tradetime import market_is_open  # noqa: E402
from _store import (  # noqa: E402
    connect,
    init_schema,
    is_trading_day,
    load_raw_snapshot,
    raw_text_sha256,
    save_trading_calendar,
)
from _sources.http import SourceError  # noqa: E402


# ── fixture 构造器：一份**形状合法**的深交所 monthList 响应 ──────────────────
def szse_month_payload(year: int, month: int, holidays: frozenset[str] = frozenset()):
    """按深交所 monthList 的字段形状（jyrq/jybz/zrxh）合成一整个自然月。

    工作日默认开市；周末与 `holidays`（``YYYYMMDD`` 集合）休市。用来喂解析器 ——
    真实交易所日历长这样，但内容由测试掌控，断言才可复现。
    """
    days = []
    d = _dt.date(year, month, 1)
    while d.month == month:
        ymd = d.strftime("%Y%m%d")
        is_open = d.weekday() < 5 and ymd not in holidays
        days.append({"jyrq": d.isoformat(), "zrxh": str(d.weekday() + 1),
                     "jybz": "1" if is_open else "0"})
        d += _dt.timedelta(days=1)
    return {"data": days}


@pytest.fixture
def db(tmp_path):
    """一个迁到最新版（含 v15 fact_trading_calendar）的一次性库。"""
    p = tmp_path / "t.db"
    init_schema(p)
    return p


def _dt_cn(y, m, d, hh, mm=0):
    return _dt.datetime(y, m, d, hh, mm, tzinfo=CN_TZ)


# ═══════════════════════════ P1 · 解析（纯函数，离线）═══════════════════════════
class TestParse:
    def test_喂固定响应_解析出是否开市(self):
        # 2026-01：元旦 01-01(四)/01-02(五) 休市。喂**文本**（json.loads 后）解析。
        payload = json.loads(json.dumps(
            szse_month_payload(2026, 1, frozenset({"20260101", "20260102"}))))
        cal = parse_trading_calendar(payload, year=2026, month=1)
        by = {d.date: d.is_open for d in cal.days}
        assert by["20260101"] is False, "元旦（周四）必须判成休市"
        assert by["20260102"] is False, "元旦假期第二天休市"
        assert by["20260105"] is True,  "01-05 周一，交易日"
        assert by["20260103"] is False, "01-03 周六，休市"
        assert by["20260104"] is False, "01-04 周日，休市"
        # open_days 只含交易日，升序
        assert "20260101" not in cal.open_days
        assert "20260105" in cal.open_days
        assert list(cal.open_days) == sorted(cal.open_days)

    def test_覆盖整月每一天(self):
        cal = parse_trading_calendar(szse_month_payload(2026, 2), year=2026, month=2)
        assert len(cal.days) == 28, "2026-02 有 28 天，每天一条"

    def test_接受单元素数组顶层(self):
        # 深交所有的报表端点顶层是 [{...}] —— 也要能解。
        payload = [szse_month_payload(2026, 3)]
        cal = parse_trading_calendar(payload, year=2026, month=3)
        assert len(cal.days) == 31

    def test_日期分隔符归一化(self):
        payload = {"data": [{"jyrq": "2026/09/01", "jybz": "1"}]}
        # 只有一天 ⇒ 整月不完整 ⇒ 抛错，但错在「不完整」不在「解析日期」——
        # 用一个只校验 _normalize_date 的窄断言：
        assert szse._normalize_date("2026/09/01") == "20260901"
        assert szse._normalize_date("2026-09-01") == "20260901"
        assert szse._normalize_date("20260901") == "20260901"

    # ── fail-closed（R-3）：宁可整月拒绝，也不给半份 ──
    def test_空data抛错(self):
        with pytest.raises(SourceError, match="未返回 data"):
            parse_trading_calendar({"data": []}, year=2026, month=9)

    def test_缺日抛错(self):
        p = szse_month_payload(2026, 9)
        p["data"].pop()  # 删掉一天 ⇒ 该月不完整
        with pytest.raises(SourceError, match="不完整|错位"):
            parse_trading_calendar(p, year=2026, month=9)

    def test_多出别的月份的日期抛错(self):
        p = szse_month_payload(2026, 9)
        p["data"].append({"jyrq": "2026-10-01", "jybz": "0"})
        with pytest.raises(SourceError, match="不完整|错位"):
            parse_trading_calendar(p, year=2026, month=9)

    def test_jybz非0或1抛错(self):
        p = szse_month_payload(2026, 9)
        p["data"][0]["jybz"] = "2"
        with pytest.raises(SourceError, match="字段异常"):
            parse_trading_calendar(p, year=2026, month=9)

    def test_重复日期抛错(self):
        p = szse_month_payload(2026, 9)
        p["data"].append(dict(p["data"][0]))  # 复制第一天 ⇒ 重复
        with pytest.raises(SourceError, match="重复日期|不完整"):
            parse_trading_calendar(p, year=2026, month=9)

    def test_顶层形状不对抛错(self):
        with pytest.raises(SourceError):
            parse_trading_calendar("not json obj", year=2026, month=9)


# ═══════════════════════════ P2 · raw 层真的存 raw ═══════════════════════════
class TestRawPersisted:
    def test_落盘raw_text逐字节相同且sha一致(self, db):
        payload = szse_month_payload(2026, 9, frozenset())
        raw_text = json.dumps(payload, ensure_ascii=False)  # 「数据源发来的原文」
        # 桩 fetcher：不出网，交出一份带已知 raw_text 的日历。
        stub = lambda y, m: TradingCalendar(  # noqa: E731
            year=y, month=m,
            days=tuple(CalendarDay(d["jyrq"].replace("-", ""), d["jybz"] == "1")
                       for d in payload["data"]),
            raw=payload, raw_text=raw_text)

        refresh_trading_calendar(2026, 9, fetcher=stub, path=db)

        row = load_raw_snapshot(1, path=db)  # 一次性库里第一行就是它
        assert row is not None
        assert row["raw_text"] == raw_text, "落盘的 raw_text 必须与响应体逐字节相同"
        assert row["content_sha256"] == raw_text_sha256(raw_text), \
            "content_sha256 必须基于 raw_text 算（批 I 的判法，不是第二套）"
        assert row["source"] == "szse:calendar/2026-09"

    def test_fact表写入并溯源到raw(self, db):
        payload = szse_month_payload(2026, 9, frozenset({"20260901"}))
        raw_text = json.dumps(payload)
        stub = lambda y, m: TradingCalendar(  # noqa: E731
            year=y, month=m,
            days=tuple(CalendarDay(d["jyrq"].replace("-", ""), d["jybz"] == "1")
                       for d in payload["data"]),
            raw=payload, raw_text=raw_text)
        refresh_trading_calendar(2026, 9, fetcher=stub, path=db)

        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM fact_trading_calendar").fetchone()[0]
            snap = [r[0] for r in c.execute(
                "SELECT DISTINCT snapshot_id FROM fact_trading_calendar").fetchall()]
        assert n == 30, "2026-09 有 30 天，每天一条 fact 行"
        assert snap == [1], "每行都溯源到那一份 raw（snapshot_id=1）"
        # 被标成休市的 09-01 确实 is_open=0
        assert is_trading_day("20260901", path=db) is False


# ═══════════════ P4 · 有日历数据时 market_is_open 给正确答案 ═══════════════
class TestMarketOpenWithCalendar:
    @pytest.fixture
    def seeded(self, db):
        """把 2026-01（元旦休市）灌进 fact 表。"""
        payload = szse_month_payload(2026, 1, frozenset({"20260101", "20260102"}))
        save_trading_calendar(
            source="szse:calendar/2026-01", as_of="t", retrieved_at="t",
            days=[(d["jyrq"].replace("-", ""), d["jybz"] == "1")
                  for d in payload["data"]],
            path=db)
        return db

    def test_元旦被判成休市(self, seeded):
        # 缺陷案例：09:31 落在竞价时段内，但今天是元旦 ⇒ 应为 False（修好了）
        assert market_is_open(_dt_cn(2026, 1, 1, 9, 31), path=seeded) is False

    def test_普通交易日盘中仍开市(self, seeded):
        assert market_is_open(_dt_cn(2026, 1, 5, 10, 0), path=seeded) is True   # 周一

    def test_交易日盘前盘后不算开市(self, seeded):
        assert market_is_open(_dt_cn(2026, 1, 5, 8, 0), path=seeded) is False
        assert market_is_open(_dt_cn(2026, 1, 5, 15, 30), path=seeded) is False

    def test_日历里的周末休市(self, seeded):
        assert market_is_open(_dt_cn(2026, 1, 3, 10, 0), path=seeded) is False  # 周六


# ═══════════ P5 · 无数据 / 超范围时回退，与「改之前」逐一相同 ═══════════
class TestFailDirectionUnchanged:
    def test_空表回退到weekday(self, db):
        # 库里没有任何日历行 ⇒ 元旦按 weekday 判据 = 开市（与批 L 之前相同）
        assert market_is_open(_dt_cn(2026, 1, 1, 9, 31), path=db) is True
        # 周末仍然 False
        assert market_is_open(_dt_cn(2026, 1, 3, 10, 0), path=db) is False

    def test_超出已抓范围回退(self, db):
        # 只灌 2026-01，问 2026-03 的某个工作日 ⇒ 查不到 ⇒ 回退 weekday
        payload = szse_month_payload(2026, 1, frozenset({"20260101"}))
        save_trading_calendar(
            source="szse:calendar/2026-01", as_of="t", retrieved_at="t",
            days=[(d["jyrq"].replace("-", ""), d["jybz"] == "1")
                  for d in payload["data"]],
            path=db)
        # 2026-03-02 周一，日历没覆盖 ⇒ None ⇒ weekday 回退 ⇒ 盘中开市
        assert is_trading_day("20260302", path=db) is None
        assert market_is_open(_dt_cn(2026, 3, 2, 10, 0), path=db) is True

    def test_库不存在时is_trading_day返回None不抛错(self, tmp_path):
        # 全新 clone 里日历库本就不存在 ⇒ 视作「没覆盖到」，不崩
        assert is_trading_day("20260101", path=tmp_path / "nope.db") is None

    def test_取最新一条_按retrieved_at(self, db):
        # 交易所补发调整：同一天先写「开市」，再写「休市」（更晚 retrieved_at）
        save_trading_calendar(source="s", as_of="t1", retrieved_at="2026-01-01T08:00",
                              days=[("20260115", True)], path=db)
        save_trading_calendar(source="s", as_of="t2", retrieved_at="2026-01-01T09:00",
                              days=[("20260115", False)], path=db)
        assert is_trading_day("20260115", path=db) is False, "应取更晚 retrieved_at 的那条"


# ═══════════════════ P6 · 选型站得住：免鉴权源，无凭据依赖 ═══════════════════
class TestNoCredentialDependency:
    def test_适配器源码不含任何凭据(self):
        src = (Path(__file__).resolve().parent.parent
               / "skills" / "_sources" / "szse.py").read_text()
        low = src.lower()
        for bad in ("token", "api_key", "apikey", "app_id", "appid",
                    "app_secret", "appsecret", "secret", "password", "authorization"):
            assert bad not in low, f"深交所日历是免鉴权源，源码不该出现 {bad!r}"

    def test_测试套件不需要任何真实凭据(self):
        # 本文件既不读环境里的 token，也不联网 —— 全离线、纯 fixture。
        import os
        assert not any("SZSE" in k or "TUSHARE" in k for k in os.environ), \
            "选的是免鉴权源，测试不该依赖任何 SZSE_/TUSHARE_ 凭据环境变量"
