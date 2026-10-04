"""新浪交易日历 —— A 股**含未来**的交易日名单。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：新浪那份压缩日历的**解码**、抓取、归一化落进 `fact_trading_calendar`
- **不覆盖**：「此刻开不开市」的判断（那在 `tradetime.market_is_open`，它查本表）

🔴 为什么非要它：日线反推回答不了「今天」
------------------------------------------
`fact_trading_calendar` 在本项目长期是**空表** —— 原本的生产方走深交所官方端点，
而本机连不通它（见 `szse.py` 模块头）。于是 `market_is_open()` 恒走 weekday 回退。

2026-09-25（中秋）实测撞到后果：六个 agent 里 `news` 按「今天」报交易日 20260925，
日线类报 20260924，`risk` 因交易日不一致 **stance=无法判定** —— 而 20260925
根本不是交易日。系统不知道自己在休市日出卡。

⚠️ **用指数日线反推交易日是不够的**：盘中日线**不含当天** bar（这正是
`phase-2-specialists.md` 记的「盘中日线类报上一交易日」），所以它只能回答过去，
回答不了「今天开不开」—— 而那恰恰是出问题的那个问题。
⇒ 必须要一份**前瞻**日历。实测可达的源只有这一个。

数据源
------
``https://finance.sina.com.cn/realstock/company/klc_td_sh.txt``

实测：1990-12-19 → 2026-12-31，8796 个交易日，**含交易所已公布的未来排期**。
508 字节压 8796 个日期 —— 它是**压缩编码**的，不是明文。

🔴 关于那段解码：为什么自己重写，而不是引一个库
-----------------------------------------------
通行做法是引一个封装库，它把一段 ~17KB 的混淆 JS 丢进 JS 引擎跑。两条都不合适：

* 引库 ⇒ 运行时多出 JS 引擎与 DataFrame 两个重依赖，而我们只要一列日期；
* 把那 17KB 第三方混淆 JS 抄进本仓库 ⇒ 授权与可维护性都是问题（本仓库 Apache-2.0、公开）。

⇒ 读懂它，用本模块下面这几十行把**日历那一支**重写出来。

**它不是「差不多对」：** `tests/test_sina_calendar.py` 拿离线 fixture 解出来的
8796 个日期，与通行实现的解码结果**逐一比对**过，完全相同（那个实现额外手工补了
一天 1992-05-04，是它自己加的，不在编码里）。

🔴 这个编码**不是日历专用**
---------------------------
它是新浪的一族压缩序列格式，同一个位流读取器还承载日 OHLCV、分时、收盘序列等。
格式由流首 12 bit 的 `format id` 分发：

=========  =========================================
`139`      日期数组 —— **本模块实现的就是这一支**
`1479`     日 OHLCV
`3466`     全量 OHLCV + 成交额 + 盘后
`136`      分时（量 / 价 / 均价）
`200`      仅收盘序列
`197`      通用数值矩阵
=========  =========================================

⚠️ **只实现 139。** 其余五支现在没有消费方，预先写出来就是 L-1（建了没人读的东西）。
撞到别的 id 会抛错**并报出那个 id**，将来要加就在 `_DECODERS` 里加一支 ——
位流原语（`_BitStream`）是共用的，已被日历这一支逐字节证明过。
"""

from __future__ import annotations

import datetime
from typing import Any, Callable

from .http import SourceError, get_text

__all__ = ["CALENDAR_URL", "SOURCE", "decode_series", "parse_datelist",
           "fetch_trading_days", "refresh_trading_calendar"]

CALENDAR_URL = "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt"
_REFERER = "https://finance.sina.com.cn/"

#: 落库时写进 `fact_trading_calendar.source` 的标识（与 raw 层同一个字符串）。
SOURCE = "sina:calendar/klc_td_sh"

#: 位流的符号表 —— 标准 base64 字母表，由原实现按 A-Z / a-z / 0-9 / +/ 拼出。
_ALPHABET = ("".join(chr(65 + i) for i in range(26))
             + "".join(chr(97 + i) for i in range(26))
             + "".join(chr(48 + i) for i in range(10)) + "+/")

#: 日期基准：1970-01-01 起第 7657 天 = 1990-12-19（上交所开市日）。
_EPOCH_DAY = 7657
_UNIX_EPOCH = datetime.date(1970, 1, 1)


