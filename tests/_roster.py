"""测试共用：给「本文件不测 roster」的卡造出**对得上号**的缺席登记（批 P）。

为什么需要它
------------
批 P 之前 `DecisionCard._check_roster` 只比条数，于是十一个测试文件里约 25 处写着

    missing=[f"占位{i}——本文件不测 roster" for i in range(5)]

——纯粹为了把条数凑够。批 P 把判据换成「每个缺席的 agent 各有一条自己的登记」
（代码形如 `supervisor.<agent>.agent_no_response`）之后，这种凑数的占位项**正是那道
检查要拦的东西**：5 个 agent 缺席配 5 条毫不相干的 missing，实测能通过，而这就是
评审 §18 指出的洞。

⇒ 这些位置改成真的登记「缺了谁」。对测试本身是无损的（它们本来就不测 roster），
对读者是更诚实的：卡上写的缺席，就是真的缺席的那几个。
"""

from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[1]

from _contract import EXPECTED_ROSTER, MissingItem, absent_agent_missing  # noqa: E402

__all__ = ["absent_registrations", "DEFAULT_ROSTER"]

#: 默认期望名单 —— 与 `DecisionCard.absent_agents` 在 `expected_roster is None`
#: 时的回退口径同源（Registry 派生），不手抄第二份。
DEFAULT_ROSTER: tuple[str, ...] = tuple(sorted(EXPECTED_ROSTER))


def absent_registrations(present, roster=None) -> list[MissingItem]:
    """`roster` 里没出现在 `present` 的那些 agent，各造一条缺席登记。"""
    present = {getattr(p, "agent", p) for p in present}
    names = tuple(roster) if roster is not None else DEFAULT_ROSTER
    return [absent_agent_missing(a) for a in names if a not in present]
