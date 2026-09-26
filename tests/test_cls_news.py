"""财联社电报 —— `cn.news.flash` 的 PRIMARY。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：签名算法、`rn` 上限那条硬边界、「空 = 失败」的判据、`level` 透传、
  广告剔除、游标推进
- **不覆盖**：降级接线（在 `test_news_scan.py` / decision_client 的测试里）、
  静默阈值（**现在没有阈值**，见 `cls_news` 模块头）
"""
from __future__ import annotations

import hashlib
import json

import pytest

from easyup_biga.providers import cls_news
from easyup_biga.providers.http import SourceError


def _row(rid: int, ctime: int, *, text="财联社电报正文", level="C", **kw):
    return {"id": rid, "ctime": ctime, "content": text, "level": level,
            "is_ad": 0, "is_fad": 0, "subjects": [{"subject_name": "环球市场情报"}],
            **kw}


def _stub(monkeypatch, pages):
    """桩掉**最外层网络边界**（`get_json_and_text`），签名与翻页逻辑照跑。

    🔴 不桩 `_page` —— 那会把签名拼接、errno 判断一并跳过，
    而那两处正是这个源最容易出错的地方（L-12：只桩最外层）。
    """
    calls = []

    def fake(url, *, referer):
        calls.append(url)
        payload = pages[min(len(calls) - 1, len(pages) - 1)]
        return payload, json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(cls_news, "get_json_and_text", fake)
    return calls


class TestSign:
    def test_签名是md5套sha1的字典序query(self):
        p = {"b": "2", "a": "1"}
        expect = hashlib.md5(
            hashlib.sha1(b"a=1&b=2").hexdigest().encode()).hexdigest()
        assert cls_news._sign(p) == expect

    def test_签名串与发出去的query逐字一致(self, monkeypatch):
        """🔴 两边任何一处不同，服务端算出来就是另一个签名。

        表现是 `errno` 非 0，**不是 4xx** —— 从状态码看不出来。
        """
        calls = _stub(monkeypatch, [{"errno": 0, "data": {
            "roll_data": [_row(1, 1790000000)]}}])
        cls_news.fetch_feed(pages=1, page_size=50)
        qs, sign = calls[0].split("?", 1)[1].rsplit("&sign=", 1)
        assert sign == hashlib.md5(
            hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


class TestPageSizeHardLimit:
    """🔴 `rn > 50` 返回空列表且 `errno=0` —— 源说「成功」然后什么都不给。

    实测交替三轮完全一致：`rn=50 → 50 条`，`rn=51/55/99 → 0 条`。
    照着「多要一点总没坏处」调到 100，得到的是一个**永远说今天没新闻**的
    provider，而且不报错。
    """

    @pytest.mark.parametrize("bad", [0, -1, 51, 100, 500])
    def test_越界在发请求之前就拦下(self, monkeypatch, bad):
        calls = _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": []}}])
        with pytest.raises(ValueError, match="page_size"):
            cls_news.fetch_feed(page_size=bad)
        assert calls == [], "越界的请求根本不该发出去"

    def test_上限本身可用(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {
            "roll_data": [_row(1, 1790000000)]}}])
        assert len(cls_news.fetch_feed(page_size=cls_news.MAX_PAGE_SIZE).items) == 1

    def test_默认值不超过上限(self):
        assert cls_news.PAGE_SIZE <= cls_news.MAX_PAGE_SIZE


