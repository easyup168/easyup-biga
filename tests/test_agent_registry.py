"""AGENT_REGISTRY —— roster 单一源的常驻守卫（确定性编排批 K）。

批 K 把散在**五处**的 roster 收编成 `_contract.AGENT_REGISTRY` 一处，其余全部派生。
这份测试钉住的不变量（编号对应分发提示词的探针 P1–P6）：

  P1  派生出的 STAGE1/STAGE2/RISK_AGENT/SNAPSHOT_INDEX_AGENTS 与收编前的手写字面量
      **逐项相等** —— 收编是空操作，不是顺手改了行为。
  P2  Card 的期望 roster 在**生成时冻结**进 `card_json`，`absent_agents` 读它，
      不随之后 Registry 变化而变（堵那处「同一张历史卡不同时间给不同答案」的静默漂移）。
  P3  老卡（`card_json` 没有这个字段）回退到读今天的 Registry，不报错、不炸。
  P4  `tools/verify/adapter_spike.py` 的 `STAGE1` 真的跟着 Registry 走 —— 收编前它是
      第五处独立字面量、且**零测试覆盖**（只 import `_runtime`，从不 import `_contract`）。
  P5  `discipline` 在册（Stage 2）但 `spawned=False` ⇒ **永不进 absent_agents 的权威**
      （裁定 13：没有输入源、从不 spawn；带回权威就是每张卡常驻一条它的缺失噪音）。
  P6  见 `test_roster_matches_config.py`：配置缺席不再静默 skip，而发可见警告（R-3）。

以及 `RISK_AGENT` 的 fail-closed 派生：Stage 2 里「唯一会被 spawn 的那个」前提破了会炸。
"""

from __future__ import annotations

import pathlib
import sys
import warnings

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tools" / "verify"))

from _contract import (  # noqa: E402
    AGENT_REGISTRY,
    EXPECTED_ROSTER,
    RISK_AGENT,
    SNAPSHOT_INDEX_AGENTS,
    STAGE1_AGENTS,
    STAGE2_AGENTS,
    STANCE_VOCAB,
    AgentDefinition,
    AgentVerdict,
    DecisionCard,
)
from _contract.registry import _sole_spawned_stage2  # noqa: E402


# ── 造最小合法对象 ──────────────────────────────────────────────────────────
def _v(agent: str, task_id: str = "BIGA-20260101-001") -> AgentVerdict:
    """一个通过铁律的最小 verdict：PASS、无缺失、无 result ⇒ 无需证据覆盖。"""
    return AgentVerdict(task_id=task_id, agent=agent, status="completed",
                        verdict="PASS", result={}, evidence=(), missing=())


def _card(present, *, expected_roster=None, from_store=False,
          decision_id="BIGA-20260101-001") -> DecisionCard:
    """一张最小合法卡。`from_store=True` 让「缺席多于 missing」降级为警告而非拒绝
    —— 这些测试要观察 `absent_agents`，不测 `_check_roster` 的宽严（那有专门的测试）。
    """
    return DecisionCard(
        decision_id=decision_id, status="WAIT", headline="t",
        verdicts=[_v(a, decision_id) for a in present], synthesis="", model_ref="m",
        from_store=from_store, expected_roster=expected_roster)


def _old_card_dict(present, decision_id="BIGA-20260101-002") -> dict:
    """模拟**老** card_json —— 用真 DecisionCard 序列化后**删掉** `expected_roster`
    键（批 K 之前落库的卡根本没有这个键）。用真对象而不是手搓一个 card 形状的
    字典，是为了不踩 `test_contract_single_impl` 的「字典版契约」扫描（铁律 4）。
    """
    d = _card(present, from_store=True, decision_id=decision_id).to_dict()
    d.pop("expected_roster", None)   # 老卡的 JSON 里没有这个键
    return d


