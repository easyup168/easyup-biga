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
import json
import pathlib
import sys
import time
import uuid
from datetime import timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "decision-card" / "scripts"))

from _contract import (  # noqa: E402
    STAGE1_AGENTS,
    STANCE_VOCAB,
    SYNTHESIZER_AGENT,
    VETO_STANCE,
    AgentAssessment,
    AgentVerdict,
    Evidence,
    FactBundle,
    MissingItem,
    RunState,
    new_run_context,
    now_cn,
)
from _runtime import SpawnHandle, SpawnResult, SpawnStartError, SpawnStatus  # noqa: E402
from _snapshot import SnapshotCoordinator  # noqa: E402
from _sources import parse_index_daily  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    connect,
    load_online_card,
    run_events,
    save_assessment,
    save_verdict,
)

import orchestrator as orch_mod  # noqa: E402
from orchestrator import (  # noqa: E402
    DecisionOrchestrator,
    OrchestratorError,
    OrchestratorTimeout,
)

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
    """假 adapter：start() 替在场 agent 写 verdict；wait() 返回归一化结果。

    `fail_start_at=N` ⇒ 第 N 次 `start()` 抛 `SpawnStartError`（模拟 Stage 1 中途
    某一路起不来，用于 C-III 的 cancel 探针）；`cancelled` / `started_handles`
    记录取消调用与已成功启动的 handle，供探针核对身份。
    `wait_calls` 记录每次 wait 收到的 timeout（供 C3-4 预算收窄探针核对）。
    """

    def __init__(self, db, *, judgment=None, absent=(), synth_status=SpawnStatus.SUCCEEDED,
                 fail_start_at=None, risk_stance="放行"):
        self.db = db
        self.judgment = judgment
        self.absent = set(absent)
        self.synth_status = synth_status
        # 🔴 批 F：risk 被 spawn 时，它 amend 的 stance（模拟照新提示词跑 amend_verdict.py）。
        self.risk_stance = risk_stance
        self.spawned: list[tuple[str, str]] = []      # (agent, group_id)
        self.tasks: dict[str, str] = {}               # agent → 任务文本（批 F：验 risk 提示词）
        self.output_schemas: dict[str, dict] = {}
        self.fail_start_at = fail_start_at
        self._start_n = 0
        self.started_handles: list[SpawnHandle] = []  # 成功 start 返回的 handle
        self.cancelled: list[SpawnHandle] = []        # 收到 cancel() 的 handle
        self.wait_calls: list[tuple[tuple[str, ...], float]] = []  # (agents, timeout)
        #: 🔴 批 O：这次编排的 run_id。真 Adapter 从 attach 的 session_key
        #: （`agent:main:orchestrator-<run_id>`）知道自己属于哪次执行尝试，假的照做 ——
        #: Stage 1 落下的原件必须带上它，否则编排器按 run 取的时候一条也看不见。
        self.run_id: str | None = None

    def start(self, agent, task_id, task, *, group_id, run_timeout_sec=None,
              task_name=None, output_schema=None):
        self._start_n += 1
        if self.fail_start_at is not None and self._start_n == self.fail_start_at:
            raise SpawnStartError(f"[{agent}] 故意起不来（第 {self._start_n} 个 start）")
        self.spawned.append((agent, group_id))
        self.tasks[agent] = task
        if output_schema is not None:
            self.output_schemas[agent] = output_schema
        rid = f"run-{agent}-{uuid.uuid4().hex[:6]}"
        if agent == SYNTHESIZER_AGENT or agent in self.absent:
            pass  # 判官不产 verdict；缺席的 agent 不写
        elif agent == RISK:
            # 🔴 批 F：risk 不再自己产 fact —— 编排器已在 spawn 之前落好这次决策的 risk
            #    fact 行（此刻它是 (task_id, risk) 唯一的行），risk 只 amend 一个 stance。
            #    这正是新提示词教它做的（amend_verdict.py --ref <编排器给的> --stance …），
            #    也让「卡上 risk 判定引用的就是编排器预存那条」在测试里真实成立。
            # 🔴 批 O：不能再用 latest_verdict_ids(task_id)（已按 run 取代）。
            #    这个假 Adapter 拿不到编排器的 run_id，而它要找的那一行在本测试里
            #    唯一（一次运行只有一个 risk fact）——直接按 (task_id, agent, fact) 取。
            with connect(self.db, readonly=True) as _c:
                fact_id = _c.execute(
                    "SELECT verdict_id FROM agent_verdicts WHERE task_id=? AND agent=? "
                    "AND kind='fact' ORDER BY verdict_id DESC LIMIT 1",
                    (task_id, RISK)).fetchone()[0]
            save_assessment(
                AgentAssessment(task_id=task_id, agent=RISK, stance=self.risk_stance),
                fact_id=fact_id, path=self.db)
        else:
            save_verdict(_verdict(agent, task_id), run_id=self.run_id, path=self.db)
        h = SpawnHandle(runtime_run_id=rid, agent=agent, task_id=task_id,
                        group_id=group_id, session_key=f"sk-{rid}")
        self.started_handles.append(h)
        return h

    def cancel(self, handle):
        self.cancelled.append(handle)

    def wait(self, handles, timeout_sec):
        self.wait_calls.append((tuple(h.agent for h in handles), timeout_sec))
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