class TestEmptyIsFailureNotNoNews:
    """这个源的「0 条」只可能是我们错了或它坏了。

    财联社是 7×24 商业电报服务，非交易日实测仍有 8.6 条/小时。
    """

    def test_首页空要抛而不是返回空feed(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": []}}])
        with pytest.raises(SourceError, match="不是「今天没新闻」"):
            cls_news.fetch_feed()

    def test_errno非零要冒泡(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 5, "msg": "sign error", "data": {}}])
        with pytest.raises(SourceError, match="errno=5"):
            cls_news.fetch_feed()

    def test_roll_data不是数组要抛(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": "oops"}}])
        with pytest.raises(SourceError, match="报文形状变了"):
            cls_news.fetch_feed()

    def test_解析失败率高要抛而不是当成新闻少(self, monkeypatch):
        """🔴 字段改名的表现是「条数偏少」，看不出来。"""
        rows = [_row(i, 1790000000 + i) for i in range(2)]
        rows += [{"id": i, "nope": 1} for i in range(10, 18)]   # 8/10 解析不出
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": rows}}])
        with pytest.raises(SourceError, match="不要当成「新闻少」"):
            cls_news.fetch_feed()


class TestParsing:
    def test_level透传且大写(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": [
            _row(1, 1790000000, level="b")]}}])
        assert cls_news.fetch_feed().items[0].level == "B"

    def test_level缺失是None不是空串(self, monkeypatch):
        """🔴 `None` = 这个源没说；空串会被下游当成一个**存在的**档位。"""
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": [
            _row(1, 1790000000, level="")]}}])
        assert cls_news.fetch_feed().items[0].level is None

    def test_广告被剔除(self, monkeypatch):
        rows = [_row(1, 1790000000), _row(2, 1790000001, is_ad=1),
                _row(3, 1790000002, is_fad=1)]
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": rows}}])
        assert len(cls_news.fetch_feed().items) == 1

    def test_广告占多数也不算形状变了(self, monkeypatch):
        """🔴 我第一版把广告剔除算进了解析失败率，于是 3 行里 2 条广告
        就会报「字段可能改名了」—— 把一个**正常**页面判成故障。

        > 一个计数器量两件事 = 两件事都测不准。

        广告被剔除是源自己声明的，解析不出来是形状变了。分开算。
        """
        rows = [_row(1, 1790000000)] + [
            _row(10 + i, 1790000001 + i, is_ad=1) for i in range(9)]
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": rows}}])
        assert len(cls_news.fetch_feed().items) == 1

    def test_整页都是广告不当成解析失败(self, monkeypatch):
        """全是广告 ⇒ 内容行 0 条 ⇒ 不该报形状错，该报「一条都没解析出来」。"""
        rows = [_row(i, 1790000000 + i, is_ad=1) for i in range(5)]
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": rows}}])
        with pytest.raises(SourceError, match="一条都没解析出来"):
            cls_news.fetch_feed()

    def test_subjects进tags(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": [
            _row(1, 1790000000)]}}])
        assert cls_news.fetch_feed().items[0].tags == ("环球市场情报",)

    def test_按时间降序(self, monkeypatch):
        rows = [_row(1, 1790000000), _row(2, 1790000600), _row(3, 1790000300)]
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": rows}}])
        ats = [i.at for i in cls_news.fetch_feed().items]
        assert ats == sorted(ats, reverse=True)

    def test_ctime按北京时间解(self, monkeypatch):
        _stub(monkeypatch, [{"errno": 0, "data": {"roll_data": [
            _row(1, 1790000000)]}}])
        at = cls_news.fetch_feed().items[0].at
        assert at.utcoffset().total_seconds() == 8 * 3600


class TestPaging:
    def test_游标取原始末条而不是解析后末条(self, monkeypatch):
        """🔴 末条若是广告会被丢掉，拿解析后的末条当游标会**漏掉它之后那一段**。"""
        p1 = {"errno": 0, "data": {"roll_data": (
            [_row(i, 1790000000 - i) for i in range(9)]
            + [_row(99, 1789999000, is_ad=1)])}}
        p2 = {"errno": 0, "data": {"roll_data": [
            _row(200 + i, 1789990000 - i) for i in range(9)]}}
        calls = _stub(monkeypatch, [p1, p2])
        cls_news.fetch_feed(pages=2)
        assert "last_time=1789999000" in calls[1], calls[1]

    def test_游标没推进就停下(self, monkeypatch):
        """同一页重复返回时必须跳出，否则死循环。"""
        same = {"errno": 0, "data": {"roll_data": [_row(1, 1790000000)]}}
        calls = _stub(monkeypatch, [same])
        assert len(cls_news.fetch_feed(pages=5).items) == 1
        assert len(calls) == 2, "第二页发现没有新 id 就该停"


def test_静默阈值是None而不是抄新浪那个600():
    """🔴 手上只有非交易日样本（2026-09-25 中秋 / 09-26 周六）。

    同窗口速率差 4 倍（新浪 34 条/小时、财联社 8.6 条/小时），
    抄 600 大概率得到一盏**正常也红**的灯 —— 而几次之后所有人都开始忽略它。

    ⇒ R-3：算不出来就说算不出来。等 2026-09-28 实测再定。
    """
    assert cls_news.STALE_SEC is None


