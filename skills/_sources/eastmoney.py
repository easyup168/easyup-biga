"""东方财富系端点 —— 股池（涨停/炸板/跌停）与涨跌家数。

🔴 为什么直接打 HTTP，而不用现成的 Python 封装库
---------------------------------------------------
常见的 A 股数据封装库提供了同名接口，用起来省事得多。这里不用，有两个实测理由：

1. **封装库把权威日期字段丢了。** 原始接口返回 ``qdate``（这份数据到底是哪天的），
   封装库整理成 DataFrame 时没有保留它。而下面第 2 条说明了为什么这个字段是命门。

2. **这类爬虫库不承诺 API 稳定。** 一个长期运行的同类系统记录过两次
   接口被删 / 改名导致的**静默失效数月**。它们的结论是把版本号钉死，
   并且「不许新增接口而不加失败告警」。既然本项目只需要几个端点，
   直接持有 URL 反而让失效点更少、更可见。

🔴 数据源的静默陷阱（实测，2026-09-19）
---------------------------------------
涨停池接口对**任何**日期参数都返回 ``rc=0``，从不报错：

===================  ==========  ======  ================================
请求 date            返回 qdate   tc      实际含义
===================  ==========  ======  ================================
20260918（交易日）    20260918    78      ✅ 正确
20260920（周日）      20260918    78      ⚠️ 静默返回上一交易日的数据
20260101（元旦）      20260918    0       ⚠️ 最坏：你会记下「元旦涨停 0 家」
===================  ==========  ======  ================================

第三行是这一类失败的典型形状：**返回了一个看似合理的数字，而它描述的根本不是你问的那天。**
没有异常、没有警告、没有空值。

因此本模块的铁规则：

    as_of 永远取自 `qdate`（数据自己声明的日期），
    绝不取自「我请求的日期」。两者不符时由上层记入 missing[]。

🔴 涨跌家数端点连 qdate 都没有（实测，2026-09-20）
---------------------------------------------------
它返回的是**当前快照**，不带任何日期字段。周日请求它，返回的数与上一交易日
逐位相同（`4277/1173/180`）—— 它把最后一个交易日冻在那里反复发。

⇒ 这个端点的 `as_of` 只能由调用方结合交易日推断，并且**必须为此记一条 warning**。
⇒ 也因此（裁定 15）它只能有**一个**生产 agent：两个 agent 并行各调一次，
   同一张 Card 上就会出现同一个字段两个值，而两个都带着推断出来的 `as_of`。
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

from .http import SourceError, get_json

__all__ = [
    "PoolResult",
    "BreadthResult",
    "fetch_pool",
    "fetch_breadth",
    "POOL_ENDPOINTS",
]

_REFERER = "https://quote.eastmoney.com/"

#: 三个股池端点。key 是本项目内部名，value 是接口路径。
POOL_ENDPOINTS: dict[str, str] = {
    "limit_up": "getTopicZTPool",      # 涨停池
    "broken_board": "getTopicZBPool",  # 炸板池
    "limit_down": "getTopicDTPool",    # 跌停池
}

_POOL_BASE = "https://push2ex.eastmoney.com/"
#: 涨跌家数端点。同一份数据有多个镜像主机，可用性随时间变化 ——
#: 实测同一时刻 push2delay 可用而 push2his / push2 被服务端直接断连。
#: 🔴 备选链耗尽仍然失败时抛错，由上层记入 missing[]，不允许退化成 0。
_BREADTH_HOSTS = ("push2delay.eastmoney.com", "push2his.eastmoney.com")
_BREADTH_PATH = "/api/qt/ulist.np/get"

#: 涨跌家数取自各市场的总指数：上证 / 深证综指 / 北证 50
_BREADTH_SECIDS = "1.000001,0.399106,0.899050"


@dataclass(frozen=True)
class PoolResult:
    """一个股池的原始返回。

    Attributes:
        pool: 内部池名（`POOL_ENDPOINTS` 的 key）。
        requested_date: 请求的日期 ``YYYYMMDD``。
        qdate: 🔴 **数据自己声明的日期**。与 `requested_date` 不符即为陈旧数据。
        total: 接口给出的总数 ``tc``。
        rows: 池内个股原始记录。
        raw: 完整原始响应，原样落 raw 层。
    """

    pool: str
    requested_date: str
    qdate: str | None
    total: int
    rows: list[dict[str, Any]]
    raw: dict[str, Any]

    @property
    def date_matches(self) -> bool:
        return self.qdate == self.requested_date


@dataclass(frozen=True)
class BreadthResult:
    """涨跌平家数（全市场合计）。"""

    advance: int
    decline: int
    flat: int
    per_market: list[dict[str, Any]]
    raw: dict[str, Any]


def fetch_pool(pool: str, date: str, *, page_size: int = 500) -> PoolResult:
    """取一个股池。

    Args:
        pool: `POOL_ENDPOINTS` 的 key。
        date: ``YYYYMMDD``。
        page_size: 一次取多少条。涨停家数历史极值在 200 上下，500 足够，
            但仍然核对 ``tc`` 与实际行数，不一致即抛错 —— 悄悄少几行比取不到更难发现。
    """
    if pool not in POOL_ENDPOINTS:
        raise ValueError(f"未知的池名 {pool!r}，可选 {sorted(POOL_ENDPOINTS)}")
    if len(date) != 8 or not date.isdigit():
        raise ValueError(f"date 必须是 YYYYMMDD，收到 {date!r}")

    qs = urllib.parse.urlencode({
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": page_size,
        "sort": "fbt:asc",
        "date": date,
    })
    payload = get_json(f"{_POOL_BASE}{POOL_ENDPOINTS[pool]}?{qs}", referer=_REFERER)

    if payload.get("rc") != 0:
        raise SourceError(f"{pool}: 接口返回 rc={payload.get('rc')}")

    data = payload.get("data")
    if data is None:
        # 接口用 data=null 表示「这一天没有数据」，与「0 条」不是一回事。
        raise SourceError(f"{pool}: data 为 null（date={date}），无法区分「0 条」与「无此交易日」")

    rows = data.get("pool") or []
    total = int(data.get("tc", 0))
    qdate = str(data["qdate"]) if data.get("qdate") is not None else None

    if total and len(rows) != total:
        raise SourceError(
            f"{pool}: 接口声称 tc={total} 但只返回 {len(rows)} 行 —— "
            f"分页可能截断（page_size={page_size}）"
        )

    return PoolResult(pool=pool, requested_date=date, qdate=qdate,
                      total=total, rows=rows, raw=payload)


def fetch_breadth() -> BreadthResult:
    """取全市场涨跌平家数。

    ⚠️ 这个接口给的是**当前**快照，不带交易日字段（模块 docstring 有实测）。
    因此它的 `as_of` 只能由调用方结合交易日推断 —— 上层会为此单独记一条警告。
    这与股池的 `qdate` 形成对比：**同一次采集里，不同字段的可信度可以是不同的**，
    契约层要求逐条证据带自己的 `as_of`，正是为了不让这种差别被抹平。
    """
    qs = urllib.parse.urlencode({
        "fltt": 2,
        "fields": "f12,f14,f104,f105,f106",
        "secids": _BREADTH_SECIDS,
    })
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    for host in _BREADTH_HOSTS:
        try:
            payload = get_json(f"https://{host}{_BREADTH_PATH}?{qs}", referer=_REFERER)
            break
        except SourceError as e:
            errors.append(f"{host}: {e}")
    if payload is None:
        raise SourceError("breadth: 全部备选主机失败 —— " + " | ".join(errors))

    if payload.get("rc") != 0:
        raise SourceError(f"breadth: 接口返回 rc={payload.get('rc')}")
    diff = (payload.get("data") or {}).get("diff") or []
    if not diff:
        raise SourceError("breadth: data.diff 为空")

    adv = dec = flat = 0
    per_market: list[dict[str, Any]] = []
    for d in diff:
        a, dn, f = d.get("f104"), d.get("f105"), d.get("f106")
        if a is None or dn is None or f is None:
            raise SourceError(f"breadth: {d.get('f14')} 缺涨跌平字段 f104/f105/f106")
        adv, dec, flat = adv + int(a), dec + int(dn), flat + int(f)
        per_market.append({"name": d.get("f14"), "code": d.get("f12"),
                           "advance": int(a), "decline": int(dn), "flat": int(f)})

    return BreadthResult(advance=adv, decline=dec, flat=flat,
                         per_market=per_market, raw=payload)
