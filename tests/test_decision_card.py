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

from _contract import (  # noqa: E402
    STANCE_VOCAB,
    AgentVerdict,
    DecisionCard,
    Evidence,
    MissingItem,
    new_task_id,
    now_cn,
)
from _store import connect, init_schema, list_agent_runs, load_online_card  # noqa: E402



from _provenance import open_test_run, provenance_for  # noqa: E402
from _roster import absent_registrations  # noqa: E402

#: 批 O：卡构造器拿不到 fixture，而在线卡的血缘必须指向**真落库**的行
#: （见 tests/_provenance.py）。`db` fixture 把库路径放这儿。
_DB: list = []



def _load(name: str):
    # 🔴 幂等：已经有别的测试文件 `_load` 过就**复用那个实例**，绝不用新实例覆写
    #    sys.modules。否则 orchestrator.py 在它自己 import 时绑定的 card_ops 与本文件
    #    覆写后的不是同一个对象 —— test_orchestrator 的 monkeypatch.setattr(card_ops,
    #    "persist", …) 打在新实例上、orchestrator 却调旧实例，patch 静默落空（全量里
    #    才复现的跨文件污染，见 test_run_id_capture.py / 教程第 32 章）。
    if name in sys.modules:
        return sys.modules[name]
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
        result={"limit_up_count": 78}, data_completeness=0.8,
        evidence=[ev], missing=missing, elapsed_ms=1000, **kw)


def full_roster() -> list[AgentVerdict]:
    """六个已建成 agent 各出一条最小合法判定——供不关心 roster 完整性的
    测试使用，避免触发 F-6 之后「缺席且无 missing 解释即拒」的判据。"""
    return [verdict(agent=a) for a in sorted(STANCE_VOCAB)]


def judgment(**kw) -> "card_ops.Judgment":
    base = {"status": "WAIT", "headline": "核心矛盾一句话", "synthesis": ""}
    base.update(kw)
    return card_ops.Judgment(**base)


def _synth(**kw):
    """批 N：在线卡必须带 run / 切片血缘（`save_card` 的在线分支要求）。

    本文件的卡多数最终会 `persist()`，统一在这里补上两个常量，省得每个调用点
    各写一遍（写漏一个就是一条与被测内容无关的红）。纯函数性质不受影响 ——
    两次调用补的是同一对常量，`synthesize()` 的输入仍然只由入参决定。
    """
    if _DB and "verdict_refs" not in kw:
        rid, esid, refs = provenance_for(
            _DB[0], kw["decision_id"], [v.agent for v in kw["verdicts"]])
        kw.setdefault("run_id", rid)
        kw.setdefault("evidence_set_id", esid)
        kw["verdict_refs"] = refs
    return card_ops.synthesize(**kw)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    open_test_run(p)          # 批 N：见 tests/_provenance.py
    _DB[:] = [p]
    return p


