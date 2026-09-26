# Phase 3 设计 —— 数据平台地基 + 第一条调度

> 📄 **阶段 · 进行中**（P3-0…P3-3 ✅🔶 / P3-4 🔶 / P3-5 🔶 / P3-6 ⬜ / P3-7 ⬜）
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
| P3-2 | 把现有 `index_daily` 接到通用 `DatasetSnapshotService` | — | ✅ |
| P3-3 | Security Master（point-in-time universe） | **v27** | 🔶 **链路建成，上游未探活** |
| P3-4 | 全市场 EOD + Raw 归档 + Parquet + DuckDB + **第一条 cron** | — | ✅ `cn.equity.daily_bars` 进册 + `eod-daily-bars-biga.timer` |
| P3-5 | Tradability + Adjustment Factors | — | ⬜ **三个 dataset 都没有读取方** —— 见 §3.5 |
| P3-6 | 迁移 §2.2 那五条 direct feed | — | 🔶 **6a 完成**（provider 选择进数据层），主体待真实端到端验收 |
| P3-7 | SnapshotResolver + `required_datasets` + EvidenceSet v2 | — | ⬜ |
| P3-11 | 确定性离线回放 | — | ✅ |
| P3-12 | point-in-time / 修订链审计 | — | ✅ |
| P3-13 | PIT 安全的 DuckDB 查询（`query_eod_as_of`） | — | ✅ |
| P3-14 | 完整性重算 + Specialist 采数边界 AST 判据 | — | ✅ |
| P3-15 | 演练框架 + 集中式 Primary→Fallback | — | ✅ |
| P3-16 | 哈希链式上线验收账本 | — | ✅ |
| P3-17 | 发布闸门 `tools/verify/phase3_acceptance.py` | — | ✅ |

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

#### ⏩ P3-4 / P3-5 部分落地（2026-09-26）：外部「P3-0…P3-10R 完整包」的深度评审

外部交付了一份覆盖 P3-0…P3-10R 的 source-overlay（64 个源文件）。
它的自述很诚实：`code_acceptance: PASS` / `live_acceptance: NOT RUN`，
并且明确写着「**code baseline closed，不等于 all live production acceptance completed**」。

##### 🔴 为什么不能整份覆盖

它的基线是**它自己的 P3-3**，而本仓库在 P3-0…P3-3 做过十几处结构性修正。
逐项扫描：`source_prefix` / `ProviderNotRegistered` / `throttle` /
`QUALITY_POLICIES` 在它的 overlay 里**出现在 0 个文件**。
直接覆盖 = 把那些修正**静默回退**。

⇒ 按层选择性合并，保留本仓库的模型。

##### 评审查出的五处

| # | 问题 | 性质 |
|---|---|---|
| 1 | **双发布路径**：`Phase3Publisher` 与 `DatasetSnapshotService` 逐项相同地各走一遍 `DataRun → RawArtifact → Partition → Quality → Snapshot` | 🔴 L-3，且直接违反设计 v1 风险 1 的裁定「新写一个独立 snapshot manager —— **禁止**」 |
| 2 | **P3-4 的 Parquet 读写零行为覆盖**：三条相关断言分别是「路径字符串拼得对不对」「pyproject 里有没有那个子串」「analytics 源码里有没有 `read_parquet` 这个词」 | 🔴 全落在「❌ 源码里有没有这个字符串」那一列 |
| 3 | systemd `WorkingDirectory=%h/easyup-biga` | 🔴 **路径根本不对**（本仓库在 `~/.openclaw-biga/workspace`），装上起不来 |
| 4 | 单元名 `biga-eod-daily-bars` 是 `biga-` 前缀不是 `-biga` 后缀 | 🔴 不满足 `isolation.py` 判据；又因它引用的路径不是 `.openclaw-biga`，守卫**根本不会把它算成 BigA 的单元** —— 比报红更糟 |
| 5 | `bin/biga-data` 改成 `exec python -m …` | 🔴 本机**没有 `python`**，只有 `python3`；而且它整份覆盖了既有的 `list`/`providers` |

3/4/5 是同一个形状：**这套 deploy/CLI 面从没在这个环境里跑过**。

##### 本轮合进来的

