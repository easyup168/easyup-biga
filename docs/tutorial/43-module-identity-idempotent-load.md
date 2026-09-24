# 第 43 章 · 把一个坑填了两次的地方，填第三次：动态同名模块 monkeypatch 错位

> 📄 **过程** · 写完即冻结
> **覆盖**：外部评审 A 节"消除动态同名模块 monkeypatch 错位"——把批 J-II
> （教程第 32 章）只修了一处的坑，推广到全仓其余 11 处未修实例 ｜
> **不覆盖**：A 节其余项（统一 stdout/stderr 契约、Isolation Registry 化、
> 测试同时支持 Git worktree 与 ZIP walk）——见 `TODO.md`

## 目标 / 产出

三轮对抗性复核指出：外部评审 A 节点名的"动态同名模块 monkeypatch 错位"不是
一个孤立的、已经被批 J-II 解决的历史问题——批 J-II 只堵住了 `test_run_id_
capture.py` 这一处，全仓还有 11 处一模一样的坑从未被推广修复过。真机复现：

```bash
pytest tests/test_orchestrator.py tests/test_facts_split_e3.py   # 红
pytest tests/test_facts_split_e3.py tests/test_orchestrator.py   # 绿（顺序反过来）
```

给全部 12 处加载逻辑（含批 J-II 那处已修好的）补齐同一份幂等检查，新增两条
不依赖收集顺序的直接身份断言测试。

## 为什么这么做

### 同一个坑，为什么会填两次都填不干净

批 J-II（教程第 32 章）遇到这个问题时，做法是"改正在踩的那个文件"——这在
当时是对的（范围要小、改动要可解释），但这份 `_load()` 逻辑本身是**复制粘贴**
出来的：全仓 14 个测试文件用 `importlib.util.spec_from_file_location` 从
文件路径加载模块（原因是 `decision-card`/`risk-check`/`market-calc` 这些
目录名带连字符，不是 Python 能直接 `import` 的包名），其中至少 12 处的加载
逻辑长这样：

```python
def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # 🔴 无条件覆写，不检查是否已存在
    spec.loader.exec_module(mod)
    return mod
```

`sys.modules[name] = mod` 这一行是**全局、可变、进程范围**的状态——同一个
pytest 进程里，任何一个测试文件调用 `_load("card_ops", ...)` 都会把
`sys.modules["card_ops"]` 换成一个全新对象，不管这个名字是不是已经被别人
（包括生产代码 `orchestrator.py` 自己）加载过、正在用。

批 J-II 发现问题时，改的是"这次踩到的那一份"。而这份逻辑同时存在于另外
11 个文件里，**没有人回头把同一个修法推广过去**——不是因为疏忽被隐藏了，是
因为这类 bug 只在特定的跨文件收集顺序下才会真的红，日常按字母序跑全量测试
大概率不会撞上（也确实一直没撞上，直到这次对抗性复核用非默认顺序去跑）。

> 通用原则：一个 bug 被复制粘贴进了 N 个文件，只修复"当前踩到的那一份"不
> 等于修复了这个 bug——它等于给了自己一个错觉。真正的收尾是回头搜一遍
> 有没有其余复制体。

### 为什么只有 `card_ops` 和 `risk_check` 是"真会红"的，其余是"应该修但没实锤"

`orchestrator.py` 自己在模块顶层做了两处会绑定这些名字的 import：

```python
from risk_check import build_fact_bundle
...
import card_ops
```

这两行执行的时刻，`sys.modules["risk_check"]`/`sys.modules["card_ops"]`
里当时是什么，`orchestrator.py` 自己的引用就永远指向什么——**除非**后面又有
测试文件用上面那份 `_load()` 把它们换掉。换掉之后，`orchestrator.py` 自己
的引用是旧对象，任何测试对"新对象"做的 `monkeypatch.setattr` 都打不到
`orchestrator.py` 真正调用的那个。这是唯一被真机复现过的具体故障路径。

`market_calc`/`sector_calc`/`technical_calc`/`emotion_calc`/`amend_verdict`
没有被 `orchestrator.py`（或其他生产代码）直接 `import`——Stage 1 的
Specialist 是被**spawn 成独立会话**跑对应 skill 脚本，不是被编排器进程内
import。所以这几个名字目前没有已知的生产碰撞路径，"应该修"的理由是评审
A 节点名的**模式**（同一份无幂等检查的加载逻辑），不是"已经复现过具体故障"。
按同一个标准全部补上，不因为"这几个目前没坑到人"就留着——留着就是给下一个
在这些名字上做 monkeypatch 的人埋雷。

### 修法：让 `_load` 退化成 Python `import` 本来的语义

