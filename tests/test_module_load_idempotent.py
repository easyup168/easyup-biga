"""常驻守卫：测试里手写的模块加载不许无条件覆写 `sys.modules`。

为什么需要它
------------
`skills/decision-card`/`skills/risk-check` 这些目录名带连字符，测试文件没法走
标准 `import` 语法，只能用 `importlib.util.spec_from_file_location` 手动加载并
登记进 `sys.modules[name] = mod`。这行本身是全局、可变、进程范围的状态——如果
不加"已经加载过就复用"这道检查，同一个 pytest 进程里，后收集的文件会用一个
全新对象覆写掉先收集的文件（或生产代码 `orchestrator.py` 自己的 `import`）已经
绑定的那个，`monkeypatch.setattr` 因此可能打在一个谁都不再实际使用的对象上——
静默失效，且失效与否取决于 pytest 的文件收集顺序。

批 J-II（教程第 32 章）踩到过一次并修好，但只改了当时正在改的那一个文件；三轮
对抗性复核发现全仓另有 11 处一模一样的复制体从未被推广修复（教程第 43 章，
`docs/tutorial/43-module-identity-idempotent-load.md`）。

🔴 为什么不能只靠运行时身份断言（本仓库确实先这么做过一次）
------------------------------------------------------------------
`test_orchestrator.py::TestModuleIdentityAcrossTestFiles` 断言"orchestrator.py
绑定的 card_ops/risk_check 与当前 import 拿到的是同一个对象"——这条断言本身没错，
但它能不能**抓到**一处新引入的违规，取决于那个违规文件在字母序收集顺序里排在
`test_orchestrator.py` 前面还是后面：排在前面的话，`orchestrator.py` 自己的
`import` 反而会"捡漏"到违规文件已经登记的对象，两者天然一致，断言通过——
不是修法没用，是这条断言只在"覆写方后收集"的顺序里才会真的发作。

实测过：把某处幂等检查完全删掉，默认字母序全量 `pytest -q` **一条不红**——
运行时身份断言对这类"排在 orchestrator 前面"的违规完全失明。

⇒ 补一条源码级扫描，判据不依赖任何运行时状态、任何收集顺序：**任何
`sys.modules[...] = ...` 赋值，其所在的函数（或模块顶层）内必须能找到一个
`... in sys.modules` 的判断**。与 `test_no_raw_sqlite.py`/
`test_contract_single_impl.py` 同一个风格——本仓库对这类"复制粘贴出来的坑"
一贯用源码扫描钉，不靠运行时表现。

需要豁免时在该行或上一行注明::

    sys.modules[name] = mod  # sys-modules-exempt: <理由>
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent

from _scan import repo_files  # noqa: E402

EXEMPT_MARKER = "sys-modules-exempt:"

#: 找到 `sys.modules[...] = ...` 的赋值目标之后，再往里/外找幂等检查时，
#: 一旦跨过这些边界就不再是"同一段逻辑"——不越过嵌套的函数/类/lambda。
_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines) and EXEMPT_MARKER in lines[idx]:
            return True
    return False


def _is_sys_modules_expr(node) -> bool:
    """`node` 是不是 `sys.modules` 这个表达式本身。"""
    return (isinstance(node, ast.Attribute) and node.attr == "modules"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _is_sys_modules_subscript(node) -> bool:
    """`node` 是不是形如 `sys.modules[...]` 的下标表达式。"""
    return (isinstance(node, ast.Subscript) and _is_sys_modules_expr(node.value))


def _is_membership_test(node) -> bool:
    """`node` 是不是形如 `X in sys.modules` / `X not in sys.modules` 的比较。"""
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
        return False
    return any(_is_sys_modules_expr(o) for o in (node.left, *node.comparators))


def _walk_same_scope(node):
    """像 `ast.walk`，但遇到新的函数/类/lambda 作用域就不再往里走——
    只看"跟这个节点同一层逻辑"的语句，不借用无关函数里恰好存在的检查。"""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, _SCOPE_BOUNDARY):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _enclosing_scope(node, parent_of):
    """`node` 所在的最近一层函数（或到顶为模块本身）。"""
    n = node
    while True:
        p = parent_of.get(id(n))
        if p is None or isinstance(p, ast.Module):
            return p if p is not None else n
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p
        n = p


def find_offenders(paths: list[pathlib.Path]) -> list[str]:
    """扫描给定文件，返回"无幂等检查的 sys.modules 覆写点"列表（`path:lineno`）。

    抽成独立函数（不内联进测试体）是为了下面的"判据自检"能复用同一套逻辑，
    不必另外维护一份简化版判据——那正是 L-3 的形状。
    """
    offenders: list[str] = []
    for path in paths:
        src = path.read_text(encoding="utf-8", errors="ignore")
        lines = src.splitlines()
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:
            continue
        parent_of: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_of[id(child)] = parent
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign)
                    and any(_is_sys_modules_subscript(t) for t in node.targets)):
                continue
            if _is_exempt(lines, node.lineno):
                continue
            scope = _enclosing_scope(node, parent_of)
            guarded = any(isinstance(n, ast.If) and _is_membership_test(n.test)
                         for n in _walk_same_scope(scope))
            if not guarded:
                try:
                    label = path.relative_to(REPO)
                except ValueError:
                    label = path  # 判据自检用的样本文件在 tmp_path 里，不在仓库下
                offenders.append(f"{label}:{node.lineno}")
    return offenders


def _py_files() -> list[pathlib.Path]:
    return [p for p in repo_files(".py") if p.parent == REPO / "tests"]


def test_扫描范围非空():
    """守卫的守卫——扫不到文件的扫描器永远全绿。"""
    files = _py_files()
    assert len(files) >= 10, f"只扫到 {len(files)} 个 tests/*.py，扫描范围可能坏了"


def test_tests目录里没有无幂等检查的sys_modules覆写():
    offenders = find_offenders(_py_files())
    assert not offenders, (
        "以下位置无条件覆写了 sys.modules，没有找到同一层的幂等检查：\n  "
        + "\n  ".join(offenders)
        + "\n改成先判断 `if name in sys.modules: return sys.modules[name]`（或"
        "等价的 if/else），已加载过就复用同一个对象——不然后收集的文件会静默"
        "换掉先收集的文件（或 orchestrator.py 自己 import）已经绑定的那个，"
        "monkeypatch 打不到真正被调用的对象上，且是否发作取决于收集顺序。\n"
        "真需要无条件覆写就在该行标 `sys-modules-exempt: <理由>`。"
    )


def test_这条检查真的能抓到无条件覆写(tmp_path):
    """🔴 判据自检：造一个真的违规样本，确认它会被逮住——不自检的话，这条测试
    会因为"当前仓库里没有违规"而永远绿，那正是「通过是因为什么都没查」。"""
    bad = tmp_path / "bad_loader.py"
    bad.write_text(
        "import sys, importlib.util\n"
        "def _load(name, rel):\n"
        "    spec = importlib.util.spec_from_file_location(name, rel)\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = mod\n"  # 无条件覆写，没有任何幂等检查
        "    spec.loader.exec_module(mod)\n"
        "    return mod\n",
        encoding="utf-8",
    )
    offenders = find_offenders([bad])
    assert offenders == [f"{bad}:5"], f"判据没抓到样本里第 5 行的违规：{offenders}"


def test_带幂等检查的覆写不算违规(tmp_path):
    """反面对照：同样的覆写，前面加一道幂等检查，不该被判成违规。"""
    good = tmp_path / "good_loader.py"
    good.write_text(
        "import sys, importlib.util\n"
        "def _load(name, rel):\n"
        "    if name in sys.modules:\n"
        "        return sys.modules[name]\n"
        "    spec = importlib.util.spec_from_file_location(name, rel)\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = mod\n"
        "    spec.loader.exec_module(mod)\n"
        "    return mod\n",
        encoding="utf-8",
    )
    assert find_offenders([good]) == []


def test_模块级if_else风格也认得(tmp_path):
    """反面对照：`test_sector_calc.py` 那种模块级 if/else 风格（赋值在 else 分支
    里，不是 if 分支之后的同级早返回），同样不该被判成违规。"""
    good = tmp_path / "good_module_level.py"
    good.write_text(
        "import sys, importlib.util\n"
        "if 'sector_calc' in sys.modules:\n"
        "    sc = sys.modules['sector_calc']\n"
        "else:\n"
        "    spec = importlib.util.spec_from_file_location('sector_calc', 'x.py')\n"
        "    sc = importlib.util.module_from_spec(spec)\n"
        "    sys.modules['sector_calc'] = sc\n"
        "    spec.loader.exec_module(sc)\n",
        encoding="utf-8",
    )
    assert find_offenders([good]) == []
