"""Phase 2 出口条件工具的红灯测试。

这里专门防止三类曾经出现过的假阳性：

- 条件 4：`--break-source` 演练被算成「真实缺失」；
- 条件 3：任意 agent 的「否决」、或 risk 否决但 Card 未被拦住，被算成真实 Risk Veto；
- 条件 5：盘中延迟预算有**两个出处**，改一处忘另一处时剩下那处照样报绿。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`tools/verify/exit_conditions.py` 的两条判据函数、以及条件 5 判据的**唯一性**
- **不覆盖**：`missing_ledger.py` 自身的真实/演练分类（`tests/test_missing_ledger.py`）、
  退出码三态（`tests/test_verify_exit_codes.py`）
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load_module():
    p = REPO / "tools/verify/exit_conditions.py"
    spec = importlib.util.spec_from_file_location("exit_conditions_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


EC = _load_module()


def _v(agent: str, stance: str):
    return SimpleNamespace(agent=agent, stance=stance)


def _card(status: str, verdicts):
    return SimpleNamespace(status=status, verdicts=verdicts)


def test_条件4_演练与弱证据都不能把门槛顶满():
    rows = [
        {"day": "2026-09-21", "real": [], "drill": ["emotion.pool.unavailable"]},
        {"day": "2026-09-21", "real": ["supervisor.agent_offline"], "drill": []},
        {"day": "2026-09-22", "real": ["legacy"], "drill": []},
        {"day": "2026-09-22", "real": ["supervisor.agent_no_response"], "drill": []},
        {"day": "2026-09-23", "real": [], "drill": ["market.quote.unavailable"]},
    ]
    ok, hit, days = EC._missing_gate(rows)
    assert ok is False
    assert hit == []
    assert days == set()


def test_条件4_必须五张源级真实缺失且至少两天():
    rows = [
        {"day": "2026-09-21", "real": [f"emotion.source.{i}"], "drill": []}
        for i in range(4)
    ] + [
        {"day": "2026-09-22", "real": ["market.quote.unavailable"], "drill": []}
    ]
    ok, hit, days = EC._missing_gate(rows)
    assert ok is True
    assert len(hit) == 5
    assert days == {"2026-09-21", "2026-09-22"}


def test_条件3_非risk否决不能算():
    cards = {
        "D1": _card("AVOID", [_v("market", EC.VETO_STANCE)]),
    }
    ok, rejected = EC._qualified_vetoes(
        ["D1"], loader=cards.get, replay_checker=lambda _: (True, "PASS")
    )
    assert ok == []
    assert rejected == []


def test_条件3_risk否决但card未阻断不能算():
    cards = {
        "D1": _card("WAIT", [_v("risk", EC.VETO_STANCE)]),
    }
    ok, rejected = EC._qualified_vetoes(
        ["D1"], loader=cards.get, replay_checker=lambda _: (True, "PASS")
    )
    assert ok == []
    assert rejected and "status" in rejected[0][1]


def test_条件3_replay不通过不能算():
    cards = {
        "D1": _card("BLOCK", [_v("risk", EC.VETO_STANCE)]),
    }
    ok, rejected = EC._qualified_vetoes(
        ["D1"], loader=cards.get, replay_checker=lambda _: (False, "mismatch")
    )
    assert ok == []
    assert rejected and "replay" in rejected[0][1]


def test_条件3_只有严格三项都满足才算():
    cards = {
        "D1": _card("AVOID", [_v("risk", EC.VETO_STANCE)]),
    }
    ok, rejected = EC._qualified_vetoes(
        ["D1"], loader=cards.get, replay_checker=lambda _: (True, "PASS")
    )
    assert ok == [("D1", "AVOID")]
    assert rejected == []


# ─────────────────────────── 条件 5 · 盘中延迟预算只有一处定义

def test_条件5_盘中延迟预算只有一处定义():
    """🔴 §9 L-3：同一条判据两个出处，改了一处忘了另一处时剩下那处仍然报绿。

    实测形状：`latency_report.py` 早就把预算重推成 180s 并写下了推导过程，
    而 `phase1_acceptance.check_8_latency` 的默认值还停在 90s 的字面量上。
    两个工具**都不报错**，各自按各自的线判 —— 同一次运行，一个说达标一个说超。

    ⚠️ 只把 90_000 改写成 180_000 不算修好：那只是让两个字面量**此刻**相等，
       下次重推还会再分叉一次。判据必须是「只有一处」，不是「两处相等」。
    """
    sys.path.insert(0, str(REPO / "tools" / "verify"))
    import latency_report
    import phase1_acceptance

    import inspect
    default = inspect.signature(
        phase1_acceptance.check_8_latency).parameters["budget_ms"].default
    assert default == latency_report.DEFAULT_BUDGET_MS, (
        f"验收 #8 用的预算 {default} 与 latency_report 的 "
        f"{latency_report.DEFAULT_BUDGET_MS} 对不上 —— 同一次运行会得出两个结论。")

    src = (REPO / "tools/verify/phase1_acceptance.py").read_text(encoding="utf-8")
    assert re.search(r"budget_ms\s*:\s*int\s*=\s*[0-9]", src) is None, (
        "`check_8_latency` 又把预算写成了字面量。\n"
        "  唯一定义在 `tools/verify/latency_report.py::DEFAULT_BUDGET_MS`"
        "（180s 的推导过程也在那儿）—— 这里 import 它，别再抄一个数。")
