"""全仓扫描的**唯一**文件枚举 —— 三个 AST 扫描器共用。

为什么要抽出来
--------------
在此之前，`test_no_raw_sqlite.py` 与 `test_contract_single_impl.py` 各自定义了
**一模一样**的 `EXCLUDE_DIRS`。两份相同的常量就是 L-3 的形状：
改了一份忘了另一份时，剩下那份仍然报绿。

🔴 为什么不用手维护的黑名单
---------------------------
实测（2026-09-21）：`.claude/worktrees/` 下出现了三份仓库副本
（`git worktree`，被 `.git/info/exclude` 忽略）。两个扫描器都走文件系统，
**忽略规则对它们不生效** —— 于是同一份 `_store/db.py` 被数了四遍，
其中三遍报成「违规」。

黑名单的问题是它只挡得住**你想到过的**目录。而仓库里会长出什么
（worktree、缓存、临时克隆）不由测试作者决定。

⇒ 改用 ``git ls-files -co --exclude-standard``：
**git 认为属于这个仓库的文件**，正好就是扫描该覆盖的范围。
它自动尊重 `.gitignore` 与 `.git/info/exclude`，而且是单一口径。

⚠️ 代价：完全没被 git 跟踪、又被忽略的文件扫不到。
这是对的 —— 那种文件本来就不构成仓库的一部分。
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]


def repo_files(suffix: str = ".py") -> list[pathlib.Path]:
    """仓库里的文件（已跟踪 + 未跟踪但未被忽略），按路径排序。

    Args:
        suffix: 只要这个后缀的。传空串则全要。
    """
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True)
    files = []
    for line in out.stdout.splitlines():
        if suffix and not line.endswith(suffix):
            continue
        p = REPO / line
        if p.is_file():
            files.append(p)
    return sorted(files)
