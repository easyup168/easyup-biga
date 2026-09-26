"""6 位代码 → 交易所 / 板块 —— **唯一**的那份号段表。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：一个代码（可选地带上 provider 声明的市场）是不是 **A 股个股**，
  以及它属于哪个交易所、哪个板块
- **不覆盖**：基金 / 债券 / 指数 / B 股的分类。它们一律返回 `None`
  —— 本仓库只做 A 股个股，认不出就 fail closed

🔴 为什么要收成一份
-------------------
这个判断在本仓库曾经有**三份**手写实现
（`security_master._exchange_and_board`、`eod_daily_bars._inst`、
`tradability._provider_iid`，后两者逐字相同）。

同机另一套长期运行的实例为同一件事做过一次普查：同一个判断曾有二十多处
独立实现、其中大半是错的。它的失败形状不是崩溃，是**静默取到另一只真标的**
——沪深号段有重叠，最有名的是 `000001`：

    上证指数（沪）  vs  平安银行（深）

2026-09-26 接通达信盘后包时，这件事从「理论风险」变成了**实测**：
那个包按市场分文件，同一份数据里 **840 个代码在两个市场都存在**。
把它们按代码拍平，`000001` 会拿到上证指数的 3888 点当股价。

🔴 两条铁律
-----------
1. **`market` 给了就必须一致。** provider 知道市场时（文件名/字段里写着），
   它说了算；号段规则只是**没人告诉你时**的回退。
   不一致 ⇒ 这个代码在那个市场上不是个股（`sh000001` 就是这么被挡掉的）。
2. **认不出返回 `None`，绝不默认某个市场。** 猜一个出来的后果不是报错，
   是把基金 / 债券 / 板块指数静默混进股票 universe。

⚠️ 用**两位**号段，不枚举三位
-----------------------------
实测踩过：创业板只枚举了 `300` / `301`，而 `302132`（中航成飞）真实在交易
⇒ `cn.security_master` 的第一次真实同步**整体失败**。
交易所的号段分配本来就是两位粒度的，枚举三位是在追一个会变的东西。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Segment", "a_share_segment", "is_a_share"]


@dataclass(frozen=True, slots=True)
class Segment:
    exchange: str
    board: str


#: 顺序即优先级。⚠️ BSE 必须排在最前：`92` 会被后面任何以 `9` 开头的规则吃掉。
#:
#: 🔴 **`88` 不在这里。** 它曾被写进北交所那一组，而实测（2026-09-24 的
#:    通达信盘后包）`88xxxx` 共 1120 条、**全部出现在 `sh` 文件里**，
#:    是通达信自编板块指数（`880001 总市值`、`888880 新标准券`）。
#:    真实北交所个股今天全是 `92` 段（347 只），`43`/`83`/`87` 已无在册标的
#:    但保留作历史兼容。
_SEGMENTS: tuple[tuple[tuple[str, ...], Segment], ...] = (
    (("92", "43", "83", "87"), Segment("BSE", "BSE")),
    (("688", "689"), Segment("SSE", "STAR")),
    (("60",), Segment("SSE", "SSE_MAIN")),
    (("30",), Segment("SZSE", "CHINEXT")),
    (("00",), Segment("SZSE", "SZSE_MAIN")),
)

#: provider 声明的市场 → 本仓库的交易所代码。
#:
#: 🔴 **只收无歧义的文本标识。** 数字编码故意不在这里：
#:    东财的 `f13` 用 `0` 同时表示**深市和北交所**（`430047` 这类老
#:    北交所代码的 `f13` 就是 0）。把它当权威会让整个北交所被判成
#:    「代码段与市场冲突 ⇒ 不是 A 股」，而那**不会报错**，
#:    只会让 universe 悄悄少一个交易所。
#:
#:    实测抓到过：接通达信盘后包时给 `_normalize_market` 加了
#:    `0 -> SZSE`，`cn.security_master` 的测试当场红在 `430047` 上。
#:
#: ⇒ 认不出的市场标识一律当作「provider 没说」，回退到号段规则。
#:   有歧义的信息不如没有信息 —— 后者至少不会把人引向错的结论。
_MARKET_ALIASES = {
    "sh": "SSE", "sse": "SSE", "sh.": "SSE",
    "sz": "SZSE", "szse": "SZSE",
    "bj": "BSE", "bse": "BSE", "bse.": "BSE",
}


def _normalize_market(market: object) -> str | None:
    if market in (None, "", "-") or isinstance(market, (int, float, bool)):
        return None
    return _MARKET_ALIASES.get(str(market).strip().lower())


def a_share_segment(code: object, *, market: object = None) -> Segment | None:
    """判定一个代码是不是 A 股个股；是就给出交易所与板块，不是就 `None`。

    Args:
        code: 6 位代码（接受可补零的短码）。
        market: provider **声明**的市场（`sh`/`sz`/`bj`、东财的 `f13` 等）。
            给了它就以它为准 —— 号段规则只是回退。

    ⚠️ 返回 `None` 有两种含义，调用方要区分：
    「这不是股票」（基金/债券/指数）与「这个代码在那个市场上不是股票」
    （`sh000001`）。本函数不区分它们，因为**下一步都一样：跳过**。
    """
    if code in (None, "", "-"):
        return None
    text = str(code).strip()
    if not text.isdigit() or len(text) > 6:
        return None
    text = text.zfill(6)

    declared = _normalize_market(market)
    for prefixes, segment in _SEGMENTS:
        if text.startswith(prefixes):
            # 🔴 provider 说了市场就必须一致 —— 不一致说明它在那个市场上
            #    不是个股（最典型：`sh000001` 是上证指数）。
            if declared is not None and declared != segment.exchange:
                return None
            return segment
    return None


def is_a_share(code: object, *, market: object = None) -> bool:
    return a_share_segment(code, market=market) is not None