# ── P1 · 派生 == 收编前的手写字面量（收编是空操作）─────────────────────────
class TestP1DerivedEqualsOldLiterals:
    def test_STAGE1_逐项等于旧元组(self):
        assert STAGE1_AGENTS == ("market", "sector", "news", "technical", "emotion")

    def test_STAGE2_逐项等于旧元组(self):
        assert STAGE2_AGENTS == ("risk", "discipline")

    def test_RISK_AGENT等于旧字面量(self):
        assert RISK_AGENT == "risk"

    def test_SNAPSHOT_INDEX_AGENTS等于旧frozenset(self):
        assert SNAPSHOT_INDEX_AGENTS == frozenset({"market", "sector", "technical"})

    def test_确实是从registry派生的_不是又一份手写(self):
        # 与实现同式：证明这些常量的值来自 AGENT_REGISTRY 过滤，而非另一份平行清单。
        assert STAGE1_AGENTS == tuple(d.agent_id for d in AGENT_REGISTRY if d.stage == 1)
        assert STAGE2_AGENTS == tuple(d.agent_id for d in AGENT_REGISTRY if d.stage == 2)
        assert SNAPSHOT_INDEX_AGENTS == frozenset(
            d.agent_id for d in AGENT_REGISTRY if d.reads_snapshot)
        assert EXPECTED_ROSTER == tuple(d.agent_id for d in AGENT_REGISTRY if d.spawned)


# ── Registry 自身的不变量 ────────────────────────────────────────────────────
class TestRegistryInvariants:
    def test_stage只有1和2(self):
        assert all(d.stage in (1, 2) for d in AGENT_REGISTRY)

    def test_agent_id无重复(self):
        ids = [d.agent_id for d in AGENT_REGISTRY]
        assert len(ids) == len(set(ids)), f"名册里有重名：{ids}"

    def test_STANCE_VOCAB是registry的子集(self):
        # 分发提示词「做什么」#2：STANCE_VOCAB 保持独立，但对 Registry 断言子集关系。
        reg = {d.agent_id for d in AGENT_REGISTRY}
        assert set(STANCE_VOCAB) <= reg, set(STANCE_VOCAB) - reg

    def test_EXPECTED_ROSTER今天等于STANCE_VOCAB的key集(self):
        # 🔴 这条钉住：absent_agents 的权威从「STANCE_VOCAB key 集」换成「EXPECTED_ROSTER」
        #    今天是**空操作**（两者相等）。哪天有人让某个 spawn 的 agent 没有 stance 词表、
        #    或反之，这条会红 —— 强迫他确认 absent_agents 的行为是不是真该跟着变。
        assert set(EXPECTED_ROSTER) == set(STANCE_VOCAB)

    def test_discipline在册且在STAGE2_但不spawn_不进权威(self):
        # 裁定 13 的直接后果，Registry 引入后必须继续成立（探针 P5 的静态一半）。
        assert "discipline" in {d.agent_id for d in AGENT_REGISTRY}
        assert "discipline" in STAGE2_AGENTS
        assert "discipline" not in EXPECTED_ROSTER
        assert "discipline" not in STANCE_VOCAB
        [disc] = [d for d in AGENT_REGISTRY if d.agent_id == "discipline"]
        assert disc.spawned is False


# ── RISK_AGENT 的 fail-closed 派生 ───────────────────────────────────────────
class TestRiskAgentDerivationFailsClosed:
    def test_正常_恰好一个spawn的Stage2(self):
        assert _sole_spawned_stage2(AGENT_REGISTRY) == RISK_AGENT == "risk"

    def test_两个spawn的Stage2会炸(self):
        two = (AgentDefinition("risk", 2, True, False),
               AgentDefinition("discipline", 2, True, False))
        with pytest.raises(RuntimeError, match="Stage 2 恰好应有一个"):
            _sole_spawned_stage2(two)

    def test_零个spawn的Stage2也炸(self):
        zero = (AgentDefinition("discipline", 2, False, False),)
        with pytest.raises(RuntimeError, match="Stage 2 恰好应有一个"):
            _sole_spawned_stage2(zero)


# ── P2 · 卡级冻结名单不随 Registry 后续变化 ──────────────────────────────────
class TestP2FrozenRoster:
    def test_冻结的卡读自己那份_老卡才跟着今天的registry变(self, monkeypatch):
        import _contract.card as card_mod

        frozen = _card(["market"], expected_roster=("market", "sector"), from_store=True)
        old = _card(["market"], expected_roster=None, from_store=True)

        # 人为「改变 AGENT_REGISTRY」—— absent_agents 的回退源就是 card 模块里的
        # EXPECTED_ROSTER（`from .registry import EXPECTED_ROSTER`）。
        monkeypatch.setattr(card_mod, "EXPECTED_ROSTER", ("market", "zzz-new-agent"))

        # 冻结卡：读它**生成时**冻结的那份，不受 Registry 变化影响。
        assert frozen.absent_agents == ("sector",)
        # 老卡（没冻结）：这才回退到「今天的 Registry」，于是跟着变。
        assert old.absent_agents == ("zzz-new-agent",)

    def test_两张卡各读各的冻结名单(self):
        a = _card(["market"], expected_roster=("market", "sector"), from_store=True)
        b = _card(["market"], expected_roster=("market", "news", "risk"), from_store=True)
        assert a.absent_agents == ("sector",)
        assert b.absent_agents == ("news", "risk")

    def test_expected_roster往返不丢(self):
        card = _card(["market"], expected_roster=("market", "sector", "risk"),
                     from_store=True)
        again = DecisionCard.from_dict(card.to_dict())
        assert again.expected_roster == ("market", "sector", "risk")


