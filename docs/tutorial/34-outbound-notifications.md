# 第 34 章 · 外发通知 outbox（只做「推」，不接「收」）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 G-I —— 给「Card 完成 / UNKNOWN / risk 否决 / 运行失败」四类
> 事件建一条外发通道：与 Card **同事务**入队 `notification_outbox`，一个独立 worker
> 异步投递；`RunState` 加 `NOTIFICATION_PENDING`。重点在两个判断：**投递状态该不该
> 只追加**、**哪些通知该和写库同生共死、哪些该尽力而为**。
> **不覆盖**：飞书真实 API、异步执行模型（快速 ACK + 后台完成）、飞书变 trigger、
> `main` 移出路由 —— 那些都是批 G-II（Inbound Trigger），风险高一个量级，本章一概不碰。

---

## 目标 / 产出

出一张卡要 170~200 秒，而且是**同步**的（`bin/biga-card` 一路 `wait` 到 Card 落库）。
在这一章之前，卡跑完了没有任何东西会告诉你 —— 你只能守着终端。做完这一章：

- `notification_outbox`（队列）+ `notification_deliveries`（投递日志），schema **v12**，两张都只追加。
- `_contract/notify.py`：四类事件白名单 + `card_event_type()` 分类判据。
- `save_card_with_notifications()`：Card 与 outbox 行**同一个事务**入库。
- `RunState.NOTIFICATION_PENDING`，插在 `CARD_PERSISTED` 与 `COMPLETED` 之间。
- `notify_worker.py`：扫没投成的行、经一个可替换的投递接口投出、记一条尝试。桩实现
  打印到 stdout（真飞书 adapter 是下一批的事）。

一句话：**这一批只发明「把通知推出去」这件事，不碰「从飞书收进来」那件事。**

---

## 为什么这么做

### 一、投递状态：另开一张追加日志，而不是给 outbox 开一列 `delivered_at`

这是本章唯一一个「没有预先答案、得自己拍」的设计问题。两条路都摆上台面：

| | (A) outbox 纯只追加 + `notification_deliveries` 日志 | (B) outbox 开 `delivered_at`，投成 UPDATE 一次 |
|---|---|---|
| 「投没投成」 | 派生查询：deliveries 里有没有 `status='delivered'` 的行 | 读 `delivered_at` 是不是 NULL |
| 表数 | 两张 | 一张 |
| 与本仓库先例 | **一致** | 引入第一处「被允许 UPDATE 的业务表」 |

选 **(A)**。理由不是「少写一张表也要忍」，是**本仓库的状态变更本来就这么记**：

- `run_events` 就是这个先例 —— run 的「当前状态」不在 `decision_runs` 上原地 UPDATE，
  而是 `run_events` 追加一行、当前状态 = 最新一行。
- `agent_verdicts` 的修订用 `amends` 指回原件、`decision_records` 的回放用 `replay_of`
  指回原卡。**本仓库从不原地改状态**（L-8：状态一 UPDATE，「当时看到的」就永久重建不出来了）。

投递状态恰好是同一形状的小状态机：`pending → delivered`，或 `pending → failed → 重试 →
failed → … → delivered`。append 日志天然记得下「第几次投的、结果如何、什么时候」——
而 `delivered_at` 一列只留得下终值，重试了几次、为什么失败全丢了。

> 通用原则：**当一个字段要记的是「一串发生过的事」而不是「一个当前值」，
> 用追加日志、让当前值变成派生查询，不要原地 UPDATE。** 省下的那张表，换来的是
> 一份可回放的历史 —— 对一个卖点是「可追溯、可回放」的系统，这笔账永远划算。

还有一个隐性代价让 (B) 更不划算：本仓库有一条 `test_每张表都有只追加触发器`，
schema 顶部原话是「新表默认就该在清单里，**例外必须自己举手**」。走 (B) 就得给这道
守卫开一个它看不见的口子 —— 拿一道有用的守卫换一列方便。

### 二、同生共死 vs 尽力而为：两类通知的原子性不一样

分发提示词点名 P1：「写 outbox 那步失败 ⇒ Card 也不落库」。但这**只对 card 类通知**成立，
run_failed 反过来。为什么？

- **card_completed / card_unknown / risk_block**：卡落库了、通知却没入队，就成了「人以为
  一切正常、其实没人被通知」的静默洞。所以它俩必须**同一个事务** —— 要么一起进、要么
  一起回滚。落点是 `save_card_with_notifications`：一个 `connect()` 里先插卡、再插 outbox，
  中途任一步抛错，`connect()` 回滚整段。
- **run_failed**：反过来。假如「入队失败通知」这一步能回滚「这次运行失败了」这条 `run_events`
  记录，那就是**为了发一条通知、把一条真实的终态记录抹掉** —— 比漏一条通知糟得多。所以
  run_failed 在失败终态转移**之后**尽力而为地入队（`enqueue_run_failed`，best-effort、
  幂等 `aggregate=run_id`）。进程若在中间被杀，顶多漏一条通知，run 的终态已如实落库。

