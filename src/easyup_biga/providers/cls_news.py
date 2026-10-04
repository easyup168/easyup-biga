"""财联社电报 —— `cn.news.flash` 的 PRIMARY（2026-09-26 起）。

为什么换主源
------------
`sina_news` 的模块头里原本写着：

> 财联社电报 ❌ 404，接口已变

🔴 **那条结论是错的，而且它拦住了这个源整整一个 Phase。**

死掉的是**旧的 `nodeapi` 系**接口，不是财联社。官方新版
`cls.cn/v1/roll/get_roll_list` 一直可用，强制的 `sign` **纯本地可算、零 key**：

    sign = md5(sha1("&".join(f"{k}={v}" for k in sorted(params))))

2026-09-26 重新探活：HTTP 200 / `errno=0` / 真实电报直出。

> 教训不在「财联社能用」，在**「接口挂了」这个结论会过期，而探活记录不会自己重跑**。
> 当时那条记的是「某个 URL 今天 404」，落进文档就变成了「这个源不可用」——
> 两者差一个量级，而没人会回头质疑一张写着 ❌ 的表。

它比新浪多了什么 / 少了什么
---------------------------
2026-09-26 同窗口实测（09-25 16:04 → 09-26 18:35，**非交易日**）：

| | 新浪 7x24 | 财联社电报 |
|---|---|---|
| 速率 | 34.0 条/小时 | **8.6 条/小时** |
| 重要性信号 | **无**（三个字段恒 0） | `level`：C=162 / B=18 |
| 机器行情播报 | 盘中实测占 21% | 未见 |
| 一页覆盖 | 100 条 ≈ 3 小时 | 50 条 ≈ 6 小时 |

⇒ 换主源换来的是 **`level` 这个真信号**，代价是**条数少 3/4**。
两者都要声明，不能只说前者。

🔴 探活抓到的三个反直觉事实
---------------------------

**1. `rn > 50` 返回空列表，而 `errno` 仍是 0。**

实测（交替三轮，结果完全一致）：

    rn=50 → 50 条    rn=51 → 0 条    rn=55 → 0 条    rn=99 → 0 条

源说「成功」然后什么都不给。照着「多要一点总没坏处」把 `rn` 调到 100，
得到的是一个**永远说今天没新闻**的 provider —— 不报错。

⇒ `PAGE_SIZE` 封死 50，且**首页空一律抛**（见下）。

**2. 这个源的「0 条」只可能是我们错了或它坏了，不可能是「今天没新闻」。**

财联社是 7×24 的商业电报服务，非交易日实测仍有 8.6 条/小时。
⇒ 与新浪同一条规矩：**空 = 取数失败**，绝不静默当成空集。

**3. 名字像宝的结构化字段基本是空的。**

360 条实测填充率：

    stock_list   1%      plate_list   0%
    tags         0%      bold         恒 0
    subjects    94%  ← 但那是**栏目名**（「环球市场情报」），不是个股/板块归属

这是 `is_focus` 全零那一幕的重演：**字段名不是契约，返回值才是。**
⇒ 本适配器**只取 `level`**，不碰上面那些 —— 拿 1% 填充率的字段做归因，
  得到的是一个 99% 说「不知道」的功能，而它看起来像个功能。

静默阈值：**现在测不出来，所以不设**
------------------------------------
`sina_news.STALE_SEC = 600` 是拿**盘中**实测分布定的（盘中最大间隔 235s）。
财联社这边手上只有非交易日的数据（2026-09-25 中秋 / 09-26 周六）：

    盘中时段 中位 505s / p95 1294s / 最大 1839s   ← 但那天休市，说明不了盘中

拿它推一个阈值，等于用休市日的节奏考核交易日。

⇒ `STALE_SEC = None`，消费方跳过静默判据（R-3：算不出来就说算不出来）。
  下一个交易日是 2026-09-28，实测之后再定。

⚠️ **不要为了「和新浪对齐」直接抄 600。** 同窗口速率差 4 倍，
   抄过来大概率得到一盏正常也红的灯 —— 而几次之后所有人都开始忽略它。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from easyup_biga.domain import CN_TZ

from .http import SourceError, get_json_and_text
from .news_item import NewsFeed, NewsItem, is_quote_text

__all__ = ["fetch_feed", "STALE_SEC", "PAGE_SIZE", "MAX_PAGE_SIZE"]

_BASE = "https://www.cls.cn/v1/roll/get_roll_list"
_REFERER = "https://www.cls.cn/"

#: 签名的固定公共参数。`sv` 是前端版本号 —— 上游改版可能要跟进。
#: ⚠️ 它进签名，所以**不能随便改**：改了签名就对不上，表现是 `errno` 非 0。
_BASE_PARAMS = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5"}

#: 🔴 单页条数的**硬上限**。51 起返回空列表且 `errno=0` —— 见模块头第 1 条。
MAX_PAGE_SIZE = 50

#: 默认单页条数。取满上限：这个源一页就覆盖 ~6 小时，
#: 而 `WINDOW_MIN` 默认 60 分钟 ⇒ 一次请求就够，比新浪少两次网络往返。
PAGE_SIZE = MAX_PAGE_SIZE

#: 盘中静默多久算「源出问题了」。**`None` = 还没实测出来**，见模块头最后一节。
#: 🔴 不要填一个数进来「先用着」—— 那个数会被当成实测值传下去。
STALE_SEC: int | None = None


def _sign(params: dict[str, str]) -> str:
    """财联社签名：`md5(sha1(按 key 字典序拼接的 query 串))`。纯本地算、零 key。

    ⚠️ 拼签名的串与真正发出去的 query **必须逐字一致**（同样按 key 排序、
    同样不做 URL 编码）。两边任何一处不同，服务端算出来的就是另一个签名 ——
    表现是 `errno` 非 0，不是 4xx。
    """
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


def _page(last_time: str, page_size: int) -> tuple[Any, str]:
    params = {**_BASE_PARAMS, "last_time": str(last_time),
              "refresh_type": "1", "rn": str(page_size)}
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return get_json_and_text(f"{_BASE}?{qs}&sign={_sign(params)}", referer=_REFERER)


def _parse_item(row: dict[str, Any]) -> NewsItem | None:
    """解析一行。**解析不出来就丢掉并让调用方发现数量对不上**，不猜。"""
    if not isinstance(row, dict):
        return None
    ctime = row.get("ctime")
    # 正文优先 `content`，退到 `brief`。两者都空就是解析失败。
    text = str(row.get("content") or row.get("brief") or "").strip()
    if not text or ctime in (None, ""):
        return None
    try:
        at = datetime.fromtimestamp(int(ctime), CN_TZ)
    except (TypeError, ValueError, OSError):
        return None
    # `subjects` 是**栏目名**（实测 94% 填充），不是个股/板块归属 ——
    # 放进 `tags` 与新浪的 `tag[].name` 同位：都是**源自己的分类口径**。
    tags = tuple(
        str(s.get("subject_name")) for s in (row.get("subjects") or [])
        if isinstance(s, dict) and s.get("subject_name"))
    level = str(row.get("level") or "").upper() or None
    return NewsItem(id=int(row.get("id") or 0), at=at, text=text, tags=tags,
                    is_quote=is_quote_text(text), level=level)


def fetch_feed(*, pages: int = 1, page_size: int = PAGE_SIZE) -> NewsFeed:
    """取最近的电报，按时间**降序**（最新在前）。

    Args:
        pages: 翻几页。1 页 = `page_size` 条 ≈ 6 小时（非交易日实测）。
        page_size: 单页条数，**不得超过 `MAX_PAGE_SIZE`**。

    Raises:
        ValueError: `pages < 1` 或 `page_size` 越界。
        SourceError: 取不到、`errno` 非 0、报文形状不对、或一条都没解析出来。
    """
    if pages < 1:
        raise ValueError(f"pages 至少为 1，收到 {pages}")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        # 🔴 拦在这里，不要让它变成一次「成功但是空」的请求。
        raise ValueError(
            f"page_size 必须在 1..{MAX_PAGE_SIZE} 之间，收到 {page_size} —— "
            f"实测 rn>{MAX_PAGE_SIZE} 时源返回空列表且 errno=0，"
            "那会静默表现为「今天没新闻」")

    items: list[NewsItem] = []
    raw_pages: list[Any] = []
    raw_text_pages: list[str] = []
    seen: set[int] = set()
    last_time = ""

    for pn in range(1, pages + 1):
        payload, body = _page(last_time, page_size)
        if not isinstance(payload, dict):
            raise SourceError(
                f"财联社第 {pn} 页：顶层不是对象（{type(payload).__name__}）—— 报文形状变了")
        # 🔴 正向识别：`errno` 非 0 必须冒泡，不能静默当成「今天没快讯」。
        if payload.get("errno"):
            raise SourceError(
                f"财联社第 {pn} 页：errno={payload.get('errno')!r} "
                f"msg={payload.get('msg')!r}（签名串与实际 query 不一致时也报这个）")
        rows = (payload.get("data") or {}).get("roll_data")
        if not isinstance(rows, list):
            raise SourceError(
                f"财联社第 {pn} 页：data.roll_data 不是数组"
                f"（{type(rows).__name__}）—— 报文形状变了")
        if pn == 1 and not rows:
            # 见模块头第 2 条。首页空不可能是「没新闻」。
            raise SourceError(
                "财联社首页 0 条 —— 这个源非交易日实测仍有 8.6 条/小时，"
                f"0 条意味着请求有问题（先查 rn 是否 >{MAX_PAGE_SIZE}）或源故障，"
                "不是「今天没新闻」")
        raw_pages.append(payload)
        raw_text_pages.append(body)
        if not rows:
            break

        # 🔴 广告先滤掉，**再**算解析率 —— 两者不是一回事：
        #
        #      广告被剔除  = 源自己声明的（`is_ad`/`is_fad`），我们照做
        #      解析不出来  = 字段改名了，形状变了
        #
        #    合在一个计数器里的后果：某天广告占比超过 20%，守卫会报
        #    「字段可能改名了」—— 把一个**正常**的页面判成故障，
        #    而真正改名那天它给出的还是同一句话。
        #    （一个计数器量两件事 = 两件事都测不准。）
        #
        #    ⚠️ 实测 360 条里两个广告字段都是 0 —— 这条分支**从没触发过**。
        #       哪天真触发了，说明源的形态变了，值得注意。
        content_rows = [r for r in rows
                        if not (isinstance(r, dict)
                                and (r.get("is_ad") or r.get("is_fad")))]
        parsed = [it for it in (_parse_item(r) for r in content_rows)
                  if it is not None]
        if content_rows and len(parsed) < len(content_rows) * 0.8:
            raise SourceError(
                f"财联社第 {pn} 页：{len(content_rows)} 条非广告行只解析出 "
                f"{len(parsed)} 条 —— 字段可能改名了，不要当成「新闻少」")
        fresh = [it for it in parsed if it.id not in seen]
        if not fresh:
            break                      # 游标没推进，防死循环
        seen.update(it.id for it in fresh)
        items.extend(fresh)

        # 游标 = 本页**原始**末条的 ctime（不是解析后的末条 ——
        # 末条若是广告会被丢掉，拿解析后的末条当游标会漏掉它之后那一段）。
        last_time = str(rows[-1].get("ctime") or "")
        if not last_time:
            break

    if not items:
        raise SourceError(
            "财联社：一条都没解析出来 —— 见首页空那条的说明，这不是「今天没新闻」")

    items.sort(key=lambda i: i.at, reverse=True)
    return NewsFeed(items=tuple(items), raw={"pages": raw_pages},
                    raw_text=json.dumps(raw_text_pages, ensure_ascii=False))
