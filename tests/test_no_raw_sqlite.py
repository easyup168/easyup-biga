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

from _scan import is_external_reference, repo_files  # noqa: E402

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
    """`p` 不受「DB 唯一入口」约束——本体所在目录，或只读参考材料（原因见
    `test_contract_single_impl.py::_in_contract` 同一处注释，同一个道理）。"""
    return STORE_DIR in p.parents or is_external_reference(p)


ALL_FILES = _py_files()


def test_docs_external下的示意代码不算违规裸sqlite3():
    """2026-09-23 实测撞到：`test_scan_fallback.py` 模拟"没有 git"退化成纯
    文件系统遍历时，会扫到 `docs/external/` 下外部评审自带的示意代码
    （一份示范 Repository 该长什么样的 `repository.py`，直接
    `import sqlite3`，平时被 gitignore、正常 git 路径天然看不到），
    误判成业务代码违规裸用 sqlite3。"""
    fake = REPO / "docs" / "external" / "some-review" / "reference" / "repository.py"
    assert _in_store(fake), "docs/external/ 下的文件应该被当作只读参考材料排除"


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


# ──────────────────────────── 间接 import 也要挡（外部评审 F12）
#
# 第二条检查的文档字符串**明确写着**「独立于上一条 —— 有人可能通过
# importlib 或别名绕过 import 检查」，但它的判定逻辑仍然只认
# `X.connect(...)` 里 X 是字面量 `ast.Name` 且 `id == "sqlite3"`。
#
# 评审实测：`__import__("sqlite3").connect(path)` 与
# `importlib.import_module("sql" + "ite3")` + getattr 两条检查全部通过，
# 而且**这不是文字游戏** —— 拿到的是一条真实、完整可读写的
# `sqlite3.Connection`，能打开磁盘上任意 sqlite 文件。
#
# 🔴 与 F11 不同，这条**没有「读取时二次校验」那道后备**：
#    一条绕开 `db.py` 的裸连接可以被拿去做任何读写，不经过任何契约层。
#
# ⚠️ 这里不打算追平「任意字符串拼接」—— 那是判定不了的。
#    换一个可判定的判据：`importlib.import_module` / `__import__`
#    在业务代码里**本来就不是一个自然会写出的模式**。
#    ⇒ 默认禁止，真有需要就 `store-exempt:` 举手。
#      这与词表、触发器、`server_as_of` 是同一条原则。

_INDIRECT_IMPORT = {"__import__", "import_module"}


def test_业务代码不许用间接import():
    bad = []
    for path in _py_files():
        if STORE_DIR in path.parents or path.parent.name == "tests":
            continue
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in _INDIRECT_IMPORT and not _is_exempt(lines, node.lineno):
                bad.append(f"{path.relative_to(REPO)}:{node.lineno} {name}(…)")
    assert not bad, (
        "业务代码里出现了间接 import：\n  " + "\n  ".join(bad) + "\n"
        "  它能绕开上面两条 AST 检查拿到一条真实可读写的 sqlite3 连接，\n"
        "  而这条路**没有读取时的二次校验兜底**。\n"
        "  真有需要就在该行标 `store-exempt: <理由>`。"
    )


def test_这条检查真的能抓到绕过写法(tmp_path):
    """🔴 判据自检：造一个真的绕过样本，确认它会被逮住。

    不自检的话，这条测试会因为「业务代码里本来就没有间接 import」
    而永远绿 —— 那正是「通过是因为什么都没查」。
    """
    sample = tmp_path / "sneaky.py"
    sample.write_text('c = __import__("sqlite3").connect("/tmp/x.db")\n', encoding="utf-8")
    hits = [n for n in ast.walk(ast.parse(sample.read_text(encoding="utf-8")))
            if isinstance(n, ast.Call)
            and (n.func.id if isinstance(n.func, ast.Name)
                 else getattr(n.func, "attr", "")) in _INDIRECT_IMPORT]
    assert hits, "判据连最直白的绕过写法都抓不到"
