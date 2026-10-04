# 第 54 章 · 接上 lint，拿一个**有人会看**的基线（F 节 · 批 U-IV）

> 📄 **过程** · 写完即冻结
> **覆盖**：ruff / mypy 接入、规则集与 `line-length` 怎么**量**出来而不是拍出来、
> 基线数字与它们的根因、以及顺手修掉的一个真 bug ｜
> **不覆盖**：清空历史违规（本批明确**不承诺**）、130 处 `sys.path.insert`
> （批 U-III）；打包配置本身见[第 52 章](52-packaging-metadata.md)

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `[tool.ruff]` / `[tool.mypy]` | 落在 `pyproject.toml`，不新开 dotfile |
| `[project.optional-dependencies].dev` | `ruff` / `mypy` —— **不是**运行时依赖 |
| ruff 基线 | **169** 条（124 条可自动修） |
| mypy 基线 | **35** 条 / 9 个文件（共检查 32 个） |
| 真 bug 修掉 | 3 处 `F821`：注解引用了本文件从未 import 的 `datetime` |

## 为什么这么做

### 1. 范围为什么刻意收窄成「拿基线」

一个跑了这么久的仓库第一次接 lint/type 检查，大概率炸出几十到几百条历史违规。
如果这一批的目标是「清零」，范围会失控到覆盖全仓大部分文件 ——
而那跟 F 节本身（打包与依赖清理）已经不是同一件事了。

⇒ 只加配置 + 记一个数。**不承诺清零。**

### 2. 🔴 `line-length` 是本批唯一真正重要的决定

先看两个数,差别全在这一个配置上:

| 配置 | ruff 报出 |
|---|---|
| ruff 默认 `line-length = 88` | **741** 条 |
| 本批采用 `line-length = 100` | **169** 条 |

741 里有 **603 条是 E501（行太长），占 81%**。而其中 **63% 含中文**。

原因是个容易漏的细节：**ruff 的 E501 按「显示列宽」算，不按字符数。**
中日韩字符占两列 ⇒ 88 列 ≈ 44 个汉字。本仓库大量中文注释与文档字符串，
于是「这行注释比 44 个汉字长」被报了 383 次。

> 🔴 一份没人看的 lint 配置比没有 lint 更糟：**它训练人忽略输出。**
> 而一旦养成「ruff 报的都是噪音」的习惯，真问题混在里面也一样会被划过去。

所以这个值不能拍，要量。实测全仓 36852 行的**列宽**分布：

```
  p50 = 43   p90 = 79   p98 = 88   p99 = 91   p99.9 = 101   max = 114

  limit= 88 ⇒ 超限 616 行 (1.67%)
  limit=100 ⇒ 超限  37 行 (0.10%)
  limit=120 ⇒ 超限   0 行 (0.00%)
```

⇒ **仓库的事实约定就是 100 列左右**，不是 88。取 100 得到 37 条，
是一份看得完、也改得动的清单；取 120 则一条都不报 —— 那等于没接这条规则。

⚠️ 想收紧到 88 是一次**独立的、范围明确的**排版工作，不该藏在「接入 lint」里。

> 通用原则：给一个已有代码库设阈值时，先量它**现在**的分布。
> 阈值的作用是标出异常值，不是宣布整个仓库都是异常值。

### 3. 为什么只开 `E`/`F`/`I`

`F`（pyflakes）报的是**真问题**——未使用的导入、未定义的名字、重复定义。
`E`（pycodestyle 错误）与 `I`（import 排序）是低争议的基础项。

不开 `B`/`UP`/`SIM`/`ANN` 等：那些引出的是**写法偏好**（「这里该用推导式」
「`Optional[X]` 该写成 `X | None`」），一开就是几百条，而每一条都需要一次
「我们仓库到底要不要这么写」的讨论。那是另一件事。

### 4. 工具放 `optional-dependencies`，不放 `dependencies`

第 52 章给 `[project.dependencies]` 立了一条守卫：**声明 ⇔ 代码双向一致**
（声明了就得真用，用了就得声明）。ruff/mypy 谁也不会被包里的代码 `import`
—— 写进 `dependencies` 会当场违反那条守卫，而且那也确实是假的：
**不装 ruff 照样能跑整套系统**。

⇒ `[project.optional-dependencies].dev`，`pip install -e ".[dev]"`。

配置同理放进 `pyproject.toml` 而不是新开 `ruff.toml` + `mypy.ini` 两个 dotfile
—— 第 52 章刚把 `pyproject.toml` 立成项目配置的家，没有理由马上再分出去两份。

## 顺手修掉的一个真 bug：3 处 `F821`

提示词说「如果发现其中有一条**明显是真 bug**，单独修掉并写清楚」。有一条：

