"""常驻守卫：业务代码里不许出现裸 sqlite3。

全仓 AST 扫描，`skills/_store/` 之外的任何 .py 都不许：

  * `import sqlite3` / `from sqlite3 import ...`
  * 调用 `sqlite3.connect(...)`

为什么这条值得写成测试而不是写进规范：
切换到 PostgreSQL 的触发条件已经写死在 architecture.md §5.1，
而「切库」这件事的成本完全取决于连接逻辑集中还是分散。
一旦有人在某个 skill 里图省事直接 `sqlite3.connect`，
它不会报任何错、跑得好好的，直到切库那天才被发现 —— 那时它可能已经有十几处了。

需要例外时在该行或上一行注明::

    import sqlite3  # store-exempt: <理由>
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
STORE_DIR = REPO / "skills" / "_store"

from _scan import repo_files  # noqa: E402

EXEMPT_MARKER = "store-exempt:"

BANNED_MODULES = {"sqlite3"}


def _py_files() -> list[pathlib.Path]:
    # 🔴 走 git 的口径，不走文件系统 —— 见 `tests/_scan.py` 的「worktree 副本」一节
    return repo_files(".py")


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines) and EXEMPT_MARKER in lines[idx]:
            return True
    return False


def _in_store(p: pathlib.Path) -> bool:
    return STORE_DIR in p.parents


ALL_FILES = _py_files()


def test_扫描范围非空():
    """守卫的守卫 —— 扫不到文件的扫描器永远全绿。"""
    assert len(ALL_FILES) >= 3, f"只扫到 {len(ALL_FILES)} 个 .py，扫描范围可能坏了"
    assert any(_in_store(p) for p in ALL_FILES), "没扫到 _store/ 本身"


def test_store之外不许import_sqlite3():
    offenders: list[str] = []
    for p in ALL_FILES:
        if _in_store(p):
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(p))):
            hit = False
            if isinstance(node, ast.Import):
                hit = any(a.name.split(".")[0] in BANNED_MODULES for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                hit = bool(node.module) and node.module.split(".")[0] in BANNED_MODULES
            if hit and not _is_exempt(lines, node.lineno):
                offenders.append(f"{p.relative_to(REPO)}:{node.lineno}")
    assert not offenders, (
        "以下文件直接 import 了 sqlite3：\n  " + "\n  ".join(offenders)
        + "\n一切 DB 访问走 `from _store import ...`。"
    )


def test_store之外不许调用sqlite3_connect():
    """独立于上一条 —— 有人可能通过 importlib 或别名绕过 import 检查。"""
    offenders: list[str] = []
    for p in ALL_FILES:
        if _in_store(p):
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(p))):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_hit = (
                isinstance(f, ast.Attribute)
                and f.attr == "connect"
                and isinstance(f.value, ast.Name)
                and f.value.id in BANNED_MODULES
            )
            if is_hit and not _is_exempt(lines, node.lineno):
                offenders.append(f"{p.relative_to(REPO)}:{node.lineno} sqlite3.connect(...)")
    assert not offenders, (
        "以下位置直接开了 sqlite3 连接：\n  " + "\n  ".join(offenders)
        + "\n请用 `from _store import connect`。"
    )


def test_store层自身确实是唯一入口():
    """反向断言：`_store/` 里确实有 sqlite3，否则说明这条规则在守一个空壳。"""
    src = "\n".join(p.read_text(encoding="utf-8") for p in STORE_DIR.rglob("*.py"))
    assert "import sqlite3" in src, "_store/ 里没有 sqlite3 —— 这条规则守着一个不存在的入口"
