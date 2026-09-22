"""DecisionOrchestrator 离线测试（确定性编排批 C-II）。

用一个**假 adapter** 驱动整条 Stage 0→3：假 adapter 在 `start()` 时替 Specialist/risk
往库里写一条合法 verdict（模拟它们真跑了 skill），`wait()` 返回归一化结果；判官那次
返回结构化 {status,headline,synthesis}。这样整条编排逻辑（占号→开 run→细粒度状态链→
按号取回 verdict→组装→落库）都能离线验，不花钱、不联网。

真实 spawn 的那部分（能不能真把 synthesizer 起起来、端到端出一张真卡）是 P1/P2
的 live 探针，走 `bin/biga-card` / orchestrator CLI 手工跑。
"""

from __future__ import annotations

import contextlib
import pathlib
import sys
import uuid
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from _contract import (  # noqa: E402
    STAGE1_AGENTS,
    STANCE_VOCAB,
    SYNTHESIZER_AGENT,
    AgentVerdict,
    Evidence,
    RunState,
    new_run_context,
    now_cn,
)
from _runtime import SpawnHandle, SpawnResult, SpawnStatus  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    load_online_card,
    run_events,
    save_verdict,
)

import orchestrator as orch_mod  # noqa: E402
from orchestrator import DecisionOrchestrator, OrchestratorError  # noqa: E402

RISK = orch_mod.RISK_AGENT
ROSTER = (*STAGE1_AGENTS, RISK)


def _verdict(agent: str, task_id: str) -> AgentVerdict:
    t = now_cn()
    stance = next(s for s in STANCE_VOCAB[agent] if s != "无法判定")
    field = f"{agent}_x"
    return AgentVerdict(
        task_id=task_id, agent=agent, status="completed", verdict="PASS",
        result={field: 1}, data_completeness=1.0,
        evidence=[Evidence(field=field, source="biga.db:test", value=1,
                           as_of=t - timedelta(seconds=60), retrieved_at=t)],
        stance=stance, elapsed_ms=100)


class FakeAdapter:
    """假 adapter：start() 替在场 agent 写 verdict；wait() 返回归一化结果。"""

    def __init__(self, db, *, judgment=None, absent=(), synth_status=SpawnStatus.SUCCEEDED):
        self.db = db
        self.judgment = judgment
        self.absent = set(absent)
        self.synth_status = synth_status
        self.spawned: list[tuple[str, str]] = []      # (agent, group_id)
        self.output_schemas: dict[str, dict] = {}

    def start(self, agent, task_id, task, *, group_id, run_timeout_sec=None,
              task_name=None, output_schema=None):
        self.spawned.append((agent, group_id))
        if output_schema is not None:
            self.output_schemas[agent] = output_schema
        rid = f"run-{agent}-{uuid.uuid4().hex[:6]}"
        if agent != SYNTHESIZER_AGENT and agent not in self.absent:
            save_verdict(_verdict(agent, task_id), path=self.db)
        return SpawnHandle(run_id=rid, agent=agent, task_id=task_id,
                           group_id=group_id, session_key=f"sk-{rid}")

    def wait(self, handles, timeout_sec):
        out = []
        for h in handles:
            if h.agent == SYNTHESIZER_AGENT:
                out.append(SpawnResult(h, self.synth_status, result="判官回复",
                                       structured=self.judgment))
            elif h.agent in self.absent:
                out.append(SpawnResult(h, SpawnStatus.TIMEOUT, error="没回来"))
            else:
                out.append(SpawnResult(h, SpawnStatus.SUCCEEDED, result="done",
                                       usage={"input": 10, "output": 5}))
        return out


def _make_orch(db, **fake_kw):
    fake = FakeAdapter(db, **fake_kw)

    @contextlib.contextmanager
    def attach(session_key, *, ttl_ms, **_):
        attach.last_session = session_key
        attach.last_ttl = ttl_ms
        yield fake

    orch = DecisionOrchestrator(attach=attach, deadline_sec=780)
    return orch, fake, attach


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    init_schema(p)
    return p


_GOOD_JUDGMENT = {"status": "WAIT", "headline": "核心矛盾一句话",
                  "synthesis": "两三句理由"}


