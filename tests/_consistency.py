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

REPO = pathlib.Path(__file__).resolve().parents[1]


def built_agents() -> set[str]:
    """已经建好的 Specialist —— 有 `agents/<name>/` 目录就算建了。

    这是本仓库里"agent 名册"唯一的权威源：目录本身。任何测试需要
    "当前有哪些 agent"时都应该调用这个函数，而不是各自维护一份名单
    （那正是 F8/F10 反复踩的坑）。

    `discipline` 推到 Phase 3（裁定 13），不该出现在任何名册里——
    调用方如需处理这个例外，自己在结果上减掉，不在这里特判。
    """
    return {p.name for p in (REPO / "agents").iterdir() if p.is_dir()}


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
