# 第 52 章 · 让这个仓库变成一个装得上的包（F 节 · 批 U-II）

> 📄 **过程** · 写完即冻结
> **覆盖**：`[build-system]`/`[project]` 的落地、五个「不许凭空定」的问题各自
> 怎么核实出来的、为什么 console scripts 是**裁定不做**而不是遗漏，以及
> `pip install` 给这个仓库带来的一个**没人预料到的**副作用 ｜
> **不覆盖**：82+ 处 `sys.path.insert` 的逐一核实（批 U-III，本批一个字没改）、
> ruff/mypy 基线（批 U-IV）；包自足本身见[第 51 章](51-package-self-containment.md)

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `pyproject.toml` | `[build-system]`（setuptools）+ `[project]`，此前**只有** `[tool.pytest.ini_options]` |
| 验收 | 全新 venv、无 `PYTHONPATH`，`pip install -e .` 与 `pip install .` 都装得上 |
| `tests/test_packaging.py` | 9 条：声明 ⇔ 代码双向一致、打包范围、版本不矛盾、构建产物不被当成第二份实现 |
| `tests/_scan.py` | 降级遍历新增排除 `build/`/`dist/`/`*.egg-info` |
| 探针 | 4 处 sabotage，其中一处**当场证伪了我自己刚写的判据** |

## 为什么这么做

### 1. 五个问题，提示词明确要求「去核实，不要凭空定」

| # | 问题 | 核实出来的答案 |
|---|---|---|
| 1 | `skills/` 算不算这个包的一部分 | **不算**，三条独立理由 |
| 2 | 运行时依赖有哪些 | **零个** —— 扫出来的，不是抄来的 |
| 3 | 包名与版本号 | `easyup-biga` / `0.3.0.dev0` |
| 4 | `requires-python` | `>=3.12` |
| 5 | console scripts 落在哪 | **一个都不做**，是裁定不是遗漏 |

下面逐个说依据。

### 2. `skills/` 不进包 —— 三条独立理由，任何一条都够

1. **根本不可能。** `skills/` 下 7 个目录名带连字符（`decision-card`、
   `market-calc`、`news-scan`、`risk-check`、`sector-calc`、`technical-calc`、
   以及 `emotion-calc`）—— 连字符不是合法的 Python 标识符，它们**做不成包**。
2. **没有仓库外的消费方。** `_contract`/`_store`/`_sources`/`_runtime`/`_snapshot`
   五个薄壳的用途，H-I 自己写得很清楚：「让全仓既有的 `from _contract import ...`
   一个字符都不用改」。**全仓** —— 仓库外没有人 import 它们。
3. 🔴 **装进去等于占共享命名空间里的通用名。** `_contract`、`_store`、`_sources`
   会成为任何安装环境的**顶层**模块名。这正是红线 R-2 的形状 ——
   R-2 那张表里三处（nvm bin / systemd 单元名 / 端口）讲的都是同一件事：
   两套东西装在同一个地方，凡是「按约定取默认名」的地方都可能互相顶掉，
   **而顶掉是静默的**。site-packages 是又一个这样的共享命名空间。

> 通用原则：判断「要不要把某个目录打进包里」，先问**仓库外有没有消费方**。
> 没有，就是一个只会污染别人命名空间的内部实现细节。

实测验证（非 editable 安装之后）：

```
  ✅ _contract 未进入全局命名空间
  ✅ _store 未进入全局命名空间
  ✅ site-packages 里没有 skills/ 的任何东西
```

### 3. 零依赖 —— 这个结论是扫出来的

提示词特意写了「不要照抄任何示例项目的依赖列表」。做法是 AST 扫 `src/` 与
`skills/` 下**全部** import，减掉 stdlib（`sys.stdlib_module_names`）、减掉
本仓库自己的名字：

```
非 stdlib 顶层名： ['budget', 'card_ops', 'easyup_biga',
                    'entry_guard', 'feishu_deliverer', 'risk_check']
```

`easyup_biga` 是自己；另外五个逐个 `find` 过去，全是
`skills/*/scripts/` 里的**兄弟模块**（那些脚本自己把所在目录挂上 `sys.path`）。
⇒ `dependencies = []`。网络采集走 stdlib 的 `urllib.request`，没有 `requests`。

🔴 **这让提示词给的 P1 探针前提不成立。** 原文是「临时删掉
`[project.dependencies]` 里的某一项，断言 import 报错（证明依赖列表不是摆设）」
—— 没有项可删。