class TestHappyPath:
    def test_走细粒度8步链到COMPLETED(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        assert card.status == "WAIT"
        seq = [(e["from_state"], e["to_state"]) for e in run_events(ctx.run_id, path=db)]
        assert seq == [
            (None, RunState.RECEIVED),
            (RunState.RECEIVED, RunState.PREFLIGHTED),
            (RunState.PREFLIGHTED, RunState.SNAPSHOT_FROZEN),
            (RunState.SNAPSHOT_FROZEN, RunState.STAGE1_RUNNING),
            (RunState.STAGE1_RUNNING, RunState.STAGE1_COMPLETED),
            (RunState.STAGE1_COMPLETED, RunState.RISK_RUNNING),
            (RunState.RISK_RUNNING, RunState.SYNTHESIZING),
            (RunState.SYNTHESIZING, RunState.CARD_PERSISTED),
            (RunState.CARD_PERSISTED, RunState.COMPLETED),
        ]

    def test_不走legacy粗边(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        edges = {(e["from_state"], e["to_state"]) for e in run_events(ctx.run_id, path=db)}
        assert (RunState.PREFLIGHTED, RunState.CARD_PERSISTED) not in edges

    def test_decision_id占号在open_run之前_从头非空(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        assert ctx.decision_id is None  # 进来时还没号
        card = orch.run(ctx)
        # 第一条 run_event（RECEIVED）就属于这个 decision —— 头一行就非空
        from _store import run_header
        assert run_header(ctx.run_id, path=db)["decision_id"] == card.decision_id

    def test_卡落库可读回(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        assert load_online_card(card.decision_id, path=db) is not None

    def test_五个specialist加risk共用各自groupId且都被spawn(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        orch.run(new_run_context(origin="cli", non_interactive=True))
        agents = [a for a, _ in fake.spawned]
        for a in ROSTER:
            assert a in agents
        assert SYNTHESIZER_AGENT in agents
        # Stage 1 五个共用一个 groupId
        s1_groups = {g for a, g in fake.spawned if a in STAGE1_AGENTS}
        assert len(s1_groups) == 1

    def test_判官spawn带output_schema(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        orch.run(new_run_context(origin="cli", non_interactive=True))
        assert SYNTHESIZER_AGENT in fake.output_schemas
        assert fake.output_schemas[SYNTHESIZER_AGENT]["required"] == \
            ["status", "headline", "synthesis"]

    def test_grant的ttl不小于总预算(self, db):
        orch, _, attach = _make_orch(db, judgment=_GOOD_JUDGMENT)
        orch.run(new_run_context(origin="cli", non_interactive=True))
        assert attach.last_ttl >= 780 * 1000  # §7-2 硬约束

    def test_usage落进run_events_detail(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        s1c = [e for e in run_events(ctx.run_id, path=db)
               if e["to_state"] == RunState.STAGE1_COMPLETED][0]
        assert "usage" in s1c["detail"]
        assert s1c["detail"]["usage"].get("market") == {"input": 10, "output": 5}


class TestPartialAndFailure:
    def test_缺席specialist进缺失项且照常出卡(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, absent=["news"])
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        codes = [m.code for m in card.missing]
        assert "supervisor.agent_no_response" in codes
        # 状态仍到 COMPLETED（出一张标着缺失的卡，比不出强）
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.COMPLETED

    def test_判官没给判断则整体FAILED(self, db):
        orch, _, _ = _make_orch(db, judgment=None)  # synthesizer 返回 structured=None
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError, match="判官"):
            orch.run(ctx)
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.FAILED

    def test_判官失败也是FAILED(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT,
                                synth_status=SpawnStatus.FAILED)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError):
            orch.run(ctx)
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.FAILED

    def test_零证据则FAILED(self, db):
        # 全员缺席 → 没有任何 verdict 落库
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, absent=list(ROSTER))
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError, match="零证据"):
            orch.run(ctx)
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.FAILED


class TestOwnershipGuard:
    """🔴 评审阻塞项 1：`orchestrator.py` 的 `main()` 自己拦发起方 —— 因为 `main`
    有 shell，能 `exec python3 orchestrator.py` 绕过 bin/biga-card 的守卫。这是把
    「main 够不到编排器」从 agent-spawn 面补到 exec 面，L-14 边界的最后一段。

    ⚠️ 判据落在 `main()` 上，且 `run()` 被换成会炸的桩 —— 一旦守卫没拦住、执行流
    跑到了 run()，测试立刻失败（而不是去真花钱 spawn）。fail-closed 的测试不能有
    fail-open 的代价（教程 19/24 章那条，这一批已经违反过一次）。
    """

    @staticmethod
    def _boom(self, ctx):  # 冒充 DecisionOrchestrator.run：越过守卫就会撞上它
        raise AssertionError("守卫没拦住，执行流跑到了 run() —— 真机上这里会 spawn 花钱")

    def test_main拒绝agent血缘_exit3(self, monkeypatch, db):
        import orchestrator as m
        monkeypatch.setattr(m.entry_guard, "classify_caller",
                            lambda *a, **k: (m.entry_guard.AGENT, "血缘里有运行时标记"))
        monkeypatch.setattr(m.DecisionOrchestrator, "run", self._boom)
        assert m.main([]) == 3

    def test_main放行human血缘(self, monkeypatch, db):
        import orchestrator as m

        class _Card:
            decision_id = "BIGA-20260101-001"
            def render(self): return "CARD"
            def to_dict(self): return {}

        ran = {}
        def _ok(self, ctx):
            ran["ok"] = True
            return _Card()
        monkeypatch.setattr(m.entry_guard, "classify_caller",
                            lambda *a, **k: (m.entry_guard.HUMAN, "人"))
        monkeypatch.setattr(m.DecisionOrchestrator, "run", _ok)
        assert m.main([]) == 0 and ran.get("ok"), "human 血缘应当放行到 run()"

    def test_main判不了也放行_但打依据(self, monkeypatch, db, capsys):
        import orchestrator as m

        class _Card:
            decision_id = "BIGA-20260101-002"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(m.entry_guard, "classify_caller",
                            lambda *a, **k: (m.entry_guard.UNKNOWN, "读不到血缘"))
        monkeypatch.setattr(m.DecisionOrchestrator, "run", lambda self, ctx: _Card())
        rc = m.main([])
        assert rc == 0
        assert "判不了" in capsys.readouterr().err, "UNKNOWN 放行也要把依据打出来（R-3）"
