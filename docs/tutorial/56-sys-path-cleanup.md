# 第 56 章 · 删路径操作,和「全绿是因为别处兜住了」(F 节 · 批 U-III)

> 📄 **过程** · 写完即冻结
> **覆盖**：126 处 `sys.path.insert` 的分类判据、为什么提示词给的判据**对测试
> 文件不成立**、删掉的 56 处与保留的 70 处各自的理由 ｜
> **不覆盖**：薄壳退役(69 处被它钉住,记进 `TODO.md` 留给下一批)、
> 打包本身见[第 52 章](52-packaging-metadata.md)、lint 见[第 54 章](54-lint-baseline.md)

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| 删除 | `tests/` 里 **56 处**冗余 `sys.path.insert` + 被删出来的 **24 个**孤儿 import |
| 保留 | **70 处**,每一类都有具体理由(见下表) |
| `tests/test_test_path_bootstrap.py` | 6 条守卫,判据是**解析后的路径**不是行的形状 |
| 探针 | 把一处删掉的加回去 ⇒ 守卫红、而那个测试文件自己**全绿** |

## 为什么这么做

### 1. 🔴 提示词给的判据,对测试文件不成立

原文:「删除前先跑一次它所在的测试确认能跑,删除后再跑一次确认仍然能跑」。

问题在于 `sys.path.insert` 是**进程级全局副作用**,而 pytest 把所有测试模块
导进**同一个进程**。实测(都清空 `pythonpath`):

| 跑法 | 结果 |
|---|---|
| `test_store.py` 单独 | `ModuleNotFoundError: No module named '_contract'` |
| 加上字母序第一个自举的文件(`test_agent_registry.py`) | **70 条全过** |

`test_store.py` 自己**一行 `sys.path.insert` 都没有** —— 它靠的是别的文件在
import 期把 `skills/` 塞进了 `sys.path`。

⇒ **删一处再跑全量必然全绿**,因为别处兜住了。那个判据测的是**这一堆**的性质,
不是**这一行**的性质。

> 通用原则:判断「这行还有没有用」时,先问**它的作用域有多大**。
> 进程级副作用不能用「跑一遍还绿吗」来判 —— 必须让它**单独待在一个进程里**。

这和第 43 章(动态同名模块 monkeypatch 依赖收集顺序)、第 51 章(pytest 路更宽
所以测不出跨包旧写法)是同一个形状,**第三次**了。

### 2. 真正的分界线:谁在跑它

按「解析后的目标目录」重新分类 126 处(⚠️ 不是按行里有没有某个词,见坑 1):

| 谁在跑 | 处数 | 有没有 `pythonpath` | 结论 |
|---|---|---|---|
| `tests/` —— 只经 pytest | 84 | **有**(`["skills","src","."]`) | 指向这三条的冗余 |
| `skills/` · `tools/` · `deploy/` —— `python3 x.py` 直接跑 / 子进程 | 42 | **没有** | 全部承重 |

决定性的两条核实:`tests/` 下**没有任何 `__main__` 入口**,也**没有任何调用方**
用 `python3 tests/...` 的方式跑它们 ⇒ 测试文件只经 pytest 这一条路。

再把 `tests/` 那 84 处拆开 —— 只有挂**已覆盖目标**的才冗余:

- **删 56 处**:目标是 `skills` / `src` / `tests`。`pythonpath` 已经挂好前两条,
  pytest 自己会把测试文件所在目录塞进 `sys.path[0]`(prepend 模式)⇒ 第三条也覆盖
- **留 28 处**:目标是 `tools/verify/` · `deploy/openclaw/` · 各 skill 的
  `scripts/` —— **这些目录不在 `pythonpath` 上**,删了对应测试当场 import 失败

### 3. 剩下 69 处删不掉,而理由值得写下来

`skills/` · `tools/` 下那 42 处里的大部分,挂的是仓库根的 `skills/` ——
因为全仓还有 **233 处**在用旧名字 import(`from _contract import ...`),
而第 52 章已经明确把 `skills/` **排除在 pip 包之外**(三条理由,含 R-2)。

⇒ 要删它们,前提是先把那 233 处全迁到 `easyup_biga.*` —— 那等于**给 H-I 的
薄壳定退役**,是一个独立的架构决定,不该作为「删几个路径操作」的副产品夹带进来。

> 通用原则:**留白要带理由,不要带到期日不明的承诺。**
> 记进 `TODO.md` 的是「为什么现在删不了」与「什么条件下才该删」,
> 不是「以后再说」。

## 坑

### 1. 🔴 同一批里,按字符串形状分类栽了**三次**

| # | 在哪 | 怎么栽的 |
|---|---|---|
| 1 | 第一次分类 | 按「行里有没有 `skills` 这个词」分。`skills/decision-card/scripts/budget.py` 里的 `parents[2]` 解析出来**正是** `skills/`,却被分进了「安全」那堆 |
| 2 | 清理孤儿 import | ruff 报了文件名**和行号**,我用文件名去删「长得像 `import sys` 的那一行」—— 而 ruff 指的是**函数体内第 136 行**那个。结果删掉了顶层那个**真正在用的**,3 条测试当场失败 |
| 3 | 守卫第一版 | 按「行里有没有 `"scripts"`」判。`str(REPO / "skills/market-calc/scripts")` 整条路径写在**一个**字符串字面量里,没命中,被误判成冗余 |

