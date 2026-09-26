"""运行时契约（`AGENTS.md` / `SOUL.md` / `IDENTITY.md`）的卫生检查。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：契约里有没有写进**会过期的事实**（当前只查 provider 名字）
- **不覆盖**：契约写得好不好、词表对不对（那在各 agent 自己的测试里）、
  `CLAUDE.md`（**它不是运行时文件** —— 只有开发时的工具链读它）

🔴 为什么运行时契约要单独管
---------------------------
`AGENTS.md` 是**运行时**加载的：

    飞书 → gateway → main（读根 AGENTS.md + SOUL.md + IDENTITY.md）
                      ↓ spawn
                    specialist（只读各自 agents/<id>/AGENTS.md）

写进去的东西 agent **会当真，而且不会去核对**。
所以契约里只能放**不变的**东西；本次运行的具体参数由编排器在任务消息里注入
（`_specialist_task` / `_risk_task` 就是干这个的）—— 那份是每次都新鲜的。
"""
from __future__ import annotations

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────────────────────────────

#: 🔴 `AGENTS.md` 是**运行时**加载的（`CLAUDE.md` 不是 —— 那只有开发时用）。
#:    写进去的东西 agent 会当真，而它**不会去核对**。
#:
#:    ⇒ 易变的事实不许写进契约。provider 是谁尤其不能写：
#:      换源、降级都不经过 agent，而契约里那个名字会在某次换源之后
#:      **悄悄变成假话**。
#:
#:    实测（2026-09-26）：`agents/news/AGENTS.md` 里一度写着
#:    「主源（财联社）给每条电报一个档位」—— 就是这个形状。
#:    agent 需要知道的是「这次有没有」，判据在 `missing[]` 里，
#:    **那是每次运行都新鲜的**。
#:
#:    ⚠️ 这也是 P3-6 的边界：specialist 不许碰 provider 模块，
#:      自然也不该在契约里认识 provider 的名字。
_PROVIDER_WORDS = ("财联社", "新浪", "东财", "东方财富", "腾讯", "通达信",
                   "eastmoney", "sina_", "cls_news", "tencent")

_CONTRACTS = sorted((REPO / "agents").glob("*/AGENTS.md")) + [
    REPO / "AGENTS.md", REPO / "SOUL.md", REPO / "IDENTITY.md"]


def test_找到了运行时契约文件():
    """🔴 探针的探针：清单空了它也会「全绿」。"""
    present = [p for p in _CONTRACTS if p.exists()]
    assert len(present) >= 8, [p.name for p in present]


@pytest.mark.parametrize("path", _CONTRACTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_运行时契约里不出现provider名字(path: pathlib.Path):
    if not path.exists():
        pytest.skip(f"{path} 不存在")
    text = path.read_text(encoding="utf-8")
    hits = sorted({w for w in _PROVIDER_WORDS if w in text})
    assert not hits, (
        f"{path.relative_to(REPO)} 里出现了 provider 名字：{hits}\n"
        "  🔴 换源、降级都不经过 agent，而契约里那个名字会在某次换源之后\n"
        "     **悄悄变成假话** —— 而 agent 不会去核对它。\n"
        "  ⇒ 改成说「有的源给、有的不给」，判据指向 `missing[]` 里的那条\n"
        "     （每次运行都新鲜）。provider 是谁是数据层的事（P3-6 边界）。"
    )
