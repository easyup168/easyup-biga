"""情绪数据采集 —— 纯取数，不做任何判断与计算。

分层依据见 architecture.md §5.2：
    collectors（本模块）→ raw 层 → indicators（确定性计算）→ derived

本模块的唯一职责是「把数据源返回的东西原样拿回来」。
它不算涨停率、不判断强弱、不填默认值。任何一层的失败都**如实抛出**，
由上层决定进 `missing[]` 还是重试 —— 采集层自作主张填个 0，
上层就永远没有机会知道这里出过问题。

🔴 为什么直接打 HTTP，而不用现成的 Python 封装库
---------------------------------------------------
常见的 A 股数据封装库提供了同名接口，用起来省事得多。这里不用，有两个实测理由：

1. **封装库把权威日期字段丢了。** 原始接口返回 ``qdate``（这份数据到底是哪天的），
   封装库整理成 DataFrame 时没有保留它。而下面第 2 条说明了为什么这个字段是命门。

2. **这类爬虫库不承诺 API 稳定。** 一个长期运行的同类系统记录过两次
   接口被删 / 改名导致的**静默失效数月**。它们的结论是把版本号钉死，
   并且「不许新增接口而不加失败告警」。既然本项目只需要三个端点，
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
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SourceError",
    "PoolResult",
    "BreadthResult",
    "fetch_pool",
    "fetch_breadth",
    "POOL_ENDPOINTS",
]

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_REFERER = "https://quote.eastmoney.com/"
_TIMEOUT = 15

#: 重试次数与退避。实测这些公开端点会偶发 TLS 握手超时 / 连接被重置，
#: 单次失败不代表数据源挂了。
#: 🔴 但重试耗尽后**必须抛错**，由上层记入 missing[] ——
#:    绝不允许「重试几次还不行就返回 0」，那会把网络抖动变成一条虚假的市场事实。
_RETRIES = 3
_BACKOFF_SEC = (0.8, 2.0)

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


class SourceError(RuntimeError):
    """数据源不可用或返回了无法解释的内容。

    🔴 采集层遇到问题一律抛这个，绝不返回一个「兜底值」。
    返回兜底值 = 让上层无法区分「真的是这个数」和「没取到」。
    """


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


def _get_json(url: str) -> dict[str, Any]:
    endpoint = url.split("?")[0]
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": _REFERER})
    last: Exception | None = None

    for attempt in range(_RETRIES):
        if attempt:
            time.sleep(_BACKOFF_SEC[min(attempt - 1, len(_BACKOFF_SEC) - 1)])
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            continue
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            # 返回了内容但不是 JSON —— 多半是网关错误页，重试没有意义
            raise SourceError(
                f"{endpoint}: 返回不是合法 JSON（前 200 字符）{body[:200]!r}"
            ) from e

    raise SourceError(
        f"{endpoint}: {_RETRIES} 次尝试全部失败，最后一次 "
        f"{type(last).__name__}: {last}"
    ) from last


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
    payload = _get_json(f"{_POOL_BASE}{POOL_ENDPOINTS[pool]}?{qs}")

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

    ⚠️ 这个接口给的是**当前**快照，不带交易日字段。
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
            payload = _get_json(f"https://{host}{_BREADTH_PATH}?{qs}")
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
