"""「Specialist 真的被调用过」——外部评审 F3 + 复查发现的洞。

根 `AGENTS.md` 原本写着：「`agent_runs` 那张表是唯一凭证。」
**这句话不成立** —— 那张表是 BigA 自己写的，手工跑一遍 skill 也会写进去。
真正能区分的是 OpenClaw 运行时自己记的 `subagent_runs`。

🔴 第一版修复的洞（外部复查发现，已实测复现）
---------------------------------------------
新机制**不按决策号绑定**，只问「这个 agent 名字有没有出现在最近 50 条里」：

    伪造决策号 BIGA-20260921-901，用合法 API 写 6 行 agent_runs
    → spawn 核验：6 个 agent 两份独立记录都齐   ✅ 通过

伪造的号**蹭上了别的决策的真实记录**。而 `bin/biga-card` 正常使用就会
反复运行 ⇒ 这个条件在真实机器上**几乎总是成立**，不是刁钻场景。

⚠️ 最讽刺的是：决策号本来就明文写在 `payload_json` 里，只是没被用来绑定。

🔴 而本文件的第一版**结构上不可能发现它** —— 它去 fake
`_runtime_spawn_records` 本身，正好把「按决策号过滤」那段整个绕过去。
⇒ 这一版改成造一个**真的 sqlite**，让被测代码走完整的查询路径。

> 假的东西造得太靠上，测的就是自己写的假货，不是产品代码。
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3  # store-exempt: 造的是 **OpenClaw 运行时**状态库的仿件，
                # 不是 BigA 事实层。`_store` 单一入口规则是为了「将来切 PG
                # 只改一个文件」，而这个库永远不会跟着 BigA 迁移。
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tools" / "verify"))

import phase1_acceptance as pa  # noqa: E402
import spawn_check  # noqa: E402

from _contract import STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402

ALL = [a for a in list(STAGE1_AGENTS) + list(STAGE2_AGENTS) if a != "discipline"]
MINE, OTHER = "BIGA-20260921-777", "BIGA-20260921-778"


def fake_runtime_db(path: pathlib.Path, runs: list[tuple[str, str]]) -> pathlib.Path:
    """造一份运行时状态库。`runs = [(agent, decision_id), …]`。

    字段形状照抄真库：`child_session_key` 是 `agent:<name>:subagent:<uuid>`，
    决策号明文出现在 `payload_json` 里。
    """
    # store-exempt: 同上 —— 外部运行时库的仿件
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE subagent_runs (run_id TEXT, child_session_key TEXT,"
                 " controller_session_key TEXT, requester_session_key TEXT,"
                 " created_at INTEGER, payload_json TEXT)")
    for i, (agent, did) in enumerate(runs):
        conn.execute("INSERT INTO subagent_runs VALUES (?,?,?,?,?,?)", (
            f"run-{i}", f"agent:{agent}:subagent:uuid-{i}",
            "agent:main:card-1", "agent:main:card-1", 1000 + i,
            f'{{"prompt":"本次决策编号 {did}，请…"}}'))
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def wire(tmp_path, monkeypatch):
    """`wire(agent_runs里的, 运行时记录)` —— 两侧分别给。"""
    def _w(ours: list[str], runtime: list[tuple[str, str]]):
        monkeypatch.setattr(
            pa, "list_agent_runs",
            lambda **kw: [{"agent": a} for a in ours], raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs",
                            lambda **kw: [{"agent": a} for a in ours])
        monkeypatch.setenv(
            "BIGA_RUNTIME_DB",
            str(fake_runtime_db(tmp_path / "rt.db", runtime)))
    return _w


class TestBoundToDecisionId:
    """🔴 复查那条洞的正面回归。"""

    def test_别人的记录不算我的(self, wire):
        """机器上跑过别的真实决策 —— 那些记录不能给这次背书。

        这是复查实测的场景，也是第一版**唯一没设想到**的场景。
        """
        wire(ALL, [(a, OTHER) for a in ALL])       # 运行时全是另一个号的
        assert spawn_check.main([MINE]) == 1, "蹭上别人的记录被判通过了"

    def test_自己的记录才算(self, wire):
        wire(ALL, [(a, MINE) for a in ALL])
        assert spawn_check.main([MINE]) == 0

    def test_两个决策混在库里也能分开(self, wire):
        """真实机器上本来就是这样 —— 一天跑很多次。"""
        wire(ALL, [(a, OTHER) for a in ALL] + [(a, MINE) for a in ALL])
        assert spawn_check.main([MINE]) == 0

    def test_部分伪造(self, wire):
        """五个真 spawn，risk 那行是手工塞的。"""
        wire(ALL, [(a, MINE) for a in ALL if a != "risk"])
        assert spawn_check.main([MINE]) == 1

    def test_按名字分段比_不按子串(self, wire):
        """`news` 不该被 `newsflash` 顶替 —— 子串包含的误判从来不报错。"""
        wire(["news"], [("newsflash", MINE)])
        assert spawn_check.main([MINE]) == 1


class TestThreeState:
    def test_读不到运行时库是判不了(self, wire, monkeypatch):
        wire(ALL, [])
        monkeypatch.setenv("BIGA_RUNTIME_DB", "/nonexistent/nope.db")
        assert pa.spawn_proof(MINE).readable is False
        assert spawn_check.main([MINE]) == 2

    def test_两边都空是判不了_不是通过(self, wire):
        """🔴 「无事可查却报绿」—— 第一版在这里退出 0。"""
        wire([], [])
        assert spawn_check.main([MINE]) == 2

    def test_库能读但本决策零记录是伪造_不是判不了(self, wire):
        """这两种必须分开：**「判不了」会被忽略，「伪造」不会。**"""
        wire(ALL, [(a, OTHER) for a in ALL])
        proof = pa.spawn_proof(MINE)
        assert proof.readable is True and proof.rows == 0
        assert spawn_check.main([MINE]) == 1


class TestCoverage:
    def test_覆盖契约里的每一个agent(self, wire):
        """F3 原本的要害：只认 "emotion" 一个名字。"""
        wire(ALL, [(a, MINE) for a in ALL])
        assert set(pa.spawn_proof(MINE).per_agent) == set(ALL)
        assert len(ALL) >= 6

    def test_缺席不算伪造(self, wire):
        """本次压根没跑某个 agent，与「跑了但是假的」是两回事。"""
        sub = [a for a in ALL if a != "news"]
        wire(sub, [(a, MINE) for a in sub])
        assert spawn_check.main([MINE]) == 0


class TestWiredIntoRealPath:
    """L-1：新增任何检查，必须存在**被证明的调用方**。"""

    def test_出卡流程真的会调它(self):
        text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        run = text.split("# ── 出新卡")[1]
        assert "tools/verify/spawn_check.py" in run, (
            "spawn_check.py 没有被出卡流程调用 —— \n"
            "  一个不会被跑到的检查，和没有这个检查是一回事。")

    def test_查询按决策号过滤(self):
        """🔴 判据落在 SQL 上：没有 WHERE 就是复查发现的那个洞。

        ⚠️ 只看**像 SQL 的**常量 —— docstring 里也会提到那些词，
        而那会让这条检查退化成「文件里出现过这个字符串吗」。
        """
        src = (REPO / "tools" / "verify" / "phase1_acceptance.py").read_text(
            encoding="utf-8")
        sqls = [n.value for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "subagent_runs" in n.value and "SELECT" in n.value]
        assert sqls, "核验不再读运行时自己记的 subagent_runs"
        assert all("WHERE" in q for q in sqls), (
            "取运行时记录时没有按决策号过滤 —— \n"
            "  那样「最近 N 条里出现过这个 agent 名」就算通过，\n"
            "  而机器上本来就会反复跑别的决策，这个条件几乎总成立。")
        assert not any("LIMIT" in q for q in sqls), (
            "按决策号取不该有条数上限 —— 一天跑得多了，早先的决策会悄悄查不到。")


class TestOrphanSpawns:
    """孤儿 spawn：**钱花了，结果进不了任何卡**。

    实测（2026-09-21 19:31:52，飞书触发的那次）：

        emotion     无决策号
        technical   无决策号
        market      无决策号
        sector      BIGA-20260921-000   ← 临时号，`save_verdict` 会直接拒

    四个白跑，约 $0.4 / 2.5 分钟。根因是 Supervisor 在**占号之前**
    就 spawn 了 Stage 1 —— L-11「身份晚于证据」，而 schema v4
    那整套机制正是为了防它。

    🔴 **`spawn_proof()` 查不到它们** —— 它按决策号查，
    而孤儿的特征恰恰是**没有号可查**。两个检查方向相反，缺一不可：

        spawn_proof    这张卡上的 agent，真的被 spawn 了吗
        orphan_spawns  被 spawn 的 agent，最后进卡了吗
    """

    DAY = "20260921"

    def _db(self, tmp_path, runs):
        """`runs = [(agent, payload_里的决策号 或 None), …]`"""
        import datetime
        from _contract import CN_TZ
        base = int(datetime.datetime(2026, 9, 21, 10, 0,
                                     tzinfo=CN_TZ).timestamp() * 1000)
        p = tmp_path / "rt.db"
        # store-exempt: 外部运行时库的仿件，不是 BigA 事实层
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE subagent_runs (run_id TEXT, child_session_key TEXT,"
                     " controller_session_key TEXT, requester_session_key TEXT,"
                     " created_at INTEGER, payload_json TEXT)")
        for i, (agent, did) in enumerate(runs):
            body = f'本次决策编号 {did}' if did else '请分析一下'
            conn.execute("INSERT INTO subagent_runs VALUES (?,?,?,?,?,?)",
                         (f"r{i}", f"agent:{agent}:subagent:u{i}", "agent:main:main",
                          "agent:main:main", base + i * 1000,
                          f'{{"prompt":"{body}"}}'))
        conn.commit()
        conn.close()
        return p

    def test_带号的不算孤儿(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BIGA_RUNTIME_DB",
                           str(self._db(tmp_path, [("market", "BIGA-20260921-007")])))
        assert pa.orphan_spawns(self.DAY) == []

    def test_无号的是孤儿(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BIGA_RUNTIME_DB",
                           str(self._db(tmp_path, [("market", None)])))
        got = pa.orphan_spawns(self.DAY)
        assert len(got) == 1 and got[0][1] == "market" and "无决策号" in got[0][2]

    def test_临时号也是孤儿(self, tmp_path, monkeypatch):
        """🔴 `-000` 是临时号，落库会被 `save_verdict` 直接拒 ——
        带着它 spawn，等于确定要白跑。"""
        monkeypatch.setenv("BIGA_RUNTIME_DB",
                           str(self._db(tmp_path, [("sector", "BIGA-20260921-000")])))
        got = pa.orphan_spawns(self.DAY)
        assert len(got) == 1 and "临时号" in got[0][2]

    def test_非specialist不计入(self, tmp_path, monkeypatch):
        """别的 agent 不在本检查范围 —— 扩大范围只会制造噪音。"""
        monkeypatch.setenv("BIGA_RUNTIME_DB",
                           str(self._db(tmp_path, [("some-helper", None)])))
        assert pa.orphan_spawns(self.DAY) == []

    def test_读不到运行时库是判不了(self, monkeypatch):
        """R-3：读不到 ≠ 没问题。"""
        monkeypatch.setenv("BIGA_RUNTIME_DB", "/nonexistent/nope.db")
        assert pa.orphan_spawns(self.DAY) is None

    def test_巡检工具真的会报它(self):
        """L-1：新增检查必须有被证明的消费方，判据是命令的字面量。"""
        # ⚠️ 判的是**有没有真的调用**，不是「源码里出现过这个词」——
        #    第一版查字符串，把调用换成 `orphans = []` 之后 import 行
        #    还在，于是照样绿。又一次「通过是因为查错了地方」。
        src = (REPO / "tools" / "verify" / "budget_report.py").read_text(
            encoding="utf-8")
        called = any(
            isinstance(n, ast.Call)
            and (getattr(n.func, "id", "") or getattr(n.func, "attr", "")) == "orphan_spawns"
            for n in ast.walk(ast.parse(src)))
        assert called, (
            "budget_report.py 没有真的调用 orphan_spawns —— \n"
            "  一个不会被跑到的检查，等于没有这个检查。")