class _BitStream:
    """6 bit/符号、LSB-first 的位流。

    原实现把「当前符号下标」与「符号内位偏移」两个游标散在闭包里，这里收成一个
    对象 —— 三个读取原语（`bit` / `fields` / `gamma`）全部只通过它推进游标，
    不存在第二处改游标的地方。
    """

    def __init__(self, text: str) -> None:
        #: 每个字符在符号表里的序号；表外字符为 -1（原实现的 indexOf 同语义）。
        self.sym = [_ALPHABET.find(ch) for ch in text]
        self.n = len(self.sym)
        self.i = 0      # 符号下标
        self.off = 0    # 符号内位偏移（0..5）

    @property
    def exhausted(self) -> bool:
        return self.i >= self.n

    def bit(self) -> int:
        """取 1 bit。流尽返回 0（不抛错 —— 编码依赖这个行为收尾）。"""
        if self.exhausted:
            return 0
        got = self.sym[self.i] & (1 << self.off)
        self.off += 1
        if self.off >= 6:
            self.off -= 6
            self.i += 1
        return 1 if got else 0

    def fields(self, widths: list[int], signed: list[int] | None = None) -> list[int]:
        """按位宽依次取若干个整数。`signed[k]` 非 0 ⇒ 该字段按二补码解释。

        ⚠️ 位宽 > 30 的字段拆成 `30 + 余数` 两段再拼 —— 原实现这么做是因为
        JS 的位运算只有 32 bit 有符号。Python 没有这个限制，但**照搬拆法**：
        改写时保持与原编码一致，比"用更自然的写法"重要。
        """
        signed = signed or []
        out: list[int] = []
        for k, width in enumerate(widths):
            if not width:
                out.append(0)
                continue
            if self.exhausted:
                return out
            if width <= 30:
                value, left = 0, width
                while True:
                    take = min(6 - self.off, left)
                    chunk = (self.sym[self.i] >> self.off) & ((1 << take) - 1)
                    value |= chunk << (width - left)
                    self.off += take
                    if self.off >= 6:
                        self.off -= 6
                        self.i += 1
                    left -= take
                    if left <= 0:
                        break
                if k < len(signed) and signed[k] and value >= (1 << (width - 1)):
                    value -= 1 << width
                out.append(value)
            else:
                lo, hi = self.fields([30, width - 30],
                                     [0, signed[k] if k < len(signed) else 0])
                out.append(lo + hi * (1 << 30))
        return out

    def gamma(self) -> int:
        """带符号的一元编码：符号位 + 连续 1 的个数。返回 ±n（n ≥ 1）。"""
        sign = self.bit()
        n = 1
        while self.bit():
            n += 1
        return n * (2 * sign - 1)


def _next_weekday(cursor: int) -> tuple[int, datetime.date]:
    """游标前进一个**工作日**，返回新游标与对应日期。

    `cursor % 7` 落在 3 / 4 时是周末，跳过去。⚠️ 判据是 `cursor % 7` 而不是
    真实星期几 —— 因为基准日 `_EPOCH_DAY` 已经把相位吸收进去了。
    """
    cursor += 1
    phase = cursor % 7
    if phase in (3, 4):
        cursor += 5 - phase
    return cursor, _UNIX_EPOCH + datetime.timedelta(days=_EPOCH_DAY + cursor)


def _decode_datelist(bs: _BitStream, variant: int) -> list[datetime.date]:
    """`format id = 139`：游程编码的交易日名单。

    编码形状：从起始日游标开始，**逐个工作日**往前走；一段连续的交易日用一个
    游程长度表示，游程之间夹着的那个工作日就是休市日（法定节假日）。
    多天连休 ⇒ 连续出现长度为 0 的游程。
    """
    if variant > 1:
        return []
    run_bits = 0
    cursor = bs.fields([18])[0] - 1
    end = bs.fields([18])[0]
    remaining = -1
    days: list[datetime.date] | None = None

    while cursor < end:
        cursor, day = _next_weekday(cursor)
        if remaining <= 0:
            if bs.bit():
                run_bits += bs.gamma()
            remaining = bs.fields([3 * run_bits], [0])[0] + 1
            if days is None:            # 第一段：当天本身算交易日
                days = [day]
                remaining -= 1
        else:
            days.append(day)            # type: ignore[union-attr]
        remaining -= 1
    return days or []


