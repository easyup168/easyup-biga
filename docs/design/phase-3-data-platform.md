# Phase 3 设计 —— 数据平台地基 + 第一条调度

> 📄 **阶段 · 未开工**（适配裁定已定 2026-09-25 / P3-0 ⬜）
> **覆盖**：Phase 3 的范围、里程碑、出口条件，以及外部设计包的 14 条适配裁定 ｜ **不覆盖**：契约字段与表结构（见 [`architecture.md`](architecture.md)）、施工过程（见 [`../tutorial/`](../tutorial/README.md)）、勾选状态（见 [`../../TODO.md`](../../TODO.md)）

> Phase 3 的**设计 SSOT**。结构性问题（存储平面选型、失败模式清单、表结构）
> 仍以 [`architecture.md`](architecture.md) 为准 —— 本文不复制它，只在偏离时指名。

---

## 0. 这份设计从哪来，以及为什么需要一章「裁定单」

2026-09-25 收到一份外部设计包（7 份文档 + 4 份参考代码骨架），解压在
`docs/external/biga-phase3-data-platform-foundation-revised/`。

它的方向与本仓库**高度一致**：包里提的四个存储平面（SQLite 控制面 / Raw 文件 /
Parquet / DuckDB），[`architecture.md`](architecture.md) §5.1 早就逐项写死了选型
**和触发条件**。所以它不是一份需要重新论证的新架构，而是把 §5.1 里
「选型已定、未建」的三个平面**真的建起来**的施工图 —— 最贵的那部分论证已经做完了。

🔴 **但它是按通用工程写的，不知道本仓库的守卫、裁定与既有实现。**
逐条核对下来有 **14 处**会撞：其中 5 处会让现有守卫真的报红，4 处在替我们做
一个没被讨论过的决定，5 处是包自身的内部矛盾或缺口。

### 为什么裁定单必须自包含

`.gitignore` 第 62 行是 `/docs/external/*` —— 那个包**不在仓库里**
（`external/` 下现在被跟踪的三份是当初显式 force-add 的）。
⇒ 任何人 clone 下来都看不到它。所以下面每一条都**先复述外部包说了什么**，
再给裁定，而不是写「见外部包 §N」。指向一个 clone 不出来的东西，
和 [`../../CLAUDE.md`](../../CLAUDE.md) 里那条「一个 clone 不出来的数字比写 ⬜ 更糟」
是同一个毛病。

🔴 **开工时以本文为准，不以外部包为准。**

---

## 1. 适配裁定单

| # | 外部包说 | 本仓库的实际 | 裁定 |
|---|---|---|---|
| 1 | Schema 从 **v21** 起 | `SCHEMA_VERSION = 22` | 从 **v23** 起排 |
| 2 | CLI `biga data run <job>` | `bin/biga` 是纯 passthrough | 新建 **`bin/biga-data`** |
| 3 | `eod-daily-bars.timer` | 单元名是共享命名空间（R-2） | **`eod-daily-bars-biga.timer`** |
| 4 | 涨跌家数进 emotion dataset | 裁定 15：归 market | 拆出 **`cn.market.breadth`** |
| 5 | 四份 `phase-3-*.md` + 三个新目录 | 一个阶段一份设计文档 | 合成**本文**一份 |
| 6 | 独立 ADR 记存储选型 | §5.1 已逐项写死 | **不落 ADR**，增量补进 §5.1 |
| 7 | 默认可以用第三方库 | `dependencies = []` | **pyarrow 进 dependencies** |
| 8 | DuckDB 是 Phase 3 的一个平面 | §5.1：它不独立触发 | **推迟**到选股闭环 |
| 9 | `required_datasets` 在 P3-0 验收 | registry.py 写明「现在不装」 | 跟 Resolver 一起进 **P3-7** |
| 10 | 日历表改名扩列（同时又说「保留现实现」） | 现表是另一套列名 | **保留现表**，只注册不改列 |
| 11 | Manifest **v2** | 现有 manifest 没有版本字段 | 缺字段 ⇒ **按 v1 读** |
| 12 | `TIMEOUT → 1` | `_verdict.py`：2 查数据 / 1 查代码 | **`TIMEOUT → 2`** |
| 13 | 范围只有数据平台 | 路线图还有 discipline / 飞书 / cron | **数据平台 + 第一条 cron** |
| 14 | 16 条手写出口条件（两份，还不一样） | Phase 2 已吃过手写清单的亏 | 进 **`exit_conditions.py`** |

