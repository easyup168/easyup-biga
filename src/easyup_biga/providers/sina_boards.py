"""新浪板块排行 —— `cn.sector.board_snapshot` 的 **FALLBACK**。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：行业 / 概念板块的**涨跌幅排行**与领涨股
- **不覆盖**：主力净流入、板块内涨跌家数 —— 这个源不给，见下

🔴 为什么是它
-------------
同机另一套长期运行的实例的代码里写着（原样转述其判断）：

> 主数据源：新浪 `newFLJK.php`（175 个概念板块，免费无限制）
> 备用数据源：东财 push2（500 个概念板块，**部分环境被拒**）

也就是说在**一个每天跑的生产系统**里，这两者的主备关系与本仓库当前的
正好**相反** —— 它把新浪放主源，东财放备胎，理由是东财会被拒。
2026-09-26 我们撞上的正是「被拒」（`push2` 整组 502）。

⚠️ 口径差异 —— **不等价，必须知道少了什么**
--------------------------------------------
| | 东财 `clist`（主源） | 本适配器 |
|---|---|---|
| 概念板块数 | ~500 | **175** |
| 行业板块数 | ~86 | 84 |
| 涨跌幅排行 | ✅ | ✅ |
| 领涨股 | ✅ | ✅ |
| **主力净流入** | ✅ | ❌ **不给** |
| **板块内涨跌家数** | ✅ | ❌ **不给** |

🔴 缺主力净流入**不是静默降级** —— 消费方（`sector-calc`）已经为此写过
一条专门的 `missing[]`：

> 主力净流入 —— N/N 个板块接口未给该字段，**排名不成立**

那条是外部评审 F6 逼出来的（「资金字段可以独立于 pct 失效，
而『第一名』读起来毫无破绽，带着 0.0 亿直接上卡」）。
本适配器正好落进它已经准备好的那条路 —— **降级会被看见，不会被吞掉**。

字段布局（逐位实测对拍过）
--------------------------
响应是 GBK 的 `var S_Finance_bankuai_xxx = {…}`，每个值是逗号分隔的字符串：

```
gn_hwqc,华为汽车,97,25.049,-0.596,-2.3234,1486461364,28685781469,sz000829,2.870,9.320,0.260,天音控股
   0      1      2     3      4       5         6           7          8      9     10    11     12
```

| 位 | 含义 |
|---|---|
| 0 / 1 | 板块代码 / 名称 |
| 2 | 成分股数 |
| 5 | **板块涨跌幅（%）** |
| 6 / 7 | 成交量 / 成交额 |
| 8 / 12 | 领涨股代码 / **名称** |
| 9 / 10 / 11 | 领涨股涨幅% / 现价 / 涨跌额 |

⚠️ 第 9~11 位的顺序**不是**「价、涨跌额、涨幅」那种直觉顺序。
2026-09-26 拿两只龙头股与新浪实时行情逐位对拍确认：
`sz000829` 昨收 9.06 / 现价 9.32 ⇒ 涨幅 2.870%、涨跌额 0.260 ——
与第 9、11 位**逐位吻合**。猜错这个顺序不会报错，只会让领涨股的涨幅变成价格。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .eastmoney import Board, BoardResult
from .http import SourceError, throttle

__all__ = ["SINA_BOARDS_URL", "BOARD_KINDS", "parse_boards", "fetch_boards"]

SINA_BOARDS_URL = "https://money.finance.sina.com.cn/q/view/newFLJK.php"
_REFERER = "https://finance.sina.com.cn"

#: 本仓库的 kind → 新浪的 `param`。
#: ⚠️ 只登记我们真的消费的两种。新浪还有 `area`（地域，31 个），
#:    没有消费方就不登记 —— 那是 L-1 的形状。
BOARD_KINDS = {"industry": "industry", "concept": "class"}

#: 一行至少要有这么多字段才谈得上解析。领涨股那几位可能缺。
_MIN_PARTS = 6
_VAR_PATTERN = re.compile(r"var\s+\w+\s*=\s*(\{.*\})", re.S)


def _f(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_boards(text: str, kind: str) -> BoardResult:
    """把一次响应解析成板块快照。**不联网**，能对着已存的 raw 重放。"""
    if kind not in BOARD_KINDS:
        raise ValueError(f"未知 kind {kind!r}，可用：{sorted(BOARD_KINDS)}")
    match = _VAR_PATTERN.search(text)
    if match is None:
        raise SourceError(
            f"sina boards/{kind}: 响应里没有 `var … = {{…}}` 赋值 —— "
            "要么被限流返回了别的页面，要么上游改了输出形状")
    try:
        payload: Any = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SourceError(f"sina boards/{kind}: 赋值右边不是 JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise SourceError(f"sina boards/{kind}: 板块表为空")

    boards: list[Board] = []
    for raw_row in payload.values():
        parts = str(raw_row).split(",")
        if len(parts) < _MIN_PARTS:
            raise SourceError(
                f"sina boards/{kind}: 某行只有 {len(parts)} 个字段"
                f"（至少要 {_MIN_PARTS}）—— 上游可能改了布局")
        pct = _f(parts[5])
        if pct is None:
            raise SourceError(
                f"sina boards/{kind}: {parts[1]!r} 的涨跌幅不是数字：{parts[5]!r}")
        leader = parts[12].strip() if len(parts) > 12 else ""
        boards.append(Board(
            code=parts[0].strip(), name=parts[1].strip(), pct=pct,
            # 🔴 这个源**不给**这三个。写 `None` 而不是 0 ——
            #    0 会让「没有资金进出」和「没给这个字段」长得一模一样，
            #    而消费方对前者会照常排名、对后者会上报 missing。
            main_inflow=None, advance=None, decline=None,
            leader=leader or None,
        ))

    return BoardResult(kind=kind, total=len(boards), boards=boards,
                       raw=payload, raw_text=text)


def fetch_boards(kind: str) -> BoardResult:
    """取一类板块的当前排行。

    ⚠️ 响应是 **GBK**。按 UTF-8 解会把板块名变成乱码，而那**不会报错** ——
    只会让「新能源」变成一串问号出现在卡面上。
    """
    import urllib.request

    from .http import UA

    param = BOARD_KINDS[kind] if kind in BOARD_KINDS else None
    if param is None:
        raise ValueError(f"未知 kind {kind!r}，可用：{sorted(BOARD_KINDS)}")
    throttle("sina")
    request = urllib.request.Request(
        f"{SINA_BOARDS_URL}?param={param}",
        headers={"User-Agent": UA, "Referer": _REFERER})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except Exception as exc:  # noqa: BLE001
        raise SourceError(
            f"sina boards/{kind}: {type(exc).__name__}: {exc}") from exc
    return parse_boards(body.decode("gbk", "replace"), kind)
