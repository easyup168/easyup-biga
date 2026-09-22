#!/usr/bin/env python3
"""DecisionOrchestrator —— 用**程序**驱动 Stage 0→3（确定性编排批 C-II）。

设计原则（设计文档 §1）::

    Program decides workflow.   ← 这个类：什么时候开始、下一步是谁、超时多久、
                                   什么算完成、哪些数据属于同一个 Decision
    Agent decides judgment.     ← Specialist 判断市场；synthesizer 判官给
                                   status/headline/synthesis

它替代的是**老路径里 main 的编排职责**（spawn 谁、按什么顺序、怎么等、传号、
合成），不是 main 的判断职责 —— 后者交给 `synthesizer` 判官（一个 `allowAgents=[]`
的叶子 agent，结构上没有 spawn 能力，见 `_contract.SYNTHESIZER_AGENT`）。

🔴 与老路径最本质的区别：`main` 在这里**没有步骤可执行** —— 编排是一个 Python
   对象，不是可被 spawn 的 agent，main 没有一条工具调用能到达它。这不是「守卫
   拦住它」，是「够不到」。L-14 出卡递归事故就出在「谁能启动」这条边界上，
   这一批把那条边界从提示词约定变成程序结构。

状态机走**细粒度链**（批 B 的 8 步），不走 legacy 粗边 —— 每一步都是这个对象
自己驱动的，它理应知道走到哪了。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import sys
import time
import uuid

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _contract import (  # noqa: E402
    STAGE1_AGENTS,
    SYNTHESIZER_AGENT,
    MissingItem,
    RunContext,
    RunState,
    new_run_context,
)
from _runtime import OpenClawRuntimeAdapter, SpawnStatus  # noqa: E402
from _snapshot import SnapshotCoordinator  # noqa: E402
from _store import (  # noqa: E402
    latest_verdict_ids,
    open_run,
    reserve_decision_id,
    transition,
)
import card_ops  # noqa: E402
import entry_guard  # noqa: E402

#: Stage 2 的制衡层。这一批只有 risk（discipline 按裁定 13 不建）。
RISK_AGENT = "risk"

#: 🔴 冻结数据切片（批 D-II）。Stage 1 之前冻结这两个指数的日线一次，
#: market/sector/technical 都从这一份读，不各自联网。
SNAPSHOT_SYMBOLS = ("sh000001", "sz399106")

#: 冻结的根数 —— 取**所有消费者里最大的那个**。今天是 technical 的 120
#: （`technical_calc.py::BAR_COUNT`），不是 market 的 25。取小了 technical 读 120
#: 会撞 read 端的 fail-closed（见 `_snapshot` 教程第 25/26 章、`TODO.md` 批 D-II 输入）。
SNAPSHOT_BARS = 120

#: 会读冻结日线的 Specialist —— 只有这三个消费 `fetch_index_daily`。
#: 只给它们的任务文本加 `--evidence-set-id`；emotion/news 的 skill 没有这个参数。
#: 谁给某个 skill 接了 `--evidence-set-id`，谁把它加进这里 —— 漂了探针 P1 会抓到
#: （三条 Evidence 反查不到同一个冻结集）。
SNAPSHOT_INDEX_AGENTS = frozenset({"market", "sector", "technical"})

#: 判官的结构化输出契约 —— 靠 `outputSchema` 拿到干净的 {status, headline, synthesis}，
#: 不去解析它的自然语言回复（那是 F3/L-13 的形状）。
JUDGMENT_SCHEMA = {
    "type": "object",
    "required": ["status", "headline", "synthesis"],
    "properties": {
        "status": {"type": "string", "enum": ["BUY", "WAIT", "AVOID", "BLOCK"]},
        "headline": {"type": "string"},
        "synthesis": {"type": "string"},
    },
}


class OrchestratorError(RuntimeError):
    """编排失败（判官没给出判断 / 零证据 / 关键步骤挂了）。CLI 据此给退出码。"""


class DecisionOrchestrator:
    """`run(ctx) -> DecisionCard`，程序驱动整条出卡链路。

    时间预算（可被环境变量覆盖，与 `bin/biga-card` 的老口径对齐）：
    总预算 `BIGA_CARD_DEADLINE_SEC`（780s），分给 Stage 1 / risk / 判官三段。
    某一段等超时 ⇒ 缺席的 agent 记 missing、照常往下走（「出一张标着不知道的卡，
    比不出卡强」）；只有关键步骤（判官）失败或**零证据**才整体 FAIL。
    """

    def __init__(
        self,
        *,
        attach=None,
        snapshot: SnapshotCoordinator | None = None,
        model_ref: str = "anthropic/claude-sonnet-5",
        deadline_sec: int | None = None,
        stage1_sec: int | None = None,
        risk_sec: int | None = None,
        synth_sec: int | None = None,
    ):
        # attach 是依赖注入点：默认真实 adapter，测试传假的。
        self._attach = attach or OpenClawRuntimeAdapter.attach
        # snapshot 同理：默认真实 coordinator（真联网抓一次并冻结），测试传一个
        # 装了假 fetcher 的，freeze 就不出网。
        self._snapshot = snapshot or SnapshotCoordinator()
        self._model_ref = model_ref
        self.deadline_sec = deadline_sec or int(os.environ.get("BIGA_CARD_DEADLINE_SEC", "780"))
        self.stage1_sec = stage1_sec or int(os.environ.get("BIGA_ORCH_STAGE1_SEC", "300"))
        self.risk_sec = risk_sec or int(os.environ.get("BIGA_ORCH_RISK_SEC", "180"))
        self.synth_sec = synth_sec or int(os.environ.get("BIGA_ORCH_SYNTH_SEC", "180"))

    # ── 主流程 ───────────────────────────────────────────────────────────
    def run(self, ctx: RunContext) -> "card_ops.DecisionCard":
        # Stage 0：占号（在 open_run 之前 —— decision_id 从一开始就非空，
        # 不留 legacy 那个「开 run 时还没号」的口子）。
        did = ctx.decision_id or reserve_decision_id(by="orchestrator")
        if ctx.decision_id != did:
            ctx = dataclasses.replace(ctx, decision_id=did)
        open_run(ctx)  # RECEIVED

        state = RunState.RECEIVED
        run_timeout = max(30, self.stage1_sec)
        try:
            state = self._to(ctx.run_id, state, RunState.PREFLIGHTED)
            ttl_ms = (self.deadline_sec + 60) * 1000
            with self._attach(f"agent:main:orchestrator-{ctx.run_id}", ttl_ms=ttl_ms) as ad:
                # 🔴 批 D-II：冻结一次，Stage 1 之前完成。market/sector/technical 都
                #    从这一份读（各带 --evidence-set-id），不再各自联网抓日线 ——
                #    「所有 Specialist 看同一份数据」从「机制可行」变成「这次真的如此」。
                #    freeze 失败（抓不到）⇒ 异常上抛 ⇒ 整体 FAILED（fail-closed：宁可
                #    不出卡，也不退回各自抓一份、悄悄丢掉共享保证）。
                esid = self._snapshot.freeze_index_daily(
                    did, SNAPSHOT_SYMBOLS, bars=SNAPSHOT_BARS)
                # evidence_set_id 从批 B 起就在 RunContext 里、一直是 None ——
                # 这一批第一次真的填它（in-memory；decision_runs 是只追加、RECEIVED
                # 时已写入 None，所以持久记录落在这次转移的 detail + evidence_sets 表）。
                ctx = dataclasses.replace(ctx, evidence_set_id=esid)
                state = self._to(ctx.run_id, state, RunState.SNAPSHOT_FROZEN,
                                 detail={"evidence_set_id": esid,
                                         "symbols": list(SNAPSHOT_SYMBOLS),
                                         "bars": SNAPSHOT_BARS})

                # ── Stage 1：并行 fan-out ──
                state = self._to(ctx.run_id, state, RunState.STAGE1_RUNNING)
                gid = f"g-{ctx.run_id[:8]}-s1"
                handles = [ad.start(a, did, self._specialist_task(a, did, esid),
                                    group_id=gid, run_timeout_sec=run_timeout)
                           for a in STAGE1_AGENTS]
                r1 = ad.wait(handles, self.stage1_sec)
                state = self._to(ctx.run_id, state, RunState.STAGE1_COMPLETED,
                                 detail=self._stage_detail(r1))

                # ── Stage 2：制衡层 risk（读 Stage 1 冻结证据）──
                stage1_ids = latest_verdict_ids(did)
                s1_refs = [stage1_ids[a] for a in STAGE1_AGENTS if a in stage1_ids]
                state = self._to(ctx.run_id, state, RunState.RISK_RUNNING)
                gid_r = f"g-{ctx.run_id[:8]}-risk"
                rh = ad.start(RISK_AGENT, did, self._risk_task(did, s1_refs),
                              group_id=gid_r, run_timeout_sec=max(30, self.risk_sec))
                r2 = ad.wait([rh], self.risk_sec)

                # ── Stage 3：判官给综合判断，程序组装 Card ──
                state = self._to(ctx.run_id, state, RunState.SYNTHESIZING,
                                 detail=self._stage_detail(r2))
                all_ids = latest_verdict_ids(did)
                ordered = [all_ids[a] for a in (*STAGE1_AGENTS, RISK_AGENT) if a in all_ids]
                if not ordered:
                    raise OrchestratorError(
                        "零证据：Stage 1/2 没有任何 agent 落下判定原件，无从合成。")
                verdicts, refs = card_ops.load_verdicts_and_refs(ordered)
                judgment = self._judge(ad, did, verdicts, present=set(all_ids))

                card = card_ops.synthesize(
                    decision_id=did, verdicts=verdicts, judgment=judgment,
                    model_ref=self._model_ref, verdict_refs=refs)
                state = self._to(ctx.run_id, state, RunState.CARD_PERSISTED,
                                 detail={"record": "persisting"})
                card_ops.persist(card)
                self._to(ctx.run_id, state, RunState.COMPLETED)
                return card
        except OrchestratorError:
            self._fail(ctx.run_id, state, RunState.FAILED)
            raise
        except Exception as e:  # noqa: BLE001
            self._fail(ctx.run_id, state, RunState.FAILED, reason=str(e))
            raise OrchestratorError(f"编排在 {state} 失败：{e}") from e

    # ── 判官（Stage 3 的 judgment）────────────────────────────────────────
    def _judge(self, ad, did: str, verdicts, *, present: set[str]) -> "card_ops.Judgment":
        """spawn synthesizer 判官，拿回 status/headline/synthesis，程序组装成 Judgment。

        🔴 数据不经判官搬运：Card 的证据来自 verdict_refs（程序从库里读），判官只
        产出**判断**。缺席 agent 的 missing 由程序算（缺席是完整性事实，不是判断）。
        """
        gid = f"g-{did[-3:]}-synth"
        task = self._synth_task(did, verdicts)
        sh = ad.start(SYNTHESIZER_AGENT, did, task, group_id=gid,
                      run_timeout_sec=max(30, self.synth_sec),
                      output_schema=JUDGMENT_SCHEMA)
        [sres] = ad.wait([sh], self.synth_sec)
        j = sres.structured if isinstance(sres.structured, dict) else None
        if sres.status != SpawnStatus.SUCCEEDED or not j \
                or not all(k in j for k in ("status", "headline", "synthesis")):
            raise OrchestratorError(
                f"判官没给出可用的综合判断（status={sres.status}，structured={j!r}）。"
                f"没有判断就没有 Card —— 不静默编一个。")

        # 缺席 agent → 缺失项（程序算，不是判官报）。缺席是完整性事实。
        absent = [a for a in (*STAGE1_AGENTS, RISK_AGENT) if a not in present]
        extra = [MissingItem(f"{a} 未返回结果", "supervisor.agent_no_response")
                 for a in absent]
        return card_ops.Judgment(status=j["status"], headline=j["headline"],
                                 synthesis=j["synthesis"], extra_missing=extra)

    # ── 转移的薄封装（把 state 往前推并返回新 state）──────────────────────
    def _to(self, run_id: str, frm: str, to: str, *, detail: dict | None = None) -> str:
        transition(run_id, frm, to, detail=detail)
        return to

    def _fail(self, run_id: str, frm: str, term: str, *, reason: str | None = None) -> None:
        # best-effort：已经在异常路径上了，转移再失败也不该盖住原始异常。
        try:
            transition(run_id, frm, term,
                       detail={"reason": reason} if reason else None)
        except Exception:  # noqa: BLE001
            pass

    # ── 任务文本（给 Specialist / risk / 判官的指令）──────────────────────
    def _specialist_task(self, agent: str, did: str, evidence_set_id: str) -> str:
        # 🔴 绝不在指令里出现日期（ORCHESTRATION.md 反复踩过）——走宽松模式，
        #    取数据源最近一个交易日，并让它写出是哪天。
        base = (
            f"本次决策编号 {did}。请给出当前市场状态/情绪的事实与判断。\n"
            f"不要指定日期，走宽松模式，取数据源给出的最近一个交易日，"
            f"并在回答里明确写出那是哪一天。\n"
            f"跑你的 skill 时必须加 --task-id {did}。")
        # 🔴 读冻结日线的三个 Specialist 必须再带 --evidence-set-id —— 与 --task-id
        #    同一种机制（提示词说要做什么，跑完靠代码/探针核实真做了）。emotion/news
        #    的 skill 没有这个参数，不加（加了它们也不认）。
        if agent in SNAPSHOT_INDEX_AGENTS:
            base += (
                f"\n本次决策的指数日线已冻结（evidence_set_id={evidence_set_id}）。"
                f"跑 skill 时必须再加 --evidence-set-id {evidence_set_id} —— "
                f"读这份冻结快照，不要自己联网抓日线，这样所有 Specialist 看的是同一份。")
        return base

    def _risk_task(self, did: str, s1_ids: list[int]) -> str:
        ids = ",".join(str(i) for i in s1_ids)
        return (
            f"请依据 Stage 1 的冻结证据做风险审查。\n"
            f"本次决策编号 {did}，跑 skill 时必须加 --task-id {did}。\n"
            f"Stage 1 的 verdict_ref：{ids}\n"
            f"不要自己重新采集数据。")

    def _synth_task(self, did: str, verdicts) -> str:
        return (
            f"你是综合判官。下面是本次决策（{did}）已冻结的各 agent 判定摘要。\n"
            f"请只做一件事：权衡它们，给出这张 Decision Card 的\n"
            f"  status（BUY/WAIT/AVOID/BLOCK）、headline（核心矛盾一句话）、"
            f"synthesis（两三句理由，只能引用下面出现过的事实）。\n\n"
            f"🔴 硬约束：risk 若「否决」，status 只能 AVOID 或 BLOCK；"
            f"risk「无法判定」则不许 BUY。\n"
            f"不要采集数据、不要跑任何 skill、不要 spawn 任何 agent —— "
            f"你没有这些能力，也不需要。按结构返回三样即可。\n\n"
            f"── 冻结判定摘要 ──\n{self._summarize(verdicts)}")

    @staticmethod
    def _summarize(verdicts) -> str:
        lines = []
        for v in verdicts:
            res = ", ".join(f"{k}={val}" for k, val in list(v.result.items())[:6])
            miss = "；".join(str(m) for m in v.missing) or "无"
            lines.append(
                f"[{v.agent}] verdict={v.verdict} stance={v.stance or '—'}\n"
                f"    结论: {res}\n    缺失: {miss}")
        return "\n".join(lines)

    @staticmethod
    def _stage_detail(results) -> dict:
        """把一批 SpawnResult 的 usage/状态收进转移 detail（批 C-I 的 token 用量落点）。

        🔴 usage 只落 `run_events.detail`，不落 `agent_runs`（v2 删过 token 列，
        唯一真相源是运行时 trajectory；这里留一条供排查，不建第二套账）。
        """
        return {
            "agents": {r.handle.agent: r.status for r in results},
            "usage": {r.handle.agent: r.usage for r in results if r.usage},
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="程序驱动出卡（DecisionOrchestrator）")
    ap.add_argument("--origin", default="cli", choices=["cli", "feishu", "cron"])
    ap.add_argument("--trigger-id", default=None)
    ap.add_argument("--model-ref", default="anthropic/claude-sonnet-5")
    ap.add_argument("--json", action="store_true", help="输出 Card 的 JSON 而非文本卡")
    a = ap.parse_args(argv)

    # 🔴 ownership 守卫 —— L-14 边界的最后一段（评审阻塞项 1）。
    #    这个类头部写着「main 没有一条工具调用能到达它」，但 `main` 继承了运行时的
    #    shell 工具，能 `exec python3 orchestrator.py` **绕过 bin/biga-card 的守卫**
    #    直接启动。那句中心断言当前是假的 —— 而关掉 L-14 正是 C-II 存在的理由。
    #    ⇒ 在这里再判一次发起方血缘（纯函数 entry_guard.classify_caller）：agent 会话
    #    血缘里有运行时标记 → 拒绝；人/cron 触发没有 → 放行。这样「够不到」在 exec
    #    面也成立，不只在 agent-spawn 面。bin/biga-card 仍有自己那道，这是纵深防御两层。
    kind, why = entry_guard.classify_caller()
    if kind == entry_guard.AGENT:
        print(f"🔴 orchestrator.py 不能从 agent 会话里启动（{why}）。", file=sys.stderr)
        print("   出卡由人 / cron 经 bin/biga-card 触发 —— agent 没有一条路能启动它。",
              file=sys.stderr)
        print("   若你收到「请出一张决策卡，严格按以下步骤」那段：**你不是执行者**，"
              "别自己编排、别跑这个脚本（见 AGENTS.md）。", file=sys.stderr)
        return 3
    if kind == entry_guard.UNKNOWN:
        print(f"🔶 发起方判不了（{why}）—— 放行，依赖 bin/biga-card 的锁与预算兜底。",
              file=sys.stderr)

    ctx = new_run_context(origin=a.origin, non_interactive=True,
                          trigger_id=a.trigger_id)
    print(f"run {ctx.run_id}   （查进度：bin/biga-card --status {ctx.run_id}）",
          file=sys.stderr)
    orch = DecisionOrchestrator(model_ref=a.model_ref)
    try:
        card = orch.run(ctx)
    except OrchestratorError as e:
        print(f"🔴 出卡失败：{e}", file=sys.stderr)
        print(f"   死在哪一步：bin/biga-card --status {ctx.run_id}", file=sys.stderr)
        return 4
    print(json.dumps(card.to_dict(), ensure_ascii=False, indent=2) if a.json
          else card.render())
    print(f"已落库 decision_id={card.decision_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
