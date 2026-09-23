"""深交所官方交易日历 —— `cn.trading_calendar` 的 Provider 适配器（批 L）。

为什么是深交所官方 monthList
----------------------------
交易日历要回答的是「某个自然日开不开市」。开工前把能免鉴权拿到的源都探活了一遍
（记录在教程第 38 章的「坑」一节），结论是：

* **新浪 `klc_td_sh.txt`**：可达，但返回的是**加密串**，akshare 要靠一个 JS 引擎
  （`py_mini_racer` 跑一段混淆过的 `hk_js_decode`）才能解出来 —— 本项目的口径是
  「薄适配器、不引重依赖」，一个 JS 解释器进不来。
* **东财 `RPTA_WEB_TRADE_DATE`**：可达，但数据脏（把周日也列成交易日、无未来、
  尾部塞 `20311231` 哨兵行），建不出可靠日历。
* **timor.tech 放假 API**：可达，但它是**办公日历**不是**交易所日历** —— 调休上班的
  周末它标成工作日，而交易所那天**不开市**（用新浪日线实测核实：2026-02-14 等调休
  日 `traded=False`）。照它算会把非交易日当成交易日，正是最危险的静默错。
* **深交所官方 monthList**：一手官方源、结构化 JSON、每月每天带交易标志、缺日/未发布
  当场抛错 —— 判据最清楚。选它。

  这也与仓库对 `a-stock-data`（Provider Catalog / 接口参考）的既定定位一致：它的
  `trading_calendar` 用的正是这个端点（`jyrq`/`jybz` 字段形状抄自它）。

🔴 已知的部署约束（诚实写出来，别让后人白排查）
------------------------------------------------
`www.szse.cn` 从**本项目当前唯一的部署环境**（WSL）**连不通** —— TCP 能握手、随后
挂死（HTTPS/HTTP 都试过，45s 超时）。这是这台机器到交易所站点的网络事实，不是本
适配器的 bug。后果与影响面：

* `fetch_trading_calendar`（联网那层）在本环境跑必然 `SourceError`；
* 于是 `fact_trading_calendar` 在本环境保持空表；
* 于是 `market_is_open()` **回退到 weekday 判据**（见 `tradetime.py`）——
  这是朝安全方向的退化（红线 R-3：算不出来就说算不出来，宁可多报 missing）。

⇒ 本适配器的**解析层**（`parse_trading_calendar`，纯函数）由离线 fixture 完整测到
（探针 P1）；**落库链**（`refresh_trading_calendar`）由注入桩 fetcher 离线测到
（探针 P2）。真实抓取会在**能连通深交所的运行环境**里把日历填进来，届时
`market_is_open()` 自动从回退切换到查表 —— 消费方（`market_is_open`）与它的读取
关系是真实且被测的（不是 L-1 的「零消费方」），只是数据的**写入**取决于网络可达性。

分层（照 `sina.py` 的既定形状）
-------------------------------
* `parse_trading_calendar`：**不联网的纯函数**，负责把响应解析成 `TradingCalendar`，
  能对着已存的 raw 重放（同一判据只有一份实现，L-3）。
* `fetch_trading_calendar`：**联网的薄函数**，只负责拼 URL、取文本、交给纯函数。
* `refresh_trading_calendar`：抓取 → 原样落 raw → 归一化进 `fact_trading_calendar`。
  🔴 **不接 `SnapshotCoordinator`**：那套解决的是「同一次决策运行内多个 Specialist
  必须看同一份易变网络数据」，交易日历是低频更新的只读参考表，不是那个形状。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from _contract import now_cn

from .http import SourceError, get_json_and_text

__all__ = ["CalendarDay", "TradingCalendar", "fetch_trading_calendar",
           "parse_trading_calendar", "refresh_trading_calendar",
           "SZSE_CALENDAR_URL"]

#: 深交所「按月」交易日历端点。免鉴权。
SZSE_CALENDAR_URL = ("https://www.szse.cn/api/report/exchange/"
                     "onepersistenthour/monthList")
_REFERER = "https://www.szse.cn/"


@dataclass(frozen=True)
class CalendarDay:
    """某一个自然日开不开市。`date` 已归一化成 ``YYYYMMDD``（与 `DailyBar.day` 同口径）。"""

    date: str
    is_open: bool


@dataclass(frozen=True)
class TradingCalendar:
    """一个自然月的完整交易日历。

    `days` 覆盖该月**每一个自然日**（含休市日 `is_open=False`），完整性由
    `parse_trading_calendar` 在解析时校验 —— 缺日就抛错，构造不出一个「半份」日历。
    """

    year: int
    month: int
    days: tuple[CalendarDay, ...]
    raw: Any
    #: 🔴 数据源发来的**原始响应文本**（`get_json_and_text` 交出的那段），一路带到
    #: `save_raw_snapshot(raw_text=...)`；`content_sha256` 基于它算（批 I）。
    #: `None` = 不是从网络响应来的（对切片/重建的 raw 重放时没有原文）。
    raw_text: str | None = None

    #: 🔴 **这个端点不带「这份日历发布于何时」的时刻** —— 它给的是整月的开/休标志，
    #: 没有一个单一的服务端时刻可言。显式写出 `None`（不是不实现：不实现会让人以为漏了）。
    #: 调用方（`refresh_trading_calendar`）据此走 `server_as_of or now_cn()`，
    #: 用取回时刻当 as_of —— 与 `BreadthResult` / `BoardResult` 同一约定（F16）。
    #: （非注解 ⇒ 是类属性而不是 dataclass 字段，不进 __init__。）
    server_as_of = None

    @property
    def open_days(self) -> tuple[str, ...]:
        """该月的交易日（``YYYYMMDD``），升序。"""
        return tuple(d.date for d in self.days if d.is_open)


def _month_last_day(year: int, month: int) -> int:
    """该月有多少天 —— 不 `import calendar`（本文件同名，避免任何解析歧义）。"""
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (first_next - timedelta(days=1)).day


def _normalize_date(jyrq: Any) -> str:
    """把深交所 `jyrq` 归一化成 ``YYYYMMDD``，接受 ``-`` / ``/`` 分隔或无分隔。"""
    s = str(jyrq).strip()[:10].replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        raise SourceError(f"szse:calendar: 无法解析交易日期 {jyrq!r}")
    return s


def parse_trading_calendar(
    payload: Any, *, year: int, month: int, raw_text: str | None = None
) -> TradingCalendar:
    """把深交所 monthList 的原始响应解析成 `TradingCalendar`。

    🔴 **纯函数，不联网。** 抽出来是为了能对着已存的 raw 重放，不必第二次实现同一套
    解析（L-3）。字段形状抄自 `a-stock-data` 的 `trading_calendar`：
    ``data[]`` 每条含 ``jyrq``（交易日期）与 ``jybz``（交易标志：``"1"`` 开市 /
    ``"0"`` 休市）。

    🔴 **完整性 fail-closed（红线 R-3）**：该月**每一个自然日**都必须在响应里出现一次，
    否则抛错。理由是深交所对未发布/未排定的月份会返回空或残缺，而「缺了几天」若被
    静默接受，就会把没数据的那几天当成**休市**（`is_trading_day` 查无此行会回退，但
    若这里放行一份残月、休市日和「没数据」就混成一谈了）。宁可整月拒绝，也不给半份。

    Raises:
        SourceError: 形状不对、`jybz` 非 0/1、日期无法解析、含重复日期、或该月不完整。
    """
    # 深交所有些报表端点顶层是 `[{...}]`，有些是 `{...}` —— 两种都接，
    # 都不是就 fail-closed（不猜）。
    obj = payload
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        obj = obj[0]
    if not isinstance(obj, dict):
        raise SourceError(
            f"szse:calendar/{year}-{month}: 顶层既不是对象也不是单元素数组，"
            f"而是 {type(payload).__name__}")

    data = obj.get("data")
    if not isinstance(data, list) or not data:
        raise SourceError(
            f"szse:calendar/{year}-{month}: 未返回 data 数组 —— "
            "该月可能未发布，不能据此推断全月休市（fail-closed）")

    days: list[CalendarDay] = []
    seen: set[str] = set()
    for rec in data:
        if not isinstance(rec, dict):
            raise SourceError(f"szse:calendar/{year}-{month}: data 里有非对象行 {rec!r}")
        jybz = str(rec.get("jybz"))
        if jybz not in ("0", "1") or not rec.get("jyrq"):
            raise SourceError(
                f"szse:calendar/{year}-{month}: 交易标志/日期字段异常 "
                f"（jybz={rec.get('jybz')!r} jyrq={rec.get('jyrq')!r}）")
        d = _normalize_date(rec["jyrq"])
        if d in seen:
            raise SourceError(f"szse:calendar/{year}-{month}: 含重复日期 {d}")
        seen.add(d)
        days.append(CalendarDay(date=d, is_open=(jybz == "1")))

    last = _month_last_day(year, month)
    expected = {f"{year:04d}{month:02d}{day:02d}" for day in range(1, last + 1)}
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise SourceError(
            f"szse:calendar/{year}-{month}: 日历不完整或月份错位 —— "
            f"缺 {missing[:5]}{'…' if len(missing) > 5 else ''}"
            f" 多 {extra[:5]}{'…' if len(extra) > 5 else ''}；不能继续（fail-closed）")

    days.sort(key=lambda x: x.date)
    return TradingCalendar(year=year, month=month, days=tuple(days),
                           raw=payload, raw_text=raw_text)


def fetch_trading_calendar(year: int, month: int) -> TradingCalendar:
    """取深交所某个自然月的交易日历。

    Raises:
        ValueError: `month` 不在 1–12。
        SourceError: 不可达、形状不对、或该月不完整（见 `parse_trading_calendar`）。
    """
    if not isinstance(year, int) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"year/month 必须是整数、month∈[1,12]，收到 {year!r}/{month!r}")
    # 深交所 month 参数不补零（照抄 a-stock-data 的既定写法）。
    url = f"{SZSE_CALENDAR_URL}?month={year}-{month}"
    payload, raw_text = get_json_and_text(url, referer=_REFERER)
    return parse_trading_calendar(payload, year=year, month=month, raw_text=raw_text)


def refresh_trading_calendar(
    year: int,
    month: int,
    *,
    fetcher: Any = None,
    path: Any = None,
) -> TradingCalendar:
    """抓取某月日历 → 原样落 raw → 归一化进 `fact_trading_calendar`。返回抓到的日历。

    Args:
        fetcher: 真实抓取函数，默认 `fetch_trading_calendar`。
            🔴 可注入的唯一理由是**可测**：离线测试（`conftest.py` 的禁网围栏）传一个
            返回固定 `TradingCalendar` 的桩，就能在不出网的情况下断言落库链正确
            （探针 P2）。与 `SnapshotCoordinator` 注入 `fetcher` 同一招（L-12：只替换
            最外层出网边界，不 mock 被测逻辑本身）。
        path: 库路径，透传给 `_store`（测试指向 tmp 库）。

    🔴 **直接调 `save_raw_snapshot`，不接 `SnapshotCoordinator`**（理由见模块头）。
    `_store` 在函数内惰性 import —— 让 `parse`/`fetch` 两层保持零 `_store` 依赖，
    离线解析测试的 import 图因此是干净的。
    """
    from _store import save_raw_snapshot, save_trading_calendar  # 惰性：见 docstring

    retrieved = now_cn()
    cal = (fetcher or fetch_trading_calendar)(year, month)
    source = f"szse:calendar/{year:04d}-{month:02d}"
    # 日历没有单一「描述时刻」——它描述整月。诚实的 as_of 是「取回这份已公布日历的时刻」：
    # 交易所若补发调整，更晚一次抓取自然得到更晚的 as_of/retrieved_at，读端取最新一条。
    stamp = retrieved.isoformat()
    snapshot_id = save_raw_snapshot(
        source=source, as_of=stamp, retrieved_at=stamp,
        payload=cal.raw, raw_text=cal.raw_text, path=path)
    save_trading_calendar(
        source=source, as_of=stamp, retrieved_at=stamp,
        days=[(d.date, d.is_open) for d in cal.days],
        snapshot_id=snapshot_id, path=path)
    return cal
