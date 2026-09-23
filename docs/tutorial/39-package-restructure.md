# 第 39 章 · 三个基础设施包搬进 `src/easyup_biga/`

> 📄 **过程** · 写完即冻结
> **覆盖**：确定性编排批 H-I —— `skills/_contract`/`_store`/`_sources` 三个共享
> 基础设施包迁进 `src/easyup_biga/{domain,persistence,providers}/`，旧路径留
> re-export 薄壳 ｜
> **不覆盖**：H-II（`_runtime`/`_snapshot` 往哪迁、`application`/`integrations`/
> `cli` 三个命名空间装什么——留白，见「本章要点」最后一条）；架构升级本身的设计
> 依据（见 `docs/design/deterministic-orchestration.md` §8～§11）

---

## 目标 / 产出

外部评审 §29 建议长期把代码迁到
`src/easyup_biga/{domain,application,providers,runtime,persistence,integrations,cli}`。
这一批只做其中最小、最安全的一步：把三个已经存在、边界已经清楚的共享基础设施包
——`skills/_contract`（契约类型）、`skills/_store`（DB 唯一入口）、
`skills/_sources`（外部数据采集）——原样搬进新目录，旧路径留一份「薄壳」
继续可用。全仓 199 处 `from _contract import ...` 之类的导入语句**一个字符
都不用改**。

产出：`src/easyup_biga/{domain,persistence,providers}/` 三个真实包（`git mv`
保留 history）；旧路径 `skills/{_contract,_store,_sources}/` 变成 24 个纯
re-export 薄壳；`pyproject.toml`、四处 AST 守卫常量、15 章教程指针、
`architecture.md` 十几处代码指针跟着更新。

## 为什么这么做（占篇幅最大的一节）

### 为什么只搬三个包，不是外部评审建议的全部七个

`deterministic-orchestration.md` §8 采纳了 §29 的建议，但只对**三个包**给出了
具体缓解方案——教程路径怎么处理、`sys.path.insert(0,"skills")` 这道承重墙怎么
不塌、纯结构改动怎么验收。`_runtime`/`_snapshot` 两个包往哪迁、
`application`/`integrations`/`cli` 三个命名空间该装什么，§8 完全没讨论。

这不是遗漏，是诚实的边界：当时确实没想清楚。现在硬做，得到的是没有设计依据的
猜测性目录——`application/` 该装 `orchestrator.py` 吗？可它现在活在
`skills/decision-card/scripts/` 里，是**skill 自己的编排脚本**，不是一个独立
共享包，搬不搬、怎么搬是另一个问题。`_snapshot/coordinator.py` 算 `application`
还是别的？没人判过。

⇒ 按这个仓库一贯的做法（`agents/` 子目录不预建空目录），拆成 H-I（这次做，三个
有把握的包）/ H-II（留白，等真有内容要放再建）。**拆分辩解**：与 A/C/D/E/J 每次
「耦合面不同就拆会话」是同一条纪律，不是临时找的借口。

### 为什么「纯目录搬迁」听起来简单，实际两处地方会咬人

**`sys.path.insert(0, "skills")` 是承重墙。** 全仓 71 个文件靠它 + `from
_contract import ...` 这种写法工作，`pyproject.toml` 的 `pythonpath` 配置也
只挂了 `skills`。搬完文件、不留兼容层的话，这 199 处调用点全部要改——一个
「结构不变」的批次变成一个「到处改一行 import」的批次，diff 混进逻辑变更，
评审失去意义。

⇒ 旧包原地留**薄壳**：`skills/_contract/`、`skills/_store/`、
`skills/_sources/` 三个目录继续存在，但每个文件都只有几行 re-export 代码，
真实实现全部搬到 `src/easyup_biga/`。

**自定位文件不是「纯移动」安全的。** `db.py`（`DEFAULT_DB_PATH`）与
`tradetime.py`（挂 `skills/` 到 `sys.path`）都用 `pathlib.Path(__file__)
.resolve().parent...` 算仓库根/`skills/` 的位置——这类代码的正确性**依赖
自己在目录树里的深度**。从 `skills/_store/db.py` 搬到
`src/easyup_biga/persistence/db.py`，深了一层（多出 `src/` 与
`easyup_biga/`），`parent.parent.parent`（原来正好到仓库根）现在停在了
`src/`。`DEFAULT_DB_PATH` 悄悄算成 `src/data/biga.db`——一个不存在的库，
而错误只会在真的尝试写库时才炸。

这正是设计文档 §8 那句「结构改动唯一可信的验收是行为不变」的具体含义：
**移动文件本身可以改变行为**，如果文件的正确性建立在"我在哪一层"这个假设上。

### 薄壳怎么写：两种写法，服务两个不同的可达性问题

包级壳（3 个 `__init__.py`）与子模块壳（21 个）用了两种不同的技巧，因为
它们要解决的问题不一样：

