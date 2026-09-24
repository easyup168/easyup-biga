"""批 E-III：risk 迁到 FactBundle + 退役 amend_verdict.py 旧路径。Facts/Assessment 拆分收官。

risk 不是市场/板块那种迁移 —— 它的 stance 是 `VETO_STANCE`（"否决"），制衡层唯一能拦住
BUY 的信号。前五个迁错了 stance 后果是「这句话不准」；这一个迁错了后果是「一个真该被拦的
决策放行了」。所以探针清单比前面多一条不能省的：VETO 必须能从 `AgentAssessment` 一路
穿透到 `DecisionCard` 真正拦截它的那一层。

  P1  risk 产 `FactBundle`（不是 `AgentVerdict`），落库 kind='fact'
  P2  🔴 VETO 穿透：risk fact + AgentAssessment(stance=否决) → 走 card_ops → DecisionCard
      **真的拦住 BUY**（断到消费方用它做判断的地方，不是断"stance 字段等于否决"）
  P3  旧路径退役：对历史合体行跑旧命令 → 明确报错（不是静默改库）；退役的代码真的没了
  P4  _assess_fact 对 risk 的 fact 行正常：--stance 成功、--add-missing 被拒（证明保留的
      那条路没被这一批破坏，换 risk 这个 agent 名字）
  P5  回归：card_ops 聚合**六个全新形状**的 Specialist 不丢 —— Facts/Assessment 拆分的
      最终验收（E-I 的 P3 → E-II 的 P4 → 这里六个全是新形状）

全部离线。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    VETO_STANCE,
    AgentAssessment,
    AgentVerdict,
    Evidence,
    FactBundle,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_verdict_meta,
    save_assessment,
    save_fact_bundle,
    save_verdict,
)

TID = "BIGA-20260302-001"

#: 批 O：fact 行要归属到一次真实的执行尝试（`load_verdict_ids_for_run` 按 run 取）。
_RID = "f" * 32


def _load(name: str, rel: str):
    # 🔴 幂等：已经有别的测试文件 `_load` 过就**复用那个实例**，绝不用新实例覆写
    #    sys.modules。否则 orchestrator.py 在它自己 import 时绑定的 card_ops 与本文件
    #    覆写后的不是同一个对象 —— test_orchestrator 的 monkeypatch.setattr(card_ops,
    #    "persist", …) 打在新实例上、orchestrator 却调旧实例，patch 静默落空（全量里
    #    才复现的跨文件污染，见 test_run_id_capture.py / 教程第 32 章）。
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


risk = _load("risk_check", "skills/risk-check/scripts/risk_check.py")
amend = _load("amend_verdict", "skills/decision-card/scripts/amend_verdict.py")
card_ops = _load("card_ops", "skills/decision-card/scripts/card_ops.py")

from _provenance import open_test_run  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    open_test_run(p, run_id=_RID)     # 批 O：fact 要归属到一次真实的执行尝试
    return p


def _ev(field, value=1.0):
    t = now_cn()
    return Evidence(field=field, source=f"derived:{field}", value=value,
                    as_of=t - timedelta(seconds=60), retrieved_at=t)


def _up(agent, field="trade_date", value="20260302"):
    """一条上游 AgentVerdict 桩（risk 读它）。"""
    t = now_cn()
    stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
    return AgentVerdict(
        task_id=TID, agent=agent, status="completed", verdict="PASS",
        result={field: value}, data_completeness=1.0,
        evidence=[Evidence(field=field, source=f"derived:{agent}", value=value,
                           as_of=t - timedelta(seconds=60), retrieved_at=t)],
        stance=stance, elapsed_ms=1)


# ───────────────────────────────────────────── P1 · risk 产 FactBundle


class TestP1RiskFactBundle:
    def test_risk产FactBundle不是AgentVerdict(self, db, monkeypatch):
        store = {1: _up("market"), 2: _up("emotion")}
        monkeypatch.setattr(risk, "load_verdict", lambda vid: store.get(vid))
        fb = risk.build_fact_bundle(verdict_ids=[1, 2], store=False, task_id=TID)
        assert type(fb) is FactBundle, f"risk 产的是 {type(fb).__name__}，不是 FactBundle"
        assert not hasattr(fb, "stance")

    def test_落库后kind是fact(self, db, monkeypatch):
        store = {1: _up("market"), 2: _up("emotion")}
        monkeypatch.setattr(risk, "load_verdict", lambda vid: store.get(vid))
        fb = risk.build_fact_bundle(verdict_ids=[1, 2], store=False, task_id=TID)
        fid = save_fact_bundle(fb)
        assert load_verdict_meta(fid)["kind"] == "fact"


# ─────────────────────────────────── P2 · VETO 穿透（核心交付物）


def _save_outcome(agent, stance, *, missing=None):
    """落一条 fact 行 + 一条 AgentAssessment，返回 assessment 的 id
    （load_verdict 多态会把它压回带 stance 的 AgentVerdict）。"""
    fb = FactBundle(
        task_id=TID, agent=agent,
        status="completed" if not missing else "partial",
        verdict="PASS" if not missing else "WARNING",
        result={f"{agent}_x": 1}, data_completeness=1.0,
        evidence=[_ev(f"{agent}_x")], missing=list(missing or []))
    fid = save_fact_bundle(fb)
    return save_assessment(AgentAssessment(task_id=TID, agent=agent, stance=stance),
                           fact_id=fid)


def _six(db, *, risk_stance):
    """六个全新形状的 Specialist（fact + assessment），risk 的 stance 可指定。"""
    non_veto = {"market": "放量上涨", "emotion": "修复", "sector": "主线明确",
                "technical": "多头", "news": "平静"}
    ids = [_save_outcome(a, s) for a, s in non_veto.items()]
    ids.append(_save_outcome("risk", risk_stance))
    return ids


class TestP2VetoPenetration:
    """🔴 P2：risk 的否决经「fact + AgentAssessment(否决)」→ save → load_verdict 多态
    → to_agent_verdict() → DecisionCard 读 `.stance` 拦截。断到消费方真正拦 BUY 的地方。
    """

    def test_否决时stance经多态压回AgentVerdict(self, db):
        ids = _six(db, risk_stance=VETO_STANCE)
        verdicts, _refs = card_ops.load_verdicts_and_refs(ids)
        risk_v = next(v for v in verdicts if v.agent == "risk")
        # 前置断言：否决真的穿过了新形状，到了 DecisionCard 会读的那个字段
        assert risk_v.stance == VETO_STANCE, "否决没有从 AgentAssessment 穿透到 .stance"

    def test_P2_否决真的拦住BUY(self, db):
        ids = _six(db, risk_stance=VETO_STANCE)
        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        # 🔴 核心：不是断言 stance=='否决'，是断到 DecisionCard 真正用它做判断的那一层 ——
        #    给 BUY 会被契约层拒（制衡层的否决权不可被合成阶段绕过）。
        j = card_ops.Judgment(status="BUY", headline="核心矛盾", synthesis="理由")
        with pytest.raises(ValueError, match="否决"):
            card_ops.synthesize(decision_id=TID, verdicts=verdicts, judgment=j,
                                model_ref="test", verdict_refs=refs)

    def test_否决必须体现为AVOID或BLOCK_WAIT被拒(self, db):
        ids = _six(db, risk_stance=VETO_STANCE)
        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        # WAIT（再看看）软化了否决（不要做）—— 被拒
        j = card_ops.Judgment(status="WAIT", headline="核心矛盾", synthesis="理由")
        with pytest.raises(ValueError, match="否决"):
            card_ops.synthesize(decision_id=TID, verdicts=verdicts, judgment=j,
                                model_ref="test", verdict_refs=refs)
        # AVOID 正确体现否决 —— 放行
        j2 = card_ops.Judgment(status="AVOID", headline="核心矛盾", synthesis="理由")
        card = card_ops.synthesize(decision_id=TID, verdicts=verdicts, judgment=j2,
                                   model_ref="test", verdict_refs=refs)
        assert card.status == "AVOID"

    def test_不否决时BUY不被veto拦(self, db):
        # 反面对照：risk 放行时，同样六个agent、无缺失，BUY 不被否决权拦
        ids = _six(db, risk_stance="放行")
        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        j = card_ops.Judgment(status="BUY", headline="核心矛盾", synthesis="理由")
        card = card_ops.synthesize(decision_id=TID, verdicts=verdicts, judgment=j,
                                   model_ref="test", verdict_refs=refs)
        assert card.status == "BUY"  # 没有否决 ⇒ BUY 能过（证明拦的是否决本身）


# ─────────────────────────────────── P3 · 旧路径退役


class TestP3OldPathRetired:
    def test_历史合体行的旧命令报退役不静默(self, db, capsys):
        from _store import save_verdict  # 仍保留，用来造老形状
        vid = save_verdict(AgentVerdict(
            task_id=TID, agent="market", status="completed", verdict="PASS",
            result={"sh_close": 3000.0}, data_completeness=1.0,
            evidence=[_ev("sh_close", 3000.0)], stance="放量上涨", elapsed_ms=1))
        rc = amend.main(["--ref", str(vid), "--add-missing", "market.trend.no_history",
                         "x", "--verdict", "WARNING", "--stance", "放量上涨"])
        assert rc == 2
        assert "退役" in capsys.readouterr().err

    def test_退役的代码真的没了(self):
        src = (REPO / "skills/decision-card/scripts/amend_verdict.py").read_text(encoding="utf-8")
        # 旧路径的三个标志物都不该再出现在**代码**里（docstring/注释里提到不算）
        import ast
        tree = ast.parse(src)
        code_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "dataclasses" not in code_names, "dataclasses.replace 老路径没删干净"
        # save_verdict 不该再被 amend 调用（它退的是"活的产出方"，函数本身留在 _store）
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "save_verdict" not in called
        assert "save_assessment" in called  # 保留的新路径还在用


# ─────────────────────────────────── P4 · _assess_fact 对 risk 正常


class TestP4AssessFactRisk:
    def _risk_fact(self, db, *, verdict="PASS", missing=None):
        fb = FactBundle(
            task_id=TID, agent="risk",
            status="completed" if not missing else "partial", verdict=verdict,
            result={"coverage_ratio": 1.0}, data_completeness=1.0,
            evidence=[_ev("coverage_ratio", 1.0)], missing=list(missing or []))
        return save_fact_bundle(fb, run_id=_RID)

    def test_risk_fact行加否决stance成功(self, db):
        from _store import load_outcome, load_verdict_ids_for_run
        fid = self._risk_fact(db)
        rc = amend.main(["--ref", str(fid), "--stance", VETO_STANCE])
        assert rc == 0
        # 判断落在**新的一行**（AgentAssessment），事实那行没被重打
        aid = load_verdict_ids_for_run(_RID)["risk"]
        assert aid != fid
        oc = load_outcome(aid)
        assert oc.stance == VETO_STANCE
        assert oc.fact.result["coverage_ratio"] == 1.0  # 事实原样在

    def test_risk_fact行加add_missing被拒(self, db, capsys):
        fid = self._risk_fact(db)
        rc = amend.main(["--ref", str(fid), "--add-missing", "risk.x.y", "限制"])
        assert rc == 2
        assert "--stance" in capsys.readouterr().err


# ─────────────────────────────────── P5 · 六个全新形状聚合不丢


class TestP5AllSixNewForm:
    def test_六个全新形状card聚合不丢(self, db):
        ids = _six(db, risk_stance="放行")
        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        assert {v.agent for v in verdicts} == {
            "market", "emotion", "sector", "technical", "news", "risk"}
        # 六个全是新形状（fact+assessment），stance 都经多态压回
        for v in verdicts:
            assert v.stance is not None, f"{v.agent} 的 stance 没被压回 AgentVerdict"
        card = card_ops.synthesize(
            decision_id=TID, verdicts=verdicts,
            judgment=card_ops.Judgment(status="WAIT", headline="x", synthesis="y"),
            model_ref="test", verdict_refs=refs)
        assert len(card.verdicts) == 6


# ─────────────────────────────── P4 · risk 双跑同一决策（批 F 唯一索引守卫）


class TestBatchFDoubleFactRejected:
    """🔴 批 F / P4：risk 万一没听新提示词、又自己跑了一遍 `risk_check.py --task-id
    <同一个 did>`，第二次落 fact 必须**报明确错**（不是裸 IntegrityError），不静默覆盖、
    也不静默产生两条并存原件。这条洞在批 F 之前是**静默**的：fact 行 amends 恒 NULL，
    v6 的线性修订唯一索引（WHERE amends IS NOT NULL）天生管不到它 —— v11 的
    ux_fact_per_task_agent 才堵上。"""

    def _five_upstream(self, db):
        """库里放 5 个真实上游 verdict，返回它们的 id（供 --verdict-ids 用）。"""
        return [save_verdict(_up(a), path=db)
                for a in ("market", "sector", "news", "technical", "emotion")]

    def test_P4_CLI双跑同一did_第二次退出2且报明确错(self, db, capsys):
        ids = self._five_upstream(db)
        idarg = ",".join(str(i) for i in ids)
        # 第一次（首次落库，模拟编排器已落 / 或人首次手跑）：正常
        rc1 = risk.main(["--verdict-ids", idarg, "--task-id", TID])
        assert rc1 in (0, 2), f"首次跑 risk_check 应正常，rc={rc1}"
        # 第二次（模拟 risk 没听话、又跑一遍同一个 did）：被拒、退出 2、报明确错
        rc2 = risk.main(["--verdict-ids", idarg, "--task-id", TID])
        assert rc2 == 2, f"第二次落 fact 应被拒并退出 2，rc={rc2}"
        err = capsys.readouterr().err
        assert "已经有一份 fact" in err          # 说清楚撞了什么
        assert "IntegrityError" not in err        # 不是裸 IntegrityError
        assert "--no-store" in err                # 指路：手工看输出加 --no-store
        # 没有静默并存：库里仍只有一条 risk fact
        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM agent_verdicts WHERE task_id=? "
                          "AND agent='risk' AND kind='fact'", (TID,)).fetchone()[0]
        assert n == 1, f"第二次落 fact 后不该并存两条，实际 {n}"

    def test_P4_红灯_删掉唯一索引则第二次静默并存(self, db):
        """🔴 G-1 红灯（内联）：把 ux_legacy_fact_per_task_agent 删掉，第二次 save_fact_bundle
        就**静默成功、并存两条** —— 证明这道守卫真的在拦，不是恒过。tmp 库、跑完即弃。"""
        fb = FactBundle(task_id=TID, agent="risk", status="completed", verdict="PASS",
                        result={"coverage_ratio": 1.0}, data_completeness=1.0,
                        evidence=[_ev("coverage_ratio")])
        save_fact_bundle(fb, path=db)
        with connect(db) as c:
            # 批 O：v11 的 ux_fact_per_task_agent 已拆成两条分区索引；这条 CLI
            #    路径不带 --run-id ⇒ 落的是 run_id IS NULL 的行 ⇒ 受 legacy 那条管。
            c.execute("DROP INDEX ux_legacy_fact_per_task_agent")
        # 索引没了 ⇒ 第二次不再 IntegrityError，静默插入第二条
        save_fact_bundle(fb, path=db)
        with connect(db, readonly=True) as c:
            n = c.execute("SELECT COUNT(*) FROM agent_verdicts WHERE task_id=? "
                          "AND agent='risk' AND kind='fact'", (TID,)).fetchone()[0]
        assert n == 2, "删了唯一索引后第二条应能静默落库 —— 这正是守卫在防的洞"
