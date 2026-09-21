"""数值围栏 —— 外部评审 F5 / F6。

两条发现是同一个形状：**有值、而且值看起来正常，但它是错的。**
比 `missing` 危险得多，因为不会被看见。

==========  ==================================================
F5          60 日高/低点距离没有量级围栏，一根坏 tick 吃出荒谬数字
F6          资金字段被吞成 0 时，「主力净流入前 5」给出任意的第一名
==========  ==================================================

🔴 两条的守卫都**不能**只查最新一根 / 只查一个字段 ——
   评审两次都是从「没人看的那一根」和「没人看的那个字段」进来的。
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _sources.eastmoney import Board, BoardResult  # noqa: E402
from _sources.sanity import (  # noqa: E402
    BOARD_PCT_LIMIT,
    HL_SANITY_FACTOR,
    INDEX_PCT_LIMIT,
    implausible_bars,
    is_valid_price,
)


@dataclass
class FakeBar:
    high: float
    low: float
    close: float


def clean_window(n: int = 60, base: float = 3000.0) -> list[FakeBar]:
    return [FakeBar(base + i * 2 + 5, base + i * 2 - 5, base + i * 2) for i in range(n)]


class TestF5MagnitudeFence:
    def test_干净窗口不报(self):
        assert implausible_bars(clean_window()) == []

    def test_评审构造的坏tick被抓到(self):
        """F5 原样：窗口内**第 91 根**（不是最新一根）的 low 改成 0.01。

        0.01 是正数 —— 不触发任何「≤0」守卫，也不会 ZeroDivisionError。
        正是本项目自己反复强调的「rc=0、字段齐全、类型正确」那类垃圾值。
        """
        win = clean_window()
        win[30] = FakeBar(win[30].high, 0.01, win[30].close)
        sick = implausible_bars(win)
        assert sick and "0.01" in sick[0]

    def test_真实腰斩不被误报(self):
        """🔴 定紧了会在极端行情里报红，而永远报警的检查会被忽略。"""
        crash = [FakeBar(3000 - i * 20 + 5, 3000 - i * 20 - 5, 3000 - i * 20)
                 for i in range(60)]
        assert implausible_bars(crash) == []

    def test_中位数不被单根坏值带偏(self):
        """用均值的话，一根 0.01 会把基准拉低，坏值自己就显得没那么离谱。"""
        win = clean_window()
        win[0] = FakeBar(1e9, 1e9, 1e9)
        assert implausible_bars(win), "极大值必须被抓到"

    @pytest.mark.parametrize("v", [None, 0, -1, float("nan"), float("inf")])
    def test_无效价格(self, v):
        assert not is_valid_price(v)

    def test_high小于low是结构错误(self):
        win = clean_window()
        win[5] = FakeBar(3000.0, 3100.0, 3050.0)
        assert any("high" in m and "low" in m for m in implausible_bars(win))

    def test_两条涨跌幅围栏是同一份定义的两个别名(self):
        """L-3：原来两个 skill 各写一个 `PCT_ABS_LIMIT`，值还不一样。"""
        sys.path.insert(0, str(REPO / "skills/market-calc/scripts"))
        sys.path.insert(0, str(REPO / "skills/sector-calc/scripts"))
        import market_calc
        import sector_calc
        assert market_calc.PCT_ABS_LIMIT is INDEX_PCT_LIMIT
        assert sector_calc.PCT_ABS_LIMIT is BOARD_PCT_LIMIT
        assert INDEX_PCT_LIMIT != BOARD_PCT_LIMIT, "分开命名就是为了让不同是明示的"


def board(name: str, pct: float, inflow: float | None) -> Board:
    return Board(code="x", name=name, pct=pct, main_inflow=inflow,
                 advance=1, decline=1, leader=None)


class TestF6ZeroSwallow:
    def test_缺失不再被吞成0(self):
        """根因在解析层：`float(r.get("f62") or 0.0)`。

        同一份代码对 `f3` 的处理是**相反的** —— 为 None 或 "-" 直接 raise。
        一份解析器里两套口径，而被吞的那一半没人守。
        """
        from _sources.eastmoney import _num
        assert _num(None, float) is None
        assert _num("-", float) is None
        assert _num("", float) is None
        assert _num(0, float) == 0.0, "真的是 0 要保留 —— 与「没给」是两件事"
        assert _num("1.5", float) == 1.5

    def test_全零资金流会被识别(self):
        """F6 构造：20 个板块 pct 各不相同非零（过得了 pre_session 守卫），
        main_inflow 全部 0.0。"""
        r = BoardResult(kind="industry", total=20, raw={},
                        boards=[board(f"板块{i}", 1.0 + i * 0.1, 0.0) for i in range(20)])
        assert r.nonzero_count == 20, "pct 侧是健康的 —— 这正是它躲过守卫的原因"
        assert r.inflow_nonzero_count == 0
        assert r.inflow_known == 20

    def test_字段缺席与全零可区分(self):
        absent = BoardResult(kind="industry", total=3, raw={},
                             boards=[board(f"b{i}", 1.0, None) for i in range(3)])
        zeros = BoardResult(kind="industry", total=3, raw={},
                            boards=[board(f"b{i}", 1.0, 0.0) for i in range(3)])
        assert absent.inflow_known == 0 and zeros.inflow_known == 3
        assert absent.inflow_nonzero_count == zeros.inflow_nonzero_count == 0

    @pytest.mark.parametrize("inflow,code", [
        (None, "sector.inflow.field_absent"),
        (0.0,  "sector.inflow.all_zero"),
    ])
    def test_资金侧失效时不给排名而是报缺失(self, monkeypatch, inflow, code):
        """🔴 判的是「有没有报出缺失」，不是「排名对不对」——
        排名在这种输入下**本来就是任意的**，断言它等于什么都没意义。
        """
        sys.path.insert(0, str(REPO / "skills/sector-calc/scripts"))
        import sector_calc

        boards = [board(f"板块{i}", 1.0 + i * 0.1, inflow) for i in range(20)]
        monkeypatch.setattr(
            sector_calc, "fetch_boards",
            lambda kind: BoardResult(kind=kind, total=20, raw={"pages": []},
                                     boards=boards))
        c = sector_calc.build_verdict(break_source=set(), store=False,
                                      task_id="BIGA-20260921-001")
        codes = [m.code for m in c.missing]
        assert code in codes, codes
        assert "main_inflow_top" not in c.result, "资金侧失效时不该给出「第一名」"