**包级壳**：自己在 `__file__` 相对路径上把 `src/` 挂上 `sys.path`，再
`from easyup_biga.domain import *` + 显式 re-export `__all__`。这一步不依赖
`pyproject.toml` 的 `pythonpath`——因为 `bin/biga-card` 拉起的子进程、
`systemd-run` 脱树跑的任务，都**不经过 pytest 配置**，只有壳自己动手挂
`sys.path`，真实运行时才能 import 到 `easyup_biga`。`pyproject.toml` 里
**另外**把 `src/` 加进 `pythonpath`，是为了让 pytest 内部**直接**
`import easyup_biga.xxx`（不经过任何壳）也稳定可达——两条可达性路径，
两道探针分别验证（P1 走壳、P5 走非 pytest 真实路径）。

**子模块壳**（21 个，比如 `skills/_contract/card.py`）没有用
`from easyup_biga.domain.card import *`，而是：

```python
import sys as _sys
import easyup_biga.domain.card as _mod
_sys.modules[__name__] = _mod
```

`import *` 只会拷贝 `__all__`（或所有非下划线名）里列出的名字到当前模块
——如果本体后来新增一个没进 `__all__` 的名字，两边就会**悄悄漂开**。
`sys.modules[__name__] = _mod` 直接让 `_contract.card` 与
`easyup_biga.domain.card` 在 `sys.modules` 里指向**同一个模块对象**——
不是复制一份 API 表面，是身份相同。`_contract.card is easyup_biga.domain
.card` 为真，`isinstance` 检查、`is` 比较都不会因为「模块被 import 了两次
变出两个类对象」这种 Python 常见陷阱而出问题。

> 通用原则：写「旧名字指向新实现」的兼容壳时，`import *` 提供的是**API 表面
> 的快照**，`sys.modules[__name__] = 真实模块` 提供的是**身份等同**。前者
> 简单但会漂；后者多写两行，但杜绝了「壳与本体不同步」这整类问题——尤其当
> 本体后续还会继续演化时，选身份等同。

### 一个接受下来的不完美：`persistence`/`providers` 暂时还不能只靠 `src/` 自足

三个新包里，`domain` 是叶子——不依赖另外两个，只挂 `src/`（不挂 `skills/`）
也能独立 import。`persistence`（`easyup_biga.persistence.db` 等）与
`providers`（`easyup_biga.providers.eastmoney` 等）内部还是
`from _contract import ...`——这是搬迁前就写在那儿的旧写法，这一批**没有
改它**。后果：只挂 `src/`、不挂 `skills/` 会 `ModuleNotFoundError: _contract`；
真实运行时不会撞见这个问题，因为 `skills/` 与 `src/` 恒同时在
`sys.path` 上（薄壳自挂 + `pyproject.toml` 都挂了两条）。

把它改成 `from easyup_biga.domain import ...` 才算这三个新包真正互相独立、
不反向依赖旧壳——但那是**内容改动**，会让本章反复强调的验收方式
（结构不变、行为不变、`replay --check` 逐字段相同）失效，而且外部评审
§29 与设计文档 §8 都没把这一步纳入讨论范围。⇒ 记下来、接受，留给 H-II
或专门的清理批次，不假装它不存在，也不在 H-I 里顺手做掉。

## 执行

```bash
# 真实文件搬迁（保留 history）
git mv skills/_contract/card.py src/easyup_biga/domain/card.py
# ...对 24 个真实文件（3 个 __init__.py + 21 个子模块）各做一遍

# 验证 history 真的保留了
git log --follow --oneline -- src/easyup_biga/persistence/db.py | tail -5

# 旧路径写回薄壳（示例，__init__.py 与子模块两种写法见上）

# 全套验证
python3 -m pytest -q                                    # 1358 条，基线不变
python3 -m pytest --collect-only -q                      # 条数迁移前后一致
bin/biga-card --check BIGA-20260923-004                  # 组装逐字段相同
python3 tools/verify/isolation.py                         # 5✅+1🔶，与迁移前一致
python3 -c "import sys; sys.path.insert(0,'skills'); import _contract, _store, _sources"  # 非 pytest 路径
```

## 坑

**`__file__` 自定位代码不是「纯移动」安全的（本章「为什么」一节详述）。**
`db.py`/`tradetime.py` 深了一层后，`DEFAULT_DB_PATH` 悄悄算成
`src/data/biga.db`。**全套 1358 条测试都没抓到**——测试从不走默认路径
（都用 `tmp_path`/`BIGA_DB_PATH` 注入），只有真的用默认路径跑一次
`bin/biga-card --check` 才会撞见 `StoreNotInitialised`。教训：单元测试的
覆盖率再高，如果所有测试都绕开了「真实默认值」这条路径，这条路径本身就是
盲区——迁移这类批次，必须有一步用**默认配置**跑一次真实命令，不能只信
测试全绿。

