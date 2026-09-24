"""批 I 探针 P1–P6：让 raw 层真的存 raw。

改之前，raw 层的链路是 `get_json()` → `json.loads` → `json.dumps(sort_keys=True)`
落盘，`content_sha256` 因此是**我们自己重排后**的指纹，证明不了数据源发来的字节。
这一批加了 `raw_market_snapshot.raw_text`（原始响应文本）并让 `content_sha256` 基于
它算。下面每个 Test 类对应设计里的一道探针。

覆盖：
  P1 原始性 —— 原文走完整链路后逐字节不变
  P2 哈希正确 —— content_sha256 == sha256(原文)，且不同序列化 → 不同哈希
  P3 消费方不被破坏 —— load 回来的 payload 仍是解析后对象（coordinator 的 len/切片）
  P4 漏传即报错 —— save 强制 raw_text 非空
  P5 只追加 —— 加列后 raw 层仍拒绝 UPDATE/DELETE
  P6 注释真实 —— raw_text 列逐字节等于原始响应体（「不做任何归一化」为真）

不覆盖：`get_text` 的编码假设是否正确（字节级原始性）—— 批 I 明确排除的独立问题。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

import _sources.http as http  # noqa: E402
import _sources.sina as sina  # noqa: E402
import _sources.sina_news as sina_news  # noqa: E402
from _store import (  # noqa: E402
    AppendOnlyViolation,
    connect,
    init_schema,
    load_raw_snapshot,
    payload_sha256,
    raw_text_sha256,
    save_raw_snapshot,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


# 一段带**乱序 key + 多余空白**的合法新浪日线响应体。乱序/空白是关键：旧口径把它
# json.loads 再 json.dumps(sort_keys=True)，指纹会把这些差异统统抹平。
_BODY = (
    '[\n'
    '  {"volume": "1000", "close": "10.50",  "day": "2026-09-18",\n'
    '   "open": "9.00", "high": "11.00", "low": "8.50"} ,\n'
    '  {"low": "9.0", "day": "2026-09-19", "open": "10.5",\n'
    '   "close": "11.0", "high": "12.0", "volume": "2000"}\n'
    ']'
)


def _kw(**over):
    """save_raw_snapshot 的公共 kwargs（探针只关心 raw_text/payload/哈希）。"""
    base = dict(source="sina:kline/sh000001",
                as_of="2026-09-18T15:00:00+08:00",
                retrieved_at="2026-09-18T15:00:03+08:00")
    base.update(over)
    return base


# ─────────────────────────────────────────────── P1 · 原始性（逐字节）


class TestP1Rawness:
    def test_原文走完整链路后逐字节不变(self, db, monkeypatch):
        """桩掉最底层的网络原语 `get_text`，让 get_json_and_text / 适配器 / 落库 /
        回读全走真的实现 —— 断言存下来的原文与最初的响应体**逐字节**相同。"""
        monkeypatch.setattr(http, "get_text", lambda url, **kw: _BODY)
        d = sina.fetch_index_daily("sh000001", bars=2)
        assert d.raw_text == _BODY, "适配器没把原文一路带上来"
        sid = save_raw_snapshot(**_kw(payload=d.raw, raw_text=d.raw_text), path=db)
        got = load_raw_snapshot(sid, path=db)
        assert got["raw_text"] == _BODY  # 🔴 逐字节，不是「看起来一样」


# ─────────────────────────────────────────────── P2 · 哈希基于原文


class TestP2HashIsTextBased:
    def test_content_sha256等于手算的文本sha256(self, db):
        sid = save_raw_snapshot(**_kw(payload=json.loads(_BODY), raw_text=_BODY), path=db)
        got = load_raw_snapshot(sid, path=db)
        assert got["content_sha256"] == hashlib.sha256(_BODY.encode("utf-8")).hexdigest()

    def test_同数据不同序列化产出不同哈希(self, db):
        """要修的问题的反面验证：数据相同、只是键序不同的两次响应，改之前会被判成
        「一样」（payload_sha256 抹平了键序），改之后能分辨。"""
        body_a = '{"a": 1, "b": 2}'
        body_b = '{"b": 2, "a": 1}'          # 同数据、键序不同
        assert json.loads(body_a) == json.loads(body_b)   # 数据确实相同
        sa = save_raw_snapshot(**_kw(source="t:a", payload=json.loads(body_a),
                                     raw_text=body_a), path=db)
        sb = save_raw_snapshot(**_kw(source="t:b", payload=json.loads(body_b),
                                     raw_text=body_b), path=db)
        ha = load_raw_snapshot(sa, path=db)["content_sha256"]
        hb = load_raw_snapshot(sb, path=db)["content_sha256"]
        assert ha != hb, "新口径必须能分辨两次不同序列化"
        # 反面：旧口径（payload_sha256）会把这两个判成一样 —— 正是批 I 要修的
        assert payload_sha256(json.loads(body_a)) == payload_sha256(json.loads(body_b))


# ─────────────────────────────────── P3 · 消费方（coordinator）不被破坏


class TestP3PayloadStaysParsed:
    def test_load回来的payload仍是解析后对象(self, db):
        """🔴 coordinator.read_index_daily 对 payload 做 len()/切片。加了原始文本列
        之后，load 返回的 payload 必须还是解析后的对象，不能变成 str。"""
        sid = save_raw_snapshot(**_kw(payload=json.loads(_BODY), raw_text=_BODY), path=db)
        got = load_raw_snapshot(sid, path=db)
        assert isinstance(got["payload"], list)          # 解析后对象
        assert not isinstance(got["payload"], str)       # 不是字符串
        assert len(got["payload"]) == 2                  # len()/切片用法成立
        assert got["raw_text"] == _BODY                  # 原文另存一列，两者并存


# ─────────────────────────────────────────────── P4 · 漏传 raw_text 即报错


class TestP4SaveRequiresRawText:
    @pytest.mark.parametrize("bad", [None, ""])
    def test_raw_text为None或空被拒(self, db, bad):
        """一个 collector 漏传（None / 空串）必须当场报错，不能静默往新列塞空值。"""
        with pytest.raises(ValueError, match="raw_text"):
            save_raw_snapshot(**_kw(payload={"k": 1}, raw_text=bad), path=db)

    def test_漏传raw_text直接TypeError(self, db):
        """raw_text 是必填关键字（无默认）—— 漏改的 collector 连调用都构不成，
        不可能静默走到「往新列存了个空值」。"""
        with pytest.raises(TypeError):
            save_raw_snapshot(**_kw(payload={"k": 1}), path=db)


# ─────────────────────────────────────────────── P5 · 加列后仍只追加


class TestP5StillAppendOnly:
    def test_加raw_text列后仍拒绝UPDATE和DELETE(self, db):
        """ADD COLUMN 不该绕开 raw_market_snapshot 已有的只追加触发器 —— 连**新列
        本身**的 UPDATE 也要被拒。"""
        sid = save_raw_snapshot(**_kw(payload={"k": 1}, raw_text="x"), path=db)
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("UPDATE raw_market_snapshot SET raw_text='y' "
                          "WHERE snapshot_id=?", (sid,))
        with pytest.raises(AppendOnlyViolation, match="只追加"):
            with connect(db) as c:
                c.execute("DELETE FROM raw_market_snapshot WHERE snapshot_id=?", (sid,))


# ─────────────────────────────────── P6 · 建表注释真实性（逐字节）


class TestP6TableCommentIsTrue:
    def test_raw_text列逐字节等于原始响应体(self, db, monkeypatch):
        """直接读 raw_text 列（绕过 load 的 json.loads），比对构造的原始响应体 ——
        用一条真实存下的行证明「raw_text 不做任何归一化」这句建表注释现在是真话。"""
        monkeypatch.setattr(http, "get_text", lambda url, **kw: _BODY)
        d = sina.fetch_index_daily("sh000001", bars=2)
        sid = save_raw_snapshot(**_kw(payload=d.raw, raw_text=d.raw_text), path=db)
        with connect(db, readonly=True) as c:
            stored = c.execute(
                "SELECT raw_text FROM raw_market_snapshot WHERE snapshot_id=?",
                (sid,)).fetchone()[0]
        assert stored == _BODY
        # 与 content_sha256 的口径也吻合（都基于这段原文）
        assert raw_text_sha256(stored) == \
            load_raw_snapshot(sid, path=db)["content_sha256"]


# ─────────────────────────── P7 · 多页聚合源（快讯/板块榜）的原文按页保真
#
# P1/P6 走的是**单次请求**的源（sina 日线）。多页聚合的源不一样：一份快照由 N 次
# 请求拼成，`raw_text` 是各页 body 的 JSON 数组。这条路径 P1/P6 覆盖不到 —— 单独一道
# 探针，证明每一页的响应体都逐字节进了那个数组（这也是本批我自己最担心被攻破的一处）。


class TestP7MultiPageRawness:
    def test_多页源的原文按页逐字节保真(self, db, monkeypatch):
        # 两页，空白与键序都不同的合法 7x24 响应体
        page1 = ('{"result": {"status": {"code": 0}, "data": {"feed": {"list": [\n'
                 '  {"id": 2, "rich_text": "  甲条 ", "create_time": "2026-09-22 10:00:00"}\n'
                 ']}}}}')
        page2 = ('{"result":{"status":{"code":0},"data":{"feed":{"list":['
                 '{"create_time":"2026-09-22 09:00:00","id":1,"rich_text":"乙条"}'
                 ']}}}}')
        pages = {1: page1, 2: page2}

        def fake(url, **kw):
            import urllib.parse as up
            pn = int(up.parse_qs(up.urlparse(url).query)["page"][0])
            body = pages[pn]
            return json.loads(body), body       # (解析结果, 原文)

        monkeypatch.setattr(sina_news, "get_json_and_text", fake)
        feed = sina_news.fetch_feed(pages=2)

        # raw_text 是两页 body 的 JSON 数组，逐页可**原样**取回
        assert json.loads(feed.raw_text) == [page1, page2]
        sid = save_raw_snapshot(**_kw(source="sina:7x24", payload=feed.raw,
                                      raw_text=feed.raw_text), path=db)
        got = load_raw_snapshot(sid, path=db)
        assert got["content_sha256"] == hashlib.sha256(feed.raw_text.encode()).hexdigest()
        assert json.loads(got["raw_text"]) == [page1, page2]   # 落库后仍逐字节还原每页
