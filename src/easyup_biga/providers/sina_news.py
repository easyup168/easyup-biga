"""新浪财经 7x24 快讯 —— `news` 的唯一数据源。

为什么是它
----------
2.4 开工前探活了四个候选，两个当场出局：

| 源 | 结果 |
|---|---|
| **新浪 7x24 直播** | ✅ `create_time` 已是北京时间、id 单调、9 页 ~900 条覆盖 29 小时 |
| 东财个股新闻搜索 | ✅ 可用，但形状是关键词检索，不适合做「最近发生了什么」 |
| 财联社电报 | ❌ 404，接口已变 |
| 东财 7x24 快讯 | ❌ 每补一个参数就再要一个（`fastColumn` → `sortEnd` → …），形状不稳 |

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
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from _contract import CN_TZ

from .http import SourceError, get_json_and_text

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

#: 🔴 机器生成的行情播报。**只打标，不过滤。**
#:
#: 实测盘中 100 条里有 21 条是这种：
#:
#:     「深证成指涨1.00%，现报13777.470点；上证指数涨0.44%，现报3929.027点」
#:     「中证500指数期货连续主力合约日内涨1%，现报7717.60点」
#:
#: 这些数字是 `market` / `sector` 已经权威产出的事实（裁定 15）。
#: news 不得从文本里重新提取它们。
#:
#: 为什么打标而不是过滤：过滤是**判断性归类**（铁律 4），
#: 而且一个正则误伤一条真消息，下游完全看不出来。
#: 打标把这个判断留给 agent，同时让「有多少条是播报」变成一个可见的事实。
_QUOTE_RE = re.compile(
    r"现报|报\d+\.\d+点|指数(涨|跌)\d|主力合约|连续主力|涨幅居前|跌幅居前")
#: 带【标题】的通常是编辑写的快讯，即使含数字也不是机器播报。
#:
#: ⚠️ **已知局限：这条豁免会漏标一部分真播报。** 实测见过：
#:
#:     【国债期货开盘】30年期主力合约涨0.16%，10年期主力合约涨0.01%…
#:     【医药生物板块拉升 诺禾致源20%涨停】…
#:
#: 前者是机器播报，后者踩到了 sector / emotion 的事实。
#:
#: **不调正则去追它们** —— 那是拿一天的样本过拟合，而代价是误伤真消息。
#: 关键在于这个标记的定位：**它是给 agent 的提示，不是防线。**
#: 真正的防线写在 `agents/news/AGENTS.md`：
#: 「不许从快讯里提取任何行情数字」—— 无论有没有这个标记。
#:
#: ⇒ 所以 `quote_count` 是**下界**，不是精确计数。用它感知量级，不要用它做判据。
_HEADLINE_RE = re.compile(r"^【")


@dataclass(frozen=True)
class NewsItem:
    """一条快讯。

    Attributes:
        id: 源的自增 id，**单调递减**（新的更大）。回放靠它定位原文。
        at: 发布时刻（北京时间，源本来就给的北京时间）。
        text: 正文原文，不做任何改写。
        tags: 源自带的分类（公司 / 市场 / 焦点 / 宏观 …）。**是它的口径，不是我们的。**
        is_quote: 是否为机器行情播报。见 `_QUOTE_RE`。
    """

    id: int
    at: datetime
    text: str
    tags: tuple[str, ...]
    is_quote: bool


@dataclass(frozen=True)
class NewsFeed:
    items: tuple[NewsItem, ...]
    #: 解析后的原始报文（`{"pages": [各页对象]}`），落 raw 层的 `payload_json`。
    raw: dict[str, Any]
    #: 🔴 各页**原始响应文本**的 JSON 数组（批 I）：这个源翻多页，每页一次请求；
    #: 数组每个元素逐字节等于对应那页的响应体。`content_sha256` 基于它算。
    raw_text: str | None = None

    @property
    def server_as_of(self) -> datetime | None:
        """**服务端自己声明的时刻**；`None` = 这个端点不带日期。

        统一接口的理由见 `_sources/__init__.py` 顶部的「F16」一节：
        由端点自己声明，调用方就不必逐处判断「这个源有没有日期」——
        而那个判断一旦分散，就必然有某一处判错（F4 就是这么来的）。
        """
        return self.newest_at

    @property
    def newest_at(self) -> datetime | None:
        return self.items[0].at if self.items else None

    @property
    def quote_count(self) -> int:
        return sum(1 for i in self.items if i.is_quote)


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
    is_quote = bool(_QUOTE_RE.search(text)) and not _HEADLINE_RE.match(text)
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