但探针要证明的那件事仍然成立，只是方向反了：零依赖的情况下，
「列表不是摆设」要证明的是**零确实是对的**。⇒ 换成**双向一致**的常驻判据：
声明了什么就得真的用，用了什么就得声明。sabotage 是往包里塞一行
`import requests`，断言守卫报红、且干净 venv 里真的 `ModuleNotFoundError`。

> 通用原则：探针的**前提**不成立时，不要硬跑那个动作，也不要跳过 ——
> 回到它想证明的那句话，为这个前提重新设计一次。

### 4. 版本号写 `0.3.0.dev0`，不写 `0.2.0`

最后一个发布版是 `0.2.0`（`v0.2.0` tag 真实存在），而 `CHANGELOG.md` 的
`[未发布]` 里堆着大量内容 —— HEAD 远在那个 tag 之后。

写 `0.2.0` 等于宣称「这棵树就是那个 tag」，**是假的**。这跟本仓库反复强调的
那条是同一件事：数字必须描述一个真实状态（第 51 章开头那个测试条数徽章，
栽的也是这个）。`.dev0` 是 PEP 440 的开发版记号，明确表示「在 0.2.0 之后、
尚未发布」。

⚠️ 但这么一写，**版本号这个事实就有了两个出处**（`pyproject.toml` +
`CHANGELOG.md`）—— 标准的 L-3 形状。所以配一条守卫，判据不是「两处字面相同」
（它们本来就不同，CHANGELOG 顶上是 `[未发布]`），而是**不矛盾**：
声明的版本必须严格大于最后一个已发布版。

### 5. `requires-python = ">=3.12"` —— 宁可窄，不可未经测试

代码里找不到任何 3.11/3.12 专属语法（无 `match`、无 `tomllib`、
无 `datetime.UTC`、无 `itertools.batched`），所以**技术上**大概支持更低版本。

但这台机器只有 3.12.3，仓库没有 CI，没有任何其他版本被跑过。
写 `>=3.10` 是一句**从未被验证过的宣称** —— 而这套测试是唯一的证据来源。

⇒ 写 `>=3.12`，并配一条守卫：声明的下界不许高于正在跑测试的解释器。
放宽它是一次**可测的改动**（在 3.10/3.11 上跑一遍全量），不是一次猜测。

### 6. console scripts：不做，而且把「为什么不做」变成可核实的判据

F 节原文点名了这一项，提示词也说「如果决定不做，把理由写进 CHANGELOG，
不要静默跳过」。核实下来的事实：

| 事实 | 数据 |
|---|---|
| `tools/` 与 `tools/verify/` 是 Python 包吗 | **都没有 `__init__.py`** |
| 脚本是不是仓库绑定的 | 10 个里 **8 个**靠 `__file__` 回溯仓库根，再 `sys.path.insert(仓库根/"skills")` |
| `main()` 的形状合不合适 | 合适 —— 9 个都是 `main(argv=None) -> int` |

形状是合适的，**语义不合适**：这些脚本被设计成「在一份 checkout 里跑」。
非 editable 安装之后 `__file__` 落在 site-packages，它们回溯出来的「仓库根」
根本不存在。`isolation.py` 尤其明显 —— 它检查的是**这台机器这个仓库**的隔离
状态，一个能在任何目录敲的全局命令在语义上就是错的。

要做成 console script 只有两条路，都不属于本批：把它们搬进
`src/easyup_biga/`（那是 H-I/H-II 形状的重构，需要独立设计依据），
或者把 `tools` 这个极通用的顶层名塞进 site-packages（**又是 R-2 的形状**）。

🔴 所以这一项配了一条守卫，钉的不是「没有 scripts」，而是**那两个前提**：
`tools/` 仍然不是包、且多数脚本仍然仓库绑定。哪天这两条不成立了，守卫会红 ——
**那时才是重新裁定的时刻**，而不是让一条「决定不做」的注释永远躺在那里。

> 通用原则：把「决定不做」写成守卫时，判据要取**当初支撑这个决定的前提**，
> 不是取决定的结果。钉结果只能防止别人改；钉前提才能在**世界变了**的时候提醒你。

## 坑

### 1. 🔴 探针当场证伪了我自己刚写的判据

写完「五个子包都在打包范围内」那条守卫，自己按 `include` 的 glob 重算了一遍。
跑 G-1 探针（往配置里塞 `exclude = ["easyup_biga.application*"]`）时：

```
── ③ 常驻守卫抓到了吗 ──
（无输出 —— 9 条全绿）
```

**探针全绿。** 我那一版只看了 `include`，完全没看 `exclude`。

修法不是「补上 exclude 的处理」，而是**别自己重算**：把 pyproject 里的配置原样
交给 `setuptools.find_packages()`，问它到底会收哪些。自己写一份平行的 glob 语义
就是 L-3 的形状，而这里的「另一份实现」是 setuptools 本体 —— 永远不可能赢。

