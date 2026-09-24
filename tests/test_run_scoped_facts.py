"""批 O：Fact 的身份从 `(task_id, agent)` 换成 `(run_id, agent)`（外部评审 B-2…B-5）。

这一批做的是评审 §6.3/§6.4 那半：**同一个 Decision 的第二个 Run 要能保存自己的
Fact，并且只读得到自己的**。批 N 在卡那一层把 cross-run 污染拦成硬失败；这里从根上
让它不可能发生。

探针清单：

  P1  schema v17：两条分区索引在、v11 那条**不在了**（留着它等于这批什么都没做）。
  P2  同一个 run 里同一个 agent 落两次 fact ⇒ 拒（run 分区真的在管）。
  P3  🔴 **同一个 decision 的两个 run 各自落同一个 agent 的 fact ⇒ 都成功**
      —— 评审回归测试「同一 Decision 两个 Run 可以分别保存同 Agent Fact」。
      这是整批的正面判据：它在 v17 之前是**做不到**的。
  P4  `run_id IS NULL` 的历史行仍按 `(task_id, agent)` 管唯一 —— 拆索引不能把
      v11 堵上的那个洞对历史数据重新打开。
  P5  两条索引撞上时报错**分流**：一个说"本次执行尝试里已经有了"，一个说"历史行"。
      混成一句话会把「同一次执行写了两遍」说成「这个决策号已经有 fact 了」，
      而后者在批 O 之后是合法的。
  P6  🔴 `load_verdict_ids_for_run` 只返回**本 run** 的原件 —— 评审回归测试
      「Retry 不读取旧 Run Verdict」。判据是 run，不是 decision。
  P7  空 run_id 一律拒绝（fail closed）：空值会让查询退化成「取所有历史行」。
  P8  `latest_verdict_ids` 真的退役了：import 不到，且生产代码里没有残留引用。

🔴 每条都做过 sabotage 验证，记录见 CHANGELOG 批 O。
"""

from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import (  # noqa: E402
    AgentAssessment,
    Evidence,
    FactBundle,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_verdict_ids_for_run,
    save_assessment,
    save_fact_bundle,
)
from _provenance import open_test_run  # noqa: E402

DID = "BIGA-20260925-001"
RUN_A = "a" * 32
RUN_B = "b" * 32


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    open_test_run(p, decision_id=DID, run_id=RUN_A)
    open_test_run(p, decision_id=DID, run_id=RUN_B)
    return p


def _fact(agent="market", did=DID, value=1.0) -> FactBundle:
    t = now_cn()
    return FactBundle(
        task_id=did, agent=agent, status="completed", verdict="PASS",
        result={f"{agent}_x": value}, data_completeness=1.0,
        evidence=[Evidence(field=f"{agent}_x", source=f"derived:{agent}", value=value,
                           as_of=t - timedelta(seconds=60), retrieved_at=t)], missing=[])


# ══════════════════════════════════════════════════════════════ P1 · schema
class TestSchemaV17:

    @pytest.mark.parametrize("index", ["ux_fact_per_run_agent",
                                       "ux_legacy_fact_per_task_agent"])
    def test_两条分区索引都在(self, db, index):
        with connect(db, readonly=True) as c:
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name=?",
                            (index,)).fetchone()
        assert sql is not None, f"{index} 不存在"
        assert "WHERE" in sql["sql"].upper(), (
            f"{index} 必须是**分区**索引 —— 不带 WHERE 的话两条会互相盖住语义")

    def test_v11那条旧索引已经不在了(self, db):
        """🔴 留着它这批就白做了：它按 (task_id, agent) 管**全部** fact 行，
        会继续拦住第二个 run 的合法写入。"""
        with connect(db, readonly=True) as c:
            row = c.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                            ("ux_fact_per_task_agent",)).fetchone()
        assert row is None, "ux_fact_per_task_agent 还在 —— 第二个 run 仍然写不进自己的 fact"


# ═══════════════════════════════════════════════════ P2 / P3 / P4 · 唯一约束
class TestFactIdentityIsRunScoped:

    def test_同一个run里同一个agent不许落两次(self, db):
        save_fact_bundle(_fact(), run_id=RUN_A, path=db)
        with pytest.raises(ValueError, match="已经有一份 fact 原件了"):
            save_fact_bundle(_fact(value=2.0), run_id=RUN_A, path=db)

    def test_同一个decision的两个run各自落同一个agent都成功(self, db):
        """🔴 整批的正面判据（评审回归测试）。v17 之前这是**做不到**的 ——
        第二次会撞 `ux_fact_per_task_agent`，而编排器不会因此停，转头用上一轮的
        旧原件合成一张看起来很新的卡（真机 PoC 复现过）。"""
        a = save_fact_bundle(_fact(value=1.0), run_id=RUN_A, path=db)
        b = save_fact_bundle(_fact(value=2.0), run_id=RUN_B, path=db)
        assert a != b
        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM agent_verdicts WHERE task_id=? "
                          "AND agent='market' AND kind='fact'", (DID,)).fetchone()[0]
        assert n == 2, "同一个决策号名下、两个 run 各一份 —— 这正是 B-2 要的形状"

    def test_历史行仍按task_id管唯一(self, db):
        """拆索引不能把 v11 堵上的洞对历史行重新打开。

        🔴 非平凡：`(run_id, agent)` 对 run_id 为 NULL 的行退化成 `(NULL, agent)`，
        而 SQLite 里多行 NULL **不算重复** —— 只有 legacy 那条分区索引管得住它们。
        """
        save_fact_bundle(_fact(), path=db)                      # run_id=None
        with pytest.raises(ValueError, match="已经有一份 fact 原件了"):
            save_fact_bundle(_fact(value=2.0), path=db)

    def test_历史行与run行互不干扰(self, db):
        """两条索引的 WHERE 互斥：同一个 (task_id, agent) 既可以有一条历史行，
        也可以有各 run 自己的那份。"""
        save_fact_bundle(_fact(), path=db)                      # 历史行
        save_fact_bundle(_fact(value=2.0), run_id=RUN_A, path=db)
        save_fact_bundle(_fact(value=3.0), run_id=RUN_B, path=db)
        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM agent_verdicts WHERE task_id=? "
                          "AND agent='market' AND kind='fact'", (DID,)).fetchone()[0]
        assert n == 3


