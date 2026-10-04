# 第 26 章 · 让 Specialist 改口读冻结快照

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 D-II —— 把 market/sector/technical 的 `fetch_index_daily`
> 真正切到 `read_index_daily`；为什么坏号要 fail-closed 而不是退回抓取；为什么
> `raw_hash` 取冻结集那份而不对切片重算；一条恒真检查怎么**改判据**而不是删掉；
> 一个我以为会以某种方式报红、结果从另一处报红的探针
> **不覆盖**：SnapshotCoordinator 机制本身（第 25 章）；breadth/pool/news/emotion
> 的迁移（批 D 后两段，未做）；Facts/Assessment 拆分（批 E）

---

## 目标 / 产出

- `orchestrator.py`：Stage 1 之前冻结一次（sh/sz@120），evidence_set_id 进转移 detail
  与 `RunContext`；只给日线三个 agent 的任务文本加 `--evidence-set-id`
- `market_calc.py` / `sector_calc.py` / `technical_calc.py`：各加可选 `--evidence-set-id`，
  给了就读冻结、没给就自己抓
- 读冻结时 `Evidence.raw_hash` 取冻结集登记的 `content_sha256`（新增只读访问器
  `SnapshotCoordinator.frozen_content_sha256`）
- `risk_check.py`：`CROSS_CHECK_PAIRS` 判据从「比值」改成「比 raw_hash」
- 1003 条离线测试全绿（985 → 1003）

---

## 为什么这么做

### 这一批和 D-I 的根本区别：它改的是**行为**

D-I 建好了 `SnapshotCoordinator`，但 `freeze_index_daily` 一个调用方都没有 ——
三个 skill 仍各自联网。「所有 Specialist 看同一份数据」是一句「机制上可以」的话。
这一批把它变成「这次决策真的如此」：编排器真的冻结一次，三个消费者真的读同一份。

> D-I 是「在旁边搭好、验实」，D-II 是「切生产调用点」。切错的代价不是一个测试变红，
> 是三个 Specialist 的实际输出变了 —— 所以每一处都要留一条**能报红的检查**，而不是
> 「改完就好」。

### `--evidence-set-id` 为什么是**可选**的

最容易顺手做错的地方：把「没给 evidence_set_id 就报错」当成更严格、更好的版本。

不对。手工单跑某个 skill 调试（`python3 technical_calc.py --render`）是一条**一直
存在**的路径（`spawn_check.py` 的文档就提到它）。如果「没给号就报错」，这条调试路
径会被连坐拦掉 —— 而它和「生产路径该收紧」是两件事，混成一件就是把一个正当用法
误杀。

> 通用原则：收紧一条路径时，先问「我会不会顺手拦掉另一条正当的路径」。
> 「更严格」不总是「更好」——它可能只是把两个不同的用途压成了一个。

⇒ 给了就读冻结，没给就跟今天一样自己抓。生产路径（编排器）总是给，于是生产收紧了；
调试路径不给，于是保留了。

### 但「给了坏号」必须 fail-closed —— 这才是真正危险的地方

可选不等于软弱。给了一个**读不出来**的 evidence_set_id（不存在 / 没冻这个 symbol /
冻的根数不够），skill 绝不能「读不到就退回自己抓」。

那正是最危险的失败：它会产出一张**看起来完全正常**的卡，只是那个 Specialist 偷偷
用了另一份数据 —— 而「所有人看同一份」这句话已经被打穿，没有任何地方报错。

⇒ 读冻结失败一律 `SnapshotReadError` 上抛（fail-closed）。`read_index_daily` 的
except 里**不**接它、**不**退回 `fetch_index_daily`。这是 R-3 / L-2 的形状：
算不出来必须说算不出来，不 fail-open。

> 「可选」说的是**要不要走冻结这条路**；一旦选了走，走不通就得响。
> 把「可选」误解成「走不通就悄悄换一条」，等于把开关做成了 fail-open。

### `raw_hash` 为什么取冻结集那份，不对自己读到的那截重算

