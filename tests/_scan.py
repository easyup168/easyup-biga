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

#: `docs/external/` 是只读参考材料（CLAUDE.md「文档纪律」）——外部评审/上游
#: 文档常常自带示意代码（比如一份 `domain_models.py` 示范契约该长什么样），
#: 名字撞上本仓库的契约类、或示范代码里直接 `import sqlite3`，都是**在描述
#: 同一个域**，不代表本仓库长出了第二份实现。AST 纯度类守卫（契约唯一实现 /
#: DB 唯一入口）应该排除这整个目录，不分是否被 git 跟踪——
#: 2026-09-23 实测撞到：这类参考材料大多刻意 gitignore（新文件不提交），
#: 正常 git 路径下天然不可见，只有 `test_scan_fallback.py` 模拟「没有 git」
#: 退化成纯文件系统遍历时才会扫到，而那正是这道判据本该在两条路径上一致的
#: 地方——一致，不代表都要报红，是都要用同一个排除规则。
EXTERNAL_DIR = REPO / "docs" / "external"


def is_external_reference(p: pathlib.Path) -> bool:
    """`p` 是否落在只读参考材料目录下 —— 见上方 EXTERNAL_DIR 的注释。"""
    return EXTERNAL_DIR in p.parents

#: 降级遍历时跳过的目录。
#: ⚠️ 这份黑名单**只在没有 git 的时候**才生效 —— 正常路径仍然用
#:    `git ls-files`，所以它不会重新变成「只挡得住想到过的目录」那个坑。
_FALLBACK_SKIP = {
    ".git", ".claude", "__pycache__", ".pytest_cache", ".ruff_cache",
    "node_modules", "data", "logs", ".venv", "venv",
    # 🔴 批 U-II：`build/` 与 `dist/` 是 setuptools 的构建产物。在此之前它们
    #    在本仓库**没有理由存在**；U-II 之后 `pip install -e .` 是一条被写进
    #    文档的常规操作，而它会在 build/lib/ 下留下**每个模块的第二份拷贝**。
    #    实测（2026-09-24 探针）：副本里带着 build/、剥掉 .git 之后，
    #    `test_A_契约类名只在_contract_下定义`（不变式 I-3「契约只有一份实现」）
    #    等**五条**守卫集体误报 —— 扫描器把构建产物当成了第二份实现。
    #    ⚠️ 正常 git 路径不受影响（.gitignore 挡着），所以这个坑**只在降级模式下
    #    出现**，而降级模式正是为「发布 tarball / 容器 COPY / sdist」准备的 ——
    #    从开发机 `COPY . .` 进容器恰好就会把 build/ 带进去。
    "build", "dist",
}

#: 后缀型的构建产物（`easyup_biga.egg-info/`）。`_FALLBACK_SKIP` 比的是**整段
#: 路径名**，匹配不了这种带可变前缀的目录，所以单列一条。
_FALLBACK_SKIP_SUFFIX = (".egg-info",)


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
        if any(part in _FALLBACK_SKIP
               or part.endswith(_FALLBACK_SKIP_SUFFIX) for part in rel.parts):
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


def sandbox_ignore(repo_root: pathlib.Path):
    """造沙盒副本时的排除规则 —— **两处沙盒共用的唯一实现**。

    🔴 为什么不能直接用 `shutil.ignore_patterns("data", ...)`
    -------------------------------------------------------
    `ignore_patterns` 的 glob 对**每一层目录**生效。写 `"data"` 本意是排掉仓库
    根下那个装 SQLite 库的 `data/`，实际连 `src/easyup_biga/data/`（Phase 3 的
    数据平台包）一起丢掉了 —— 而且是**静默**丢：copytree 不报错，沙盒照常建起来，
    只是少了一个包。

    实测后果（2026-09-25，评审外部 P3-1 实现时发现）：只要有任何生产路径
    `import easyup_biga.data`，`test_spawn_proof.py` 的 11 条出卡路径测试就会
    集体 `ModuleNotFoundError`，而报错指向的是沙盒里的临时目录，看不出是
    排除规则干的。

    ⚠️ 这是同一个坑的**第三个实例**。`test_scan_fallback.py` 的注释里已经记着
    前两个：`.biga-card-stop`（运行时总闸被复制进沙盒）与 `build/`（构建产物
    没被排除）。那条注释的原话是「这份硬编码名单没跟上」—— 现在它连名单**形状**
    都不对：按名字匹配任意层级，而它想表达的是「仓库根下的那一个」。

    ⇒ 改成按**相对仓库根的路径**判断，并且两处沙盒共用这一份。
    """
    root = repo_root.resolve()
    #: 只在仓库根下排除（按路径判，不按名字判）
    ROOT_ONLY = {"data", "memory", ".claude", ".pytest_cache", "build", "dist"}
    #: 任意层级都排除（它们在任何目录下都是同一类东西）
    ANY_LEVEL = {".git", "__pycache__", ".biga-card-stop", ".biga-card.lock"}

    def _ignore(directory: str, names: list[str]) -> set[str]:
        here = pathlib.Path(directory).resolve()
        drop = {n for n in names if n in ANY_LEVEL}
        if here == root:
            drop |= {n for n in names if n in ROOT_ONLY}
        drop |= {n for n in names if n.endswith(".egg-info")}
        return drop

    return _ignore
