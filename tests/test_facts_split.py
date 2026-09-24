"""批 E-I：Facts / Assessment 拆分 —— 契约 + 存储 + 试点 emotion。全部离线。

把焊在一起的 `AgentVerdict` 拆成 `FactBundle`（事实）+ `AgentAssessment`（判断），
`AgentOutcome` 是组合视图。这里钉住六道探针：

  P1  历史 AgentVerdict 走 LegacyAdapter → FactBundle+AgentAssessment 逐字段一致
  P2  写路径严：新落库路径不收旧形状 / 缺必需字段的新形状被拒
  P3  新旧并存：5 个旧 AgentVerdict + 1 个新三型，Card 聚合全部 6 个，不丢试点
  P4  CROSS_CHECK 新判据：两条都有 evidence_set_id 就比它；一条没有则退回 raw_hash
  P5  跨型铁律：UNKNOWN 的事实上挂方向判断，在 AgentOutcome/save_assessment 被拒
  P6  回归：未迁移的 market 走 amend_verdict.py 老路径，与这一批之前一字不差
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    AgentAssessment,
    AgentOutcome,
    AgentVerdict,
    Evidence,
    FactBundle,
    LegacyAdapter,
    MissingItem,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_outcome,
    load_verdict_ids_for_run,
    load_verdict,
    save_assessment,
    save_fact_bundle,
    save_verdict,
)
from _provenance import open_test_run  # noqa: E402

TID = "BIGA-20260918-001"


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


card_ops = _load("card_ops", "skills/decision-card/scripts/card_ops.py")
amend = _load("amend_verdict", "skills/decision-card/scripts/amend_verdict.py")


def _ev(field: str, value=1.0, *, raw_hash=None, evidence_set_id=None) -> Evidence:
    t = now_cn()
    return Evidence(field=field, source=f"em:{field}", value=value,
                    as_of=t - timedelta(seconds=60), retrieved_at=t,
                    raw_hash=raw_hash, evidence_set_id=evidence_set_id)


def _emotion_verdict(stance="亢奋") -> AgentVerdict:
    """一份**历史形状**的 emotion AgentVerdict（事实 + stance 焊在一起）。"""
    return AgentVerdict(
        task_id=TID, agent="emotion", status="completed", verdict="PASS",
        result={"limit_up_count": 78, "broken_rate": 0.24},
        data_completeness=0.9,
        evidence=[_ev("limit_up_count", 78), _ev("broken_rate", 0.24)],
        warnings=["盘中读数"], missing=[], stance=stance, elapsed_ms=8400)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


# ───────────────────────────────────── P1 · LegacyAdapter 逐字段还原


class TestLegacyAdapter:
    def test_P1_历史verdict拆成新三型逐字段一致(self):
        v = _emotion_verdict(stance="亢奋")
        fb, a = LegacyAdapter.split(v)
        # 事实逐字段对得上
        assert fb.task_id == v.task_id and fb.agent == v.agent
        assert fb.status == v.status and fb.verdict == v.verdict
        assert dict(fb.result) == dict(v.result)
        assert fb.data_completeness == v.data_completeness
        assert [e.to_dict() for e in fb.evidence] == [e.to_dict() for e in v.evidence]
        assert list(fb.warnings) == list(v.warnings)
        assert [m.to_dict() for m in fb.missing] == [m.to_dict() for m in v.missing]
        assert fb.elapsed_ms == v.elapsed_ms
        # 判断对得上
        assert a is not None and a.stance == v.stance
        # 组合回去 == 原件（不是「能跑」，是内容对得上）
        assert LegacyAdapter.to_outcome(v).to_agent_verdict().to_dict() == v.to_dict()

    def test_无stance的历史verdict拆出assessment为None(self):
        v = _emotion_verdict(stance=None)
        fb, a = LegacyAdapter.split(v)
        assert a is None
        assert LegacyAdapter.to_outcome(v).to_agent_verdict().to_dict() == v.to_dict()


# ───────────────────────────────────── P2 · 写路径严


class TestWriteStrict:
    def test_P2_新落库路径不收旧AgentVerdict(self, db):
        with pytest.raises(TypeError, match="只接受.*FactBundle"):
            save_fact_bundle(_emotion_verdict(), path=db)  # type: ignore[arg-type]

    def test_assessment必须挂在fact行上(self, db):
        # 一条旧 AgentVerdict 落库（legacy 行），拿它当 fact_id → 拒绝
        vid = save_verdict(_emotion_verdict(stance=None), path=db)
        with pytest.raises(ValueError, match="不是 FactBundle"):
            save_assessment(AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"),
                            fact_id=vid, path=db)

    def test_assessment缺stance无法构造(self):
        with pytest.raises(ValueError, match="必须带 stance"):
            AgentAssessment(task_id=TID, agent="emotion", stance=None)  # type: ignore[arg-type]


class TestFactBundleInvariants:
    """🔴 FactBundle 走的是与 AgentVerdict **同一份** `check_fact_invariants`（L-3：单一
    实现）。这里钉住 FactBundle 也守铁律 —— 探针 D 会把那份共用实现弄坏，断言
    AgentVerdict 和 FactBundle **一起**红（证明真的是一份，不是各写一遍）。
    """

    def test_missing非空不许PASS(self):
        with pytest.raises(ValueError, match="铁律 1"):
            FactBundle(task_id=TID, agent="emotion", status="partial", verdict="PASS",
                       result={}, missing=[MissingItem("缺", "emotion.x.y")])

    def test_result字段必须有证据(self):
        with pytest.raises(ValueError, match="铁律 3"):
            FactBundle(task_id=TID, agent="emotion", status="completed", verdict="PASS",
                       result={"limit_up_count": 78}, evidence=[])


# ───────────────────────────────────── P3 · 新旧并存


class TestCoexistence:
    def _legacy(self, agent, db, *, missing=None, stance=None):
        st = stance or next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
        v = AgentVerdict(
            task_id=TID, agent=agent, status="completed" if not missing else "partial",
            verdict="PASS" if not missing else "WARNING",
            result={f"{agent}_x": 1.0}, data_completeness=1.0,
            evidence=[_ev(f"{agent}_x")],
            missing=list(missing or []), stance=st, elapsed_ms=100)
        return save_verdict(v, path=db)

    def test_P3_五旧一新_Card聚合全部六个(self, db):
        ids = []
        # 5 个旧 AgentVerdict（其中 market 带一条 missing，用来确认它没被丢）
        ids.append(self._legacy("market", db,
                                missing=[MissingItem("成交额缺", "market.turnover.x")]))
        for ag in ("sector", "news", "technical", "risk"):
            ids.append(self._legacy(ag, db))
        # 1 个新三型（emotion）：fact + assessment，带一条自己的 missing
        fb = FactBundle(task_id=TID, agent="emotion", status="partial", verdict="WARNING",
                        result={"limit_up_count": 78}, data_completeness=0.5,
                        evidence=[_ev("limit_up_count", 78)],
                        missing=[MissingItem("情绪周期无历史", "emotion.cycle.no_history")])
        fid = save_fact_bundle(fb, path=db)
        aid = save_assessment(AgentAssessment(task_id=TID, agent="emotion", stance="修复"),
                              fact_id=fid, path=db)
        ids.append(aid)

        # card_ops 走的就是 load_verdicts_and_refs —— 新旧都压成 AgentVerdict
        verdicts, refs = card_ops.load_verdicts_and_refs(ids)
        agents = {v.agent for v in verdicts}
        assert agents == {"market", "sector", "news", "technical", "risk", "emotion"}
        # 🔴 试点那一个真的带进来了：它的 stance 与 missing 都在
        emo = next(v for v in verdicts if v.agent == "emotion")
        assert emo.stance == "修复"
        assert any(m.code == "emotion.cycle.no_history" for m in emo.missing)

        # 合成一张卡，断言两条 missing（market + emotion）都聚合进去，一条都没丢
        card = card_ops.synthesize(
            decision_id=TID, verdicts=verdicts,
            judgment=card_ops.Judgment(status="WAIT", headline="核心矛盾", synthesis="理由"),
            model_ref="test", verdict_refs=refs)
        codes = {m.code for m in card.missing}
        assert "market.turnover.x" in codes and "emotion.cycle.no_history" in codes
        assert len(card.verdicts) == 6

    def test_按run取到assessment行(self, db):
        """批 O：改用 `load_verdict_ids_for_run`（原 `latest_verdict_ids` 已退役）。

        顺带钉住 assessment 的 run_id **从 fact 行继承**这条不变量——不继承的话
        按 run 取就会漏掉 tip，只拿到 fact 行。
        """
        rid = open_test_run(db, decision_id=TID)
        fb = FactBundle(task_id=TID, agent="emotion", status="completed", verdict="PASS",
                        result={"limit_up_count": 78}, data_completeness=1.0,
                        evidence=[_ev("limit_up_count", 78)])
        fid = save_fact_bundle(fb, run_id=rid, path=db)
        aid = save_assessment(AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"),
                              fact_id=fid, path=db)
        # tip 是 assessment 行；load_verdict 把它压回带 stance 的 AgentVerdict
        assert load_verdict_ids_for_run(rid, path=db)["emotion"] == aid
        assert load_verdict(aid, path=db).stance == "亢奋"


# ───────────────────────────────────── P4 · CROSS_CHECK 新判据


risk = _load("risk_check", "skills/risk-check/scripts/risk_check.py")


def _up(agent, field, *, raw_hash=None, evidence_set_id=None):
    t = now_cn()
    stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
    ev = [Evidence(field=field, source="sina:kline/sh000001", value=3911.0,
                   as_of=t - timedelta(seconds=60), retrieved_at=t,
                   raw_hash=raw_hash, evidence_set_id=evidence_set_id),
          Evidence(field="trade_date", source="sina:kline/sh000001", value="20260918",
                   as_of=t - timedelta(seconds=60), retrieved_at=t,
                   raw_hash=raw_hash, evidence_set_id=evidence_set_id)]
    return AgentVerdict(task_id=TID, agent=agent, status="completed", verdict="PASS",
                        result={field: 3911.0, "trade_date": "20260918"},
                        data_completeness=1.0, evidence=ev, stance=stance, elapsed_ms=1)


class TestCrossCheckEvidenceSet:
    @pytest.fixture()
    def wired(self, monkeypatch):
        store: dict[int, AgentVerdict] = {}
        monkeypatch.setattr(risk, "load_verdict", lambda vid: store.get(vid))
        return store

    def test_P4_都有evidence_set_id就比它_不同则报(self, wired):
        wired[1] = _up("market", "sh_close", evidence_set_id="es-AAA")
        wired[2] = _up("technical", "close", evidence_set_id="es-BBB")
        v = risk.build_fact_bundle(verdict_ids=[1, 2], store=False, task_id=TID)
        xconf = v.result["cross_check_conflict"]
        assert xconf, "不同 evidence_set_id 却没报冲突"
        # 🔴 冲突必须由 **evidence_set_id 判据本身**报出，不能是 raw_hash 兜底凑巧也报。
        #    两个 _up 都没给 raw_hash（都是 None）：若偏好失效退回比 raw_hash，会因
        #    「两条都无 raw_hash」印一条「无法核实」的冲突 —— 一条假绿。只断言「报了冲突」
        #    锚不住这个特性（评审实测：禁用偏好后本条仍绿）。锚死在只有 es-id 路径才印的
        #    「冻结集」+ 两个 es-id 值上：raw_hash 兜底那条文案里既没有「冻结集」也没有 es-id。
        msg = " ".join(xconf)
        assert "冻结集" in msg and "es-AAA" in msg and "es-BBB" in msg, (
            f"冲突不是由 evidence_set_id 判据报出的（疑似退回了 raw_hash 兜底）：{xconf}")
        assert any(m.code == "risk.upstream.cross_check_conflict" for m in v.missing)

    def test_同一个evidence_set_id不报(self, wired):
        wired[1] = _up("market", "sh_close", evidence_set_id="es-SAME")
        wired[2] = _up("technical", "close", evidence_set_id="es-SAME")
        v = risk.build_fact_bundle(verdict_ids=[1, 2], store=False, task_id=TID)
        # 批 R 起 result 的值是递归冻结的：list → tuple（序列化后仍是数组，
        # 卡片与落库逐字节不变）。这里比的是**内存形态**，所以写 ()。
        assert v.result["cross_check_conflict"] == ()

    def test_P4_一条没有evidence_set_id则退回raw_hash_不跳过(self, wired):
        # market 有 es-id、technical 没有（老 Specialist）→ 退回比 raw_hash
        wired[1] = _up("market", "sh_close", evidence_set_id="es-X", raw_hash="HASH_A")
        wired[2] = _up("technical", "close", raw_hash="HASH_B")  # 无 es-id
        v = risk.build_fact_bundle(verdict_ids=[1, 2], store=False, task_id=TID)
        # 不是「因为缺字段就跳过」——退回 raw_hash 比较，HASH_A≠HASH_B ⇒ 报冲突
        assert v.result["cross_check_conflict"]


# ───────────────────────────────────── P5 · 跨型铁律


class TestCrossTypeInvariant:
    def _unknown_fact(self):
        return FactBundle(task_id=TID, agent="emotion", status="partial", verdict="UNKNOWN",
                          result={}, missing=[MissingItem("全缺", "emotion.pool.unavailable")])

    def test_P5_UNKNOWN事实挂方向判断_AgentOutcome拒绝(self):
        with pytest.raises(ValueError, match="UNKNOWN"):
            AgentOutcome(fact=self._unknown_fact(),
                         assessment=AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"))

    def test_P5_save_assessment也拒绝(self, db):
        fid = save_fact_bundle(self._unknown_fact(), path=db)
        with pytest.raises(ValueError, match="UNKNOWN"):
            save_assessment(AgentAssessment(task_id=TID, agent="emotion", stance="亢奋"),
                            fact_id=fid, path=db)

    def test_UNKNOWN事实挂无法判定_可以(self, db):
        fid = save_fact_bundle(self._unknown_fact(), path=db)
        aid = save_assessment(AgentAssessment(task_id=TID, agent="emotion", stance="无法判定"),
                              fact_id=fid, path=db)
        assert load_verdict(aid, path=db).stance == "无法判定"


# ───────────────────────────────────── P6 · 老路径回归


class TestLegacyAmendRetired:
    def test_旧合体行的修订路径已退役(self, db, capsys):
        """🔴 批 E-III：六个 skill 全迁完之后，操作合体 AgentVerdict 的旧修订路径退役。
        `save_verdict` 仍保留（LegacyAdapter 读路径宽的测试还靠它造老形状），所以历史
        合体行仍能造出来；但对它跑 amend 会**明确报错退役**，不是静默改库。"""
        vid = save_verdict(AgentVerdict(
            task_id=TID, agent="market", status="completed", verdict="PASS",
            result={"sh_close": 3911.0}, data_completeness=1.0,
            evidence=[_ev("sh_close", 3911.0)], elapsed_ms=50), path=db)
        rc = amend.main(["--ref", str(vid), "--add-missing",
                         "market.trend.no_history", "只有单日，无法判断趋势",
                         "--verdict", "WARNING", "--stance", "放量上涨"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "退役" in err and "只读" in err

    def test_amend给fact行加stance_走新路径不重打(self, db):
        fb = FactBundle(task_id=TID, agent="emotion", status="completed", verdict="PASS",
                        result={"limit_up_count": 78}, data_completeness=1.0,
                        evidence=[_ev("limit_up_count", 78)])
        rid = open_test_run(db, decision_id=TID)
        fid = save_fact_bundle(fb, run_id=rid, path=db)
        rc = amend.main(["--ref", str(fid), "--stance", "亢奋"])
        assert rc == 0
        aid = load_verdict_ids_for_run(rid, path=db)["emotion"]
        assert aid != fid  # 判断落在**新的一行**
        oc = load_outcome(aid, path=db)
        assert oc.stance == "亢奋"
        # 事实那一行没被重打：fact 行的 evidence 原样在
        assert oc.fact.evidence[0].field == "limit_up_count"

    def test_amend给fact行加missing被明确拒绝(self, db, capsys):
        fb = FactBundle(task_id=TID, agent="emotion", status="completed", verdict="PASS",
                        result={"limit_up_count": 78}, data_completeness=1.0,
                        evidence=[_ev("limit_up_count", 78)])
        fid = save_fact_bundle(fb, path=db)
        rc = amend.main(["--ref", str(fid), "--add-missing", "emotion.x.y", "限制",
                         "--verdict", "WARNING"])
        assert rc == 2
        # 明确指路，不是静默：告诉调用方 fact 行只收 --stance，缺口归 skill、caveat 归「需要注意」
        err = capsys.readouterr().err
        assert "--stance" in err and "需要注意" in err
