"""出卡编排只能有一份 —— L-3。批 C-II 之后那「一份」是**代码**，不是提示词。

背景（2026-09-21 19:31，飞书触发）
-----------------------------------
同一段编排步骤原来有两份：`bin/biga-card` 的提示词（100% 照做）与 `AGENTS.md`
的 Stage 0~3（内容一样，飞书路径下没被执行）。从飞书说「出一张决策卡片吧」的
结果是 4/5 个 spawn、`sessions_yield`（契约禁止）、占号发生在 spawn 之后，
约 $0.4 白花 —— **而这些规则在契约里一条不缺**。于是先合并成
`ORCHESTRATION.md` 一份提示词，`bin/biga-card` 从标记块里抽。

批 C-II：编排从「喂给 LLM 的提示词」变成**程序**
----------------------------------------------
`bin/biga-card` 不再抽提示词喂给 `main`，而是直接跑
`skills/decision-card/scripts/orchestrator.py`（`DecisionOrchestrator`）——
占号 / 并行 spawn / 等待 / 传号 / 合成全在代码里。

⇒ 「唯一那份」= **orchestrator.py（代码）**。
  · `ORCHESTRATION.md` 收缩成给各角色的**指令口径**，不再含可执行的 PROMPT 块；
  · 任何**契约 / 指南**类 markdown 里都不该再出现「一段让 LLM 照着 spawn→等待→
    合成的可执行步骤」—— 那既是 L-3（第二套口径悄悄漂），又是把编排交回给 LLM
    的路（L-14 出卡递归就出在「谁能启动」这条边界上）。
  · **设计文档例外**：`docs/design/` 是描述「怎么设计的」的 SSOT，本就该讲这套
    机制；教程是冻结的过程记录；CHANGELOG / TODO 是历史。这些不在扫描范围内。
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from _scan import repo_files  # noqa: E402

ORCH = REPO / "skills" / "decision-card" / "ORCHESTRATION.md"
CARD = REPO / "bin" / "biga-card"
ORCHESTRATOR = REPO / "skills" / "decision-card" / "scripts" / "orchestrator.py"
AGENTS = REPO / "AGENTS.md"

#: 编排「怎么执行」写成**给 LLM 的可执行提示词**时的指纹：spawn 工具 + 等待工具
#: + 契约禁止的 yield + 合成步骤**一起出现**。不比整段文字（措辞会变），
#: 这几样同现才说明「有人把机制又写成了一段步骤」。
_FINGERPRINT = ("sessions_spawn", "agents_wait", "sessions_yield", "synthesize")


class TestSingleSourceIsCode:
    """唯一那份 = orchestrator.py（代码），不再是提示词。"""

    def test_编排的唯一那份是orchestrator_py(self):
        assert ORCHESTRATOR.exists(), "编排的唯一一份（代码）不见了"
        src = ORCHESTRATOR.read_text(encoding="utf-8")
        assert "class DecisionOrchestrator" in src
        # 它**自己**驱动每一步 —— 这些是代码调用，不是提示词里的祈使句。
        for kw in ("reserve_decision_id", "STAGE1_AGENTS",
                   "load_verdicts_and_refs", "card_ops.synthesize"):
            assert kw in src, f"orchestrator.py 没在自己驱动 {kw} —— 那编排还在别处"

    def test_ORCHESTRATION_md不再有可执行提示词块(self):
        """收缩之后它只留指令口径，那段 `<!-- PROMPT -->` 提示词必须没了 ——
        它正是当年被抽出来喂给 main 的第二套口径。"""
        text = ORCH.read_text(encoding="utf-8")
        assert "PROMPT:BEGIN" not in text and "PROMPT:END" not in text, (
            "ORCHESTRATION.md 还留着 PROMPT 标记块 —— C-II 已经把编排变成代码，\n"
            "  那段提示词就是会悄悄漂的第二套口径（它自己当年就漂过 --decision-id）。")

    def test_biga_card调orchestrator_而不再抽提示词喂main(self):
        run = CARD.read_text(encoding="utf-8").split("# ── 出新卡")[1]
        assert "orchestrator.py" in run, "出卡流程没有调 orchestrator.py"
        assert "PROMPT:BEGIN" not in run, "bin/biga-card 还在抽提示词 —— 那是老路径"
        # 老路径的 `$BIGA agent --agent main` 只能作为**历史注释**留着（解释锁为何
        # 存在），不能是活命令 —— 过滤掉注释行再查，否则连那段注释都不让留。
        active = "\n".join(ln for ln in run.splitlines()
                           if not ln.lstrip().startswith("#"))
        assert "agent --agent main" not in active, (
            "bin/biga-card 的**活命令**里还在 spawn main 编排 —— C-II 要程序驱动")


class TestNoSecondPromptForLLM:
    """没有第二处把编排写成**给 LLM 照着做的可执行步骤**。"""

    #: 扫描范围之外：设计文档（描述机制的 SSOT）、教程（冻结过程）、
    #: external（只读）、以及历史/唯一那份本身。
    _EXEMPT_PREFIX = ("docs/design/", "docs/tutorial/", "docs/external/")
    _EXEMPT_FILE = {
        ORCH.relative_to(REPO).as_posix(),
        "CHANGELOG.md", "TODO.md",
        pathlib.Path(__file__).relative_to(REPO).as_posix(),
    }

    def test_编排步骤不许作为可执行提示词出现在契约或指南里(self):
        dup = []
        for f in repo_files(".md") + repo_files(""):
            rel = f.relative_to(REPO).as_posix()
            if rel in self._EXEMPT_FILE or rel.startswith(self._EXEMPT_PREFIX):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if all(k in text for k in _FINGERPRINT):
                dup.append(rel)
        assert not dup, (
            f"这些地方把编排机制又写成了一段可执行步骤：{dup}\n"
            f"  唯一那份是 orchestrator.py（代码）。契约/指南里再写一遍 spawn→等待→\n"
            f"  合成，就是又一套会悄悄漂的口径（L-3），也是把编排交回 LLM 的路（L-14）。")

    def test_契约不再叫main手工编排(self):
        """`AGENTS.md`（main 的契约）只说「出卡是程序，别自己编排」，
        不再手把手教它 spawn→等待→合成。"""
        text = AGENTS.read_text(encoding="utf-8")
        assert "bin/biga-card" in text, "契约没提到出卡入口"
        assert "### Stage 0" not in text, (
            "契约里又出现 Stage 小节 —— 那是第二份编排，实测飞书路径下不会被执行")
        # 收缩后 main 的契约里不该同现「拉起工具 + 等待工具」这对机制字面量 ——
        # 那只有「教 main 怎么编排」时才会一起出现。
        assert not ("sessions_spawn" in text and "agents_wait" in text), (
            "AGENTS.md 又出现了 spawn+等待 这对编排机制 —— main 不该被教怎么手工编排")


class TestDecisionIdContradictionResolvedInCode:
    """收缩把「`--decision-id` 到底填不填」的三处自相矛盾**搬进了代码**：
    编排永远用占好的那个号显式合成；`synthesize.py` 永远优先用证据自带的号，
    绝不在有上游号时另分配一个（那正是产出混血卡 014 的那条）。"""

    def test_orchestrator总是显式传decision_id(self):
        src = ORCHESTRATOR.read_text(encoding="utf-8")
        assert re.search(r"card_ops\.synthesize\(\s*\n?\s*decision_id=did", src), (
            "orchestrator 合成时没有显式传占好的 decision_id —— 混血卡的口子又开了")

    def test_synthesize优先用证据自带的号(self):
        src = (REPO / "skills" / "decision-card" / "scripts" / "synthesize.py").read_text(
            encoding="utf-8")
        # args.decision_id 缺省时，用 verdict 自带的共享号，而不是无脑另分配一个。
        assert "shared.pop()" in src and "next_decision_id()" in src, (
            "synthesize.py 不再优先用证据自带的号 —— 那就退回到会撞号的老行为")
