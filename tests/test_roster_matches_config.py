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
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills"))

from _contract import STAGE1_AGENTS, STAGE2_AGENTS  # noqa: E402

CONFIG = pathlib.Path.home() / ".openclaw-biga" / "openclaw.json"


def _built() -> set[str]:
    """已经建好的 Specialist —— 有 workspace 目录就算建了。

    `discipline` 推到 Phase 3（裁定 13），不该出现在任何名册里。
    """
    return {p.name for p in (REPO / "agents").iterdir() if p.is_dir()}


@pytest.mark.skipif(not CONFIG.exists(), reason="本机没有 BigA 运行时配置")
class TestRosterConsistency:
    @staticmethod
    def _cfg() -> dict:
        return json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_已建的specialist都注册了(self):
        entries = set((self._cfg().get("agents") or {}).get("entries") or {})
        missing = _built() - entries
        assert not missing, (
            f"这些 agent 有 workspace 却没注册进 agents.entries：{sorted(missing)}\n"
            "  后果：spawn 时找不到它")

    def test_已建的specialist都在main的白名单里(self):
        """🔴 本文件存在的理由。"""
        main = ((self._cfg().get("agents") or {}).get("entries") or {}).get("main", {})
        allow = (main.get("subagents") or {}).get("allowAgents")
        if allow is None:
            return                      # 没有白名单 = 不限制，也行
        missing = _built() - set(allow)
        assert not missing, (
            f"这些 agent 建好了却不在 main.subagents.allowAgents 里："
            f"{sorted(missing)}\n"
            "  后果：Supervisor 照契约 spawn 它会被拒，而且**不报错** ——\n"
            "        Card 照常产出，只是少了一个领域。\n"
            "  怎么办：把它加进 ~/.openclaw-biga/openclaw.json 的该数组")

    def test_两处名册必须一致(self):
        """🔴 外部评审 F10：**姊妹配置已经出过事故，另一半至今没人管。**

        上面那条守卫是为 `allowAgents` 漏了 news 那次事故专门写的。
        但 `tools.agentToAgent.allow` 是**同一个形状**的第二处名册 ——
        全仓没有任何测试碰过它（grep 不到一处引用），实测它确实少了 news。

        > 清单类约定第一次踩坑后，团队本能地去补**那一个**，
        > 而不去问「还有哪些地方是同样的结构」。—— 评审的原话。

        ⚠️ 这类 json 配置**不受版本控制**，漂移天然不出现在 git diff 里
        被 review 到 —— 所以只能靠测试对账。
        """
        cfg = self._cfg()
        main = ((cfg.get("agents") or {}).get("entries") or {}).get("main", {})
        allow_agents = set((main.get("subagents") or {}).get("allowAgents") or [])
        a2a = set(((cfg.get("tools") or {}).get("agentToAgent") or {}).get("allow") or [])
        if not allow_agents or not a2a:
            pytest.skip("有一处没设白名单 = 不限制，无从对账")
        missing = allow_agents - a2a
        assert not missing, (
            f"这些 agent 在 allowAgents 里，却不在 tools.agentToAgent.allow："
            f"{sorted(missing)}\n"
            "  两处名册各写一遍，改一处忘另一处 —— 与 2026-09-21 10:37 那次同型。\n"
            "  怎么办：把它加进 ~/.openclaw-biga/openclaw.json 的该数组")

    def test_契约里的stage名单都建好了(self):
        """反方向：契约说有，实际没建。

        `discipline` 是唯一允许的例外（裁定 13：没有输入源）。
        """
        declared = set(STAGE1_AGENTS) | set(STAGE2_AGENTS)
        unbuilt = declared - _built() - {"discipline"}
        assert not unbuilt, (
            f"契约里的 {sorted(unbuilt)} 还没建 —— "
            "risk 会把它算进覆盖率分母，拉低每一张卡的可信度")
