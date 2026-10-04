# 第 31 章 · 收敛 `run_id` 三同名（一个名字被三个东西共用，且不报错）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 J-II —— 把 `run_id` 这个名字在仓库里的三个含义收敛成一个；
> `agent_runs.run_id`→`ledger_id` 改名；`SpawnHandle.run_id`→`runtime_run_id` 并落库；
> spawn 核验从文本匹配升级成结构化 join；一处必须 live 验的命名空间前提。
> **不覆盖**：`run_id` capture 贯穿 `agent_verdicts`/`VerdictRef`/`DecisionCard`（批 J-I，
> 另一个会话）；按 `run_id` 强制过滤（enforce，等真正的重试路径出现再做）。

---

## 目标 / 产出

做完这一章，`run_id` 在 BigA 自己的代码与数据库里**只有一个意思**：编排的一次执行尝试。

- `agent_runs` 的自增主键从 `run_id` 改名 `ledger_id`（它一直是账本行号，不是 run）。
- `SpawnHandle` 的 `run_id` 改名 `runtime_run_id`（它是运行时返回的 spawn id），并**落库**。
- `spawn_check` 有了落库的 `runtime_run_id`，从「按决策号文本匹配」升级成「结构化 join」。
- 一道新守卫：任何名为 `run_id` 的列必须是 `TEXT` 且值能追到 `decision_runs.run_id`。

---

## 为什么这么做

### 一个名字，三个东西，同住一个库

先看实测（2026-09-23）。`run_id` 这个字面量在仓库里有三个出现处，每一处指的都不一样：

| 出现处 | 实际是什么 | 实测值 |
|---|---|---|
| `decision_runs.run_id` / `run_events.run_id` | ✅ 编排的一次执行尝试 | `'cd5af379…'`（32 位 hex）|
| `agent_runs.run_id` | ❌ **INTEGER 自增账本行号** | `130, 129, 128…` |
| `SpawnHandle.run_id` | ❌ 其实是运行时返回的 spawn id（UUID），且**从不落库** | 运行时给的 id |

危害不是「某处算错了」。三个东西各自的代码单独看都对。危害是：

> 任何一条 join、任何一份报表，只要跨了这三处，就会拿到一个**语义正确、指向错误**的
> 数字 —— 而且不报错。

这和第 22 章那个「`decision_id` 被迫承担五件事」是同一个病的**反面**：那次是一个名字扛
五个含义，这次是三个东西共用一个名字。两者都不是「写得草率」——`agent_runs` 建于 Phase 1
（那时只有一种 run），`SpawnHandle.run_id` 忠实照抄了运行时自己的列名（在 adapter 内部它
是对的）。**是身份模型拆开之后，旧名字没跟着搬家。**

> 通用原则：同名不同义的字段，静默地互相冒充。它不会在测试里炸，会在你三个月后读一份
> join 出来的报表时，让你相信一个错的结论。命名空间的边界要在**代码里**认得出来。

### 为什么三处必须一起改

改一半会留下一个「半新半旧」的命名空间：读者拿到一个 `run_id`，无法判断它属于已改的那半
还是没改的那半 —— 比现在（三个都叫 `run_id`，至少一视同仁）更难读。所以这一批要么不做,
要做就一次清干净。J2-1 与 J2-2 合在一起做，还有个更硬的理由：它们**动的是同一张只追加表**
（`agent_runs`）—— 拆开就是对一张只追加表连开两次刀。

### 「现在改是免费的」这句话，得先证明

改名最怕的是漏掉一个读取方，运行时才炸。所以动手前先 grep 全仓，确认 `agent_runs.run_id`
这一列**没有任何代码读它的值**。结果：唯一的引用是 `list_agent_runs` 里的 `ORDER BY run_id`
—— 那是改名自己要顺手改的东西，不是外部消费方。别的地方（`agent_trace.py` / `latency_report.py`
/ 各测试）读的是 `r["agent"]`、`len(rows)`，从不读 `r["run_id"]`。

> 通用原则：「这个改动是安全的」不是一句信心，是一次 grep。**「免费」要能被证明**，否则
> 它只是「我还没发现代价」。

而 `SpawnHandle.run_id` 反过来 —— 它有一个**现成的、已在生产路径上的消费方**在等它落库：
`spawn_check`（`bin/biga-card` 每次出卡都调它）现在靠 `payload_json LIKE '%<决策号>%'` 文本
匹配认 spawn。有了落库的 `runtime_run_id`，它就能从文本匹配变成结构化 join。这不是「填了
没人查」的提前量（那是 L-1），是**填上就立刻有人用**。

