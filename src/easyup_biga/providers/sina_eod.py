"""新浪全市场日线 —— `cn.equity.daily_bars` 的 **PRIMARY**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：分页抓「当前在交易的」沪深京 A 股当日截面，翻译成 `EodBar`
- **不覆盖**：交易日判定、有效日推断（`eod_pipeline` 的事）

🔴 为什么它是主源，而东财退成备用
--------------------------------
本项目原来的主源是东财 `push2/clist`。2026-09-26 那组 host 整体 502
（同一时刻 `push2ex` / `push2his` / `datacenter-web` 全 200 ⇒ 是它们的上游故障），
而全市场日线是 EOD 链的核心 —— 它取不到，当天什么都发不出来。

换源的依据不是「那天它挂了」，是两条**长期**证据：

1. **同机另一套长期运行的实例，每天的全市场日线走的就是这个端点**
   （`Market_Center.getHQNodeDataSimple`，`node=hs_a`，`num=500`）。
   它不是「听说能用」，是每天在跑。
2. 公开的 A 股数据源目录（`a-stock-data`）把可用性排序写成
   「优先用腾讯 / 交易所官方等不封 IP 的源；东财接口共用同一套风控，
   IP 被封会成片失联」。我们这次就撞上了成片失联。

⚠️ 口径与东财**不完全等价**，差异写在这里，不当等价替换
-------------------------------------------------------
| | 东财 `clist` | 本适配器 |
|---|---|---|
| 成员 | 板块过滤串（主板/创业/科创/北交）| 行情节点 `hs_a`，**当前在交易的** |
| 自报总数 | 有 ⇒ 「declared_total vs 实收」这道交叉校验有效 | **没有** ⇒ 那道校验退化成恒等式 |
| 成交量单位 | 股 | **股**（实测比值 1.006）—— 但见下方「单位守卫」|
| 停牌 | 出现在结果里、OHLC 为 `"-"` | **不出现** ⇒ 见下 |

🔴 **停牌股不出现在结果里，这不是缺陷，但它改变了下游的含义。**
`cn.security.tradability` 用「在 universe 里、但当日没有价」区分停牌与数据缺失。
东财会把停牌股带回来（价格是 `"-"`），新浪直接不返回。
两者在 tradability 里都归到同一种状态（universe 有、bars 没有），
所以结论不变 —— 但**理由不同**，别把「没返回」读成「源坏了」。

分页与限流
----------
固定用 Simple：**非 Simple 的 `getHQNodeData` 在 `num=500` 时静默截断到 100**。
页大小取 2000（实测依据见 `_PAGE_SIZE` 的注释）⇒ 3 页，串行 + 限流约 10 秒。
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Sequence

from easyup_biga.domain import now_cn

from .eod_bar import EodBar, EodFetchResult
from .http import SourceError, get_text, throttle

__all__ = [
    "SINA_EOD_URL",
    "parse_eod_page",
    "parse_eod_pages",
    "fetch_eod_snapshot",
]

SINA_EOD_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeDataSimple"
)
_REFERER = "https://vip.stock.finance.sina.com.cn/mkt/"
_NODE = "hs_a"
#: 🔴 这个数是**量出来的**，不是抄来的。
#:
#: 同机另一套长期运行的实例用 `num=500`（12 页 / 28 秒）。实测（2026-09-26）：
#:
#: | num | 返回 | 体积 | 耗时 |
#: |---|---|---|---|
#: | 500 | 500 | — | 3.7s |
#: | 2000 | 2000 | ~570 KB | 3.1s |
#: | 6000 | **5568（全市场）** | 1587 KB | 7.2s |
#: | 8000 | 5568 | 1587 KB | 6.0s |
#:
#: ⚠️ `num=8000` 仍然只返回 5568 ⇒ **没有静默截断**（对比：非 Simple 的
#:    `getHQNodeData` 在 `num=500` 时静默截到 100 —— 那种才是危险的）。
#:
#: 取 2000（3 页 ≈ 10 秒）而不是 6000（1 页）：一次 1.6 MB 的请求失败就全丢，
#: 三次中等请求的单次失败面更小。**不是「越少请求越好」，是「单次失败的
#: 代价要可控」。**
_PAGE_SIZE = 2000
#: 全市场约 5600 ⇒ 3 页。上限留足余量，但撞到它是**错误**不是正常结束。
_MAX_PAGES = 20
#: 🔴 **成交量单位守卫的窗口。**
#:
#: 同机另一套长期运行的实例，代码里留着一句用真金白银换来的注释：
#: 「成交股数用 amount/close 推, 不用 volume —— volume 单位跨某日变过」。
#: 也就是说**这个字段的单位在历史上真的漂移过一次**。
#:
#: ⇒ 不写死倍数，改成每次取数都自检：`(amount / close) / volume` 的中位数
#:   应当接近 1（单位是「股」）。若某天上游改回「手」，这个比值会跳到 ~100，
#:   守卫当场 fail closed —— 而不是让成交量静默大 100 倍。
#:
#: ⚠️ 窗口取得宽（0.5 ~ 2.0）是故意的：分子用收盘价、分母是全天成交量，
#:   本来就有 VWAP 与收盘价的偏离（实测当日 p5/p95 = 0.99 / 1.02）。
#:   这道守卫要抓的是**数量级**跳变，不是精度。
_VOLUME_UNIT_WINDOW = (0.5, 2.0)


def _blank(value: Any) -> Any:
    """原样搬运，只把**明确的空值**统一成 `None`。"""
    return None if value in (None, "", "-") else value


def _price(value: Any) -> Any:
    """价格字段：空值之外，**0 也视为没有**（没成交就没有价）。

    🔴 价格和涨跌幅的「0」含义**相反**，必须分开处理：

    | | `0.000` 的含义 |
    |---|---|
    | 开高低收 | 没有成交 ⇒ 缺失 |
    | 涨跌幅 | **平盘** ⇒ 一个真实的值 |

    第一版把 `"0.000"` 写进了统一的空值表，于是**平盘家数恒为 0** ——
    实测抓到：全市场 58 只（首页 2000 只里）的 `changepercent` 就是
    `"0.000"`，它们有成交量、有真实价格，是货真价实的平盘。

    > 一个恒为 0 的计数不会报错，只会让人以为那天市场没有平盘。
    """
    value = _blank(value)
    if value is None:
        return None
    try:
        return None if float(value) <= 0 else value
    except (TypeError, ValueError):
        # 不是数字 ⇒ 原样交给归一化层去炸，别在这里吞掉。
        return value


def parse_eod_page(text: str, *, page_no: int) -> list[EodBar]:
    body = text.strip()
    if not body:
        raise SourceError(f"sina eod page {page_no}: empty response")
    if body in ("null", "[]"):
        return []
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError(f"sina eod page {page_no}: response is not JSON") from exc
    if not isinstance(payload, list):
        raise SourceError(f"sina eod page {page_no}: top level is not a list")

    out: list[EodBar] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SourceError(f"sina eod page {page_no}: row is not an object")
        code = item.get("code")
        if not code:
            raise SourceError(f"sina eod page {page_no}: row missing code: {item!r}")
        # 🔴 `symbol` 形如 `sh600000` / `bj920000` —— 前两位就是**市场**，
        #    这个源是知道的，所以要带出去，不要让下游按代码前缀去猜。
        #    猜的后果不是报错：`000001` 在沪是上证指数、在深是平安银行。
        symbol = str(item.get("symbol") or "")
        market = symbol[:2].lower() if len(symbol) >= 2 else None
        out.append(EodBar(
            symbol=code,
            market_hint=market,
            name=item.get("name"),
            open=_price(item.get("open")),
            high=_price(item.get("high")),
            low=_price(item.get("low")),
            # `trade` 是最新价；收盘后取就是收盘价。
            close=_price(item.get("trade")),
            # `settlement` 是昨收。
            prev_close=_price(item.get("settlement")),
            # 原样搬运。单位由 `parse_eod_pages` 的守卫核对，不在这里换算 ——
            # 写死倍数正是上游改单位那天会静默出错的地方。
            volume=_blank(item.get("volume")),
            amount=_blank(item.get("amount")),
            change_amount=_blank(item.get("pricechange")),
            # ⚠️ **不走 `_price`** —— 涨跌幅的 0 是平盘，是个真实的值。
            change_percent=_blank(item.get("changepercent")),
        ))
    return out


def _assert_volume_unit(rows: Sequence[EodBar]) -> None:
    """核对 `volume` 的单位真的是「股」—— **每次取数都核**。

    🔴 判据是 `(amount / close) / volume` 的**中位数**，不是某一只。
    单只会被大单/异常价带偏；中位数对整页的系统性单位错误敏感，
    对个别脏数据不敏感，正是我们要的方向。

    ⚠️ 不静默换算、也不静默放行。上游哪天把单位改回「手」，
    这里当场抛 —— 而不是让全市场成交量小 100 倍地进库。
    那种错**不会报错**，只会让某天的换手率、量比全部失真。
    """
    ratios: list[float] = []
    for row in rows:
        try:
            volume = float(row.volume)
            amount = float(row.amount)
            close = float(row.close)
        except (TypeError, ValueError):
            continue
        if volume > 0 and amount > 0 and close > 0:
            ratios.append((amount / close) / volume)
    if len(ratios) < 20:
        raise SourceError(
            f"sina eod: 只有 {len(ratios)} 只能核对成交量单位（需要 ≥20）——"
            "样本太少时这道守卫等于没有，宁可失败")
    ratios.sort()
    median = ratios[len(ratios) // 2]
    low, high = _VOLUME_UNIT_WINDOW
    if not low <= median <= high:
        raise SourceError(
            f"sina eod: 成交量单位疑似变了 —— (amount/close)/volume 的中位数 "
            f"= {median:.3f}，预期落在 {low}~{high}（单位为「股」）。"
            f"≈100 说明上游改回了「手」，≈0.01 说明反向。"
            "这不是精度问题，是数量级 —— 不修正就进库会让成交量全盘失真。")


def parse_eod_pages(
    texts: Sequence[str], *, retrieved_at: str | None = None
) -> EodFetchResult:
    rows: list[EodBar] = []
    for page_no, text in enumerate(texts, start=1):
        rows.extend(parse_eod_page(text, page_no=page_no))
    if not rows:
        raise SourceError("sina eod: no rows returned")

    seen: set[str] = set()
    for row in rows:
        key = str(row.symbol)
        if key in seen:
            raise SourceError(f"sina eod: duplicate code {key}")
        seen.add(key)

    _assert_volume_unit(rows)

    return EodFetchResult(
        rows=tuple(rows),
        raw_text=json.dumps(list(texts), ensure_ascii=False),
        retrieved_at=retrieved_at or now_cn().isoformat(),
        # ⚠️ 这个源不自报总数 ⇒ 只能填实收行数，那道交叉校验因此失效。
        declared_total=len(rows),
        # 快照型端点，给不出业务日 ⇒ 只能当日收盘后发布。
        effective_trade_date=None,
        provider_id="sina",
    )


def _page_url(page_no: int, page_size: int) -> str:
    query = urllib.parse.urlencode({
        "page": page_no,
        "num": page_size,
        "sort": "symbol",
        "asc": 1,
        "node": _NODE,
        "_s_r_a": "srt",
    })
    return f"{SINA_EOD_URL}?{query}"


def fetch_eod_snapshot(
    *,
    page_size: int = _PAGE_SIZE,
    max_pages: int = _MAX_PAGES,
) -> EodFetchResult:
    """串行翻页抓当日全市场截面。

    ⚠️ 终止条件是「**这一页不满**或空」，不是「翻够 N 页」——
    这个源不告诉你总数，写死页数会在上市数量变化时静默截断。
    撞到 `max_pages` 是**错误**，不是正常结束。
    """
    if page_size < 1 or page_size > _PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    texts: list[str] = []
    for page_no in range(1, max_pages + 1):
        throttle("sina")
        text = get_text(_page_url(page_no, page_size), referer=_REFERER)
        rows = parse_eod_page(text, page_no=page_no)
        if not rows:
            break
        texts.append(text)
        if len(rows) < page_size:
            break
    else:
        raise SourceError(
            f"sina eod: 翻到第 {max_pages} 页仍然是满页 —— "
            "要么上游行为变了，要么分页参数不对；不静默截断全市场")

    if not texts:
        raise SourceError("sina eod: first page is empty")
    return parse_eod_pages(texts, retrieved_at=now_cn().isoformat())
