"""news-scan —— 第一个「skill 算不出结论」的 Specialist。

它和前五个的形状不同：产出是**事实 + 原文**，判断留给 agent。
所以这里钉的不是「算得对不对」，而是**它有没有在不该说话的地方说话**。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import CN_TZ, new_task_id  # noqa: E402
from _sources.sina_news import NewsFeed, NewsItem  # noqa: E402


def _load():
    p = REPO / "skills/news-scan/scripts/news_scan.py"
    spec = importlib.util.spec_from_file_location("news_scan", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NS = _load()


def _feed(now: datetime, *, n: int = 20, step_sec: int = 60,
          quote_every: int = 0, span_extra_min: int = 300) -> NewsFeed:
    """造一个 feed：最新在前，每 `step_sec` 一条。

    `span_extra_min` 让最旧一条远早于窗口起点，避免误触 window.incomplete。
    """
    items = []
    for i in range(n):
        at = now - timedelta(seconds=step_sec * i)
        items.append(NewsItem(id=10_000 - i, at=at, text=f"【测试{i}】正文{i}",
                              tags=("市场",),
                              is_quote=bool(quote_every and i % quote_every == 0)))
    # 补一条很旧的，代表「窗口之前的数据我们也取到了」
    items.append(NewsItem(id=1, at=now - timedelta(minutes=span_extra_min),
                          text="很旧的一条", tags=(), is_quote=False))
    return NewsFeed(items=tuple(items), raw={"pages": []})


def _build(monkeypatch, now: datetime, feed: NewsFeed, **kw):
    monkeypatch.setattr(NS, "now_cn", lambda: now)
    monkeypatch.setattr(NS, "fetch_feed", lambda **_: feed)
    return NS.build_verdict(break_source=set(), store=False,
                            task_id=new_task_id(1), **kw)


MON_OPEN = datetime(2026, 9, 21, 10, 30, tzinfo=CN_TZ)      # 周一盘中
MON_NIGHT = datetime(2026, 9, 21, 23, 0, tzinfo=CN_TZ)      # 周一夜间
SAT = datetime(2026, 9, 26, 10, 30, tzinfo=CN_TZ)           # 周六


class TestStaleGuardOnlyDuringSession:
    """🔴 静默守卫只在连续竞价时段启用。

    非盘中静默是常态：夜间实测间隔可达 1830s，周末只会更长。
    在那里设线，得到的是又一个「周末必红」的灯 —— 本项目已踩过三次这个形状。
    """

    def _stale_codes(self, v):
        return [m.code for m in v.missing if m.code == "news.feed.stale"]

    def test_盘中长时间静默要红(self, monkeypatch):
        # 最新一条在 20 分钟前，超过 STALE_SEC=600
        feed = _feed(MON_OPEN - timedelta(minutes=20), n=5)
        v = _build(monkeypatch, MON_OPEN, feed)
        assert self._stale_codes(v), "盘中静默 20 分钟却没报"

    def test_盘中正常间隔不红(self, monkeypatch):
        """先问「什么时候不该红」：实测盘中最大间隔 235s。"""
        feed = _feed(MON_OPEN - timedelta(seconds=235), n=20)
        v = _build(monkeypatch, MON_OPEN, feed)
        assert not self._stale_codes(v), "实测的正常间隔被判成了故障"

    def test_夜间静默半小时不红(self, monkeypatch):
        """夜间实测最大间隔 1830s —— 在这里报红就是噪音。"""
        feed = _feed(MON_NIGHT - timedelta(minutes=30), n=5)
        v = _build(monkeypatch, MON_NIGHT, feed)
        assert not self._stale_codes(v)

    def test_周六静默不红(self, monkeypatch):
        feed = _feed(SAT - timedelta(hours=2), n=5)
        v = _build(monkeypatch, SAT, feed)
        assert not self._stale_codes(v)

    def test_报错文案要提到休市日(self, monkeypatch):
        """本系统没有交易日历 ⇒ 节假日会误报。

        文案必须自曝这一点，否则读的人会去查一个不存在的故障。
        """
        feed = _feed(MON_OPEN - timedelta(minutes=20), n=5)
        v = _build(monkeypatch, MON_OPEN, feed)
        txt = next(str(m) for m in v.missing if m.code == "news.feed.stale")
        assert "节假日" in txt


class TestTruncationIsMissingNotWarning:
    """读了 16% 却报 PASS —— 第一版真的这么干过。"""

    def test_超上限要进missing(self, monkeypatch):
        feed = _feed(MON_OPEN, n=200, step_sec=10)   # 200 条挤在窗口内
        v = _build(monkeypatch, MON_OPEN, feed, max_items=120)
        assert "news.window.truncated" in [m.code for m in v.missing]
        assert v.verdict != "PASS", "截断了还报 PASS 就是静默 fail-open"

    def test_额度够时不红(self, monkeypatch):
        """实测盘中 60 分钟约 93 条，上限 120 ⇒ 正常不该红。"""
        feed = _feed(MON_OPEN, n=93, step_sec=38)
        v = _build(monkeypatch, MON_OPEN, feed, max_items=120)
        assert "news.window.truncated" not in [m.code for m in v.missing]
        assert v.result["items_truncated"] == 0


class TestWindowCoverage:
    def test_取回的数据没覆盖到窗口起点要红(self, monkeypatch):
        """不报的话 agent 会说「近一小时没有重大消息」，而它只看了 10 分钟。"""
        feed = _feed(MON_OPEN, n=10, step_sec=60, span_extra_min=10)
        v = _build(monkeypatch, MON_OPEN, feed, window_min=60)
        assert "news.window.incomplete" in [m.code for m in v.missing]

    def test_覆盖全了不红(self, monkeypatch):
        feed = _feed(MON_OPEN, n=10, step_sec=60, span_extra_min=300)
        v = _build(monkeypatch, MON_OPEN, feed, window_min=60)
        assert "news.window.incomplete" not in [m.code for m in v.missing]


class TestQuotesAreTaggedNotFiltered:
    """🔴 铁律 4：skill 不做判断性归类。

    机器行情播报里的数字是 market/sector 的事实（裁定 15），
    但**过滤**是判断，而且误伤一条真消息在下游完全看不出来。
    ⇒ 打标，让 agent 决定；同时把「有多少条是播报」变成可见的事实。
    """

    def test_播报仍在items里且带标记(self, monkeypatch):
        feed = _feed(MON_OPEN, n=20, step_sec=30, quote_every=2)
        v = _build(monkeypatch, MON_OPEN, feed)
        assert v.result["quote_count"] == 10
        assert len(v.result["items"]) == 20, "播报被过滤掉了 —— 那是判断性归类"
        assert sum(1 for i in v.result["items"] if i["quote"]) == 10


class TestNewsProducesNoMarketNumbers:
    """裁定 15：news 不得产出任何行情数字。

    快讯文本里全是「上证指数涨0.44%」这类数字，而那是 market 的事实。
    """

    _FORBIDDEN = {"sh_close", "close", "pct", "sh_pct", "turnover", "volume",
                  "limit_up_count", "advance_count", "ma20", "main_inflow"}

    def test_结果字段里没有行情字段(self, monkeypatch):
        v = _build(monkeypatch, MON_OPEN, _feed(MON_OPEN, n=20))
        assert not (self._FORBIDDEN & set(v.result)), \
            f"news 产出了行情字段：{self._FORBIDDEN & set(v.result)}"

    def test_契约里写明了不许提取数字(self):
        p = REPO / "agents/news/AGENTS.md"
        if not p.exists():
            pytest.skip("news agent 尚未建")
        assert "不许" in p.read_text(encoding="utf-8")


class TestEmptyFeedIsFailureNotQuiet:
    """本源实测凌晨 1 点仍有 21 条/小时 ⇒ 0 条只可能是取数失败。

    这与行情源恰好相反（那边 0 常常是「这一天还没开始」）。
    """

    def test_源层空结果直接报错(self):
        from _sources.http import SourceError
        import _sources.sina_news as SN
        payload = {"result": {"status": {"code": 0},
                              "data": {"feed": {"list": []}}}}
        orig = SN.get_json
        SN.get_json = lambda *a, **k: payload
        try:
            with pytest.raises(SourceError, match="一条都没取到"):
                SN.fetch_feed(pages=1)
        finally:
            SN.get_json = orig

    def test_窗口内为空要进missing(self, monkeypatch):
        # 数据都在窗口之外
        old = MON_OPEN - timedelta(hours=5)
        feed = NewsFeed(items=(NewsItem(id=1, at=old, text="旧", tags=(),
                                        is_quote=False),), raw={"pages": []})
        v = _build(monkeypatch, MON_OPEN, feed, window_min=60)
        assert "news.window.empty" in [m.code for m in v.missing]


class TestParseFailureIsNotQuietNews:
    def test_解析失败率高要报错(self):
        """字段改名的表现是「今天新闻少」—— 必须当成故障。"""
        from _sources.http import SourceError
        import _sources.sina_news as SN
        rows = [{"rich_text": "ok", "create_time": "2026-09-21 10:00:00", "id": 1}]
        rows += [{"nope": 1} for _ in range(9)]     # 9/10 解析不出来
        payload = {"result": {"status": {"code": 0},
                              "data": {"feed": {"list": rows}}}}
        orig = SN.get_json
        SN.get_json = lambda *a, **k: payload
        try:
            with pytest.raises(SourceError, match="不要当成"):
                SN.fetch_feed(pages=1)
        finally:
            SN.get_json = orig