```
src/easyup_biga/domain/verdict.py:442:40:    F821 Undefined name `datetime`
src/easyup_biga/providers/eastmoney.py:114:  F821 Undefined name `datetime`
src/easyup_biga/providers/sina.py:99:        F821 Undefined name `datetime`
```

三处都是这种形状 —— 注解里用了 `datetime`，而**文件从头到尾没有 import 它**：

```python
def max_age_at(self, evaluated_at: datetime) -> int:   # ← datetime 哪来的？
```

它们没炸，是因为这些模块都有 `from __future__ import annotations` ⇒
注解是**字符串**，从不求值。

🔴 **但要说清楚它到底坏在哪，不要说过头**（第 47 章的教训：潜伏 ≠ 正在发生）。
实测：

```
>>> typing.get_type_hints(v.AgentVerdict.max_age_at)
NameError: name 'datetime' is not defined          ← 真的解析不开
>>> from easyup_biga.domain import AgentVerdict     ← 普通 import 与构造完全不受影响
```

⇒ 结论是**潜伏**：任何要解析注解的东西（`get_type_hints`、
某些序列化/校验库、IDE 的类型推断）碰到就会炸，而今天仓库里没有这样的调用方。
修法是三行 `from datetime import datetime`，改完 `get_type_hints` 就能解出
`datetime.datetime | None`。

## 基线：数字，以及数字背后的根因

**光给一个总数没有用。** 基线要能让人判断「这堆东西值不值得动」，
就得说清楚它们是不是同一个根因。

### ruff：169 条

| 规则 | 条数 | 是什么 |
|---|---|---|
| `I001` | 90 | import 没排序。**100% 可自动修** |
| `E501` | 36 | 超过 100 列的真·长行 |
| `F401` | 28 | 未使用的导入。**可自动修**，且与批 U-III 直接相关（见下） |
| `E702` | 4 | 一行里用分号写了多条语句 |
| `F541`/`E402`/`E401`/`F811`/`F841` | 各 2 | f-string 没占位符 / import 不在文件顶部 / 一行多个 import / 重复定义 / 变量赋了不用 |
| `E741` | 1 | 变量名有歧义（`l`/`I`/`O`）|

按目录：`tests/` 96 · `skills/` 44 · `src/` 20 · `tools/` 9。共 **124 条可自动修**。

### mypy：35 条 / 9 个文件

| 错误码 | 条数 | 根因 |
|---|---|---|
| `arg-type` | 23 | 见下两个桶 |
| `attr-defined` | 11 | **同一个根因** |
| `return-value` | 1 | 一个 tuple 的元素类型比声明的宽 |

🔴 **那 11 条 `attr-defined` 全是同一句话**：`"MissingItem" has no attribute "code"`。
看着吓人 —— `.code` 正是 `MissingItem` 的身份信号。但它不是 bug：

```python
class MissingItem(str):
    __slots__ = ("code",)
    def __new__(cls, detail, code=LEGACY_CODE):
        obj = super().__new__(cls, detail)
        object.__setattr__(obj, "code", code)   # ← 只在这里赋值，没有类级注解
```

属性在运行时确实存在，mypy 只是**看不见它被声明过**。补一行 `code: str` 注解
能一次消掉 11 条 —— 但那是改契约代码，属于另一次有明确范围的工作，不是本批。

`arg-type` 的 23 条也集中在两个桶里：

- **9 条**是 `list[X]` 传给期望 `tuple[X, ...]` 的参数 —— 契约类冻结成 tuple，
  构造时由 `deep_freeze` 归一。**构造函数实际接受的比它的注解说的宽**，
  诚实的修法是把注解放宽成 `Sequence[X]`。
- **8 条**是 `int(cur.lastrowid)`，typeshed 把 `lastrowid` 标成 `int | None`。
  ⚠️ 有意思的是 mypy 指着的正是**教程第 34 章记过的那件事**：
  「`ON CONFLICT DO NOTHING` 冲突时 `lastrowid` 不可靠 ⇒ 看 `rowcount`」。
  这 8 处都是普通 INSERT，今天不会取到 `None` —— 但这条规则值得留着。

## 坑

### 1. 用 `index()` 找 TOML 表头，劈掉了一行注释

往 `pyproject.toml` 插配置时，拿 `s.index("[tool.pytest.ini_options]")` 定位 ——
**匹配到了注释里的那个字符串**：

```
# 在此之前本文件**只有** [tool.pytest.ini_options] —— `pip install -e .` 这条
```

于是新配置被插进这行注释的中间，把它劈成两半，`tomllib` 当场解析失败。

第二个问题跟着来：`[project.optional-dependencies]` 我插在了 `dependencies = []`
之后 —— 而它后面还有 `classifiers = [...]`，那些键因此**变成了新表的成员**。

