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

`TestReadbackWiredIntoRealPath` 是后来加的（F-4，设计文档 §6）：
同一个 `bin/biga-card` 沙盒，验的是另一个检查（`readback_check.py`，
毒行巡检）有没有被同样的方式接住退出码——两者是同一个形状的教训，
放在同一个文件里复用沙盒机制，比另起一份更省。
"""

from __future__ import annotations

import ast
import os
import time
import re
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

from _scan import repo_files  # noqa: E402

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



def fake_runtime_db_task_runs(path: pathlib.Path,
                              runs: list[tuple[str, str]],
                              execs: list[tuple[str, str]] = ()) -> pathlib.Path:
    """造一份**只有 `task_runs`** 的运行时库 —— 这是编排器路径的真实形状。

    `runs = [(agent, decision_id), …]`   真 spawn（`task_kind` 为 NULL，run_id 是 uuid 形）
    `execs = [(agent, decision_id), …]`  Specialist 自己在会话里跑 shell
                                          （`task_kind='exec'`，run_id 形如 `exec:<名>`）

    ⚠️ **只建查询用得到的 7 列**，真库那张表有 33 列（含 `task_id` 等）——
    仿件不是真库的复制品，只保证被查的那几列形状一致。说成「照抄真库」会让
    读者以为它能替真库回答别的问题。
    实测 `BIGA-20260922-001` 在 `task_runs` 里有 12 行真 spawn 与 5 行 exec，
    而 `subagent_runs` 里一行都没有。
    """
    conn = sqlite3.connect(path)  # store-exempt: 外部运行时库的仿件
    conn.execute("CREATE TABLE task_runs (run_id TEXT, agent_id TEXT,"
                 " child_session_key TEXT, requester_session_key TEXT,"
                 " task_kind TEXT, task TEXT, created_at INTEGER)")
    for i, (agent, did) in enumerate(runs):
        conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)", (
            f"uuid-run-{i}", agent, f"agent:{agent}:subagent:uuid-{i}",
            "agent:main:orchestrator-abc123", None,
            f"本次决策编号 {did}。请给出…", 2000 + i))
    for i, (agent, did) in enumerate(execs):
        conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)", (
            f"exec:name-{i}", agent, f"agent:{agent}:subagent:uuid-x{i}",
            f"agent:{agent}:subagent:uuid-x{i}", "exec",
            f"cd ~/ws && python3 skills/… --task-id {did}", 3000 + i))
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


@pytest.fixture()
def wire_rr(tmp_path, monkeypatch):
    """像 `wire`，但 agent_runs 行带 `runtime_run_id`（批 J-II 结构化 join 用）。

    `ours = [(agent, runtime_run_id 或 None), …]`；`runtime = [(agent, did), …]`
    （复用 `fake_runtime_db`：第 i 条运行时记录的 `run_id` 是 `"run-{i}"`）。
    """
    def _w(ours: list[tuple[str, str | None]], runtime: list[tuple[str, str]]):
        rows = [{"agent": a, "runtime_run_id": rr} for a, rr in ours]
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: rows, raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: rows)
        monkeypatch.setenv(
            "BIGA_RUNTIME_DB", str(fake_runtime_db(tmp_path / "rt.db", runtime)))
    return _w


class TestStructuredJoin:
    """🔴 批 J-II · J2-3：有 `runtime_run_id` 时，spawn 核验用它与运行时
    `subagent_runs.run_id` 做**结构化 join**，比按 `child_session_key` 段名的文本
    匹配硬。这一组的全部理由是证明「新判据真的比旧的硬」，不是「新判据也能过」。
    """

    def test_伪造的runtime_run_id蹭不上别人的记录(self, wire_rr):
        """market 那行 `runtime_run_id` 在运行时里根本不存在，但决策号出现在某条
        `payload_json` 里（= 蹭上别人记录那个旧洞）。结构化 join 报伪造。"""
        # 运行时只有一条属于本决策的记录，run_id="run-0"；agent_runs 记的却是 rt-fake。
        wire_rr([("market", "rt-fake-never-spawned")], [("market", MINE)])
        assert spawn_check.main([MINE]) == 1, "假 runtime_run_id 蹭上了别人的记录"

    def test_同一场景下旧的LIKE判据会放过它(self, wire_rr):
        """把 `runtime_run_id` 抹成 None（模拟历史行）——退回段名判据，
        而 `child_session_key` 段名恰好是 `market`，于是**被放过**。
        这正是 J2-3 要堵的洞：证明新旧判据在同一份数据上给出相反结论。"""
        wire_rr([("market", None)], [("market", MINE)])
        assert spawn_check.main([MINE]) == 0, (
            "退回分支本应按段名放过 —— 若这里也报伪造，说明退回分支被写坏了")

    def test_真实匹配的runtime_run_id通过(self, wire_rr):
        """反方向：agent_runs 记的 `runtime_run_id` 与运行时那条的 `run_id` 一致
        （`fake_runtime_db` 第 0 条是 `run-0`）⇒ join 命中 ⇒ 通过。
        否则上面两条可以靠「join 永远不命中」平凡成立。"""
        wire_rr([("market", "run-0")], [("market", MINE)])
        assert spawn_check.main([MINE]) == 0

    def test_历史行与新行混在同一决策里各走各的(self, wire_rr):
        """market 有真 runtime_run_id（走 join、命中），news 是历史行（None，走段名、
        命中）—— 任一 agent 缺 runtime_run_id 不让整条检查失效，也不互相污染。"""
        wire_rr([("market", "run-0"), ("news", None)],
                [("market", MINE), ("news", MINE)])  # news 是第 1 条 → run-1
        assert spawn_check.main([MINE]) == 0


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

    @staticmethod
    def _orch_path(work) -> pathlib.Path:
        return work / "skills" / "decision-card" / "scripts" / "orchestrator.py"

    @staticmethod
    def _orch_stub(work, db, did: str) -> str:
        """orchestrator.py 的桩内容：落一张真卡 + 渲染到 stdout + 退出 0。

        🔴 批 C-II 之后，出卡的**付费动作**是 `orchestrator.py`（经 Adapter → 真
           spawn），不再是 `$BIGA agent`。而 Adapter 用的是 `DEFAULT_BIGA`（写死的
           真实路径），**根本不读 `BIGA` 环境变量** —— 所以桩必须落在 orchestrator.py
           上，桩 `BIGA` 已经拦不住花钱了。这一处正是收缩把「付费调用」挪了地方、
           而守卫还盯着老地方（L-13）会咬人的位置。
        """
        return (
            "import sys\n"
            f"sys.path.insert(0, {str(work / 'skills')!r})\n"
            "from _contract import (AgentVerdict, DecisionCard, Evidence, now_cn,\n"
            "                       new_run_context)\n"
            "from _store import init_schema, open_run, save_card\n"
            "t = now_cn()\n"
            f"TID = {did!r}\n"
            f"init_schema({str(db)!r})\n"
            # 🔴 批 N：在线卡必须带 run_id / evidence_set_id，且 run_id 要追得到
            #    decision_runs —— 桩也得开一次真 run，不能编一个字符串。
            "RID = 's' * 32\n"
            "open_run(new_run_context(origin='cli', non_interactive=True,\n"
            f"                         decision_id=TID, run_id=RID), path={str(db)!r})\n"
            "v = AgentVerdict(task_id=TID, agent='market', status='completed',\n"
            "                 verdict='PASS', result={'x': 1}, data_completeness=1.0,\n"
            "                 stance='分化', elapsed_ms=1,\n"
            "                 evidence=[Evidence(field='x', source='s', value=1,\n"
            "                                    as_of=t, retrieved_at=t)])\n"
            # 🔴 只落 1 个 agent，另外 5 个天然缺席——F-8 之后 roster 判据按计数
            #    比较（missing 条数 >= 缺席 agent 数），给 5 条占位 missing 才够，
            #    免得抢在这批探针要验证的事情前面报错。
            "card = DecisionCard(decision_id=TID, status='WAIT', headline='h',\n"
            "                    verdicts=[v], synthesis='', model_ref='m',\n"
            "                    missing=['占位1 —— 本文件不测 roster',\n"
            "                             '占位2', '占位3', '占位4', '占位5'],\n"
            "                    run_id=RID, evidence_set_id='es-stub')\n"
            f"save_card(card, path={str(db)!r})\n"
            "print('run stub-run   （查进度：bin/biga-card --status stub-run）',"
            " file=sys.stderr)\n"
            "print(card.render())\n"
        )

    @staticmethod
    def _seeded_repo(tmp_path, did: str, spawn_stub: str, readback_stub: str | None = None):
        """造一个能走完出卡路径的沙盒。**不联网、不花钱。**

        桩（`readback_stub` 缺省时不桩 —— 让真的 `readback_check.py` 跑在刚种下的
        干净库上，它本该报 0 条毒行，用于验证 F-4 的接入不影响「一切正常」时的行为）：
          · `orchestrator.py` → 直接往库里落一张真卡并渲染（替掉真 spawn，见 `_orch_stub`）
          · `spawn_check.py` → 由调用方决定退出码
          · `readback_check.py` → 同上（F-4：设计文档 §6，A-I 评审欠的账）
        """
        import shutil
        import subprocess
        work = tmp_path / "repo"
        shutil.copytree(REPO, work, symlinks=True, ignore=shutil.ignore_patterns(
            # 🔴 运行时产物必须排除 —— 事故当天 `.biga-card-stop`（总闸）
            #    被原样复制进沙盒，于是三条出卡路径测试全部拿到 rc=3。
            #    沙盒要复制的是**代码**，不是这台机器此刻的运行状态。
            ".biga-card-stop", ".biga-card.lock",
            ".git", "__pycache__", "data", ".pytest_cache", ".claude", "memory"))
        db = tmp_path / "t.db"
        # 预建空 schema，让 bin/biga-card 读 BEFORE（readonly）时库已存在、返回空。
        subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(work / 'skills')!r}); "
             f"from _store import init_schema; init_schema({str(db)!r})"],
            check=True, capture_output=True)

        # 🔴 桩掉 orchestrator.py —— 这才是收缩之后的付费调用。
        TestWiredIntoRealPath._orch_path(work).write_text(
            TestWiredIntoRealPath._orch_stub(work, db, did), encoding="utf-8")

        # BIGA 桩留成一个「响亮的空操作」：Adapter 走 DEFAULT_BIGA 不经这里，
        # 万一有别的路径去调 $BIGA，这里会在 stderr 留痕而不是真起会话。
        stub = tmp_path / "fake-biga"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "echo '🔴 stub BIGA 被调用 —— C-II 后 orchestrator 用 DEFAULT_BIGA，"
            "不该经过这里' >&2\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        (work / "tools" / "verify" / "spawn_check.py").write_text(
            spawn_stub, encoding="utf-8")
        if readback_stub is not None:
            (work / "tools" / "verify" / "readback_check.py").write_text(
                readback_stub, encoding="utf-8")
        return work, db, stub

    @staticmethod
    def _run(work, db, stub):
        import os
        import subprocess
        # 🔴 自证不花钱：收缩后付费点是 orchestrator.py（Adapter 走 DEFAULT_BIGA，
        #    不读 BIGA 环境变量）。只在它已被桩掉的沙盒里跑 —— 桩没落上就当场炸，
        #    而不是让一次真跑去触发真 spawn。fail-closed 在**使用点**，不靠元测试兜。
        orch_src = TestWiredIntoRealPath._orch_path(work).read_text(encoding="utf-8")
        assert "OpenClawRuntimeAdapter" not in orch_src, (
            "sandbox 里的 orchestrator.py 不是桩 —— 这次 _run 会触发真 spawn（真花钱）")
        return subprocess.run(
            ["bash", str(work / "bin" / "biga-card")],
            capture_output=True, text=True, cwd=work, timeout=120,
            env={**os.environ, "BIGA": str(stub), "BIGA_DB_PATH": str(db)})

    @pytest.mark.parametrize("rc,why", [(1, "有伪造"), (2, "判不了")])
    def test_核验失败时出卡命令必须失败(self, tmp_path, rc, why):
        """🔴 **这条才是闸门的判据。** 上面那条只查「字符串在不在」。

        外部深度评审指出：`bin/biga-card` 原来是裸调用 + 无条件 `exit 0`，
        而脚本用 `set -uo pipefail`（没有 `-e`）⇒ 退出码被整个丢掉：

            spawn_check exit 1  →  biga-card 仍 exit 0
            spawn_check exit 2  →  biga-card 仍 exit 0

        真实语义是「跑了一次检查并打印结果」，不是验收 Gate。

        ⚠️ **而我的守卫没抓到** —— 调用在、退出码被丢，它照样绿。
        同一形状第 8 次：**守卫查的地方，和它声称守的地方，不是同一处。**
        """
        work, db, stub = self._seeded_repo(
            tmp_path, "BIGA-20260921-001",
            f"import sys\nprint('桩：{why}', file=sys.stderr)\nsys.exit({rc})\n")
        r = self._run(work, db, stub)
        assert r.returncode != 0, (
            f"spawn_check 返回 {rc}（{why}），而 biga-card 仍然成功退出 ——\n"
            f"  那它就只是一句打印，不是闸门。\n  stdout 尾部：{r.stdout[-260:]}")
        assert "spawn 核验未通过" in r.stderr, r.stderr[-260:]
        assert "BIGA-20260921-001" in r.stdout, "卡应当照常渲染（钱已经花了，藏起来没意义）"

    def test_核验通过时出卡命令成功(self, tmp_path):
        """反方向 —— 否则上面那条可以靠「永远失败」平凡通过。"""
        work, db, stub = self._seeded_repo(
            tmp_path, "BIGA-20260921-002", "print('桩：全齐')\n")
        r = self._run(work, db, stub)
        assert r.returncode == 0, f"核验通过却失败了：rc={r.returncode}\n{r.stderr[-260:]}"

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


class TestReadbackWiredIntoRealPath:
    """F-4（设计文档 §6，A-I 评审欠的账）：毒行巡检必须接一个真实调用方。

    `readback_check.py` 批 A-I 就建好了，但只有测试和文档——没有任何
    自动路径会跑它。对照 `spawn_check.py` 当年的教训（调用在、退出码
    被丢，守卫照样绿），这里同样要证明**退出码被用上**，不只是打印。
    """

    def test_出卡流程真的会调它(self):
        text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        run = text.split("# ── 出新卡")[1]
        assert "tools/verify/readback_check.py" in run, (
            "readback_check.py 没有被出卡流程调用 —— \n"
            "  一个不会被跑到的检查，和没有这个检查是一回事。")

    def test_干净库时不影响出卡命令成功(self, tmp_path):
        """反面：readback_check 不桩，让它跑在刚种下的干净库上。"""
        work, db, stub = TestWiredIntoRealPath._seeded_repo(
            tmp_path, "BIGA-20260922-001", "print('桩：全齐')\n")
        r = TestWiredIntoRealPath._run(work, db, stub)
        assert r.returncode == 0, (
            f"干净库却失败了：rc={r.returncode}\n{r.stderr[-260:]}")

    def test_巡检报红时出卡命令的退出码跟着变(self, tmp_path):
        """🔴 这条才是闸门的判据 —— 同 spawn_check 那次教训一样，
        「字符串在不在」测不出退出码有没有被接住。

        把巡检改成必然失败，断言 `bin/biga-card` 的退出码跟着变
        （而不是像 spawn_check 当年那样被 `set -uo pipefail`
        （没有 `-e`）吞掉，仍然 exit 0）。
        """
        work, db, stub = TestWiredIntoRealPath._seeded_repo(
            tmp_path, "BIGA-20260922-002", "print('桩：全齐')\n",
            readback_stub="import sys\nprint('桩：巡检报红', file=sys.stderr)\nsys.exit(1)\n")
        r = TestWiredIntoRealPath._run(work, db, stub)
        assert r.returncode == 6, (
            f"巡检报红，但 biga-card 的退出码没有跟着变（rc={r.returncode}）—— \n"
            f"  那它就只是一句打印，不是闸门。\n  stderr 尾部：{r.stderr[-300:]}")
        assert "毒行巡检发现历史遗留问题" in r.stderr

    def test_巡检报红不与spawn核验共用同一个信号(self, tmp_path):
        """🔴 毒行是历史遗留，不是这次运行的错——它不该让人以为是
        **这次**出卡的 spawn 证据链断了（那是 rc=4 该管的事）。
        两者必须是不同的退出码，否则读者会查错方向。"""
        work, db, stub = TestWiredIntoRealPath._seeded_repo(
            tmp_path, "BIGA-20260922-003", "print('桩：全齐')\n",
            readback_stub="import sys\nsys.exit(1)\n")
        r = TestWiredIntoRealPath._run(work, db, stub)
        assert r.returncode != 4, "毒行巡检不该冒充 spawn 核验失败的退出码"

    def test_出卡命令仍然会渲染卡片(self, tmp_path):
        """毒行是历史遗留，不该让「这次出的卡」被藏起来——钱已经花了。"""
        work, db, stub = TestWiredIntoRealPath._seeded_repo(
            tmp_path, "BIGA-20260922-004", "print('桩：全齐')\n",
            readback_stub="import sys\nsys.exit(1)\n")
        r = TestWiredIntoRealPath._run(work, db, stub)
        assert "BIGA-20260922-004" in r.stdout


# ─────────────────────────── 2026-09-21 21:03 事故：出卡递归
#
# `AGENTS.md` 当时写着「🔴 要出卡？跑这一条」并贴出 `bin/biga-card`。
# 而那条命令做的事就是把编排提示词喂给 `main` —— 读到那句话的正是它。
#
#   bin/biga-card → $BIGA agent --agent main → 新 main 会话
#                                                ↓ 读 AGENTS.md
#                                          bin/biga-card → …
#
# 实测：187 个 main 会话 / 约 195 次调用，92 次是那行命令的逐字复制。
# 预算闸门在占号处拦下约 58 次（省约 $48），但每次被拦前已付掉一个
# main 轮次 —— 实账 $8.99。
#
# 🔴 递归的每一层都**没有任何异常信号**：每一层都在「照文档做」。


class TestNoCardRecursion:
    """契约侧 + 机器侧两道，缺一不可。"""

    def test_契约不许叫supervisor去跑出卡命令(self):
        """判据是「命令有没有作为**可执行行**出现」，不是「文件里提没提它」。

        ⚠️ 不能简单断言 `"biga-card" not in text` —— 这一节**必须**提到它
           才能说「不要运行它」。描述一条「不要做 X」的规则就得写出 X，
           `CLAUDE.md` 记过四次同样的形状。
        ⇒ 只禁**代码块里的裸调用**，因为那才是会被照抄的东西。
        """
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        bad = []
        in_block = False
        for i, ln in enumerate(text.splitlines(), 1):
            if ln.startswith("```"):
                in_block = not in_block
                continue
            if not in_block:
                continue
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            # 画递归示意图的那几行带箭头，不是可执行命令
            if "→" in s or "↓" in s:
                continue
            if re.search(r"(^|[;&|]\s*|/)biga-card(\s|$)", s) and not re.search(
                    r"--(list|show|check)\b", s):
                bad.append(f"AGENTS.md:{i}  {s}")
        assert bad == [], (
            "Supervisor 的契约里出现了可直接照抄的出卡命令：\n"
            + "".join(f"  · {b}\n" for b in bad)
            + "  🔴 `bin/biga-card` 做的事就是把编排提示词喂给 `main`。\n"
              "     叫 `main` 去跑它 = 让系统再造一个自己，**无条件无限递归**。\n"
              "     2026-09-21 21:03–21:50 实际发生过：187 个会话 / $8.99。\n"
              "  那条命令是**给人敲的**。契约里要写的是「绝对不要运行它」。")

    def test_契约明确写了不要运行它(self):
        """反向：上面那条可以靠「一个字都不提」平凡通过，那也是错的 ——
        不提，下一个模型就会自己发明这个调用。"""
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        assert "绝对不要运行" in text and "biga-card" in text, \
            "契约必须**点名**禁止，而不是回避这个命令"

    def test_出卡命令自己有单实例锁(self):
        """🔴 契约那一侧靠不住 —— 这是本项目的第一课。

        同一天 19:31 那次「四个 spawn 无归属」，规则也一条不缺地写在契约里。
        ⇒ 机器这一侧必须自己拦。判据是**真的并发跑两次**，不是「源码里有 flock」。
        """
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            work, db, stub = TestWiredIntoRealPath._seeded_repo(
                td, "BIGA-20260921-701", "print('桩：全齐')\n")
            # 🔴 持锁的是 orchestrator 那一段（flock 在调它之前拿到）——要让第二次
            #    撞上锁，就得让**它**慢一点，桩 BIGA 已经不在出卡路径上了。
            TestWiredIntoRealPath._orch_path(work).write_text(
                "import time; time.sleep(8)\n", encoding="utf-8")
            env = {**os.environ, "BIGA": str(stub), "BIGA_DB_PATH": str(db),
                   "BIGA_CARD_FORCE": "1"}
            first = subprocess.Popen(["bash", str(work / "bin" / "biga-card")],
                                     cwd=work, env=env,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            try:
                time.sleep(2)
                second = subprocess.run(
                    ["bash", str(work / "bin" / "biga-card")],
                    cwd=work, env=env, capture_output=True, text=True, timeout=60)
                assert second.returncode != 0, (
                    "第二次并发出卡没有被拦下 —— 递归/并发就还能重演。\n"
                    f"  stdout: {second.stdout[-200:]}")
                assert "单实例锁" in second.stderr, second.stderr[-200:]
            finally:
                first.terminate()
                first.wait(timeout=30)

    def test_总闸能一键停掉出新卡(self):
        """事故止血用的。判据：存在闸门文件 ⇒ 出新卡拒绝，且**看卡不受影响**。"""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            work, db, stub = TestWiredIntoRealPath._seeded_repo(
                td, "BIGA-20260921-702", "print('桩：全齐')\n")
            (work / ".biga-card-stop").write_text("测试用\n", encoding="utf-8")
            env = {**os.environ, "BIGA": str(stub), "BIGA_DB_PATH": str(db),
                   "BIGA_CARD_FORCE": "1"}
            r = subprocess.run(["bash", str(work / "bin" / "biga-card")],
                               cwd=work, env=env, capture_output=True,
                               text=True, timeout=60)
            assert r.returncode == 3, f"总闸没拦住：rc={r.returncode}"
            assert "总闸" in r.stderr
            # 🔴 只拦花钱的动作 —— 事故排查时必须还能看已有的卡
            r2 = subprocess.run(["bash", str(work / "bin" / "biga-card"), "--list", "3"],
                                cwd=work, env=env, capture_output=True,
                                text=True, timeout=60)
            assert r2.returncode == 0, (
                "总闸把 --list 也拦了 —— 事故当中最需要的就是看现状。\n"
                f"  {r2.stderr[-200:]}")

    def test_wrapper被杀会收掉编排子进程(self):
        """🔴 评审阻塞项 2：wrapper（`bin/biga-card`）死了，它起的编排子进程不许孤立
        继续跑完 spawn 真花钱。

        事故根因**不是** `$1.2` 那个 bash bug —— 那行在文本顺序上排在编排调用之前，
        真在那崩溃根本到不了 spawn。真正的根因：一个外层短 timeout 杀掉了上层 shell，
        而 `timeout 840 orchestrator.py` 孙进程被孤立后跑完了整轮 spawn。修法是
        `bin/biga-card` 的 trap（Code Guard），不是「以后别手工乱跑」（意图）。

        判据是**真的杀 wrapper、看子进程还在不在**，不是「源码里有没有 trap」。
        """
        import os
        import signal
        import subprocess
        import tempfile

        def _alive(pid: int) -> bool:
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                return False

        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            work, db, stub = TestWiredIntoRealPath._seeded_repo(
                td, "BIGA-20260921-802", "print('桩：全齐')\n")
            pidfile = td / "orch.pid"
            # 编排桩：记下自己的 pid，长睡 —— 模拟 wrapper 被杀时它还在跑（不 spawn、不花钱）
            TestWiredIntoRealPath._orch_path(work).write_text(
                "import os, pathlib, time\n"
                f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
                "time.sleep(120)\n", encoding="utf-8")
            env = {**os.environ, "BIGA": str(stub), "BIGA_DB_PATH": str(db),
                   "BIGA_CARD_FORCE": "1"}
            proc = subprocess.Popen(
                ["bash", str(work / "bin" / "biga-card")], cwd=work, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            orch_pid = None
            try:
                deadline = time.time() + 20
                while not pidfile.exists() and time.time() < deadline:
                    time.sleep(0.2)
                assert pidfile.exists(), "编排子进程没起来，测试前提不成立"
                orch_pid = int(pidfile.read_text())
                assert _alive(orch_pid), "编排子进程此刻应当在跑"
                # 杀掉 wrapper —— 模拟外层 timeout / 工具超时把上层 shell 收了
                proc.terminate()
                proc.wait(timeout=15)
                gone = time.time() + 6
                while _alive(orch_pid) and time.time() < gone:
                    time.sleep(0.2)
                assert not _alive(orch_pid), (
                    f"wrapper 被杀后编排子进程 {orch_pid} 还活着 —— 孤儿化敞口还开着，\n"
                    "  它会继续跑完 spawn 真花钱（教程 24 章那次事故的根因）。")
            finally:
                if proc.poll() is None:
                    proc.kill()
                if orch_pid and _alive(orch_pid):
                    try:
                        os.kill(orch_pid, signal.SIGKILL)
                    except OSError:
                        pass


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


# ─────────────────────────── 旧口径不会自己消失
#
# 深度评审在**三处代码**里找到已经被推翻的那句「`agent_runs` 是唯一凭证」。
# 其中 `db.py` 那一处最难看：修订插在旧结论**上面**、没删旧的，
# 一个 docstring 里两句话互相打脸。
#
# 🔴 教训不是「下次改仔细一点」。改口径是个**全仓动作**，
#    而人只会改自己当时正看着的那个文件。
#    ⇒ 判据搬进测试：活文档里不许再出现这句话，除非旁边就写着它已被推翻。

#: 旧口径的特征串。用「唯一凭证」而不是整句 —— 复述时措辞每次都不一样，
#: 但这三个字每次都在。
_STALE = "唯一凭证"

#: 「旁边写着它已被推翻」的标志。任一命中即算已修正。
#: ⚠️ 「推翻」是第一次跑这条守卫时补上的 —— `tests/test_store.py` 那段
#:    明明白白写着「第 7 章推翻了它」，却被判成违规。
#:    **守卫的第一次红灯里，一部分是守卫自己的问题**，不能直接照着改代码。
_DEBUNKED = ("不成立", "不是 spawn 的证明", "不能证明", "不是调用证明",
             "已修正", "曾经写的是相反的", "判断是错的", "执行账本",
             "推翻")

#: 完全豁免的地方 —— 它们**就是**历史记录，改了反而是篡改。
#: · `docs/external/` 只读（文档纪律）
#: · `CHANGELOG.md` 记的是「当时相信什么」
_HISTORY = ("docs/external/", "CHANGELOG.md")


def _stale_hits() -> list[str]:
    bad = []
    for path in repo_files(".py", ".md", ".sh"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(_HISTORY) or path == pathlib.Path(__file__).resolve():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if not any(_STALE in ln for ln in lines):
            continue
        # 过程文档写完即冻结 ⇒ 允许原文留着，但**整份文件**里必须有修正块。
        whole = "\n".join(lines)
        if rel.startswith("docs/tutorial/") and any(d in whole for d in _DEBUNKED):
            continue
        for i, ln in enumerate(lines):
            if _STALE not in ln:
                continue
            # 🔴 窗口只开 ±2，而且**同一行**就算。
            #    第一版开了 ±6 —— 探针（把 SKILL.md 的旧句子放回去）
            #    照样绿，因为下面那段修正块还落在窗口里。
            #    而那恰好就是 `db.py` 的病：旧断言与修正**同时存在**。
            #    ⇒ 判据必须是「这一句自己带着历史框」，不是「附近有人澄清过」。
            near = "\n".join(lines[max(0, i - 2):i + 3])
            if not any(d in near for d in _DEBUNKED):
                bad.append(f"{rel}:{i + 1}")
    return bad


def test_旧口径不许再出现在活文档里():
    """🔴 判据是**上下文**，不是有没有这个词。

    这句话在仓库里出现是正常的 —— `spawn_check.py` 的开头就引了它，
    紧接着写「**这句话本身不成立**」。被禁的是**孤零零地断言它**。

    ⚠️ 本文件自己豁免：描述一条「不要写 X」的规则就必须写出 X，
       于是扫描器永远第一个命中自己（`CLAUDE.md` 公开仓库纪律记过四次的形状）。
    """
    hits = _stale_hits()
    assert hits == [], (
        "这些地方还在断言已被推翻的旧口径（`agent_runs` = 调用证明）：\n"
        + "".join(f"  · {h}\n" for h in hits)
        + "  真正的判据是 tools/verify/spawn_check.py：\n"
          "  `agent_runs`（我们写的）+ 运行时 `subagent_runs`（我们碰不到的）\n"
          "  两份独立记录都齐，且都绑到同一个决策号，才算证明。\n"
          "  ⚠️ 修的时候**去删旧的那一句**，不要在它上面再写一句 ——\n"
          "     `db.py` 就是那么变成自相矛盾的。")


def test_扫到了活口径():
    """上面那条全绿，可能是因为「一个文件都没扫到」。

    这里反过来确认扫描器确实看得见这些文字 —— 否则它是平凡通过。
    """
    seen = [p for p in repo_files(".py", ".md")
            if _STALE in p.read_text(encoding="utf-8")]
    assert len(seen) >= 3, f"只扫到 {len(seen)} 处，扫描范围可能坏了"


class TestTaskRunsSource:
    """🔴 2026-09-23 · 运行时把一次 spawn 记在哪张表不是恒定的。

    实测 `BIGA-20260922-001`（至今唯一一张编排器真实产出的卡）：
    `subagent_runs` **0 行**，`task_runs`（排除 exec）**12 行**。
    原来只读 `subagent_runs` ⇒ 对那张真卡报「判不了」——一张真卡、一次真 spawn，
    核验却给不出结论。⇒ 两张都读。
    """

    def _wire(self, tmp_path, monkeypatch, ours, runs, execs=()):
        rows = [{"agent": a} for a in ours]
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: rows, raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: rows)
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(fake_runtime_db_task_runs(
            tmp_path / "rt.db", runs, execs)))

    def test_只有task_runs时也认得出spawn(self, tmp_path, monkeypatch):
        """P1：编排器路径的真实形状。去掉 task_runs 分支 ⇒ 这条红。"""
        did = "BIGA-20260922-001"
        self._wire(tmp_path, monkeypatch, ["market"], [("market", did)])
        proof = pa.spawn_proof(did)
        assert proof.readable
        assert proof.rows == 1, "task_runs 里的真 spawn 没被算进来"
        assert proof.per_agent["market"] == (True, True)

    def test_exec行不算被spawn(self, tmp_path, monkeypatch):
        """P2：`task_kind='exec'` 是 Specialist 自己跑 shell，不是「被起起来」。

        不排掉它 ⇒ 一个只跑过 exec、从未被 spawn 的 agent 会被判成 spawn 过，
        而这正是这套核验要抓的伪造形状。
        """
        did = "BIGA-20260922-002"
        # market 真被 spawn；sector 只有 exec 行
        self._wire(tmp_path, monkeypatch, ["market", "sector"],
                   [("market", did)], [("sector", did)])
        proof = pa.spawn_proof(did)
        assert proof.per_agent["market"] == (True, True)
        assert proof.per_agent["sector"] == (True, False), \
            "只有 exec 行的 agent 被当成 spawn 过了 —— exec 过滤没生效"

    def test_两张表都取不到才算判不了(self, tmp_path, monkeypatch):
        """R-3：少一张表 ≠ 读不到。两者在调用方那里是完全不同的结论——
        「零条记录」会被判成伪造，「判不了」不会。"""
        empty = tmp_path / "empty.db"
        # store-exempt: 外部运行时库的仿件（建库但一张表都不建）
        sqlite3.connect(empty).close()
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: [], raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: [])
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(empty))
        assert pa.spawn_proof("BIGA-20260922-003").readable is False

    def test_只有subagent_runs的历史卡不受影响(self, tmp_path, monkeypatch):
        """回归：C-II 之前的卡记在 `subagent_runs`，加了第二个来源之后仍然认得。"""
        did = "BIGA-20260921-001"
        rows = [{"agent": "emotion"}]
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: rows, raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: rows)
        monkeypatch.setenv("BIGA_RUNTIME_DB",
                           str(fake_runtime_db(tmp_path / "rt.db", [("emotion", did)])))
        proof = pa.spawn_proof(did)
        assert proof.readable and proof.rows == 1
        assert proof.per_agent["emotion"] == (True, True)


class TestRealSpawnPredicate:
    """🔴 2026-09-23 评审实证：`task_kind` 是运行时里的**自由文本开放列**，
    而 `agent_id` 的缺省解析会回退到发起者**自己的会话** ⇒ 任何 agent 在自己
    会话里建出来的 task 行，缺省就带 `agent_id = 它自己`。

    原来的「排除 task_kind='exec'」是排除列表，实测能被第四种 kind 绕过：
    六行 `image_generation`、agent_id 是 specialist 自己、决策号写在它自己可控
    的文本里 ⇒ 核验报「两份记录都齐」，而真实 spawn 零次。那正是 F3 要抓的。
    """

    def _wire(self, tmp_path, monkeypatch, ours, rows):
        """rows = [(run_id, agent_id, child_session_key, task_kind, task), …]"""
        db = tmp_path / "rt.db"
        conn = sqlite3.connect(db)   # store-exempt: 外部运行时库的仿件
        conn.execute("CREATE TABLE task_runs (run_id TEXT, agent_id TEXT,"
                     " child_session_key TEXT, requester_session_key TEXT,"
                     " task_kind TEXT, task TEXT, created_at INTEGER)")
        for i, (rid, aid, child, kind, task) in enumerate(rows):
            conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)",
                         (rid, aid, child, child, kind, task, 5000 + i))
        conn.commit(); conn.close()
        r = [{"agent": a} for a in ours]
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: r, raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: r)
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(db))

    def test_第四种task_kind且agent_id是自己_不算spawn(self, tmp_path, monkeypatch):
        """P1（评审要求补的那道）：换一种没见过的 `task_kind`，判据仍要挡住。

        这正是排除列表挡不住、肯定式判据能挡住的差别 —— 探针不能只钉
        `exec` 这一个字面量，否则它钉的是实例不是判据。
        """
        did = "BIGA-20260922-777"
        self._wire(tmp_path, monkeypatch, ["market"], [
            # agent 在自己会话里调工具建的行：run_id 带命名空间前缀
            (f"tool:image_generate:1", "market", "agent:market:subagent:self-1",
             "image_generation", f"画一张图，参考本次决策编号 {did}")])
        proof = pa.spawn_proof(did)
        assert proof.per_agent["market"] == (True, False), (
            "agent 在自己会话里建的 task 行被当成了 spawn —— "
            "判据退回排除列表了？")

    def test_真spawn仍然算(self, tmp_path, monkeypatch):
        """非平凡：上一条不能靠「什么都不算 spawn」通过。"""
        did = "BIGA-20260922-778"
        self._wire(tmp_path, monkeypatch, ["market"], [
            ("11111111-2222-3333-4444-555555555555", "market",
             "agent:market:subagent:abc", None, f"本次决策编号 {did}。请…")])
        assert pa.spawn_proof(did).per_agent["market"] == (True, True)

    def test_child段名与agent_id不符_不算spawn(self, tmp_path, monkeypatch):
        """伪造者把 agent_id 写成别人：两个字段对不上就不算。"""
        did = "BIGA-20260922-779"
        self._wire(tmp_path, monkeypatch, ["market"], [
            ("11111111-2222-3333-4444-666666666666", "market",
             "agent:emotion:subagent:abc", None, f"本次决策编号 {did}。请…")])
        assert pa.spawn_proof(did).per_agent["market"] == (True, False)

    def test_表在却读不了是判不了_不是零记录(self, tmp_path, monkeypatch):
        """P2（评审要求补的那道）：一张表读失败 ≠ 这个号没有记录。

        评审实测：上游把 `task` 列改名之后，真卡的结论不是「判不了」而是
        FAIL「这个号从未被 spawn 过」—— 一次读失败变成对真卡的指控。
        """
        db = tmp_path / "rt.db"
        conn = sqlite3.connect(db)   # store-exempt: 外部运行时库的仿件
        # 表在，但列名被上游改了 ⇒ 查询会炸
        conn.execute("CREATE TABLE task_runs (run_id TEXT, agent_id TEXT,"
                     " child_session_key TEXT, task_text TEXT, created_at INTEGER)")
        conn.commit(); conn.close()
        r = [{"agent": "market"}]
        monkeypatch.setattr(pa, "list_agent_runs", lambda **kw: r, raising=False)
        import _store
        monkeypatch.setattr(_store, "list_agent_runs", lambda **kw: r)
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(db))
        assert pa.spawn_proof("BIGA-20260922-780").readable is False, (
            "表在却读不了被当成了「零条记录」—— 那会让调用方判伪造")


class TestOrphanSpawnsSameSource:
    """🔴 评审抓到的「通过是因为什么都没查」：`orphan_spawns()` 落在只读
    `subagent_runs` 的旧口径上。实测 2026-09-22 那天它打出「✅ 无孤儿 spawn」，
    而当天 `subagent_runs` 0 行、`task_runs` 里有 27 次真 spawn。
    """

    def test_只有task_runs时也能发现孤儿(self, tmp_path, monkeypatch):
        db = tmp_path / "rt.db"
        conn = sqlite3.connect(db)   # store-exempt: 外部运行时库的仿件
        conn.execute("CREATE TABLE task_runs (run_id TEXT, agent_id TEXT,"
                     " child_session_key TEXT, task_kind TEXT, task TEXT,"
                     " created_at INTEGER)")
        from datetime import datetime as _dt
        lo = int(_dt.strptime("20260922", "%Y%m%d")
                 .replace(tzinfo=pa.CN_TZ).timestamp() * 1000)
        conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?)", (
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "market",
            "agent:market:subagent:x", None, "采集数据（没有决策号）", lo + 1000))
        conn.commit(); conn.close()
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(db))
        got = pa.orphan_spawns("20260922")
        assert got is not None, "两张表只有一张在，不该报判不了"
        assert [(a, why) for _, a, why in got] == [("market", "无决策号")], got

    def test_去重不能丢掉重复行携带的证据(self, tmp_path, monkeypatch):
        """🔴 回合三评审：两张表的文本字段**不是同一份文档**。

        `task_runs.task` 是渲染后的提示词、`subagent_runs.payload_json` 是 spawn
        载荷 JSON。实测临时号 `-000` 在前者出现 0 次、在后者 3 次。去重永远留
        `task_runs`（先读）⇒ 「只带临时号」这个分支在真实数据上再也不会亮：
        数量对、钱对，但读的人被静默送去错误的诊断方向
        （「无决策号」= 没带号；「只带临时号」= **占号晚于 spawn**，即 L-11）。

        ⇒ 去重是去掉重复的**行**，不该去掉那行携带的**证据**：同一个 run_id 的
        几行，扫决策号时扫它们文本的**并集**。
        """
        from datetime import datetime as _dt
        db = tmp_path / "rt.db"
        conn = sqlite3.connect(db)   # store-exempt: 外部运行时库的仿件
        conn.execute("CREATE TABLE task_runs (run_id TEXT, agent_id TEXT,"
                     " child_session_key TEXT, task_kind TEXT, task TEXT,"
                     " created_at INTEGER)")
        conn.execute("CREATE TABLE subagent_runs (run_id TEXT, child_session_key TEXT,"
                     " controller_session_key TEXT, requester_session_key TEXT,"
                     " created_at INTEGER, payload_json TEXT)")
        lo = int(_dt.strptime("20260921", "%Y%m%d")
                 .replace(tzinfo=pa.CN_TZ).timestamp() * 1000)
        rid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        # 同一次 spawn：task_runs 那份文本**不含**决策号（渲染后的提示词）
        conn.execute("INSERT INTO task_runs VALUES (?,?,?,?,?,?)", (
            rid, "sector", "agent:sector:subagent:x", None,
            "[Subagent Context] You are running as a subagent…", lo + 1000))
        # 而 subagent_runs 那份载荷里带着临时号
        conn.execute("INSERT INTO subagent_runs VALUES (?,?,?,?,?,?)", (
            rid, "agent:sector:subagent:x", "agent:main:card-1", "agent:main:card-1",
            lo + 1000, '{"runId":"%s","prompt":"…BIGA-20260921-000…"}' % rid))
        conn.commit(); conn.close()
        monkeypatch.setenv("BIGA_RUNTIME_DB", str(db))

        got = pa.orphan_spawns("20260921")
        assert len(got) == 1, f"同一次 spawn 应该只报一条，得到 {got}"
        assert got[0][1] == "sector"
        assert "临时号" in got[0][2], (
            f"证据在去重时被丢了 —— 分类成了 {got[0][2]!r}。"
            "「无决策号」与「只带临时号」指向不同根因，后者是 L-11")
