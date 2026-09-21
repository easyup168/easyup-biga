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

    @staticmethod
    def _seeded_repo(tmp_path, did: str, spawn_stub: str):
        """造一个能走完出卡路径的沙盒。**不联网、不花钱。**

        两个桩：
          · `BIGA` → 直接往库里落一张真卡（替掉 agent 调用）
          · `spawn_check.py` → 由调用方决定退出码
        """
        import shutil
        work = tmp_path / "repo"
        shutil.copytree(REPO, work, symlinks=True, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "data", ".pytest_cache", ".claude", "memory"))
        db = tmp_path / "t.db"

        seed = tmp_path / "seed.py"
        seed.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(work / 'skills')!r})\n"
            "from _contract import AgentVerdict, DecisionCard, Evidence, now_cn\n"
            "from _store import init_schema, save_card\n"
            "t = now_cn()\n"
            f"TID = {did!r}\n"
            "v = AgentVerdict(task_id=TID, agent='market', status='completed',\n"
            "                 verdict='PASS', result={'x': 1}, confidence=1.0,\n"
            "                 stance='分化', elapsed_ms=1,\n"
            "                 evidence=[Evidence(field='x', source='s', value=1,\n"
            "                                    as_of=t, retrieved_at=t)])\n"
            f"init_schema({str(db)!r})\n"
            "save_card(DecisionCard(decision_id=TID, status='WAIT', headline='h',\n"
            "                       verdicts=[v], synthesis='', model_ref='m'),\n"
            f"          path={str(db)!r})\n", encoding="utf-8")

        stub = tmp_path / "fake-biga"
        stub.write_text(f"#!/usr/bin/env bash\n{sys.executable} {seed}\n",
                        encoding="utf-8")
        stub.chmod(0o755)
        (work / "tools" / "verify" / "spawn_check.py").write_text(
            spawn_stub, encoding="utf-8")
        return work, db, stub

    @staticmethod
    def _run(work, db, stub):
        import os
        import subprocess
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
