# 第 50 章 · 让包真的自足（F 节 · 批 U-I）

> 📄 **过程** · 写完即冻结
> **覆盖**：`src/easyup_biga/` 内部跨包旧写法的清理、为什么这件事**全量 pytest
> 测不出来**、守卫的判据为什么必须是 AST 而不是 grep，以及惰性 import 为什么
>需要单独一条测试 ｜
> **不覆盖**：`pyproject.toml` 的 `[build-system]`/`[project]` 正式化与
> `pip install -e .`（批 U-II）、82 处 `sys.path.insert` 的逐一核实（批 U-III）、
> ruff/mypy 基线（批 U-IV）—— 四个子批的边界与先后依赖见
> [`../guide/f-node-packaging-kickoff-prompt.md`](../guide/f-node-packaging-kickoff-prompt.md)

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| 9 个文件的 import 改写 | `persistence`(3) / `providers`(5) / `application`(1) 内部改成 `from easyup_biga.xxx import ...` |
| 1 处 `sys.path` 自举删除 | `providers/tradetime.py` —— 它的唯一消费方就是本批删掉的那行 |
| 4 处文档字符串更正 | 各包 `__init__.py` 的「用法」示例原本只给旧路径 |
| `tests/test_package_self_contained.py` | 7 条守卫：AST 扫描 + 隔离 import + 三条反向自检 |
| 探针 | 2 处 sabotage，其中一处证明「模块级 import 全绿」测不出惰性那行 |

## 为什么这么做

### 1. 迁移搬的是「对外接口」，没搬「内部怎么互相导入」

批 H-I/H-II（第 39 章）把五个包从 `skills/_*` 搬到
`src/easyup_biga/{domain,persistence,providers,runtime,application}`，
旧位置留 re-export 薄壳，好让**存量调用方**一个字符都不用改。那一批的成功
判据是「199 处既有导入照样能跑」——它达成了。

但它没回答另一个问题：**包内部**的文件互相导入时写的是什么？答案是旧写法：

```python
# src/easyup_biga/persistence/db.py
from _contract import (...)          # ← 已经搬走的那个名字
```

这行能跑，全靠 `pyproject.toml` 的 `pythonpath = ["skills", "src", "."]`
**同时**挂了两条路：`_contract` 解析到 `skills/_contract` 薄壳 → 薄壳自己再把
`src/` 挂上 → 最后才拿到 `easyup_biga.domain`。

⇒ 一个绕了三步、且依赖「`skills/` 目录存在」的解析链，藏在一个名字叫
「已经迁移完成」的包里。

### 2. 🔴 这件事的要害不是「不优雅」，是**全量回归测不出来**

先量一下，再说结论。把 `persistence/db.py` 一行改回旧写法，跑消费它的测试：

```
$ sed -i 's/^from easyup_biga.domain import ($/from _contract import (/' \
        src/easyup_biga/persistence/db.py
$ python3 -m pytest tests/test_store.py -q
...............................................                          [100%]
```

**47 条全绿。** 同一时刻，只挂 `src/` 的隔离 import：

```
ModuleNotFoundError: No module named '_contract'
退出码 = 1
```

两个判据看同一份代码，一个说没事，一个说装不起来。而它们的差别只有一件事：
`pythonpath` 有没有同时挂着 `skills/`。

> 通用原则：**当测试环境比真实安装环境「路更宽」时，回归全绿证明的是「在宽路上
> 能跑」，不是「代码是对的」。** 差别不会以失败的形式出现，它以**通过**的形式
> 出现 —— 这正是本仓库最优先防范的静默 fail-open（红线 R-3）。

所以这一批真正的交付物不是那 9 行 import，是那条**只挂 `src/` 的隔离判据**。
9 行 import 是可以再写坏的；判据在，写坏当场就红。

### 3. 为什么顺带删了一处 `sys.path.insert`，而它本该是批 U-III 的活

`providers/tradetime.py` 原来长这样：

```python
# 🔴 批 H-I：…这里要挂上 sys.path 的始终是 skills/（`from _contract import` 经薄壳
#    解析，薄壳再把 src/ 挂上）…
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "skills"))

from _contract import CN_TZ  # noqa: E402
```

那段注释把它的理由写得很清楚：**自举 `skills/` 是为了让下一行解析成功。**
本批把下一行（以及同文件里另一处惰性 `from _store import`）改成
`easyup_biga.*` 绝对导入之后，这段自举**一个消费方都不剩**。

