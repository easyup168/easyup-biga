# 第 74 章 · 发布闸门，与一条被「换掉」的守卫（Phase 4 · P4-8/P4-9）

> 📄 **过程 · 写完即冻结**（2026-09-26）
> **覆盖**：合 Batch 5（CLI / 定时器 / 验收 / 发布闸门）的过程、改掉的两处、以及一次没红的探针 ｜ **不覆盖**：Phase 4 的设计裁定（见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md)）

---

## 目标 / 产出

合入 `easyup-biga-phase4-batch5-release-20260926`：P4-8（CLI / job / 报告 / systemd）
+ P4-9（验收账本 / finalizer / 发布闸门）。

产出：`bin/biga-review`、`review/{cli,jobs,reports,acceptance,finalizer}.py`、
两组 `-biga` 定时器、`tools/verify/phase4_acceptance.py`。

---

## 运维面这次一处都没错

第 66 章记过 Phase 3 那份外部交付的 deploy 面「三处都不对：路径不存在、
写 `python`、单元名是 `biga-` **前缀**而非 `-biga` 后缀（守卫根本不认它 ——
比报红更糟）」。

这一份全对：

| 项 | 实际 |
|---|---|
| 单元名 | `review-outcomes-biga.{service,timer}` / `measurement-biga.{service,timer}` ✅ |
| WorkingDirectory | `%h/.openclaw-biga/workspace` ✅ |
| 入口 | `exec python3 -m easyup_biga.review.cli` ✅ |
| 安装脚本 | 默认 dry-run，`--apply` 才真装 ✅ |
| 调度命令的字面量 | `biga-review catchup` / `biga-review measure run --policy …` —— 两个子命令在 CLI 里都真的存在 ✅ |

还有一处值得学：

```ini
# CLI exit 2 means NOT_READY (for example H5 has not matured), not a process failure.
SuccessExitStatus=2
```

三态退出码遇上 systemd 会有个隐患：`2` 在 systemd 眼里是失败，
于是「H5 还没成熟」每天都会让这个 unit 变红。
`SuccessExitStatus=2` 把它摆平了 —— **把「还没到时候」和「坏了」在运维面上也分开。**

---

## 改掉的第一处：一条被「换掉」而不是「追加」的守卫

`tests/test_verify_exit_codes.py` 里有一张手写名单 `_WIRED`，
列出所有用了三态退出码的工具。外部包对它的改动是：

```diff
-          "source_bundle.py", "source_survey.py"]
+          "source_bundle.py", "phase4_acceptance.py"]
```

**把 `source_survey.py` 换掉了，不是追加。**

而紧挨着这张名单的注释，记的正是这个洞：

> ⚠️ 最后两个是 2026-09-25 补进来的，它们从第一天起就 `import _verdict`、
> 也真的返回三态，却一直不在这张表里 —— 于是下面那两条守卫**从没查过它们**。

好消息是：本仓库当时不只补了名单，还加了一条**反向守卫**
（`test_名单没有漏掉任何用了这套码的工具`，扫 `tools/verify/` 反过来对账）。
探针实测：换掉之后那条反向守卫当场红。

> 🔴 **「补上漏掉的那一条」和「让漏掉不可能发生」是两种修法。**
> 前者这次会被重新犯一遍，后者不会。

⇒ 名单改成两个都留，并在旁边记一句「外部包换过一次」。

---

## 改掉的第二处：闸门与 CLI 在没迁移的库上吐裸 traceback

合完之后第一件事是**真跑一次**（第 71 章的教训：`import` 过了不等于跑得通）：

```console
$ ./bin/biga-review status
  ...
  sqlite3.OperationalError: no such table: review_subjects

$ python3 tools/verify/phase4_acceptance.py
  ...
  sqlite3.OperationalError: no such table: measurement_runs
```

原因不是代码错了 —— 是**生产库 `data/biga.db` 还停在 v27**
（Phase 3 的迁移跑过，Phase 4 的 v28–v31 还没跑）。

但这个输出有三重问题：

1. 它既不是 `PASS` 也不是干净的三态，是个**崩**
2. 它没说该做什么（开发流程第 8 条：报错要指路）
3. 读的人会以为是代码 bug，而真实情况是**环境还没准备好**

⇒ 加一个结构性前置检查 `require_phase4_schema()`：

```python
with connect(path, readonly=True) as conn:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
if current < PHASE4_MIN_SCHEMA:
    raise Phase4SchemaNotReady(...)
```

⚠️ 判据是 `PRAGMA user_version`（**结构**），不是去 `catch OperationalError`
再看它的文案（**字符串形状** = L-13）。表名改一次，按文案判的写法就静默失效。