def _fake_daily(symbol, *, bars):
    """离线桩日线 —— 冻结用（批 D-II：orchestrator 现在会真的调 freeze）。"""
    from datetime import date, timedelta
    rows = [{"day": (date(2026, 3, 2) + timedelta(days=i)).strftime("%Y-%m-%d"),
             "open": 3000.0 + i, "high": 3010.0 + i, "low": 2990.0 + i,
             "close": 3000.0 + i, "volume": 10_000_000 + i} for i in range(bars)]
    return parse_index_daily(symbol, rows, raw_text=json.dumps(rows))


def _make_orch(db, **fake_kw):
    fake = FakeAdapter(db, **fake_kw)

    @contextlib.contextmanager
    def attach(session_key, *, ttl_ms, **_):
        attach.last_session = session_key
        attach.last_ttl = ttl_ms
        # 批 O：`agent:main:orchestrator-<run_id>` —— 与真 Adapter 同一个来源。
        fake.run_id = session_key.rsplit("-", 1)[-1]
        yield fake

    # 注入装了假 fetcher 的 coordinator —— freeze 因此不出网（禁网围栏兜底）。
    snapshot = SnapshotCoordinator(fetcher=_fake_daily, path=db)
    orch = DecisionOrchestrator(attach=attach, snapshot=snapshot, deadline_sec=780)
    return orch, fake, attach


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "biga.db"
    monkeypatch.setenv("BIGA_DB_PATH", str(p))
    # 🔴 D-3（外部评审 §15）：main() 新增的 Python 层 preflight（总闸/单实例锁）
    # 默认查真实仓库根的 .biga-card-stop / .biga-card.lock——测试必须隔离到
    # 临时路径。真实踩过：flock 按 open file description 算，不因为函数返回
    # 就释放（模块级 _lock_fh 活到进程结束），同一个 pytest 进程里一个测试
    # 调 main() 拿到锁之后，同文件里后面的测试会被这把锁跨测试拒掉。
    monkeypatch.setenv("BIGA_STOP_FILE", str(tmp_path / "no-such-stop-file"))
    monkeypatch.setenv("BIGA_CARD_LOCK_FILE", str(tmp_path / "biga-card.lock"))
    init_schema(p)
    return p


_GOOD_JUDGMENT = {"status": "WAIT", "headline": "核心矛盾一句话",
                  "synthesis": "两三句理由"}