下面按「会不会静默出错」分组展开。

---

### 1.1 🔴 会撞守卫 / 破裁定（1–5）

#### 裁定 1 · Schema 从 v23 起排

外部包给的参考 schema 建七张表（`data_job_runs` / `data_run_events` /
`provider_attempts` / `raw_artifacts` / `dataset_partitions` / `quality_reports` /
`dataset_snapshots`），并规划 v21…v24 四步迁移。

实测 `SCHEMA_VERSION = 22`：**v21 与 v22 都已被占用**（v21 = `ux_online_agent_run_once`，
v22 = `decision_runs.expected_spawn_agents` + `ux_online_runtime_run_id`）。

⇒ 七张表从 **v23** 起排。

> 🔴 这正是 **v20 撤 v18** 那次的形状 —— 当时照抄评审建议的索引名、没先 grep
> 一遍既有约束，造出一条与 `ux_evidence_set_per_run` 逐字相同的索引（L-3）。
> 照抄外部材料里的版本号是同一个动作。

#### 裁定 2 · CLI 是 `bin/biga-data`，不是 `biga data`

外部包建议 `biga data list` / `biga data run eod-daily-bars --trade-date …`。

[`bin/biga`](../../bin/biga) 是**纯 passthrough**：最后一行
`exec "$BIGA_NODE" "$BIGA_ENTRY" --profile biga "$@"`。
`biga data …` 会被原样转给 openclaw CLI，而它没有 `data` 子命令。

本仓库自己的 CLI 一直是**独立可执行文件**：`biga-card` / `biga-calendar` /
`biga-notify` / `biga-reap`。⇒ 新建 **`bin/biga-data`**，子命令沿用包里的
`list` / `run` / `status` / `snapshots` / `show-snapshot`，全部支持 `--json`。

> 这条不改的话不会静默出错 —— 它会当场报「未知子命令」。列在这里是因为
> 它会污染 P3-1 的验收标准（写了一条跑不起来的命令）。

#### 裁定 3 · systemd 单元名带 `-biga` 后缀

外部包 §11 写第一条 Timer 是 `eod-daily-bars.timer`。

**systemd 用户单元名是两套实例共享的命名空间**（红线 R-2 第 2 处）。既有单元
全部带后缀（`notify-worker-biga.timer` / `stale-run-reaper-biga.timer`），
由 [`tools/verify/isolation.py`](../../tools/verify/isolation.py) 的
`check_namespaces` 守着 —— **这条会真的报红**。

⇒ `eod-daily-bars-biga.timer` / `.service`。装之前照例确认
`OPENCLAW_SYSTEMD_UNIT` 是空的。

#### 裁定 4 · 涨跌家数拆成 `cn.market.breadth`，不进 emotion dataset

外部包的 `cn.market.emotion_close` schema 里有 `advance_count` / `decline_count` /
`advance_decline_ratio`，而 §7「Emotion 数据迁移」把这个 dataset 交给 Emotion Agent。

