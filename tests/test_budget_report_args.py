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