class TestHappyPath:
    def test_走细粒度9步链到COMPLETED(self, db):
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
            # 🔴 批 G-I：CARD_PERSISTED → NOTIFICATION_PENDING（outbox 入队）→ COMPLETED。
            (RunState.CARD_PERSISTED, RunState.NOTIFICATION_PENDING),
            (RunState.NOTIFICATION_PENDING, RunState.COMPLETED),
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

    def test_CARD_PERSISTED带真实record_id(self, db):
        """C3-1（设计文档 §2 追加 5 §17-18）：转移的 detail 里是 persist() 返回的
        真实 record_id，不是占位串 —— 转移写在 persist() 成功拿到 record_id 之后。"""
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        ev = [e for e in run_events(ctx.run_id, path=db)
              if e["to_state"] == RunState.CARD_PERSISTED][0]
        assert isinstance(ev["detail"]["record_id"], int)
        assert ev["detail"]["record_id"] >= 1


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

    def test_全员stage1缺席_出无上游fact的卡_不再零证据FAIL(self, db):
        """🔴 批 F 行为变化：全员 Stage 1 缺席时，编排器**免费**落一条 risk「无上游」
        fact 并照常出卡，不再判「零证据 FAILED」——「出一张标着不知道的卡，比不出卡强」。
        这是 P2（无上游早退）在 orchestrator 一路走到 COMPLETED 的体现。
        ⚠️ 老行为（all-absent → FAILED "零证据"）在批 F 之前成立，是因为那时 risk 也缺席、
        库里一条 verdict 都没有；现在 risk 的 fact 由编排器无条件先落，ordered 至少有它。"""
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, absent=list(STAGE1_AGENTS))
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        # 到 COMPLETED，不是 FAILED
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.COMPLETED
        # 卡上有 risk 那条（UNKNOWN、无上游、无 stance），且 risk 没被 spawn
        risk_v = next(v for v in card.verdicts if v.agent == RISK)
        assert risk_v.verdict == "UNKNOWN"
        assert risk_v.stance is None                       # 早退不追加 assessment
        assert RISK not in [a for a, _ in fake.spawned]    # 无上游 ⇒ 不 spawn risk
        assert any(m.code == "risk.upstream.none" for m in card.missing)

    def test_stage1中途start失败_已启动的handle被cancel(self, db):
        """C3-2（设计文档 §2 追加 5.2）：Stage 1 五路 fan-out 里第 3 个 start() 抛错，
        之前已成功 start 的两个兄弟 handle 必须被 cancel() —— 否则它们会空跑到各自
        runTimeoutSeconds 才停，白烧钱。这恰好是 `OpenClawRuntimeAdapter.cancel()`
        一直缺的那个真调用方（N 可达 5、天然有 drain 的真实取消场景）。"""
        assert len(STAGE1_AGENTS) >= 3  # 前置：够第 3 个才谈得上「前两个」
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, fail_start_at=3)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError):
            orch.run(ctx)
        # 第 3 个 start 抛错 ⇒ 前两个已成功启动
        assert len(fake.started_handles) == 2
        # 🔴 前两个都被 cancel —— 身份一致，不只是数量对
        assert fake.cancelled == fake.started_handles
        # start 阶段就崩，没进 wait；死在 STAGE1_RUNNING → FAILED
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.FAILED

    def test_persist抛异常时run_events无CARD_PERSISTED(self, db, monkeypatch):
        """C3-1（设计文档 §2 追加 5 §17-18）：CARD_PERSISTED 必须写在 persist()
        成功之后。否则 persist() 抛错会在 run_events 里留一条「已落库」的假记录，
        而库里其实没有这张卡 —— 一个可修复的失败被记成了不可修复的谎。
        这条正是外部评审建议的回归测试。"""
        import card_ops

        def boom(card, **kw):
            raise RuntimeError("落库炸了（人工注入）")

        monkeypatch.setattr(card_ops, "persist", boom)
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError):
            orch.run(ctx)
        states = [e["to_state"] for e in run_events(ctx.run_id, path=db)]
        assert RunState.CARD_PERSISTED not in states  # 没落成就不许有这条假记录
        assert states[-1] == RunState.FAILED           # 死在 persist 之前（SYNTHESIZING→FAILED）


class TestDeadlineBudget:
    """C3-4（设计文档 §2 追加 5 §35）：各阶段不再各用各的固定预算，而是按总
    deadline 还剩多少收窄 —— 三段之和不会超过 `deadline_sec`，不靠外层 bash
    `timeout` 当唯一防线。"""

    def test_各阶段等待被剩余deadline收窄(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        # 总预算远小于任一阶段自己的固定预算 ⇒ 三段都应被压到总预算以内。
        orch.deadline_sec = 50
        orch.stage1_sec, orch.risk_sec, orch.synth_sec = 300, 180, 180
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)  # 假 wait 是瞬时的 ⇒ 仍能跑到 COMPLETED
        timeouts = [t for _, t in fake.wait_calls]
        assert len(timeouts) == 3  # stage1 / risk / synth 各一次 wait
        s1_to, risk_to, synth_to = timeouts
        # 每一段都 ≤ 总预算，且严格小于它自己那段固定值（证明真的被收窄了）。
        assert 0 < s1_to <= 50 and s1_to < 300
        assert 0 < risk_to <= 50 and risk_to < 180
        assert 0 < synth_to <= 50 and synth_to < 180

    def test_stage_timeout取剩余与阶段预算的较小者(self, db):
        orch, _, _ = _make_orch(db)
        now = time.monotonic()
        # 剩余很多 → 用阶段自己的预算
        assert orch._stage_timeout(now + 1000, 180) == pytest.approx(180, abs=2)
        # 剩余很少 → 收窄到剩余
        assert 25 <= orch._stage_timeout(now + 30, 180) <= 30

    def test_stage_timeout预算耗尽则抛OrchestratorTimeout(self, db):
        """remaining <= 0 时抛的必须是 `OrchestratorTimeout`（`OrchestratorError`
        的子类），不是裸 `OrchestratorError`——2026-09-24（外部评审 §13）之前两者
        共用同一个类型，`run()` 的 except 链因此把超时也转成了 FAILED，查死因时
        分不清"等太久"还是"真的跑挂了"。`pytest.raises(OrchestratorError, ...)`
        本来就能捕获子类，这里改用更精确的类型断言，确保真的是新子类。"""
        orch, _, _ = _make_orch(db)
        past = time.monotonic() - 1  # deadline 已经过去
        with pytest.raises(OrchestratorTimeout, match="预算"):
            orch._stage_timeout(past, 180)

    def test_端到端_超时进TIMEOUT状态_不是FAILED(self, db, monkeypatch):
        """完整走一次 `run()`，`_stage_timeout` 抛 `OrchestratorTimeout` ⇒ 最终
        状态必须是 `RunState.TIMEOUT`。探针：把 `run()` 里 `except
        OrchestratorTimeout` 那个分支删掉（或把它挪到 `except OrchestratorError`
        之后），这条就会退回 FAILED、变红。"""
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)

        def boom(deadline, stage_budget):
            raise OrchestratorTimeout("总预算耗尽（人工注入）")

        monkeypatch.setattr(orch, "_stage_timeout", boom)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorTimeout):
            orch.run(ctx)
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.TIMEOUT


