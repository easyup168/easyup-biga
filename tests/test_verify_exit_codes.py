"""验证工具的退出码三态 —— 外部深度评审「`latency_report` 三态不统一」。

评审报的是一处，扫一遍是四处
----------------------------
评审指出 `latency_report.py` 读不到运行时数据时退 `1` —— 与「Stage 1 真的
串行了」同一个码。顺着同一条线扫下去，同形状**一共四处**：

==========================  ====================================
`latency_report.py`         读不到轮次 / 找不到决策 → `1`
`agent_trace.py`            一个 agent 库都没有 → `1`
`missing_ledger.py`         库里一张卡都没有 → `1`
`phase1_acceptance.py`      **PENDING 塌进 FAIL 码**
==========================  ====================================

最后那一处最说明问题 —— 它的上一行注释是：

    🔴 PENDING 不算 PASS。这条是本项目的第一条红线。
    return 0 if tally == {"PASS": len(checks), "FAIL": 0, "PENDING": 0} else 1

**注释是三态的，代码是两态的。**

> 🔴 三态在**每一层**都要是三态。
> 最常见的读法是「跑完没报错就是过了」—— 读的正是退出码。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`tools/verify/` 下工具的退出码来自唯一定义、且判不了 ≠ 不通过
- **不覆盖**：每个工具的具体判据对不对（各自的测试文件管）
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
from datetime import datetime, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))

import _verdict as _v  # noqa: E402
import latency_report as lr  # noqa: E402

from _contract import CN_TZ  # noqa: E402
from _store.runtime import AgentTurn, RuntimeProbe  # noqa: E402

#: 用了这套口径的工具。判据是「它自己 import 了 `_verdict`」。
_WIRED = ["isolation.py", "latency_report.py", "agent_trace.py",
          "missing_ledger.py", "phase1_acceptance.py", "spawn_check.py",
          "readback_check.py", "exit_conditions.py", "budget_report.py",
          "security_master_probe.py", "phase3_acceptance.py", "phase3_runtime.py",
          "source_bundle.py"]
#: ⚠️ 最后两个是 **2026-09-25 补进来的**，它们从第一天起就 `import _verdict`、
#:    也真的返回三态，却一直不在这张表里 —— 于是下面那两条守卫**从没查过它们**。
#:
#:    🔴 根因不是「忘了加」，是**判据写在一张手写名单上**：名单不会因为
#:    `tools/verify/` 下多出一个文件而自己变长，而它的失效是静默的
#:    （少查一个 ≠ 报错）。⇒ 由 `test_名单没有漏掉任何用了这套码的工具`
#:    反过来钉住：**用了这套码，就必须在名单里**。


class TestSingleDefinition:
    """L-3：同一套口径不能有第二份实现。"""

    def test_三个码互不相同(self):
        assert len({_v.PASS, _v.FAIL, _v.UNKNOWN}) == 3
        assert _v.PASS == 0, "0 必须是通过 —— shell 的 `&&` 就是这么读的"
        assert _v.UNKNOWN != 0, "红线 R-3：判不了不许是 0"
        assert _v.FAIL != 0

    @pytest.mark.parametrize("name", _WIRED)
    def test_每个工具都从唯一定义拿码(self, name):
        src = (REPO / "tools" / "verify" / name).read_text(encoding="utf-8")
        assert "import _verdict as _v" in src, (
            f"{name} 没接上退出码的唯一定义。\n"
            "  各写一遍裸数字的后果评审已经点过：`2` 和 `1` 的含义在不同文件里不一样。")

    @pytest.mark.parametrize("name", _WIRED)
    def test_main里不许再出现裸数字(self, name):
        """🔴 判据落在 AST 上，不是「有没有 import」。

        只查 import 挡不住「import 了、但某个分支还是 `return 1`」——
        那正是本仓库反复踩的「守卫查的地方和它声称守的地方不是同一处」。

        ⚠️ 只查 `main()` 的 `return` 与 `SystemExit(...)`。别的函数返回整数
           是正常的（计数、下标），一并禁掉会逼出一堆无意义的改写。
        """
        path = REPO / "tools" / "verify" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant)
                            and isinstance(sub.value.value, int)):
                        bad.append(f"main() 第 {sub.lineno} 行 return {sub.value.value}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "SystemExit" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, int)):
                bad.append(f"第 {node.lineno} 行 SystemExit({node.args[0].value})")

        assert bad == [], (
            f"{name} 还在用裸退出码：\n" + "".join(f"  · {b}\n" for b in bad)
            + "  改成 `_v.PASS` / `_v.FAIL` / `_v.UNKNOWN`。\n"
              "  裸数字读不出意图 —— `return 1` 到底是「不通过」还是「没查成」，\n"
              "  评审就是在这个歧义上找到四处缺陷的。")

    def test_名单没有漏掉任何用了这套码的工具(self):
        """🔴 反向钉住 `_WIRED` —— 名单是手写的，**它不会自己变长**。

        上面两条守卫都按 `_WIRED` 参数化。于是「新工具忘了加进名单」的后果是
        **少两条 test case**，而不是一条红 —— 测试总数每天都在变，没人会发现。

        实测：`exit_conditions.py` 与 `budget_report.py` 从建起就用着这套码，
        却一直不在名单里，两条守卫**从没查过它们**。查出来靠的不是复查名单，
        是有人顺手 `grep` 了一遍目录。

        ⇒ 判据反过来：**目录里凡是 import 了 `_verdict` 的，都得在名单里**。
           这条不需要人记得，新增文件时它自己会红。
        """
        # 🔴 两种 import 形式都要认。原本只查字面量 `"import _verdict"`，
        #    于是 `from _verdict import PASS, FAIL` 这种写法**整个漏过去** ——
        #    而它恰恰是新工具最可能的写法（IDE 自动补全给的就是它）。
        #    ⇒ 这条反向守卫自己也犯了它要防的毛病：判据比它声称的范围窄。
        #    实测 2026-09-26：`phase3_acceptance.py` 第一版正是这么写的。
        used = {p.name for p in (REPO / "tools" / "verify").glob("*.py")
                if re.search(r"^\s*(?:import _verdict|from _verdict import)",
                             p.read_text(encoding="utf-8"), re.M)}
        missing = sorted(used - set(_WIRED))
        assert missing == [], (
            "这些工具用了 `_verdict` 的退出码，却不在 `_WIRED` 里 ——\n"
            "  于是本文件的两条守卫从没查过它们：\n"
            + "".join(f"  · {m}\n" for m in missing)
            + "  加进 `_WIRED` 即可（加完两条守卫会立刻替它们体检）。")
        assert set(_WIRED) <= used, (
            f"`_WIRED` 里有目录下不存在或没接上唯一定义的名字："
            f"{sorted(set(_WIRED) - used)}")

    def test_describe覆盖三个码(self):
        """报错要指路 —— 只给数字的退出码会被当成「反正非零」。"""
        for code in (_v.PASS, _v.FAIL, _v.UNKNOWN):
            assert _v.describe(code) and "未定义" not in _v.describe(code)
        assert "未定义" in _v.describe(99)


# ─────────────────────────── latency_report 的四条路径

def _turn(agent: str, t0: int, t1: int) -> AgentTurn:
    base = datetime(2026, 9, 21, 15, 0, tzinfo=CN_TZ)
    return AgentTurn(
        agent_id=agent, session_key=f"s-{agent}", run_id=f"r-{agent}-{t0}",
        model="m",
        started_at=base + timedelta(seconds=t0),
        ended_at=base + timedelta(seconds=t1),
        tokens_in=10, tokens_out=10, cache_read=0, cache_write=0,
        cost_usd=0.01)


@pytest.fixture()
def wire(monkeypatch):
    """`wire(turns, window)` —— 两侧都桩掉，**不读任何真库**。"""
    def _w(turns, window=("BIGA-20260921-001",
                          datetime(2026, 9, 21, 15, 4, tzinfo=CN_TZ), 240_000)):
        monkeypatch.setattr(lr, "read_turns",
                            lambda **kw: RuntimeProbe(turns=list(turns)))
        monkeypatch.setattr(lr, "_decision_window", lambda did: window)
        monkeypatch.setattr(lr, "_previous_card_time", lambda did: None)
        # `read_task_runs()` 返回 (记录, 不可用原因) —— 桩要照抄这个形状。
        # ⚠️ 第一版返回了 `[]`，脚本在解包时 `ValueError`。
        #    桩把接口写错，测出来的就不是产品行为。
        monkeypatch.setattr(lr, "read_task_runs", lambda **kw: ([], ["测试桩"]))
    return _w


def test_读不到轮次是判不了(wire, capsys):
    """🔴 评审报的那一处。全新环境里运行时库本来就是空的。"""
    wire([])
    assert lr.main([]) == _v.UNKNOWN
    assert "判不了" in capsys.readouterr().out


def test_找不到决策是判不了(wire, capsys):
    wire([_turn("main", 0, 200)], window=None)
    assert lr.main([]) == _v.UNKNOWN
    assert "判不了" in capsys.readouterr().out


def test_窗口里没有轮次是判不了(wire, capsys):
    """轮次存在，但都不在这次决策的窗口里 —— 最常见成因是报告跑早了。"""
    far = datetime(2026, 9, 1, 10, 0, tzinfo=CN_TZ)
    wire([_turn("main", 0, 200)], window=("BIGA-20260901-001", far, 1000))
    assert lr.main([]) == _v.UNKNOWN
    assert "判不了" in capsys.readouterr().out


def test_没有supervisor轮次时并行检查是判不了(wire, capsys):
    """🔴 实测漏报过一次：报 41.5s「达标」，缺掉的合成轮是 159.1s。

    这条与「真的串行」必须是不同的码 —— 一个去等数据，一个去改提示词。
    """
    wire([_turn("market", 0, 60), _turn("sector", 0, 60)])
    assert lr.main(["--parallel-check"]) == _v.UNKNOWN
    capsys.readouterr()


def test_真的串行才是不通过(wire, capsys):
    """区间**不相交** = 串行。这是唯一该退 FAIL 的情形。"""
    wire([_turn("main", 0, 240), _turn("market", 0, 60), _turn("sector", 61, 120)])
    rc = lr.main(["--parallel-check"])
    out = capsys.readouterr().out
    assert rc == _v.FAIL, f"串行没被判成不通过（rc={rc}）：\n{out[-800:]}"


def test_并行且有supervisor才通过(wire, capsys):
    wire([_turn("main", 0, 240), _turn("market", 0, 60), _turn("sector", 5, 65)])
    rc = lr.main(["--parallel-check"])
    out = capsys.readouterr().out
    assert rc == _v.PASS, f"并行却没通过（rc={rc}）：\n{out[-800:]}"