### `RENAME COLUMN` 会悄悄改坏触发器 —— 必须真跑 SQL 验

`agent_runs` 挂着两个只追加触发器。SQLite 的 `ALTER TABLE … RENAME COLUMN` 会**自动改写
引用该列的触发器体**。我们的触发器体里只有一句 `RAISE(ABORT, '常量串')`，不引用任何列，
理论上不受影响 —— 但「理论上」在这里不够：

> 触发器名字还挂在 `sqlite_master` 里，而触发器**体**被改坏，这**不会报错**。

所以判据必须是「对迁移后的表**真跑**一次 `UPDATE`、一次 `DELETE`，看拒不拒」，不是「查
`sqlite_master` 看触发器名字还在不在」。这正是第 19 章那条教训（守卫查的地方和它声称守的
地方要是同一处）在 schema 迁移上的形状。

### 结构化 join：照抄已验证过的形状，不发明第二种

`spawn_check` 的新判据是：`agent_runs.runtime_run_id` 非空时用它与运行时 `subagent_runs.run_id`
做 join，为 NULL（历史行）时退回现有的 LIKE。这个「优先用强绑定、缺失退回弱绑定、任一缺失
不让整条失效」的形状，仓库里**已经有一份验证过的**——第 26 章 `risk_check.py` 的
`CROSS_CHECK_PAIRS`（两条都有 `evidence_set_id` 就比它、任一缺失退回比 `raw_hash`）。

⇒ 逐字同形照抄，**不发明第二种兼容写法**。两套「优先/退回」的判据各自演进，就是 L-3 的
标准形状：改了一份忘了另一份，剩下那份仍然报绿。

还有一个更硬的设计：join 的右表**复用已按决策号过滤的记录**，而不是去全表查「这个 id 存在
吗」。这样「id 存在」同时蕴含「这条记录属于本次决策」—— 伪造者就算抄一个别处的真 id，那条
记录的 payload 也不含本决策号，照样蹭不上。

### 一处线下无法替代、必须 live 验的前提

整个结构化 join 建在一条假设上：`SpawnHandle.runtime_run_id`（来自 `sessions_spawn` 的
`runId`）与运行时 `subagent_runs.run_id` **是同一个命名空间**。线下只能靠「两边都长得像
UUID」猜。猜错的后果特别阴险：

> join 永远匹配不上 ⇒ 每次都走退回分支 ⇒ **看起来一切正常**。spawn 核验静默退化成了它本
> 要取代的那个文本匹配，而没有任何红灯。

这种「猜错也不报错」正是本项目最优先防范的失败模式，所以它值得破一次例：起**一个**最小
spawn，直接查运行时库验证。这也是为什么通用前置写「默认不调用付费模型」的同时，允许正文
写明「需要真实调用 + 理由 + 预算上限」时以正文为准 —— 判据的**前提**只能在真实环境里验。

> 通用原则：一个「猜错了也不会红」的假设，是守卫链里最危险的一环。它得在真实环境里被
> 确认一次，哪怕要花一点钱 —— 静默退化比多花几分钱贵得多。

### 守卫要「可派生」，不是清单

新守卫防的是「将来又长出第四个语义不同的 `run_id`」。如果写成「检查这几张表的这几列」
（清单），它只加固当时想到的那几个 —— 下一个人加的新表不在清单里，静默漏掉。所以判据从
`sqlite_master` **现有的表推出来**：任何名为 `run_id` 的列必须是 `TEXT`，且值能追到
`decision_runs.run_id`。语义不同的第四个同名几乎必然过不了这两关（`agent_runs.run_id` 当年
是 INTEGER，第一关就红）。

---

## 执行

```bash
BIGA=~/.openclaw-biga/bin/biga
cd ~/.openclaw-biga/workspace   # 实际在独立 worktree 上做，与并行批零重叠

# 0. 基线：自己实测，不照抄文档里的数
python3 -m pytest -q | tail -2          # 1068 passed

# 1. schema v9：RENAME + ADD 同一条迁移（都动 agent_runs 这张只追加表）
#    ALTER TABLE agent_runs RENAME COLUMN run_id TO ledger_id;
#    ALTER TABLE agent_runs ADD COLUMN runtime_run_id TEXT;

# 2. 全仓 grep 确认改名安全（P1-A）：SpawnHandle 已无 .run_id，agent_runs.run_id 无消费方
grep -rn "\.run_id\b" skills tools tests bin | grep -i "handle"   # 应为空

# 3. SpawnHandle.run_id → runtime_run_id（adapter + spike + 两处测试）
#    record_agent_run/record_verdict_run 接住；persist() 加 keyword-only runtime_run_ids
#    orchestrator 从 r1/r2 收 {agent: runtime_run_id} 传进去（Stage 3 判官不进）

# 4. spawn_proof 结构化 join（照抄 CROSS_CHECK_PAIRS 形状）

# 5. 回归 + 计数同步
python3 -m pytest -q | tail -2          # 1079 passed
bash tools/verify/sync_test_count.sh    # 三份活文档 → 1079

# 6. 一处 live 命名空间补验（最小 spawn，成本远低于 $0.05）
#    start() 返回 runtime_run_id → 直接查 subagent_runs WHERE run_id=<它>
```