```text
data/file_store.py        Parquet/raw 文件面（同目录 temp → fsync → 读回校验 → 原子 replace）
data/records.py           各 dataset 的领域记录类型
data/publication.py       行级发布入口 —— **薄层，委托账本**
data/analytics.py         DuckDB 跨日查询
data/review_outcomes.py   确定性复盘指标
data/snapshot_resolver.py 按 EvidenceSet 解析冻结快照
data/eod_pipeline.py + datasets/{eod_daily_bars,tradability,adjustment_factors,emotion_close}
providers/eastmoney_eod.py
```

##### 消掉那条双发布路径的做法

`DatasetSnapshotService.publish()` **只做加法**（质量终态、修订链两个可选能力），
P3-2 的既有行为一个字节不变；行级发布改成 `DatasetRowPublisher`，
只管 raw 归档 + Parquet + 算发布请求，**账本交回唯一那处**。

⚠️ 顺带发现：`src/easyup_biga/data/datasets/security_master.py`（P3-3，**我上一轮自己合进来的**）
里还有**第三处**账本流程。上一轮没看出来 —— L-3 最容易在 grep 共同调用时现形，
而不是在读 diff 时。它中间还要写 fact 表，消除它需要给发布服务加一个
materialize 回调 ⇒ **独立一件事，下一轮收**。

##### 模块命名：`phase3_` 前缀全部去掉

`phase3_storage` / `phase3_models` / `phase3_publication` / `test_phase3_p34_p310`
犯的是 `docs/README.md` 那条「不许按阶段/步骤切分」的同一条：
**到 Phase 5 时这些名字只告诉你它是什么时候加的，不告诉你它是什么。**

> 判据：阶段名用在**会随阶段结束而完结**的东西上是对的
> （`tools/verify/phase1_acceptance.py` 就是那一次验收本身）；
> 用在**会一直活下去的基础设施**上就答不出问题了。

##### ⏩ Parquet 面已验证（2026-09-26 当天，duckdb 装好之后）

`pytest --run-installed tests/test_eod_pipeline.py` 现在真的：
写一份 Parquet → 断言文件落地 → **同分区同版本重写被拒**（不可变）→
用 duckdb 读回来逐项对内容。

⚠️ 那条测试我第一版写错了：`write_parquet_rows` 返回的第一个值是**路径字符串**
不是 `Path`，而签名里明明白白是 `tuple[str, str, int]` —— 我看过那个签名，
写的时候却照脑子里的印象写。装上 duckdb 一跑就 `AttributeError`。
**照签名写测试，别照印象写。** 它没装 duckdb 时是 skip，所以这个错一直藏着 ——
这也说明「标 installed 的测试」在依赖没装时**证明不了任何事**，包括它自己对不对。

##### 🔴 环境级的前提（保留，因为它仍然约束部署）

**duckdb 的装法不是「pip install 一下」。** `duckdb` 是声明了的运行时依赖，
但本仓库的生产执行模型是 `bin/*` 直接用**系统 python3** + `sys.path` 挂载
（不装包、无 venv），而这台机器的 python 是 PEP 668 externally-managed，
`pip install` 与 `--user` 都被挡。装法见 [`../guide/install.md`](../guide/install.md)。

⇒ 行为测试在 `tests/test_eod_pipeline.py`，标 `installed`。
默认 `pytest` 仍然 hermetic（2169 passed / 433 skipped），
`--run-installed` 时 2172 passed —— 多出来的三条就是它。

##### P3-6 / P3-7 **没有合**

P3-7 的 `required_datasets` 引用 `cn.sector.board_snapshot` / `cn.news.flash` /
`cn.market.limit_pool` —— 那些要等 P3-6 把五条 direct feed 迁完并进册才存在。
而 P3-6 要重写 6 个 skill + orchestrator（**生产决策路径**）。

⇒ 那是这包里风险最高的一块，单独一轮做，按设计自己的规矩
「旧行为回归全绿 + 新红灯测试全绿」逐个里程碑收。

### 3.1 schema 分步（v1–v22 永不修改）

```text
v23  data_job_runs / data_run_events               复用 Decision Run 的 append-only + CAS
v24  provider_attempts / raw_artifacts             raw_artifacts 只存文件 metadata
                                                   小体积的 raw_market_snapshot 不迁移
v25  dataset_partitions / quality_reports / dataset_snapshots
                                                   UNIQUE(dataset_id, partition_key, data_version)
v26  evidence_set_datasets                         UNIQUE(evidence_set_id, dataset_id)
v27  fact_security_master                          append-only，按 available_at 读可见版本（P3-3 ✅）
```