批 U-III 的题目是「82 处 `sys.path.insert` 逐一核实哪些冗余、哪些必需」，
而它明确警告过不要写批量脚本无差别删除 —— 判据得是「这行现在还有没有用」，
不是「有没有这行」（L-13 的另一种形状）。

⇒ 这一处不需要那道判断：**它的唯一用途是同一次编辑删掉的那行。**
留着它反而留下一条**指向已不存在的机制**的注释 —— 而注释说谎比没有注释更糟。
剩下 81 处仍然归 U-III。

### 4. 守卫的判据必须是 AST，不是 grep

开工提示词里给的 grep 是 `^from _\|^import _`。拿它当**守卫**的判据，两头都不准：

| 形状 | grep | 为什么 |
|---|---|---|
| `__init__.py` 文档字符串里的用法示例 | **误报** | 4 处，长得和真 import 一模一样 |
| `from ._freeze import deep_freeze` | **误报** | 相对导入，`module` 就是 `_freeze`；下划线开头的同级兄弟模块是本仓库真实写法 |
| `    from _store import is_trading_day`（函数内惰性） | **漏报** | 行首有缩进，`^from` 不命中 |
| `__import__("_contract")` | **漏报** | 本仓库在裸 sqlite3 那道守卫上**已经被实测绕过一次** |

四种形状里 grep 错三种、漏两种。判据换成 AST 之后，问的是「这里真的发生了一次
跨包导入吗」，四种形状全部可判定：

- `ast.walk` ⇒ 函数体内的惰性 import 一样算
- `ImportFrom.level == 0` ⇒ 相对导入天然排除，不用维护例外名单
- `ast.Call` + 常量参数 ⇒ 间接 import 也算

> 通用原则：按字符串形状分类（L-13）在**举例**时总是够用，在**守卫**里总是不够
> —— 因为守卫面对的是以后所有人会写出的形状，不是今天这几行。

### 5. 惰性 import 需要**单独一条**测试，这不是冗余

很自然会想：既然有了「五个包都能独立 import」这条，惰性那行也在包里，不就被
覆盖了吗？

**没有。惰性 import 的定义就是「导入模块时不执行」。** 模块级的 import 测试
走到 `import easyup_biga.providers` 就结束了，函数体里那行从头到尾没被求值。

这不是推理，是量出来的。只把惰性那行改回旧写法，跑新守卫的 7 条：

```
FAILED  test_包内部不许再走skills薄壳                    ← AST 抓到了
FAILED  test_惰性import的那两处在隔离环境里也成立          ← 真调函数，抓到了
        test_只挂src时五个包都能独立import                ← 全绿，什么都没发现
```

第三条在缺陷完好无损的情况下报绿。⇒ 那条「真的把函数调起来」的测试不是
补充，是**唯一**能在运行时抓到惰性那行的判据。

### 6. 三条反向自检，各堵一个「因为什么都没查而全绿」

| 自检 | 不写它会怎样 |
|---|---|
| 扫描范围非空且覆盖五个子包 | 扫不到文件的扫描器永远全绿 |
| 这条扫描真的抓得到旧写法（四种形状） | 扫描器逻辑写坏时，「没有违规」这句话变成空话 |
| **隔离环境里 `skills` 确实不可达** | 隔离没生效时，测出来的是「两条路都挂着能 import」—— 而那本来就成立 |

第三条最容易漏。少了它，`test_只挂src时五个包都能独立import` 有可能因为
`skills/` 其实还够得着而全绿，那时它验证的是一个恒真命题。

## 执行

改写范围先核实一遍，不照抄提示词里的数字：

```bash
$ grep -rnE "^\s*(from|import) _(contract|store|sources|runtime|snapshot)\b" \
       src/easyup_biga/ --include=*.py | grep -v __init__
src/easyup_biga/persistence/runtime.py:36:from _contract import CN_TZ
src/easyup_biga/persistence/runs.py:26:from _contract import (
src/easyup_biga/persistence/db.py:26:from _contract import (
src/easyup_biga/providers/sina.py:36:from _contract import now_cn
src/easyup_biga/providers/szse.py:55:from _contract import now_cn
src/easyup_biga/providers/szse.py:222:    from _store import save_raw_snapshot, ...
src/easyup_biga/providers/tradetime.py:48:from _contract import CN_TZ  # noqa: E402
src/easyup_biga/providers/tradetime.py:95:    from _store import is_trading_day
src/easyup_biga/providers/eastmoney.py:53:from _contract import now_cn
src/easyup_biga/providers/sina_news.py:60:from _contract import CN_TZ
src/easyup_biga/application/coordinator.py:46:from _contract import ...
src/easyup_biga/application/coordinator.py:47:from _sources import ...
src/easyup_biga/application/coordinator.py:48:from _store import (
```