# ═══════════════════════════════════════════════════════════ P5 · 报错分流
class TestErrorsAreDistinguishable:

    def test_撞run分区时说的是本次执行尝试(self, db):
        save_fact_bundle(_fact(), run_id=RUN_A, path=db)
        with pytest.raises(ValueError) as e:
            save_fact_bundle(_fact(value=2.0), run_id=RUN_A, path=db)
        assert "本次执行尝试" in str(e.value) and RUN_A in str(e.value)

    def test_撞legacy分区时说的是历史行(self, db):
        save_fact_bundle(_fact(), path=db)
        with pytest.raises(ValueError) as e:
            save_fact_bundle(_fact(value=2.0), path=db)
        msg = str(e.value)
        assert "不带 run_id" in msg, "要说清是历史行那条分区，不是「本次执行尝试」"
        assert "--run-id" in msg, "报错要指路：手工跑 skill 落库请带 --run-id"

    def test_两种报错都不是裸IntegrityError(self, db):
        for run_id in (RUN_A, None):
            save_fact_bundle(_fact(agent=f"a{run_id!s:.1}"), run_id=run_id, path=db)
            with pytest.raises(ValueError) as e:
                save_fact_bundle(_fact(agent=f"a{run_id!s:.1}", value=2.0),
                                 run_id=run_id, path=db)
            assert "IntegrityError" not in str(e.value)


# ══════════════════════════════════════════════════ P6 / P7 · 按 run 取原件
class TestLoadVerdictIdsForRun:

    def test_只返回本run的原件(self, db):
        """🔴 评审回归测试「Retry 不读取旧 Run Verdict」。

        Run A 落了 market / news；Run B 只落了 market。按 decision 聚合的话
        Run B 会读到 **Run A 的 news**（评审 §6.2 那张表）；按 run 取读不到。
        """
        save_fact_bundle(_fact("market"), run_id=RUN_A, path=db)
        save_fact_bundle(_fact("news"), run_id=RUN_A, path=db)
        save_fact_bundle(_fact("market", value=9.0), run_id=RUN_B, path=db)

        assert sorted(load_verdict_ids_for_run(RUN_A, path=db)) == ["market", "news"]
        got_b = load_verdict_ids_for_run(RUN_B, path=db)
        assert sorted(got_b) == ["market"], (
            f"Run B 读到了不属于它的原件：{sorted(got_b)} —— 这正是 cross-run 污染")

    def test_取到amend链的tip(self, db):
        """assessment 的 run_id 从它 amends 的 fact 行继承 ⇒ 按 run 取一样拿得到 tip。"""
        fid = save_fact_bundle(_fact(), run_id=RUN_A, path=db)
        aid = save_assessment(AgentAssessment(task_id=DID, agent="market",
                                              stance="放量上涨"), fact_id=fid, path=db)
        assert load_verdict_ids_for_run(RUN_A, path=db)["market"] == aid

    def test_历史行取不到(self, db):
        """`run_id IS NULL` 的行不属于任何一次执行尝试，在线路径不该读到它们。"""
        save_fact_bundle(_fact(), path=db)
        assert load_verdict_ids_for_run(RUN_A, path=db) == {}

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_空run_id一律拒绝(self, db, bad):
        """🔴 fail closed：空值会让 `WHERE run_id=?` 退化成「取所有 NULL 行」——
        正好是按 run 取要防的那件事，而且不会报错。"""
        with pytest.raises(ValueError, match="非空 run_id"):
            load_verdict_ids_for_run(bad, path=db)


# ═════════════════════════════════════════════════════════ P8 · 旧接口退役
class TestLegacyAggregatorRetired:

    def test_latest_verdict_ids_已经import不到(self):
        with pytest.raises(ImportError):
            from _store import latest_verdict_ids  # noqa: F401

    def test_全仓没有残留引用(self):
        """🔴 判据是**仓库 AST 扫描**，不是「我记得都改了」—— 漏一处就是一条按
        decision 聚合的活路径，而那正是 cross-run 污染的入口。

        用 AST 而不是文本匹配：叙述性文字（docstring 里说明「它取代了谁」）**不算**
        引用，按文本扫会把说明本身判成违例，于是只能靠删说明来过测试 —— 那是拿
        「为什么这么改」换一个绿灯。
        """
        import ast
        from _scan import repo_files
        offenders = []
        for f in repo_files():
            path = REPO / f
            if path.suffix != ".py" or not path.is_file():
                continue
            if path.resolve() == pathlib.Path(__file__).resolve():
                continue          # 本文件写着这个名字是为了断言它没了
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                name = (node.id if isinstance(node, ast.Name)
                        else node.attr if isinstance(node, ast.Attribute)
                        else node.name if isinstance(node, (ast.alias,)) and False
                        else None)
                if name == "latest_verdict_ids":
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
                if isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        if a.name == "latest_verdict_ids":
                            offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert not offenders, (
            "还有按 decision 聚合的引用：" + ", ".join(sorted(set(offenders))))
