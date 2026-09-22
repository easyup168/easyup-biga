"""结构性一致性检查的**唯一**实现 —— 替代「手抄一份清单，靠记性同步」。

为什么要抽出来
--------------
外部评审（`docs/external/2026-09-21-adversarial-review-phase2.md` 第六节）
点名的模式：`STANCE_VOCAB` 与已建好的 agent 名单、`tools.agentToAgent.allow`
与 `subagents.allowAgents`、`decision_ids` 要不要 append-only 保护……
每一次都是同一个形状——**一个事实在别处已经有权威答案，这里却又手抄了一份**，
而手抄的那份不会随权威源变化自动更新。

F1（schema 差集）、F9（glob 扫目录）、F12（AST 禁令扫描）各自独立解决过
一次这个形状——做对了，但做了三遍，且下一次遇到同样的形状大概率还会
再发明第四种写法。这个模块把「比对」那半步（`claimed` 和 `source` 是否
一致、不一致该说什么）抽出来复用；「权威源是什么、清单从哪读」仍然由
调用方决定——这部分本来就无法通用化，勉强抽象只会换来一层更难读的间接。

复查（第七节）验证过的教训：F8/F10/F16 修好了当时报出的那个具体实例，
但都停在「手写一个新分支/新条目」，没有换成这里的模式——所以模式还在，
下一个新登记的东西照样可能被漏掉。这个模块存在的意义就是让"下一次"
的成本从"重新想一遍怎么写"降到"调用这两个函数之一"。
"""
from __future__ import annotations

import pathlib

from _contract import SYNTHESIZER_AGENT

REPO = pathlib.Path(__file__).resolve().parents[1]


#: **非 Specialist 的已建 agent** —— 有 `agents/<name>/` 目录、要进配置白名单，
#: 但**不产出 AgentVerdict/stance**，所以不进 `STANCE_VOCAB`、不受「每个 stance 词
#: 都要在契约里出现」这类 specialist 专属检查约束。
#:
#: 首个成员：`synthesizer`（确定性编排批 C-II 的综合判官）——它读冻结 verdict、
#: 产出 Card 的 status/headline/synthesis，不是一个领域 Specialist。
#: 名字单点定义在 `_contract.SYNTHESIZER_AGENT`，这里引用它，不手抄一份。
#:
#: 🔴 为什么要显式列出来，而不是「目录存在就当 specialist」：
#: `built_agents()` 原本把「有目录」直接等同于「是 specialist」，靠的是当时
#: 目录里恰好只有 specialist。加一个非 specialist 的 agent 就打破这个隐含前提 ——
#: 与其让 `STANCE_VOCAB == built_agents()` 静默要求 synthesizer 也有 stance 词表
#: （它没有），不如把「哪些是 support 类」这件事**显式登记**在这里。
SUPPORT_AGENTS: frozenset[str] = frozenset({SYNTHESIZER_AGENT})


def built_agents() -> set[str]:
    """**全部**已建 agent —— 有 `agents/<name>/` 目录就算建了（含 Specialist 与
    support 类如 `synthesizer`）。

    这是本仓库里"agent 名册"唯一的权威源：目录本身。需要"配置白名单是否覆盖了
    所有已建 agent"用它（配置要覆盖 specialist **和** support，否则 orchestrator
    spawn 不到 synthesizer）。需要"哪些是产出 stance 的 specialist"用
    `built_specialists()`。

    `discipline` 推到 Phase 3（裁定 13），不该出现在任何名册里——
    调用方如需处理这个例外，自己在结果上减掉，不在这里特判。
    """
    return {p.name for p in (REPO / "agents").iterdir() if p.is_dir()}


def built_specialists() -> set[str]:
    """已建的 **Specialist**（产出 AgentVerdict/stance 的）—— `built_agents()` 减去
    `SUPPORT_AGENTS`。

    `STANCE_VOCAB` 该与这个相等（每个 specialist 一套 stance 词表），
    而不是与 `built_agents()` 相等 —— 后者含 synthesizer 这类不产出 stance 的。
    """
    return built_agents() - SUPPORT_AGENTS


def assert_matches_source(
    claimed: set[str], source: set[str], *, what: str, fix_hint: str = "",
) -> None:
    """断言 `claimed` 与权威源 `source` **完全相等**（无遗漏也无多余）。

    用于"这份清单理应和权威源一一对应"的场景，比如
    `STANCE_VOCAB` 的 key 集合理应等于已建好的 agent 名单。
    """
    missing = source - claimed
    extra = claimed - source
    if not missing and not extra:
        return
    lines = [f"{what}：与权威源不一致。"]
    if missing:
        lines.append(f"  权威源有、清单没有：{sorted(missing)}")
    if extra:
        lines.append(f"  清单有、权威源没有（多半是改名/下线后没清理）：{sorted(extra)}")
    if fix_hint:
        lines.append(f"  {fix_hint}")
    raise AssertionError("\n".join(lines))


def assert_subset_of_source(
    claimed: set[str], source: set[str], *, what: str, fix_hint: str = "",
) -> None:
    """断言 `claimed` ⊆ `source`——用于"清单不能超出权威范围"这类单向检查。

    与 `assert_matches_source` 的区别：不关心权威源里有、清单里没有的部分
    （那可能是有意的子集，比如 main 的白名单没有必要覆盖尚未开放的 agent），
    只挡"清单里凭空多出来的东西，而权威源根本不认"。
    """
    extra = claimed - source
    if not extra:
        return
    lines = [f"{what}：清单里有权威源不认的东西：{sorted(extra)}"]
    if fix_hint:
        lines.append(f"  {fix_hint}")
    raise AssertionError("\n".join(lines))
