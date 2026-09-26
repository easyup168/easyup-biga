"""6 位代码 → 交易所/板块 —— 唯一那份号段表的守卫。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：号段判定、`market` 一致性、以及几个**真实踩过**的边界
- **不覆盖**：各调用方怎么用它（那在各自的测试里）
"""
from __future__ import annotations

import pytest

from easyup_biga.providers.instrument_segments import (
    Segment,
    a_share_segment,
    is_a_share,
)


@pytest.mark.parametrize("code,exchange,board", [
    ("600000", "SSE", "SSE_MAIN"),
    ("601398", "SSE", "SSE_MAIN"),
    ("605588", "SSE", "SSE_MAIN"),
    ("688001", "SSE", "STAR"),
    ("689009", "SSE", "STAR"),
    ("000001", "SZSE", "SZSE_MAIN"),
    ("002594", "SZSE", "SZSE_MAIN"),
    ("300750", "SZSE", "CHINEXT"),
    ("301001", "SZSE", "CHINEXT"),
    ("302132", "SZSE", "CHINEXT"),
    ("920000", "BSE", "BSE"),
    ("430047", "BSE", "BSE"),
])
def test_A股个股都认得(code, exchange, board):
    assert a_share_segment(code) == Segment(exchange, board)


def test_创业板302段():
    """🔴 `302132 中航成飞` —— 2026-09-26 第一次拿真实全市场名单跑时撞上的。

    在补上它之前，`cn.security_master` 的第一次真实同步**必然整体失败**。
    枚举三位前缀是在追一个会变的东西；交易所的分配本来就是两位粒度。
    """
    assert is_a_share("302132")


@pytest.mark.parametrize("code", [
    "510300",   # 沪 ETF
    "113050",   # 沪可转债
    "123456",   # 深可转债
    "880001",   # 🔴 通达信自编板块指数（真包里 1120 条，全在 sh）
    "899050",   # 北证 50 指数
    "200011",   # 深 B 股
    "900901",   # 沪 B 股
    "",
    None,
    "60000",    # 5 位，不补零就不猜
    "abcdef",
])
def test_不是A股个股一律返回None(code):
    assert a_share_segment(code) is None


# ── market 一致性 ──────────────────────────────────────────────────────────
def test_market说了就以它为准():
    """🔴 `000001` 在沪市是上证指数、在深市是平安银行。

    实测：2026-09-24 的通达信盘后包里 **840 个代码在两个市场都存在**。
    按代码拍平会让上证指数的点位变成平安银行的「股价」，而且不报错。
    """
    assert a_share_segment("000001", market="sz") == Segment("SZSE", "SZSE_MAIN")
    assert a_share_segment("000001", market="sh") is None


def test_数字市场编码一律当作没说():
    """🔴 东财的 `f13` 用 `0` **同时**表示深市和北交所。

    把它当权威会让整个北交所被判成「代码段与市场冲突 ⇒ 不是 A 股」，
    而那不会报错，只会让 universe 悄悄少一个交易所。
    实测抓到过：`430047`（老北交所段）的 `f13` 就是 0。

    ⇒ 有歧义的信息不如没有信息 —— 后者至少不会把人引向错的结论。
    """
    assert a_share_segment("430047", market=0) == Segment("BSE", "BSE")
    assert a_share_segment("600000", market=1) == Segment("SSE", "SSE_MAIN")


def test_认不出的市场标识回退到号段():
    assert a_share_segment("600000", market="unknown") == Segment("SSE", "SSE_MAIN")


def test_三个调用方都用这一份():
    """防 L-3 复发：这个判断曾在本仓库有**三份**手写实现。"""
    import ast
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    suspicious = []
    for path in (repo / "src").rglob("*.py"):
        if path.name == "instrument_segments.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # 找 `code.startswith(("43", "83", ...))` 这种自造号段判断
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args):
                continue
            arg = node.args[0]
            values = ([e.value for e in arg.elts if isinstance(e, ast.Constant)]
                      if isinstance(arg, ast.Tuple) else
                      [arg.value] if isinstance(arg, ast.Constant) else [])
            if any(isinstance(v, str) and v.isdigit() and len(v) in (2, 3)
                   for v in values):
                suspicious.append(f"{path.relative_to(repo)}:{node.lineno}")
    assert not suspicious, (
        "又出现了自造的代码号段判断：\n  " + "\n  ".join(suspicious) + "\n"
        "  这个判断只有一份实现：providers/instrument_segments.py。\n"
        "  它的失败形状不是崩溃，是**静默取到另一只真标的**。")
