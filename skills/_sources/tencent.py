"""腾讯行情 —— 成交额，以及一个**自带时间戳**的交叉校验源。

为什么还要第二个行情源
----------------------
新浪日线没有成交额（只有成交量），而「今天两市成交 X 万亿」是交易员实际用的数。
腾讯这条返回里有成交额，**而且带一个 14 位时间戳**（``20260918161402``）——
这让它可以与新浪日线的 ``day`` 对账：两个独立源说的不是同一天时，
market-calc 会把它记成 `missing`，而不是挑一个用。

⚠️ 东财的行情端点为什么没用（实测 2026-09-20）
----------------------------------------------
唯一稳定可达的 ``push2delay`` 主机**给的行情字段是垃圾**::

    最新 = 0.0        涨跌幅 = -29971017728.0        成交额 = 0.0

`rc=0`、字段齐全、类型正确 —— **任何「是不是数字」的检查都会放过它**。
这是 market-calc 守卫 1（点位与涨跌幅的量级校验）存在的直接原因。

字段位置（按 ``~`` 分割，实测 88 段）::

    [1] 名称   [2] 代码   [3] 最新   [4] 昨收   [6] 成交量(手)
    [30] 时间戳 YYYYMMDDHHMMSS        [37] 成交额(万元)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .http import SourceError, get_text
from .tradetime import CN_TZ

__all__ = ["IndexQuote", "fetch_index_quote", "TENCENT_SYMBOLS"]

_BASE = "https://qt.gtimg.cn/q="
_REFERER = "https://gu.qq.com/"

TENCENT_SYMBOLS = {"sh": "sh000001", "sz": "sz399106"}

_LINE = re.compile(r'v_([a-z]{2}\d{6})="([^"]*)"')
#: 最小段数。实测 88 段，取 38 是因为我们最远只读到 [37]；
#: 卡死在 88 会让对方多加一个字段就误报。
_MIN_PARTS = 38


@dataclass(frozen=True)
class IndexQuote:
    """一个指数的实时行情快照。

    Attributes:
        quoted_at: 🔴 **数据自己声明的时刻** ``YYYYMMDDHHMMSS``。
            这是本源存在的主要理由 —— 另外两个源一个没日期、一个只有交易日。
        volume_hand: 成交量，单位**手**（≠ 新浪日线的股，差 100 倍）。
        amount_wan: 成交额，单位**万元**。
    """

    code: str
    name: str
    last: float
    prev_close: float
    volume_hand: int
    amount_wan: float
    quoted_at: str
    raw: str

    @property
    def trade_date(self) -> str:
        return self.quoted_at[:8]

    @property
    def server_as_of(self) -> datetime | None:
        """**服务端自己声明的时刻**；`None` = 这个端点不带日期。

        统一接口的理由见 `_sources/__init__.py` 顶部的「F16」一节：
        由端点自己声明，调用方就不必逐处判断「这个源有没有日期」——
        而那个判断一旦分散，就必然有某一处判错（F4 就是这么来的）。
        """
        return self.quoted_dt

    @property
    def quoted_dt(self) -> datetime:
        """时间戳解析成北京时间。

        🔴 解析放在这里、不放在调用方：`quoted_at` 的格式是本模块的知识，
        每个消费方各写一遍 `strptime` 就是 L-3 的形状。
        时区固定 `Asia/Shanghai` —— 源给的本来就是北京时间。
        """
        return datetime.strptime(self.quoted_at, "%Y%m%d%H%M%S").replace(tzinfo=CN_TZ)


def fetch_index_quote(codes: Sequence[str]) -> dict[str, IndexQuote]:
    """取一批指数行情。返回 ``{code: IndexQuote}``。

    Raises:
        SourceError: 不可用、少返回了某个代码、段数不足或时间戳非法。
    """
    if not codes:
        raise ValueError("codes 不能为空")

    body = get_text(f"{_BASE}{','.join(codes)}", referer=_REFERER, encoding="gbk")
    found = dict(_LINE.findall(body))
    if not found:
        raise SourceError(f"tencent:quote: 返回里没有任何 v_<code> 段（前 200 字符）{body[:200]!r}")

    missing = [c for c in codes if c not in found]
    if missing:
        # 少返回一个代码就悄悄少一个市场的成交额 —— 合计数会小一半而不会报错。
        raise SourceError(f"tencent:quote: 请求了 {list(codes)}，返回里缺 {missing}")

    out: dict[str, IndexQuote] = {}
    for code in codes:
        parts = found[code].split("~")
        if len(parts) < _MIN_PARTS:
            raise SourceError(
                f"tencent:quote/{code}: 只有 {len(parts)} 段，"
                f"少于需要的 {_MIN_PARTS} —— 接口字段布局可能变了")
        quoted_at = parts[30].strip()
        if len(quoted_at) != 14 or not quoted_at.isdigit():
            raise SourceError(
                f"tencent:quote/{code}: 时间戳不是 14 位数字（{quoted_at!r}）—— "
                "而它是本源唯一的日期依据")
        try:
            out[code] = IndexQuote(
                code=code, name=parts[1],
                last=float(parts[3]), prev_close=float(parts[4]),
                volume_hand=int(float(parts[6])), amount_wan=float(parts[37]),
                quoted_at=quoted_at, raw=found[code],
            )
        except (TypeError, ValueError) as e:
            raise SourceError(f"tencent:quote/{code}: 数值解析失败 —— {e}") from e

    return out
