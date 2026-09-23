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
import json
import pathlib
import sys
from datetime import datetime

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))
sys.path.insert(0, str(REPO / "tests"))

from _contract import (  # noqa: E402
    CN_TZ,
    LEGACY_CODE,
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    MissingItem,
)
from _consistency import assert_matches_source, built_specialists  # noqa: E402


def _ev(field="sh_close", source="sina:kline/sh000001", raw_hash="abc123"):
    return Evidence(field=field, source=source, value=1.0,
                    as_of=datetime(2026, 9, 18, 15, 0, tzinfo=CN_TZ),
                    retrieved_at=datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ),
                    calc_version="v1", raw_hash=raw_hash)


def _v(agent="market", **kw):
    # 拼的是构造 AgentVerdict 的 kwargs，下一步就交给真正的 dataclass。
    # contract-exempt: 不是第二套契约，是同一个契约的入参
    base = dict(task_id="BIGA-20260918-001", agent=agent, status="completed",
                verdict="PASS", result={"sh_close": 1.0}, data_completeness=1.0,
                evidence=[_ev()], warnings=[], missing=[], elapsed_ms=1)
    base.update(kw)
    return AgentVerdict(**base)


def _fill_roster(*present: str) -> list:
    """给定的 agent 已经出场，凑出 STANCE_VOCAB 里其余 agent 的空白判定。

    F-8 之后 roster 判据按计数比较——这个文件大多数测试只关心 1、2 个
    agent 的 MissingItem 行为，不关心 roster 完不完整，补满省得每条都要
    单独算「缺席几个、该给几条占位 missing」。
    """
    return [_v(agent=a) for a in sorted(STANCE_VOCAB) if a not in present]


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

    def test_STANCE_VOCAB覆盖全部已建好的agent(self):
        """🔴 外部评审 F8 的结构性半部分。

        `verdict.py` 的构造函数已经修好了运行时行为——`STANCE_VOCAB.get(agent)`
        命不中就直接拒绝，不再静默退化成「1~16 字任意词都收」。但复查指出：
        那只堵住了"用到才崩"，没有一条测试在**忘记登记**这件事发生的那一刻
        （建 agent 的那次 commit）就报红——要等到真正调用才会崩，CI 阶段
        发现不了。

        下面这一条以下两个测试（`sorted(STANCE_VOCAB)` 做 parametrize 源）
        天生只能测"已经登记"的 agent，结构上不可能覆盖"忘记登记"这个分支——
        这条单独存在，权威源换成 `built_agents()`（与 STANCE_VOCAB 无关的
        独立事实：`agents/` 目录下有没有这个 agent）。
        """
        assert_matches_source(
            set(STANCE_VOCAB), built_specialists(),
            what="STANCE_VOCAB 的 key 集合 vs 已建好的 Specialist",
            fix_hint="新建一个 specialist 时，在 `_contract/verdict.py` 的 "
                      "STANCE_VOCAB 里登记它的词表；若是 support 类（不产 stance，"
                      "如 synthesizer），登记进 `_consistency.SUPPORT_AGENTS`")

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
                         ("fetch_index_quote", lambda codes: (
                              {c: tmc.quote(c, 99416945.0, 485712507) for c in codes},
                              "v_sh000001=\"...\";")),
                         ("fetch_breadth", lambda: __import__("_sources").BreadthResult(
                              4277, 1173, 180, [], {"rc": 0},
                              raw_text=json.dumps({"rc": 0})))):
            monkeypatch.setattr(mc, name, fn)
        monkeypatch.setattr(mc, "now_cn",
                            lambda: datetime(2026, 9, 18, 18, 0, tzinfo=CN_TZ))
        monkeypatch.setattr(mc, "save_raw_snapshot", lambda **kw: 1)

        v = mc.build_fact_bundle(date=None, break_source=set(), store=True,
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
                            headline="h", verdicts=[v, *_fill_roster("market")],
                            missing=["老缺失"], synthesis="", model_ref="m")
        again = DecisionCard.from_dict(card.to_dict())
        assert again.missing[0].code == LEGACY_CODE
        assert str(again.missing[0]) == "老缺失"

    def test_代码在卡的往返中保留(self):
        m = MissingItem("成交额取不到", "market.turnover.unavailable")
        v = _v(verdict="WARNING", status="partial", missing=[m])
        card = DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v, *_fill_roster("market")],
                            missing=[m], synthesis="", model_ref="m")
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
        card = co.synthesize(decision_id="BIGA-20260918-001",
                             verdicts=[a, b, *_fill_roster("market", "emotion")],
                             judgment=co.Judgment(status="WAIT", headline="h"),
                             model_ref="m")
        assert len(card.missing) == 2, "代码不同就是不同的缺失，不能按文本去重"

    def test_卡面同时显示人话与代码(self):
        m = MissingItem("成交额取不到", "market.turnover.unavailable")
        v = _v(verdict="WARNING", status="partial", missing=[m])
        card = DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v, *_fill_roster("market")],
                            missing=[m], synthesis="", model_ref="m")
        text = card.render()
        assert "成交额取不到" in text and "market.turnover.unavailable" in text


