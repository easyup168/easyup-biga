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

🔴 但「没有 git」不能等于「测试跑不起来」
----------------------------------------
外部深度评审：把仓库解压到一个没有 `.git` 的目录（发布 tarball、
容器里的 `COPY`、`pip download` 的 sdist），`check=True` 直接抛
`CalledProcessError` ⇒ **十几条守卫集体 error**，而它们和 git 毫无关系。

> 更糟的是那种失败**看起来像代码坏了**，排查方向一开始就是错的。

⇒ 退化到文件系统遍历 + 一份最小黑名单，并把「这次是降级模式」
  **说出来**（`scan_mode()`）。降级下扫不到的东西要算「判不了」，
  不能算通过 —— 红线 R-3 在扫描器上的落点。
"""

from __future__ import annotations

import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

#: 降级遍历时跳过的目录。
#: ⚠️ 这份黑名单**只在没有 git 的时候**才生效 —— 正常路径仍然用
#:    `git ls-files`，所以它不会重新变成「只挡得住想到过的目录」那个坑。
_FALLBACK_SKIP = {
    ".git", ".claude", "__pycache__", ".pytest_cache", ".ruff_cache",
    "node_modules", "data", "logs", ".venv", "venv",
}


def _git_files() -> list[str] | None:
    """git 说哪些文件属于这个仓库。不可用时返回 `None`。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=REPO, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # CalledProcessError：不是仓库 / FileNotFoundError：机器上没装 git
        return None
    return out.stdout.splitlines()


def _walk_files() -> list[str]:
    out = []
    for p in REPO.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO)
        if any(part in _FALLBACK_SKIP for part in rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def scan_mode() -> str:
    """`"git"` 或 `"walk"` —— 给需要区分三态的守卫用。

    🔴 有守卫（比如「运行时产物不进仓库」）的判据**本质上依赖 git**：
    降级模式下它既不是通过也不是失败，是**判不了**。
    那种守卫应当读这个值并 `skip`，而不是默默报绿。
    """
    return "git" if _git_files() is not None else "walk"


def repo_files(*suffixes: str) -> list[pathlib.Path]:
    """仓库里的文件（已跟踪 + 未跟踪但未被忽略），按路径排序。

    Args:
        *suffixes: 只要这些后缀的，例如 ``repo_files(".py", ".md")``。
            一个都不传 ⇒ 全要（与原来传空串同义）。
    """
    wanted = tuple(s for s in suffixes if s)
    lines = _git_files()
    if lines is None:
        lines = _walk_files()
    files = []
    for line in lines:
        if wanted and not line.endswith(wanted):
            continue
        p = REPO / line
        if p.is_file():
            files.append(p)
    return sorted(files)