`evidence_set_datasets`（v26）是 v1 相对旧草案的一处新增：**关键血缘要能用 SQL 查，
不能只藏在 manifest JSON 里** —— 这正是 schema v16 加 Run Provenance 三列时学到的东西。

#### ⏩ P3-2 落地（2026-09-25）：外部实现验收，取其主体、改五处

外部交付 P3-2 实现包（**又是只跑 574 条 focused，不跑全量**）。核心设计对：
`DatasetSnapshotService` 走完整状态机、按逻辑分区幂等、`raw_artifact_id` 只在
恰好一个 artifact 时才填（**拒绝拿随便一个外键撒谎**，与仓库「不硬凑」同源）；
桥接器发布前**逐个 symbol 核对冻结 raw 的 `content_sha256` 与 manifest 逐字相同**，
不二次抓取；血缘与 EvidenceSet 主行**同事务**写入，避免半发布状态。

改掉的五处：

| # | 外部实现 | 问题 | 本仓库 |
|---|---|---|---|
| 1 | `except KeyError:` 兜 seam | `entry["snapshot_id"]` 缺失等结构性 bug **也抛 KeyError** ⇒ 被静默咽掉（R-3） | 专有异常 `ProviderNotRegistered`，只咽它 |
| 2 | `source.split(":")[0]` 当 provider_id | 站点前缀 ≠ 适配器 id。对日历会解析成 `sina` 而真实是 `sina_calendar` | `provider_for_source()` 按「本 dataset 登记了谁」求交集 |
| 3 | manifest 自报 provider 不与 raw 核对 | 血缘可以描述一个与 raw 层不符的出处 | 不一致就抛 |
| 4 | `quality_policy` 指向不存在的策略 id | 「点名一个不存在的东西」 | `src/easyup_biga/data/quality.py` 真名册 + 守卫，且每条必须写**不检查什么** |
| 5 | 同事务血缘校验又写一遍 | 与 `link_evidence_set_dataset` 里那份重复（L-3） | 收成 `db.assert_snapshot_linkable()` |

#### 🔴 我自己判错一次，记下来

我一度把外部那条「注入 fetcher + provider 未注册 ⇒ 跳过血缘」的 seam **整个删掉**，
理由是「生产路径与测试路径应当是同一条」。跑全量才发现它破了批 E-20.2 的
`test_换个provider落库的source跟着变` —— 那条测试**必须**注入一个未注册的
provider 才能验「出处不会说谎」。

⇒ seam 的**条件**是对的，错的只是它的**捕获范围**。已恢复，只改捕获。

> 教训：**「测试路径 ≠ 生产路径」不等于「那条分叉是错的」。**
> 先问那条分叉在为哪条测试服务，再决定删不删。

⚠️ 顺带修正 `cn.index.daily_bars` 的切片键：`(symbol, as_of)` → `("evidence_set_id",)`。
一次冻结发布**一个**分区（bundle 里有多个 symbol），按 symbol 切等于声称有多个。
外部 P3-2 自己也改了这个值 —— 而本仓库的 `_check_partition_keys()`
（P3-1 加的）会在写入时当场抓到它，这正是把那个字段变成承重件的收益。

#### ⏩ P3-3 落地（2026-09-26）：整条链建成，**但上游还没探活成功**

外部 P3-3 实现的设计侧有一处做得特别对，原样保留：

> **Point-in-time honesty boundary** —— 这个源只给当前在册名单、没有完整退市
> 历史，所以只宣称「从 BigA 第一次成功同步那天起」的 point-in-time，
> **不把今天的名单回填成一份历史快照**。

那正是 R-3 的正解：算不出来就说算不出来。

🔴 **但它的 `TEST_RESULTS.md` 写着 "Live Eastmoney network fetching was not
executed"** —— 整个 provider 从没打过真接口，而仓库开发流程第一条是「设计先探活」。

本仓库补做探活（2026-09-26）：`clist/get` 在三个 host 上全失败，而**同一分钟内**
兄弟端点 `ulist.np` / `push2ex` 都返回了 `rc:0` 真数据；随后因请求过密被整体
限流，未能复验。⇒ 状态记作 **未验证**，不是「不可用」。

⇒ `python3 tools/verify/security_master_probe.py` 在不被限流时复验（退出码三态）。