> 通用原则：**原子性不是越强越好，要看「两件事谁离了谁更糟」。** 卡和它的通知是「一起
> 才有意义」，所以绑死；失败终态和它的通知是「终态更重要」，所以解耦。

### 三、`NOTIFICATION_PENDING` 只代表「入队」，不代表「已送达」

`RunState` 原本**故意**没有这个状态（它的 docstring 写着「推迟到批 G，outbox 存在之前
没有消费方」）。这一批加回来，插在 `CARD_PERSISTED → COMPLETED` 中间，变成
`CARD_PERSISTED → NOTIFICATION_PENDING → COMPLETED`，并**删掉了直达那条边**（跳过它
现在是非法转移）。

但它只代表「outbox 行已入队」。`COMPLETED` 紧接其后、纯 DB、**不等 worker 真的把消息
发出去**。为什么这条边界要划死：worker 投递是异步的，一次飞书 API 抽风不能把 Run 卡在
非终态。所以：

- 入队（`NOTIFICATION_PENDING`）是快的、和其余转移一样是纯 DB 操作。
- `COMPLETED` 的达成**不依赖投递结果** —— outbox 里躺着一条没投出去的通知，Run 照样是
  COMPLETED。worker 事后另起一个进程去补投，跟 `bin/biga-card` 调用者的体验完全无关。

### 四、run_failed 写在「各自转移那里」，但只有一份实现

失败终态有三个（FAILED / TIMEOUT / CANCELLED），产生它们的地方有两处：编排器内部的
`orchestrator._fail()`（今天唯一真在产 FAILED 的）、以及 bash 经 `run_ledger.cmd_move`
收进 TIMEOUT/CANCELLED（stale-run reaper 是 Phase 3，今天这两个还没有自动生产方，但
CLI 路径先接好）。

一度想把入队塞进 `_store.transition()`（所有转移的唯一咽喉，最不会漏），但最终**没有**：

- 分发提示词要求 card 通知写在**编排层**（`persist`），run_failed 也该对称地留在编排/CLI
  层，让 `_store.transition()` 保持一个干净的状态机原语。
- 塞进 `transition()` 会让**每一次**转移（几十处）都过一遍通知判断，blast radius 太大，
  还会连累一堆现有测试。

所以两处都调**同一个** `enqueue_run_failed(run_id)`（实现只有一份，在 `_store.db`），
入队决策留在调用方。

### 五、`card_event_type`：判据是 stance，不是 status；偏向报不确定

把一张已产出的卡分到哪一类，是个纯函数，优先级互斥：

1. `risk_block` —— 任一 verdict 的 `stance == VETO_STANCE`。**判据是 stance 不是 status**：
   `status='BLOCK'` 也可能是判官自己权衡出来的（没有否决），而 veto stance 是契约里
   「制衡层否决」的唯一权威信号。
2. `card_unknown` —— 任一 verdict 数据完整度 `UNKNOWN`，或卡带着缺失项。判据取「任一/非空」
   而不是某个比例阈值，**故意偏向报不确定**（R-3：UNKNOWN/缺失必须显式推出去，不藏在一个
   `card_completed` 背后让人以为一切正常）。
3. `card_completed` —— 其余。

现状下（Phase 2 出口条件未达成，risk 常报 UNKNOWN）多数卡会落到 `card_unknown` —— 这是
**如实反映**当前数据完整度，不是 bug；也不是死分支（一张真正干净的卡就会命中 completed）。

---

## 执行

### schema v12 建出两张表

```console
$ python3 -c "import sys;sys.path.insert(0,'skills');from _store import db;print('schema ->',db.init_schema())"
schema -> 12
```

新表随 `init_schema` 幂等建出（`notification_outbox` / `notification_deliveries`），
两张都带只追加触发器（复用 `_append_only` 那对，和其余 8 张表同一套）。

### 走一遍：入队 → worker 投递

```console
$ python3 -m pytest -q tests/test_notification_outbox.py
.........................                                                 [100%]
25 passed
```

worker 默认桩把队列清空、打印到 stdout：

```
[notify] card_completed BIGA-20260923-040 :: {"decision_id":"BIGA-20260923-040"}
[notify] card_unknown  BIGA-20260923-041 :: {"decision_id":"BIGA-20260923-041"}
...
待投 4  投出 4  失败 0
```

再跑一次 worker：`待投 0` —— 已投成的不会再投（幂等由「没有 `delivered` 记录才算待投」保证）。

---

## 坑

### 坑 1 · 卡的缺失项必须上浮，测试才建得出「card_unknown」

第一版 `card_unknown` 的测试给某个 verdict 塞了 `missing=[…]`，但没同步给 `card.missing`，
构造 `DecisionCard` 当场红：