class TestSnapshotFreeze:
    """批 D-II：orchestrator 在 Stage 1 之前冻结一次，SNAPSHOT_FROZEN 转移接了真东西。"""

    def test_SNAPSHOT_FROZEN带evidence_set_id(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        sf = [e for e in run_events(ctx.run_id, path=db)
              if e["to_state"] == RunState.SNAPSHOT_FROZEN][0]
        esid = sf["detail"]["evidence_set_id"]
        assert esid.startswith("es-")
        assert sf["detail"]["bars"] == 120  # 取最大消费者 technical 的根数，不是 25

    def test_一次决策只冻2行raw_登记1个evidence_set(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        with connect(db, readonly=True) as c:
            n_raw = c.execute("SELECT COUNT(*) FROM raw_market_snapshot").fetchone()[0]
            n_es = c.execute("SELECT COUNT(*) FROM evidence_sets").fetchone()[0]
        assert n_raw == 2, "两个指数代码 ⇒ 冻结只落 2 行 raw"
        assert n_es == 1

    def test_specialist_task_只给日线三个agent带esid(self, db):
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        for a in ("market", "sector", "technical"):
            assert "--evidence-set-id" in orch._specialist_task(a, "BIGA-20260101-001", "es-x", "rid-x")
        for a in ("emotion", "news"):
            assert "--evidence-set-id" not in orch._specialist_task(a, "BIGA-20260101-001", "es-x", "rid-x")

    def test_JI_evidence_set落库带本次run_id(self, db):
        """🔴 批 J-I item 4：编排器把 ctx.run_id 传进 freeze_index_daily ⇒
        evidence_sets.run_id == 这次 run 的 id。这条接线只有代码、容易漂，钉住它。"""
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        orch.run(ctx)
        with connect(db, readonly=True) as c:
            got = c.execute("SELECT run_id FROM evidence_sets").fetchall()
        assert got and all(r[0] == ctx.run_id for r in got), (
            f"evidence_sets.run_id 没绑到本次 run：{[r[0] for r in got]} != {ctx.run_id}")

    def test_JI_五个stage1的任务文本都带run_id(self, db):
        """🔴 批 J-I item 3：--run-id 给每一个**自己跑 skill 落库**的 Stage 1 agent。
        ⚠️ 批 F 起 risk 不再自己跑 risk_check.py —— 它的 fact run_id 改由编排器
        save_fact_bundle 直接 capture（见下面 test_JI_F_risk_fact的run_id由编排器capture），
        所以 `_risk_task` 里不再有 --run-id，这条只覆盖 Stage 1 那五个。"""
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        RID = "deadbeef" * 4
        for a in ("market", "sector", "technical", "emotion", "news"):
            assert f"--run-id {RID}" in orch._specialist_task(a, "BIGA-20260101-001", "es-x", RID)

    def test_JI_F_risk_fact的run_id由编排器capture(self, db):
        """🔴 批 F + J-I：risk 不再自己跑 skill，它的 fact run_id 改由编排器
        save_fact_bundle(risk_fb, run_id=ctx.run_id) 直接落 —— 查库里那条 risk fact
        行的 run_id 就是本次 run 的 id（J-I「每个产落库记录的 agent 都能追到 run」对
        risk 这条，现在由编排器兑现，不再靠 risk 手传 --run-id）。"""
        orch, _, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        with connect(db, readonly=True) as c:
            got = c.execute(
                "SELECT run_id FROM agent_verdicts WHERE task_id=? AND agent='risk' "
                "AND kind='fact'", (card.decision_id,)).fetchall()
        assert got and all(r[0] == ctx.run_id for r in got), (
            f"risk fact 行的 run_id 没绑到本次 run：{[r[0] for r in got]} != {ctx.run_id}")

    def test_freeze失败则整体FAILED(self, db):
        """freeze 抓不到 ⇒ 异常上抛 ⇒ FAILED（fail-closed，不退回各自抓一份）。"""
        def _boom(symbol, *, bars):
            raise RuntimeError("数据源挂了")
        boom_coord = orch_mod.SnapshotCoordinator(fetcher=_boom, path=db)
        fake = FakeAdapter(db, judgment=_GOOD_JUDGMENT)

        @contextlib.contextmanager
        def attach(session_key, *, ttl_ms, **_):
            yield fake
        orch = DecisionOrchestrator(attach=attach, snapshot=boom_coord, deadline_sec=780)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError):
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


class TestPreflightGates:
    """🔴 Budget / flock / 总闸下沉（外部评审 §15）——纵深防御第二层，
    `bin/biga-card` 自己那道守卫不删。判据同 `TestOwnershipGuard`：`run()`
    换成必炸的桩，一旦守卫没拦住、执行流跑到了 run()，测试立刻失败。

    `db` fixture 已经把 `BIGA_STOP_FILE`/`BIGA_CARD_LOCK_FILE` 隔离到临时路径
    （见 fixture 定义的注释）——这里的用例不会碰真实仓库根的那两个文件。
    """

    @staticmethod
    def _boom(self, ctx):
        raise AssertionError("守卫没拦住，执行流跑到了 run() —— 真机上这里会 spawn 花钱")

    @staticmethod
    def _human(monkeypatch):
        monkeypatch.setattr(orch_mod.entry_guard, "classify_caller",
                            lambda *a, **k: (orch_mod.entry_guard.HUMAN, "人"))

    def test_总闸文件存在时main拒绝_exit3(self, monkeypatch, db, tmp_path):
        self._human(monkeypatch)
        stop_file = tmp_path / "stop-marker"
        stop_file.write_text("测试用\n", encoding="utf-8")
        monkeypatch.setenv("BIGA_STOP_FILE", str(stop_file))
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run", self._boom)
        assert orch_mod.main([]) == 3

    def test_总闸文件不存在时放行(self, monkeypatch, db):
        self._human(monkeypatch)

        class _Card:
            decision_id = "BIGA-20260101-003"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run",
                            lambda self, ctx: _Card())
        assert orch_mod.main([]) == 0

    def test_budget不通过时main拒绝_exit3(self, monkeypatch, db):
        self._human(monkeypatch)
        monkeypatch.setattr(orch_mod.budget, "check_budget",
                            lambda **k: ["人工注入的预算拒绝理由（测试）"])
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run", self._boom)
        assert orch_mod.main([]) == 3

    def test_budget通过时放行(self, monkeypatch, db):
        self._human(monkeypatch)
        monkeypatch.setattr(orch_mod.budget, "check_budget", lambda **k: [])

        class _Card:
            decision_id = "BIGA-20260101-004"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run",
                            lambda self, ctx: _Card())
        assert orch_mod.main([]) == 0

    def test_budget不通过但BIGA_CARD_FORCE为1时放行(self, monkeypatch, db):
        """🔴 2026-09-24（对抗性复核）：`bin/biga-card` 自己那道 Budget Guard
        认 `BIGA_CARD_FORCE=1`（打印"确实要跑：BIGA_CARD_FORCE=1 bin/biga-card"
        这条恢复路径），但这里之前不认——wrapper 放行之后编排器自己又拦一次，
        人已经显式说了"确实要跑"却还是被拒。两层必须认同一个开关。"""
        self._human(monkeypatch)
        monkeypatch.setenv("BIGA_CARD_FORCE", "1")
        monkeypatch.setattr(orch_mod.budget, "check_budget",
                            lambda **k: ["人工注入的预算拒绝理由（测试）"])

        class _Card:
            decision_id = "BIGA-20260101-004b"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run",
                            lambda self, ctx: _Card())
        assert orch_mod.main([]) == 0

    def test_BIGA_CARD_FORCE不为1时仍然生效budget检查(self, monkeypatch, db):
        """探针：确认上一条不是"FORCE 让检查整个失效"——只有精确等于字符串
        "1" 才越过，其余取值（包括常见的误用 "true"/"0"）一律仍然拒绝。"""
        self._human(monkeypatch)
        monkeypatch.setenv("BIGA_CARD_FORCE", "true")
        monkeypatch.setattr(orch_mod.budget, "check_budget",
                            lambda **k: ["人工注入的预算拒绝理由（测试）"])
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run", self._boom)
        assert orch_mod.main([]) == 3

    def test_两次不带LOCK_HELD的并发调用_第二次被单实例锁拒绝(self, monkeypatch, db):
        """🔴 真实 flock 语义（不 mock）：第一次调用真的拿到锁且不释放
        （`_lock_fh` 模块级变量活到进程结束），第二次调用自己重新 open() 同一个
        锁文件再 flock，会与第一次互斥——这正是单实例锁该有的效果。"""
        self._human(monkeypatch)

        class _Card:
            decision_id = "BIGA-20260101-005"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run",
                            lambda self, ctx: _Card())
        assert orch_mod.main([]) == 0   # 第一次：真的拿到锁，正常跑完
        assert orch_mod.main([]) == 3   # 第二次：被第一次持有的锁拒绝

    def test_带LOCK_HELD时信任锁已持有_不会被自己的锁挡住(self, monkeypatch, db):
        """🔴 这是 flock 语义陷阱的直接验证：`bin/biga-card` 拿到锁后传
        `BIGA_CARD_LOCK_HELD=1`，子进程看到它就跳过自检——如果没有这个跳过
        逻辑（探针：删掉 `_preflight_lock` 里那个 early return），第二次调用
        会被第一次自己持有的锁挡住，即便两次调用其实是同一条正常出卡流程的
        一部分，不是真的并发冲突。"""
        self._human(monkeypatch)

        class _Card:
            decision_id = "BIGA-20260101-006"
            def render(self): return "CARD"
            def to_dict(self): return {}
        monkeypatch.setattr(orch_mod.DecisionOrchestrator, "run",
                            lambda self, ctx: _Card())
        assert orch_mod.main([]) == 0  # 第一次：真的拿到锁（占着，不释放）
        monkeypatch.setenv("BIGA_CARD_LOCK_HELD", "1")
        assert orch_mod.main([]) == 0, \
            "带 BIGA_CARD_LOCK_HELD 时不该自己抢锁，也就不会被自己占着的锁挡住"

    def test_bin_biga_card真的会设置LOCK_HELD且在flock成功之后(self):
        """`main()` 里的 `_preflight_lock` 只在函数级测过（上面几条）——这里补
        `bin/biga-card` 那一侧：`export BIGA_CARD_LOCK_HELD=1` 必须真的写在脚本
        里，且在 `flock -n 9` 成功（没有触发它自己的 `exit 3`）之后、在真正调用
        编排器之前。位置判据同 `test_entry_guard.py::test_守卫排在第一次花钱
        之前`——按字符串在文件里第一次出现的位置比较。"""
        text = (REPO / "bin" / "biga-card").read_text(encoding="utf-8")
        run = text.split("# ── 出新卡")[1]
        for name, needle in (("flock 检查", "flock -n 9"),
                             ("设置 LOCK_HELD", "BIGA_CARD_LOCK_HELD=1"),
                             ("调编排器", "skills/decision-card/scripts/orchestrator.py")):
            assert needle in run, f"出卡流程里找不到「{name}」这一步（{needle}）"
        i_flock = run.find("flock -n 9")
        i_held = run.find("BIGA_CARD_LOCK_HELD=1")
        i_orch = run.find("skills/decision-card/scripts/orchestrator.py")
        assert i_flock < i_held < i_orch, (
            "BIGA_CARD_LOCK_HELD 必须设在 flock 成功之后、调用编排器之前——"
            "设早了锁可能还没拿到，设晚了编排器已经跑了自己的 flock 自检")


