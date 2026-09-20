"""stance / raw_hash / 机器可读 missing —— Phase 2 补的三个可追溯性空白。

这三件事都来自同一个问题：**结论产生了，但没法被追问。**

- `stance`：方向判断只存在于自然语言回复里 ⇒ Phase 4 想把它与 T+5 结果
  做相关时，历史数据里根本没有这一列
- `raw_hash`：Evidence 说得出 `source` / `as_of`，但说不出**具体哪一份响应** ——
  只能靠时间戳猜
- `missing` 代码：散文缺失项只能数次数，说不出「是哪一类缺失」
"""

from __future__ import annotations

import dataclasses
import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    CN_TZ,
    LEGACY_CODE,
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    MissingItem,
)


def _ev(field="sh_close", source="sina:kline/sh000001", raw_hash="abc123"):
    return Evidence(field=field, source=source, value=1.0,
                    as_of=datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ),
                    retrieved_at=datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ),
                    calc_version="v1", raw_hash=raw_hash)


def _v(agent="market", **kw):
    # 拼的是构造 AgentVerdict 的 kwargs，下一步就交给真正的 dataclass。
    # contract-exempt: 不是第二套契约，是同一个契约的入参
    base = dict(task_id="BIGA-20260918-001", agent=agent, status="completed",
                verdict="PASS", result={"sh_close": 1.0}, confidence=1.0,
                evidence=[_ev()], warnings=[], missing=[], elapsed_ms=1)
    base.update(kw)
    return AgentVerdict(**base)


class TestStance:
    def test_词表外的词被拒(self):
        with pytest.raises(ValueError, match="不在该 agent 的词表里"):
            _v(stance="偏强震荡")

    def test_词表内的词可用(self):
        assert _v(stance="放量上涨").stance == "放量上涨"

    def test_数据不够就不许有方向(self):
        """🔴 UNKNOWN 的意思是「不知道」，不是「有判断但数据一般」。"""
        with pytest.raises(ValueError, match="方向是从哪来的"):
            _v(verdict="UNKNOWN", stance="放量上涨",
               missing=[MissingItem("x", "market.turnover.unavailable")],
               status="partial")

    def test_数据不够时可以显式写无法判定(self):
        v = _v(verdict="UNKNOWN", stance="无法判定", status="partial",
               missing=[MissingItem("x", "market.turnover.unavailable")])
        assert v.stance == "无法判定"

    def test_往返不丢(self):
        v = _v(stance="分化")
        assert AgentVerdict.from_dict(v.to_dict()).stance == "分化"

    def test_不给stance也合法(self):
        """skill 不做判断 —— 它产出的原件本来就没有 stance。"""
        assert _v().stance is None


class TestStanceVocabMatchesContracts:
    """🔴 词表在契约层，判断表在 AGENTS.md —— 两边必须一致。

    改了一边忘了另一边，agent 会给出一个契约层拒绝的词，
    然后花几轮去猜为什么被拒。
    """

    @pytest.mark.parametrize("agent", sorted(STANCE_VOCAB))
    def test_每个词都出现在该agent的契约里(self, agent):
        text = (REPO / "agents" / agent / "AGENTS.md").read_text(encoding="utf-8")
        missing = [w for w in STANCE_VOCAB[agent] if w not in text]
        assert not missing, \
            f"{agent}/AGENTS.md 里没有这些 stance：{missing} —— agent 不会知道能填什么"

    @pytest.mark.parametrize("agent", sorted(STANCE_VOCAB))
    def test_契约里的选项行与词表逐项一致(self, agent):
        """输出格式那一行列出的选项，必须正好是词表。"""
        text = (REPO / "agents" / agent / "AGENTS.md").read_text(encoding="utf-8")
        line = next((ln for ln in text.splitlines()
                     if ln.startswith("状态：") or ln.startswith("阶段：")), None)
        assert line, f"{agent}/AGENTS.md 找不到「状态：」/「阶段：」那一行"
        listed = {w.strip() for w in line.split("：", 1)[1].split("/")}
        assert listed == set(STANCE_VOCAB[agent]), \
            f"{agent} 契约列的是 {sorted(listed)}，词表是 {sorted(STANCE_VOCAB[agent])}"


class TestRawHash:
    def test_往返不丢(self):
        e = _ev(raw_hash="deadbeef")
        assert Evidence.from_dict(e.to_dict()).raw_hash == "deadbeef"

    def test_派生字段允许为空(self):
        assert _ev(source="derived:x", raw_hash=None).raw_hash is None

    def test_与raw层用同一个哈希函数(self):
        """两边各算各的，某天序列化参数改了一处就再也对不上 —— 而那是静默的。"""
        from _store import payload_sha256
        import hashlib, json
        payload = {"b": 2, "a": [1, {"c": None}]}
        assert payload_sha256(payload) == hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()