异常单独一个类型，理由与仓库已有的 `StoreNotInitialised` 同源：
**「还没装好」和「装坏了」不该报同一种脸色。**

现在的输出：

```
ERROR: Phase4SchemaNotReady: 这个库的 schema 是 v27，Phase 4 需要 v31。
  迁移（只追加，不改 v1–v27 的历史表）：
    python3 -c "... init_schema() ..."
  ⚠️ 生产库先备份：cp data/biga.db data/biga.db.pre-v31.bak
```

---

## 🔴 坑：一条**没红**的探针

给上面那条修复配探针时，把 CLI 的前置检查整段删掉，预期测试变红。

**它是绿的。**

按开发流程第 3 条「探针第一次红的时候先怀疑探针」的反面：
**探针没红时，先问是守卫没用，还是场景根本触发不到它。**

查下来是后者。测试里的子进程指向的是一个**不存在**的库路径：

```python
"--db", str(tmp_path / "v27b.db")     # 这个文件根本没建
```

不存在的库在 `connect(readonly=True)` 那一层就抛 `StoreNotInitialised`，
而 CLI 早就有一条 `except (KeyError, ValueError, RuntimeError)` 兜着它 ——
输出干干净净，断言 `"Traceback" not in stderr` 当然通过。

**场景落在了另一条早就存在的路径上，压根没走到我新加的那条。**

⇒ 改成真造一个 v27 库（照 `MIGRATIONS` 逐条执行到 27 为止），再跑一次探针：
这回红了。

> 通用原则：**测「A 坏了会怎样」的时候，先确认你的输入真的会走到 A。**
> 一个落在旁路上的用例，长得和有效用例一模一样。

顺带还撞到一个小的：`--db` 是全局选项，必须放在子命令**前面**。
写成 `cli status --db X` 得到的是 `unrecognized arguments` ——
那条测试于是在断言「报错有没有指路」，而实际拿到的是 argparse 的用法提示。

---

## 值得原样保留的：闸门不许把环境缺口算成证据

`README_FIRST.md` 里这句话，是整份交付里最值钱的一行：

> The finalizer cannot claim Code Ready without a recorded successful **real full regression**.

而它的 `TEST_RESULTS.md` 真的照做了 —— 自陈全量跑不完、两个 DuckDB 失败是
环境缺口、`test_spawn_proof.py` 是「timeout / not proven」而**不是 PASS**：

> This distinction is intentional: Batch 5 finalization must not convert
> environment gaps or timeouts into release evidence.

这正是本仓库红线 R-3 的同一件事，由交付方自己写出来。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① 代码面闸门
python3 tools/verify/phase4_acceptance.py --code-only     # 期望 exit 0

# ② 没迁移的库上必须指路，而不是 traceback
./bin/biga-review status 2>&1 | head -4                   # 期望看到 init_schema 与备份提示

# ③ 退出码名单两个都在
python3 -c "
import sys, pathlib; sys.path.insert(0, 'tests')
import test_verify_exit_codes as t
assert {'source_survey.py','phase4_acceptance.py'} <= set(t._WIRED)
print('名单两个都在 ✅')"

# ④ Phase 4 五个测试文件
python3 -m pytest -q tests/test_p4_0.py tests/test_p4_batch1.py tests/test_p4_batch2.py \
                     tests/test_p4_batch3.py tests/test_p4_batch4.py tests/test_p4_batch5.py
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | 三态退出码遇上 systemd 要配 `SuccessExitStatus=2` —— 把「还没到时候」和「坏了」在运维面上也分开 |
| 2 | 外部包把一条守卫**换掉**而不是追加，正好重建了旁边注释记录的那个洞 |
| 3 | 🔴 **「补上漏掉的那一条」和「让漏掉不可能发生」是两种修法** —— 前者会被重新犯一遍 |
| 4 | 合完先**真跑一次**：裸 traceback 既不是三态、也不指路、还让人以为是代码 bug |
| 5 | 前置检查的判据落在 `PRAGMA user_version`（结构），不是 catch 异常看文案（L-13） |
| 6 | 「还没装好」和「装坏了」用不同的异常类型 —— 仓库已有 `StoreNotInitialised` 这个先例 |
| 7 | 🔴 **探针没红时，先问是守卫没用，还是场景根本触发不到它** |
| 8 | 一个落在旁路上的用例，长得和有效用例一模一样 |
| 9 | 测「A 坏了会怎样」之前，先确认输入真的会走到 A |
| 10 | 交付方自己写明「不把环境缺口和超时转成发布证据」—— 与 R-3 是同一件事 |
