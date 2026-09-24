"""没有 `.git` 的时候，守卫还能不能跑 —— 外部深度评审。

为什么这是个真问题
------------------
全仓扫描的文件枚举走 `git ls-files -co --exclude-standard`（`tests/_scan.py`，
理由见那份 docstring：黑名单只挡得住你想到过的目录）。

但它当时写的是 `check=True`。于是把仓库放到**一个没有 `.git` 的目录**里 ——
发布 tarball、容器镜像里的 `COPY`、sdist、`git archive` 的产物 ——

    subprocess.CalledProcessError: Command '['git', 'ls-files', …]'
    returned non-zero exit status 128.

**十几条与 git 毫无关系的守卫集体 error。** 契约单一实现、裸 sqlite 扫描、
文档规约……一条都跑不了。

> 🔴 更糟的是那种失败**看起来像代码坏了**。
> 排查方向一开始就是错的 —— 和「凭据共享」那条耦合是同一类陷阱。

这一章的判据
------------
不满足于「函数不抛异常了」。**真的把仓库复制成没有 `.git` 的样子，
在里面跑真实的守卫**，断言它们仍然全绿。

⚠️ 这一条与 `_scan.py` 里那句「黑名单只挡得住你想到过的目录」并不矛盾：
   降级黑名单**只在 git 不可用时**生效，正常路径仍然是 git 说了算。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

import _scan  # noqa: E402

#: 在 git-less 副本里真跑一遍的守卫。挑的是**纯 AST 扫描**：
#: 它们完全不关心 git，却是上面那个 error 的头号受害者。
#: 实测 0.75s，放进默认测试集不会让人开始跳过测试。
_VICTIMS = ["tests/test_no_raw_sqlite.py", "tests/test_contract_single_impl.py"]


def test_有git时走git():
    assert _scan.scan_mode() == "git"


def test_没有git时报告降级_而不是假装正常(tmp_path, monkeypatch):
    """🔴 降级必须**说出来**（红线 R-3 在扫描器上的落点）。

    悄悄降级比抛异常更危险：扫描范围变了，而输出一模一样。
    """
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(_scan, "REPO", tmp_path)
    assert _scan.scan_mode() == "walk"
    assert [p.name for p in _scan.repo_files(".py")] == ["a.py"]


def test_降级时跳过明显不属于仓库的东西(tmp_path, monkeypatch):
    """git 不在了，`.gitignore` 也就没人执行 —— 至少别把缓存和副本算进来。

    ⚠️ 这份黑名单**不完整，也不可能完整**。它只是让降级模式可用，
       不是想把 git 那份口径重新实现一遍。
    """
    for rel in ("keep.py", "__pycache__/x.py", ".claude/worktrees/c/y.py",
                "data/z.py", "node_modules/w.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(_scan, "REPO", tmp_path)
    assert [p.name for p in _scan.repo_files(".py")] == ["keep.py"]


def test_多后缀(tmp_path, monkeypatch):
    """`repo_files(".py", ".md")` —— 原来只收一个后缀，
    于是需要两种文件的守卫要么调两遍、要么传空串再自己过滤。"""
    for rel in ("a.py", "b.md", "c.sh"):
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(_scan, "REPO", tmp_path)
    assert [p.name for p in _scan.repo_files(".py", ".md")] == ["a.py", "b.md"]
    assert len(_scan.repo_files()) == 3, "一个都不传 ⇒ 全要"


def test_把仓库剥掉git之后守卫仍然全绿(tmp_path):
    """真实复现评审的场景 —— 判据是**别的守卫的退出码**，不是本模块的断言。

    🔴 为什么不满足于单测 `repo_files()`：
       评审报的现象是「十几条守卫集体 error」。那是**集成层**的事实，
       而只测枚举函数会漏掉「某个守卫自己又调了一次 git」这种情况。
       本项目已经八次踩到「守卫查的地方和它声称守的地方不是同一处」。
    """
    work = tmp_path / "repo"
    shutil.copytree(REPO, work, symlinks=True, ignore=shutil.ignore_patterns(
            # 🔴 运行时产物必须排除 —— 事故当天 `.biga-card-stop`（总闸）
            #    被原样复制进沙盒，于是三条出卡路径测试全部拿到 rc=3。
            #    沙盒要复制的是**代码**，不是这台机器此刻的运行状态。
            ".biga-card-stop", ".biga-card.lock",
            ".git", "__pycache__", ".pytest_cache", "data", ".claude", "memory",
            # 🔴 2026-09-24：同一个坑的第二个实例。F 节打包工作开工后，
            #    `build/`（setuptools 的构建产物）第一次出现在这台机器上，
            #    这份硬编码名单没跟上——它比对的是 git ls-files 之外的东西，
            #    不读 .gitignore，加了 .gitignore 条目救不了它。
            "build", "dist", "*.egg-info"))
    assert not (work / ".git").exists(), "副本里还有 .git，这条测试等于没测"

    r = subprocess.run(
        [sys.executable, "-m", "pytest", *_VICTIMS, "-q", "-p", "no:cacheprovider"],
        cwd=work, capture_output=True, text=True, timeout=180,
        # 🔴 清掉 `GIT_DIR` 之类的继承 —— 否则 git 会顺着环境变量
        #    找回真仓库，副本里没有 `.git` 也照样成功，测试变成平凡通过。
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    assert r.returncode == 0, (
        "把仓库剥掉 `.git` 之后，与 git 无关的守卫跑不起来了：\n"
        f"--- stdout ---\n{r.stdout[-1800:]}\n--- stderr ---\n{r.stderr[-600:]}")