class TestSkillsEmitTraceableEvidence:
    """单一来源的证据必须带 `raw_hash`，且缺失项不许留 legacy 代码。"""

    @staticmethod
    def _load(skill, mod):
        d = REPO / "skills" / skill / "scripts"
        sys.path.insert(0, str(d))
        spec = importlib.util.spec_from_file_location(mod, d / f"{mod}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[mod] = m
        spec.loader.exec_module(m)
        return m

    def test_market单源证据都有raw_hash(self, monkeypatch):
        mc = self._load("market-calc", "market_calc")
        import test_market_calc as tmc  # 复用那边的桩
        for name, fn in (("fetch_index_daily", lambda symbol, **k: tmc.bars(
                              symbol, 25, 3911.871, 3875.6, 48571250700)),
                         ("fetch_index_quote", lambda codes: {
                              c: tmc.quote(c, 99416945.0, 485712507) for c in codes}),
                         ("fetch_breadth", lambda: __import__("_sources").BreadthResult(
                              4277, 1173, 180, [], {"rc": 0}))):
            monkeypatch.setattr(mc, name, fn)
        monkeypatch.setattr(mc, "now_cn",
                            lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
        monkeypatch.setattr(mc, "save_raw_snapshot", lambda **kw: 1)

        v = mc.build_verdict(date=None, break_source=set(), store=True,
                             task_id="BIGA-20260918-001")
        bad = [e.field for e in v.evidence
               if "/" in e.source and not e.source.startswith("derived:")
               and not e.raw_hash]
        assert not bad, f"这些单源证据没有 raw_hash，追不回原始响应：{bad}"

    @pytest.mark.parametrize("skill,mod,flag", [
        ("market-calc", "market_calc", "sina_sh"),
        ("emotion-calc", "emotion_calc", "limit_up"),
    ])
    def test_缺失项不许是legacy(self, skill, mod, flag):
        """`legacy.unclassified` 是给历史数据的收编口，新代码不许产出它。"""
        src = (REPO / "skills" / skill / "scripts" / f"{mod}.py").read_text(encoding="utf-8")
        assert LEGACY_CODE not in src, f"{mod}.py 里出现了 {LEGACY_CODE}"
        assert "MissingItem(" in src, f"{mod}.py 的缺失项没有带代码"


class TestMissingCodes:
    def test_裸字符串按遗留收编(self):
        v = _v(verdict="WARNING", status="partial", missing=["旧的散文"])
        assert v.missing[0].code == LEGACY_CODE and v.missing[0].is_legacy

    def test_旧卡仍能回放(self):
        """Phase 1/2 早期落库的卡里 missing 是裸字符串 —— 必须还读得进来。"""
        v = _v(verdict="WARNING", status="partial", missing=["老缺失"])
        card = DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v], missing=["老缺失"],
                            synthesis="", model_ref="m")
        again = DecisionCard.from_dict(card.to_dict())
        assert again.missing[0].code == LEGACY_CODE
        assert str(again.missing[0]) == "老缺失"

    def test_代码在卡的往返中保留(self):
        m = MissingItem("成交额取不到", "market.turnover.unavailable")
        v = _v(verdict="WARNING", status="partial", missing=[m])
        card = DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v], missing=[m],
                            synthesis="", model_ref="m")
        assert DecisionCard.from_dict(card.to_dict()).missing[0].code == \
            "market.turnover.unavailable"

    def test_同文本不同代码不合并(self):
        """两个源各自不可用却报了同一句话，那是两件事，合并会让统计少一条。"""
        sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))
        spec = importlib.util.spec_from_file_location(
            "card_ops", REPO / "skills/decision-card/scripts/card_ops.py")
        co = importlib.util.module_from_spec(spec)
        sys.modules["card_ops"] = co
        spec.loader.exec_module(co)

        a = _v("market", verdict="WARNING", status="partial",
               missing=[MissingItem("数据源不可用", "market.turnover.unavailable")])
        b = _v("emotion", verdict="WARNING", status="partial",
               missing=[MissingItem("数据源不可用", "emotion.pool.unavailable")],
               evidence=[_ev(field="limit_up_count")],
               result={"limit_up_count": 1.0})
        card = co.synthesize(decision_id="BIGA-20260918-001", verdicts=[a, b],
                             judgment=co.Judgment(status="WAIT", headline="h"),
                             model_ref="m")
        assert len(card.missing) == 2, "代码不同就是不同的缺失，不能按文本去重"

    def test_卡面同时显示人话与代码(self):
        m = MissingItem("成交额取不到", "market.turnover.unavailable")
        v = _v(verdict="WARNING", status="partial", missing=[m])
        card = DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v], missing=[m],
                            synthesis="", model_ref="m")
        text = card.render()
        assert "成交额取不到" in text and "market.turnover.unavailable" in text