live 补验的真实输出：

```
start() 返回 runtime_run_id = 'e5c00e01-4992-4634-9d2b-18346ff464b0'
          child session_key = 'agent:emotion:subagent:a4ee178b-…'
[查询] SELECT run_id, child_session_key FROM subagent_runs WHERE run_id='e5c00e01-…':
   run_id=e5c00e01-…  child_session_key=agent:emotion:subagent:a4ee178b-…
wait() 归一化状态 = succeeded  raw='done'
=== 结论：同一命名空间 ✅ J2-3 成立 ===
```

---

## 坑

**改名不是「免费」的那一列有一个隐藏引用**。`grep run_id` 的第一反应是「没人读」，但
`list_agent_runs` 的 `ORDER BY run_id DESC` 会在改名后当场 `no such column: run_id`。它不
算「外部消费方」（不读值），但确实要随改名一并改 —— 「没人读这一列的**值**」与「没有任何
SQL 引用这个列名」是两件事。P1-B 探针（把目标名改成第三个名字）正是让这个引用现形：
6 条 `list_agent_runs` 测试当场翻红，证明确实有测试盯着列名。

**新类插错位置，会把别的类的方法吃掉**。给 `test_store.py` 加守卫类时，一开始插在了
`TestAppendOnly` 中间 —— Python 里一个模块级 `class` 会结束上一个类，于是排在插入点之后的
两个方法（`test_决策编号发出去不能收回` 等）被**并进了新类**。测试仍然全绿（fixture 通用），
但类归属错了。教训：给一个已有类**追加**东西要插在它**结束之后**，不是「看起来相关的地方」。
`pytest --co -rA` 一眼能看出归属对不对。

---

## 验证

```bash
BIGA=~/.openclaw-biga/bin/biga
cd ~/.openclaw-biga/workspace

# 全套回归全绿
python3 -m pytest -q | tail -2                                    # 1079 passed

# 迁移后触发器仍真的拦得住写（P2：真跑 SQL，不看名字）
python3 -m pytest tests/test_store.py -k 迁移后agent_runs -q       # 1 passed

# 结构化 join 比文本匹配硬（P3）：伪造被抓、旧 LIKE 会放过、真实匹配通过
python3 -m pytest tests/test_spawn_proof.py::TestStructuredJoin -q # 4 passed

# 守卫可派生、类型子句与值子句各自会红（P5 常驻自证）
python3 -m pytest tests/test_store.py::TestRunIdNamespace -q       # 3 passed

# 一张已有决策号回放一致（闸门 4）
bin/biga-card --check <某个已落库决策号>
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 同名不同义的字段会**静默互相冒充**：join 出来的数字语义对、指向错、不报错 —— 与「一个 id 扛五件事」是同一个病的反面 |
| 2 | 三处同名**必须一起改**：改一半留下的半新半旧命名空间，比三个都叫 `run_id` 更难读 |
| 3 | 「改名是免费的」得用一次 grep 证明 —— 确认这一列**没有代码读它的值**，而不是凭信心 |
| 4 | `RENAME COLUMN` 会自动改写触发器体，**改坏也不报错** ⇒ 判据必须真跑 `UPDATE`/`DELETE`，不是查触发器名字还在不在 |
| 5 | 「优先强绑定 / 缺失退回弱绑定」这个形状仓库里已有验证过的一份（`CROSS_CHECK_PAIRS`）⇒ 照抄，不发明第二种（L-3） |
| 6 | join 的右表复用**按决策号过滤过**的记录 ⇒ 「id 存在」同时蕴含「属于本决策」，比全表存在性更硬 |
| 7 | 「命名空间是否一致」这种**猜错也不会红**的前提，必须 live 验一次（最小 spawn），否则守卫会静默退化 |
| 8 | 防「第四个同名」的守卫要**可派生**（从 `sqlite_master` 推），不是清单 —— 清单只加固当时想到的那几个 |
