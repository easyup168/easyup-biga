"""不带日期的端点（涨跌家数 / 板块榜）该署哪个 `as_of` —— 三态判据。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`as_of_for_undated_snapshot` 的三态 + 日历缺失时的回退；
  两次真实事故各自对应的那一态
- **不覆盖**：谁去查日历（那是 `persistence.latest_trading_day`）、
  skill 怎么用它（在各自的 skill 测试里）

🔴 为什么这个函数值得单独一份测试
---------------------------------
它修的不是「取错了数」，是**「给对的数配了错的时刻」** —— 而这类错
**不报错**，只会让下游的一致性判据自己打自己：

    2026-09-26 实测：market/technical 报 20260924、news 报 20260926
    ⇒ risk 判「上游报告了不同的交易日，不能当作同一天的事实一起审」
    ⇒ 整张卡降级成 WAIT。

而那个「不一致」有一半是我们自己标出来的。

两次事故的方向**正好相反**，所以这份测试必须同时钉住两端 ——
只钉一端的话，修它的人会顺手把另一端改回去（历史上已经发生过一次）。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from easyup_biga.domain import CN_TZ
from easyup_biga.providers.tradetime import as_of_for_undated_snapshot


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CN_TZ)


class Test两次真实事故:
    def test_盘中午休不许被贴成上一交易日收盘(self):
        """🔴 事故一（F4，`BIGA-20260921-017`，周一 12:41 午休）。

        涨跌家数取回的是**今天此刻**的 4385/1100/145，而日线还停在上周五
        ⇒ 卡面写着 `as_of 09-18 15:00`。**一个今天的数，挂着上周五的时间戳。**

        午休不在连续竞价时段内，但**这一天还没收盘** —— 数据仍是今天的。
        """
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 21, 12, 41), latest_trade_date="20260921")
        assert as_of == _at(2026, 9, 21, 12, 41)
        assert warn and "盘中快照" in warn

    def test_非交易日不许被贴成此刻(self):
        """🔴 事故二（`BIGA-20260926-003`，周六 18:11）。

        端点返回的其实是 **09-24 收盘**的 1120/4305/137（它自己不会说），
        而 `as_of` 标成 09-26 18:11。**一个上周四的数，挂着周六的时间戳。**
        """
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 26, 18, 11), latest_trade_date="20260924")
        assert as_of == _at(2026, 9, 24, 15, 0)
        assert warn and "20260924" in warn and "不是交易日" in warn


class Test三态:
    @pytest.mark.parametrize("hh,mm", [(9, 35), (11, 0), (12, 41), (14, 59)])
    def test_交易日收盘前一律取回时刻(self, hh, mm):
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 28, hh, mm), latest_trade_date="20260928")
        assert as_of == _at(2026, 9, 28, hh, mm)
        assert warn and "盘中快照" in warn

    @pytest.mark.parametrize("hh", [15, 18, 23])
    def test_交易日收盘后对齐到当日十五点且不报警(self, hh):
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 28, hh), latest_trade_date="20260928")
        assert as_of == _at(2026, 9, 28, 15, 0)
        assert warn is None, warn

    def test_非交易日对齐到最近交易日收盘(self):
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 27, 10), latest_trade_date="20260924")
        assert as_of == _at(2026, 9, 24, 15, 0)
        assert warn


class Test日历缺失时的回退:
    def test_查不到就退回取回时刻并说出来(self):
        """🔴 `None` ≠ 「没有交易日」（R-3）。

        不许在这里推算「往前找第一个非周末」—— 长假里那会给出错误答案，
        而**错误答案和正确答案长得一模一样**。
        """
        ret = _at(2026, 9, 26, 18, 11)
        as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=ret, latest_trade_date=None)
        assert as_of == ret
        assert warn and "没覆盖" in warn

    def test_回退必须带警告而不是静默(self):
        _as_of, warn = as_of_for_undated_snapshot(
            retrieved_at=_at(2026, 9, 26, 18, 11), latest_trade_date=None)
        assert warn is not None, "静默回退 = 卡面看起来板上钉钉"


def test_market与sector共用同一个实现():
    """🔴 两个 skill 各写一份就是 L-3 —— 而那份差异的表现是
    「两个 agent 报了不同的交易日」，**不报错**。

    判据打在「两处 import 的是同一个函数对象」上。
    """
    import importlib.util
    import pathlib
    import sys

    repo = pathlib.Path(__file__).resolve().parent.parent
    loaded = {}
    for name, rel in (("market_calc", "skills/market-calc/scripts/market_calc.py"),
                      ("sector_calc", "skills/sector-calc/scripts/sector_calc.py")):
        if name in sys.modules:
            loaded[name] = sys.modules[name]
            continue
        for d in ("skills/market-calc/scripts", "skills/sector-calc/scripts"):
            p = str(repo / d)
            if p not in sys.path:
                sys.path.insert(0, p)
        spec = importlib.util.spec_from_file_location(name, repo / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        loaded[name] = mod

    assert (loaded["market_calc"].as_of_for_undated_snapshot
            is loaded["sector_calc"].as_of_for_undated_snapshot
            is as_of_for_undated_snapshot)
