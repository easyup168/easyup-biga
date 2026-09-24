"""契约里的 Agent 名册 vs 运行时配置的白名单 —— 必须一致。

🔴 一次真实的静默失败（2026-09-21 10:37）
------------------------------------------
2.4 把 `news` 写进了 Supervisor 契约的 Stage 1 spawn 列表，
也建好了 agent、注册进了 `agents.entries`。端到端跑下来：

    10:37:18  technical / market / emotion / sector   ← 只有四个
                                                       ← news 没被 spawn

原因是 `agents.entries.main.subagents.allowAgents` 是一条**显式白名单**，
里面没有 `news`。契约说 spawn 五个、配置只允许四个，
**中间没有任何报错** —— Card 照常产出，只是少了一个领域。

而 `risk` 会如实报「Stage 1 缺席：news」⇒ 看起来像 news agent 没建好，
排查方向天生是错的。

⇒ 名册这种「两处各写一遍」的东西，必须有人对账。

🔴 外部评审 F10：上面这条守卫只补了 `allowAgents`。`tools.agentToAgent.allow`
是同一个形状的第二处名册，当时全仓没有任何测试碰过它，实测它也确实少了 news。
复查又指出：把这两处各写一条测试，只是把「漏一个」变成「漏两个都补上了，
但漏第三个」——**清单式检查只加固了具体报出的那几个字段**。

⇒ 改成数据驱动：`_MUST_COVER_BUILT` 登记"所有理应覆盖已建好 agent 的配置
字段"，新增第三个这样的字段时，在字典里加一行就够了，不需要再写一条新测试
方法；`test_每个字段都覆盖已建好的agent` 会自动把它纳入。

⚠️ 这一版删掉了一条曾经写出来又删掉的测试，如实记一笔：曾经想额外加一条
"这几个字段必须彼此完全相等"，实测直接报错——`agents.entries` 天然比
`allowAgents`/`agentToAgent.allow` 多一个 `"main"` 自己（entries 回答的是
"谁被注册了"，另外两个回答的是"main 能碰到谁"，main 不会把自己列进
自己能 spawn 的对象里）。这不是 bug，是三个字段本来就不是同一个问题的
三份答案。而"每个字段都必须覆盖已建好的 agent"这条本身已经完整复现了
F10 要防的事故（`news` 是已建好的 agent，任何一个字段漏了它都会被
直接抓到）——不需要再加一条"彼此相等"的检查去做同一件事，还做错了方向。
"""

from __future__ import annotations

import json
import pathlib
import warnings
from typing import Callable

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402
from _consistency import assert_subset_of_source, built_agents  # noqa: E402

CONFIG = pathlib.Path.home() / ".openclaw-biga" / "openclaw.json"


def _entries(cfg: dict) -> set[str] | None:
    entries = (cfg.get("agents") or {}).get("entries") or {}
    return set(entries) or None


def _allow_agents(cfg: dict) -> set[str] | None:
    main = ((cfg.get("agents") or {}).get("entries") or {}).get("main", {})
    allow = (main.get("subagents") or {}).get("allowAgents")
    return None if allow is None else set(allow)


def _agent_to_agent_allow(cfg: dict) -> set[str] | None:
    allow = ((cfg.get("tools") or {}).get("agentToAgent") or {}).get("allow")
    return None if allow is None else set(allow)


#: 🔴 运行时配置里，所有"理应覆盖已建好 agent 名单"的字段。
#: 加第三个这样的字段（同一个形状的第三处名册）时，在这里加一行 ——
#: 不需要再写一条新测试方法，下面这条测试会自动把它纳入。
_MUST_COVER_BUILT: dict[str, Callable[[dict], set[str] | None]] = {
    "agents.entries": _entries,
    "agents.entries.main.subagents.allowAgents": _allow_agents,
    "tools.agentToAgent.allow": _agent_to_agent_allow,
}


class RuntimeConfigUnavailable(UserWarning):
    """本机没有 BigA 运行时配置 ⇒「配置白名单 vs 已建 agent」这半边无法在此核对。

    🔴 批 K：这是 R-3「算不出来要显式说」的一个 test 形态。它是一个**可见**的
    信号（`-q` 的 warnings summary 也会列出来），不是静默 skip。
    """


def _require_runtime_config() -> dict:
    """读运行时配置；没有就发一条**可见**的 UNKNOWN 警告再 skip。

    🔴 批 K —— 修 `test_roster_matches_config.py` 的 `skipif` 缺口。

    原来整个 `TestRosterConsistency` 挂 `@pytest.mark.skipif(not CONFIG.exists())`：
    fresh clone / CI 上**静默跳过整组**——连**根本不需要配置**的
    `test_契约里的stage名单都建好了`（契约名单 vs 已建 agent）都被一起跳过了。
    这正是 R-3 想防的形状：算不出来（配置不在）不该悄悄变成「没查出问题」。

    改法（范围有意收窄，见 CHANGELOG）：
      · 不需要配置的那半边检查**照常跑**——CI 上也拦得住「契约里声明了但没建」的漂移；
      · 需要配置的那半边（配置白名单 vs 已建 agent）在配置缺席时发一条
        `RuntimeConfigUnavailable` 警告再 skip。skip 是诚实的「这台机器上没有那个
        **外部**产物」（配置在仓库外、人工维护，CI 上本就不该有），警告让它不再静默。
    """
    if not CONFIG.exists():
        warnings.warn(
            RuntimeConfigUnavailable(
                f"没有 {CONFIG} —— 「运行时配置白名单 vs 已建 agent」核对不了，跳过这半边。"
                f"（契约名单 vs 已建 agent 那半边不需要它、照常跑。）"),
            stacklevel=2)
        pytest.skip(f"本机没有 BigA 运行时配置（{CONFIG}）——已发 RuntimeConfigUnavailable 警告")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


class TestRosterConsistency:
    @pytest.mark.parametrize("field", sorted(_MUST_COVER_BUILT))
    def test_每个字段都覆盖已建好的agent(self, field):
        """🔴 本文件存在的理由，现在对**全部**登记字段都成立，不只是某一个。

        需要运行时配置；缺席时 `_require_runtime_config()` 发可见警告再 skip（批 K）。
        """
        claimed = _MUST_COVER_BUILT[field](_require_runtime_config())
        if claimed is None:
            pytest.skip(f"{field} 没设 = 不限制，也行")
        assert_subset_of_source(
            built_agents(), claimed,
            what=f"已建好的 agent vs {field}",
            fix_hint=(
                "后果：Supervisor 照契约 spawn 它会被拒，而且**不报错**——"
                "Card 照常产出，只是少了一个领域。\n"
                f"  怎么办：把它加进 ~/.openclaw-biga/openclaw.json 的 {field}"))

    def test_契约里的stage名单都建好了(self):
        """反方向：契约说有，实际没建。

        `discipline` 是唯一允许的例外（裁定 13：没有输入源）。
        """
        declared = (set(STAGE1_AGENTS) | set(STAGE2_AGENTS)) - {"discipline"}
        assert_subset_of_source(
            declared, built_agents(),
            what="契约里声明的 stage 名单 vs 已建好的 agent",
            fix_hint="risk 会把没建好的 agent 算进覆盖率分母，拉低每一张卡的可信度")