##### 按公开参考实现 `a-stock-data` 改掉抓取层两处

那是一份记录每个端点实测可用参数、主源/备胎顺序与风控经验的速查库
（外部设计 v1 的 §14 也把它列为 Provider 调研的参考）。

| # | 外部实现 | 依据 | 本仓库 |
|---|---|---|---|
| 1 | `ThreadPoolExecutor(2)` 并发翻约 54 页、零间隔 | 参考实现的东财统一入口是**串行 + 最小间隔 1 秒 + 抖动**，注明「避免高频被封 IP」；实测十来个请求即被整体 502 | 串行 + `http.throttle("eastmoney")` |
| 2 | `urlencode(..., safe="+:")` 让 `+` 裸上线 | query 里的裸 `+` 服务端解成**空格**（`m:1+t:2` → `m:1 t:2`）；参考实现走 requests params，编成 `%2B` | `safe=":,"`，显式 `%2B` |
| 3 | 主域 `push2delay` 在前 | 参考实现的顺序是 `push2` → `push2delay` | 对齐 |

⚠️ 顺带：它的 source 写成 `eastmoney:security_master/current` —— 那是东财的
**第三套名字**（库里既有的东财 raw 行前缀是 `em:`）。已改成 `em:`，
注册表里的适配器级 id 是 `eastmoney_security_master`，两者由
`provider_for_source()` 换算。

⚠️ 纠正我自己的一处误判：我一度说它「少发了 `po/np/fltt/invt/fid` 五个参数」。
**那是错的** —— 常量块里只列了两个，实际请求发了十个，比参考实现还多一个 `ut`。
看常量不看调用点，就是按「代码长什么样」而不是「它实际做什么」下判断。

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

#### ⏩ P3-11..P3-17 落地（2026-09-26）：外部完整包深度评审后**选择性**合并

外部交付了 P3-0..P3-17 的完整 source overlay。**不能整份覆盖** —— 它的基线是
它自己的 P3-3，逐项扫 overlay：`source_prefix` / `ProviderNotRegistered` /
`throttle` / `QUALITY_POLICIES` 出现在 **0 个文件**。覆盖等于把本仓库
P3-0..P3-3 的十几处修正静默回退。

合进来的是 P3-11..P3-17 那一层（`replay` / `lineage_audit` / `integrity` /
`manifest_compat` / `failover` / `drills` / `acceptance` / `finalizer` / `cli`），
逐个适配到本仓库的名字与口径。评审改掉的五处：

| # | 外部写法 | 问题 | 本仓库的做法 |
|---|---|---|---|
| 1 | `code_ready = not [e for e in errors if not e.startswith("five consecutive") …]` | **判据挂在错误文案上**（L-13）。改个措辞结论就翻面，且不会有测试红 | `code_errors` / `live_errors` 从头就是两个 list |
| 2 | 一张硬编码的 7 条 `provider → 模块路径` map | 注册了但不在 map 里的 provider **一个都不查** —— 守卫查的地方和它声称守的地方不是同一处 | 从 `ProviderDefinition.modules` 派生 |
| 3 | 一张手写的平行 `PROVIDER_BINDINGS` 元组 | 与 `DatasetDefinition` 的 primary/fallback/validation 三个字段是**孪生清单**，漂了不报错 | `bindings_for_dataset()` 从唯一手写处派生 |
| 4 | `datetime.now(timezone.utc)` 写验收账本 | 违反「时间一律北京时间」。这类 bug 的形状是**差 8 小时但仍然是个合法时刻**，不报错 | `now_cn()` |
| 5 | `status.startswith("FAILED")` 判尝试失败 | 按字符串形状分类。多一个 `FAILED_*` 值或改名，判据静默跟着变 | 对 `ProviderAttemptStatus` 取值做集合判定 |

另外三处**没合**：systemd 单元名是 `biga-` 前缀而非 `-biga` 后缀（R-2 的守卫
根本不会把它算成 BigA 的单元，**比报红更糟**）、`WorkingDirectory` 指向
不存在的路径、可执行文件写 `python`（本机没有这个名字）。

P3-6 / P3-7 也没合 —— P3-7 引用的三个 dataset 要等 P3-6 迁完才存在，
而 P3-6 要重写 6 个 skill + `orchestrator.py`（**生产决策路径**）。

##### 🔴 闸门第一次跑就照出上一轮的欠账

