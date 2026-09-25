# Phase 3 设计 —— 数据平台地基 + 第一条调度

> 📄 **阶段 · 进行中**（P3-0 ✅ / P3-1 ✅ / P3-2 ⬜）
> **覆盖**：Phase 3 的设计基线、范围、里程碑、出口条件，以及本仓库对外部设计的适配裁定 ｜ **不覆盖**：契约字段与表结构（见 [`architecture.md`](architecture.md)）、施工过程（见 [`../tutorial/`](../tutorial/README.md)）、勾选状态（见 [`../../TODO.md`](../../TODO.md)）

> Phase 3 的**设计 SSOT**。结构性问题（存储平面选型、失败模式清单、表结构）
> 仍以 [`architecture.md`](architecture.md) 为准 —— 本文不复制它，只在偏离时指名。

---

## 0. 设计基线

**`BigA Data Architecture v1`（2026-09-25 收到）** 是 Phase 3 的设计基线。
它**取代**了此前的「Phase 3 Data Platform Foundation」草案。

两版的关键差别不在内容，在**它是怎么写出来的**：v1 是照 Phase 2 收口源码
重新扫描得到的，旧草案是从理想架构反推的。结果是旧草案有两条与现状直接冲突
（schema 从 v21 起排 —— 实际已是 v22；交易日历主源写官方端点 —— 实际那条路
在本项目的部署环境里连不通、零生产调用方），而 v1 两条都对。

🔴 **两份外部材料都不在仓库里**（`.gitignore` 排除了 `docs/external/*`）
⇒ 本文每条都自包含，不写「见外部包 §N」。

### 0.1 本仓库先做的 14 条裁定，v1 怎么处理的

2026-09-25 早些时候，我按旧草案出过一份 14 条适配裁定。v1 到手后逐条对照：

| 结果 | 条数 | 说明 |
|---|---|---|
| **v1 独立得出同样结论** | 9 | schema v23 起排 / `required_datasets` 延到 P3-7 / 注册表在代码里照 `AGENT_REGISTRY` 形状 / 日历主源是 `sina_calendar` 而 `szse` 保留 / 不新建第二个 provider 包 / Manifest 缺版本按 v1 读 / DuckDB 推迟 / 不重写 Decision Kernel / `bin/biga-data` 独立 CLI |
| **v1 更精确，按它改** | 4 | 见 §0.2 |
| **v1 未涉及，本仓库自己的裁定保留** | 1 | 退出码映射（§1.2） |

⚠️ 那 9 条是**独立到达**的 —— 我靠探活查库，v1 靠重扫源码。两条路得出同一个
结论，这件事本身比任何一条结论都更值得记：**当设计与现状冲突时，现状赢，而
查现状的成本比想象中低。**

### 0.2 v1 推翻了我的 5 处，其中 1 处是我判错

P3-0 已按 v1 全部改过。逐条记在这里，因为每一条都是可复用的判断。

#### ① 注册范围：7 个 → 2 个（我判错了）

我按「`raw_market_snapshot.source` 里真实出现过的 7 组」全收，理由是
**「只收一半，『系统里有哪些数据集』就有两份答案」**。

v1 写死：P3-0 只注册**现有持久链**的两个（`cn.trading_calendar` /
`cn.index.daily_bars`），其余「在对应迁移 PR 才激活，避免 Registry 成为愿望清单」，
并规划了一条守卫 **`no zero-consumer active dataset`**。

🔴 **v1 是对的，而且理由正是本仓库自己那条。** 我的理由把两个问题混成一个：

- 「系统里有哪些数据集」—— 今天的答案在**各 skill 的代码里**，Registry
  收不收都改变不了。
- 「Data Platform 管着哪些」—— 这才是名册该答的。

那 5 个今天是 agent 直连 provider 的遗留路径，平台对它们一无所知。收进来
等于让名册**声称管着 5 个它完全没接手的东西** —— 就是
「点名了一个不存在的东西，读者会认为这条已经有人管了」。

⇒ 待迁的 5 个记在 §2.2 的迁移清单里，各自在 P3-6 进册。

#### ② `provider_id` 从「站点前缀」改成「适配器级」（我判错的那一处的根）

