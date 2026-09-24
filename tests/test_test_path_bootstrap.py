"""常驻守卫：`tests/` 里不许再出现**冗余**的 `sys.path.insert`（批 U-III）。

批 U-III 从 `tests/` 删掉 56 处手写路径操作。它们冗余的理由只有一条，且写在
`pyproject.toml` 里：`pythonpath = ["skills", "src", "."]` —— pytest 启动时就把
这三条挂好了，测试文件再挂一遍不改变任何结果。

🔴 但「冗余」只对**这三个目标**成立
------------------------------------
指向 `tools/verify/` / `deploy/openclaw/` / 各 skill 的 `scripts/` 的那些
**是承重的** —— 那些目录**不在** `pythonpath` 上，删了对应测试当场 import 失败。
本文件因此不是「禁止 `sys.path.insert`」，而是「禁止指向已被覆盖的目标」。

🔴 判据取**解析后的路径**，不取这行长什么样
--------------------------------------------
本批实测踩过：先按「行里有没有 `skills` 这个词」分类，于是
`skills/decision-card/scripts/budget.py` 里的 `parents[2]`（解析出来正是
`skills/`）被分进了「安全」那堆 —— 按字符串形状分类就是 L-13。

⚠️ 还有一次更贵的：清理孤儿 `import sys` 时，按 ruff 报的**文件名**去删「长得像
`import sys` 的那一行」，而 ruff 指的是**函数体内**第 136 行那个。结果删掉了顶层
那个真正在用的，3 条测试当场失败。**ruff 给了行号，而我用了字符串匹配。**
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

#: `pyproject.toml` 的 `pythonpath` 已经挂好的三条，外加 pytest 自己会把测试文件
#: 所在目录塞进 `sys.path[0]`（prepend 导入模式）⇒ `tests/` 也算已覆盖。
_COVERED = {"skills", "src", "tests", "."}


def _pythonpath_from_pyproject() -> list[str]:
    """🔴 判据的来源必须是**配置本身**，不是这里抄一份。

    抄一份就是 L-3：哪天有人把 `pythonpath` 改短了，这条守卫仍然按老名单
    放行，而被放行的那些此刻已经变成承重的了。
    """
    import tomllib
    d = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return d["tool"]["pytest"]["ini_options"]["pythonpath"]


def _insert_sites(path: pathlib.Path) -> list[tuple[int, str, ast.Call]]:
    """`(行号, 源码, 调用节点)`，只算**真的调用**了 `sys.path.insert` 的。

    用 AST 而不是 `"sys.path.insert" in line` —— 后者会把注释和文档字符串里
    提到这个名字的地方一起算进来（本批写注释时就制造过这样的假站点）。
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    for node in ast.walk(ast.parse(src, filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "insert"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "path"
                and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys"):
            out.append((node.lineno, lines[node.lineno - 1].strip(), node))
    return out


def _resolved_target(path: pathlib.Path, node: ast.Call) -> str | None:
    """这次调用**挂的是哪个目录**（仓库相对路径）。判不出来返回 `None`。

    🔴 判据是解析路径，不是看这行长什么样。第一版就是按「行里有没有
    `"scripts"` 这个词」判的，于是
    `str(REPO / "skills/market-calc/scripts")` —— 整个路径写在**一个**字符串
    字面量里 —— 没命中，被误判成冗余。同一批里这是第三次栽在字符串形状上。
    """
    parts: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            parts.extend(x for x in sub.value.split("/") if x)
        elif (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Attribute)
              and sub.value.attr == "parents" and isinstance(sub.slice, ast.Constant)):
            # `pathlib.Path(__file__).resolve().parents[n]` —— 文件位置已知，
            # 这个能算出来，不必当成「判不出来」
            try:
                base = path.resolve().parents[int(sub.slice.value)]
                return str(base.relative_to(REPO)) if base != REPO else "."
            except (ValueError, IndexError):
                return None
    if not parts:
        return None                     # 动态目标（`str(d)` 之类）
    return "/".join(parts)


def _targets_covered_dir(path: pathlib.Path, node: ast.Call) -> bool:
    """挂的目标是不是**已被 pythonpath / pytest 覆盖**的那几条。

    判不出来（动态目标）⇒ 返回 False。这不是 fail-open：一个动态拼出来的目标
    **按构造就不可能**恰好是 `skills`/`src`/`tests` 这三个静态根之一 ——
    本守卫要拦的是「又把这三条之一挂了一遍」，不是「禁止一切动态路径」。
    """
    t = _resolved_target(path, node)
    return t in _COVERED


# ───────────────────────────────────────────── 守卫的守卫（反向自检）

def test_pythonpath仍然挂着这三条():
    """整条规则建立在它上面。它变了，这条守卫的判据就得跟着变。"""
    assert _pythonpath_from_pyproject() == ["skills", "src", "."], (
        "pyproject 的 pythonpath 变了 —— 本文件「哪些目标算已覆盖」的判据必须同步重审，"
        "否则会继续放行此刻已经变成承重的那些")


def test_扫描范围非空():
    assert len(list(TESTS.glob("test_*.py"))) >= 20, "扫不到测试文件"


def test_这条扫描认调用不认字符串():
    """注释/文档字符串里提到 `sys.path.insert` 不该被算成站点。"""
    sample = ast.parse('# sys.path.insert(0, "x")\n"""sys.path.insert(0, 1)"""\n')
    hits = [n for n in ast.walk(sample) if isinstance(n, ast.Call)]
    assert not hits, "注释与文档字符串里的同名字样被当成了调用"


def test_承重的那些不会被误判():
    """反向断言：指向未覆盖目录的写法必须被放行，否则这条守卫会逼人删掉承重的行。

    最后两条是本批实测的**真实误判**（第一版按字符串形状判，把它们判成了冗余）。
    """
    here = TESTS / "test_x.py"
    for src in ('sys.path.insert(0, str(REPO / "tools" / "verify"))',
                'sys.path.insert(0, str(REPO / "deploy" / "openclaw"))',
                'sys.path.insert(0, str(REPO / "skills/market-calc/scripts"))',
                'sys.path.insert(0, str(d))'):
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "insert")
        assert not _targets_covered_dir(here, node), f"{src} 被误判成冗余"


def test_冗余的那些确实会被抓到():
    here = TESTS / "test_x.py"
    for src in ('sys.path.insert(0, str(REPO / "skills"))',
                'sys.path.insert(0, str(REPO / "src"))',
                'sys.path.insert(0, str(REPO / "tests"))'):
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", "") == "insert")
        assert _targets_covered_dir(here, node), f"{src} 没被抓到"


# ───────────────────────────────────────────── 正题

def test_tests下不许再出现冗余的sys_path_insert():
    offenders: list[str] = []
    for p in sorted(TESTS.glob("*.py")):
        for lineno, line, node in _insert_sites(p):
            if _targets_covered_dir(p, node):
                offenders.append(f"{p.relative_to(REPO)}:{lineno}  {line}")

    assert not offenders, (
        "`tests/` 里又出现了指向**已被覆盖目标**的 sys.path.insert：\n  "
        + "\n  ".join(offenders)
        + "\n\n这三条 `pyproject.toml` 的 pythonpath 已经挂好了（skills / src / .），"
          "\n测试文件再挂一遍不改变任何结果 —— 批 U-III 删掉的就是这一类（56 处）。"
          "\n⚠️ 指向 tools/ · deploy/ · 各 skill 的 scripts/ 的**不在此列**，"
          "那些目录不在 pythonpath 上，是承重的。"
    )
