# 第 40 章 · 飞书可靠性 + 生命周期收敛（外部评审 C/D 部分）

> 📄 **过程** · 写完即冻结
> **覆盖**：对一份外部专家评审的 C（飞书可靠性）/D（生命周期收敛）两部分做独立核实，
> 修复 Trigger 幂等中毒、通知无限重试、TIMEOUT 误分类、无人收敛的非终态 run、
> 安全闸门可被绕过五个具体问题 ｜
> **不覆盖**：评审 A（发布基线）/B（Run Provenance）/E（Contract 与数据质量）/
> F（Package 与 Registry）部分——已核实但暂不处理，见 `TODO.md`；stale-run reaper
> 的调度接入（本批明确只做纯函数，不接 cron/systemd timer）

## 目标 / 产出

对一份外部专家评审（`docs/external/biga-latest-deep-review-classified/`，本地留存不进仓库）的
C（飞书可靠性）与 D（生命周期收敛）两部分做独立核实，确认哪些是真实问题、哪些评审判断需要修正，
然后修复五个具体点：

1. 飞书 Trigger 幂等中毒——占号成功但 `orchestrator.py` 从未启动到时，同一 trigger 永久卡在
   "正在启动"。
2. 飞书通知无限重试 + Worker 假成功——失败没有分类、没有上限，`main()` 恒定 `exit 0`。
3. `TIMEOUT` 被错误分类为 `FAILED`——状态机早就定义了这个终态，编排器却从未用过。
4. 外部信号杀掉进程后，无人收敛非终态 run（stale-run reaper）。
5. 直接调用 `orchestrator.py` 可以绕过 `bin/biga-card` 的 Budget/flock/总闸三道守卫。

## 为什么这么做

### 核实优先于相信

评审报告很长（27 节 + A-H 八阶段修复清单），不能拿到就当真、也不能因为是"外部意见"就照单全收。
用两个并行的 Explore agent 逐条核对评审断言与当前代码的真实状态，发现：

- **评审整体可信度很高**——不是泛泛而谈。比如"通知无限重试"这条，另一个并行会话在处理真实用户
  报障时独立发现了同一个问题（`notify-worker-biga.service` 缺 `EnvironmentFile` 导致推送链路
  自上线起从未成功过），跟评审描述完全对应。
- 但也有需要修正的地方："Package 重组已引发 monkeypatch 错位"这条评审的"已经引发"部分查无实据
  ——项目反而已经用 shim identity-aliasing 做了预防性设计，现有测试正常依赖它工作。
- 评审的测试环境是一个 zip 快照（`Review Target: easyup-biga-main(1).zip`），不是 git checkout。
  在真实 git 环境下完整跑一次 `pytest -q`，除了与评审无关的环境噪音外全部通过——"完整 pytest
  不能全绿"这条的紧迫性被 zip 环境放大了。

> 通用原则：外部评审的价值在于"指出你没看到的角落"，不在于"报告本身就是真相"。
> 核实成本远低于在错误前提上返工的成本。

### C-1：Trigger 幂等中毒——为什么选简单方案，不建新状态机

评审建议给 Trigger 建一套 `RESERVED/LAUNCHING/STARTED/LAUNCH_FAILED` 状态机，责任分散在
`bin/biga-card` 四道守卫的每个拒绝分支里各自上报状态。这个方案的问题是：**责任分散意味着容易漏**
——四个守卫拒绝路径里少写一处上报，这套状态机就会有盲区。

选的方案更简单：复用已经存在的 `decision_ids.reserved_at`，加一个超时阈值判断。
`accept_trigger()` 原来的逻辑是"查得到 run 就报当前状态，查不到就报'正在启动'，永远不重试"；
改成"`hdr is None`（守卫拒绝，从未启动到）或者 `state` 已经是失败终态，且占号超过
`LAUNCH_RETRY_TIMEOUT_SEC`（900s）——允许重新拉起一次"。

阈值 900s 不是随便定的：要明显大于 Budget Guard 的 `INFLIGHT_SEC`（600s）和硬超时预算
`CARD_DEADLINE_SEC+60`（840s），这样 reaper 有时间先把卡住的 run 收敛成终态，Budget Guard 的
inflight 窗口也早已过期，两者不会互相打架。

这个方案安全的关键论证：`hdr is None` 时 `decision_runs` 里根本没有这一行，说明
`open_run()` 从未调用、Stage 1 没写过任何 fact；`state` 是失败终态时同理——不会再写新数据。
"重新拉起产生第二个 run" 在这两种精确场景下都不会撞上 B 部分（Run Provenance）核实过的
`ux_fact_per_task_agent`（按 `(task_id, agent)` 的唯一约束）。这也是为什么 C-1 不需要先做
B 部分的 Run Provenance 闭环才能安全落地。