改完再跑同一个探针：

```
E  AssertionError: 这些磁盘上存在的包不会被打进去：['easyup_biga.application']
```

> 通用原则：守卫要判断「某个工具会怎么做」时，**去问那个工具**，
> 不要照着它的文档再实现一遍。

### 2. 陈旧的 `build/` 会让配置改动**看起来没生效**

同一个探针，第一次跑非 editable 安装时 `application` 照样装进去了 ——
一度以为是 pip 的 wheel 缓存，加了 `--no-cache-dir` 仍然如此。

真正的原因是上一次 editable 安装留下的 `build/` 与 `src/*.egg-info/`：
setuptools 复用了陈旧的 `build/`。⇒ 改了打包配置要先 `rm -rf build dist *.egg-info`
再验证，否则你验的是上一次的构建结果。

### 3. 🔴 `pip install` 会让「契约只有一份实现」这条不变式误报

这是本批最意外的一个发现，而且**只在降级模式下出现**。

`pip install` 在 `build/lib/easyup_biga/` 下留下**每个模块的第二份拷贝**。
正常路径不受影响 —— 扫描器走 `git ls-files`，`.gitignore` 挡着。
但扫描器有一条**降级路径**：没有 `.git` 时退化成文件系统遍历
（为「发布 tarball / 容器 `COPY . .` / sdist」准备的），**忽略规则对它不生效**。

实测探针：把仓库连 `build/` 一起复制到一个没有 `.git` 的目录，跑那几道纯度守卫：

```
FAILED tests/test_contract_single_impl.py::test_A_契约类名只在_contract_下定义
FAILED tests/test_contract_single_impl.py::test_C_不许手搓字典版契约
FAILED tests/test_contract_single_impl.py::test_契约对象只从_store取_不自己解JSON列
FAILED tests/test_no_raw_sqlite.py::test_store之外不许import_sqlite3
FAILED tests/test_no_raw_sqlite.py::test_store之外不许调用sqlite3_connect
```

**五条集体误报**，其中第一条正是不变式 I-3。而从开发机 `COPY . .` 进容器，
恰好就会把 `build/` 带进去。

⚠️ 这个坑**是 U-II 自己造出来的**：在此之前 `build/` 在本仓库没有理由存在；
U-II 之后 `pip install -e .` 是一条被写进文档的常规操作。

修在 `tests/_scan.py` 的降级黑名单里，不是修在某条测试上。区别很重要：
另一处改动把 `build/` 从**沙盒测试的复制清单**里排掉了，那让那条端到端测试
不再复现这个场景 —— 但**扫描器本身仍然是错的**。⇒ 两件事都要做，
且因为沙盒不再复现，这个不变式需要一条**单独的**测试钉住，否则修完就没人守了。

### 4. 并行会话撞树（第三次），这次撞的是一次进行中的 merge

干到一半，`main` 的工作区变成了另一个会话**正在做的 merge**：5 处冲突未解决
（CHANGELOG / CLAUDE / README / review-prompt / 教程索引 —— 正是那组经典冲突
文件），外加它带进来的章节与本仓库已有的章节**同号**。

处理：**不去解别人的 merge**（第 48 章那次事故就是这个形状），把本批挪进独立
worktree 提交。这是仓库自己的先例（第 35 章「并行撞树第二次 ⇒ worktree 隔离」）。

🔴 **一个号最后是被三方同时认领的**，而这件事只有在真的去合的时候才看得见：

| 认领方 | 当时的号 | 最终 |
|---|---|---|
| 批 2 的溯源章 | 50 | **50**（先落地） |
| 批 U-I 的包自足章 | 50 → 被改成 51 | **51** |
| 本章（批 U-II） | 51 | **52** |
| batch3 的章 | 51 | 53（合并时再改） |

判决规则是仓库自己的先例（第 36 章）：**按落地先后**，后落地的改号；
同时落地时比「嵌入引用数」，少的那方改（改起来便宜）。

⚠️ 真正的教训不是「撞了号」，而是**撞车只在合并那一刻才可见**：
四个会话各自 `ls docs/tutorial/ | tail -1` 都看到一个空号，各自都是对的。
dry-run merge（`git merge --no-commit`）是唯一能提前看见它的动作 ——
本章这次就是先 dry-run 出 `教程序号不连续：[..., 50, 51, 51]` 才动手改的，
不是合完发现红了再回头改。

## 验证