`tools/verify/phase3_acceptance.py --code-only` 第一次运行即报：

```text
❌ P3-4 尚未落地 ⇒ 注册表里没有：['cn.equity.daily_bars', 'cn.security.tradability']
❌ P3-5 尚未落地 ⇒ 注册表里没有：['cn.equity.adjustment_factors', 'cn.market.emotion_close']
```

P3-4/P3-5 的四个 dataset 模块在上一轮已合入 `src/easyup_biga/data/datasets/`，
但对应的 `DatasetDefinition` **从没进过注册表** ⇒ 它们一调 `run()` 就会在
`get_dataset()` 抛 —— **今天是死代码**。

> 这条值得记下来的不是「漏了四行注册」，而是：
> **模块合进来了 ≠ 它能跑。** 上一轮四个模块的测试都绿，因为那些测试测的是
> `write_parquet_rows` 这类不经过注册表的下层函数。
> ⇒ 「有测试」和「有能跑到底的路径」是两件事。

### 3.5 P3-5 的三个 dataset 为什么没进册 —— 它们真的没有读取方

`DatasetDefinition` 的契约把判据写死了：**答不出谁读它，这条就还不该进册**。
逐个问：

| dataset | 谁读它 |
|---|---|
| `cn.equity.daily_bars` | `analytics.query_eod_as_of` / `query_eod_between`（真扫 Parquet）✅ |
| `cn.security.tradability` | 无 —— 算出来当场用掉（日线覆盖率的分母），从没被读回过 |
| `cn.equity.adjustment_factors` | 无，连写入方都没人调 |
| `cn.market.emotion_close` | 无，同上 |

⇒ 它们不是漏注册。**触发条件**：
- `tradability` —— 筛选/复盘需要区分「停牌」与「数据缺失」的那一刻。
  那时它的 raw 必须指向**同一份 provider 响应**（与日线共用），
  不能再是把自己的输出重新序列化（见第 65 章）
- `adjustment_factors` / `emotion_close` —— 先有调用方，再谈注册

### 3.6 P3-6 拆成 6a / 6b，且 6b 不能无人值守做

**6a（已完成）**：provider 选择进数据层。`src/easyup_biga/data/client.py` 按注册表的
PRIMARY → FALLBACK 链路取数；`bin/biga-calendar` 改走它，不再 import 任何
provider（AST 判据钉住）。这一步让 `cn.trading_calendar` 声明了很久的那条
降级路径第一次真的存在，也给 `failover` 模块补上了生产消费方。

**6b（未做）**：五条盘中 direct feed 迁进 Dataset 层，重写 5 个 skill +
`orchestrator.py`。

🔴 **它不能在无人值守模式下做完**，理由不是工作量，是**验收方式**：

> 开发流程第 6 条：端到端必须开新会话且等结算。

6b 改的是**生产决策路径**。改坏了不报错，只是某天 Card 上的数不对 ——
而那种错误只有真实跑一次、看 Card 上的数才发现得了。
在拿不到那个信号的情况下把它做完，等于把一段未验证的改动推进生产路径。

⇒ 6b 单独一轮，按设计自己的规矩「旧行为回归全绿 + 新红灯测试全绿」逐个
milestone 收，每个 milestone 后跑一次真实端到端。

---

### 3.4 `tools/verify/phase3_acceptance.py` —— Phase 3 的发布闸门

它回答两个**分开的**问题，并用三态退出码把它们区分开（口径见 `_verdict.py`）：

| 退出码 | 含义 | 下一步 |
|---|---|---|
| `0` | 代码面齐了 **且** 上线证据够了 | 打 tag `v1-data-platform-foundation` |
| `1` | **代码面**有问题 | 去看代码：少了模块 / 注册表指向空气 / 依赖没声明 |
| `2` | 代码面没问题，**证据不足** | 去跑演练、去等够五个交易日 |

🔴 区分 1 和 2 是它存在的主要理由。外部实现两者都退 `1` ——
于是「还没跑够五天」和「代码写坏了」在 CI 里长一个样，而这两件事下一步完全不同。

⚠️ **它今天必然退 1**，因为 P3-4/P3-5/P3-6 的 dataset 还没进注册表。
这是诚实的红，不是坏掉的守卫 —— 按裁定 14，没达成就写 `⬜`/`🔶`，不写 `✅`。

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
