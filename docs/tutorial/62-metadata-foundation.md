# 62 · 评审一份外部实现，然后合并而不是覆盖（Phase 3 · P3-1）

> 📘 **过程** · 写完即冻结
> **覆盖**：P3-1 —— schema v23–v26、元数据落库、与外部实现包的合并判定、修掉一颗潜伏已久的地雷 ｜
> **不覆盖**：P3-0 的注册表（见 [61 章](61-dataset-registry.md)）、Parquet / 质量策略（P3-4）

---

## 目标 / 产出

控制面第一次装下**采集**这条生命周期：

```text
schema v23–v26                              七张追加式表
src/easyup_biga/persistence/data.py         唯一写入面
src/easyup_biga/data/jobs.py                Data Run 状态机
tests/test_data_platform_foundation.py      整条血缘的冒烟
tests/_scan.py::sandbox_ignore()            两处沙盒共用的排除规则（修地雷）
```

---

## 为什么这么做

### 这一章真正的内容是「怎么评审一份别人的实现」

外部交付了一份 P3-0+P3-1 的完整实现包，要求是「对比 review，如果他的版本更好就覆盖」。

它的包装很完整：MANIFEST、变更文件清单、迁移说明、测试结果、一份 52KB 的 patch、
以及一整份改好的源码树。**而它的 `TEST_RESULTS.md` 第一行就写着**：

> The user requested a light validation pass rather than a full 1900+ test run.

也就是说：**它自己声明没跑全量**，而它改了 `persistence/schema.py` 与
`persistence/__init__.py` —— 全仓最多人依赖的两个文件。

### 第一步不是读代码，是建对照组

直接在他们的源码树里跑 pytest 得到 27 failed，但**那个数字没有意义** ——
那份树是 zip 解出来的、没有 `.git`，而仓库里相当一部分守卫按 git 清单扫。

⇒ 在**同一个 worktree** 里做 A/B：

```text
原始 main（对照）        1891 passed /  0 failed
加上他们的代码           1881 passed / 14 failed
```

现在 14 这个数字才有意义。

> 通用原则：**跨环境的测试数字不可比。** 先建一个只差「被测物」一项的对照组，
> 否则你分不清是代码坏了还是环境不同。

### 12 条真破坏，根因两条

去掉 2 条徽章未同步（无害），剩下 12 条：

**一 · `EvidenceSetDatasetLink` 撞铁律 4。** 守卫禁止在 `domain/` 之外定义名字
里带 `Evidence` / `Verdict` / `DecisionCard` 词根的类。

这是**误报**：那个类是 `evidence_set_datasets` 的一行，名字里的 `EvidenceSet`
指的是被引用方，不是它自己是什么。守卫自带 `# contract-exempt:` 豁免机制，
作者还写明「豁免要写在被扫的代码里」—— 这正是它该用的场合。

⇒ 没有改名绕开。改名能让守卫闭嘴，但读者仍然会问「这是不是第二份 Evidence 契约」，
而豁免注释**就在那里回答这个问题**。

**二 · 沙盒把 `src/easyup_biga/data/` 静默丢掉了。**（下一节）

---

## 坑

### 🔴 一颗埋了很久的地雷，是被这次合并**引爆**的

11 条失败集中在 `test_spawn_proof.py`，报的是：

```text
ModuleNotFoundError: No module named 'easyup_biga.data'
```

而那个包明明在。根因在沙盒的建法：

```python
shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(
    ".git", "__pycache__", "data", ".pytest_cache", ".claude", "memory"))
```

`ignore_patterns` 的 glob 对**每一层目录**生效。写 `"data"` 本意是排掉仓库根下
那个装 SQLite 库的 `data/`，实际把 `src/easyup_biga/data/` 也一起丢了 ——
而且**不报错**：copytree 照常完成，沙盒照常建起来，只是少了一个包。

它此前没爆，只因为没有任何生产路径 import 那个包。P3-1 把
`src/easyup_biga/persistence/__init__.py` 接上新包的那一刻，出卡路径就真的
依赖它了。