# ── P3 · 老卡（无该字段）回退到今天的 Registry ───────────────────────────────
class TestP3OldCardCompat:
    def test_老卡没有字段_from_dict得到None(self):
        restored = DecisionCard.from_dict(_old_card_dict(["market"]))
        assert restored.expected_roster is None

    def test_老卡的absent_agents回退到今天的EXPECTED_ROSTER_不炸(self):
        restored = DecisionCard.from_dict(_old_card_dict(["market", "sector"]))
        assert set(restored.absent_agents) == set(EXPECTED_ROSTER) - {"market", "sector"}
        assert "discipline" not in restored.absent_agents


# ── P4 · adapter_spike.py 的 STAGE1 跟着 Registry 走 ─────────────────────────
class TestP4AdapterSpikeMigrated:
    def test_adapter_spike的STAGE1派生自contract(self):
        # 收编前这个脚本零测试覆盖（不 import _contract、不在 pytest 下跑）。
        # 现在它 `from _contract import STAGE1_AGENTS`，这条测试第一次盖住它。
        import adapter_spike
        assert adapter_spike.STAGE1 == STAGE1_AGENTS, (
            "adapter_spike.STAGE1 与契约的 STAGE1_AGENTS 不一致 —— "
            "它又变回一份独立字面量了（普查点名的第三处漂移风险）。")


# ── P5 · discipline 永不出现在 absent_agents 的权威里 ─────────────────────────
class TestP5DisciplineNeverAbsent:
    def test_只有一个agent作答时_缺席里也没有discipline(self):
        # 权威是 EXPECTED_ROSTER（spawn 的那几个），discipline 不在其中 ⇒ 永不「缺席」。
        card = _card(["market"], from_store=True)
        assert "discipline" not in card.absent_agents
        assert set(card.absent_agents) == set(EXPECTED_ROSTER) - {"market"}

    def test_即使卡冻结名单来自含discipline的registry_也不会带上它(self):
        # EXPECTED_ROSTER 就是「排除 discipline 之后」的 spawn 集 —— 用它当冻结名单，
        # discipline 依旧进不来（模拟「Registry 里明明有 discipline 这条」的场景）。
        card = _card(["market"], expected_roster=EXPECTED_ROSTER, from_store=True)
        assert "discipline" not in card.absent_agents


# ── P6 · skipif 缺口：配置缺席发可见警告，不静默 skip ─────────────────────────
class TestP6RosterConfigGapSignalsVisibly:
    def test_config缺席时发RuntimeConfigUnavailable警告而非静默skip(self, monkeypatch):
        import test_roster_matches_config as trmc

        monkeypatch.setattr(trmc, "CONFIG",
                            pathlib.Path("/nonexistent-biga/openclaw.json"))
        with warnings.catch_warnings(record=True) as rec:
            warnings.simplefilter("always")
            with pytest.raises(pytest.skip.Exception):
                trmc._require_runtime_config()
        assert any(isinstance(w.message, trmc.RuntimeConfigUnavailable) for w in rec), (
            "配置缺席时没有发出可见的 RuntimeConfigUnavailable 警告 —— "
            "又退回到 R-3 想防的静默 skip 了。")

    def test_契约vs已建这半边不需要配置_照常能跑(self, monkeypatch):
        # 反过来钉住：不依赖运行时配置的那半边检查，不应被配置缺席拖累（原 skipif 会）。
        import test_roster_matches_config as trmc

        monkeypatch.setattr(trmc, "CONFIG",
                            pathlib.Path("/nonexistent-biga/openclaw.json"))
        # 直接调它——不碰 _require_runtime_config，所以配置在不在都无所谓，不 skip。
        trmc.TestRosterConsistency().test_契约里的stage名单都建好了()