```python
def _load(name, rel):
    if name in sys.modules:      # 幂等：已经加载过就复用同一个对象
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
```

一行 `if` 就够了——Python 标准的 `import X` 本来就是这个语义（已经在
`sys.modules` 里就直接复用，不重新执行模块顶层代码）。这份手写的加载逻辑
之所以存在，只是因为目录名带连字符没法走标准 `import` 语法，**不该**因此
连"复用已加载实例"这条标准语义也一起丢掉。

十二处按各自原有风格（函数式 `_load(name, rel)`、单参数 `_load(name)`、
模块级一次性 `spec_from_file_location`、类方法 `self._load(skill, mod)`）
各自补一份等价的幂等检查，不做跨文件抽公共 helper 的重构——十二段几乎相同
的三行 `if` 好过为十二个各自独立、路径参数形状还不完全一致的加载点硬拗一个
共享函数。

### 新测试怎么做到"不依赖收集顺序、不用跑两个文件"

最直接的验证方式是像上面那样跑两个文件、换两种顺序——但这样的测试要么得
拉起子进程跑 pytest（重、慢），要么得依赖"以特定顺序运行这两个特定文件"
这个约定本身（脆、容易被以后的人不小心破坏）。

更直接的断言是绕开"顺序"这个变量，直接问一句结构性的话：**这次会话里，
`orchestrator.py` 绑定的模块对象，和这次会话里任何人现在 `import` 同名
模块拿到的对象，是不是同一个？**

```python
def test_orchestrator绑定的card_ops与现在import拿到的是同一个对象(self):
    import card_ops
    assert orch_mod.card_ops is card_ops
```

这条断言本身**不关心**测试是怎么收集的——不管 pytest 用什么顺序跑完了
所有测试文件的收集阶段，只要"任何一处幂等检查退化回无条件覆写"，
`orch_mod.card_ops`（`orchestrator.py` 自己收集时绑定的）与"现在
`import card_ops` 拿到的"（反映的是最后一次覆写后的状态）就会分裂成
两个不同对象，这条断言当场失败——不需要凑巧撞上某个特定的收集顺序。

## 执行

```bash
# 复现原始 bug（正序红、反序绿）
pytest tests/test_orchestrator.py tests/test_facts_split_e3.py -q
pytest tests/test_facts_split_e3.py tests/test_orchestrator.py -q

# 改完之后两种顺序、以及全量反序都要绿
pytest tests/test_orchestrator.py tests/test_facts_split_e3.py -q
pytest $(ls tests/test_*.py | sort -r) -q
```

## 坑

**sabotage 顺序选错，第一次验证是假阴性**：验证新测试 `TestModuleIdentity
AcrossTestFiles` 能不能抓到回归时，第一次把 `test_facts_split_e3.py` 放在
`test_orchestrator.py` **前面**跑——这个顺序下，`orchestrator.py` 自己的
`import card_ops` 反而会"捡漏"到 `test_facts_split_e3.py` 已经放进
`sys.modules` 的对象（Python 的 `import` 本来就是"已经在 sys.modules 就
复用"），两边天然一致，sabotage 测试全绿——不是修法没用，是验证的顺序刚好
没触发分裂。回到"先收集 `orchestrator.py`、再收集会覆写的文件"这个真正
制造分裂的顺序，两条新测试才如实抓到注入的 sabotage。

> 通用原则：验证一个"顺序依赖 bug"的回归测试时，sabotage 本身也要用
> **正确的触发顺序**去跑——顺序选反了，看到"绿"证明不了任何事。

## 验证

```bash
python3 -m pytest -q      # 1441 条，全绿
python3 -m pytest $(ls tests/test_*.py | sort -r) -q   # 全量反序同样全绿
tools/verify/audit_public.sh --worktree
```

## 本章要点

| 要点 | 一句话 |
|---|---|
| 复制粘贴的 bug 只修当前那份不算修完 | 批 J-II 修了 1 处，另外 11 处一模一样的复制体潜伏到三轮复核才被推广修复 |
| 找"真会碰撞"的名字要看生产代码的 import | `card_ops`/`risk_check` 被 `orchestrator.py` 直接 import，其余五个目前没有已知生产碰撞路径，按模式统一修不因为"没坑到人"而留着 |
| 手写的模块加载逻辑该补回标准 import 的幂等语义 | 连字符目录名逼你手写加载，不代表可以放弃"已加载就复用"这条默认行为 |
| 顺序依赖 bug 的回归测试要绕开"顺序"本身 | 直接断言两个引用是不是同一个对象，比"跑两个特定文件、两种特定顺序"更稳固 |
| sabotage 也要用对触发顺序 | 顺序选反了，sabotage 全绿证明不了修法有效，只证明这次没选中会触发分裂的路径 |
