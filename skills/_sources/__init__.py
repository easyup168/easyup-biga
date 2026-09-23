"""BigA 采集层 —— 所有外部数据源的**唯一入口**。

分层依据见 `architecture.md` §5.2：

    collectors（本包）→ raw 层 → indicators（确定性计算）→ derived

本包的唯一职责是「把数据源返回的东西原样拿回来」。
它不算比率、不判断强弱、不填默认值。任何一层的失败都**如实抛出**，
由上层决定进 `missing[]` 还是重试 —— 采集层自作主张填个 0，
上层就永远没有机会知道这里出过问题。

为什么是一个共享包，而不是每个 skill 自己带一份
------------------------------------------------
`emotion-calc` 与 `market-calc` 都要打行情接口，共用的不只是 URL，
更是**重试 / 退避 / 请求头 / 备选主机链**这套判据。
各写一遍就会慢慢漂开，而漂开时不报错 —— 表现只是某个源偶尔多一条 missing。

⇒ 第二个消费方出现的那一刻就是抽取的时刻（裁定 15 的同源理由）。

用法::

    from _sources import fetch_pool, fetch_breadth, SourceError
"""

from .eastmoney import (
    BOARD_KINDS,
    POOL_ENDPOINTS,
    Board,
    BoardResult,
    fetch_boards,
    BreadthResult,
    PoolResult,
    fetch_breadth,
    fetch_pool,
)
from .http import SourceError, get_json, get_json_and_text, get_text
from .sina import SINA_SYMBOLS, DailyBar, IndexDaily, fetch_index_daily, parse_index_daily
from .szse import (
    SZSE_CALENDAR_URL,
    CalendarDay,
    TradingCalendar,
    fetch_trading_calendar,
    parse_trading_calendar,
    refresh_trading_calendar,
)
from .tencent import TENCENT_SYMBOLS, IndexQuote, fetch_index_quote
from .tradetime import (
    MARKET_CLOSE,
    as_of_for_trade_date,
    market_is_open,
    session_in_progress,
)

from .sanity import (  # noqa: F401
    BOARD_PCT_LIMIT,
    HL_SANITY_FACTOR,
    INDEX_PCT_LIMIT,
    implausible_bars,
    is_valid_price,
)

__all__ = [
    "BOARD_PCT_LIMIT",
    "HL_SANITY_FACTOR",
    "INDEX_PCT_LIMIT",
    "implausible_bars",
    "is_valid_price",
    "BOARD_KINDS",
    "Board",
    "BoardResult",
    "MARKET_CLOSE",
    "POOL_ENDPOINTS",
    "SINA_SYMBOLS",
    "SZSE_CALENDAR_URL",
    "TENCENT_SYMBOLS",
    "BreadthResult",
    "CalendarDay",
    "DailyBar",
    "IndexDaily",
    "IndexQuote",
    "PoolResult",
    "SourceError",
    "TradingCalendar",
    "as_of_for_trade_date",
    "fetch_boards",
    "fetch_breadth",
    "fetch_index_daily",
    "parse_index_daily",
    "fetch_index_quote",
    "fetch_pool",
    "fetch_trading_calendar",
    "parse_trading_calendar",
    "refresh_trading_calendar",
    "market_is_open",
    "session_in_progress",
    "get_json",
    "get_json_and_text",
    "get_text",
]
