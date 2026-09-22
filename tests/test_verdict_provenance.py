"""VerdictRef —— Card 证明「这次决策用的是哪一条判定原件」（设计文档 §6 A6）。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`VerdictRef` 值对象本身的校验、`DecisionCard.input_verdict_refs`
  的往返与「新卡类型必须对」、`_store.verify_verdict_refs()` 的核对逻辑
  （含探针：改掉库里某条原件的 sha，断言核对报红）
- **不覆盖**：`verdict_ref=NN`（stderr 打的那个裸整数 id）的 CLI 约定，
  那是 `tests/test_verdict_refs.py` 的地盘——两者名字很像但不是一回事，
  见 `_contract/verdict_ref.py` 模块 docstring 的那条警告
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills"))

from _contract import (  # noqa: E402
    CN_TZ,
    CONTRACT_VERSION,
    AgentVerdict,
    DecisionCard,
    Evidence,
    VerdictRef,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    init_schema,
    load_verdict_meta,
    save_verdict,
    verify_verdict_refs,
)

TID = "BIGA-20260922-001"
_SHA = "a" * 64


def _ev(field="sh_close"):
    t = now_cn()
    return Evidence(field=field, source="sina:kline/sh000001", value=1.0,
                    as_of=t, retrieved_at=t)


def _verdict(agent="market") -> AgentVerdict:
    return AgentVerdict(task_id=TID, agent=agent, status="completed", verdict="PASS",
                        result={"sh_close": 1.0}, data_completeness=1.0, evidence=[_ev()],
                        stance="放量上涨" if agent == "market" else "无法判定")


def _card(refs: list[VerdictRef] | None = None, **kw) -> DecisionCard:
    # 🔴 本文件测的是 VerdictRef 机制，不是 roster——默认给 5 条占位
    #    missing（F-8 之后 roster 判据按计数比较：missing 条数须不少于
    #    缺席 agent 数），这里只有 1 个 agent，STANCE_VOCAB 另外 5 个天然缺席。
    # contract-exempt: 拼的是构造 DecisionCard 的 kwargs，不是第二套契约
    base = dict(decision_id=TID, status="WAIT", headline="h",
                verdicts=[_verdict()], synthesis="", model_ref="m",
                missing=[f"占位缺失项{i}——本文件不测 roster" for i in range(5)],
                input_verdict_refs=refs or [])
    base.update(kw)
    return DecisionCard(**base)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


class TestVerdictRefConstruction:
    def test_合法构造(self):
        r = VerdictRef(agent="market", verdict_id=1, content_sha256=_SHA,
                       contract_version=CONTRACT_VERSION)
        assert r.agent == "market" and r.verdict_id == 1

    def test_空agent被拒(self):
        with pytest.raises(ValueError, match="agent"):
            VerdictRef(agent="", verdict_id=1, content_sha256=_SHA,
                      contract_version=CONTRACT_VERSION)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_非正verdict_id被拒(self, bad):
        with pytest.raises(ValueError, match="verdict_id"):
            VerdictRef(agent="market", verdict_id=bad, content_sha256=_SHA,
                      contract_version=CONTRACT_VERSION)

    @pytest.mark.parametrize("bad", ["short", "z" * 64, ""])
    def test_content_sha256必须是64位十六进制(self, bad):
        with pytest.raises(ValueError, match="content_sha256"):
            VerdictRef(agent="market", verdict_id=1, content_sha256=bad,
                      contract_version=CONTRACT_VERSION)

    def test_frozen(self):
        r = VerdictRef(agent="market", verdict_id=1, content_sha256=_SHA,
                       contract_version=CONTRACT_VERSION)
        with pytest.raises(Exception):
            r.agent = "sector"  # type: ignore[misc]

    def test_序列化往返(self):
        r = VerdictRef(agent="market", verdict_id=1, content_sha256=_SHA,
                       contract_version=CONTRACT_VERSION)
        assert VerdictRef.from_dict(r.to_dict()) == r


class TestCardCarriesRefs:
    def test_新卡类型不对被拒(self):
        """铁律 4：不许自建第二套 VerdictRef 结构。"""
        # contract-exempt: 鸭子类型，用于断言契约拒绝它
        class FakeRef:
            agent = "market"

        with pytest.raises(TypeError, match="铁律 4"):
            _card(refs=[FakeRef()])

    def test_历史卡没有这个字段也能读(self):
        """旧卡（A6 之前落库的）没有 input_verdict_refs——from_dict 缺省成空。"""
        d = _card().to_dict()
        del d["input_verdict_refs"]
        c = DecisionCard.from_dict(d)
        assert c.input_verdict_refs == ()

    def test_往返不丢(self):
        ref = VerdictRef(agent="market", verdict_id=7, content_sha256=_SHA,
                         contract_version=CONTRACT_VERSION)
        c = _card(refs=[ref])
        again = DecisionCard.from_dict(c.to_dict())
        assert again.input_verdict_refs == (ref,)

    def test_不能原地追加(self):
        with pytest.raises(AttributeError):
            _card().input_verdict_refs.append(
                VerdictRef(agent="market", verdict_id=1, content_sha256=_SHA,
                          contract_version=CONTRACT_VERSION))


class TestVerifyVerdictRefs:
    """探针：改掉库里某条原件的 sha，断言核对报红。"""

    def test_没有ref可核时视为一致(self, db):
        assert verify_verdict_refs(_card()) == []

    def test_哈希对得上时一致(self, db):
        vid = save_verdict(_verdict(), path=db)
        real_sha = load_verdict_meta(vid, path=db)["content_sha256"]
        ref = VerdictRef(agent="market", verdict_id=vid, content_sha256=real_sha,
                         contract_version=CONTRACT_VERSION)
        assert verify_verdict_refs(_card(refs=[ref]), path=db) == []

    def test_哈希对不上时报红(self, db):
        """🔴 前置断言：先确认「哈希对得上」那条真的会用到 real_sha，
        不然这条探针可能从头到尾没触发它声称要触发的检查（本仓库吃过这个亏，
        见 A-II 分发提示词里 A-I 评审自己栽的那次标点归一化误判）。"""
        vid = save_verdict(_verdict(), path=db)
        real_sha = load_verdict_meta(vid, path=db)["content_sha256"]
        tampered = ("0" if real_sha[0] != "0" else "1") + real_sha[1:]
        assert tampered != real_sha, "前置断言：篡改后的哈希必须真的不同"

        ref = VerdictRef(agent="market", verdict_id=vid, content_sha256=tampered,
                         contract_version=CONTRACT_VERSION)
        problems = verify_verdict_refs(_card(refs=[ref]), path=db)
        assert len(problems) == 1
        assert "market" in problems[0] and str(vid) in problems[0]

    def test_verdict_id不存在时报红(self, db):
        ref = VerdictRef(agent="market", verdict_id=999999, content_sha256=_SHA,
                         contract_version=CONTRACT_VERSION)
        problems = verify_verdict_refs(_card(refs=[ref]), path=db)
        assert len(problems) == 1 and "找不到" in problems[0]

    def test_agent对不上时报红(self, db):
        """C3-3：`verdict_id` 是**跨 agent 的全局自增**，不按 agent 分号段。
        一条 ref 声称是 market 的原件、`verdict_id`/`content_sha256` 却全指向
        news 那一行时，「能找到 + hash 对」两条都成立 —— 只有核对
        `ref.agent == 存量.agent` 才能拦下这种张冠李戴（设计文档 §2 追加 5 §8-16）。
        """
        vid_market = save_verdict(_verdict(agent="market"), path=db)
        vid_news = save_verdict(_verdict(agent="news"), path=db)
        news_sha = load_verdict_meta(vid_news, path=db)["content_sha256"]

        # 前置断言：这两条确实是不同行、不同 agent（否则伪造无从谈起）。
        assert vid_market != vid_news
        assert load_verdict_meta(vid_market, path=db)["agent"] == "market"
        assert load_verdict_meta(vid_news, path=db)["agent"] == "news"

        # 🔴 前置断言：一条「agent 也说 news」的 ref（id/sha 全指向同一行）核对通过 ——
        #    证明 forged 唯一的破绽就是 agent，只有 agent 核对能抓到它，
        #    存在性与 hash 两道检查都不会触发（A-II 探针纪律：先确认命中的是目标条件）。
        consistent = VerdictRef(agent="news", verdict_id=vid_news,
                                content_sha256=news_sha,
                                contract_version=CONTRACT_VERSION)
        assert verify_verdict_refs(_card(refs=[consistent]), path=db) == []

        # 伪造：声称 market，实际 verdict_id + sha 全对得上 news 那一行。
        forged = VerdictRef(agent="market", verdict_id=vid_news,
                            content_sha256=news_sha,
                            contract_version=CONTRACT_VERSION)
        problems = verify_verdict_refs(_card(refs=[forged]), path=db)
        assert len(problems) == 1
        assert "market" in problems[0] and "news" in problems[0]


class TestSynthesizeBuildsRefs:
    """`synthesize.py --verdict-ids` 是唯一能造出 VerdictRef 的路径。"""

    def test_按id合成时card带上真实的ref(self, db):
        import subprocess
        import sys as _sys
        vid = save_verdict(_verdict(), path=db)
        script = REPO / "skills/decision-card/scripts/synthesize.py"
        env = {**__import__("os").environ, "BIGA_DB_PATH": str(db)}
        # 🔴 F-8：只提交 1 个 agent（market），另外 5 个天然缺席——
        #    roster 判据按计数比较，5 条 --extra-missing 才够。
        extra_missing_args = []
        for i in range(5):
            extra_missing_args += ["--extra-missing", "supervisor.agent_offline",
                                   f"占位{i}——本文件不测 roster"]
        r = subprocess.run(
            [_sys.executable, str(script), "--verdict-ids", str(vid),
             "--status", "WAIT", "--headline", "h", "--model-ref", "m",
             *extra_missing_args,
             "--no-store", "--json"],
            capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        import json
        card = json.loads(r.stdout)
        refs = card["input_verdict_refs"]
        assert len(refs) == 1
        assert refs[0]["verdict_id"] == vid and refs[0]["agent"] == "market"
        assert refs[0]["content_sha256"] == load_verdict_meta(vid, path=db)["content_sha256"]