我取 `raw_market_snapshot.source` 的前缀，于是新浪的日线、日历、快讯合成
一个 `sina`。**那个模型答错了它自己要答的问题**：`datasets_of("sina")` 会说
「sina 挂了影响 3 个数据集」，而那是三个可以各自独立挂的端点 ——
它系统性地**高估影响面**，而「它挂了会影响什么」正是这份名册的主要用途。

⇒ `provider_id` 与 `providers/` 下的模块同名。我原本要守的那件事
（别让同一个源有三套名字）挪到 `ProviderDefinition.source_prefix`，
由测试单向钉住。

#### ③ dataset id 命名与一处**概念**错误

| 我写的 | v1 | 差别 |
|---|---|---|
| `cn.index.quote` | `cn.index.realtime_quote` | 命名 |
| `cn.sector.rankings` | `cn.sector.board_snapshot` | 命名 |
| `cn.market.emotion_close` | `cn.market.limit_pool` + `cn.market.emotion_close` | **概念** |

最后一条不是改名：v1 把**原始涨停/炸板/跌停池**（observed，东财端点）与
**情绪收盘指标**（derived，算出来的）拆成两个 dataset。我注册的那条内容其实是
前者、名字用了后者 —— 而后者今天**根本不存在**（emotion agent 自己算，没落成
数据集）。这正是裁定 16 的 `observed` / `derived` 之分在 dataset 层的体现。

#### ④ 状态枚举分层：`DataStatus` → `DataRunStatus`

v1 另有一个 `DatasetStatus`（`COLLECTING`/`VALIDATING`/`COMPLETE`/`PARTIAL`/
`QUARANTINED`/`FAILED`/`SUPERSEDED`）描述**快照**的状态。我那个枚举描述的是
**一次执行**的终态 —— 两层东西，几个值同名。

⇒ 改名 `DataRunStatus`，`COMPLETE` → `COMPLETED`（v1 的 Data Run 终态列表就是
带 D 的，那个字母正是与 `DatasetStatus.COMPLETE` 拉开距离的地方）。
`DatasetStatus` 等 P3-1 有快照可标时再加。

#### ⑤ `coordinator.py` 的注释漂移

v1 的 gap 表点名 `Docs | coordinator 注释有历史漂移 | CLEANUP`。核实属实：
它的 docstring 写着「现在 `freeze_index_daily` **还没有生产调用方**」，
而 `orchestrator.py` 早在批 D-II 就接了。已修。

> 🔴 这类漂移的危害不是读者少知道一件事，是**读者据此做决定**：
> 一份说自己没有生产调用方的模块，看起来是可以随便改签名的。

---

## 1. 本仓库的适配裁定

v1 已经吸收了绝大部分。**仍然只在本仓库成立**的只剩下面几条。

### 1.1 systemd 单元名带 `-biga` 后缀

v1 的 systemd 一节写 `biga data run eod-daily-bars --trade-date ...`，没写单元名。
**systemd 用户单元名是两套实例共享的命名空间**（红线 R-2 第 2 处），既有单元
全部带后缀，由 [`isolation.py`](../../tools/verify/isolation.py) 的
`check_namespaces` 守着 —— **写默认名那条守卫会真的报红**。

⇒ `eod-daily-bars-biga.timer` / `.service`。装之前确认 `OPENCLAW_SYSTEMD_UNIT` 是空的。

### 1.2 退出码对齐 `_verdict.py`

v1 **没有**规定 Data Job 的退出码映射 ⇒ 这条是本仓库自己的裁定，不是偏离。

本仓库有退出码的唯一定义
（[`tools/verify/_verdict.py`](../../tools/verify/_verdict.py)）：
`0 = PASS` / `1 = FAIL（查了真的不对，去看代码）` / `2 = UNKNOWN（没查成，
去看数据与时机）`，原话是「**`2` 去查数据，`1` 去查代码**」。

| `DataRunStatus` | 退出码 | 为什么 |
|---|---:|---|
| `COMPLETED` / `SKIPPED_UP_TO_DATE` | 0 | 已经是最新不算失败 |
| `FAILED` | 1 | 去看代码 / 配置 |
| `PARTIAL` / `QUARANTINED` / `TIMEOUT` | 2 | 都是数据与时机问题 |
| `CANCELLED` | 130 | 人按的 Ctrl-C，SIGINT 惯例，**不进三态** |

🔴 `TIMEOUT → 2` 是与旧草案的实质分歧（它退 1）。超时是典型的「去看数据源 /
环境」，退 1 会让排查方向天生是错的。