class TestSynthesize:
    def test_是纯函数_相同输入逐字段相同(self):
        kw = dict(decision_id=DID, verdicts=full_roster(), judgment=judgment(),
                  model_ref="m", generated_at="2026-09-19T16:00:00+08:00")
        assert _synth(**kw).to_dict() == _synth(**kw).to_dict()

    def test_聚合各Verdict的缺失项(self):
        # 🔴 F-8：roster 判据按计数比较，只造 2 个 agent 会被判「缺席 4 个、
        #    只解释了 2 条」而拒绝——这条测的是缺失项聚合，不是 roster，
        #    补满另外 4 个 agent（无 missing）让 roster 判据不介入。
        c = _synth(
            decision_id=DID,
            verdicts=[verdict(missing=["最高板"]), verdict(agent="risk", missing=["位置风险"]),
                     verdict(agent="market"), verdict(agent="sector"),
                     verdict(agent="technical"), verdict(agent="news")],
            judgment=judgment(), model_ref="m")
        # 🔴 A1：MissingItem 的 == 只看 code，比较人话内容要显式取 .detail
        assert [m.detail for m in c.missing] == ["最高板", "位置风险"]

    def test_合并Supervisor自己发现的缺失项(self):
        # 🔴 F-8：同上，补满 roster 免得计数判据抢在聚合逻辑前面报错。
        c = _synth(
            decision_id=DID, verdicts=[verdict(missing=["最高板"]),
                                       verdict(agent="risk"), verdict(agent="sector"),
                                       verdict(agent="technical"), verdict(agent="news"),
                                       verdict(agent="market")],
            judgment=judgment(extra_missing=["risk agent 尚未上线"]), model_ref="m")
        assert [m.detail for m in c.missing] == ["最高板", "risk agent 尚未上线"]

    def test_去重且保序(self):
        """顺序稳定，回放的 diff 才是干净的。"""
        c = _synth(
            decision_id=DID,
            verdicts=[verdict(missing=["A", "B"]), verdict(agent="risk", missing=["B", "C"])],
            judgment=judgment(extra_missing=["A", "D"] + absent_registrations(
                ["emotion", "risk"])), model_ref="m")
        # 批 P：缺席登记跟在后面（它们也是 extra_missing）；去重保序只看前四条。
        assert [m.detail for m in c.missing][:4] == ["A", "B", "C", "D"]

    def test_model_ref带组装版本(self):
        c = _synth(decision_id=DID, verdicts=full_roster(),
                                judgment=judgment(), model_ref="anthropic/x")
        assert card_ops.SYNTHESIS_VERSION in c.model_ref

    def test_契约仍然拦得住(self):
        """组装层不许绕过契约：缺失项非空还给 BUY，必须抛错。

        🔴 F-8：满 roster，让 `_check_roster()` 通过、真正测到的是它后面
        那条铁律 2 检查——否则 roster 判据会先报错，盖住这条测试要验的事。
        """
        with pytest.raises(ValueError, match="不得给买入结论"):
            _synth(
                decision_id=DID,
                verdicts=[verdict(missing=["最高板"]), verdict(agent="risk"),
                         verdict(agent="sector"), verdict(agent="technical"),
                         verdict(agent="news"), verdict(agent="market")],
                judgment=judgment(status="BUY"), model_ref="m")

    def test_comparable剥掉每次必然不同的字段(self):
        # 复用同一个 verdict：证据时间戳**不该**被 comparable 剥掉 ——
        # 回放用的是冻结证据，它的时间戳本来就必须一模一样。
        # 这里要隔离的只有 generated_at / elapsed_ms。
        verdicts = full_roster()
        a = _synth(decision_id=DID, verdicts=verdicts,
                                judgment=judgment(), model_ref="m", elapsed_ms=100)
        b = _synth(decision_id=DID, verdicts=verdicts,
                                judgment=judgment(), model_ref="m", elapsed_ms=999)
        assert a.to_dict() != b.to_dict()
        assert card_ops.comparable(a) == card_ops.comparable(b)


