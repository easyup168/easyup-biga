"""合成与回放测试。

核心要证明的一件事：**在线路径与回放路径走的是同一份组装代码。**
如果它们各写一套，「换模型重跑看结论变没变」这个实验就失去意义 ——
观察到的差异里会混进代码差异。
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "decision-card" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _contract import AgentVerdict, DecisionCard, Evidence, new_task_id, now_cn  # noqa: E402
from _store import connect, init_schema, load_card  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


card_ops = _load("card_ops")
replay = _load("replay")

DID = new_task_id(1, day="20260919")


def verdict(agent="emotion", missing=None, **kw) -> AgentVerdict:
    t = now_cn()
    ev = Evidence(field="limit_up_count", source="em:push2ex/limit_up", value=78,
                  as_of=t - timedelta(hours=2), retrieved_at=t, label="涨停家数")
    missing = missing or []
    return AgentVerdict(
        task_id=DID, agent=agent,
        status="completed" if not missing else "partial",
        verdict=kw.pop("verdict", "PASS" if not missing else "WARNING"),
        result={"limit_up_count": 78}, confidence=0.8,
        evidence=[ev], missing=missing, elapsed_ms=1000, **kw)


def judgment(**kw) -> "card_ops.Judgment":
    base = {"status": "WAIT", "headline": "核心矛盾一句话", "synthesis": ""}
    base.update(kw)
    return card_ops.Judgment(**base)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


class TestSynthesize:
    def test_是纯函数_相同输入逐字段相同(self):
        kw = dict(decision_id=DID, verdicts=[verdict()], judgment=judgment(),
                  model_ref="m", generated_at="2026-09-19T16:00:00+08:00")
        assert card_ops.synthesize(**kw).to_dict() == card_ops.synthesize(**kw).to_dict()

    def test_聚合各Verdict的缺失项(self):
        c = card_ops.synthesize(
            decision_id=DID,
            verdicts=[verdict(missing=["最高板"]), verdict(agent="risk", missing=["位置风险"])],
            judgment=judgment(), model_ref="m")
        assert c.missing == ["最高板", "位置风险"]

    def test_合并Supervisor自己发现的缺失项(self):
        c = card_ops.synthesize(
            decision_id=DID, verdicts=[verdict(missing=["最高板"])],
            judgment=judgment(extra_missing=["risk agent 尚未上线"]), model_ref="m")
        assert c.missing == ["最高板", "risk agent 尚未上线"]

    def test_去重且保序(self):
        """顺序稳定，回放的 diff 才是干净的。"""
        c = card_ops.synthesize(
            decision_id=DID,
            verdicts=[verdict(missing=["A", "B"]), verdict(agent="risk", missing=["B", "C"])],
            judgment=judgment(extra_missing=["A", "D"]), model_ref="m")
        assert c.missing == ["A", "B", "C", "D"]

    def test_model_ref带组装版本(self):
        c = card_ops.synthesize(decision_id=DID, verdicts=[verdict()],
                                judgment=judgment(), model_ref="anthropic/x")
        assert card_ops.SYNTHESIS_VERSION in c.model_ref

    def test_契约仍然拦得住(self):
        """组装层不许绕过契约：缺失项非空还给 BUY，必须抛错。"""
        with pytest.raises(ValueError, match="不得给买入结论"):
            card_ops.synthesize(decision_id=DID, verdicts=[verdict(missing=["最高板"])],
                                judgment=judgment(status="BUY"), model_ref="m")

    def test_comparable剥掉每次必然不同的字段(self):
        # 复用同一个 verdict：证据时间戳**不该**被 comparable 剥掉 ——
        # 回放用的是冻结证据，它的时间戳本来就必须一模一样。
        # 这里要隔离的只有 generated_at / elapsed_ms。
        v = verdict()
        a = card_ops.synthesize(decision_id=DID, verdicts=[v],
                                judgment=judgment(), model_ref="m", elapsed_ms=100)
        b = card_ops.synthesize(decision_id=DID, verdicts=[v],
                                judgment=judgment(), model_ref="m", elapsed_ms=999)
        assert a.to_dict() != b.to_dict()
        assert card_ops.comparable(a) == card_ops.comparable(b)


class TestReplay:
    def _seed(self, db, *, extra_missing=()) -> DecisionCard:
        c = card_ops.synthesize(
            decision_id=DID, verdicts=[verdict()],
            judgment=judgment(extra_missing=list(extra_missing)),
            model_ref="anthropic/claude-sonnet-5", elapsed_ms=21400)
        card_ops.persist(c)
        return c

    def test_一致性检查通过(self, db, capsys):
        self._seed(db)
        assert replay.main([DID, "--check"]) == 0
        assert "一致" in capsys.readouterr().out

    def test_还原Supervisor的缺失项(self, db):
        """回归：不还原 extra_missing 时回放会悄悄少掉缺失项。"""
        extras = ["risk agent 尚未上线", "discipline agent 尚未上线"]
        self._seed(db, extra_missing=extras)
        assert replay.main([DID, "--check"]) == 0
        assert load_card(DID).missing == extras

    def test_检查能发现不一致(self, db, monkeypatch, capsys):
        """守卫的守卫：--check 必须真的会红，否则它是空转的。"""
        self._seed(db)
        real = card_ops.synthesize

        def drifted(**kw):
            kw["judgment"] = card_ops.Judgment(
                status="AVOID", headline=kw["judgment"].headline,
                synthesis=kw["judgment"].synthesis,
                extra_missing=kw["judgment"].extra_missing)
            return real(**kw)

        monkeypatch.setattr(replay, "synthesize", drifted)
        assert replay.main([DID, "--check"]) == 2
        assert "不一致" in capsys.readouterr().err

    def test_回放不覆盖原始记录(self, db):
        self._seed(db)
        assert replay.main([DID, "--status", "AVOID", "--headline", "更保守",
                            "--model-ref", "anthropic/claude-opus-5", "--store"]) == 0
        assert load_card(DID).status == "WAIT"          # 在线那条没变
        with connect(db, readonly=True) as c:
            rows = c.execute("SELECT record_id, replay_of, status "
                             "FROM decision_records ORDER BY record_id").fetchall()
        assert [r["status"] for r in rows] == ["WAIT", "AVOID"]
        assert rows[1]["replay_of"] == rows[0]["record_id"]

    def test_model_ref不层层累积版本后缀(self, db):
        """回放取回的 model_ref 已带 (synth/N)，不剥掉会越叠越长。"""
        self._seed(db)
        replay.main([DID, "--store"])
        with connect(db, readonly=True) as c:
            m = c.execute("SELECT model_ref FROM decision_records "
                          "WHERE replay_of IS NOT NULL").fetchone()["model_ref"]
        assert m.count(card_ops.SYNTHESIS_VERSION) == 1

    def test_不存在的decision_id(self, db, capsys):
        assert replay.main(["BIGA-20260101-999", "--check"]) == 1


class TestSharedCode:
    """🔴 硬性要求：回放与在线共用同一份合成代码。"""

    def test_两条路径都从card_ops导入synthesize(self):
        names = {}
        for f in ("synthesize", "replay"):
            tree = ast.parse((SCRIPTS / f"{f}.py").read_text(encoding="utf-8"))
            names[f] = {
                a.name for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module == "card_ops"
                for a in n.names
            }
        assert "synthesize" in names["synthesize"], "在线路径没有用共用合成函数"
        assert "synthesize" in names["replay"], "回放路径没有用共用合成函数"

    def test_两个脚本都不自己构造DecisionCard(self):
        """只有 card_ops 能直接 new 一张 Card；CLI 必须走它。"""
        for f in ("synthesize", "replay"):
            tree = ast.parse((SCRIPTS / f"{f}.py").read_text(encoding="utf-8"))
            direct = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id == "DecisionCard"]
            assert not direct, f"{f}.py 绕过 card_ops 直接构造了 DecisionCard"
