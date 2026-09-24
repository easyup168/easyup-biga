"""news-scan —— 第一个「skill 算不出结论」的 Specialist。

它和前五个的形状不同：产出是**事实 + 原文**，判断留给 agent。
所以这里钉的不是「算得对不对」，而是**它有没有在不该说话的地方说话**。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import datetime, timedelta

import pytest

import _contract.evidence as evidence_mod

REPO = pathlib.Path(__file__).resolve().parents[1]

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
    return NewsFeed(items=tuple(items), raw={"pages": []}, raw_text="[]")


def _build(monkeypatch, now: datetime, feed: NewsFeed, **kw):
    monkeypatch.setattr(NS, "now_cn", lambda: now)
    # 🔴 契约层也读时钟（F15 之后 Evidence 会拒绝未来时刻），
    #    这里的「现在」必须两边一致 —— 否则本文件钉在收盘后的那些
    #    回归测试，会在真实时刻早于它们时被契约层拒掉。
    #    假时钟只假一半，比不假更难查。
    monkeypatch.setattr(evidence_mod, "now_cn", lambda: now)
    monkeypatch.setattr(NS, "fetch_feed", lambda **_: feed)
    # 批 E-II：news 迁到产 FactBundle（只事实、无 stance）而非 AgentVerdict。
    return NS.build_fact_bundle(break_source=set(), store=False,
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
        orig = SN.get_json_and_text
        SN.get_json_and_text = lambda *a, **k: (payload, json.dumps(payload))
        try:
            with pytest.raises(SourceError, match="一条都没取到"):
                SN.fetch_feed(pages=1)
        finally:
            SN.get_json_and_text = orig

    def test_窗口内为空要进missing(self, monkeypatch):
        # 数据都在窗口之外
        old = MON_OPEN - timedelta(hours=5)
        feed = NewsFeed(items=(NewsItem(id=1, at=old, text="旧", tags=(),
                                        is_quote=False),), raw={"pages": []},
                        raw_text="[]")
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
        orig = SN.get_json_and_text
        SN.get_json_and_text = lambda *a, **k: (payload, json.dumps(payload))
        try:
            with pytest.raises(SourceError, match="不要当成"):
                SN.fetch_feed(pages=1)
        finally:
            SN.get_json_and_text = orig


# ══ 外部评审 P1-3：连续事件流的时间语义 ═══════════════════════
#
# 原来把最新一条的日期喂给 `as_of_for_trade_date()` —— 那个函数是给**日线**
# 用的：交易日过完了就返回当天 15:00。
#
# 7x24 没有「收盘」这个概念。于是收盘后：
#
#   最新一条 19:58，取回 20:00  ⇒  as_of=15:00  ⇒  staleness 5 小时
#
# 实际只有 2 分钟。这会污染 staleness_sec、risk 的 max_evidence_age_sec、
# 新鲜度判断，以及日后所有数据质量统计。
#
# 🔴 它**盘中是对的** —— 这正是它躲过一整天实测的原因：
#    当天所有测试都在 15:00 之前。⇒ 本类的用例全部钉在**收盘后**。

CLOSED = datetime(2026, 9, 21, 20, 0, tzinfo=CN_TZ)      # 收盘后
LATE = datetime(2026, 9, 21, 23, 50, tzinfo=CN_TZ)       # 深夜


class TestContinuousStreamTemporalSemantics:
    def test_收盘后as_of等于最新一条的时刻(self, monkeypatch):
        newest = CLOSED - timedelta(minutes=2)
        feed = _feed(newest, n=20, step_sec=30)
        v = _build(monkeypatch, CLOSED, feed)
        ev = {e.field: e for e in v.evidence}
        assert ev["newest_at"].as_of == newest, (
            f"as_of={ev['newest_at'].as_of} 应当等于最新一条的时刻 {newest}；"
            "落到 15:00 就是把 2 分钟前的消息说成 5 小时前")

    def test_收盘后不产生假陈旧度(self, monkeypatch):
        newest = CLOSED - timedelta(minutes=2)
        v = _build(monkeypatch, CLOSED, _feed(newest, n=20, step_sec=30))
        ev = {e.field: e for e in v.evidence}
        gap = (ev["newest_at"].retrieved_at - ev["newest_at"].as_of).total_seconds()
        assert gap == 120, f"真实陈旧度 120s，算出来 {gap}s"
        assert gap < 600, "这就是旧实现会给出的 5 小时"

    def test_深夜同理(self, monkeypatch):
        newest = LATE - timedelta(minutes=1)
        v = _build(monkeypatch, LATE, _feed(newest, n=10, step_sec=30))
        ev = {e.field: e for e in v.evidence}
        assert ev["newest_at"].as_of == newest

    def test_盘中也仍然正确(self, monkeypatch):
        """修之前盘中碰巧是对的 —— 修之后不能把它改坏。"""
        newest = MON_OPEN - timedelta(seconds=90)
        v = _build(monkeypatch, MON_OPEN, _feed(newest, n=20, step_sec=30))
        ev = {e.field: e for e in v.evidence}
        assert ev["newest_at"].as_of == newest

    def test_不再依赖日线口径的换算(self):
        """判据是**有没有被调用 / 被 import**，不是「源码里出不出现」。

        🔴 第一版写的是 `"as_of_for_trade_date" not in src` —— 当场红了，
        因为**解释「为什么不能用它」的注释里必然要提到它的名字**。
        （这是本项目第 6 次撞上「描述规则时把 X 抄进去」那个形状，
        只不过这次反过来：判据把合法的解释也禁掉了。）

        ⇒ 用 AST。散文可以提它，代码不能用它。
        """
        import ast
        tree = ast.parse((REPO / "skills/news-scan/scripts/news_scan.py")
                         .read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        bad = {"as_of_for_trade_date"} & (imported | called)
        assert not bad, (
            f"news 仍在用日线的 as_of 换算：{bad} —— "
            "7x24 是连续事件流，没有「收盘」这个概念")


# ══ 共享采集层：被截断的响应不该炸掉 skill ═══════════════════
#
# 实测（2026-09-21 15:14，跑 FIX-04 的验证时撞上的）：
# 新浪 7x24 返回了被截断的 chunked 响应，`news_scan.py` **直接崩溃**，
# 吐出一个裸 traceback。
#
# 根因：`http.client.IncompleteRead` 继承自 `HTTPException` + `ValueError`，
# **不继承 `OSError`** —— 而重试层的 except 只写了
# `(URLError, TimeoutError, OSError)`。
#
# 按本项目的口径，它应该变成一条 `missing`，让卡照常出、只是标着「不知道」。
#
# ⚠️ 重试只此一处 ⇒ 这条影响**全部六个 skill**。


class TestTruncatedResponseBecomesSourceError:
    def test_IncompleteRead_不是OSError(self):
        """先钉死前提 —— 否则下次有人会以为 OSError 够用。"""
        import http.client
        assert not issubclass(http.client.IncompleteRead, OSError)

    def test_被截断的响应变成SourceError(self, monkeypatch):
        import http.client
        import _sources.http as H
        from _sources.http import SourceError

        def boom(*a, **k):
            raise http.client.IncompleteRead(b"half")

        monkeypatch.setattr(H.urllib.request, "urlopen", boom)
        monkeypatch.setattr(H, "BACKOFF_SEC", (0, 0, 0))
        with pytest.raises(SourceError, match="次尝试全部失败"):
            H.get_text("https://x/y", referer="https://x")

    def test_skill层把它变成缺失项而不是崩溃(self, monkeypatch):
        import http.client
        from _sources.http import SourceError

        def boom(**_):
            raise SourceError("7x24: 3 次尝试全部失败，最后一次 IncompleteRead")

        monkeypatch.setattr(NS, "now_cn", lambda: MON_OPEN)
        monkeypatch.setattr(NS, "fetch_feed", boom)
        v = NS.build_fact_bundle(break_source=set(), store=False,
                                 task_id=new_task_id(1))
        assert v.verdict == "UNKNOWN"
        assert "news.feed.unavailable" in [m.code for m in v.missing]
