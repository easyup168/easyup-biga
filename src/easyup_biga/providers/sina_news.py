"""新浪财经 7x24 快讯 —— `cn.news.flash` 的 **FALLBACK**（2026-09-26 起）。

⏩ 2026-09-26 更正：这里原来写着「`news` 的唯一数据源」
-------------------------------------------------------
现在的 PRIMARY 是 `cls_news`（财联社电报）。本源降为备用，理由见那边的模块头。

一句话：**财联社带 `level`（源侧重要性档位），而本源三个看起来像重要性的
字段实测恒 0**（见下面第 1 条）。代价是条数 —— 本源是财联社的 4 倍。

为什么当初选了它
----------------
2.4 开工前探活了四个候选：

| 源 | 当时的结果 |
|---|---|
| **新浪 7x24 直播** | ✅ `create_time` 已是北京时间、id 单调、9 页 ~900 条覆盖 29 小时 |
| 东财个股新闻搜索 | ✅ 可用，但形状是关键词检索，不适合做「最近发生了什么」 |
| ~~财联社电报~~ | ~~❌ 404，接口已变~~ ← 🔴 **这条是错的，见下** |
| 东财 7x24 快讯 | ❌ 每补一个参数就再要一个（`fastColumn` → `sortEnd` → …），形状不稳 |

🔴 那张表里的「财联社 ❌」把一个可用的源挡了整整一个 Phase
---------------------------------------------------------
死掉的是**旧的 `nodeapi` 系**接口，不是财联社。官方
`cls.cn/v1/roll/get_roll_list` 一直可用，签名纯本地可算、零 key。
2026-09-26 重新探活：HTTP 200 / `errno=0` / 真实电报直出。

> 教训不在「财联社能用」，在**探活结论会过期，而它不会自己重跑**。
> 当时真正观察到的是「某个 URL 今天 404」，落进这张表却变成了
> 「这个源不可用」—— 两者差一个量级。
> 而一旦写成表格里的一个 ❌，**没人会回头质疑它**。

⇒ 表里的 ❌ 要写**当时试了什么**，不要写成对这个源的终审判决。

🔴 探活抓到的两个反直觉事实
---------------------------

**1. `is_focus` / `top_value` / `tab` 全是常量 0。**

它们的名字看起来都是「重要性标记」，实测 100 条里没有一条非零。
照着它们设计过滤器，会得到一个永远选出空集的过滤器 ——
而空集在下游表现为「今天没有重要新闻」，不会报错。

> 又一次印证「设计先探活」：字段名不是契约，返回值才是。

**2. 这个源的「0 条」只可能是取数失败。**

实测凌晨 1 点仍有 21 条/小时。这和行情源**恰好相反** ——
那边的 0 常常是「这一天还没开始」（见 `eastmoney.BoardResult.nonzero_count`）。

⇒ news 不需要「盘前守卫」，但需要一条**静默即故障**的判据。

静默阈值是测出来的，不是拍的
----------------------------
取 500 条（2026-09-20 20:07 → 09-21 10:19）算相邻间隔：

| 时段 | 中位 | p95 | **最大** |
|---|---|---|---|
| 盘中 | 30s | 116s | **235s** |
| 盘前 | 38s | 168s | 601s |
| 盘后 | 86s | 466s | 715s |
| 夜间 | 81s | 542s | **1830s** |

⇒ `STALE_SEC = 600`，**且只在盘中启用**。

盘中实测最大间隔 235s，600s 留了 2.5 倍余量 ⇒ 正常不会红。
非盘中不设阈值：夜间实测就能到 30 分钟，而周末只会更长 ——
在那里设一条线，得到的是又一个「周末必红」的灯，
几次之后所有人都开始忽略它（本项目已经踩过三次这个形状）。
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from easyup_biga.domain import CN_TZ

from .http import SourceError, get_json_and_text
from .news_item import NewsFeed, NewsItem, is_quote_text

#: ⚠️ `NewsItem` / `NewsFeed` 现在住在 `news_item.py`（两个 provider 共用）。
#:    这里**继续再导出**：下游一直从本模块 import，改 import 路径与本次
#:    要解决的问题无关，多改一处就多一次漏改的机会。
__all__ = ["NewsItem", "NewsFeed", "fetch_feed", "STALE_SEC", "PAGE_SIZE"]

_BASE = "https://zhibo.sina.com.cn/api/zhibo/feed"
_REFERER = "https://finance.sina.com.cn/7x24/"
#: 7x24 全球财经直播间的 id。
_ZHIBO_ID = 152

#: 单页条数。实测 100 生效（与东财板块榜那个被硬顶在 100 的 `pz` 不同，
#: 这里没有测到截断，但也没有理由要更多）。
PAGE_SIZE = 100

#: 盘中静默多久算「源出问题了」。见模块 docstring 的实测分布。
STALE_SEC = 600

def _parse_item(row: dict[str, Any]) -> NewsItem | None:
    """解析一行。**解析不出来就丢掉并让调用方发现数量对不上**，不猜。"""
    text = row.get("rich_text")
    ct = row.get("create_time")
    rid = row.get("id")
    if not isinstance(text, str) or not text.strip() or not isinstance(ct, str):
        return None
    try:
        at = datetime.fromisoformat(ct).replace(tzinfo=CN_TZ)
    except ValueError:
        return None
    tags = tuple(str(t.get("name")) for t in (row.get("tag") or [])
                 if isinstance(t, dict) and t.get("name"))
    # 与财联社共用同一个文本判据 —— 一个源一套正则就是 L-3。
    is_quote = is_quote_text(text)
    return NewsItem(id=int(rid or 0), at=at, text=text, tags=tags, is_quote=is_quote)


def fetch_feed(*, pages: int = 1) -> NewsFeed:
    """取最近的快讯，按时间**降序**（最新在前）。

    Args:
        pages: 翻几页。1 页 = `PAGE_SIZE` 条 ≈ 盘中 1 小时。

    Raises:
        SourceError: 取不到、报文形状不对、或**一条都没解析出来**。
    """
    if pages < 1:
        raise ValueError(f"pages 至少为 1，收到 {pages}")

    items: list[NewsItem] = []
    raw_pages: list[dict[str, Any]] = []
    # 与 `raw_pages` 平行累积各页**原始响应文本**（批 I）。
    raw_text_pages: list[str] = []
    for pn in range(1, pages + 1):
        qs = urllib.parse.urlencode({
            "page": pn, "page_size": PAGE_SIZE, "zhibo_id": _ZHIBO_ID,
            "tag_id": 0, "dire": "f", "dpc": 1,
        })
        payload, body = get_json_and_text(f"{_BASE}?{qs}", referer=_REFERER)
        result = (payload or {}).get("result") or {}
        status = result.get("status") or {}
        if status.get("code") != 0:
            raise SourceError(
                f"7x24 第 {pn} 页: 接口 status.code={status.get('code')} "
                f"msg={status.get('msg')!r}")
        rows = (((result.get("data") or {}).get("feed") or {}).get("list"))
        if not isinstance(rows, list):
            raise SourceError(
                f"7x24 第 {pn} 页: data.feed.list 不是数组（{type(rows).__name__}）"
                " —— 报文形状变了")
        parsed = [it for it in (_parse_item(r) for r in rows) if it is not None]
        # 🔴 解析失败率高说明形状变了，而不是「今天新闻少」。
        #    不设这道检查的话，表现是缺失项为空、条数偏少 —— 完全看不出来。
        if rows and len(parsed) < len(rows) * 0.8:
            raise SourceError(
                f"7x24 第 {pn} 页: {len(rows)} 行只解析出 {len(parsed)} 条 —— "
                "字段可能改名了，不要当成「新闻少」")
        items.extend(parsed)
        raw_pages.append(payload)
        raw_text_pages.append(body)
        if not rows:
            break

    if not items:
        # 见模块 docstring：这个源的 0 条只可能是取数失败。
        raise SourceError(
            "7x24: 一条都没取到 —— 本源实测凌晨 1 点仍有 21 条/小时，"
            "0 条意味着取数失败，不是「今天没新闻」")

    items.sort(key=lambda i: i.at, reverse=True)
    return NewsFeed(items=tuple(items), raw={"pages": raw_pages},
                    raw_text=json.dumps(raw_text_pages, ensure_ascii=False))