### C-2：引入 `abandoned` 状态，不是加个计数器就完事

`notification_deliveries.status` 原来只有二值 `'delivered'`/`'failed'`。评审要求"非 retryable
错误不该无限重试"，直觉的做法是在 `undelivered_notifications()` 里叠加一个 `attempt_count` 判断
——但这样"投递失败"和"已经放弃"两件事仍然共用同一个 `'failed'` 状态，无法从库表直接区分。
加第三个值 `'abandoned'` 是更小的改动、语义更清楚：`record_delivery` 的白名单从两值变三值，
`undelivered_notifications()` 排除 `abandoned` 的行即可。

`FeishuError` 也从"一个裸 `RuntimeError`"改成携带 `retryable: bool` 属性——缺配置这类错误
`retryable=False`（重试不会变好，下次还是同一份 live 配置）；`bin/biga message send` 非零退出
`retryable=True`（可能是网络抖动）。异常没有这个属性时（比如 `StdoutDeliverer` 抛的普通异常）
默认当 `True`——判不清该不该放弃时，宁可继续重试，不要武断放弃一条本来还有机会的通知（R-3 方向）。

### D-1：`OrchestratorTimeout` 必须是子类，且必须排在父类之前

状态机早就定义了 `RunState.TIMEOUT`，`orchestrator.py` 却只有一个 `OrchestratorError`，超时和
其他失败共用它，`run()` 的 except 链因此把两者都转成 `FAILED`。修法是加一个
`OrchestratorTimeout(OrchestratorError)` 子类，`_stage_timeout()` 预算耗尽时抛这个而不是基类。

容易漏的细节：`run()` 里 `except OrchestratorTimeout` 这个分支必须写在 `except
OrchestratorError` **之前**——Python 的异常匹配是顺序尝试，子类分支排在父类分支之后就永远进不去
（父类分支会先把它接住）。这条踩过：第一版写完直接跑测试，因为端到端测试本身设计成"总能捕获到
某个 OrchestratorError"，两种顺序表面上看起来"都通过"，只有专门断言"最终状态必须是 TIMEOUT 不是
FAILED"的测试才能抓出顺序错误——这也是为什么 sabotage 验证要真的删掉正确分支、看测试是否报出
`assert 'FAILED' == 'TIMEOUT'` 这种具体错误，不能只看"测试绿了"就算数。

只覆盖"应用层能预见的超时"（`_stage_timeout` 主动判断）；进程被外部信号杀掉来不及抛任何异常，
是 D-2 的范围。

### D-2：只写纯函数，不接调度——这不是偷懒，是项目自己定的范围

`docs/guide/orchestration-kickoff-prompt.md` 里早就有一条明确指示："可以写一个纯函数……但不要
接 cron 或 systemd timer——那是批 G 的地盘"。这次严格照办：`tools/maintenance/stale_run_reaper.py`
只有 `find_stale_runs()`（扫描）+ `reap()`（转移）+ 一个 `--apply`/`--threshold-sec` 的 CLI，
**不**创建任何 `.service`/`.timer` 文件。调度接入留给未来某一批。

### 一个真实的设计缺陷：TOCTOU 保护用错了 `expected`

第一版 `reap()` 的逻辑是：对每个候选 run，重新调用 `current_state()` 拿到"当前真实状态"当
`transition()` 的 `expected` 参数，想法是"两次读取之间状态变了，CAS 会因为 expected 不匹配而
拒绝"。写完自己的 sabotage 测试才发现这个想法是错的：**重新读取的"当前状态"永远等于"当前状态"
本身**——如果一个 run 在扫描之后确实从 `PREFLIGHTED` 推进到了 `STAGE1_RUNNING`（说明它还在正常
运行，不该被打扰），重新读取会拿到 `STAGE1_RUNNING`，`transition(run_id, "STAGE1_RUNNING",
TIMEOUT, ...)` 是一个合法转移、expected 也匹配，于是**真的会执行**，把一个正常运行中的 run 误杀
成 TIMEOUT。CAS 保护形同虚设。

正确的做法是用 `find_stale_runs()` **扫描那一刻**读到的旧状态做 `expected`。这样如果两次之间
状态真的变了（无论是变成终态还是变成别的非终态），`expected` 就对不上当前真实状态，CAS 才会
如实拒绝。

> 通用原则：CAS（compare-and-set）的 `expected` 必须是"你观察到、准备据此行动的那个旧值"，
> 不能是"行动前再看一眼的新值"——后者会让比较的两边永远相等，保护机制名存实亡。

### 一个真实的 POSIX 陷阱：flock 不能简单"下沉"