**3 个包、9 个文件、13 行。** 开工提示词写的是「只改 3 个文件」——
那是把「3 个包」记成了「3 个文件」。范围结论（哪三个包）是对的，数字不是；
按范围执行，不按数字执行。

改完，本批唯一有意义的验收：

```bash
$ TMPD=$(mktemp -d)
$ env -i PATH=/usr/bin:/bin HOME="$TMPD" PYTHONPATH="$PWD/src" \
    python3 -s -c '
import sys, easyup_biga, easyup_biga.domain, easyup_biga.persistence
import easyup_biga.providers, easyup_biga.runtime, easyup_biga.application
print("skills 在 sys.path 里吗 :", any("skills" in p for p in sys.path))
print("_contract 被加载了吗 :", "_contract" in sys.modules)'
skills 在 sys.path 里吗 : False
_contract 被加载了吗 : False
```

`env -i` / `cwd` 指向空目录 / `-s` 三件事各挡一条漏进来的路：继承的
`PYTHONPATH`、`python -c` 塞进 `sys.path[0]` 的当前目录、用户 site-packages。

## 坑

### 1. 基线本来就是红的，而它红在一个跟本批毫无关系的地方

开工提示词第一条要求是「`python3 -m pytest -q` 基线必须全绿」。实测：

```
E  AssertionError: 实测 1645 条，文档里写着 [('README.md', 1642), …]
```

查下来是**上一个提交自己**造成的：它往 `docs/guide/` 加了一份文档，而
`test_docs_convention.py` 有三条检查是按 `docs/**/*.md` 参数化的 ——
每多一份文档就多 3 条 case，1642 + 3 = 1645，而那个提交没跑
`sync_test_count.sh`。

值得记下来的是**怎么确认它与本批无关**：在上一个提交的父提交上开一个干净
worktree 跑全量，得到 1642 且全绿；再对一遍工作区里 18 份未跟踪文档贡献的
54 条 case（`405 - 348 = 57 = 54 + 3`）。三个数字对得上，才敢说「这 3 条来自
那份新文档，不是别处漏掉的测试」。

⇒ 单独一个提交修掉它，再开始本批。理由是 CLAUDE.md 那条：**徽章必须始终描述
一个真被测过的提交**。捎在功能提交里，等于让主分支多留一个「pytest 不全绿」
的提交。

> 通用原则：基线不绿时，先确认它红在哪 —— 「红着也能往下做」和「这红跟我无关」
> 是两句不同的话，只有后者需要被证明。

### 2. 同一棵工作树上有并行会话在写

干活期间 `TODO.md` 出现了一段不是本会话写的内容（与 `DREAMS.md` 同一秒的
mtime）。这不影响代码，但影响**提交卫生**：`git add -A` 会把别人的工作签上
自己的名字。⇒ 按文件逐个 `git add`，提交前 `git diff --cached --stat` 对一遍。

### 3. `import sys` 会变成未使用，`import pathlib` 不会

删掉 `sys.path.insert` 那段之后，`sys` 在 `tradetime.py` 里再无用处，要一起删。
`pathlib` 看着也像只服务于那段（`_REPO_ROOT = pathlib.Path(...)`），但它还在
函数签名的类型注解里用着（`path: pathlib.Path | str | None`）——留下。

顺手删掉的还有 `# noqa: E402`：那个豁免的存在前提是「import 出现在代码之后」，
自举一走，import 回到文件顶部，豁免也就没了对象。这类**因为别处而存在**的标记
在改动周围代码时最容易变成孤儿，而它们不会报错。

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. 本批守卫 7 条
python3 -m pytest tests/test_package_self_contained.py -q
# 预期：7 passed

# 2. 隔离 import —— 本批真正的验收判据
TMPD=$(mktemp -d)
env -i PATH=/usr/bin:/bin HOME="$TMPD" PYTHONPATH="$PWD/src" \
  python3 -s -c 'import easyup_biga.domain, easyup_biga.persistence, \
easyup_biga.providers, easyup_biga.runtime, easyup_biga.application; print("ok")'
# 预期：ok
rm -rf "$TMPD"

# 3. 包内部零跨包旧写法（AST 口径，`grep -v __init__` 是因为文档字符串不算）
grep -rnE "^\s*(from|import) _(contract|store|sources|runtime|snapshot)\b" \
     src/easyup_biga/ --include=*.py | grep -v __init__
# 预期：无输出