class TestRestatedMissing:
    """同一个生产方不许把同一句话说两遍 —— 裁定 15 + L-10。

    实测来源：`BIGA-20260921-021`，**飞书触发的第一张卡**。

        risk.upstream.coverage_incomplete  Stage 1 缺席：news，这些领域的风险本次没有被看过
        risk.coverage.insufficient         Stage 1 缺席:news,这些领域的风险本次没有被看过

    🔴 注意标点：全角 `：，` vs 半角 `:,`。**后者是 LLM 重打出来的** ——
    这正是 L-10「不让 LLM 搬运结构化数据」要防的形态。

    ⚠️ 判据是 **(命名空间, 归一化正文)**，不是光看正文。
    第一版只看正文，当场把 `test_同文本不同代码不合并` 判红了 ——
    那条测试钉的是一个**合法反例**：两个不同的源各自「数据源不可用」
    是两件事，合并会让统计少一条。
    """

    @staticmethod
    def _card(items, **kw):
        v = _v("risk", verdict="UNKNOWN", status="partial", missing=items)
        return DecisionCard(decision_id="BIGA-20260918-001", status="WAIT",
                            headline="h", verdicts=[v], missing=list(items),
                            synthesis="", model_ref="m", **kw)

    A = ("Stage 1 缺席：news，这些领域的风险本次没有被看过",
         "risk.upstream.coverage_incomplete")
    B = ("Stage 1 缺席:news,这些领域的风险本次没有被看过",
         "risk.coverage.insufficient")

    def test_同一命名空间同一句话被拒(self):
        with pytest.raises(ValueError, match="同一件事报了两遍"):
            self._card([MissingItem(*self.A), MissingItem(*self.B)])

    def test_报错要说清是哪两条(self):
        """🔴 报错要指路 —— 「有重复」没用，要说出两个代码。"""
        try:
            self._card([MissingItem(*self.A), MissingItem(*self.B)])
        except ValueError as e:
            assert self.A[1] in str(e) and self.B[1] in str(e)

    def test_不同agent同一句话是合法的(self):
        """两个源各自不可用却报了同一句话 —— 那是两件事。"""
        a = _v("market", verdict="WARNING", status="partial",
               missing=[MissingItem("数据源不可用", "market.turnover.unavailable")])
        b = _v("emotion", verdict="WARNING", status="partial",
               missing=[MissingItem("数据源不可用", "emotion.pool.unavailable")],
               evidence=[_ev(field="limit_up_count")], result={"limit_up_count": 1.0})
        DecisionCard(decision_id="BIGA-20260918-001", status="WAIT", headline="h",
                     verdicts=[a, b, *_fill_roster("market", "emotion")],
                     synthesis="", model_ref="m",
                     missing=[*a.missing, *b.missing])

    def test_旧卡只警告不拒(self):
        """「新卡严格，旧卡可读」—— 库里已经有这样的卡，回放不该崩。"""
        items = [MissingItem(*self.A), MissingItem(*self.B)]
        v = _v("risk", verdict="UNKNOWN", status="partial", missing=items)
        # 目的正是绕过 __init__ 走反序列化路径，验「旧卡可读」
        # contract-exempt: from_dict 的入参，不是第二套契约
        raw = DecisionCard.from_dict({
            "decision_id": "BIGA-20260918-001", "status": "WAIT", "headline": "h",
            "verdicts": [v.to_dict()], "missing": [m.to_dict() for m in items],
            "synthesis": "", "model_ref": "m", "elapsed_ms": 0})
        assert raw.restate_warning, "旧卡应当带出提示，而不是静默"
        assert "同一件事报了两遍" in raw.render()

    def test_契约不再指示risk自己加这条(self):
        """判据落在**契约文本**上 —— 根因在那里，不在代码里。"""
        import re
        text = (REPO / "agents" / "risk" / "AGENTS.md").read_text(encoding="utf-8")
        cmds = re.findall(r"--add-missing\s+(\S+)", text)
        assert not cmds, (
            f"risk 契约仍在指示 agent 追加缺失项 {cmds} —— \n"
            "  skill 已经报了覆盖不足，再加一条就是重述。")