Budget 检查（`budget.check_budget()`）和 Ownership 检查（`entry_guard.classify_caller()`）
都是纯判断，下沉到 `orchestrator.py` 只是多调用一次，没有副作用。**flock 不是**——它是排他资源。
`bin/biga-card` 用 `exec 9>"$ROOT/.biga-card.lock"` + `flock -n 9` 拿到锁；如果
`orchestrator.py` 自己重新 `open()` 同一个路径再 `flock`，这是一次全新的 file description，
POSIX 语义下与父进程持有的锁是**互斥**关系，不是共享关系——正常出卡也会被自己的 Python 层拒绝，
这是真实会立刻炸的 bug，不是理论风险。

解法：`bin/biga-card` 拿到锁后设置环境变量 `BIGA_CARD_LOCK_HELD=1` 再调子进程；`orchestrator.py`
看到这个变量就信任锁已经被持有，跳过自检；没有这个变量（绕过 `bin/biga-card` 直接跑）才自己
`open()` + `flock`。这跟 `entry_guard` 靠环境变量识别调用方是同一种纵深防御哲学——信任协作方
如实设置，不是绝对安全边界。

## 执行

三个 Explore agent 并行核实评审 C/D 部分的具体断言（每个花了 6-11 分钟、50-65 次工具调用），
核实完成后进入 Plan 模式做设计探活，然后按 C-1 → C-2 → D-1 → D-2 → D-3 顺序实施，每项完成后：

```bash
python3 -m pytest tests/test_<相关文件>.py -q   # 先跑局部
# sabotage：临时改坏关键逻辑，确认新测试真的会红
python3 -m pytest -q                             # 再跑全量，确认无回归
```

## 坑

1. **探针字符串误伤已有测试**：`test_entry_guard.py::test_守卫排在第一次花钱之前` 用"某个
   字符串第一次出现的位置"判断 `bin/biga-card` 里几道守卫的顺序。给锁检查块加的新注释里提前
   写了 `orchestrator.py` 这个词，导致这条测试认为"付费调用"发生在"预算检查"之前——防护顺序
   判据是真的，我的注释用词才是问题。改用不含目标字符串的表述（"编排器"而不是文件名）解决。

2. **sabotage 本身写错，第一次验证是假阳性**：删掉 `bin/biga-card` 里 `export
   BIGA_CARD_LOCK_HELD=1` 这一行时，随手把它换成一条提到这行内容的注释——这条注释仍然包含
   字符串 `BIGA_CARD_LOCK_HELD=1`，导致基于字符串存在性的测试断言仍然通过，sabotage 没有真的
   验证到任何东西。改用不含目标字符串的占位符（`: # no-op`）重做一次才抓到真实的失败。

3. **跨测试锁污染**：`orchestrator.py::main()` 新增的单实例锁默认查真实仓库根的
   `.biga-card.lock`。同一个 pytest 进程里，一个测试调用 `main()` 拿到锁后（模块级 `_lock_fh`
   活到进程结束，不会因为函数返回就释放），同一文件里后面的测试会被这把锁跨测试拒绝。修法是让
   `db` fixture 同时把 `BIGA_STOP_FILE`/`BIGA_CARD_LOCK_FILE` 隔离到各自测试的临时目录。

## 验证

```bash
# 三个新测试文件 + 修改过的旧文件
python3 -m pytest tests/test_inbound_trigger.py tests/test_notification_outbox.py \
  tests/test_orchestrator.py tests/test_stale_run_reaper.py tests/test_entry_guard.py -q

# 全量
python3 -m pytest -q      # 1375 条，全绿

# 公开仓库审查
tools/verify/audit_public.sh --worktree
```

## 本章要点

| 要点 | 一句话 |
|---|---|
| 核实优先于相信 | 外部评审逐条核实，发现"已引发 monkeypatch 错位"缺乏实据、"完整 pytest 不绿"是 zip 环境的假阳性 |
| Trigger 重试范围要精确 | 只在"从未启动到"或"已是失败终态"两种精确场景下允许重试——这两种场景天然不会撞 Fact 唯一约束 |
| 加状态值优于叠加计数判断 | `abandoned` 让"投递失败"和"已放弃"在库表里可直接区分，不用每次现算 |
| 异常子类必须排在父类之前 | `except 子类` 写在 `except 父类` 后面，那个分支永远进不去 |
| 只做既定范围内的事 | reaper 严格只做纯函数，不接调度——项目自己已经划过这条线 |
| CAS 的 expected 要用旧值 | 重新读取的"当前状态"保护不了 TOCTOU，必须是扫描时刻的快照 |
| flock 不能简单下沉 | 排他资源不是纯判断，子进程重新加锁会跟父进程互斥，需要显式环境变量传递"锁已持有" |
| 探针要用不含目标字符串的 sabotage | 用注释描述被删的内容，注释本身可能让基于字符串匹配的测试假阳性通过 |
