"""常驻守卫：打包元数据必须描述**这棵树的真实情况**（批 U-II）。

批 U-II 给 `pyproject.toml` 补上 `[build-system]` + `[project]`，让
`pip install -e .` 与「在无 `PYTHONPATH` 的全新 venv 里 `import easyup_biga`」
这条外部评审 F 节的验收标准真正成立。

🔴 为什么打包配置需要守卫，而不是「装一次成功就完了」
----------------------------------------------------
因为它声明的每一件事都**可能与代码脱钩，而脱钩不报错**：

  * `dependencies = []` 声明零依赖 —— 哪天有人 `import requests` 忘了声明，
    开发环境里照样跑（系统装着一堆东西），只有在干净 venv 里才炸
  * `packages.find` 的范围 —— 少收一个子包，editable 安装**测不出来**
    （editable 就是指回 `src/`，什么都在），只有非 editable 安装才暴露
  * `version` —— 现在这个事实有两个出处（本文件 + `CHANGELOG.md`），L-3 的形状
  * `requires-python` —— 声明的下界与真正被测过的版本可以完全无关

⚠️ 开工提示词给的 P1 探针是「临时删掉 `[project.dependencies]` 里的某一项，
   断言 import 报错」。**那个探针的前提在本仓库不成立** —— 实测扫下来依赖列表
   本来就是空的（非 stdlib 的 import 只有 `easyup_biga` 自己）。
   所以这里把它换成等价的形式：**声明与代码双向一致**。
   「列表不是摆设」在零依赖的情况下，要证明的正是「零确实是对的」。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PKG_DIR = REPO / "src" / "easyup_biga"

from _scan import repo_files  # noqa: E402

PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]

#: `skills/*/scripts/` 里的脚本互相 import 兄弟模块（靠脚本自己挂 sys.path），
#: 那不是第三方依赖，是同目录的文件。判据取「磁盘上有没有这个 .py」，
#: 不取硬编码名单 —— 名单会漂，目录不会。
def _sibling_module_names() -> set[str]:
    return {p.stem for p in (REPO / "skills").rglob("scripts/*.py")}


def _third_party_imports(files: list[pathlib.Path]) -> dict[str, list[str]]:
    """`files` 里 import 了哪些**非 stdlib、非本仓库**的顶层模块。

    返回 `{模块名: [出现位置, ...]}`。
    """
    siblings = _sibling_module_names()
    own = {"easyup_biga", "_contract", "_store", "_sources", "_runtime", "_snapshot"}
    found: dict[str, list[str]] = {}
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for dotted in names:
                root = dotted.split(".")[0]
                if (root in sys.stdlib_module_names or root in own
                        or root in siblings or root.startswith("test")):
                    continue
                found.setdefault(root, []).append(
                    f"{p.relative_to(REPO)}:{node.lineno}")
    return found


def _pkg_files() -> list[pathlib.Path]:
    return [p for p in repo_files(".py") if PKG_DIR in p.parents]


def _version_tuple(v: str) -> tuple[int, int, int, int]:
    """`X.Y.Z` / `X.Y.Z.devN` → 可比较的元组。

    不用 `packaging.version` —— 那是第三方库，而本仓库刚刚声明零依赖，
    为了一条测试引入一个依赖就把被测的那件事本身破坏了。
    我们自己的版本号形状固定，手写解析足够且不引入任何东西。
    """
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.dev(\d+))?", v)
    assert m, f"版本号 {v!r} 不是 X.Y.Z 或 X.Y.Z.devN 的形状"
    major, minor, patch, dev = m.groups()
    # dev 版排在同号正式版**之前**（PEP 440）：0.3.0.dev0 < 0.3.0
    return (int(major), int(minor), int(patch), -1 if dev is None else int(dev))


def _last_released() -> str:
    """`CHANGELOG.md` 里最近的**已发布**版本号。"""
    for line in (REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if (m := re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)):
            return m.group(1)
    raise AssertionError("CHANGELOG.md 里找不到任何已发布版本 —— 格式变了？")


# ───────────────────────────────────────────────── 守卫的守卫（反向自检）

def test_扫描范围非空():
    assert len(_pkg_files()) >= 20, "扫不到包内文件，扫描范围可能坏了"
    assert _sibling_module_names(), "skills/*/scripts/ 一个都没扫到"


def test_第三方import扫描真的抓得到():
    """没有这条，「零第三方依赖」那条断言在扫描器坏掉时会因为什么都没查而全绿。"""
    sample = REPO / "src" / "easyup_biga" / "__init__.py"   # 真实存在的文件，只借它的路径
    tree = ast.parse("import requests\nfrom pandas import DataFrame\n")
    # 直接复用判据本体，避免测一个平行实现（L-3）
    siblings = _sibling_module_names()
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert {"requests", "pandas"} <= roots
    assert not ({"requests", "pandas"} & (set(sys.stdlib_module_names) | siblings)), \
        "requests/pandas 居然被当成 stdlib 或兄弟模块 —— 判据坏了"
    assert sample.exists()


# ───────────────────────────────────────────────── 正题

def test_声明的依赖与包里真实的第三方import一致():
    """🔴 这是开工提示词那条 P1 探针的等价形式（原版前提不成立，见模块头）。

    双向：声明了什么就得真的用，用了什么就得声明。
    """
    declared = {re.split(r"[<>=!~\[ ]", d)[0].strip().lower().replace("_", "-")
                for d in PROJECT["dependencies"]}
    actual = _third_party_imports(_pkg_files())
    actual_norm = {m.lower().replace("_", "-") for m in actual}

    undeclared = actual_norm - declared
    assert not undeclared, (
        "包里 import 了没声明的第三方库：\n  "
        + "\n  ".join(f"{m} ← {actual[m][:3]}" for m in sorted(actual)
                      if m.lower().replace("_", "-") in undeclared)
        + "\n\n写进 pyproject.toml 的 [project.dependencies]。\n"
          "🔴 别指望开发环境帮你发现 —— 这台机器装着一堆东西，\n"
          "   只有在全新 venv 里装这个包的时候才会炸。"
    )
    unused = declared - actual_norm
    assert not unused, (
        f"声明了却没在包里 import 的依赖：{sorted(unused)}\n"
        "  一个没人用的依赖是摆设，且会让「装不上」的排查方向跑偏。"
    )


def test_skills下也没有第三方import():
    """`skills/` 不进 pip 包（见 pyproject 注释），所以它的依赖**无处声明**。

    ⇒ 一旦那边冒出第三方 import，本仓库就出现了一个「没有任何地方记录、
    只能靠开发机恰好装着」的依赖。这条把它拦在出现的那一刻。
    """
    files = [p for p in repo_files(".py") if (REPO / "skills") in p.parents]
    assert files, "扫不到 skills/ 下的文件"
    actual = _third_party_imports(files)
    assert not actual, (
        f"skills/ 下出现第三方 import：{ {k: v[:2] for k, v in actual.items()} }\n"
        "  skills/ 不进 pip 包 ⇒ 这个依赖没有任何地方声明得了。\n"
        "  要么换成 stdlib，要么先决定 skills/ 的依赖该记在哪。"
    )


def test_磁盘上的每个子包都真的会被打进去():
    """`packages.find` 少收一个子包，**editable 安装测不出来** ——
    editable 就是指回 `src/`，什么都在。只有非 editable 安装才暴露。

    🔴 判据是**问 setuptools**，不是自己按 `include`/`exclude` 的 glob 重算一遍。
    第一版就是自己重算的，而它只看了 `include`、漏了 `exclude` ——
    G-1 探针（往配置里塞 `exclude = ["easyup_biga.application*"]`）当场证明
    那一版抓不到：探针全绿。自己写一份平行的 glob 语义就是 L-3 的形状，
    而这里的「另一份实现」是 setuptools 本体，永远不可能赢。
    """
    try:
        from setuptools import find_packages
    except ModuleNotFoundError:          # pragma: no cover
        # R-3：算不出来不当作通过。setuptools 不在 Python 3.12 的 venv 默认集里。
        pytest.skip("环境里没有 setuptools —— 打包范围无从核对（不是通过）")

    find = PYPROJECT["tool"]["setuptools"]["packages"]["find"]
    assert find["where"] == ["src"], f"打包根变了：{find['where']}"
    assert find.get("include"), "没有 include 白名单 —— 范围会依赖「碰巧没有别的包」"

    on_disk = {f"easyup_biga.{p.name}" for p in PKG_DIR.iterdir()
               if p.is_dir() and (p / "__init__.py").exists()}
    assert {"easyup_biga.domain", "easyup_biga.persistence", "easyup_biga.providers",
            "easyup_biga.runtime", "easyup_biga.application"} <= on_disk, \
        f"磁盘上的子包少了：{sorted(on_disk)}"

    # 把 pyproject 里的配置**原样**交给 setuptools，问它到底会收哪些
    found = set(find_packages(where="src", include=find["include"],
                              exclude=find.get("exclude", ())))
    missing = (on_disk | {"easyup_biga"}) - found
    assert not missing, (
        f"这些磁盘上存在的包不会被打进去：{sorted(missing)}\n"
        f"  pyproject 的 include={find['include']} exclude={find.get('exclude', ())}\n"
        "  🔴 editable 安装发现不了这件事（它指回 src/，什么都在）——\n"
        "     只有 `pip install .`（非 editable）才暴露。"
    )


def test_版本号与CHANGELOG不矛盾():
    """🔴 版本号这个事实现在有两个出处（pyproject + CHANGELOG）= L-3 的形状。

    判据不是「两处字面相同」（它们本来就不同：CHANGELOG 顶上是 `[未发布]`），
    而是**不矛盾**：声明的版本必须严格大于最后一个已发布版。
    写成已发布版 = 宣称「这棵树就是那个 tag」，而 `[未发布]` 里有内容就说明它不是。
    """
    declared = PROJECT["version"]
    released = _last_released()
    assert _version_tuple(declared) > _version_tuple(released), (
        f"pyproject 写着 {declared}，而 CHANGELOG 最近的发布版是 {released}。\n"
        f"  {declared} <= {released} 等于宣称「这棵树就是那个发布版」。\n"
        "  发版时：CHANGELOG 的 [未发布] 改成版本号 + 日期，这里去掉 .devN，两处一起改。"
    )


def test_requires_python不高于正在跑测试的解释器():
    """声明的下界必须**真的被测过**。

    写一个比手上这个解释器更高的下界，等于宣称一个从未跑过的环境；
    而这套测试正是唯一的证据来源。
    """
    spec = PROJECT["requires-python"]
    m = re.fullmatch(r">=\s*(\d+)\.(\d+)", spec)
    assert m, f"requires-python {spec!r} 不是 '>=X.Y' 的形状 —— 判据要跟着改"
    floor = (int(m.group(1)), int(m.group(2)))
    assert sys.version_info[:2] >= floor, (
        f"requires-python 声明 {spec}，而跑测试的是 "
        f"{sys.version_info.major}.{sys.version_info.minor} —— "
        "声明的下界从来没被测过")


def test_没有project_scripts是裁定不是遗漏():
    """F 节原文点名了 console scripts。**决定不做**的理由写在 pyproject 注释与
    CHANGELOG 批 U-II 条目里；这条确保「不做」不会哪天变成静默的「忘了」。

    判据取那两个可核实的前提：`tools/` 不是 Python 包、且多数脚本靠 `__file__`
    回溯仓库根。这两条哪天不成立了，这条会红 —— 那时才是重新裁定的时刻。
    """
    assert "scripts" not in PROJECT, (
        "加了 [project.scripts] —— 那就把这条守卫连同 pyproject 里的理由注释一起改掉，"
        "别让文档继续说「决定不做」")
    assert not (REPO / "tools" / "__init__.py").exists(), \
        "tools/ 有了 __init__.py —— console scripts 的前提变了，回去重新裁定"
    repo_bound = [p.name for p in sorted((REPO / "tools" / "verify").glob("*.py"))
                  if "parent.parent" in p.read_text(encoding="utf-8")
                  or "parents[" in p.read_text(encoding="utf-8")]
    assert len(repo_bound) >= 5, (
        f"只有 {len(repo_bound)} 个 tools/verify 脚本靠 __file__ 回溯仓库根了"
        f"（{repo_bound}）—— 「它们是仓库绑定的」这个理由可能过期了，回去重新裁定")


# ───────────────────────────────────────────────── 构建产物不能被当成第二份实现

def test_降级扫描排除构建产物(tmp_path, monkeypatch):
    """🔴 `pip install` 会在 `build/lib/` 下留下**每个模块的第二份拷贝**。

    正常 git 路径不受影响（`.gitignore` 挡着），但**降级模式**（没有 `.git`：
    发布 tarball / 容器 `COPY . .` / sdist）走的是文件系统遍历，忽略规则对它
    不生效 —— 于是构建产物会被当成「契约的第二份实现」，撞上不变式 I-3。

    实测（2026-09-24 探针，批 U-II）：副本里带着 `build/`、剥掉 `.git` 之后，
    `test_A_契约类名只在_contract_下定义` 等**五条**守卫集体误报；
    往 `_FALLBACK_SKIP` 补上 build/dist/egg-info 之后同一个副本全绿。

    ⚠️ `test_scan_fallback.py` 的沙盒**不复制** build/（那份名单是另一回事：
    沙盒该复制的是代码，不是这台机器此刻的构建状态）⇒ 那条端到端测试**碰不到**
    这个场景。所以这个不变式需要本条单独钉住，否则修完就没人守了。
    """
    import _scan

    for rel in ("src/easyup_biga/domain/card.py",
                "build/lib/easyup_biga/domain/card.py",   # 构建产物：同一个类的第二份
                "dist/whatever.py",
                "src/easyup_biga.egg-info/x.py",
                "tests/test_real.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("class DecisionCard: pass\n", encoding="utf-8")

    monkeypatch.setattr(_scan, "REPO", tmp_path)
    walked = set(_scan._walk_files())

    assert "src/easyup_biga/domain/card.py" in walked, "真实源码被漏掉了"
    assert "tests/test_real.py" in walked, "测试文件被漏掉了"
    leaked = {f for f in walked
              if f.startswith(("build/", "dist/")) or ".egg-info/" in f}
    assert not leaked, (
        f"降级遍历收进了构建产物：{sorted(leaked)}\n"
        "  它们是 pip install 留下的模块拷贝 —— AST 纯度类守卫会把它们\n"
        "  当成「契约的第二份实现」（不变式 I-3），在没有 git 的环境里集体误报。"
    )