```
ValueError: Verdict 报告的缺失项没有上浮到 Card: [MissingItem('情绪源不可用', code='emotion.src')]
         —— 缺失项必须逐条显示，汇总时丢弃等于静默 fail-open
```

这是契约铁律在替你把关：verdict 说缺了什么，卡必须逐条显示。修法是把同一条缺失项也传给
卡。**这不是测试的 bug，是契约在提醒「你造的这张卡本身不合法」。**

### 坑 2 · `ON CONFLICT DO NOTHING` 命中冲突时 `lastrowid` 不可靠

幂等靠 `INSERT … ON CONFLICT(event_type, aggregate) DO NOTHING`。但冲突（没插）时
`cursor.lastrowid` 返回的是**上一次成功插入**的 rowid，不是 None —— 直接返回它会把
「其实没插」误报成「插了一行 N」。判据要落在 `cursor.rowcount`（冲突时为 0）：
`return int(cur.lastrowid) if cur.rowcount else None`。探针 D 把 `ON CONFLICT` 整个去掉后，
重复入队直接 `UNIQUE constraint failed` 抛出来 —— 幂等确实是它给的。

### 坑 3 · 加一个状态，`STATE_MEANING` 不给它一句话就红

`RunState` 加 `NOTIFICATION_PENDING` 之后，不用自己写新守卫 —— 既有的
`test_每个状态都有消费方` 立刻替你把关：`run_ledger.STATE_MEANING` 里不给它一句话，
`describe_state()` 当场抛「这个状态没有读取方」。这正是 L-1 的现成落点：**加了状态但没人读
= 一条死配置**，而这道守卫逼你当场回答「谁读它」。

### 坑 4 · 并行批次占了同一个 schema 版本号

开工时另一批（批 F）在别的工作树上未提交，也占用了 v11。这不是代码冲突（各在各的树上），
是**版本号命名空间的撞车** —— 和当年 J-I/J-II 处理 v9/v10 一模一样。批 F 先合并落地，
合并 `orchestration` 进本批工作树时按同一套先例重新编号：本批的两张新表改占 **v12**，
迁移体本身一字未动，`schema.py` 里两个版本号的注释都留了记号。

> 通用原则：**append-only 的迁移列表，版本号是共享命名空间。** 多批并行时，它和「systemd
> 单元名」「端口」一样会静默撞 —— 靠「先看对方占了没」而不是「假设自己那个号是空的」。

---

## 验证

```console
# 1. 本章新增的探针 + 不变量（P1 同事务 / P2 幂等 / P3 只追加 / P4 worker / P5 必经态）
$ python3 -m pytest -q tests/test_notification_outbox.py
25 passed

# 2. 状态机与编排器的回归（happy path 现在多一步 NOTIFICATION_PENDING）
$ python3 -m pytest -q tests/test_run_state_machine.py tests/test_orchestrator.py
109 passed

# 3. worker 能把队列清空（outbox 有一个真能跑的读取方 —— L-1）
$ python3 skills/decision-card/scripts/notify_worker.py
待投 N  投出 N  失败 0
```

每一道新守卫都单独跑过探针（把被守的东西弄坏、确认报红、还原）—— 记录见 `CHANGELOG`
批 G-I 的探针表。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 投递状态用**追加日志**（`notification_deliveries`），「投没投成」是派生查询 —— 不给 outbox 开 `delivered_at` 原地改，与 `run_events`/`amends`/`replay_of` 同一条「状态变更一律追加」先例 |
| 2 | **card 通知与 Card 同生共死**（同事务），**run_failed 尽力而为**（终态转移后入队）—— 原子性看「两件事谁离了谁更糟」，不是越强越好 |
| 3 | `NOTIFICATION_PENDING` 只代表「已入队」不代表「已投递」；`COMPLETED` 不依赖投递结果 —— 一次飞书抽风不能把 Run 卡在非终态 |
| 4 | run_failed 写在「各自转移那里」但只有**一份实现**（`enqueue_run_failed`）；没塞进 `transition()`（保持状态机原语干净、blast radius 小）|
| 5 | `card_event_type` 判 `risk_block` 用 **stance 不是 status**；判 `card_unknown` 偏向报不确定（R-3 fail-loud）|
| 6 | `ON CONFLICT DO NOTHING` 冲突时 `lastrowid` 不可靠 ⇒ 判据落在 `rowcount` |
| 7 | 加状态不用自己写守卫：`test_每个状态都有消费方` 逼你在 `STATE_MEANING` 给它一句话（L-1 现成落点）|
| 8 | append-only 迁移列表的**版本号是共享命名空间**，多批并行会静默撞 —— 先看对方占了没，别假设 |
| 9 | 这一批**只做推、不接收**：没有飞书输入、没有新攻击面、不涉及 R-2（那都是批 G-II）|