三个消费者读**不同根数**：sector 2 根、market 25 根、technical 120 根。如果各自对
自己读到的那截 `payload_sha256`，会得到**三个不同的哈希** —— 而它们本该指向同一份
冻结数据。

⇒ 读冻结时，`Evidence.raw_hash` 取**冻结集登记的** `content_sha256`（整份 raw 的
指纹，freeze 时算好、记进 manifest）。三个消费者无论读几根，raw_hash 都是同一个。
为此给 `SnapshotCoordinator` 加了个只读访问器 `frozen_content_sha256(esid, symbol)`
—— 它不改 `read_index_daily` 的签名（D-I 已评审通过），只是把读端要的这份哈希暴露
出来（D-I 的读接口没暴露它，属于「签名接不上」的正当补充）。

### 那条恒真检查：**改判据**，不是删

D-I 时特意没删 `CROSS_CHECK_PAIRS`（market.sh_close ↔ technical.close），因为那时
两者还各自抓、值可能不同，检查有意义。这一批它们共享同一份冻结数据了 —— sh_close
和 close 由同一份数据算出，**值必然相等**，比值就退化成恒真死配置（L-7）。

两条路：删掉它，或改判据。删掉就丢了「谁没读冻结快照」这个信号。改判据能两者兼得：

    旧：market.sh_close 的值 == technical.close 的值？   共享后恒真
    新：market.sh_close 的 raw_hash == technical.close 的 raw_hash？

新判据下：都读冻结 ⇒ 两个 raw_hash 都是那份的指纹 ⇒ 相同 ⇒ 不报；某个悄悄退回独立
抓取 ⇒ 它的 raw_hash 出自另一份 ⇒ 不同 ⇒ 报红。它因此**不是恒真**，而是「谁没读
冻结快照」的探照灯。

> 🔴 把恒真检查改判据，**必须**补一条探针证明新判据仍会红（P2）—— 否则你不知道
> 是把它从一个恒真改成了另一个恒真。本仓库改指标时栽过的坑，全是「改完没验它会红」。

为什么比 `raw_hash` 而不是直接比 `evidence_set_id`：`Evidence` 上没有 evidence_set_id
字段，而 `raw_hash` 已经有。给 `Evidence` 加字段是批 E 的契约改动，不在这一批范围。
`raw_hash` 够用 —— 它就是「这条证据出自哪一份数据」。

### freeze 失败：整个决策 FAILED，不降级

冻结现在是「共享数据」的唯一机制。如果 freeze 抓不到（网络持续失败，`fetch_index_daily`
已经重试过 3 次），继续往下让三个 Specialist 各自抓，等于**悄悄退回 D-II 之前的世界**
——而且没人知道。⇒ freeze 失败 ⇒ 异常上抛 ⇒ 整体 FAILED。宁可不出卡，也不出一张
「以为共享、其实没有」的卡。

---

## 坑

### 坑 1 · P5 探针从我没预期的那一处报红（而且是好事）

P5 的坏行为探针：我把 technical 的冻结分支改成 `except: 退回 fetch`（模拟静默退回），
预期 `with pytest.raises(SnapshotReadError)` 会失败（DID NOT RAISE）。结果它**没**在
那里失败，而是走到后面 `assert calls["n"] == 0` 才红（`1 == 0`）。

查下来：读那一步确实被我的 `except` 吞了、退回了 fetch（calls 变 1）；但**紧接着**
设 `raw_hash` 时又调了 `frozen_content_sha256(坏号)` —— 它对坏号**同样** fail-closed，
`SnapshotReadError` 从那里抛了出来。于是 `pytest.raises` 仍然满足，但 `calls["n"]==0`
抓住了「fetch 被调了」这个真正的坏行为。

两个 fail-closed 点（读 + 取 hash）**互相兜底**：就算有人把读那步改成退回抓取，取 hash
那步还会响。这是意外的纵深防御，不是设计出来的 —— 但它印证了那条老规矩：**探针第一
次红时先看清楚它是从哪红的**，别想当然。这里如果只信「pytest.raises 过了」，会误以为
fail-closed 成立；真正的判据是 `calls["n"]==0`（没退回抓取）。