> 通用原则：改结构化配置文件要**锚定行首**（`line.startswith("[tool.x]")`），
> 且新表头只能加在**当前表的所有键之后**。这两条都是 TOML 的结构规则，
> 而按子串查找完全看不见结构。
>
> 便宜的兜底：改完立刻 `tomllib.loads()` 解析一次。两次错误都是它当场报出来的。

### 2. 基线要在**最终会发布的那棵树**上量

第一次量完（741 → 169）之后，`batch3` 落地了。它加了代码，基线就变了
（ruff 169 → 169，`I001` 89 → 90）。变化很小，但**先量后合再报旧数**是错的 ——
那个数没人能复现出来，正是第 51 章「测试条数徽章」栽过的同一个形状。
⇒ 把 `u-four` 快进到含 `batch3` 的 `main` 之后**重新量了一遍**，本章报的是那一次。

## 验证

```bash
cd <仓库根>
python3 -m venv /tmp/biga-lint && /tmp/biga-lint/bin/pip install -e ".[dev]"

# 1. 配置本身有效（本批要证明的就是这个，不是「零违规」）
/tmp/biga-lint/bin/ruff check --no-cache --statistics .
# 预期：正常跑完并列出统计，不是配置报错退出；总数 169 上下

/tmp/biga-lint/bin/mypy --no-incremental src/
# 预期：Found 35 errors in 9 files (checked 32 source files)

# 2. 那条真 bug 修好了
/tmp/biga-lint/bin/ruff check --no-cache --select F821 .
# 预期：All checks passed!

python3 -c "
import sys; sys.path.insert(0,'src')
import typing, easyup_biga.domain.verdict as v
print(typing.get_type_hints(v.AgentVerdict.max_age_at)['evaluated_at'])"
# 预期：<class 'datetime.datetime'>（修之前是 NameError）

# 3. 配置文件结构没坏
python3 -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print('TOML OK')"

# 4. 全量回归
python3 -m pytest -q
```

⚠️ 第 1 条的预期**不是「零违规」**。本批交付的是「配置能跑 + 一个记录在案的数」。
哪天有人把它降到 0，那是另一批的成果，不是这一批的验收标准。

## 本章要点

| # | 要点 |
|---|---|
| 1 | 第一次接 lint，目标是**拿基线**不是清零。目标若定成清零，范围会失控到覆盖全仓 |
| 2 | 🔴 `line-length` 是本批唯一重要的决定：默认 88 报 741 条，取 100 报 169 条，差别全在这一个值 |
| 3 | 🔴 ruff 的 E501 按**显示列宽**算，中日韩字符占两列 ⇒ 88 列 ≈ 44 个汉字。中文注释多的仓库用默认值会被淹掉 |
| 4 | 阈值要**量**不能拍：实测列宽 p99.9=101、max=114 ⇒ 仓库事实约定就是 100。阈值的作用是标出异常值，不是宣布整个仓库是异常值 |
| 5 | 🔴 **一份没人看的 lint 配置比没有更糟** —— 它训练人忽略输出，真问题混在噪音里一样被划过去 |
| 6 | 只开 `E`/`F`/`I`。`B`/`UP`/`SIM` 引出的是「写法偏好」，每条都要一次团队讨论，那是另一件事 |
| 7 | ruff/mypy 放 `optional-dependencies` —— 放 `dependencies` 会当场违反第 52 章那条「声明⇔代码双向一致」守卫，而且那也确实是假的：不装照样能跑 |
| 8 | 修了一个真 bug：3 处注解引用了本文件没 import 的 `datetime`。🔴 **但要说清它潜伏而非正在发生** —— `get_type_hints` 实测 `NameError`，普通 import 与构造完全不受影响 |
| 9 | 基线光给总数没用。35 条 mypy 里 **11 条是同一个根因**（`MissingItem` 用 `object.__setattr__` 赋值、没有类级注解），说清根因才知道值不值得动 |
| 10 | mypy 的 8 条 `int(lastrowid)` 指着的正是**第 34 章记过的那件事**（`ON CONFLICT` 时 `lastrowid` 不可靠）—— 今天不会取到 `None`，但规则值得留着 |
| 11 | 🔴 改结构化配置要**锚定行首**：`index("[tool.x]")` 匹配到了注释里的同名字符串，把注释行劈成两半；新表头还只能加在当前表所有键**之后**，否则后面的键会变成新表的成员。改完立刻 `tomllib.loads()` 一次，两次都是它当场抓到的 |
| 12 | 基线要在**最终会发布的那棵树**上量。先量后合再报旧数，那个数没人复现得出来 —— 与第 51 章测试条数徽章同一个形状 |