⚠️ **这是同一个坑的第三个实例。** 前两个就记在那份注释里：运行时总闸
`.biga-card-stop` 被复制进沙盒（三条出卡测试全部拿到 rc=3）、构建产物 `build/`
没被排除。**前两次的修法都是「往名单里再加一个名字」** ——
而名单的**形状**一直是错的：它按名字匹配任意层级，想表达的却是「仓库根下的那一个」。

⇒ 收成一处 `tests/_scan.py::sandbox_ignore()`，按**相对仓库根的路径**判。

> 通用原则：**同一个坑第三次出现时，别再补名单，去看判据的形状。**
> 前两次「加一个名字」都成功了，那恰恰是它能活到第三次的原因。

正反探针都验过：接上依赖后全绿；把规则退回旧写法当场红。

### `partition_keys` 在两版实现里都是装饰品

他们强制它非空，我允许空。争了半天，**两边都错过了真正的问题**：
没有任何东西核对「实际写入的分区键 == 注册表声明的」。

于是 `{"symbol":…, "as_of":…}` 和 `{"trade_date":…}` 可以同时写进同一个
dataset，而 `UNIQUE(dataset_id, partition_key_json, data_version)`
**不会报错** —— 键不同 ⇒ JSON 不同 ⇒ 两行，而它们描述的是同一片数据。

⇒ 三个写入口都接上 `_check_partition_keys()`。这个字段这才变成承重件。

顺带解掉了那个「编造值」的争议：他们给日历写 `("calendar_month",)`（按官方
月度端点的形状，而主源早就换了），我写 `()`。**正确答案是 `("as_of",)`** ——
交易所逐步公布未来排期，11 月取到的日历含次年、9 月取到的不含，
区分两次取回的正是取回时刻。

> 通用原则：**当两个方案在「填什么」上僵住，先问这个字段有没有人核对它。**
> 没人核对的字段，填什么都对，也都没用。

### 一条字段该什么时候进来

`DatasetDefinition.schema_version` 在 P3-0 被我按「无消费方就是 L-1 死配置」
挡在外面。P3-1 的 `save_dataset_partition` 要拿它拒绝「分区声称的版本与注册表
不符」—— **有消费方了，这才装进来**。

这正是那条纪律想要的节奏：不是「永远别加字段」，是「等它有人用」。

---

## 验证

```bash
python3 -m pytest tests/test_data_platform_foundation.py -q   # 整条血缘
python3 -m pytest tests/test_data_registry.py -q              # P3-0 守卫
python3 -c "import sys;sys.path.insert(0,'src');\
from easyup_biga.persistence.schema import SCHEMA_VERSION;print(SCHEMA_VERSION)"   # 26
```

对照实测：

| 树 | 结果 |
|---|---|
| 原始 `main` | 1891 passed / 0 failed |
| 只加他们的代码 | 1881 passed / **14 failed** |
| 合并后 | **2211 passed / 0 failed** |

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 评审别人的实现，第一步是**建对照组**——跨环境的测试数字不可比 |
| 2 | 交付物自称「按要求只做了轻量验证」时，那句话就是最该先验的那一条 |
| 3 | 判定可以是**合并**而不是二选一：取他们的 schema 与落库层，保留我的注册表模型与守卫 |
| 4 | 🔴 `shutil.ignore_patterns` 的 glob 对**每一层**生效——写 `"data"` 会连 `src/**/data/` 一起静默丢掉 |
| 5 | 🔴 同一个坑第三次出现时别再补名单，**去看判据的形状**；前两次「加个名字」成功过，那正是它活到第三次的原因 |
| 6 | 两个方案在「字段填什么」上僵住时，先问**有没有人核对它**——没人核对的字段填什么都对也都没用 |
| 7 | 守卫误报该走它自带的豁免机制，不该改名绕开：改名让守卫闭嘴，豁免注释才回答读者的疑问 |
| 8 | 字段等到**有消费方那一刻**才装进来，这是「不装死配置」想要的节奏，不是「永远别加字段」 |
