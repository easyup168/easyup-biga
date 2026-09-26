"""`budget_report.py` 的参数解析 —— 报错要指路，不是抛一屏 traceback。

覆盖 / 不覆盖
-------------
- 覆盖：位置参数与 `--day` 两种写法、非法日期的报错形状
- **不覆盖**：报告内容本身（那要有库，且随数据变）

🔴 为什么值得单独一个文件
-------------------------
`--day 20260922` 原本会把字符串 `"--day"` 当成日期，一路走到 `strptime`
抛一屏调用栈（2026-09-23 评审顺手撞到）。那不是「参数写错了」的报错 ——
**读的人看到的是 `_strptime.py` 的栈，第一反应会去查这个工具是不是坏了。**
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
# ⚠️ 只挂 pythonpath 没覆盖的两处。`skills` 已由 pyproject 的 pythonpath 挂好，
#    再挂一遍不改变任何结果（批 U-III 删掉的就是这一类）。
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

import budget_report as br  # noqa: E402


def test_位置参数():
    assert br._parse_day(["20260922"]) == "20260922"


def test_day_flag两种写法都收():
    assert br._parse_day(["--day", "20260922"]) == "20260922"
    assert br._parse_day(["-d", "20260922"]) == "20260922"


def test_不给参数时取今天():
    got = br._parse_day([])
    assert len(got) == 8 and got.isdigit()


def test_非法日期当场指路而不是抛traceback():
    """🔴 判据是**报错内容**，不是「有没有抛异常」。

    原来的行为也「抛异常」—— 抛的是 `ValueError: time data '--day' does not
    match format '%Y%m%d'`，带着 `_strptime.py` 的调用栈。
    两者都非零退出，而只有一种告诉得了人下一步做什么。
    """
    with pytest.raises(SystemExit) as exc:
        br._parse_day(["2026-09-22"])
    msg = str(exc.value)
    assert "YYYYMMDD" in msg
    assert "2026-09-22" in msg, "报错要说清楚是哪个值不对"
    assert "用法" in msg, "报错要指路"


def test_day后面缺参数():
    with pytest.raises(SystemExit) as exc:
        br._parse_day(["--day"])
    assert "用法" in str(exc.value)


def test_main真的用了_parse_day():
    """🔴 否则 `_parse_day` 是个零消费方的解析器 —— 上面五条全绿，而命令行照样崩。

    探针实测：把 `main()` 里那行换回 `argv[0]`，上面五条**一条都不红**。
    判据落在 AST 上（`main` 的函数体里有没有调用它），不是字符串扫描 ——
    docstring 里提到这个名字是正常的（本文件就提了）。
    """
    import ast

    src = (REPO / "tools" / "verify" / "budget_report.py").read_text(encoding="utf-8")
    main_fn = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {n.func.id for n in ast.walk(main_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_parse_day" in called, (
        "budget_report.main() 没有调用 _parse_day —— "
        "那个解析器因此是零消费方，命令行仍会拿 argv[0] 当日期。")
