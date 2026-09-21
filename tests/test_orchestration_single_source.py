"""出卡编排只能有一份 —— L-3 在**提示词**上的实例。

背景（2026-09-21 19:31，飞书触发）
-----------------------------------
同一段编排步骤原来有两份：

| 在哪 | 实测 |
|---|---|
| `bin/biga-card` 的提示词 | 100% 照做 |
| `AGENTS.md` 的 Stage 0~3 | 内容一模一样，**飞书路径下没被执行** |

从飞书说「出一张决策卡片吧」的结果：

    sessions_spawn ×4      ← 只有 4 个（少 news）
    sessions_yield         ← 契约明令禁止
    exec new_decision.py   ← 占号发生在 spawn 之后（L-11）

四个 spawn 无归属，约 $0.4 白花。**而这些规则在契约里一条不缺。**

🔴 **契约写了不等于会被遵守。** 同一段知识两份实现，弱的那份会悄悄失效 ——
这就是 L-3，只不过这次失效的是提示词，不是代码。

⇒ 合并成 `skills/decision-card/ORCHESTRATION.md` 一份，
  脚本从标记块里抽，契约只留一句「跑 `bin/biga-card`」。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from _scan import repo_files  # noqa: E402

ORCH = REPO / "skills" / "decision-card" / "ORCHESTRATION.md"
CARD = REPO / "bin" / "biga-card"

#: 编排序列的指纹。不用整段文字比对 —— 措辞会变，而**这些一起出现**
#: 才说明「有人在这里又写了一遍步骤」。
_FINGERPRINT = ("new_decision", "sessions_yield", "agents_wait", "synthesize")


def _prompt_block() -> str:
    m = re.search(r"<!-- PROMPT:BEGIN -->(.*?)<!-- PROMPT:END -->",
                  ORCH.read_text(encoding="utf-8"), re.S)
    return m.group(1).strip() if m else ""


class TestSingleSource:
    def test_唯一那份存在且有标记块(self):
        assert ORCH.exists(), "编排的唯一一份不见了"
        p = _prompt_block()
        assert p, "PROMPT 标记块是空的 —— 脚本会取不到提示词"
        for kw in _FINGERPRINT:
            assert kw in p, f"摘要里少了 {kw} —— 那正是实测出过错的一步"

    def test_编排步骤不许出现第二处(self):
        """🔴 判据：**四个关键词同时出现**在同一个文件里。

        不比整段文字 —— 措辞会变，而副本往往是「改写过的同一段话」。
        实测那次失效的两份就是措辞不同、内容相同。
        """
        allowed = {
            ORCH.relative_to(REPO).as_posix(),
            "CHANGELOG.md",                       # 历史记录
            "TODO.md",                            # 待办里引用过症状
            pathlib.Path(__file__).relative_to(REPO).as_posix(),  # 本文件自己
        }
        dup = []
        for f in repo_files(".md") + repo_files(""):
            rel = f.relative_to(REPO).as_posix()
            if rel in allowed or rel.startswith(("docs/tutorial/", "docs/external/")):
                continue          # 教程是冻结的过程记录，external 只读
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if all(k in text for k in _FINGERPRINT):
                dup.append(rel)
        assert not dup, (
            f"这些地方又写了一遍编排步骤：{dup}\n"
            f"  同一段知识两份实现，弱的那份会悄悄失效（2026-09-21 实测）。\n"
            f"  只留 {ORCH.relative_to(REPO)} 一份，别处引用它。")

    def test_契约不再自己描述编排(self):
        """`AGENTS.md` 应该只说「跑 bin/biga-card」。"""
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        assert "bin/biga-card" in text, "契约没告诉 Supervisor 该跑什么"
        assert "### Stage 0" not in text, (
            "契约里又出现 Stage 小节 —— 那是第二份编排，"
            "实测它在飞书路径下不会被执行")


class TestScriptExtracts:
    def test_脚本不自己写提示词_而是抽取(self):
        src = CARD.read_text(encoding="utf-8")
        assert "PROMPT:BEGIN" in src, "脚本没有从唯一那份里抽，多半是又写了一遍"

    @pytest.mark.parametrize("break_it", ["删文件", "删标记块"])
    def test_取不到提示词时直接停(self, tmp_path, break_it):
        """🔴 **fail-closed**：取不到就停，不要退化成「让它凭契约自己编排」——
        那恰恰是实测会出错的那条路。"""
        import shutil
        work = tmp_path / "repo"
        shutil.copytree(REPO, work, symlinks=True, ignore=shutil.ignore_patterns(
            # 🔴 运行时产物必须排除 —— 事故当天 `.biga-card-stop`（总闸）
            #    被原样复制进沙盒，于是三条出卡路径测试全部拿到 rc=3。
            #    沙盒要复制的是**代码**，不是这台机器此刻的运行状态。
            ".biga-card-stop", ".biga-card.lock",
            ".git", "__pycache__", "data", ".pytest_cache", ".claude"))
        target = work / "skills" / "decision-card" / "ORCHESTRATION.md"
        if break_it == "删文件":
            target.unlink()
        else:
            target.write_text(
                re.sub(r"<!-- PROMPT:BEGIN -->.*?<!-- PROMPT:END -->", "",
                       target.read_text(encoding="utf-8"), flags=re.S),
                encoding="utf-8")
        # 🔴 把 agent 调用换成会留痕的桩 —— 这样能**证明它没被走到**。
        #    不这么做的话，守卫一坏，这条测试自己就会跑一次真实出卡
        #    （3 分钟 / $1.3）。第一版正是如此，探针当场把它跑起来了。
        marker = tmp_path / "AGENT_WAS_CALLED"
        stub = tmp_path / "fake-biga"
        stub.write_text(f'#!/usr/bin/env bash\ntouch "{marker}"\n', encoding="utf-8")
        stub.chmod(0o755)

        import os
        r = subprocess.run(
            ["bash", str(work / "bin" / "biga-card")],
            capture_output=True, text=True, cwd=work, timeout=20,   # 降级会进等待循环，20s 足够判定
            env={**os.environ, "BIGA": str(stub),
                 "BIGA_DB_PATH": str(tmp_path / "t.db")})
        assert r.returncode == 2, f"应当 fail-closed，实际 rc={r.returncode}"
        assert "取不到编排提示词" in r.stderr
        assert not marker.exists(), (
            "取不到提示词却仍然调用了 agent —— 那是一次真花钱的降级运行")
