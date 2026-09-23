"""决策编号的归属 —— 一次真实事故的回归测试。

事故（2026-09-21 盘中）
------------------------
09:37 与 09:39 各起了一次端到端。结果：

* 五个 specialist 的 verdict **全部**写着 `BIGA-20260921-001`
* 合成出来的卡却是 `BIGA-20260921-006`
* 两次运行的证据混进同一张卡，**没有任何字段能把它们分开**

两层原因：

1. 五个 specialist 都写着 `new_task_id(1)` —— 序号硬编码。
   这个 bug 在 `synthesize.py` 上修过一次，**兄弟模块一个没查**。
2. 更根本的：编号在**合成时**才分配，而那时证据早采完了。
   一个决策在收集证据之前就该有身份，否则证据无处归属。

本文件钉死修复后的三条性质。
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    ADHOC_TASK_SEQ,
    AgentVerdict,
    Evidence,
    is_adhoc_task_id,
    new_task_id,
    now_cn,
)
from _store import db  # noqa: E402


def _verdict(agent: str, task_id: str, *, stance: str | None = None) -> AgentVerdict:
    return AgentVerdict(
        agent=agent, task_id=task_id, status="completed", verdict="PASS",
        stance=stance,
        result={"trade_date": "2026-09-18"},
        evidence=[Evidence(field="trade_date", value="2026-09-18",
                           source="probe", as_of=now_cn(), retrieved_at=now_cn())],
    )


class TestAdhocNotStorable:
    """临时号可以看，不可以入账。"""

    def test_临时号落库被拒(self, tmp_path):
        p = tmp_path / "t.db"
        db.init_schema(p)
        with pytest.raises(ValueError) as e:
            db.save_verdict(_verdict("market", new_task_id(ADHOC_TASK_SEQ)), path=p)
        msg = str(e.value)
        # 报错要指路（开发流程第 8 条）：不只说错了，还要说怎么办
        assert "--no-store" in msg and "new_decision.py" in msg, \
            f"报错没有指路：{msg}"

    def test_正经号可以落库(self, tmp_path):
        p = tmp_path / "t.db"
        db.init_schema(p)
        assert db.save_verdict(_verdict("market", "BIGA-20260921-007"), path=p) > 0

    def test_守卫在唯一写入口而不是五个调用点(self):
        """🔴 这条是本次修复的要点。

        原来的 bug 之所以能同时存在于五个 specialist，就是因为每个调用点
        各写一遍默认值。守卫必须放在 `save_verdict` 里 —— 调用点会越来越多。
        """
        # 🔴 批 H-I：db.py 的真实实现已迁至 src/easyup_biga/persistence/；旧路径
        #    skills/_store/db.py 现在只是薄壳（无 save_verdict 定义）。这里读的是
        #    「守卫写在唯一写入口里」，必须读真实定义所在的文件，读到薄壳会
        #    StopIteration（找不到 save_verdict）——那是守卫在看错的地方。
        src = (REPO / "src/easyup_biga/persistence/db.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "save_verdict")
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "is_adhoc_task_id" in calls, "守卫不在唯一写入口里"


class TestNoHardcodedSeq:
    """兄弟模块检查 —— 教程 14 要点 9 的机器版。"""

    def test_没有人再硬编码序号1(self):
        bad = []
        for f in (REPO / "skills").glob("*/scripts/*.py"):
            src = f.read_text(encoding="utf-8")
            for i, line in enumerate(src.splitlines(), 1):
                if "new_task_id(1)" in line and not line.lstrip().startswith("#"):
                    bad.append(f"{f.relative_to(REPO)}:{i}")
        assert not bad, (
            f"又出现硬编码序号：{bad}\n"
            "  当天第二次决策会撞号，且多个 specialist 会写出同一个 task_id。\n"
            "  手工运行用 new_task_id(ADHOC_TASK_SEQ)，真决策由 Stage 0 下发。")


class TestReservationIsAtomic:
    """占号靠主键冲突仲裁，不靠「先查再插」。

    🔴 外部评审 F17：这个类原来**名不副实** —— 文档字符串宣称的是并发属性，
    而所有调用都是同进程同线程的顺序调用，**没有任何真正的竞争窗口**。
    与本项目自己抓到过的「并发测试探针改错地方」是同一种形状。

    评审两路各自用 multiprocessing 补了它没做的事（80 进程 ×2 轮 /
    30 进程 ×1 轮），结论一致：编号全部唯一，**底层机制本身是对的**。
    ⇒ 所以这里不是修 bug，是**让测试名副其实**：
      把真并发这一条固化下来，否则将来有人把重试逻辑改坏
      （比如误吞 `OperationalError` 而不只是 `IntegrityError`），
      这个类仍然会一路绿灯。
    """

    def test_多进程同时占号不重号(self, tmp_path):
        """真起 OS 进程，用 Barrier 卡在同一时刻一起冲。

        ⚠️ 用 multiprocessing 不用 threading：GIL 会让线程版
        「看起来并发、实际串行」，那正是这条测试要避免的假象。
        """
        import multiprocessing as mp

        p = tmp_path / "t.db"
        db.init_schema(p)
        n = 16

        def worker(barrier, q, path):
            import sys
            sys.path.insert(0, str(REPO / "skills"))
            from _store import db as d
            barrier.wait()
            try:
                q.put(d.reserve_decision_id(by="race", path=path))
            except Exception as e:                      # noqa: BLE001
                q.put(f"ERR {type(e).__name__}: {e}")

        ctx = mp.get_context("fork")
        barrier, q = ctx.Barrier(n), ctx.Queue()
        procs = [ctx.Process(target=worker, args=(barrier, q, p)) for _ in range(n)]
        for x in procs:
            x.start()
        for x in procs:
            x.join(timeout=60)

        got = [q.get(timeout=5) for _ in range(n)]
        errs = [g for g in got if str(g).startswith("ERR")]
        assert not errs, f"占号抛异常：{errs[:3]}"
        assert len(set(got)) == n, f"{n} 个进程拿到 {len(set(got))} 个不同的号：{sorted(got)}"

    def test_连续占号不重复且从1开始(self, tmp_path):
        p = tmp_path / "t.db"
        db.init_schema(p)
        got = [db.reserve_decision_id(by="t", path=p) for _ in range(5)]
        assert len(set(got)) == 5
        assert got[0].endswith("-001"), "序号 0 是临时号，不能分配给真决策"
        assert not any(is_adhoc_task_id(g) for g in got)

    def test_已出的卡也算占用(self, tmp_path):
        """两张表都要看 —— 只看一张就会重号。"""
        p = tmp_path / "t.db"
        db.init_schema(p)
        first = db.reserve_decision_id(by="t", path=p)
        # 直接往 decision_records 塞一张卡，绕过分配器
        from _contract import DecisionCard
        card = DecisionCard(
            decision_id=new_task_id(9), status="WAIT", headline="h",
            verdicts=[_verdict("market", new_task_id(9))],
            synthesis="s", model_ref="m",
            missing=[f"占位{i}——本文件不测 roster" for i in range(5)])
        db.save_card(card, path=p)
        nxt = db.reserve_decision_id(by="t", path=p)
        assert nxt not in (first, new_task_id(9))


class TestSynthesizeRejectsMixedRuns:
    """合成阶段的闸门：不同决策的证据不得合成一张卡。"""

    def test_混血被拒绝并指路(self, tmp_path):
        p = tmp_path / "t.db"
        db.init_schema(p)
        a = db.save_verdict(_verdict("market", "BIGA-20260921-007"), path=p)
        b = db.save_verdict(_verdict("emotion", "BIGA-20260921-008"), path=p)
        r = subprocess.run(
            [sys.executable, str(REPO / "skills/decision-card/scripts/synthesize.py"),
             "--verdict-ids", f"{a},{b}", "--status", "WAIT",
             "--headline", "h", "--synthesis", "s", "--model-ref", "m"],
            capture_output=True, text=True, env={**__import__("os").environ,
                                                 "BIGA_DB_PATH": str(p)})
        assert r.returncode == 1, "两次决策的证据被合成了同一张卡"
        assert "不止一次决策" in r.stderr and "怎么办" in r.stderr


class TestSynthesizeReusesUpstreamId:
    """没给 --decision-id 时，用证据自己带的号，不要另分配一个。

    实测 BIGA-20260921-014：Stage 0 占了 013 并传给五个 specialist，
    合成时忘了 --decision-id ⇒ 卡是 014、证据全写着 013。
    混血闸门不会红（号是一致的），但归属仍然断了。
    """

    def test_沿用上游的号(self, tmp_path):
        p = tmp_path / "t.db"
        db.init_schema(p)
        from _contract import STANCE_VOCAB
        ids = [db.save_verdict(_verdict(a, "BIGA-20260921-013",
                                        stance=STANCE_VOCAB[a][0]), path=p)
               for a in ("market", "emotion")]
        # 🔴 F-8：2 个 agent 到场，另外 4 个天然缺席——roster 判据按计数
        #    比较，4 条 --extra-missing 才够。
        extra_missing_args = []
        for i in range(4):
            extra_missing_args += ["--extra-missing", "supervisor.agent_offline",
                                   f"占位{i}——本文件不测 roster"]
        r = subprocess.run(
            [sys.executable, str(REPO / "skills/decision-card/scripts/synthesize.py"),
             "--verdict-ids", ",".join(map(str, ids)), "--status", "WAIT",
             "--headline", "h", "--synthesis", "s", "--model-ref", "m",
             *extra_missing_args,
             "--json"],
            capture_output=True, text=True,
            env={**__import__("os").environ, "BIGA_DB_PATH": str(p)})
        assert r.returncode == 0, r.stderr[-600:]
        assert __import__("json").loads(r.stdout)["decision_id"] == "BIGA-20260921-013"


# ══ 外部评审 P2-2：Stage 0 必须能在空环境里独立跑 ═════════════
#
# `new_decision.py` 在**全新的库**上直接抛
# `sqlite3.OperationalError: unable to open database file` ——
# 因为算下一个序号走的是 readonly 连接，而文件还不存在。
#
# Stage 0 是整条链路的第一步。它跑不起来，「自包含的入口」这个说法就不成立。


class TestStageZeroWorksOnFreshDb:
    def test_空库上占号不报错(self, tmp_path):
        p = tmp_path / "never-existed.db"
        assert not p.exists()
        got = db.reserve_decision_id(by="t", path=p)
        assert got.endswith("-001")
        assert p.exists(), "占号之后库应该被建出来"

    def test_连着占两次序号递增(self, tmp_path):
        p = tmp_path / "fresh.db"
        a = db.reserve_decision_id(by="t", path=p)
        b = db.reserve_decision_id(by="t", path=p)
        assert (a, b) == (a, a[:-3] + "002")

    def test_CLI在空库上也能跑(self, tmp_path):
        """判据是**真跑一遍 CLI** —— 库层能跑不代表入口能跑。"""
        import os
        p = tmp_path / "cli.db"
        r = subprocess.run(
            [sys.executable, str(REPO / "skills/decision-card/scripts/new_decision.py")],
            capture_output=True, text=True,
            env={**os.environ, "BIGA_DB_PATH": str(p)})
        assert r.returncode == 0, r.stderr[-400:]
        assert r.stdout.strip().endswith("-001")