class TestReplay:
    def _seed(self, db, *, extra_missing=()) -> DecisionCard:
        c = _synth(
            decision_id=DID, verdicts=full_roster(),
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
        assert [m.detail for m in load_online_card(DID).missing] == extras

    def test_同文本异代码的extra_missing不会被反推丢掉(self, db):
        """A1 的回归：追加 2 描述的那个实测场景。

        `market.turnover.unavailable` 与 `supervisor.agent_no_response`
        说的是同一句话「数据源不可用」——文本相同，但那是两件事
        （一个源自己不可用 vs 这个 agent 没回应）。旧的 str 身份语义下，
        `replay.py` 的 `m not in from_verdicts` 会把它们判成同一条，
        反推出的 `extra_missing` 会**少一条**——「回放悄悄让卡变好看」。

        A1 把 MissingItem 的身份改成只看 code 之后，这里必须能完整找回。
        """
        text = "数据源不可用"
        v = verdict(missing=[MissingItem(text, "market.turnover.unavailable")])
        extra = MissingItem(text, "supervisor.agent_no_response")
        # 🔴 F-8：满 roster（其余 5 个 agent 无 missing）——这条测的是
        # MissingItem 身份，不是 roster 计数，补满让 roster 判据不介入。
        others = [verdict(agent=a) for a in ("risk", "sector", "technical", "news", "market")]
        c = _synth(
            decision_id=DID, verdicts=[v, *others],
            judgment=judgment(extra_missing=[extra]),
            model_ref="anthropic/claude-sonnet-5", elapsed_ms=100)
        assert len(c.missing) == 2, "两条不同代码的缺失项不该被合成阶段去重"
        card_ops.persist(c)

        assert replay.main([DID, "--check"]) == 0
        again = load_online_card(DID)
        codes = sorted(m.code for m in again.missing)
        assert codes == ["market.turnover.unavailable", "supervisor.agent_no_response"], (
            f"回放反推 extra_missing 时把同文本异代码的两条合并成了一条：{codes}")

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
        """回放追加新行、不改原始行 —— 且**换模型给出新结论**是合法的。

        🔴 这正是本模块 docstring 里的用法 2（「换个模型重跑，看结论会不会变」）。
        v0.3.4 的第一版 P2-3 守卫拿 `comparable()` 做全量比对，把结论也冻住了，
        于是这条命令永远落不了库，而 CLI 还在收 `--status/--headline`。
        评审 §13.2 要冻的是**来源**，不是**结论**。
        """
        self._seed(db)
        assert replay.main([DID, "--status", "AVOID", "--headline", "更保守",
                            "--model-ref", "anthropic/claude-opus-5", "--store"]) == 0
        assert load_online_card(DID).status == "WAIT"    # 在线那条没变
        with connect(db, readonly=True) as c:
            rows = c.execute("SELECT record_id, replay_of, status "
                             "FROM decision_records ORDER BY record_id").fetchall()
        assert [r["status"] for r in rows] == ["WAIT", "AVOID"]
        assert rows[1]["replay_of"] == rows[0]["record_id"]

    def test_回放不能改血缘(self, db):
        """P2-3：回放可以给新结论，但不许换掉「这个结论基于什么」。

        评审 §18 的 `test_replay_preserves_frozen_input_lineage`。
        判据逐字段来自 `card_ops.REPLAY_FROZEN_LINEAGE`。

        sabotage 验证：把 `save_replay_card()` 里的 `_lineage_drift` 判断删掉，
        本条变红 —— 一张换掉了证据的「回放」会被当成无损回放落进库。
        """
        self._seed(db)
        original = card_ops.load_original(DID)
        with connect(db, readonly=True) as c:
            parent = c.execute("SELECT record_id FROM decision_records "
                               "WHERE decision_id=? AND replay_of IS NULL",
                               (DID,)).fetchone()["record_id"]

        # 换掉冻结切片 id —— 结论可以变，但「基于哪份数据」不许变
        tampered = card_ops.synthesize(
            historical=True, decision_id=original.decision_id,
            verdicts=original.verdicts,
            judgment=card_ops.Judgment(status=original.status,
                                       headline=original.headline,
                                       synthesis=original.synthesis),
            model_ref="m", elapsed_ms=0,
            verdict_refs=list(original.input_verdict_refs),
            expected_roster=original.expected_roster,
            evidence_set_id="es-别人的切片")

        with pytest.raises(ValueError, match="evidence_set_id"):
            card_ops.save_replay_card(tampered, parent)

    def test_回放血缘守卫不误伤换模型(self, db):
        """守卫的守卫：只换 model_ref 不动血缘，必须放行。

        `model_ref` 已经从 `ALLOWED_REPLAY_CHANGES` 里摘掉了（那是给 `--check`
        用的集合），落库侧靠 `REPLAY_FROZEN_LINEAGE` 不包含它来放行 ——
        两个集合分别回答两个问题，这条钉住它们没有再纠缠到一起。
        """
        self._seed(db)
        assert "model_ref" not in card_ops.ALLOWED_REPLAY_CHANGES, (
            "model_ref 不该在 ALLOWED_REPLAY_CHANGES 里 —— 它会让 replay --check "
            "看不见 SYNTHESIS_VERSION 漂移")
        assert replay.main([DID, "--model-ref", "anthropic/claude-opus-5",
                            "--store"]) == 0

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


#: G 节 Live Acceptance 第 9 项要求的「Provider」——回放绝不许导入这些。
_PROVIDER_ROOTS = ("_sources", "easyup_biga.providers")


def _imports_provider_module(path: pathlib.Path) -> str | None:
    """`path` 里第一个匹配 `_PROVIDER_ROOTS` 的导入名；没有则 `None`。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            if any(n == r or n.startswith(r + ".") for r in _PROVIDER_ROOTS):
                return n
    return None


class TestReplay不碰Provider:
    """G 节 Live Acceptance 第 9 项：Replay 不访问外部 Provider。

    🔴 这条性质此前**只是附带成立**：`TestReplay` 的用例本来就在 `tests/conftest.py`
    autouse 的 `_no_network` 围栏下跑、也确实全部通过，但没有人把它当成一条
    要守的性质写下来。附带成立是脆弱的——明天 `card_ops.py` 加一个「顺手核对
    最新行情」的分支，只要没有别的测试恰好踩中那条路径，不会有任何东西报红。

    两条判据缺一不可：静态的只看得见字面导入（惰性 import、间接 import 会漏），
    动态的只证明「这次测试场景下没联网」（真的连了网但被 mock 掉的调用看不出来）。
    """

    def test_replay与card_ops不静态导入provider(self):
        for name in ("replay", "card_ops"):
            hit = _imports_provider_module(SCRIPTS / f"{name}.py")
            assert hit is None, f"{name}.py 导入了 Provider 模块 {hit!r}"

    def test_check真跑一次而且没联网(self, db):
        """动态判据：真调用 `--check`，autouse 的 `_no_network` 围栏没被
        绕过就是没联网——它会在任何联网点直接抛 `NetworkUsedInTest`。"""
        c = _synth(decision_id=DID, verdicts=full_roster(),
                   judgment=judgment(), model_ref="anthropic/claude-sonnet-5",
                   elapsed_ms=100)
        card_ops.persist(c)
        assert replay.main([DID, "--check"]) == 0


class TestPersistWritesAgentRunsLedger:
    """🔴 回归：批 C-II 把合成挪进 `card_ops.persist()` 之后，没有一并搬「记账本」
    这一步（旧路径靠 standalone `synthesize.py` 记，新路径不再跑那个脚本）——
    `agent_runs` 从此再没被在线路径写过，`tools/verify/spawn_check.py` 因此永远
    「判不了」（它要拿这张表的行去跟运行时 `subagent_runs` 交叉核对）。
    2026-09-22 第一次真实 live 验证时才暴露：不是 fail-open（没把失败判成成功），
    是把成功判成了失败，且每次真实出卡都会印一条误导性的红字。

    这条测试钉住修法：在线 `persist()` 必须记账本；回放 `persist()` 不许——
    回放没有重新执行任何 agent，给它记一遍「执行」是假账。
    """

    def test_在线路径记账_每个verdict一行(self, db):
        """给了执行溯源映射 ⇒ 每个 agent **恰好一行**（`sorted(...)==` 也钉住了不许双写）。"""
        c = _synth(decision_id=DID, verdicts=full_roster(),
                                judgment=judgment(), model_ref="anthropic/claude-sonnet-5")
        rr = {v.agent: f"openclaw-{v.agent}" for v in c.verdicts}
        card_ops.persist(c, runtime_run_ids=rr)
        rows = list_agent_runs(decision_id=DID, path=db)
        assert sorted(r["agent"] for r in rows) == sorted(STANCE_VOCAB), (
            "在线 persist() 必须给每个被 spawn 的 agent 记一行 agent_runs，"
            "否则 spawn_check.py 永远判不了")

    def test_没给执行溯源映射就一行都不记(self, db):
        """B2 §5.5：`runtime_run_ids is None` ⇒ **零行**。

        🔴 这条语义是反过来的（v0.5.0 改）。旧版「不给映射就给所有 verdict 记账」
        会让 standalone 合成写出一串自称在线、却证不了任何事的幽灵账本行
        —— 它根本没 spawn 过任何东西（评审 B2 §5.3 的 PoC）。

        ⇒ 映射**就是**执行溯源本身；没有它，就没有执行可记。

        sabotage 验证：去掉 `persist()` 里 `if runtime_run_ids is not None else ()`
        那半句，本条变红。
        """
        c = _synth(decision_id=DID, verdicts=full_roster(),
                   judgment=judgment(), model_ref="anthropic/claude-sonnet-5")
        card_ops.persist(c)
        assert list_agent_runs(decision_id=DID, path=db) == [], (
            "没有执行溯源映射却写了账本行 —— 那些行不描述任何执行事实")

    def test_在线路径走严格API_record_online_agent_run(self, db, monkeypatch):
        """P1-2：provenance 三字段齐全时，账本必须经过 `record_online_agent_run()`。

        🔴 这条测的是**它有没有生产调用方**，不是它的内部校验对不对。
        v0.3.4 加这个严格 API 时写着「用于 card_ops.persist() 的在线路径」，
        而 `persist()` 走的是 `record_verdict_run()` → `record_agent_run()`，
        根本不经过它 —— 一个只有测试在调的守卫（L-1）。

        sabotage 验证：把 `record_verdict_run()` 里那段路由删掉，本条立刻变红。
        """
        # 🔴 打在 `card_ops` 的名字上，不是 `_store.db` 上 —— `persist()` 现在
        #    直接 `from _store import record_online_agent_run`，绑定发生在导入时，
        #    改 db 模块上的那个属性对它没有影响（第一版就是这么写的，patch 无效）。
        seen: list[tuple[str, str, str]] = []
        real = card_ops.record_online_agent_run

        def spy(**kw):
            seen.append((kw["agent"], kw["orchestration_run_id"],
                         kw["runtime_run_id"]))
            return real(**kw)

        monkeypatch.setattr(card_ops, "record_online_agent_run", spy)
        c = _synth(decision_id=DID, verdicts=full_roster(),
                   judgment=judgment(), model_ref="anthropic/claude-sonnet-5")
        rr = {v.agent: f"openclaw-{v.agent}" for v in c.verdicts}
        card_ops.persist(c, runtime_run_ids=rr)

        assert sorted(a for a, _, _ in seen) == sorted(rr), (
            "在线路径没有走严格 API —— `record_online_agent_run()` 没有生产调用方，"
            f"实际经过它的只有 {sorted(a for a, _, _ in seen)}")
        assert all(orch == c.run_id for _, orch, _ in seen), \
            "账本行指回的编排执行尝试必须是卡自己那次"

    def test_没拿到runtime_run_id时仍然记账_但不走严格API(self, db, monkeypatch):
        """批 F 的立场不许被 P1-2 推翻：被 spawn 却没捞回 id 的 agent 仍要记账。

        不记就是漏账（账本行数与真实 spawn 次数脱节）。它只是走宽松分支，
        由 `spawn_proof_for_run()` 判成 UNKNOWN —— R-3，不是 PASS 也不是伪造。
        """
        called: list[str] = []
        monkeypatch.setattr(card_ops, "record_online_agent_run",
                            lambda **kw: called.append(kw["agent"]))
        c = _synth(decision_id=DID, verdicts=full_roster(),
                   judgment=judgment(), model_ref="anthropic/claude-sonnet-5")
        card_ops.persist(c, runtime_run_ids={v.agent: None for v in c.verdicts})

        assert called == [], "值为 None 时不该走要求 runtime_run_id 非空的严格 API"
        rows = list_agent_runs(decision_id=DID, path=db)
        assert sorted(r["agent"] for r in rows) == sorted(STANCE_VOCAB), \
            "宽松分支必须照样记账 —— 漏账比记一条判不了的账更糟"
        # 🔴 B2 §5.4：它记的是 `online_unproven`，**不许冒充完整的在线证据**。
        assert {r["provenance_mode"] for r in rows} == {"online_unproven"}, (
            "没捞回 runtime_run_id 的行必须是 online_unproven —— "
            f"实际 {sorted({r['provenance_mode'] for r in rows})}")

    def test_回放路径不记账(self, db):
        c = _synth(decision_id=DID, verdicts=full_roster(),
                                judgment=judgment(), model_ref="anthropic/claude-sonnet-5")
        rid = card_ops.persist(c, runtime_run_ids={v.agent: f"rr-{v.agent}"
                                                   for v in c.verdicts})
        before = len(list_agent_runs(decision_id=DID, path=db, limit=1000))
        # 🔴 N1：回放卡不许带 run_id（写边界拦它），所以重建一张不带的。
        replayed = _synth(decision_id=DID, verdicts=list(c.verdicts),
                          judgment=judgment(), model_ref="anthropic/claude-sonnet-5",
                          historical=True, run_id=None,
                          evidence_set_id=c.evidence_set_id,
                          verdict_refs=list(c.input_verdict_refs))
        card_ops.persist(replayed, replay_of=rid)
        after = len(list_agent_runs(decision_id=DID, path=db, limit=1000))
        assert after == before, (
            "回放不该重复记账——它没有重新执行任何 agent，"
            "记一遍「执行」是假账，会让 agent_runs 的行数与真实 spawn 次数脱节")


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
