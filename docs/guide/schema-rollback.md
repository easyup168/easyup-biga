# Schema 版本与回滚

> 📄 **操作** · 随环境更新；跑不通就是错的
> **覆盖**：给定 `data/biga.db` 处于某个 `SCHEMA_VERSION`，需要回退到更早
> 版本时的实际步骤 ｜ **不覆盖**：每个版本具体加了什么、为什么加——那些
> 权威说明**只有一份**，在 `src/easyup_biga/persistence/schema.py` 每条
> 迁移体正上方的注释里；下表只是从那里摘一句话方便查，不是第二套口径

## 当前版本（`v1-architecture-baseline` 冻结时）

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from easyup_biga.persistence.schema import SCHEMA_VERSION, MIGRATIONS
print('SCHEMA_VERSION =', SCHEMA_VERSION, '| 迁移条数 =', len(MIGRATIONS))
"
```

```
SCHEMA_VERSION = 21 | 迁移条数 = 21
```

| 版本 | 一句话（摘自 `schema.py` 对应迁移体正上方的注释） |
|---|---|
| v1 | 初始 schema——`agent_runs` / `raw_market_snapshot` 等基础表 |
| v2 | 删掉从未被写入的 token 列 |
| v3 | `agent_verdicts` —— Specialist 的判定原件 |
| v4 | `decision_ids` —— 决策编号的分配器 |
| v5 | 给 `decision_ids` 补上只追加触发器 |
| v6 | 修订链只能线性，不许分叉 |
| v7 | 运行身份 + 显式状态机 |
| v8 | `agent_verdicts` 加 `kind`，区分旧 `AgentVerdict` / 新 `FactBundle` / 新 `AgentAssessment` |
| v9 | 收敛 `run_id` 三同名 |
| v10 | `run_id` capture 贯穿全链 |
| v11 | 一个 `(task_id, agent)` 至多一份 fact 原件 |
| v12 | 外发通知 outbox + 投递日志 |
| v13 | `raw_market_snapshot` 加 `raw_text` —— 让 raw 层真的存 raw |
| v14 | 入站幂等 —— `decision_ids` 的号绑一次外部请求 |
| v15 | `fact_trading_calendar` —— 第一张真正的 `fact_*` 表 |
| v16 | Run Provenance —— 「这张卡属于哪次执行」变成可查询的列 |
| v17 | Fact 唯一约束按 **run** 分区（**含 `DROP INDEX`**，见下） |
| v18 | 一个 run 至多一个 EvidenceSet —— ⚠️ **多余的**，与 v16 那条索引逐字相同，v20 撤回 |
| v19 | `agent_runs.provenance_mode` —— 区分在线执行行与历史行 |
| v20 | 撤掉 v18（**`DROP INDEX`**）—— 一条不变量不许有两个名字 |
| v21 | 一次 run 里一个 agent 至多一条**在线**账本行 —— 多条时真 id 会掩盖伪造 id |

## 为什么没有 DOWN migration

三个原因叠在一起，让"代码级自动回滚"投入产出比很低：

1. **迁移体绝大多数只做加法**——`CREATE TABLE`/`ALTER TABLE ADD COLUMN`/追加
   触发器。逆操作要么是空操作（删一个从未被读的列没有风险但也没有必要），
   要么本身就危险（删一个可能已经被写入真实数据的列 = 丢数据）。

   ⚠️ **「一条 `DROP` 都没有」这句话是假的，而且写下来的时候就已经是假的。**
   实际有三条：`v2`（`DROP COLUMN tokens_in/out`）、`v17`
   （`DROP INDEX ux_fact_per_task_agent`）、`v20`（`DROP INDEX
   ux_evidence_sets_run`）。三条删的都是**没有数据的东西**（从未被写入的列 /
   被更精确的索引取代的索引 / 一条重复索引），所以下面那个结论不变 ——
   但「没有一条 DROP」是一句可以被 `grep -c 'DROP' schema.py` 当场证伪的断言，
   它留在这里的那段时间里，任何照它做判断的人都会判错。
2. **SQLite 对 schema 回退的支持有限**——复杂的列变更、约束变更、以及把表
   结构恢复成某个旧形状，通常仍然只能整表重建（建新表、搬数据、改名），
   这本身就是一次不比正向迁移更简单的操作，不存在"自动挡"。

   ⚠️ 这一条原来写的是「`ALTER TABLE` **没有**删列能力」—— 与上面表里
   `v2 使用 DROP COLUMN` 直接打架。事实是 SQLite 3.35（2021）加了
   `ALTER TABLE … DROP COLUMN`，只是**限制很多**（被索引 / 被约束 / 被
   生成列引用的列都删不了），所以「能删」并不等于「能回退」。
   两句互相矛盾的话摆在同一份操作文档里，比其中任何一句错更糟：
   照着做的人不知道该信哪一句。
3. **append-only 表由触发器强制**（架构不变式，见 `CLAUDE.md`）——`UPDATE`/
   `DELETE` 会被触发器直接拒绝，"回滚数据"在这些表上根本不是选项，
   唯一的路是回到某个更早的**文件级快照**。

⇒ 三条原因都指向同一个结论：**能回滚的不是 schema 代码，是数据文件本身。**

## 实际怎么回滚

🔴 **前提**：下面第 2 步要求迁移前已有一份文件级备份。**本项目目前没有
自动化的"迁移前自动打快照"机制**——这是老实记录的一个真实缺口，不是
假装有这个能力。

1. 停掉写入方，确认没有并发写：
   ```bash
   systemctl --user list-timers 'biga-card*' 'notify-worker-biga*' 'stale-run-reaper*'
   # 确认没有正在跑的实例（.biga-card.lock 无人持有）
   ```
2. WAL 模式下，数据可能还没从 `-wal` 文件写进主库文件，先 checkpoint：
   ```bash
   sqlite3 data/biga.db "PRAGMA wal_checkpoint(TRUNCATE);"
   ```
3. 用迁移前的文件级备份整份替换：
   ```bash
   cp data/biga.db.bak-<迁移前时间戳> data/biga.db
   ```
   **没有备份**：迁移基本都是 additive（新表/新列/新触发器），老代码不认识
   新列/新表会直接忽略它们——多数情况下"只回退代码、不动数据文件"也能跑
   （新列多出来的数据老代码读不到，但不会因此报错）。三条 `DROP`（v2/v17/v20）
   删的都是没有数据的东西，同样不挡这条权宜。
   这只是 additive 迁移天然带来的权宜，**不是设计出来的回滚能力**，遇到
   任何一条迁移改变了已有列的语义（目前没有，但未来可能有）就不成立。

## 验证

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from easyup_biga.persistence.schema import SCHEMA_VERSION
print(SCHEMA_VERSION)
"
sqlite3 data/biga.db "PRAGMA integrity_check;"
```

预期：`SCHEMA_VERSION` 打印出回滚目标那个版本号；`integrity_check` 打印 `ok`。