# 4. 存量调用方一个字符都没改，照样能跑
#    ⚠️ 必须 PYTHONPATH=skills —— 薄壳在 skills/_contract/，而 `python -c` 只把
#       **当前目录**（仓库根）塞进 sys.path[0]，够不着它。第一次写这条验证时漏了，
#       跑出来是 ModuleNotFoundError，看着像改坏了，其实是命令写错了。
PYTHONPATH=skills python3 -c 'import easyup_biga.domain, _contract
from _contract import now_cn; from _store import connect
from _sources import fetch_pool
from _runtime import OpenClawRuntimeAdapter
from _snapshot import SnapshotCoordinator
print("薄壳仍然有效；身份等同：", _contract.now_cn is easyup_biga.domain.now_cn)'
# 预期：薄壳仍然有效； 身份等同： True

# 5. 全量回归
python3 -m pytest -q
```

第 4 条是这一批的**边界验证**：清理的是包内部，对外接口（薄壳）一步没动。
如果它红了，说明改动越界了 —— 那不是「顺手优化」，是破坏了 H-I 定下的兼容承诺。
实测全仓仍有 **220 处**存量旧写法调用（`skills/` 业务脚本、`tests/`、`tools/`），
它们一个字符都没改 —— 那正是薄壳存在的意义，本批不碰。

⚠️ 顺带一提，第 4 条里的 `is` 断言比「import 没报错」强：薄壳用
`sys.modules[__name__] = 真实模块` 做**身份等同**（第 39 章），
`_contract.now_cn is easyup_biga.domain.now_cn` 为真才说明两条路拿到的是同一个
对象，而不是两份各自 import 出来的副本 —— 后者会是 L-3（同一事实两套实现）。

## 本章要点

| # | 要点 |
|---|---|
| 1 | 迁移「对外接口能跑」和「包自足」是两件事。前者达成不蕴含后者，而前者的判据（回归全绿）对后者**完全无感** |
| 2 | 🔴 测试环境的路比真实安装环境宽时，全绿证明的是「在宽路上能跑」。实测：一行改回旧写法，`test_store.py` 47 条全绿，隔离 import 当场 `ModuleNotFoundError` |
| 3 | 本批真正的交付物是**只挂 `src/` 的隔离判据**，不是那 9 行 import。import 可以再写坏，判据在就当场红 |
| 4 | 删那处 `sys.path.insert` 不属于 U-III：U-III 要判断「这行还有没有用」，而这一处的唯一用途是**同一次编辑删掉的那行**，无需判断 |
| 5 | 注释说谎比没注释更糟 —— 自举那段的注释明写着「为了让下一行解析成功」，下一行没了就得一起走 |
| 6 | 守卫判据必须 AST 不能 grep：文档字符串示例与相对导入下划线兄弟模块会**误报**，函数内惰性 import 与 `__import__("…")` 会**漏报**。四种形状 grep 错三漏二 |
| 7 | 🔴 惰性 import 要单独一条「真把函数调起来」的测试。实测 sabotage：只坏惰性那行，模块级 import 那条**全绿** |
| 8 | 反向自检里最容易漏的是「隔离环境里 `skills` 确实不可达」。少了它，隔离测试可能在验证一个恒真命题 |
| 9 | 基线不绿先定位：查清是上一个提交加文档未同步条数（按 `docs/**/*.md` 参数化，每份文档 +3 条），用「父提交干净 worktree 跑出 1642 全绿」+「未跟踪文档贡献 54 条」两个数字对上才敢说与本批无关 |
| 10 | 提示词写「只改 3 个文件」，实际是 3 个**包** / 9 个文件 / 13 行。范围结论对、数字不对 ⇒ 开工前自己 grep 一遍，按范围执行不按数字执行 |
| 11 | 改动周围代码时，`# noqa`、`import sys` 这类**因为别处而存在**的标记最容易变成孤儿，而它们不报错 |
| 12 | 同一棵工作树有并行会话时，按文件 `git add`，别 `git add -A` —— 否则把别人的工作签上自己的名字 |
| 13 | 验证命令自己也会写错：`python -c` 只把**当前目录**塞进 `sys.path[0]`，验薄壳要 `PYTHONPATH=skills`。漏了就报 `ModuleNotFoundError`，看着像改坏了 —— 「跑不出来就是错的」也包括**命令**是错的 |
| 14 | 验薄壳要断言 `is`（身份等同）而不只是「import 没报错」：薄壳靠 `sys.modules[__name__] = 真实模块` 让两条路拿到**同一个对象**，拿到两份副本就是 L-3 |