三次都是同一句话:**判据必须是「它解析成什么」,不是「它长什么样」。**
第 2 次尤其贵 —— 工具已经把精确的行号交到手上了,是我自己换成了模糊匹配。

修法是把判据改成真的解析:字符串字面量按 `/` 拆开拼路径、
`parents[n]` 按文件自身位置算出来、算不出来的(`str(d)` 这种动态目标)判成
「不覆盖」并写明**为什么这不是 fail-open**——
一个动态拼出来的目标**按构造就不可能**恰好是那三个静态根之一。

### 2. 恢复 import 时插在了 `from __future__` 前面

`from __future__ import annotations` 必须是文件的第一条语句,插在它前面直接
`SyntaxError`。补回一行 import 也要看放哪。

### 3. 孤儿 import 是 U-IV 顺手还上的债

删掉 `sys.path.insert` 之后,`import sys` / `from pathlib import Path` 会变成
孤儿 —— 这正是第 51 章「坑 3」记过的那类**不报错**的东西。

这次不用人肉逐个看:第 54 章刚把 ruff 接上,`--select F401` 直接给出清单。
⚠️ 但要**取删除前后的差集**,只还自己造的债 —— 仓库本来就有 25 条 F401,
一把 `--fix` 下去会把不相关的历史违规一起改掉,diff 当场失控。
实测差集正好 **24 条**(23 个 `sys` + 1 个 `Path`)。

## 验证

```bash
cd <仓库根>

# 1. 本批守卫
python3 -m pytest tests/test_test_path_bootstrap.py -q        # 预期 6 passed

# 2. 🔴 单文件独立进程 —— 本批真正的判据（全量绿证明不了这件事）
for f in $(git diff --name-only HEAD~1 -- 'tests/*.py'); do
  python3 -m pytest "$f" -q --tb=no -p no:cacheprovider >/dev/null || echo "❌ $f"
done
# 预期：无输出（48 个文件逐个独立跑都绿）

# 3. 剩余处数与分布
python3 - <<'PY'
import subprocess, pathlib, collections
c = collections.Counter()
for f in subprocess.run(["git","ls-files","*.py"],capture_output=True,text=True).stdout.split():
    for l in pathlib.Path(f).read_text(encoding="utf-8").splitlines():
        if l.strip().startswith("sys.path.insert"): c[f.split("/")[0]] += 1
print(sum(c.values()), dict(c))
PY
# 预期：70 {'tests': 28, 'skills': 21, 'tools': 20, 'deploy': 1}

# 4. 没造出新的孤儿 import
ruff check --select F401,E402 tests/     # 预期：与批 U-IV 基线持平，不增加

# 5. 全量回归
python3 -m pytest -q
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 🔴 提示词的判据对测试文件不成立:`sys.path.insert` 是**进程级副作用**,pytest 把所有模块导进同一个进程 ⇒ 删一处再跑全量**必然全绿**,因为别处兜住了 |
| 2 | 判断「这行还有没有用」先问**它的作用域有多大**。进程级副作用必须让它**单独待在一个进程里**才判得准 |
| 3 | 实测:`test_store.py` 单独跑(清空 pythonpath)`ModuleNotFoundError`,加上字母序第一个自举的文件就 70 条全过 —— 而它自己一行 insert 都没有 |
| 4 | 真正的分界线是**谁在跑它**:`tests/` 只经 pytest(有 `pythonpath`)⇒ 指向那三条的冗余;`skills`/`tools`/`deploy` 直接跑或子进程(没有)⇒ 全部承重 |
| 5 | 这条分界线要**核实**不能假设:`tests/` 下没有任何 `__main__` 入口、也没有调用方用 `python3 tests/...` |
| 6 | 「冗余」只对 `pythonpath` 覆盖的目标成立。指向 `tools/verify`·`deploy/openclaw`·各 skill `scripts/` 的 28 处是承重的,守卫必须放行它们 |
| 7 | 🔴 **同一批里按字符串形状分类栽了三次**:`parents[2]` 解析出来是 `skills` 却因字面不含该词被放行;ruff 给了行号我却用文件名+字符串匹配删错了行(3 条测试当场失败);守卫第一版把写在**一个**字面量里的 `"skills/market-calc/scripts"` 误判成冗余 |
| 8 | 判据必须是**「它解析成什么」**:字面量按 `/` 拆开拼路径、`parents[n]` 按文件位置算出来、动态目标判「不覆盖」并写明为什么那不是 fail-open |
| 9 | 孤儿 import 用 ruff `F401` 找,但要取**删除前后的差集** —— 仓库本来就有 25 条,一把 `--fix` 会把历史违规一起改掉。差集正好 24 条 |
| 10 | 补回 import 也要看放哪:`from __future__` 必须是第一条语句,插它前面直接 `SyntaxError` |
| 11 | 剩下 69 处**删不掉**:它们挂 `skills/`,而全仓 233 处还在用旧名字 import,`skills/` 又被第 52 章明确排除在 pip 包外 ⇒ 前提是先给薄壳定退役,那是独立的架构决定 |
| 12 | **留白要带理由,不带到期日不明的承诺** —— 记进 `TODO.md` 的是「为什么现在删不了」和「什么条件下才该删」 |