```bash
cd <这个 worktree>

# 0. 先清构建产物，否则你验的是上一次的结果（见「坑 2」）
rm -rf build dist src/*.egg-info

# 1. F 节验收标准原文：全新 venv、无 PYTHONPATH
python3 -m venv /tmp/biga-pkg-test
/tmp/biga-pkg-test/bin/pip install --no-cache-dir -e .
cd /tmp && env -u PYTHONPATH /tmp/biga-pkg-test/bin/python -c \
  'import easyup_biga; print(easyup_biga.__file__)'
# 预期：打印出这个 worktree 里 src/easyup_biga/__init__.py 的路径

# 2. 更严的那一半：非 editable 安装才验得到打包范围
python3 -m venv /tmp/biga-pkg-real
/tmp/biga-pkg-real/bin/pip install --no-cache-dir .
cd /tmp && env -u PYTHONPATH /tmp/biga-pkg-real/bin/python -c \
  'import easyup_biga.application, easyup_biga.runtime; print("五个子包都在")'

# 3. skills/ 确实没被装进去（裁定 1）
/tmp/biga-pkg-real/bin/python -c \
  'import _contract' 2>&1 | tail -1
# 预期：ModuleNotFoundError: No module named '_contract'

# 4. 本批守卫
python3 -m pytest tests/test_packaging.py -q       # 预期 9 passed

# 5. 本批一个 sys.path.insert 都没改（P2）
git diff 0e3cc07 --unified=0 -- '*.py' | grep -cE "^[-+].*sys\.path\.insert"
# 预期：0

# 6. 全量回归
python3 -m pytest -q
```

第 2 条与第 1 条不能互相替代：**editable 安装测不出打包范围**
（它就是指回 `src/`，什么都在），只有非 editable 才暴露。

## 本章要点

| # | 要点 |
|---|---|
| 1 | 判断目录要不要进包，先问**仓库外有没有消费方**。`skills/` 三条理由任一都够：连字符目录做不成包、没有仓库外消费方、装进去等于占 site-packages 这个共享命名空间的通用名（R-2 的形状） |
| 2 | 依赖列表是**扫出来的**不是抄的。AST 扫全部 import 减掉 stdlib 与自己人，结论是**零依赖** |
| 3 | 🔴 探针的**前提**不成立时（零依赖 ⇒ 没有项可删），既不硬跑也不跳过，回到它想证明的那句话重新设计：换成「声明 ⇔ 代码双向一致」 |
| 4 | 版本号写 `0.3.0.dev0` 不写 `0.2.0` —— 写已发布版等于宣称「这棵树就是那个 tag」。代价是版本号有了两个出处（L-3），配守卫判「不矛盾」而非「字面相同」 |
| 5 | `requires-python` 宁可窄：代码看着支持更低版本，但那是**未经测试的宣称**。放宽是可测的改动，不是猜测 |
| 6 | 🔴 把「决定不做」写成守卫时，判据取**当初支撑这个决定的前提**（`tools/` 不是包、脚本仓库绑定），不取结果。钉结果只防别人改，钉前提才能在世界变了时提醒你 |
| 7 | 🔴 **探针证伪了我自己刚写的判据** —— 按 glob 重算的那版只看 `include` 漏了 `exclude`，sabotage 全绿。修法是**去问 setuptools**，不是补写 glob：平行实现里对方是工具本体，永远不可能赢 |
| 8 | 陈旧的 `build/` 会让配置改动看起来没生效（不是 pip 缓存，`--no-cache-dir` 也救不了）。改打包配置先 `rm -rf build dist *.egg-info` |
| 9 | 🔴 `pip install` 造出 `build/lib/` 这**第二份模块拷贝**，降级扫描（无 git：tarball / 容器 COPY）会把它当成「契约的第二份实现」，**五条守卫集体误报**。这是 U-II 自己造的坑 |
| 10 | 修在**扫描器**里，不是修在测试的复制清单里。把场景从沙盒里排掉 ⇒ 那条端到端测试不再复现它 ⇒ 这个不变式必须另有一条单独的测试钉住，否则修完就没人守了 |
| 11 | editable 安装**测不出打包范围**（指回 `src/`，什么都在）。`pip install .` 才是验收 |
| 12 | 并行撞树第三次，这次撞上一次**进行中的 merge**。不去解别人的 merge（第 48 章事故的形状），挪进独立 worktree —— 仓库第 35 章的先例 |
| 13 | 🔴 同一个章节号被**三方**认领，而撞车**只在合并那一刻可见** —— 各会话各自 `ls | tail -1` 都看到空号，各自都没错。`git merge --no-commit` 的 dry-run 是唯一能提前看见它的动作，本章这次就是先 dry-run 出「序号不连续」才动手的 |
