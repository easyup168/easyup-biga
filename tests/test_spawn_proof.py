"""「Specialist 真的被调用过」——外部评审 F3。

根 `AGENTS.md` 写着：「那张表是唯一凭证 —— 你在回答里声称调用过，不算数。」

🔴 **这句话本身不成立。** 写那行的代码只是把 verdict JSON 自带的 agent
字段抄进 `agent_runs`；`started_at`/`finished_at` 还被写死成 Card 生成的
同一时刻。于是这两种情况产出的记录**逐字节相同**：

  1. Supervisor 真的 spawn 了 market
  2. 有人手工跑 `market_calc.py --task-id …`，把 verdict_ref 喂给 synthesize

两份都是 BigA 自己写的 —— **自己写的东西证明不了自己**。

唯一能做真区分的是 OpenClaw 运行时自己记的 `subagent_runs`，
而此前那套核验①只认 `"emotion"` 一个字面量，②不在任何自动化流程里。
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tools" / "verify"))

import phase1_acceptance as pa  # noqa: E402
import spawn_check  # noqa: E402

from _contract import STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402

DID = "BIGA-20260921-777"


def _wire(monkeypatch, ours: list[str], spawned: list[str]):
    monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: [{"agent": a} for a in ours],
                        raising=False)
    import _store
    monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: [{"agent": a} for a in ours])
    monkeypatch.setattr(
        pa, "_runtime_spawn_records",
        lambda: [{"payload_json": f'{{"agent":"{a}"}}', "child_session_key": a,
                  "controller_session_key": "main"} for a in spawned])


ALL = [a for a in list(STAGE1_AGENTS) + list(STAGE2_AGENTS) if a != "discipline"]


class TestSpawnProof:
    def test_覆盖契约里的每一个agent(self, monkeypatch):
        """🔴 F3 的要害：原来只认 "emotion" 一个名字。

        判据取自契约的 stage 名单 —— 名单会随 agent 增加自己长大，
        手写的那个不会。
        """
        _wire(monkeypatch, ALL, ALL)
        proof = pa.spawn_proof(DID)
        assert set(proof) == set(ALL)
        assert len(proof) >= 6, "Phase 2 有 6 个，只认一个就是 F3 本身"

    def test_手工跑脚本会被识破(self, monkeypatch):
        """agent_runs 里有、运行时 subagent_runs 里没有 = 那行是直接写进去的。"""
        _wire(monkeypatch, ALL, [a for a in ALL if a != "market"])
        proof = pa.spawn_proof(DID)
        assert proof["market"] == (True, False)
        assert spawn_check.main([DID]) == 1

    def test_全都对上时通过(self, monkeypatch):
        _wire(monkeypatch, ALL, ALL)
        assert spawn_check.main([DID]) == 0

    def test_读不到运行时表时判不了而不是通过(self, monkeypatch):
        """🔴 红线 R-3。读不到 ≠ 没问题。"""
        monkeypatch.setattr(pa, "_runtime_spawn_records", lambda: None)
        assert pa.spawn_proof(DID) == {}
        assert spawn_check.main([DID]) == 2

    def test_缺席不算伪造(self, monkeypatch):
        """本次决策压根没跑某个 agent，与「跑了但是假的」是两回事。"""
        _wire(monkeypatch, [a for a in ALL if a != "news"], ALL)
        assert spawn_check.main([DID]) == 0


class TestWiredIntoRealPath:
    """L-1：新增任何检查，必须存在**被证明的调用方**。

    判据是**调度命令的字面量**，不是「谁调用了它」——
    `architecture.md` §9 L-1 要防的头号失败模式就是
    「建好了但没有消费方，于是永远不会跑」。
    """

    def test_出卡流程真的会调它(self):
        text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        run = text.split("# ── 出新卡")[1]
        assert "tools/verify/spawn_check.py" in run, (
            "spawn_check.py 没有被出卡流程调用 —— \n"
            "  一个不会被跑到的检查，和没有这个检查是一回事。")

    def test_核验不依赖BigA自己写的表(self):
        """🔴 被验证方控制不到的地方，才算证据。

        判据用 AST 找那个表名的字面量 —— 改名换字符串都躲不过去。
        """
        src = (REPO / "tools" / "verify" / "phase1_acceptance.py").read_text(
            encoding="utf-8")
        # ⚠️ 只看**像 SQL 的**常量。第一版没加这条，结果 docstring 里
        #    提到 subagent_runs 就让它通过了 —— 探针把查询改成读 BigA
        #    自己的表，测试照样绿。又一个「通过是因为它查错了地方」。
        consts = {n.value for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and "SELECT" in n.value}
        assert any("subagent_runs" in c for c in consts), (
            "核验不再读运行时自己记的 subagent_runs —— \n"
            "  只查 BigA 自己写的表，证明不了「是 Supervisor spawn 的」。")
