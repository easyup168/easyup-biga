"""新浪日线 —— market-calc 的**脊梁**。

为什么是脊梁而不是只做量能基线
------------------------------
它一次同时给出三样东西，而且三样都自洽：

1. **权威交易日** —— 每一行自带 ``day``。market 的其余两个源
   （腾讯行情、东财涨跌家数）一个只有时间戳、一个**完全没有日期**，
   交易日必须由这里说了算。
2. **点位与涨跌幅** —— ``close`` 与前一根的 ``close``，同源相除，不跨源。
3. **量能基线** —— 20 根历史 ``volume``，与今日量**同一个单位**。

🔴 单位陷阱（实测 2026-09-20）
------------------------------
本接口的 ``volume`` 单位是**股**，腾讯行情的成交量单位是**手**::

    新浪 sh000001 20260918  volume = 48,571,250,700   （股）
    腾讯 sh000001 20260918  成交量  =    485,712,507   （手）
    485,712,507 × 100 = 48,571,250,700                 ✅ 完全相等

⇒ 量能基线与今日量必须取自**同一个源**。跨源做比值 = **静默错 100 倍**，
   而 100 倍的量能比看起来只是「今天爆量」，不会有任何报错。
⇒ 反过来，这个恒等式成了一条免费的双源一致性校验（market-calc 守卫 4）。

⚠️ 东财的 kline 端点为什么没用
------------------------------
实测 ``push2his`` 的 ``lmt=25`` **被忽略**，它返回了全历史（一次 483KB 后被截断），
而且同一时刻该主机会被服务端直接断连。新浪这个端点的 ``datalen`` 是真生效的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _contract import now_cn

from .http import SourceError, get_json_and_text
from .tradetime import as_of_for_trade_date

__all__ = ["DailyBar", "IndexDaily", "fetch_index_daily", "parse_index_daily", "SINA_SYMBOLS"]

_BASE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData")
_REFERER = "https://finance.sina.com.cn"

#: 🔴 出处标识由**数据源模块自己**声明（批 R，评审 E-20.2）。
#:   以前 `SnapshotCoordinator` 里写死 `f"sina:kline/{symbol}"`，而 fetcher 是可注入的
#:   —— 换一个 provider 进去，raw 层照样记「sina」。那是一条**会说谎的出处**：
#:   落库的 source 描述的是调用方的假设，不是数据真正的来源。
PROVIDER_ID = "sina"
#: 解析口径版本。改了 `parse_index_daily` 的字段映射/校验就要升它，
#: 否则没法清点「哪些历史快照是用旧解析读出来的」。
ADAPTER_VERSION = "1"

#: 本项目用到的指数。深证用**综指**(399106) 而不是成指(399001)：
#: 综指覆盖整个深市，与涨跌家数、成交额的口径一致。
SINA_SYMBOLS = {"sh": "sh000001", "sz": "sz399106"}

_REQUIRED = ("day", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class DailyBar:
    """一根日线。`day` 已归一化成 ``YYYYMMDD``，`volume` 单位是**股**。"""

    day: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class IndexDaily:
    symbol: str
    bars: list[DailyBar]
    raw: list[dict[str, Any]]
    #: 🔴 数据源发来的**原始响应文本**（`get_json_and_text` 交出的那段），一路带到
    #: `save_raw_snapshot(raw_text=...)`；`content_sha256` 基于它算（批 I）。
    #: `None` = 这份 IndexDaily 不是从网络响应来的（`parse_index_daily` 对**切片后
    #: 的冻结 raw** 重建时就没有原文可言）——那条路径不落 raw，所以留空无害。
    raw_text: str | None = None
    #: 出处三件套（批 R）——由本模块声明并一路带到 raw 层，见 PROVIDER_ID 的注释。
    provider_id: str = PROVIDER_ID
    adapter_version: str = ADAPTER_VERSION

    @property
    def source(self) -> str:
        """raw 层 `source` 列的值。**唯一实现** —— 调用方不要自己拼这个字符串。"""
        return f"{self.provider_id}:kline/{self.symbol}"

    @property
    def last(self) -> DailyBar:
        return self.bars[-1]

    @property
    def server_as_of(self) -> datetime | None:
        """**服务端自己声明的时刻**；`None` = 这个端点不带日期。

        统一接口的理由见 `_sources/__init__.py` 顶部的「F16」一节：
        由端点自己声明，调用方就不必逐处判断「这个源有没有日期」——
        而那个判断一旦分散，就必然有某一处判错（F4 就是这么来的）。
        """
        return as_of_for_trade_date(self.trade_date, retrieved_at=now_cn())[0]

    @property
    def trade_date(self) -> str:
        return self.bars[-1].day


def fetch_index_daily(symbol: str, *, bars: int = 25) -> IndexDaily:
    """取一个指数最近 `bars` 根日线，按日期升序。

    Args:
        symbol: 新浪代码，如 ``sh000001``。
        bars: 取多少根。20 日均量需要 21 根（20 根基线 + 今日），
            默认多取几根抵消节假日边界 —— 但**不足时不会拿 15 根凑一个均值**，
            由上层记入 `missing[]`。

    Raises:
        SourceError: 不可用、形状不对、或行内缺字段。
    """
    if not symbol or not symbol[:2].isalpha():
        raise ValueError(f"symbol 形如 sh000001 / sz399106，收到 {symbol!r}")

    url = f"{_BASE}?symbol={symbol}&scale=240&ma=no&datalen={int(bars)}"
    payload, raw_text = get_json_and_text(url, referer=_REFERER)
    return parse_index_daily(symbol, payload, raw_text=raw_text)


def parse_index_daily(
    symbol: str, payload: Any, *, raw_text: str | None = None
) -> IndexDaily:
    """把新浪 K 线端点的原始响应（一个 dict 数组）解析成 `IndexDaily`。

    🔴 **纯函数，不联网。** 抽出来是为了让 `SnapshotCoordinator` 能从**已冻结的
    raw**（`raw_market_snapshot.payload_json` 存的就是这个数组）重建 `IndexDaily`，
    而不必第二次实现同一套解析 —— 同一判据只有一份实现（L-3）。两条路径共用
    同一套形状校验与升序/去重断言：`fetch_index_daily` 拿网络响应后调它，
    读冻结快照的路径对**切片后的** raw 调它，因此两边对「什么样的日线算合法」
    永远给出同一个答案。

    `raw_text`：只有 `fetch_index_daily`（网络路径）能给出原始响应文本；读冻结快照
    的路径拿的是**切片后的解析对象**，没有对应的原文，`raw_text` 留 `None`（那条路径
    本来就不落 raw，见 `IndexDaily.raw_text`）。

    ⚠️ raw 本身就是升序、无重复日期（否则 `fetch_index_daily` 当初落库前
    就抛错了）；对它取末尾 N 行（切片）仍然升序、无重复，所以切片后重解析
    不会新触发这两条断言。

    Raises:
        SourceError: 形状不对、行内缺字段、日期无法解析、未升序或含重复日期。
    """
    if not isinstance(payload, list):
        raise SourceError(f"sina:kline/{symbol}: 返回不是数组，而是 {type(payload).__name__}")
    if not payload:
        raise SourceError(f"sina:kline/{symbol}: 返回空数组 —— 无法区分「没有数据」与「代码写错了」")

    out: list[DailyBar] = []
    for row in payload:
        missing = [k for k in _REQUIRED if row.get(k) in (None, "")]
        if missing:
            raise SourceError(f"sina:kline/{symbol}: 某一行缺字段 {missing}（{row}）")
        day = str(row["day"])[:10].replace("-", "")
        if len(day) != 8 or not day.isdigit():
            raise SourceError(f"sina:kline/{symbol}: 无法解析日期 {row['day']!r}")
        try:
            out.append(DailyBar(
                day=day,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=int(float(row["volume"])),
            ))
        except (TypeError, ValueError) as e:
            raise SourceError(f"sina:kline/{symbol}: 数值解析失败 {row} —— {e}") from e

    days = [b.day for b in out]
    if days != sorted(days):
        # 顺序错了，「最后一根 = 最新交易日」这个前提就不成立 ——
        # 而它是整个 market-calc 的交易日来源。宁可报错也不排序后继续。
        raise SourceError(f"sina:kline/{symbol}: 返回未按日期升序，最后一根不能当作最新交易日")
    if len(set(days)) != len(days):
        raise SourceError(f"sina:kline/{symbol}: 返回含重复日期，均量会被污染")

    return IndexDaily(symbol=symbol, bars=out, raw=payload, raw_text=raw_text)