def _foreign_fact(*, verdict_ids, store, task_id):
    """冒充 build_fact_bundle 的注入桩：返回一个 foreign（证据跨决策污染）早退 fact。

    🔴 为什么要注入：编排器自己算的 s1_refs 来自 latest_verdict_ids(did)，永远属于**本**
    决策，天然产不出 foreign。所以「编排器遇到 foreign 就不 spawn」这条只能靠注入一个
    status='failed' 的 fact 来验证 —— 验的是编排器对确定性 failed fact 的处置，
    而 foreign 是 failed 的一种。task_id 用编排器传进来的（就是本次 did）。"""
    t = now_cn()
    attribution = {"foreign_task_ids": ["BIGA-20260101-999"],
                   "upstream_attribution": {"market": "BIGA-20260101-999"}}
    return FactBundle(
        task_id=task_id, agent=RISK, status="failed", verdict="UNKNOWN",
        result=attribution, data_completeness=0.0,
        evidence=[Evidence(field=k, source="derived:risk-check", value=v,
                           as_of=t, retrieved_at=t) for k, v in attribution.items()],
        missing=[MissingItem("上游判定来自别的决策，不能合在一起审",
                             "risk.upstream.foreign_decision")])


class TestBatchFRiskInline:
    """🔴 批 F：risk 的事实由编排器在 spawn 之前直接算好落库。两种确定性早退
    （foreign / 无上游）不 spawn risk；其余仍 spawn，但 risk 只解读编排器算好的
    verdict_ref、不再自己跑 risk_check.py。P1-P3 / P5 探针。"""

    @staticmethod
    def _risk_facts(db, did):
        with connect(db, readonly=True) as c:
            return [r["verdict_id"] for r in c.execute(
                "SELECT verdict_id FROM agent_verdicts WHERE task_id=? AND agent=? "
                "AND kind='fact' ORDER BY verdict_id", (did, RISK)).fetchall()]

    # ── P1：foreign（证据跨决策污染）⇒ 不 spawn risk ──
    def test_P1_foreign早退_不spawn_risk_事实进卡不缺席(self, db, monkeypatch):
        monkeypatch.setattr(orch_mod, "build_fact_bundle", _foreign_fact)
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT)
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        did = card.decision_id
        # 1) 编排器没有以 RISK 调 ad.start
        assert RISK not in [a for a, _ in fake.spawned], "foreign 早退不该 spawn risk"
        # 2) FactBundle 正确落库（恰好一条 risk fact）
        facts = self._risk_facts(db, did)
        assert len(facts) == 1, f"risk fact 行应恰好 1 条，实际 {len(facts)}"
        # 3) 这条 verdict_ref 参与了合成（在卡的 input_verdict_refs 里）
        assert facts[0] in [r.verdict_id for r in card.input_verdict_refs], \
            "编排器落的 risk fact 没参与合成"
        # 4) 卡上 risk 显示「给出事实、无 stance」，不是「缺席」
        risk_v = next(v for v in card.verdicts if v.agent == RISK)
        assert risk_v.verdict == "UNKNOWN" and risk_v.stance is None
        assert RISK not in card.absent_agents, "risk 给了事实，不该被算成缺席"
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.COMPLETED

    # ── P2：完全没有上游 ⇒ 不 spawn risk（自然可达：全员 Stage 1 缺席）──
    def test_P2_无上游早退_不spawn_risk_事实进卡不缺席(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, absent=list(STAGE1_AGENTS))
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        did = card.decision_id
        assert RISK not in [a for a, _ in fake.spawned], "无上游早退不该 spawn risk"
        facts = self._risk_facts(db, did)
        assert len(facts) == 1
        assert facts[0] in [r.verdict_id for r in card.input_verdict_refs]
        risk_v = next(v for v in card.verdicts if v.agent == RISK)
        assert risk_v.verdict == "UNKNOWN" and risk_v.stance is None
        assert RISK not in card.absent_agents                   # risk 到场
        assert set(STAGE1_AGENTS) <= set(card.absent_agents)    # 而 5 个 stage1 缺席
        assert any(m.code == "risk.upstream.none" for m in card.missing)

    # ── P3：正常场景 ⇒ 仍 spawn，卡上 risk 判定引用的就是编排器预存那条 ──
    def test_P3_正常_risk仍spawn_引用编排器预存的同一fact(self, db):
        orch, fake, _ = _make_orch(db, judgment=_GOOD_JUDGMENT, risk_stance="警示")
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        did = card.decision_id
        assert RISK in [a for a, _ in fake.spawned], "正常场景 risk 仍应被 spawn"
        # 恰好一条 risk fact（risk 没有自己再产一条 —— 唯一索引也不允许）
        facts = self._risk_facts(db, did)
        assert len(facts) == 1, f"risk fact 行应恰好 1 条（编排器预存），实际 {len(facts)}"
        orch_ref = facts[0]
        # risk 的 assessment amends 的正是编排器预存那条 fact（不是它自己又产的一条）
        with connect(db, readonly=True) as c:
            row = c.execute(
                "SELECT amends FROM agent_verdicts WHERE task_id=? AND agent=? "
                "AND kind='assessment'", (did, RISK)).fetchone()
        assert row is not None and row["amends"] == orch_ref, \
            "risk 的判断没有挂在编排器预存的那条 fact 上"
        # 新提示词生效：任务文本引用编排器给的 ref，且明令不再跑 skill
        risk_task = fake.tasks[RISK]
        assert f"verdict_ref={orch_ref}" in risk_task
        assert f"--ref {orch_ref}" in risk_task
        assert "不要重新跑 risk_check.py" in risk_task
        # 卡上 risk 判定带 stance（经 amend 从 assessment 穿回）
        risk_v = next(v for v in card.verdicts if v.agent == RISK)
        assert risk_v.stance == "警示"

    # ── P5：VETO 回归 —— 改完调用路径后，否决穿透一个环节都没断 ──
    def test_P5_VETO经批F全路径仍拦住BUY(self, db):
        # risk 给否决、判官却想给 BUY —— 合成阶段必须拒（否决从 amend 一路穿到 DecisionCard）。
        orch, _, _ = _make_orch(
            db, judgment={"status": "BUY", "headline": "h", "synthesis": "s"},
            risk_stance=VETO_STANCE)
        ctx = new_run_context(origin="cli", non_interactive=True)
        with pytest.raises(OrchestratorError):
            orch.run(ctx)
        assert run_events(ctx.run_id, path=db)[-1]["to_state"] == RunState.FAILED

    def test_P5_VETO给AVOID正确体现则出卡(self, db):
        # 反面对照：risk 否决 + 判官给 AVOID（正确体现否决）⇒ 出卡，risk.stance 就是否决。
        orch, _, _ = _make_orch(
            db, judgment={"status": "AVOID", "headline": "h", "synthesis": "s"},
            risk_stance=VETO_STANCE)
        ctx = new_run_context(origin="cli", non_interactive=True)
        card = orch.run(ctx)
        assert card.status == "AVOID"
        risk_v = next(v for v in card.verdicts if v.agent == RISK)
        assert risk_v.stance == VETO_STANCE