⚠️ 这三个数字因此在仓库里有两个出处（L-3 的形状）⇒ 由
[`tests/test_data_registry.py`](../../tests/test_data_registry.py) 钉住两者不矛盾。
没有把 `_verdict.py` 搬进包里：它的 docstring 明确把范围限定为
「`tools/verify/` 下工具的进程退出码」，搬过来等于擅自扩大它的管辖。

### 1.3 `cn.market.limit_pool` 不许绑给 emotion 消费市场宽度

v1 已经把宽度（`cn.market.breadth`）与情绪池（`cn.market.limit_pool`）分成
两个 dataset，方向与裁定 15 一致。这里补一句仓库特有的约束：

P3-6 迁移时，`cn.market.breadth` 的消费方是 **market**，不是 emotion。
代码两处都写着这件事（[`emotion_calc.py:25`](../../skills/emotion-calc/scripts/emotion_calc.py#L25)、
[`market_calc.py:22`](../../skills/market-calc/scripts/market_calc.py#L22)），
还有一道全仓静态扫描 [`test_field_single_producer.py`](../../tests/test_field_single_producer.py) 盯着。

⚠️ 裁定 15 的但书是「约束 agent，不约束 provider」—— 所以问题不在于哪张表
存了它，而在于 `required_datasets`（P3-7）把它绑给谁消费。

### 1.4 文档落地形态

v1 是 17 份文档 + 4 份 ADR。本仓库文档规约是**一个阶段一份设计文档**
（`test_一个阶段只有一份设计文档`），且只有 `design/` `tutorial/` `guide/`
`external/` 四个类别 ⇒ 全部收进**本文**；测试清单进 `tests/` 与
`exit_conditions.py`；ADR-001 的四平面选型**不另起文件** ——
[`architecture.md`](architecture.md) §5.1 早就逐项写死了，再放一份就是 L-3。

### 1.5 范围 = 数据平台 + 第一条 cron

[`TODO.md`](../../TODO.md) 路线图原本把 Phase 3 写成
「数据层加厚 + `discipline` + 独立飞书应用 + 第一条 cron」。

⇒ Phase 3 = **数据平台 + 第一条 cron**（EOD timer 天然就是它，路线图那条出口
条件「每条 cron 都有被证明的消费方」对它直接适用且能真的验）。
`discipline`（输入源仍不存在）与独立飞书应用推到 **Phase 3b**。路线图已同步拆分。

---

## 2. 范围

### 2.1 做

```text
Dataset / Provider Registry（代码唯一源，按 Milestone 激活）
Data Run 状态机 + Raw Artifact + Partition + Quality + Dataset Snapshot 元数据
把现有 index_daily Vertical Slice 接到通用 DatasetSnapshotService
Security Master（point-in-time universe）
全市场 EOD Daily Bars：Raw 归档 → Parquet → DuckDB
Tradability + Adjustment Factors
把 agent 直连 provider 的遗留路径迁到冻结事实
SnapshotResolver + required_datasets + EvidenceSet Manifest v2
第一条 cron：eod-daily-bars-biga.timer
```

### 2.2 待迁清单（今天在跑，但**不由平台管**）

这五条是 agent 直连 provider 的遗留路径。它们**不在 Registry 里**，
各自在 P3-6 的迁移 PR 中进册；每迁一条，对应 skill 的生产路径删掉 direct fetch。

| dataset_id | 今天谁在取 | 适配器 |
|---|---|---|
| `cn.index.realtime_quote` | market | `tencent` |
| `cn.market.breadth` | market | `eastmoney` |
| `cn.sector.board_snapshot` | sector | `eastmoney` |
| `cn.market.limit_pool` | emotion | `eastmoney` |
| `cn.news.flash` | news | `sina_news` |

另有 `cn.market.emotion_close` —— 它是 **derived**（由 `limit_pool` 算出），
不是 provider 原始数据集，随 P3-6 一起定型。

### 2.3 不做（本阶段）

```text
discipline agent / 独立飞书应用   —— §1.5，推到 Phase 3b
PostgreSQL / Kafka / 多机        —— architecture.md §5.1 的触发条件一条都没成立
分钟线 / Tick
全部历史回填                     —— 只保证「从今天起每天有」
重写 Phase 2 Decision Kernel     —— v1 的硬约束，所有改动沿现有边界扩展
```

> ⚠️ 第一批 dataset 不含 news 的**平台化**。而 Phase 2 出口条件 5 卡着的
> 「盘后 198s 超预算」根因正是收盘后快讯量翻倍 ⇒ 别把 P3-6 当成顺带解决
> 延迟问题记进 Phase 3 的账，它要到 news 那一条迁完才可能有影响。

---

## 3. 里程碑

| # | 内容 | schema | 状态 |
|---|---|---|---|
| P3-0 | 契约 + Dataset/Provider Registry + `bin/biga-data` | — | ✅ |
| P3-1 | Data Run / Raw Artifact / Partition / Quality / Snapshot / EvidenceSet 链接 | **v23–v26** | ✅ |
| P3-2 | 把现有 `index_daily` 接到通用 `DatasetSnapshotService` | — | ⬜ |
| P3-3 | Security Master（point-in-time universe） | **v27** | ⬜ |
| P3-4 | 全市场 EOD + Raw 归档 + Parquet + DuckDB + **第一条 cron** | — | ⬜ |
| P3-5 | Tradability + Adjustment Factors | — | ⬜ |
| P3-6 | 迁移 §2.2 那五条 direct feed | — | ⬜ |
| P3-7 | SnapshotResolver + `required_datasets` + EvidenceSet v2 | — | ⬜ |

🔴 **每个里程碑的出口都是「旧行为回归全绿 + 新红灯测试全绿」**，
Data Platform 不允许破坏 Decision Kernel。

#### ⏩ P3-1 落地（2026-09-25）：与外部实现包合并，不是二选一

外部交付了一份 P3-0+P3-1 的完整实现。在**同一个 worktree** 里做对照实测：

```text
原始 main                1891 passed / 0 failed
加上他们的代码            1881 passed / 14 failed
```

其中 2 条是徽章未同步（无害），**12 条是真破坏**，根因两条：

1. `EvidenceSetDatasetLink` 撞铁律 4 的守卫（类名含 `Evidence` 词根）
2. `src/easyup_biga/persistence/__init__.py` 急切 import 新包 ⇒ 出卡路径依赖 `easyup_biga.data`，
   而 `test_spawn_proof.py` 的沙盒**把这个包静默丢掉了**（见下）

⇒ 判定是**合并**，不是覆盖。取他们的：schema v23–v26、`src/easyup_biga/persistence/data.py`、
Data Run 状态机、快照/分区/质量记录类型、构造时校验、分区键归一、id 工厂。
保留我的：适配器级 `provider_id` + `modules` + `source_prefix`、`consumers`
与零消费方守卫、`bin/biga-data`、退出码映射、注释与报错指路。

#### 🔴 顺带修掉一颗潜伏已久的地雷

`test_spawn_proof.py` 与 `test_scan_fallback.py` 各写了一份
`shutil.ignore_patterns(..., "data", ...)`。那个 glob 按名字匹配**任意层级** ——
本意排掉仓库根的 `data/`（SQLite 库），实际连 `src/easyup_biga/data/` 一起
**静默**丢掉。

它此前没爆，只是因为没有任何生产路径 import 那个包；P3-1 把它接进
`src/easyup_biga/persistence/__init__.py` 的那一刻就会炸，而报错指向沙盒里的临时目录，看不出是
排除规则干的。

⇒ 收成一处 `tests/_scan.py::sandbox_ignore()`，按**相对仓库根的路径**判而不是
按名字判。正反探针都验过（退回旧写法当场红）。

⚠️ 这是同一个坑的**第三个实例**——前两个（运行时总闸被复制进沙盒、构建产物
没排除）就记在那份注释里。前两次的修法都是「往名单里再加一个名字」，
而名单的**形状**一直是错的。

### 3.1 schema 分步（v1–v22 永不修改）

```text
v23  data_job_runs / data_run_events               复用 Decision Run 的 append-only + CAS
v24  provider_attempts / raw_artifacts             raw_artifacts 只存文件 metadata
                                                   小体积的 raw_market_snapshot 不迁移
v25  dataset_partitions / quality_reports / dataset_snapshots
                                                   UNIQUE(dataset_id, partition_key, data_version)
v26  evidence_set_datasets                         UNIQUE(evidence_set_id, dataset_id)
v27  fact_security_master                          append-only，按 available_at 读可见版本
```

`evidence_set_datasets`（v26）是 v1 相对旧草案的一处新增：**关键血缘要能用 SQL 查，
不能只藏在 manifest JSON 里** —— 这正是 schema v16 加 Run Provenance 三列时学到的东西。

### 3.2 P3-2 的红线

```text
现有 Agent 输出 / Run / Card 行为不变
raw hash 语义不变
手工单跑 skill 的 fallback 不突然失效
```

这一步验证的是通用框架，**不新增业务数据** —— 所以「什么都没变」就是它的成功判据。

### 3.3 P3-4 才引入的东西

`duckdb` 运行时依赖、Raw 文件归档、Parquet 数据面、EOD job。
🔴 **到这一步才加依赖**，不提前 —— `pyarrow`/`duckdb` 进来之前，
「clone 下来就能跑」这个事实还成立一天算一天。

---

## 4. 出口条件

🔴 **判据是 `python3 tools/verify/exit_conditions.py`，不是这张表。**
Phase 2 刚吃过手写状态表的亏（条件 4 达成了四天没人知道）。

| # | 条件 | 能否程序判定 |
|---|---|---|
| 1 | schema 从 v22 起追加，v1–v22 一行没改 | ✅ |
| 2 | Dataset Registry 是代码唯一源，无零消费方条目 | ✅ AST + 契约测试 |
| 3 | Provider Registry 有真实消费方 | ✅ |
| 4 | 现有 `index_daily` 走通用快照框架 | ✅ 回归 |
| 5 | Security Master 支持 point-in-time universe | ✅ |
| 6 | 全市场 EOD 走通 Raw → Normalize → Quality → Parquet → Snapshot | ✅ |
| 7 | Tradability 能把停牌与「数据缺失」分开 | ✅ |
| 8 | 复权因子与原始 OHLC 独立保存 | ✅ |
| 9 | EvidenceSet v2 **结构化**引用 DatasetSnapshot | ✅ 查库 |
| 10 | 生产路径上的 Agent 不再自己选 Provider | ✅ AST |
| 11 | Replay 全离线 | ✅ |
| 12 | 修订不改写历史快照 | ✅ |
| 13 | DuckDB 能跨日查询 EOD 分区 | ✅ |
| 14 | 默认 pytest 仍然 hermetic（无网 / 无 profile / 无 .git 可跑） | ✅ |
| 15 | Phase 2 kernel 回归全绿 | ✅ |
| 16 | **老卡 replay 仍逐张通过**（manifest 缺版本按 v1 读） | ✅ |
| 17 | 第一条 cron 有**被证明的**消费方 | ✅ 判据是调度命令的字面量 |
| 18 | 连续 5 个交易日 EOD COMPLETED | ✅ 跨天累积 |
| 19 | Primary→Fallback 演练 | 🔶 演练 |
| 20 | QUARANTINED 演练 | 🔶 演练 |
| 21 | 修订 v2 演练 | 🔶 演练 |
| 22 | Raw 篡改检测演练 | 🔶 演练 |
| 23 | source ZIP 恢复 + replay 演练 | 🔶 演练 |
| 24 | 隔离自检重跑（新增 timer ⇒ 共享命名空间那项） | ✅ |

> 🔶 演练类在脚本里**显式返回 `UNKNOWN`（退出码 2）**，不许塌进 PASS —— 红线 R-3。

完成后打 tag `v1-data-platform-foundation`，然后**停止继续打磨数据地基**，
进入 Review → Screening → CandidateSet → TradingPlan → Realtime → Backtest。

---

## 5. 开工前的门

| 门 | 状态 |
|---|---|
| `v1-architecture-baseline` tag | ✅ 已存在 |
| Full hermetic tests 0 failed | ✅ |
| Phase 2 出口条件 3（真实否决落库） | ⬜ 等一个够极端的交易日，与 Phase 3 无依赖 |
| Phase 2 出口条件 5（延迟预算第三次重推） | 🔶 待裁定，与 Phase 3 无依赖 |
| **裁定 11：换掉共享的 anthropic 凭据** | 🔴 见下 |

🔴 **凭据这条是真的门，两版外部设计的前置条件清单里都没有它。**
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

Phase 3 建的东西里，**选股 / 回测 / 复盘会直接复用**的是：point-in-time
Security Master、Trading Calendar、Daily Bars、Tradability、Adjustment Factors
这五件。它们是「以后想做什么都绕不开」的那一层 ——
这也是 Phase 3 值得在 discipline 之前做的理由。