#: `format id` → 解码函数。**只实现日历那一支**，见模块 docstring。
_DECODERS: dict[int, Callable[[_BitStream, int], Any]] = {
    139: _decode_datelist,
}


def decode_series(payload: str) -> Any:
    """解一段新浪压缩序列。格式由流首自述，不由调用方指定。"""
    bs = _BitStream(payload)
    fmt, flags = bs.fields([12, 6])
    variant = 63 ^ flags
    decoder = _DECODERS.get(fmt)
    if decoder is None:
        raise SourceError(
            f"新浪压缩序列：format id {fmt} 还没有实现（variant={variant}）。\n"
            f"  已实现：{sorted(_DECODERS)}。这是一族格式，日历只是其中一支——\n"
            "  要加新的一支，在 sina_calendar.py 的 _DECODERS 里注册，"
            "位流原语（_BitStream）直接复用。")
    return decoder(bs, variant)


def parse_datelist(raw_text: str) -> list[datetime.date]:
    """`var datelist="…";` → 交易日列表。**纯函数，不联网**（离线 fixture 可回放）。"""
    if "=" not in raw_text:
        raise SourceError(f"新浪日历响应不认识（没有 `=`）：{raw_text[:80]!r}")
    payload = raw_text.split("=", 1)[1].split(";")[0].strip().strip('"')
    if not payload:
        raise SourceError("新浪日历响应里 datelist 是空的")
    days = decode_series(payload)
    if not days:
        raise SourceError("新浪日历解出来是空的 —— 不当作『今年没有交易日』")
    return days


def fetch_trading_days() -> tuple[list[datetime.date], str]:
    """抓一次日历。返回 `(交易日列表, 原始响应文本)`。失败抛 `SourceError`。

    🔴 原文一起交出来 —— raw 层的 `content_sha256` 要指向**数据源发来的字节**，
    不是我们解码后再序列化的字节（批 I 的那条）。
    """
    raw_text = get_text(CALENDAR_URL, referer=_REFERER)
    return parse_datelist(raw_text), raw_text


def refresh_trading_calendar(
    *,
    back_days: int = 730,
    fetcher: Callable[[], tuple[list[datetime.date], str]] | None = None,
    now: datetime.datetime | None = None,
    path: Any = None,
) -> tuple[int, str, str]:
    """抓日历 → 原样落 raw → 归一化进 `fact_trading_calendar`。

    返回 `(写入行数, 覆盖起, 覆盖止)`（后两者是 ``YYYYMMDD``）。

    Args:
        back_days: 往回覆盖多少个自然日。默认 730 —— 表里只留「最近两年 + 已
            公布的未来」。**不是全量 1990 年至今**：那是三万多行，而消费方
            （`market_is_open`）只关心近期；写进去的每一行都要能说清为什么在。
        fetcher: 注入点，测试用（离线 fixture）。

    🔴 **必须同时写 `is_open=0` 的行。** 日历源只给交易日名单，若只落交易日，
    `is_trading_day()` 对休市日返回的是 `None`（没覆盖到）而不是 `False` ——
    消费方会回退到 weekday，等于这次刷新白做。所以在覆盖区间内**逐个自然日**
    落一行，在名单里的 `is_open=1`，不在的 `is_open=0`。
    """
    from easyup_biga.domain import now_cn
    from easyup_biga.persistence import save_raw_snapshot, save_trading_calendar

    days, raw_text = (fetcher or fetch_trading_days)()
    known = set(days)
    stamp = (now or now_cn())
    retrieved_at = stamp.isoformat()
    # as_of：这份日历自报覆盖到哪天 —— 它是「这份数据说的话」，不是我们取它的时刻。
    as_of = max(days).strftime("%Y%m%d")

    snapshot_id = save_raw_snapshot(
        source=SOURCE, as_of=as_of, retrieved_at=retrieved_at,
        payload={"trading_days": [d.isoformat() for d in days]},
        raw_text=raw_text, path=path)

    start = stamp.date() - datetime.timedelta(days=back_days)
    end = max(days)
    rows = []
    cur = start
    while cur <= end:
        rows.append((cur.strftime("%Y%m%d"), cur in known))
        cur += datetime.timedelta(days=1)

    written = save_trading_calendar(
        source=SOURCE, as_of=as_of, retrieved_at=retrieved_at,
        days=rows, snapshot_id=snapshot_id, path=path)
    return written, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