class TestModuleIdentityAcrossTestFiles:
    """🔴 2026-09-24（A 节，三轮对抗性复核）：`orchestrator.py` 自己 `import
    card_ops` / `from risk_check import build_fact_bundle`，这两个名字同时被
    十几个测试文件用 `importlib.util.spec_from_file_location` 各自"重新加载"一份
    （历史原因：`decision-card`/`risk-check` 目录带连字符，不是能直接 import 的
    包名）。如果任何一处重新加载时无条件覆写 `sys.modules`（不检查是否已存在），
    `orchestrator.py` 自己绑定的对象和某个测试文件后来创建的新对象就会静默分裂
    成两个——`monkeypatch.setattr(card_ops, "persist", …)` 打在新对象上，
    orchestrator 却调旧对象，patch 悄悄不生效（真机复现过：`pytest
    tests/test_orchestrator.py tests/test_facts_split_e3.py` 会红，反序不会）。

    ⚠️ 2026-09-24（同一天，另一轮复核纠正了这里）：上一版文档字符串写着"这条
    测试不依赖收集顺序"——**不成立**，已经被指出并实测证伪。这条断言能不能
    抓到新引入的违规，取决于那个违规文件在字母序里排在 `test_orchestrator.py`
    前面还是后面：排在前面的话，`orchestrator.py` 自己的 `import` 反而会
    "捡漏"到违规文件已经登记的对象，两者天然一致，断言通过——默认全量
    `pytest -q`（字母序）对"文件名排在 orchestrator 前面"的违规**完全失明**
    （实测确认：12 处里有 6 处属于这一类，`test_decision_card`/
    `test_emotion_calc`/`test_facts_split{,_e2,_e3}`/`test_market_calc`）。

    真正不依赖顺序的判据是 `tests/test_module_load_idempotent.py` 的源码级
    扫描（同 `test_no_raw_sqlite.py` 的风格，不靠运行时表现）——那一条才是
    这一类问题的主守卫。这里留着的两条运行时断言仍有价值：它们精确验证
    `card_ops`/`risk_check` 这两个**已知被 orchestrator.py 直接使用**的名字
    当下这一刻确实是同一个对象，是源码扫描之外的第二层信号，但不能单独
    依赖它们判断"有没有新的复制体忘了加幂等检查"。
    """

    def test_orchestrator绑定的card_ops与现在import拿到的是同一个对象(self):
        import card_ops
        assert orch_mod.card_ops is card_ops, (
            "orchestrator.py 绑定的 card_ops 与当前 sys.modules 里的不是同一个对象——"
            "某个测试文件的 _load() 无条件覆写了 sys.modules['card_ops']，"
            "monkeypatch 会打偏（见类文档字符串）")

    def test_orchestrator绑定的risk_check_build_fact_bundle与现在import拿到的是同一个(self):
        import risk_check
        assert orch_mod.build_fact_bundle is risk_check.build_fact_bundle, (
            "orchestrator.py 绑定的 risk_check.build_fact_bundle 与当前 "
            "sys.modules['risk_check'] 里的不是同一个函数——"
            "某个测试文件的 _load()/一次性 spec_from_file_location 无条件覆写了 "
            "sys.modules['risk_check']，monkeypatch 会打偏（见类文档字符串）")
