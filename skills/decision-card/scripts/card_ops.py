"""Decision Card 的合成、落库、渲染 —— **在线与回放共用的唯一一份实现**。

架构要求：

> 回放必须与在线路径共用同一份合成代码，不许另写一套。

原因很直接：回放的用途是「换个模型重跑，看结论会不会变」。
如果回放用的是另一份组装代码，那你看到的差异里就混进了**代码差异**，
而你本来只想看**模型差异**。这个实验就废了。

职责边界
--------
本模块做的是**组装**：把 Verdict 聚合成 Card、校验契约、落库、渲染。
**判断**（status / headline / synthesis）由 LLM 给，作为 `Judgment` 传进来。

换句话说：**代码负责「怎么拼」，模型负责「拼出什么结论」。**
共用的是前者。
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field as dc_field

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    CONTRACT_VERSION,
    AgentVerdict,
    CardStatus,
    DecisionCard,
    MissingItem,
    VerdictRef,
    card_event_type,
)
from _store import (  # noqa: E402
    load_online_card,
    load_verdict,
    load_verdict_meta,
    record_verdict_run,
    save_card,
    save_card_with_notifications,
)

__all__ = ["Judgment", "synthesize", "persist", "load_verdicts_and_refs",
           "SYNTHESIS_VERSION"]


def load_verdicts_and_refs(
    verdict_ids: list[int],
) -> tuple[list[AgentVerdict], list[VerdictRef]]:
    """按 `verdict_id` 取回原件并配套构建 `VerdictRef` —— 在线合成与
    DecisionOrchestrator **共用的一份**（dev-workflow 第五问：不手抄第二份）。

    🔴 `content_sha256` 取自 `agent_verdicts` 那一列**当时写入的值**（`load_verdict_meta`），
    不是把对象重新序列化再算 —— 见 A6/追加 4，重算会让 A-I 之前落库的行集体对不上。
    """
    verdicts: list[AgentVerdict] = []
    refs: list[VerdictRef] = []
    for vid in verdict_ids:
        v = load_verdict(vid)
        if v is None:
            raise ValueError(
                f"verdict_id={vid} 在 agent_verdicts 里不存在 —— "
                f"确认 Specialist 跑 skill 时没有加 --no-store（加了就不落原件）。")
        meta = load_verdict_meta(vid)
        verdicts.append(v)
        refs.append(VerdictRef(agent=v.agent, verdict_id=vid,
                               content_sha256=meta["content_sha256"],
                               contract_version=CONTRACT_VERSION,
                               # 🔴 批 J-I：从存量行的 run_id 列直接搬（历史行是 None）。
                               run_id=meta.get("run_id")))
    return verdicts, refs

#: 组装逻辑的版本。改了组装方式就要 +1，
#: 否则「回放结论变了」会分不清是模型变了还是代码变了。
SYNTHESIS_VERSION = "synth/1"


@dataclass(frozen=True)
class Judgment:
    """模型给出的判断部分。组装逻辑之外的一切都在这里。"""

    status: CardStatus
    headline: str
    synthesis: str = ""
    #: Supervisor 自己发现的缺失项（例如「risk agent 尚未上线，未经风险审查」）。
    #: 与各 Verdict 的 missing 合并后上 Card。
    extra_missing: list[MissingItem] = dc_field(default_factory=list)


def synthesize(
    *,
    decision_id: str,
    verdicts: list[AgentVerdict],
    judgment: Judgment,
    model_ref: str,
    elapsed_ms: int = 0,
    generated_at: str = "",
    historical: bool = False,
    verdict_refs: list[VerdictRef] | None = None,
    run_id: str | None = None,
    evidence_set_id: str | None = None,
    expected_roster: tuple[str, ...] | None = None,
) -> DecisionCard:
    """把 Verdict 组装成 Card。**纯函数，不碰 IO。**

    纯函数是「在线与回放一致」的前提：
    只要输入相同，两条路径的输出必然逐字节相同，不依赖当时的库状态或网络。

    缺失项的聚合规则：各 Verdict 的 `missing` 之并集 ∪ Supervisor 自己发现的。
    去重但**保持首次出现的顺序** —— 顺序稳定，回放的 diff 才是干净的。

    Args:
        historical: 🔴 **只有回放历史卡时才传 True。**

            契约层要求「卡上的每条判定都属于这张卡」（外部评审 P1-1）。
            但 Stage 0 统一占号是后加的，已落库 31 张里有 **20 张**
            的判定写着别的号。回放这些卡时重新合成会撞上那条约束 ——
            于是**历史再也读不出来**。

            传 True 让契约层降级为「记下来并显示在卡面上」。
            ⚠️ 它只影响**能不能构造**；`save_card()` 仍然无条件拒绝，
            所以「读一张旧卡再存回去」洗不白它。
        verdict_refs: 🔴 A6：这批 verdicts 各自落库时的 `VerdictRef`
            （agent / verdict_id / content_sha256 / contract_version）。
            与 `verdicts` 是平行的两份数据，不强制一一对应——
            旧调用方（例如 `_read_verdicts()` 读 JSON 文件的退路）
            没有 verdict_id 可用时留空即可，Card 只是少一份可核对的证据，
            不影响其余字段。核对走 `_store.verify_verdict_refs()`。
        evidence_set_id: 🔴 批 N：这次决策看的是哪一份被冻结的数据切片
            （`SnapshotCoordinator.freeze_index_daily` 铸的那个 id）。在线路径由
            编排器传 `ctx.evidence_set_id`；回放把原卡那份原样带过去，不重铸。
            没有它，「所有 Specialist 看同一份数据」在卡这一层无从核实
            （外部评审 §9）。
    """
    seen: set[str] = set()
    missing: list[MissingItem] = []
    for m in [m for v in verdicts for m in v.missing] + list(judgment.extra_missing):
        item = MissingItem.coerce(m)
        # 🔴 按「代码 + 文本」去重，不只按文本：两个 agent 报同一句话但代码不同，
        #    那是两件事（例如两个源各自不可用），合并会让统计少一条。
        key = f"{item.code}\x00{item}"
        if key not in seen:
            seen.add(key)
            missing.append(item)

    # 契约层会在这里拒绝：missing 非空却给 BUY、BLOCK 却给 BUY、缺失项没上浮……
    return DecisionCard(
        from_store=historical,
        decision_id=decision_id,
        status=judgment.status,
        headline=judgment.headline,
        verdicts=verdicts,
        synthesis=judgment.synthesis,
        model_ref=f"{model_ref} ({SYNTHESIS_VERSION})",
        missing=missing,
        generated_at=generated_at,
        elapsed_ms=elapsed_ms,
        input_verdict_refs=list(verdict_refs or []),
        run_id=run_id,
        evidence_set_id=evidence_set_id,
        # 🔴 批 K：在线路径把生成时的期望 roster 冻进卡（编排器传 EXPECTED_ROSTER）；
        #    回放把原卡冻结的那份**原样带过去**（replay.py），不重算 —— 老卡为 None。
        expected_roster=expected_roster,
    )


def persist(card: DecisionCard, *, replay_of: int | None = None,
            runtime_run_ids: Mapping[str, str] | None = None) -> int:
    """落库，返回 `record_id`。在线路径 `replay_of=None`，回放路径填原始 record_id。

    🔴 只在在线路径记账本（`agent_runs`，`record_verdict_run`）——回放不重新
    执行任何 agent，给回放记一遍「执行」是假账。这与 `synthesize()` 保持纯函数
    是同一个理由的另一半：`persist()` 才是 IO 边界，账本这类「这次真的跑过」
    的记录只能长在这里，不能长在纯函数里。

    `runtime_run_ids`（批 J-II，keyword-only、默认 None）：`agent → 运行时 spawn id`
    的映射，编排器从每个 Stage 1/risk 的 `SpawnResult.handle.runtime_run_id` 收来
    （Stage 3 的 synthesizer 不产 verdict，不进这个映射）。落进 `agent_runs.
    runtime_run_id`，`spawn_check.py` 据此做结构化 join。
    🔴 必须有默认值：`synthesize.py` 与几条测试也在调 `persist()`，它们没有这个
    映射；不给默认值会把不相干的调用一起弄红。回放路径整段不记账本，自然也不写它。
    🔴 批 F：提供这个映射时，它的 **key 集**同时是「本次真正被 spawn 的 agent」的权威
    名单 —— 只有名单里的 agent 记账本行。risk 在两种确定性早退里由编排器免费算出事实、
    未被 spawn，就不进映射、也不该有账本行（否则 L-8 幽灵行 + spawn_check 误判伪造）。

    ⚠️ 这是批 C-II 的一处回归修复：旧的 standalone `synthesize.py`（编排从
    main 的提示词驱动时期）在落库前调过这个账本；批 C-II 把合成逻辑挪进这个
    模块的 `synthesize()`/`persist()`，但当时没有把这一步一并搬过来——`agent_runs`
    从那天起再没被写过，`tools/verify/spawn_check.py` 因此永远「判不了」（它需要
    这张表有行才能跟运行时的 `subagent_runs` 交叉核对）。2026-09-22 第一次真实
    live 验证时才暴露（不是安全洞：把成功误判成失败，不是把失败误判成成功）。
    """
    if replay_of is None:
        for v in card.verdicts:
            # 🔴 批 F：runtime_run_ids 提供时，它的 key 集就是「本次真正被 spawn 的 agent」
            #    的权威名单 —— 只给这些 agent 记执行账本行（agent_runs）。risk 在两种
            #    确定性早退（证据跨决策污染 / 完全没有上游）里由编排器**免费**算出事实、
            #    根本没被 spawn：给它记一行「执行过」既是 L-8 幽灵账本行（记了没发生的事），
            #    又会让 spawn_check 把它误判成伪造（agent_runs 有行、运行时 subagent_runs
            #    没有 ⇒ forged）。⚠️ 判据是 key 在不在，不是 rr.get() 的值 —— 被 spawn 但
            #    没拿到 runtime_run_id 的 agent 是「key 在、值 None」，仍要记账。
            #    不提供 runtime_run_ids（synthesize.py / 测试 / 回放）时维持原样：给所有 verdict 记账。
            if runtime_run_ids is not None and v.agent not in runtime_run_ids:
                continue
            record_verdict_run(v, decision_id=card.decision_id,
                               started_at=card.generated_at,
                               finished_at=card.generated_at,
                               model=card.model_ref,
                               runtime_run_id=(runtime_run_ids or {}).get(v.agent),
                               # 🔴 批 N：账本行指回**我们自己**那次编排执行尝试。
                               #    取 card.run_id 而不是另传一个参数 —— 卡和账本必须
                               #    说同一次执行，两个入参就是两套口径的起点。
                               orchestration_run_id=card.run_id)
        # 🔴 批 G-I：在线路径出卡后，把这张卡分到一类外发通知并入队 —— **与 Card 落库
        #    同一个事务**（save_card_with_notifications）。要么卡和通知一起进库，要么
        #    一起回滚（探针 P1）。event_type 由 `_contract.card_event_type` 从卡本身推
        #    （risk 否决 / UNKNOWN·缺失 / 正常），幂等键 aggregate=decision_id（一个决策
        #    一张卡 ⇒ 一类事件至多入队一次）。payload 带决策号供 worker 投递（P4）。
        #    回放路径不入队：回放不重新执行、也不该重推一遍通知。
        notification = {
            "event_type": card_event_type(card),
            "aggregate": card.decision_id,
            "payload": {
                "decision_id": card.decision_id,
                "status": card.status,
                "headline": card.headline,
                "missing_count": len(card.missing),
                "run_id": card.run_id,
            },
        }
        return save_online_card(card, [notification])
    return save_replay_card(card, replay_of)


#: 在线 Card 与其回放副本之间允许不同的字段集合。
#:
#: 这些字段描述的是**这次执行**，不是**这个结论**：
#: - `generated_at`：回放时的时钟，与原卡不同
#: - `elapsed_ms`：回放速度与原始执行无关
#: - `run_id`：回放没有自己的 run_id（诚实置 None，见 replay.py）
#:
#: `comparable()` 只剥这些字段；`replay.py` 的 `--check` 读它来决定
#: 「什么变化算正常、什么算回放失真」。两处共用同一份集合（L-3 的正解）。
#:
#: ⚠️ **不要往这里加 `model_ref`。** v0.3.4 加过一次，理由是「换模型是合法的回放
#: 场景」—— 那个理由对**落库**成立，对 `--check` 不成立，而这个集合同时服务两者。
#: `model_ref` 里嵌着 `SYNTHESIS_VERSION`（见 `synthesize()`），剥掉它之后
#: `--check` 就再也看不见「合成版本已经变了」——而那正是它守的东西之一。
#: 落库侧的「换模型合法」由契约层的 `REPLAY_FROZEN_LINEAGE` 单独表达
#: （它不含 `model_ref`）：两个问题，两个集合，不再互相牵连。
ALLOWED_REPLAY_CHANGES: frozenset[str] = frozenset({
    "generated_at",
    "elapsed_ms",
    "run_id",
})

#: 回放血缘的判据**不在这里** —— 它在契约层 `_contract.REPLAY_FROZEN_LINEAGE`，
#: 由写边界（`_store.save_card` 的 replay 分支）在同一个写事务里执行。
#:
#: 🔴 第一版把它长在这个模块里，于是守卫只长在 **CLI 那条路**上：外部评审用低层
#: `_store.save_card(card, replay_of=…)` 直接写、换掉 `evidence_set_id`，照样落库
#: 成功。一条「只有走某个入口才生效」的安全规则，等于没有这条规则。
#: 这里只做**转发**，不再有第二份判据（L-3）。

def save_online_card(card: DecisionCard,
                     notifications: list[dict]) -> int:
    """在线路径：落库 Card + 入队通知，返回 record_id。"""
    return save_card_with_notifications(card, notifications, replay_of=None)


def save_replay_card(card: DecisionCard,
                     parent_record_id: int | None) -> int:
    """回放路径：落库 Card（不入队通知），返回 record_id。

    血缘守卫在写边界（`_store.save_card` 的 replay 分支），见上面那段注释。
    这里保留这个名字，是因为「在线」与「回放」是两个安全档位，调用点该看得出
    自己走的是哪一个 —— 但**档位不由这个名字决定**，由 `replay_of` 参数决定，
    而判据在库里。
    """
    return save_card(card, replay_of=parent_record_id)


def comparable(card: DecisionCard) -> dict:
    """剥掉「每次必然不同」的字段，用于比较两张 Card 是否等价。

    去掉 `ALLOWED_REPLAY_CHANGES` 里的字段 —— 它们描述的是**这次执行**，
    不是**这个结论**。拿它们比较会让任何两次回放都「不一致」，
    于是一致性检查就退化成永远报警，很快没人看（又一个被忽略的守卫）。

    🔴 批 J-I：`run_id` 属于同一类。回放**不是**原来那次执行尝试，它诚实地把
    `card.run_id` 记成 None（不捏造，见 `replay.py`）——而原卡带着它真实的 run_id。
    若不剥掉，一旦在线路径开始产出带 run_id 的卡，回放这些卡的 `--check` 就会因为
    「这次执行 ≠ 上次执行」而误报「组装不一致」，把一个正确的无损回放判成坏的。
    ⚠️ `input_verdict_refs` 里各 ref 自带的 `run_id` **不剥** —— 那是判定原件的血缘，
       回放照原样带过去（`verdict_refs=list(original.input_verdict_refs)`），两边相同，
       是要被核对的证据的一部分。
    """
    d = card.to_dict()
    for field in ALLOWED_REPLAY_CHANGES:
        d.pop(field, None)
    return d


def load_original(decision_id: str) -> DecisionCard | None:
    """取回在线路径存下的那张 Card。"""
    return load_online_card(decision_id)
