"""`skills/_sources/tradetime.py` 的特征测试 + 日历感知回归（批 L）。

为什么这个文件在批 L 里第一个写
--------------------------------
`market_is_open()` / `session_in_progress()` 有**两个真实生产消费方**
（`news-scan` 判「此刻是否连续竞价」、`emotion-calc` 判「这个数是真零还是
还没产生」），却**零测试覆盖** —— 批 L 开工前 `grep -rl market_is_open
tests/` 一条不命中。改一个没有基线的函数，等于在猜。

⇒ 先固定「改之前」的行为（characterization / 金标准测试），再改。
   改完之后：
   * 有日历数据 ⇒ `market_is_open` 按 `fact_trading_calendar` 给正确答案（P4）
   * 没日历数据 ⇒ 回退到 weekday 判据，结果与「改之前」**逐一相同**（P5）

🔴 **本文件不许联网**（conftest 的 socket 围栏兜底）。日历数据一律用
桩/直接写库构造，不真去抓交易所。
"""

from __future__ import annotations

from datetime import datetime

import pytest


from _contract import CN_TZ  # noqa: E402
from _sources.tradetime import market_is_open, session_in_progress  # noqa: E402


def _dt(y, m, d, hh, mm=0) -> datetime:
    """构造一个北京时间时刻 —— 所有判据都在 `Asia/Shanghai` 下算。"""
    return datetime(y, m, d, hh, mm, tzinfo=CN_TZ)


@pytest.fixture
def no_calendar(tmp_path, monkeypatch):
    """把默认库指向一个**不存在**的路径 ⇒ `is_trading_day` 必然查不到 ⇒
    `market_is_open` 走 weekday 回退分支。

    这样这批特征测试对「改之前」和「改之后（无日历数据）」给出的调用完全
    一样（都不传 `path`），而结果必须逐一相同 —— 这正是 P5 的判据。
    改之前 `market_is_open` 根本不看库，设不设这个环境变量都无所谓；
    改之后它保证走回退分支，不受本机是否恰好存在一个默认库的影响。
    """
    monkeypatch.setenv("BIGA_DB_PATH", str(tmp_path / "does-not-exist.db"))


# ─────────────────────────────────────────────────────────────────────────
# market_is_open —— 特征测试（= weekday 回退分支的金标准，P5 的期望值）
#
# 🔴 元旦/国庆两条是**当前缺陷**，不是「正确答案」：纯 weekday 判据不认
#    节假日，工作日上的法定假日会被当成开市。批 L 修的就是它 —— 但只在
#    有日历数据时修（见 TestMarketOpenWithCalendar）。无数据时保持这个行为。
# ─────────────────────────────────────────────────────────────────────────
class TestMarketIsOpenCharacterization:
    """固定「无日历数据」时 `market_is_open` 的行为。"""

    @pytest.mark.parametrize("now,expected,why", [
        (_dt(2026, 9, 23, 10, 0),  True,  "周三盘中（早盘）"),
        (_dt(2026, 9, 23, 14, 59), True,  "周三盘中（午盘收盘前一分钟）"),
        (_dt(2026, 9, 21, 10, 0),  True,  "周一盘中"),
        (_dt(2026, 9, 23, 8, 0),   False, "周三开盘前"),
        (_dt(2026, 9, 23, 9, 30),  True,  "周三 09:30 整（区间左闭）"),
        (_dt(2026, 9, 23, 11, 30), False, "周三 11:30 整（区间右开，午休开始）"),
        (_dt(2026, 9, 23, 12, 0),  False, "周三午休"),
        (_dt(2026, 9, 23, 15, 0),  False, "周三 15:00 整（收盘，右开）"),
        (_dt(2026, 9, 23, 16, 0),  False, "周三盘后"),
        (_dt(2026, 9, 20, 10, 0),  False, "周日（周末短路）"),
        (_dt(2026, 9, 19, 10, 0),  False, "周六（周末短路）"),
        # 🔴 缺陷现状：工作日上的法定节假日被当成开市。
        (_dt(2026, 1, 1, 9, 31),   True,  "元旦（周四）—— 现状缺陷：当成交易日"),
        (_dt(2026, 10, 1, 10, 0),  True,  "国庆首日（周四）—— 现状缺陷：当成交易日"),
    ])
    def test_无日历数据时按_weekday_判据(self, no_calendar, now, expected, why):
        assert market_is_open(now) is expected, why


# ─────────────────────────────────────────────────────────────────────────
# session_in_progress —— 特征测试
#
# 🔴 批 L **不改** session_in_progress。它回答的是「这批数据声明的交易日
#    过完了没」（纯时间比较：声明的 trade_date 是不是今天且未到收盘），
#    与「今天是不是节假日」不是同一个问题。周末上午它也返回 True（那天的
#    数据确实「还没过完」）—— 这是它文档字符串写明的设计，不是缺陷。
#    给它加节假日感知会把 emotion 推向危险方向：节假日的 0 会被当成真「冰点」
#    而不是「还没产生」。所以这里固定它的行为，防止后续会话顺手改它。
# ─────────────────────────────────────────────────────────────────────────
class TestSessionInProgressCharacterization:
    @pytest.mark.parametrize("trade_date,retrieved,expected,why", [
        ("20260923", _dt(2026, 9, 23, 10, 0),  True,  "今天盘中"),
        ("20260923", _dt(2026, 9, 23, 14, 59), True,  "今天收盘前"),
        ("20260923", _dt(2026, 9, 23, 15, 0),  False, "今天 15:00（收盘，右开）"),
        ("20260923", _dt(2026, 9, 23, 15, 30), False, "今天盘后"),
        ("20260922", _dt(2026, 9, 23, 10, 0),  False, "数据是昨天的 ⇒ 那个交易日已过完"),
        ("20260924", _dt(2026, 9, 23, 10, 0),  False, "数据声明明天 ⇒ 不是今天"),
        # 周末/节假日：session_in_progress **有意**不认，返回 True（by design）
        ("20260920", _dt(2026, 9, 20, 10, 0),  True,  "周日：那天的数据确实还没过完（设计如此）"),
        ("20260101", _dt(2026, 1, 1, 10, 0),   True,  "元旦：同上，不认节假日是有意的"),
    ])
    def test_只按声明交易日与收盘时刻(self, trade_date, retrieved, expected, why):
        assert session_in_progress(trade_date, retrieved_at=retrieved) is expected, why
