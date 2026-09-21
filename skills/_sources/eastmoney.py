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
    "BOARD_KINDS",
    "Board",
    "BoardResult",
    "fetch_boards",
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


# ────────────────────────────────────────────────────────── 板块榜

#: 板块榜。key 是本项目内部名，value 是接口的 `fs` 参数。
BOARD_KINDS: dict[str, str] = {
    "industry": "m:90+t:2",   # 行业板块
    "concept": "m:90+t:3",    # 概念板块
}

_CLIST_PATH = "/api/qt/clist/get"
#: 🔴 **`pz` 大于 100 会被静默忽略**（实测：请求 600，返回 100 行，
#:    而 `total` 照报 496）。这与 kline 的 `lmt` 被忽略是同一类陷阱 ——
#:    接口不报错，只是少给你。所以必须分页，并核对总数：
#:    悄悄少几十个板块，涨跌分布就是错的，而且不会报错。
_BOARD_PAGE = 100
#: 分页上限。496/100 ≈ 5 页，给到 12 页足够且不会失控。
_BOARD_MAX_PAGES = 12


@dataclass(frozen=True)
class Board:
    """一个板块的当前快照。

    Attributes:
        pct: 涨跌幅（%）。
        main_inflow: 主力净流入，单位**元**。
        advance/decline: 板块内上涨 / 下跌的个股数。
        leader: 领涨股名称。接口偶尔为空，由上层记 warning。
    """

    code: str
    name: str
    pct: float
    main_inflow: float
    advance: int
    decline: int
    leader: str | None


@dataclass(frozen=True)
class BoardResult:
    kind: str
    total: int
    boards: list[Board]
    raw: dict[str, Any]

    @property
    def nonzero_count(self) -> int:
        """涨跌幅非零的板块数。

        🔴 **全为 0 不等于「所有板块都平盘」，而是「这一天还没开始」。**

        实测 2026-09-21（周一）08:48 盘前：本端点 496 行全部 `pct=0.0`、
        `主力净流入=0.0`、`领涨股=None`；而同一时刻腾讯行情与新浪日线
        **仍然保留上一交易日的数据**（3911.87 / 20260918）。

        ⇒ 同一时刻，不同端点对「新一天还没开始」的表现是**相反**的。
          一个保留旧值，一个清零。

        更危险的是榜单按涨跌幅排序：全 0 时「第一名」是任意的一行，
        照着它说「今日领涨板块是 X」完全是编造。
        由上层据此记 `missing`，**不要当成平盘**。
        """
        return sum(1 for b in self.boards if b.pct != 0.0)


def fetch_boards(kind: str) -> BoardResult:
    """取一个板块榜（行业 / 概念），按涨跌幅降序。

    ⚠️ **这个端点不带任何日期字段**，与涨跌家数同款 ——
    它的 `as_of` 只能由调用方结合交易日推断，上层必须为此记一条 warning。

    ⚠️ 形状陷阱：同一主机的 `ulist` 返回 ``data.diff`` 是**数组**，
    而这里是**字典**（键为 "0","1",…）。两处都写成数组解析，
    第二处会静默拿到空列表 —— 于是「今天没有板块上涨」。
    """
    if kind not in BOARD_KINDS:
        raise ValueError(f"未知的板块榜 {kind!r}，可选 {sorted(BOARD_KINDS)}")

    rows: list[dict[str, Any]] = []
    total = 0
    pages: list[dict[str, Any]] = []
    for pn in range(1, _BOARD_MAX_PAGES + 1):
        qs = urllib.parse.urlencode({
            "pn": pn, "pz": _BOARD_PAGE, "po": 1, "fltt": 2, "fid": "f3",
            "fs": BOARD_KINDS[kind],
            "fields": "f12,f14,f3,f62,f104,f105,f204",
        }, safe="+:")
        errors: list[str] = []
        payload: dict[str, Any] | None = None
        for host in _BREADTH_HOSTS:
            try:
                payload = get_json(f"https://{host}{_CLIST_PATH}?{qs}", referer=_REFERER)
                break
            except SourceError as e:
                errors.append(f"{host}: {e}")
        if payload is None:
            raise SourceError(
                f"boards/{kind} 第 {pn} 页: 全部备选主机失败 —— " + " | ".join(errors))
        if payload.get("rc") != 0:
            raise SourceError(f"boards/{kind} 第 {pn} 页: 接口返回 rc={payload.get('rc')}")

        data = payload.get("data") or {}
        diff = data.get("diff")
        # ⚠️ 形状陷阱见 docstring：这里是 dict，ulist 那边是 list
        page = list(diff.values()) if isinstance(diff, dict) else (diff or [])
        total = int(data.get("total") or total)
        pages.append(payload)
        rows.extend(page)
        if not page or len(rows) >= total:
            break

    if not rows:
        raise SourceError(f"boards/{kind}: 一行都没取到")
    if total and len(rows) < total:
        raise SourceError(
            f"boards/{kind}: 接口声称 total={total} 但翻完 {len(pages)} 页只拿到 "
            f"{len(rows)} 行 —— 分页没取全，涨跌分布会算错")
    payload = {"pages": pages}

    out: list[Board] = []
    for r in rows:
        if r.get("f3") in (None, "-") or r.get("f14") in (None, ""):
            raise SourceError(f"boards/{kind}: 某一行缺名称或涨跌幅（{r}）")
        try:
            out.append(Board(
                code=str(r.get("f12") or ""), name=str(r["f14"]),
                pct=float(r["f3"]),
                main_inflow=float(r.get("f62") or 0.0),
                advance=int(r.get("f104") or 0), decline=int(r.get("f105") or 0),
                leader=(str(r["f204"]) if r.get("f204") not in (None, "", "-") else None),
            ))
        except (TypeError, ValueError) as e:
            raise SourceError(f"boards/{kind}: 数值解析失败 {r} —— {e}") from e

    return BoardResult(kind=kind, total=total or len(out), boards=out, raw=payload)
