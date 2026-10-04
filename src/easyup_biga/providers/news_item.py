"""快讯的**中立行类型** —— `cn.news.flash` 的两个 provider 共用一份定义。

为什么单独一个模块
------------------
`NewsItem` / `NewsFeed` 原来长在 `sina_news.py` 里。2026-09-26 给这个 dataset
接第二个源（财联社）时，唯一的另一条路是让 `cls_news` 从 `sina_news` import ——
那会写下一句假话：**财联社的行依赖新浪的适配器**。

与 `eod_bar.py`（`sina_eod` / `eastmoney_eod` / `tdx_daily_package` 共用）、
`security_listing.py`（两个 security master 共用）同一个做法：
**归一化后的行属于 dataset，不属于某个源。**

覆盖 / 不覆盖
-------------
- 覆盖：一条快讯的中立形状、`is_quote` 这个**文本级**判据
- **不覆盖**：怎么取（各 provider 自己）、静默阈值（**按源定**，见下）

🔴 静默阈值不在这里
-------------------
`STALE_SEC` 留在**各 provider 自己的模块**里，因为它是那个源的实测分布，
不是快讯这件事的属性。两个源的节奏差一个量级（同窗口实测：新浪 34 条/小时、
财联社 8.6 条/小时）——

> 把两个源的阈值统一成一个常量，等于宣称它们节奏一样。它们不一样。

这正是给板块接备用源时立下的那条：**口径差异要声明，不能抹平。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["NewsItem", "NewsFeed", "is_quote_text"]

#: 🔴 机器生成的行情播报。**只打标，不过滤。**
#:
#: 实测盘中新浪 100 条里有 21 条是这种：
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
#:
#: ⚠️ 判据是**文本**，不是来源 ⇒ 它对两个 provider 一视同仁。
#:    这是它该待在中立模块里的理由：一个源一套正则就是 L-3。
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


def is_quote_text(text: str) -> bool:
    """这段正文看起来是不是机器行情播报。见 `_QUOTE_RE` 的注释。"""
    return bool(_QUOTE_RE.search(text)) and not _HEADLINE_RE.match(text)


@dataclass(frozen=True)
class NewsItem:
    """一条快讯。

    Attributes:
        id: 源的自增 id（新的更大）。回放靠它定位原文。
        at: 发布时刻（北京时间）。
        text: 正文原文，不做任何改写。
        tags: 源自带的分类。**是它的口径，不是我们的。**
        is_quote: 是否为机器行情播报。见 `is_quote_text`。
        level: 源侧声明的重要性档位，**只有财联社有**（`A`/`B`/`C`）。

            🔴 **`None` 不等于「不重要」**，它等于「这个源没说」（R-3）。
            新浪那边三个看起来像重要性标记的字段（`is_focus` / `top_value` /
            `tab`）实测 100 条里没有一条非零 —— 照着它们做过滤器，
            会得到一个永远选出空集的过滤器，而空集在下游表现为
            「今天没有重要新闻」，不报错。

            ⇒ 所以这里宁可留 `None`，也不填一个默认档。
               消费方必须能区分「不重要」和「不知道」。
    """

    id: int
    at: datetime
    text: str
    tags: tuple[str, ...]
    is_quote: bool
    level: str | None = None


@dataclass(frozen=True)
class NewsFeed:
    items: tuple[NewsItem, ...]
    #: 解析后的原始报文（`{"pages": [各页对象]}`），落 raw 层的 `payload_json`。
    raw: dict[str, Any]
    #: 🔴 各页**原始响应文本**的 JSON 数组（批 I）：这些源翻多页，每页一次请求；
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

    @property
    def important_count(self) -> int | None:
        """窗口内 `level in (A, B)` 的条数；**源没给 level 时返回 `None`**。

        🔴 不返回 0 —— 那会把「新浪供数、这个源不提供重要性」说成
        「一条重要新闻都没有」。降级发生的那一次恰恰最需要人看清楚
        自己少了什么。
        """
        if all(i.level is None for i in self.items):
            return None
        return sum(1 for i in self.items if (i.level or "").upper() in ("A", "B"))