### 坑 2 · `decision_runs.evidence_set_id` 这一列填不进去

schema v7 给 `decision_runs` 留了 `evidence_set_id` 列，注释写着「批 D 填」。但
`decision_runs` 是**只追加**的，而它那一行在 `open_run`（RECEIVED）时就写了，那时
还没冻结（freeze 在 PREFLIGHTED 之后的 SNAPSHOT_FROZEN 步）。只追加表不能事后 UPDATE，
freeze 又必然在 open_run 之后 ⇒ 这一列填不进去。

⇒ evidence_set_id 的持久记录落在**两处**：`run_events` 里 SNAPSHOT_FROZEN 那次转移的
`detail`，和 `evidence_sets` 表自己（带 decision_id）。`RunContext` 在内存里也更新了
（`dataclasses.replace`），供本次运行下游用。`decision_runs.evidence_set_id` 保持 NULL
—— 不是漏了，是它的写入时机和 freeze 的发生时机对不上，硬填要么破坏只追加、要么把
freeze 提前到预检之前（更糟）。已记进 `TODO.md`。

### 坑 3 · 「哪几个 agent 带 --evidence-set-id」是一份会漂的清单

`orchestrator.py` 里 `SNAPSHOT_INDEX_AGENTS = {market, sector, technical}` 决定给谁的
任务文本加 `--evidence-set-id`。它必须和「哪几个 skill 真的接了这个参数」一致。加了
一个新的日线消费者却忘了改这里，它就会各自抓、悄悄不共享。

这份清单没有权威源可派生（没有「哪些 skill 接了 --evidence-set-id」的注册表），所以
靠探针兜底：P1 断言三条日线证据反查到同一个冻结集 —— 谁漏了，它的 raw_hash 会对不上，
当场红。**把局限写出来**（同 `test_field_single_producer` 的字段清单），比假装它不会漂好。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. D-II 接线探针（P1–P5）
python3 -m pytest tests/test_snapshot_wiring.py -q          # 期望全绿
# 2. 编排器真的冻结一次、三个 skill 标准路径没坏
python3 -m pytest tests/test_orchestrator.py tests/test_market_calc.py \
                  tests/test_sector_calc.py tests/test_technical_calc.py \
                  tests/test_risk_check.py -q                # 期望全绿

# 3. 手工单跑一个 skill 不给 --evidence-set-id 仍能出结果（调试路径没被拦）
#    —— 需要联网，放这里当手工验证，不进 pytest
python3 skills/technical-calc/scripts/technical_calc.py --no-store --render 2>&1 | tail -5
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | D-I 搭机制、D-II 切调用点 —— 这一批改的是三个 skill 的**实际行为**，风险高 |
| 2 | `--evidence-set-id` 可选：生产路径总给（收紧），调试单跑不给（保留）；「更严格」不总是「更好」 |
| 3 | 但给了坏号必须 fail-closed（`SnapshotReadError` 上抛），**绝不静默退回独立抓取** —— 那才是最危险的 fail-open |
| 4 | 读冻结的 `raw_hash` 取冻结集登记的整份指纹，不对自己读到的那截重算 —— 否则读不同根数会得到不同哈希 |
| 5 | 恒真检查**改判据**（值→raw_hash）而不是删，才能既不恒真、又保住「谁没读冻结」的信号；改完必须探针验它仍会红 |
| 6 | freeze 失败 ⇒ 整体 FAILED，不降级各自抓 —— 否则悄悄退回 D-II 之前的世界 |
| 7 | 坑：两个 fail-closed 点互相兜底，P5 从没预期的那处报红 —— 探针第一次红先看清它从哪红 |
| 8 | 坑：`decision_runs.evidence_set_id` 因「只追加 + freeze 晚于 open_run」填不进去，记录落在 run_events.detail + evidence_sets |
