"""新浪全市场日线适配器 + 日线的主备对调（2026-09-26）。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：解析层、成交量单位守卫、降级链的可观察后果
- **不覆盖**：真实网络（解析层用离线 fixture，取数层用注入桩）
"""
from __future__ import annotations

import json

import pytest

from easyup_biga.data.datasets.eod_daily_bars import (
    FALLBACK_PROVIDER_ID,
    PROVIDER_ID,
    normalize,
)
from easyup_biga.providers.eod_bar import EodBar
from easyup_biga.providers.http import SourceError
from easyup_biga.providers.sina_eod import parse_eod_page, parse_eod_pages


def _page(rows):
    return json.dumps([
        {"symbol": f"sh{c}", "code": c, "name": n, "open": o, "high": h,
         "low": lo, "trade": cl, "settlement": pc, "volume": v, "amount": a,
         "pricechange": "0.1", "changepercent": "0.9", "ticktime": "15:30:01"}
        for c, n, o, h, lo, cl, pc, v, a in rows
    ], ensure_ascii=False)


def _sane(count=30, *, lots=False):
    """一页口径正常的数据：amount ≈ volume × close。"""
    rows = []
    for i in range(count):
        close = 10.0 + i
        shares = 100_000 + i
        volume = shares / 100 if lots else shares
        rows.append((f"60{i:04d}", f"股{i}", close - 0.2, close + 0.3, close - 0.4,
                     close, close - 0.1, volume, shares * close))
    return _page(rows)


# ── 主备对调 ───────────────────────────────────────────────────────────────
def test_日线主源是新浪备用是东财():
    """🔴 依据不是「东财那天挂了」，是两条长期证据（见 registry 里那段注释）：
    同机另一套长期运行的实例每天走的就是新浪；公开的源目录把东财标为
    「共用同一套风控、IP 被封会成片失联」。
    """
    from easyup_biga.data.failover import provider_chain

    assert PROVIDER_ID == "sina_eod"
    assert FALLBACK_PROVIDER_ID == "eastmoney_eod"
    assert [p for p, _ in provider_chain("cn.equity.daily_bars")] == [
        "sina_eod", "eastmoney_eod"]


# ── 解析层 ─────────────────────────────────────────────────────────────────
def test_解析成中立行():
    rows = parse_eod_page(_sane(30), page_no=1)
    assert len(rows) == 30
    assert rows[0].symbol == "600000"
    assert rows[0].close == 10.0


def test_重复代码要响亮失败():
    page = _page([("600000", "甲", 1, 1, 1, 1, 1, 100000, 100000),
                  ("600000", "甲", 1, 1, 1, 1, 1, 100000, 100000)])
    with pytest.raises(SourceError, match="duplicate"):
        parse_eod_pages([page])


def test_这个源不自报总数这件事写在契约里():
    """⚠️ 「declared_total vs 实收行数」那道交叉校验在这个源上**天然失效** ——
    因为它不自报总数，两者恒等。换源就要知道自己还剩几道校验。
    """
    result = parse_eod_pages([_sane(30)])
    assert result.declared_total == len(result.rows)
    assert result.effective_trade_date is None, "快照型端点给不出业务日"
    assert result.provider_id == "sina"


# ── 成交量单位守卫 ─────────────────────────────────────────────────────────
def test_成交量单位是股时正常通过():
    result = parse_eod_pages([_sane(30)])
    assert result.rows[0].volume == 100_000


def test_成交量单位变成手要当场失败(_=None):
    """🔴 同机另一套长期运行的实例的代码里留着一句用真金白银换来的注释：
    「成交股数用 amount/close 推, 不用 volume —— volume 单位跨某日变过」。

    也就是说**这个字段的单位在历史上真的漂移过一次**。
    写死倍数就会在漂移那天静默出错 —— 成交量差 100 倍，而那不会报错，
    只会让某天的换手率、量比全部失真。
    """
    with pytest.raises(SourceError, match="成交量单位疑似变了"):
        parse_eod_pages([_sane(30, lots=True)])


def test_样本太少时守卫宁可失败():
    """样本太少 ⇒ 这道守卫等于没有。宁可失败，不要假装查过。"""
    with pytest.raises(SourceError, match="样本太少"):
        parse_eod_pages([_sane(5)])


# ── 归一化：溯源写实际供数方 ───────────────────────────────────────────────
def test_归一化把供数方写进每一行():
    rows = [EodBar(symbol="600000", open=10, high=11, low=9, close=10.5,
                   prev_close=10, volume=100, amount=1050)]
    bars = normalize(rows, "20260925", "2026-09-25T15:10:00+08:00",
                     provider_id="eastmoney_eod")
    assert bars[0].provider_id == "eastmoney_eod", "降级过的那天不能写成主源"


def test_没有价格的行不产生假OHLC():
    rows = [EodBar(symbol="600000", open="-", high="-", low="-", close="-")]
    assert normalize(rows, "20260925", "2026-09-25T15:10:00+08:00") == ()
