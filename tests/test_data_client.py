"""P3-6a · 数据层的取数边界 —— provider 选择不在调用点。

覆盖 / 不覆盖
-------------
- 覆盖：日历刷新按注册表的 PRIMARY → FALLBACK 链路走；降级被**标记出来**
  而不是悄悄发生；`bin/biga-calendar` 真的经过这一层
- **不覆盖**：五条盘中 direct feed（P3-6 主体，要先有 dataset 定义）；
  szse 端点本身（本机连不通，见它的模块头）

🔴 这个文件钉的是一条「声明了却不存在」的路径
---------------------------------------------
`cn.trading_calendar` 的注册表里一直写着 `fallback_providers=("szse",)`，
而 `bin/biga-calendar` 写死了 sina —— 主源挂掉就整条失败。
**一条声明了却没实现的降级路径，比没声明更糟**：读注册表的人会以为
这件事已经有人管了。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from easyup_biga.data.client import CALENDAR_DATASET, refresh_trading_calendar
from easyup_biga.data.failover import ProviderChainExhausted
from easyup_biga.persistence import init_schema
from easyup_biga.providers.http import SourceError

REPO = pathlib.Path(__file__).resolve().parents[1]


def _ok(n=730):
    return lambda: (n, "20240101", "20261231")


def _boom(msg="primary down"):
    return lambda: (_ for _ in ()).throw(SourceError(msg))


def test_主源正常时不碰备用源(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    calls: list[str] = []
    result = refresh_trading_calendar(path=db, fetchers={
        "sina_calendar": lambda: (calls.append("sina"), (730, "20240101", "20261231"))[1],
        "szse": lambda: (calls.append("szse"), (0, "", ""))[1],
    })
    assert calls == ["sina"], "主源成功了却还是打了备用源"
    assert result.provider_id == "sina_calendar"
    assert result.degraded is False


def test_主源挂了真的降到备用源(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    result = refresh_trading_calendar(path=db, fetchers={
        "sina_calendar": _boom(), "szse": _ok(120)})
    assert result.provider_id == "szse"
    assert result.rows_written == 120


def test_降级被标记出来而不是悄悄发生(tmp_path):
    """🔴 「成了」和「降级之后成了」不是一回事 —— 后者覆盖范围更窄。

    只看返回值成不成，读的人会以为这天和平常一样；
    而降级之后「下个月某天开不开市」可能已经答不出来了。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    result = refresh_trading_calendar(path=db, fetchers={
        "sina_calendar": _boom("connection reset"), "szse": _ok()})
    assert result.degraded is True
    failed = [a for a in result.attempts if not a.succeeded]
    assert [a.provider_id for a in failed] == ["sina_calendar"]
    assert "connection reset" in failed[0].error


def test_两个都挂了才整条失败(tmp_path):
    db = tmp_path / "biga.db"
    init_schema(db)
    with pytest.raises(ProviderChainExhausted) as exc:
        refresh_trading_calendar(path=db, fetchers={
            "sina_calendar": _boom("a"), "szse": _boom("b")})
    assert len(exc.value.attempts) == 2
    assert CALENDAR_DATASET in str(exc.value)


def test_链路顺序来自注册表不是来自调用方(tmp_path):
    """探针方向：把 fetchers 的字典顺序倒过来，结果必须不变。

    ⚠️ 判据是「顺序由注册表决定」。若哪天有人改成按 `fetchers` 的插入序走，
    这条会红 —— 而那正是「provider 选择又回到调用点」的形状。
    """
    db = tmp_path / "biga.db"
    init_schema(db)
    result = refresh_trading_calendar(path=db, fetchers={
        "szse": _ok(1), "sina_calendar": _ok(2)})
    assert result.provider_id == "sina_calendar"
    assert result.rows_written == 2


def test_bin_biga_calendar_不直接import任何provider():
    """🔴 判据打在 AST 上 —— 「用哪个源」这个决定不许回到 CLI 里。

    这条测试扫的是 `bin/biga-calendar` 里那段 heredoc Python。
    它曾经写着 `from easyup_biga.providers.sina_calendar import ...`，
    于是注册表说的 fallback 与它实际做的事**是两回事**。
    """
    text = (REPO / "bin" / "biga-calendar").read_text(encoding="utf-8")
    start = text.index("import sys, pathlib")
    snippet = text[start:text.rindex("PY")]
    tree = ast.parse(snippet)
    offenders = [
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("easyup_biga.providers")
    ]
    assert not offenders, (
        f"bin/biga-calendar 直接 import 了采数适配器：{offenders}\n"
        "  用哪个源是数据层的决定 —— 走 easyup_biga.data.client。")
