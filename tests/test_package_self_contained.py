"""常驻守卫：`src/easyup_biga/` 必须自足 —— 包内部不许再走 `skills/` 薄壳。

批 H-I/H-II 把 `_contract`/`_store`/`_sources`/`_runtime`/`_snapshot` 五个包
从 `skills/_*` 迁到了 `src/easyup_biga/{domain,persistence,providers,runtime,
application}`，旧位置留了 re-export 薄壳，好让**存量调用方**一个字符都不用改。

但迁移只搬了「对外接口」，没清理「包内部互相怎么导入」：`persistence` /
`providers` / `application` 内部仍写着 `from _contract import ...` 这种旧写法。
批 U-I 把它们改成了 `from easyup_biga.xxx import ...`。本文件钉住这个状态。

🔴 为什么非要一道守卫，而不是「改完就完了」
-------------------------------------------
因为 **pytest 测不出来**。`pyproject.toml` 的 `pythonpath = ["skills", "src", "."]`
把两条路**同时**挂着，于是旧写法在测试里照常解析成功。

2026-09-24 实测（批 U-I 的 G-1 探针）：把 `persistence/db.py` 一行改回
`from _contract import (`，`tests/test_store.py` 依然 **47 条全绿** ——
而同一时刻，只挂 `src/` 的隔离 import 直接
`ModuleNotFoundError: No module named '_contract'`。

⇒ 这正是「静默 fail-open」（红线 R-3 / `architecture.md` §9）的形状：
   回归全绿，缺陷完好无损地留在原地，等到有人真的在没有 `skills/` 的环境里
   装这个包（批 U-II 的验收标准就是这个场景）才当场炸开。

🔴 为什么判据是 AST，不是 grep
------------------------------
grep `^from _` 两头都不准：

  * **误报** —— 各包 `__init__.py` 的文档字符串里写着用法示例，形状一模一样
  * **漏报** —— `__import__("_contract")` / `importlib.import_module("_store")`
    绕过去了（本仓库在裸 sqlite3 那道守卫上**已经被实测绕过一次**，
    见 `test_no_raw_sqlite.py` 的 `_INDIRECT_IMPORT`）

按字符串形状分类就是 L-13。判据必须是「这里真的发生了一次跨包导入」。
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PKG_DIR = SRC / "easyup_biga"

from _scan import repo_files  # noqa: E402

#: 迁移前的五个包名。包内部再出现它们 = 又绕回薄壳，自足性当场失效。
LEGACY_ROOTS = {"_contract", "_store", "_sources", "_runtime", "_snapshot"}

#: 间接 import 的两个入口 —— 判据是「发生了导入」，不是「长得像 import 语句」。
_INDIRECT_IMPORT = {"__import__", "import_module"}


def _legacy_root_of(dotted: str | None) -> str | None:
    """`dotted` 的**根包**若是迁移前的旧名字，返回它；否则 None。

    `_store.db` 与 `_store` 都算 —— 判的是根，不是全名。
    """
    if not dotted:
        return None
    root = dotted.split(".")[0]
    return root if root in LEGACY_ROOTS else None


def _offenders_in(tree: ast.AST) -> list[tuple[int, str]]:
    """返回 `(行号, 旧包名)`。`ast.walk` ⇒ 函数体内的惰性 import 一样算。"""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (root := _legacy_root_of(alias.name)):
                    found.append((node.lineno, root))
        elif isinstance(node, ast.ImportFrom):
            # 🔴 只看绝对导入。`level > 0` 是相对导入，`from ._freeze import ...`
            #    的 module 就是 `_freeze` —— 下划线开头的**同级兄弟模块**是本仓库
            #    真实存在的写法（`domain/_freeze.py`），按名字形状判会把它误伤。
            if node.level == 0 and (root := _legacy_root_of(node.module)):
                found.append((node.lineno, root))
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _INDIRECT_IMPORT and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if (root := _legacy_root_of(arg.value)):
                        found.append((node.lineno, root))
    return found


def _pkg_files() -> list[pathlib.Path]:
    # 走 git 的口径，不走文件系统 —— 见 `tests/_scan.py` 的「worktree 副本」一节
    return [p for p in repo_files(".py") if PKG_DIR in p.parents]


ALL_FILES = _pkg_files()


# ───────────────────────────────────────────────── 守卫的守卫（反向自检）

def test_扫描范围非空且覆盖到五个包():
    """扫不到文件的扫描器永远全绿 —— 先证明它真的在看东西。"""
    assert len(ALL_FILES) >= 20, f"只扫到 {len(ALL_FILES)} 个 .py，扫描范围可能坏了"
    seen = {p.relative_to(PKG_DIR).parts[0] for p in ALL_FILES if p.parent != PKG_DIR}
    missing = {"domain", "persistence", "providers", "runtime", "application"} - seen
    assert not missing, f"这几个子包一个文件都没扫到：{sorted(missing)}"


def test_这条扫描真的抓得到旧写法():
    """G-1 的常驻版：四种形状都要被抓到，一种都不能漏。

    没有这条，上面那条断言「没有违规」在扫描器坏掉时会**因为什么都没查**而全绿。
    """
    sample = (
        "from _contract import now_cn\n"                 # 顶层 from
        "import _store\n"                                # 顶层 import
        "def f():\n"
        "    from _sources import fetch_pool\n"          # 函数内惰性 from
        "    return __import__('_runtime')\n"            # 间接 import
    )
    hits = _offenders_in(ast.parse(sample))
    assert {root for _, root in hits} == {"_contract", "_store", "_sources", "_runtime"}, (
        f"四种形状没全抓到，只抓到 {hits}"
    )


def test_相对导入下划线兄弟模块不算违规():
    """`domain/_freeze.py` 是真实存在的同级模块 —— 按名字形状判会误伤它。"""
    assert _offenders_in(ast.parse("from ._freeze import deep_freeze\n")) == []
    assert (PKG_DIR / "domain" / "_freeze.py").exists(), (
        "`_freeze.py` 没了 —— 这条反向断言在守一个不存在的误伤场景，"
        "换一个仍然存在的下划线兄弟模块，别直接删掉它"
    )


# ───────────────────────────────────────────────── 正题

def test_包内部不许再走skills薄壳():
    offenders: list[str] = []
    for p in ALL_FILES:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for lineno, root in _offenders_in(tree):
            offenders.append(f"{p.relative_to(REPO)}:{lineno} → {root}")

    assert not offenders, (
        "`src/easyup_biga/` 内部又出现了跨包旧写法：\n  "
        + "\n  ".join(offenders)
        + "\n\n改成 `from easyup_biga.{domain,persistence,providers,runtime,"
          "application} import ...`。\n"
          "🔴 别指望 pytest 帮你发现——`pythonpath` 同时挂着 skills/ 与 src/，\n"
          "   旧写法在测试里照常解析成功，只有真装这个包的时候才炸。"
    )


# ───────────────────────────────────────────────── 隔离 import（本批真正的验收）

def _isolated(stmt: str) -> subprocess.CompletedProcess[str]:
    """在一个**只挂 `src/`、`skills/` 不可达**的进程里跑 `stmt`。

    - `env` 从空白重建 ⇒ 不继承开发环境的 `PYTHONPATH`
    - `cwd` 指向空临时目录 ⇒ `python -c` 塞进 `sys.path[0]` 的那个「当前目录」
      不会把仓库根（连带 `skills/`）带进来
    - `-s` ⇒ 不加载用户 site-packages
    """
    with tempfile.TemporaryDirectory() as td:
        return subprocess.run(
            [sys.executable, "-s", "-c", stmt],
            cwd=td,
            env={"PATH": "/usr/bin:/bin", "HOME": td, "PYTHONPATH": str(SRC)},
            capture_output=True, text=True, timeout=120,
        )


def test_隔离环境里skills确实不可达():
    """守卫的守卫：先证明这个「隔离」是真的。

    🔴 少了这条，下面那条会**因为 skills/ 其实还够得着**而全绿 ——
    测出来的将是「两条路都挂着能 import」，而那本来就成立，等于什么都没测。
    """
    r = _isolated("import _contract")
    assert r.returncode != 0, "隔离环境里居然 import 得到 `_contract` —— 隔离没生效"
    assert "ModuleNotFoundError" in r.stderr, r.stderr


def test_只挂src时五个包都能独立import():
    stmt = (
        "import easyup_biga, easyup_biga.domain, easyup_biga.persistence, "
        "easyup_biga.providers, easyup_biga.runtime, easyup_biga.application\n"
        "import sys\n"
        "assert '_contract' not in sys.modules, '仍然经薄壳解析：' + str(sys.modules['_contract'])\n"
        "print(easyup_biga.__file__)\n"
    )
    r = _isolated(stmt)
    assert r.returncode == 0, (
        "只挂 src/ 时 easyup_biga 装不起来 —— 包内部还有跨包旧写法：\n" + r.stderr
    )
    # 确认装起来的确实是仓库里这份，不是别处（比如将来 pip 装进 site-packages 的）
    assert r.stdout.strip().startswith(str(PKG_DIR)), (
        f"import 到的不是 {PKG_DIR}，而是 {r.stdout.strip()}"
    )


def test_惰性import的那两处在隔离环境里也成立():
    """`providers/tradetime.py` 与 `providers/szse.py` 把存储层写成**函数内**
    惰性 import（让时间数学层/解析层的 import 图保持纯）。

    🔴 惰性 = 导入模块时不执行 ⇒ 上面那条「五个包都能 import」**碰不到它们**。
    得真的把函数调起来，那行 import 才会执行。
    """
    stmt = (
        "import datetime\n"
        "from easyup_biga.providers.tradetime import market_is_open, CN_TZ\n"
        "market_is_open(datetime.datetime.now(CN_TZ))\n"   # 内部惰性 import persistence
        "print('ok')\n"
    )
    r = _isolated(stmt)
    assert r.returncode == 0 and "ok" in r.stdout, (
        "惰性 import 那一行在隔离环境里炸了：\n" + r.stderr
    )