# ──────────────────────────────────────────────────────────────────────
# 降级接线 —— 判据打在**冻结之后库里有什么**，不在「代码里写没写 fallback」
# ──────────────────────────────────────────────────────────────────────

def _feed(level: str | None):
    from datetime import datetime

    from easyup_biga.domain import CN_TZ
    from easyup_biga.providers.news_item import NewsFeed, NewsItem
    at = datetime(2026, 9, 26, 14, 30, tzinfo=CN_TZ)
    return NewsFeed(
        items=(NewsItem(id=1, at=at, text="测试快讯", tags=("市场",),
                        is_quote=False, level=level),),
        raw={"pages": []}, raw_text='["{}"]')


def _client(tmp_path, monkeypatch, *, cls_ok: bool, sina_ok: bool = True):
    import easyup_biga.data.decision_client as dc
    from easyup_biga.persistence import init_schema, save_evidence_set

    pytest.importorskip("pyarrow")
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "biga.db"
    init_schema(db)
    save_evidence_set(evidence_set_id="es-n", decision_id=None,
                      manifest={"kind": "t"}, path=db)

    def boom(msg):
        return lambda **_: (_ for _ in ()).throw(SourceError(msg))

    monkeypatch.setattr(dc, "_cls_fetch_feed",
                        (lambda **_: _feed("B")) if cls_ok else boom("财联社 502"))
    monkeypatch.setattr(dc, "_fetch_feed",
                        (lambda **_: _feed(None)) if sina_ok else boom("新浪 502"))
    return dc.DecisionDataClient(db_path=db, data_root="data"), db


def test_正常时由财联社供数并带出level(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, cls_ok=True)
    assert client.freeze_required(
        "es-n", ["cn.news.flash"], trade_date="20260926").complete
    feed, _h, served = client.read_news("es-n")
    assert served == "cls_news"
    assert feed.items[0].level == "B"
    assert feed.important_count == 1


def test_主源挂了由新浪供数且溯源写实际供数方(tmp_path, monkeypatch):
    from easyup_biga.persistence import connect
    client, db = _client(tmp_path, monkeypatch, cls_ok=False)
    assert client.freeze_required(
        "es-n", ["cn.news.flash"], trade_date="20260926").complete

    with connect(db, readonly=True) as conn:
        provider = conn.execute(
            "SELECT provider_id FROM dataset_partitions "
            "WHERE dataset_id='cn.news.flash'").fetchone()["provider_id"]
        attempts = [(str(r["provider_id"]), str(r["provider_role"]), str(r["status"]))
                    for r in conn.execute(
                        "SELECT provider_id,provider_role,status FROM provider_attempts "
                        "ORDER BY attempt_no")]
    assert provider == "sina_news"
    assert attempts == [("cls_news", "PRIMARY", "FAILED_RETRYABLE"),
                        ("sina_news", "FALLBACK", "SUCCEEDED")]


def test_降级到新浪时level是None而不是被补成默认档(tmp_path, monkeypatch):
    """🔴 「不重要」和「这个源没说」是两件事。

    补一个默认档（哪怕是 "C"）会让降级过的那次看起来完全正常 ——
    而那一次恰恰最需要人看清楚自己少了什么（R-3）。
    """
    client, _ = _client(tmp_path, monkeypatch, cls_ok=False)
    client.freeze_required("es-n", ["cn.news.flash"], trade_date="20260926")
    feed, _h, served = client.read_news("es-n")
    assert served == "sina_news"
    assert feed.items[0].level is None
    assert feed.important_count is None, "不许退化成 0"


def test_两源都挂是可降级失败而不是抛穿(tmp_path, monkeypatch):
    """🔴 降级链的终点必须仍然是**可降级的失败**。

    抛穿会让整张卡崩掉 —— 而 news 缺一条，卡本该照出、只是进 `missing[]`。
    """
    client, _ = _client(tmp_path, monkeypatch, cls_ok=False, sina_ok=False)
    result = client.freeze_required("es-n", ["cn.news.flash"], trade_date="20260926")
    assert not result.complete
    assert "cn.news.flash" in result.errors