**涨跌家数归 market 是裁定 15 的首次适用。** 代码里两处都写着：
[`emotion_calc.py:25`](../../skills/emotion-calc/scripts/emotion_calc.py#L25)
「🔴 涨跌家数不在这里（裁定 15）」、
[`market_calc.py:22`](../../skills/market-calc/scripts/market_calc.py#L22)
「上游文档把它同时派给了两个 agent」—— 是的，**同一份上游材料已经犯过一次**。
还有一道全仓静态扫描 [`tests/test_field_single_producer.py`](../../tests/test_field_single_producer.py) 专门盯它。

⚠️ 裁定 15 的但书是「**约束 agent，不约束 provider**」。所以问题不在于某张表里
存了这些字段，而在于 `required_datasets` 把它**绑给 emotion 消费**。

⇒ 拆成独立 dataset **`cn.market.breadth`**，消费方是 market。
`cn.market.emotion_close` 只留涨停/跌停/炸板/连板那几个。

#### 裁定 5 · 合成本文一份，不新建 `plan/` `test/` `adr/`

外部包有四份 `phase-3-*.md`，分装在 `docs/design/` `docs/plan/` `docs/test/`
`docs/adr/` 四个目录。

- `test_一个阶段只有一份设计文档` 会红（四份 `phase-3-*`）
- `plan/` `test/` `adr/` 不在 [`docs/README.md`](../README.md) 的类别表里，
  `test_文件名符合所在目录的规则` 会红在「先去 docs/README.md 定义它」
- 每份文档开头必须有类别标记 + `**覆盖**：` + `**不覆盖**：`，包里一份都没写

⇒ 范围/里程碑/出口条件全部收进**本文**；测试清单进 `tests/` 与
`exit_conditions.py`（见裁定 14）；ADR 见裁定 6。

---

### 1.2 🔶 它在替我们做决定（6–9）

#### 裁定 6 · 不落 ADR，把增量补进 §5.1

外部包里单独有一份 ADR，记四平面选型、Rejected 清单
（PG / Redis / Kafka / ClickHouse / K8s）与 Revisit Triggers。

[`architecture.md`](architecture.md) §5.1 **逐项都有**，而且切 PG 的触发条件更具体
（>1 个并发写进程 / 需要跨机 / 单表 >5000 万行）。再放一份 ADR 就是 L-3：
同一判据两个出处，改了一份忘另一份，剩下那份仍然看起来权威。

⇒ 不落这份 ADR。它真正**新增**的两条 Revisit Trigger（「多人同时使用」、
「SQLite 长期锁冲突」）补进 §5.1 的触发条件列表。

#### 裁定 7 · pyarrow 进 `dependencies`，DuckDB 不进

⚠️ 先纠正一个容易搞错的点：`dependencies = []` 那道守卫
（[`tests/test_packaging.py`](../../tests/test_packaging.py)）钉的是
**「声明 ⇔ 代码双向一致」**，不是「零依赖」。加 pyarrow 并声明它，**守卫照样绿**。

所以这是纯粹的产品取舍，代价是真实的但不在守卫上：
**「clone 下来就能跑」变成「先联网装 pyarrow」** —— 对一份同时是开源教程的仓库
有实感影响。

⇒ 已裁定：**pyarrow 进 `[project.dependencies]`**。CHANGELOG 要写清为什么
（§5.1 把「全市场 EOD 第一次建的时候就该用 Parquet」写死了，不是数据长大了才搬）。

⇒ **不**走 optional-dependencies + 降级：那会让同一个 dataset 有两套存储口径、
两条读路径，正是 L-3 的形状，而降级路径几乎不会被测到。

#### 裁定 8 · DuckDB 推迟到选股闭环

外部包把 DuckDB 列为 Phase 3 的四个平面之一，完成定义里有「DuckDB 跨日查询成功」。

§5.1 写得很清楚：**DuckDB 不是独立触发的** —— 它是查 Parquet 用的引擎，真正
有意义的触发点是「第一版选股闭环」开工（需要对全市场做批量特征计算）。

Phase 3 里没有选股消费方 ⇒ 现在建就是 **L-1 零消费方**，本仓库最优先防范的
失败模式。

⇒ Phase 3 只建 Parquet（它有消费方：EOD bars 自己）。DuckDB 连同
`duckdb` 依赖一起推迟。

#### 裁定 9 · `required_datasets` 跟 Snapshot Resolver 一起进 P3-7

外部包把「Agent `required_datasets` 全部存在」列进 **P3-0 的验收**。

[`src/easyup_biga/domain/registry.py`](../../src/easyup_biga/domain/registry.py#L32)
的 docstring 里有一整段专门写了当时**为什么不装这个字段**：
「装一个没有消费方的字段就是一条 L-1 死配置」。

Phase 3 确实解除了当时的理由（那时只有一个 dataset 走完全链）。但它的消费方
**Snapshot Resolver 要到 P3-7 才有** —— 若按包里的顺序，P3-1…P3-6 这五个里程碑
里它就是死配置。

⇒ 字段跟 Resolver 同批进。P3-0 的验收改成「Dataset ID 唯一 / Provider ID 唯一 /
未知 Dataset fail closed」三条，去掉第四条。

---

### 1.3 ⚪ 包自身的矛盾与缺口（10–14）

#### 裁定 10 · 交易日历保留现表，只注册不改列

外部包自相矛盾：实施计划说「保留当前实现」，数据模型却给了一套改名扩列的 schema
（`trade_date`→`calendar_date`、`source`→`provider_id`、`snapshot_id`→`raw_artifact_id`，
另加 `exchange` / `previous_open_date` / `next_open_date` / `session_type` / `available_at`）。

实测 `fact_trading_calendar` 是前一套，1326 行，批 L 刚落地，
`market_is_open()` 正依赖它。

⇒ **保留现表与现列名**。Phase 3 只补外围：Dataset 注册 / Provider 注册 /
Quality Policy / DataJobResult / 覆盖状态。改列名要等到真有第二个交易所
（现在只有一个来源、一个交易所，`exchange` 列没有消费方）。

#### 裁定 11 · Manifest 缺版本字段 ⇒ 按 v1 读

外部包定义 EvidenceSet Manifest **v2**（`manifest_version` / `knowledge_cutoff` /
`datasets`），又在兼容性一节规定「未知状态 **fail closed**」。

实测现库 12 行 `evidence_sets`，manifest 的键是
`['frozen_bars', 'kind', 'symbols']` —— **没有 `manifest_version`**。
照字面实现，这 12 张卡的 replay 全部挂掉。

⇒ 读端显式规定：**缺 `manifest_version` ⇒ 按 v1 读**（`kind == "index_daily"`）。
这不是给 fail-closed 开口子 —— v1 是一个**已知**状态，不是未知状态。
写端从 P3-7 起一律写 `manifest_version: "2"`。

> 🔴 验收判据不是「新卡能读」，是 **replay 那 12 张老卡仍然逐张通过**。

#### 裁定 12 · 退出码对齐 `_verdict.py`

外部包给的映射：`0 → COMPLETE/SKIPPED`、`1 → FAILED/CANCELLED/TIMEOUT`、
`2 → PARTIAL/QUARANTINED`。

本仓库有退出码的**唯一定义**
（[`tools/verify/_verdict.py`](../../tools/verify/_verdict.py)）：
`0 = PASS` / `1 = FAIL（查了真的不对，去看代码）` / `2 = UNKNOWN（没查成，
去看数据与时机，别改代码）`，docstring 原话是「**`2` 去查数据，`1` 去查代码**」。

对照下来：`PARTIAL/QUARANTINED → 2` 是对的（覆盖率不足、双源冲突都是数据问题）；
**`TIMEOUT → 1` 是错的** —— 超时是典型的「去看数据源/环境/时机」。

⇒ **`TIMEOUT → 2`**。`CANCELLED` 是人主动中断，既不是 FAIL 也不是 UNKNOWN，
单独走 `130`（SIGINT 惯例），不塞进三态。

#### 裁定 13 · 范围 = 数据平台 + 第一条 cron

[`TODO.md`](../../TODO.md) 的路线图写的 Phase 3 是
**数据层加厚 + `discipline`（含它的输入源）+ 独立飞书应用 + 第一条 cron**，
出口条件是「每条 cron 都有**被证明的**消费方」。外部包只覆盖第一项。

⇒ Phase 3 = **数据平台 + 第一条 cron**。理由：EOD job 的 timer 天然就是第一条
调度，路线图那条出口条件对它**直接适用、且能真的验**（消费方就是 EvidenceSet）。

⇒ 推到 **Phase 3b**：
- `discipline` —— 裁定 13 推迟它的理由**至今成立**：输入源仍不存在，硬建只能编
- 独立飞书应用 —— 与数据平台无耦合，现有 outbox 两张表已够 Phase 3 告警用

🔴 这是一次**明确的范围收窄**，不是遗漏。路线图那一行要同步改，
否则它会静默变成一张对不上的表（Phase 2 条件 4「达成了四天没人知道」就是这个形状）。

#### 裁定 14 · 出口条件进 `exit_conditions.py`

外部包有**两份**手写出口条件清单（架构文档 16 条 + 实施计划 16 条），
而且内容不完全一样 —— 本身就是 L-3。

Phase 2 刚吃过这个亏：条件 4 早就达成，**四天没人知道**，因为状态表是手写的。
那次的产物就是 [`tools/verify/exit_conditions.py`](../../tools/verify/exit_conditions.py)。

⇒ Phase 3 的出口条件**从第一天就进那个脚本**，能由程序判定的全部进，
判不了的（演练类）在脚本里显式返回 `UNKNOWN`，不塞进 PASS。详见 §4。

---

## 2. 范围

### 做

```text
Dataset Registry / Provider Registry / Data Job Registry
通用 Raw Artifact Store（文件 + SQLite 元数据）
Dataset Partition Store（Parquet）
Dataset Snapshot + Quality Report
第一批 Dataset：security_master / trading_calendar / index.daily_bars /
                equity.daily_bars / security.tradability /
                equity.adjustment_factors / market.emotion_close / market.breadth
Snapshot Resolver + EvidenceSet Manifest v2
第一条 cron：eod-daily-bars-biga.timer
```

### 不做（本阶段）

```text
DuckDB / 分析查询平面        —— 裁定 8，推到选股闭环
discipline agent             —— 裁定 13，推到 Phase 3b
独立飞书应用                 —— 裁定 13，推到 Phase 3b
分钟线 / Tick                —— §5.1 未触发
PostgreSQL / Kafka / 多机    —— §5.1 的触发条件一条都没成立
全部历史回填                 —— Phase 3 只保证「从今天起每天有」
news dataset                 —— 见下
```

> ⚠️ **第一批 dataset 不含 news。** 而 Phase 2 出口条件 5 卡着的「盘后 198s
> 超预算」，根因正是收盘后快讯量翻倍。⇒ **别把 P3-6（emotion 不再联网）
> 当成顺带解决延迟问题记进 Phase 3 的账** —— 它不解决。

---

## 3. 里程碑

| # | 内容 | 与外部包的差异 |
|---|---|---|
| P3-0 | 契约 + Dataset/Provider Registry | 去掉 `required_datasets` 验收（裁定 9） |
| P3-1 | Schema **v23** 七张表 + 四个 Store + `bin/biga-data` 骨架 | 版本号（1）、CLI 形态（2） |
| P3-2 | 交易日历注册进 Registry | 保留现表现列名（10） |
| P3-3 | Security Master（沪深北统一 `instrument_id`、point-in-time universe） | — |
| P3-4 | EOD Daily Bars 全链 + **第一条 cron** | Parquet/pyarrow（7）、单元名（3）、cron 进范围（13） |
| P3-5 | Tradability + Adjustment Factors | — |
| P3-6 | Emotion 迁移 + 拆出 `cn.market.breadth` | 涨跌家数归属（4） |
| P3-7 | Snapshot Resolver + `required_datasets` + Manifest v2 | 字段时机（9）、v1 兼容（11） |

🔴 **P3-4 是唯一的生产级 Vertical Slice** —— 它是第一个走完
`Provider → Raw → Normalize → Quality → Parquet → Snapshot → EvidenceSet`
全链的 dataset。前三个里程碑的意义全在于让它能被建出来。

---

## 4. 出口条件

🔴 **判据是 `python3 tools/verify/exit_conditions.py`，不是这张表。**
表只用来读，脚本才是权威（裁定 14）。

| # | 条件 | 能否程序判定 |
|---|---|---|
| 1 | Dataset / Provider Registry 各唯一，未知 Dataset fail closed | ✅ AST + 契约测试 |
| 2 | Agent 不直接 import Provider | ✅ AST 扫描 |
| 3 | 每个 Partition 有 schema/version/hash/row_count | ✅ 查库 |
| 4 | 每个 Snapshot 有 Quality Status | ✅ 查库 |
| 5 | EOD Job **连续 5 个交易日** COMPLETE | ✅ 查库（跨天累积，急不得） |
| 6 | 重跑幂等（第二次 `SKIPPED_UP_TO_DATE`，snapshot_id 相同） | ✅ 测试 |
| 7 | 修订出 v2，v1 的 partition 文件仍在 | ✅ 测试 |
| 8 | EvidenceSet 能引用 Dataset Snapshot | ✅ 查库 |
| 9 | **老的 12 张卡 replay 仍逐张通过** | ✅ `replay --check` |
| 10 | Replay 不联网 | ✅ `test_no_network` 同款围栏 |
| 11 | 默认测试全离线全绿 | ✅ `pytest` |
| 12 | 第一条 cron 有**被证明的**消费方 | ✅ 判据是调度命令的字面量 |
| 13 | Provider Fallback 演练成功 | 🔶 演练 —— 脚本只能报 `UNKNOWN` |
| 14 | Quarantine（双源冲突）演练成功 | 🔶 演练 |
| 15 | 隔离自检重跑（新增 timer ⇒ 共享命名空间那项） | ✅ `isolation.py` |
| 16 | 每个里程碑都有教程章节 | 🔶 人判 |

> 🔶 那三条判不了的，脚本里**显式返回 `UNKNOWN`（退出码 2）**，
> 不许塌进 PASS —— 红线 R-3。

---

## 5. 开工前的门

| 门 | 状态 |
|---|---|
| `v1-architecture-baseline` tag 存在 | ✅ 已存在 |
| Full hermetic tests 0 failed | ✅ |
| Phase 2 出口条件 3（真实否决落库） | ⬜ **等一个够极端的交易日**，与 Phase 3 无依赖 |
| Phase 2 出口条件 5（延迟预算第三次重推） | 🔶 待裁定，与 Phase 3 无依赖 |
| **裁定 11：换掉共享的 anthropic 凭据** | 🔴 **见下** |

🔴 **凭据这条是真的门，外部包的前置条件清单里没有它。**
[`CLAUDE.md`](../../CLAUDE.md) 写的是「Phase 3 之前必须换成 BigA 自己的凭据，
触发条件任一成立即换：① 能够申请到独立凭据 ② 出现第一次因这条耦合导致的误判排查」。

⇒ 开工前先判 ①。若此刻仍无法申请到，把「不满足 ①」这个事实**写进 TODO 的那一行**
（而不是让它继续挂着一个看起来没人管的 🔴），并保留 ② 作为强制触发点。

---

## 6. 与后续阶段的关系

```text
Phase 3   数据平台地基 + 第一条 cron        ← 本文
Phase 3b  discipline（含输入源）+ 独立飞书应用
Phase 4   测量层：安慰剂基准 + Card 的区分力检验
```

Phase 3 建的东西里，**选股 / 回测 / 复盘会直接复用**的是：
point-in-time Security Master、Trading Calendar、Daily Bars、Tradability、
Adjustment Factors 这五件。它们是「以后想做什么都绕不开」的那一层 ——
这也是 Phase 3 值得在 discipline 之前做的理由。