**AST 守卫的路径常量，光改分发提示词点名的那两处不够（L-13）。**
`test_contract_single_impl.py`/`test_no_raw_sqlite.py` 的 `CONTRACT_DIR`/
`STORE_DIR` 是提示词原本就点了名的。重新跑一遍 grep（不是照抄提示词的
清单）又扫出两处同形状、没被点名的：`test_decision_id_ownership.py` 读
`db.py` 源码找 `save_verdict` 定义、`test_store.py` 读 `schema.py`/
`db.py` 源码找特定注释、并按旧路径前缀排除"store 自己调用自己"——四处
全部指向硬编码的旧路径字面量，四处都要改。只改点名的两处会让后两处继续
指向已经变成空壳的旧目录，静默失去判断依据。

**「改回旧常量会不会被静默放过」这个设想本身被验证方式改写了。** 分发
提示词设计 P2 时设想的失败模式是"改回旧路径 → 守卫看不到新目录 → 悄悄
放过一个真的第二份实现"。实测发现：因为真实类定义**已经搬走**，旧路径
下的守卫反而会把**所有真实的域文件本身**当成"未被排除的外部文件"扫描，
在干净的树上就直接报红（`test_A` 把 `easyup_biga.domain.card` 里的真实
`DecisionCard` 定义当成"违规的第二份实现"）——比预想的静默漏放更容易
发现，但背后的必要性证明成立，探针记录时补充说明了这个偏差。

**评审时亲手撞过一次 `git checkout --` 的旧坑。** 复核这一批时，为了还原
一次 sabotage（临时把 `CONTRACT_DIR`/`STORE_DIR` 改回旧路径验证会不会红），
用了 `git checkout -- <file>` 想"撤销刚才那次改动"——但这两个文件当时还有
**另一批未提交的合法修改**（批 H-I 本身的路径更新）叠在已提交的 HEAD 之上，
`checkout --` 把 HEAD 之后的**全部**未提交内容都还原了，不只是刚才的
sabotage。同一类错误在这份仓库的历史里出现过不止一次——教训依旧是那条：
sabotage-revert 一律用 `Edit` 精确改回原文字符串，不要在文件带有未提交
真实修改时用 `git checkout --` 图快。这次能救回来，是因为改动范围小、
内容刚被完整读过一遍，能逐字还原；范围更大时未必有这个运气。

## 验证

```bash
# 1. 全套测试在基线（除已知与本批无关的 test_没有歧义的目录名）全绿
python3 -m pytest -q

# 2. 24 个文件的 git mv 真的保留了 history（抽查两个）
git log --follow --oneline -- src/easyup_biga/domain/evidence.py | tail -3
git log --follow --oneline -- src/easyup_biga/persistence/db.py | tail -3

# 3. 三份薄壳的身份等同（不是拷贝）
python3 -c "
import sys; sys.path.insert(0, 'skills')
import _contract.card as shim
import easyup_biga.domain.card as real
assert shim is real
print('OK：壳与本体是同一个模块对象')
"

# 4. 非 pytest 真实运行路径可达
python3 -c "
import sys; sys.path.insert(0, 'skills')
from _store import DEFAULT_DB_PATH
assert str(DEFAULT_DB_PATH).endswith('workspace/data/biga.db')
print('OK：DEFAULT_DB_PATH 指向仓库根，不是 src/')
"

# 5. 一张真实决策卡的组装管线迁移前后逐字段相同
bin/biga-card --check BIGA-20260923-004    # 期望：✅ 组装一致
```

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 只搬设计文档 §8 讨论过、给了具体缓解方案的三个包——`_runtime`/`_snapshot` 与 `application`/`integrations`/`cli` 三个命名空间留白（H-II），没设计依据的目录不预建 |
| 2 | 旧路径留**薄壳**才能让 199 处既有导入语句一个字符不改；包级壳自己挂 `src/` 到 `sys.path`（不靠 pytest 配置，真实运行路径也要能 import） |
| 3 | 子模块壳用 `sys.modules[__name__] = 真实模块` 做身份等同，不用 `import *`——前者杜绝「壳与本体漂移」，后者只是 API 表面的快照 |
| 4 | 🔴 `__file__` 自定位代码不是「纯移动」安全的——深一层，`parent.parent.parent` 就从仓库根变成了 `src/`；全套测试因为都绕开默认路径而完全没抓到，只有真实命令用默认配置跑一次才撞见 |
| 5 | AST 守卫的路径常量光改分发提示词点名的两处不够——重新跑一遍 grep 才找出另外两处同形状字面量，这正是 L-13：只信提示词现成的清单，不亲自验证清单还准不准 |
| 6 | 「改回旧常量会静默漏过」这个预想被实测推翻：真实类定义已经搬走，旧路径下的守卫反而会把**所有真实域文件**都当违规扫描，在干净树上就大声报红——比预想更容易发现，必要性照样成立 |
| 7 | 评审阶段亲手复现过一次 `git checkout --` 误删未提交真实修改的旧坑——文件带有未提交内容时，sabotage-revert 一律用 `Edit` 精确还原，不要图快用 `git checkout --` |
| 8 | 结构重组没有行为判据，验收方式换成「`replay --check` 逐字段相同」+「`--collect-only` 条数不减」+「非 pytest 真实路径也能 import」三件事——这也是本章「验证」小节直接抄自设计文档 §8 的原因 |
