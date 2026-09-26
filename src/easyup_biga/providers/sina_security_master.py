"""新浪全市场 A 股名单 —— `cn.security_master` 的 **FALLBACK** 适配器。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：分页抓「当前在交易的」A 股名单，翻译成 `SecurityListing`
- **不覆盖**：交易所/板块判断（归一化层唯一那份）、上市日（这个源不给）

🔴 为什么需要一个备用源 —— 它不是预防性设计，是被一次真故障逼出来的
----------------------------------------------------------------
2026-09-26 实测：`push2.eastmoney.com` / `push2delay.eastmoney.com` /
`N.push2.eastmoney.com` 这一组**全部** 502（nginx 默认页），而**同一时刻、
同一 IP、同样的请求头**下 `push2ex` / `push2his` / `datacenter-web` /
`quote.eastmoney.com` 全部 200。

⇒ 这不是限流、不是请求头、不是过滤串，是那一组 host 的上游故障。
   我们这边做什么都不会好，而 `cn.security_master` 是全市场 EOD 的**第一步** ——
   它取不到，当天整条 EOD 链就停在 `if not universe: raise`。

⚠️ 口径与主源**不等价**，必须知道差在哪
---------------------------------------
| | 东财 `clist`（primary） | 本适配器（fallback） |
|---|---|---|
| 成员 | 按板块过滤串取在册 A 股 | 行情节点 `hs_a`，**当前在交易的**那些 |
| 上市日 | 有（`f26`） | **没有** ⇒ `list_date=None` |

缺上市日不影响 universe：`fact_security_master.list_date` 可空，
`security_universe_at()` 只按 `status` / `exchange` 过滤。
它会体现在质量指标 `missing_list_date_count` 上 —— **看得见的缺，不是隐藏的缺**。

🔴 为什么不用东财 `datacenter-web` 当备用源（它字段更齐）
--------------------------------------------------------
探活过：`RPT_F10_BASIC_ORGINFO` 有上市日、有交易所，但它是**机构信息表**，
24780 条，含 `PT金田A`、`国华退` 这类早已退市的，而**没有退市列可筛**。
唯一能分开的信号是名字里的「退」「PT」字样 —— 按字符串形状分类正是 L-13。

> **字段齐但口径错，比缺一个可空字段糟得多。**

分层（照 `sina_calendar.py` / `szse.py` 的既定形状）
---------------------------------------------------
* `parse_security_master_page`：**不联网的纯函数**，能对着已存的 raw 重放
* `fetch_security_master`：联网的薄函数，串行翻页 + 限流
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Sequence

from easyup_biga.domain import now_cn

from .http import SourceError, get_text, throttle
from .security_listing import SecurityListing, SecurityMasterFetchResult

__all__ = [
    "SINA_SECURITY_MASTER_URL",
    "parse_security_master_page",
    "parse_security_master_pages",
    "fetch_security_master",
]

SINA_SECURITY_MASTER_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_REFERER = "https://vip.stock.finance.sina.com.cn/mkt/"
#: `hs_a` = 沪深京 A 股行情节点。它是**行情**节点 ⇒ 只列当前在交易的。
_NODE = "hs_a"
_PAGE_SIZE = 100
#: 全市场约 5600 只 ⇒ 56 页。留出余量，但不允许无限翻。
_MAX_PAGES = 120


def parse_security_master_page(text: str, *, page_no: int) -> list[SecurityListing]:
    """把一页响应翻译成中立行。**不判断交易所/板块**。"""
    body = text.strip()
    if not body:
        raise SourceError(f"sina security_master page {page_no}: empty response")
    if body in ("null", "[]"):
        return []
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError(
            f"sina security_master page {page_no}: response is not JSON") from exc
    if not isinstance(payload, list):
        raise SourceError(
            f"sina security_master page {page_no}: top level is not a list")

    out: list[SecurityListing] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SourceError(
                f"sina security_master page {page_no}: row is not an object")
        code = item.get("code")
        name = item.get("name")
        symbol = str(item.get("symbol") or "")
        if not code or not name:
            raise SourceError(
                f"sina security_master page {page_no}: row missing code/name: {item!r}")
        out.append(SecurityListing(
            symbol=code,
            name=name,
            # `symbol` 形如 `sh600000` / `bj920000` —— 只当诊断线索带着，
            # 交易所判断仍然由归一化层按代码前缀做（只有一份）。
            market_hint=symbol[:2] or None,
            # 🔴 这个源不给上市日。**不反推** —— 写 None，让它在质量指标里露头。
            list_date=None,
        ))
    return out


def parse_security_master_pages(
    texts: Sequence[str],
    *,
    retrieved_at: str | None = None,
) -> SecurityMasterFetchResult:
    """合并所有页。

    ⚠️ 这个源**不返回总数**，所以 `total` 只能是「我收到了多少行」。
    与主源的 `declared_total`（服务端自报）不是同一种东西 ——
    主源那边 `declared_total != len(records)` 会判 QUARANTINED，
    而这里两者恒等，那道交叉校验在降级时**天然失效**。
    写在这里免得将来有人以为它还在保护着什么。
    """
    rows: list[SecurityListing] = []
    for page_no, text in enumerate(texts, start=1):
        rows.extend(parse_security_master_page(text, page_no=page_no))
    if not rows:
        raise SourceError("sina security_master: no securities returned")

    seen: set[str] = set()
    for row in rows:
        key = str(row.symbol)
        if key in seen:
            raise SourceError(f"sina security_master: duplicate code {key}")
        seen.add(key)

    return SecurityMasterFetchResult(
        total=len(rows),
        rows=tuple(rows),
        raw={"pages": list(texts)},
        raw_text=json.dumps(list(texts), ensure_ascii=False),
        retrieved_at=retrieved_at or now_cn().isoformat(),
        provider_id="sina",
    )


def _page_url(page_no: int, page_size: int) -> str:
    query = urllib.parse.urlencode({
        "page": page_no,
        "num": page_size,
        "sort": "symbol",
        "asc": 1,
        "node": _NODE,
        "symbol": "",
    })
    return f"{SINA_SECURITY_MASTER_URL}?{query}"


def fetch_security_master(
    *,
    page_size: int = _PAGE_SIZE,
    max_pages: int = _MAX_PAGES,
) -> SecurityMasterFetchResult:
    """串行翻页抓完整名单。

    🔴 **串行 + 限流**，与主源同一条纪律：并发零间隔是最快被封的走法。
    56 页 × 1 秒多一点 ≈ 一分钟，对一个每天跑一次的同步任务完全可接受。

    ⚠️ 终止条件是「**某一页空了**」，不是「翻够 N 页」——
    这个源不告诉你总数，硬写页数会在上市数量变化时静默截断名单。
    `max_pages` 只是防跑飞的上限，撞到它是**错误**不是正常结束。
    """
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    texts: list[str] = []
    for page_no in range(1, max_pages + 1):
        throttle("sina")
        text = get_text(_page_url(page_no, page_size), referer=_REFERER)
        if not parse_security_master_page(text, page_no=page_no):
            break
        texts.append(text)
    else:
        raise SourceError(
            f"sina security_master: 翻到第 {max_pages} 页仍然非空 —— "
            "要么上游行为变了，要么分页参数不对；不静默截断名单")

    if not texts:
        raise SourceError("sina security_master: first page is empty")
    return parse_security_master_pages(texts, retrieved_at=now_cn().isoformat())
