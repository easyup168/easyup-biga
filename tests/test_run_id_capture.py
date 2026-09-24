"""批 J-I · run_id capture 贯穿全链 —— 四道探针 P1–P4 + 2b 继承证明。

只做 capture 不做 enforce：run_id 能被存下来、传下去，但**不改任何现有的判定/过滤
逻辑**。这些探针验的是「存进去、传下来、追得到」，以及「历史行没有它也不报错」。

🔴 G-1：每道探针都能红。红灯演练（弄坏 → 报红 → 还原）记在 CHANGELOG。
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import (  # noqa: E402
    AgentAssessment,
    AgentVerdict,
    Evidence,
    FactBundle,
    MissingItem,
    VerdictRef,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_outcome,
    load_verdict,
    load_verdict_meta,
    save_assessment,
    save_evidence_set,
    save_fact_bundle,
    verify_verdict_refs,
)
from _roster import absent_registrations  # noqa: E402

TID = "BIGA-20260918-001"
#: 一个真实形状的 run_id（32 位 hex，与 decision_runs.run_id 同形）。
RID = "cd5af37977fa4db9a5c2f1424cfc8001"


def _load(name: str, rel: str):
    # 🔴 幂等：已经有别的测试文件 `_load` 过就**复用那个实例**，绝不用新实例覆写
    #    sys.modules。否则 orchestrator.py 在它自己 import 时绑定的 card_ops 与本文件
    #    覆写后的不是同一个对象 —— test_orchestrator 的 monkeypatch.setattr(card_ops,
    #    "persist", …) 打在新实例上、orchestrator 却调旧实例，patch 静默落空（全量里
    #    才复现的跨文件污染）。模块本就该按名字单例。
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


card_ops = _load("card_ops", "skills/decision-card/scripts/card_ops.py")


def _fact(agent="emotion") -> FactBundle:
    t = now_cn()
    return FactBundle(
        task_id=TID, agent=agent, status="completed", verdict="PASS",
        result={"limit_up_count": 78}, data_completeness=1.0,
        evidence=[Evidence(field="limit_up_count", source="em:x", value=78,
                           as_of=t - timedelta(seconds=60), retrieved_at=t)],
        missing=[], elapsed_ms=8400)


def _verdict(agent="emotion") -> AgentVerdict:
    """一份合体 AgentVerdict —— synthesize 的 verdicts 要的是 AgentVerdict 对象。"""
    t = now_cn()
    return AgentVerdict(
        task_id=TID, agent=agent, status="completed", verdict="PASS",
        result={"limit_up_count": 78}, data_completeness=1.0,
        evidence=[Evidence(field="limit_up_count", source="em:x", value=78,
                           as_of=t - timedelta(seconds=60), retrieved_at=t)],
        missing=[], stance="亢奋", elapsed_ms=8400)


#: 单 agent（emotion）的卡：批 P 之后 roster 判据要求**每个缺席 agent 各有一条
#: 解得出它名字的登记**，凑数的占位 code 不再成立（那正是评审 §18 指出的洞）。
_SLOT_MISSING = absent_registrations(["emotion"])
_SLOT_CODES = tuple(m.code for m in _SLOT_MISSING)


def _judgment():
    return card_ops.Judgment(
        status="WAIT", headline="核心矛盾一句话", synthesis="理由",
        extra_missing=list(_SLOT_MISSING))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))   # card_ops.* 走默认库路径靠它
    init_schema(p)
    return p


def _run_id_of(db, verdict_id):
    with connect(db, readonly=True) as c:
        return c.execute("SELECT run_id FROM agent_verdicts WHERE verdict_id=?",
                         (verdict_id,)).fetchone()[0]


# ───────────────────────────────────── P1 · 在线落库真的把 run_id 存进去了


class TestP1_Persisted:
    def test_P1_fact行落库带run_id(self, db):
        """🔴 不是「没报错」，是取出来比对字符串。"""
        fid = save_fact_bundle(_fact(), run_id=RID, path=db)
        assert _run_id_of(db, fid) == RID

    def test_P1_evidence_set落库带run_id(self, db):
        """item 4 的存储端：freeze 时编排器把 ctx.run_id 传进 save_evidence_set。"""
        save_evidence_set(evidence_set_id="es-probe-1", decision_id=TID,
                          manifest={"kind": "x"}, run_id=RID, path=db)
        with connect(db, readonly=True) as c:
            got = c.execute("SELECT run_id FROM evidence_sets WHERE evidence_set_id=?",
                            ("es-probe-1",)).fetchone()[0]
        assert got == RID


# ───────────────────────────────────── 2b · assessment 从 fact 行继承 run_id


class TestInheritRunId:
    """🔴 交接问题 4：为什么「继承」在结构上不可能与事实行不一致。

    答案分两半，各一条测试：
      ① save_assessment **没有 run_id 参数** —— Agent 根本无从传一个不同的值（结构证明）。
      ② run_id 的唯一来源是 `meta`（按 fact_id 取回的那一行）—— 存进去的 assessment.run_id
         永远逐字节等于 fact.run_id（行为证明），fact 有值继承有值、fact 是 None 继承 None。
    """

    def test_2b结构证明_save_assessment没有run_id参数(self):
        params = inspect.signature(save_assessment).parameters
        assert "run_id" not in params, (
            "save_assessment 一旦开一个 run_id 参数，Agent 就能经 amend_verdict.py 手传、"
            "就可能与它 amends 的 fact 行不一致 —— 2b 的全部意义是让它只能从 meta 继承，"
            "命令行上够不到这个值。")

    def test_2b行为证明_assessment的run_id等于fact的(self, db):
        fid = save_fact_bundle(_fact(), run_id=RID, path=db)
        aid = save_assessment(
            AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"),
            fact_id=fid, path=db)
        assert _run_id_of(db, aid) == _run_id_of(db, fid) == RID

    def test_2b行为证明_fact无run_id则assessment也无(self, db):
        """继承 None，不是凭空造一个 —— capture 不 enforce。"""
        fid = save_fact_bundle(_fact(), path=db)           # 不传 run_id
        aid = save_assessment(
            AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"),
            fact_id=fid, path=db)
        assert _run_id_of(db, fid) is None
        assert _run_id_of(db, aid) is None


# ───────────────────────────────────── P2 · 没有 run_id 的历史行读得回来、不被拒


class TestP2_HistoricalNull:
    def test_P2_无run_id的行走全部读取路径都不报错(self, db):
        fid = save_fact_bundle(_fact(), path=db)   # run_id 落成 NULL（模拟迁移前数据）
        # 现有的三条读取路径都不因 run_id 是 None 就报错
        assert load_verdict(fid, path=db) is not None
        assert load_outcome(fid, path=db) is not None
        meta = load_verdict_meta(fid, path=db)
        assert meta is not None and meta["run_id"] is None      # 读回来是 None，不炸

    def test_P2_VerdictRef和Card用run_id_None构造合法(self, db):
        fid = save_fact_bundle(_fact(), path=db)
        verdicts, refs = card_ops.load_verdicts_and_refs([fid])
        assert refs[0].run_id is None                          # 从 NULL 列搬过来是 None
        card = card_ops.synthesize(
            decision_id=TID, verdicts=verdicts, judgment=_judgment(),
            model_ref="test", verdict_refs=refs)              # 不传 run_id
        assert card.run_id is None
        # 核对路径也不因 run_id 缺失就报不一致
        assert verify_verdict_refs(card, path=db) == []

    def test_P2_历史卡json没有run_id键也能读回(self):
        """真·历史卡：序列化里根本没有 run_id 这个键（迁移前落的）。"""
        from _contract import DecisionCard
        # 故意造一份**没有 run_id 键**的历史卡 JSON（迁移前序列化那时还没这个字段）——
        # 用 DecisionCard().to_dict() 造不出「缺键」样本（它总会带上 run_id）。
        d = {  # contract-exempt: 验 from_dict 对「缺 run_id 键」的向后兼容，见上
            "decision_id": TID, "status": "WAIT", "headline": "h",
            "verdicts": [_verdict().to_dict()],
            "missing": [m.to_dict() for m in _SLOT_MISSING],
            "synthesis": "", "model_ref": "m", "generated_at": now_cn().isoformat(),
            "elapsed_ms": 0,
            # 🔴 故意不放 run_id / input_verdict_refs 键
        }
        card = DecisionCard.from_dict(d)
        assert card.run_id is None                            # .get 缺省 None，不是 KeyError


# ───────────────────────────────────── P3 · 合成（非回放）卡与 ref 追到同一个 run_id


class TestP3_Synthesize:
    def test_P3_card和verdictref都追到同一个ctx_run_id(self, db):
        fid = save_fact_bundle(_fact(), run_id=RID, path=db)
        verdicts, refs = card_ops.load_verdicts_and_refs([fid])
        # VerdictRef 从存量行搬到了 run_id
        assert refs[0].run_id == RID
        # 合成（historical=False，即在线路径，不经 replay）：run_id 从 ctx 填入
        card = card_ops.synthesize(
            decision_id=TID, verdicts=verdicts, judgment=_judgment(),
            model_ref="test", verdict_refs=refs, run_id=RID)
        assert card.run_id == RID
        # 两条血缘追到同一个
        assert card.run_id == card.input_verdict_refs[0].run_id == RID


# ───────────────────────────────────── P4 · 回放不给历史卡凭空捏造 run_id


class TestP4_ReplayNoFabrication:
    def _online_card(self, run_id):
        # 用纯函数构造一张「在线」卡（不落库，够验 run_id 传播）
        v = _verdict()
        ref = VerdictRef(agent=v.agent, verdict_id=1,
                         content_sha256="a" * 64, contract_version="contract/1",
                         run_id=run_id)
        return card_ops.synthesize(
            decision_id=TID, verdicts=[v], judgment=_judgment(),
            model_ref="test", verdict_refs=[ref], run_id=run_id)

    def test_P4_回放历史卡run_id保持None不被捏造(self):
        original = self._online_card(run_id=None)             # 历史卡：无 run_id
        # 回放：synthesize 不传 run_id（replay.py 就是这么调的）
        replayed = card_ops.synthesize(
            historical=True, decision_id=TID, verdicts=list(original.verdicts),
            judgment=_judgment(), model_ref="test",
            verdict_refs=list(original.input_verdict_refs))
        assert replayed.run_id is None                        # 没有凭空造一个

    def test_P4_回放带run_id的卡_check仍一致(self):
        """一旦在线路径产出带 run_id 的卡，回放它 --check 不能因『这次执行≠上次』误判。

        回放诚实地把 run_id 记成 None（不捏造原来那次的号），而 comparable() 把 run_id
        与 generated_at/elapsed_ms 一同剥掉 ⇒ 组装一致仍然成立。
        """
        original = self._online_card(run_id=RID)              # 在线卡：带 run_id
        replayed = card_ops.synthesize(
            historical=True, decision_id=TID, verdicts=list(original.verdicts),
            judgment=_judgment(), model_ref="test",
            verdict_refs=list(original.input_verdict_refs))   # 不传 run_id
        assert original.run_id == RID and replayed.run_id is None   # 不捏造
        # 🔴 顶层 run_id 被 comparable 剥掉 ⇒ 组装一致
        assert card_ops.comparable(original) == card_ops.comparable(replayed)
        # ⚠️ 但 ref 自带的 run_id **不剥** —— 它是判定原件的血缘，回放照原样带过去，两边相同
        assert card_ops.comparable(original)["input_verdict_refs"][0]["run_id"] == RID
