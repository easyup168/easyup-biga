# 第 41 章 · 二轮对抗性复核：真机 PoC 攻破的两处

> 📄 **过程** · 写完即冻结
> **覆盖**：对第 40 章那次批 M 提交（`7d30639`）做的第二轮对抗性复核——不是
> 重新核实外部评审文档，是找另一个 agent 拿真实代码跑 sabotage + PoC 去攻击
> 刚提交的修复本身。修复 C-1 安全论证的真实缺陷（relaunch 可能用陈旧证据
> 合成卡）、C-2 的静默永久丢弃（`abandoned` 未计入退出码）、D-3 的 FORCE
> 开关不对称、一条名不副实的测试、给 stale-run reaper 补调度方 ｜
> **不覆盖**：B 部分（Run Provenance，另一个会话在并行处理）；A/E/F/G/H 部分
> （见第 40 章"不覆盖"与 `TODO.md`）

## 目标 / 产出

批 M（第 40 章）修完并推送之后，请另一个 agent 对刚提交的这版代码做对抗性
复核——不是"读一遍代码看着对不对"，是**真的跑 sabotage 破坏关键逻辑、真的
构造 PoC 场景跑通它**。复核交回 7 条发现，其中 2 条是真机可复现的严重 bug、
3 条是真实但较小的问题、1 条是测试命名与实际覆盖不符、1 条无害。逐条核实后
修复：

1. **C-1 的安全论证本身是假的**（最严重）：relaunch 判据只看"状态是不是失败
   终态"，但 Stage 1 完成之后的状态一样能合法转移到失败终态——这时 relaunch
   会用几小时前的旧证据合成一张时间戳写着"现在"的卡，且 spawn 核验照样通过。
2. **C-2 把"配置类错误"变成了静默永久丢弃**：`abandoned` 状态本该表示"不会
   再重试"，但没有让退出码非零——`notify-worker-biga.service` 每 2 分钟看到
   一次假绿色，永远不会有人知道一条通知被永久放弃了。
3. D-3 的 `BIGA_CARD_FORCE` 只在 wrapper 那层生效，编排器自己的 Budget Guard
   不认这个变量——"确实要跑"这条恢复路径必然失败；错误消息还会在没有 run_id
   的场景下指向一个不存在的 `--status <run_id>`。
4. 一条测试的名字宣称验证 TOCTOU 保护，实际验证的是状态机本身"终态无出边"
   这条性质——重命名并说明实际覆盖范围，把真正验证 TOCTOU 的那条讲清楚。
5. `stale_run_reaper.py` 全仓零调度方——给它配一对 systemd service/timer，
   补上 CLAUDE.md 自己那条"新增写数据模块必须有被证明的读取方"的规矩。

## 为什么这么做

### 为什么要"再来一轮"——核实不等于免检

第 40 章的方法论是"核实外部评审的断言，不盲信也不因为是评审就照单全收"。
但那一轮核实的是**评审文档**，不是**我们自己刚写的修复代码**。批 M 提交后
立刻请另一个独立的 agent 用真实代码攻击这版修复，是同一套方法论的延伸：
写完的代码同样需要对抗性验证，而不是"测试绿了就算数"——尤其是当修复本身
引入了新的、此前不存在的能力（relaunch 一个已经失败的 run）时，新能力的
边界条件正是最容易埋雷的地方。

> 通用原则：给外部输入做核实是必要的，但"我们自己写的修复"不因为出自内部
> 就天然免检——尤其是修复本身新增了某种此前不存在的行为路径时。

### Finding 1：安全论证的证明范围小于代码的判断范围

C-1 原来的注释写着"`hdr is None` 或状态是失败终态，两种情况都还没写过
fact"——这句话对第一种成立，对第二种不成立。`LEGAL_TRANSITIONS`（状态机的
合法转移图）允许 `STAGE1_COMPLETED`/`RISK_RUNNING`/`SYNTHESIZING`/
`CARD_PERSISTED` 这些 Stage 1 **之后**的在途状态直接转移到
`FAILED`/`TIMEOUT`/`CANCELLED`——而到达 `STAGE1_COMPLETED` 时，Stage 1 五个
Specialist 已经真的把 fact 写进了 `agent_verdicts`。

真机 PoC 复现了完整的坏结果：relaunch 之后，新一轮的五个 Specialist
`save_fact_bundle` 全部因为 `ux_fact_per_task_agent` 唯一约束抛
`ValueError`——但编排器**不检查 Stage 1 是否真的产出了新结果**，直接调
`latest_verdict_ids(did)`（按 `task_id` 查"最新"fact），查到的仍然是上一轮
遗留的旧 verdict_ref。fail-fast 检查（"零证据不合成"）因此不会触发，因为
`ordered` 非空——只是那些"证据"来自几小时前。最终产出一张 `generated_at`
是现在、`evidence.as_of` 是几小时前的卡，而 `agent_runs.runtime_run_id`
记的是这一轮真实的 spawn，spawn 核验照样通过（它核的是"这次真 spawn 了吗"，
不是"证据新不新鲜"）。

**这是一个从"看得出卡住了"退化成"看不出任何问题"的失败**——原来的症状
（trigger 永久回复"正在启动"）虽然烦人，但至少诚实；新增的这条路径会在
毫无异常提示的情况下产出一张看似正常的卡。

修法没有重新设计状态机，而是绕开状态名称、直接核实原始安全论证真正依赖的
那个不变量：`latest_verdict_ids(decision_id)` 是否为空。为空才是安全的
relaunch 场景；非空一律拒绝，把决定权交回人工（"事实只追加，这个决策号已经
不可能被安全地重新跑一遍"）。这个检查同时覆盖两个原有分支（`hdr is None`
与失败终态），不需要按分支分别判断——防御性地对两者一视同仁，成本很低。

**测试怎么证明这条判据不是"状态名字"而是"fact 是否存在"**：加了一组对照
测试——同样的状态转移路径（一路推进到 `STAGE1_COMPLETED` 再进 `TIMEOUT`），
一组落了 fact、一组不落。落了 fact 的那组必须拒绝 relaunch，没落的那组
必须仍然允许——如果守卫真的是在看状态名字而不是 fact 存在与否，这组对照
测试中至少有一个会给出错误答案。

#### 一个悬而未决的耦合：与"B-2"方向相反

另一个并行会话正在处理评审 B 部分（Run Provenance），其中 B-2 计划把 Fact
唯一约束从 `(task_id, agent)` 改成 `(run_id, agent)`——这样一个新 run
就能安全写自己的 fact，不需要被拒绝。这与这里的修法**方向相反**：一个是
"放开重放"，一个是"收窄重放、fail-closed 交回人工"。两者不能同时是最终
状态。当前保留后者，因为它是给**现在**这套 `(task_id, agent)` 约束的正确
安全阀，且已经修复了一个真实、可复现的严重 bug；B-2 真正落地时需要回来
重新评估这道检查是否还需要、要不要收紧到"检查这个 run_id 自己的 fact"——
不是自动删除，是要有人回来看一眼。这个耦合已经在 `inbound.py` 的注释与
设计文档追加 6 的 B 行里写清楚，供 B-2 的实施者对照。

### Finding 2：`abandoned` 曾经的"不计入退出码"理由是反的

C-2 引入 `abandoned` 状态时的原始理由是"已经处理完了，不是这次调用的
问题"——但这个理由推错了方向。`abandoned` 恰恰是**永远不会再被任何人看到**
的那一种：`undelivered_notifications()` 永久排除它，全仓没有 requeue 路径。
真机验证（收件人未配置的 `FeishuDeliverer`）证明：第一次投递尝试就直接
`abandoned`，退出码却是 0——`notify-worker-biga.service`（`Type=oneshot`
只看退出码）每 2 分钟看到一次"成功"，而一条通知已经被永久放弃。

这条格外值得注意，因为它精确地重新关闭了上一个真实生产事故
（`ec66d80`，"Card 推送从未成功过"）能被发现的那个信号来源——那次事故正是
靠"通知积压在 outbox 里"才被人注意到。`abandoned` 不计入退出码，等于把
"积压可见"换成了"积压也是绿的"。

修法很直接：`abandoned > 0` 与 `failed > 0` 同样触发非零退出码。退出码本身
不区分"还会重试"和"永远不会重试"这两种情况——人去看 `--limit` 输出或
outbox 里的 status 字段再区分。

**这条的测试覆盖也有一个真实缺口**：所有失败场景此前都经一个自造的
`_RetryableError`（文档字符串自称"模拟 `FeishuError` 的形状"）触发，从没有
一条测试把真实的 `feishu_deliverer.FeishuError` 接进 `deliver_pending`。
sabotage 验证证实了这个缺口：把 `FeishuError.__init__` 里
`self.retryable = retryable` 破坏成恒 `True`（分类逻辑整个失效），
`test_notification_outbox.py` 与 `test_feishu_deliverer.py` **两份测试
全部保持绿色**——消费方（worker 怎么处理 `.retryable`）测了，生产方（真
`FeishuError` 会不会带对 `.retryable` 值）没有任何测试把两者接在一起。补的
测试直接 `import FeishuError`（真实类，不是自造的替身）构造投递器，把这根
断掉的线接上。

> 通用原则：一个"消费方"和一个"生产方"分别有测试覆盖，不代表两者之间那根
> 线也被测过——sabotage 破坏两者之一的连接点（不是任何一方内部的逻辑），
> 是检验这根线是否真的存在的直接办法。

### Finding 3：两层守卫认同一个开关，不能只有一层认

`bin/biga-card` 打印"确实要跑：`BIGA_CARD_FORCE=1 bin/biga-card`"这条恢复
路径，但这个变量到达 `orchestrator.py` 时，D-3 新加的 `_preflight_budget()`
根本不检查它，无条件再查一遍预算——wrapper 层放行之后，编排器自己又拦一次。
人已经显式表达了"确实要跑"，还是会被拒绝。

修法对称：`_preflight_budget()` 认 `BIGA_CARD_FORCE=1`，与 wrapper 自己那道
Budget Guard 同一个开关、同一个语义——只越过预算，不越过总闸（总闸本来就
没有 FORCE 分支，是有意的：紧急停止开关不该有环境变量后门）、不越过锁
（`BIGA_CARD_NOLOCK` 是独立开关）。

同一次修复还处理了错误消息的一个误导：当 orchestrator 因为内层守卫（总闸/
预算/锁三者之一）拒绝时，退出码是 3，且**从未建过 run**（三处 return 都在
`new_run_context()` 之前）——但 wrapper 原来无论什么非零退出码都打印同一句
"查死在哪一步：`bin/biga-card --status <run_id>`"，指向一个不存在的东西。
现在 `ORCH_RC == 3` 时改打印"这次根本没建 run，没有 run_id 可查"，把"报错
要指路"这条纪律用对地方——指路指向真实存在的东西，指不出来就明说指不出来。

### Finding 4/5：一条测试的名字比它验证的范围更大；给 reaper 补调度方

`test_TOCTOU_扫描后已经自己到终态_不覆盖` 这个名字暗示它验证 TOCTOU 安全的
`expected` 取值来源，但 sabotage 证明不是：把 `reap()` 里的 `expected` 从
"扫描时的旧状态"改回"实时重读当前状态"这个已知有缺陷的第一版实现，这条
测试依然通过。原因是它测的场景是"进终态"，而终态在 `LEGAL_TRANSITIONS`
里没有任何出边——`transition(rid, 任何终态, TIMEOUT)` 无论 `expected` 传
新是旧都必然非法，这是状态机 DAG 本身的性质，不是 reaper 的 TOCTOU 保护。
真正验证这件事的是另一条 `test_扫描后状态变成别的非终态_CAS拒绝不覆盖`
——那条测试转移到一个**非终态**，naive 实现在这里真的会读到"当前状态"并
让一个正常运行的 run 被误杀。去掉两条测试名字里误导性的 `TOCTOU_` 前缀，
在第一条的文档字符串里写清楚它实际验证的是什么、指向真正承担这个职责的
那一条。

第 5 条：`stale_run_reaper.py` 全仓 grep 不到任何调度方调用它——只有文档
提及与它自己的测试。这违反 CLAUDE.md 自己那条硬规矩："新增任何写数据的
模块必须同时存在被证明的读取方，判据是调度命令的字面量"。第 40 章明确说
"D-2 只做纯函数、不接调度"是有意为之的范围限制（项目自己的
`orchestration-kickoff-prompt.md` 这么要求），但这一轮复核指出这不是纯粹
面向未来的选择——它当下就在咬人：进程被 SIGTERM 杀在非终态后，没有调度方
去跑 reaper，那个 trigger 会永久卡住，而 C-1 声称修好的正是这个症状的另一半。

Finding 1 的修复已经堵死了最危险的复合场景（reaper 手工跑一次、把带 fact
的 run 收成 TIMEOUT 之后，C-1 不会再被这个新终态误导重新拉起）——但"进程
被 kill 后没人自动收敛"这个纯粹的可用性缺口依然存在。跟用户确认后，照抄
`notify-worker-biga.timer`/`.service`（已经在跑的先例）的命名与结构，新建
`stale-run-reaper-biga.timer`/`.service` + `install_reap_timer.py`
（与 `install_notify_timer.py` 同构，两段小重复好过为两个只共享骨架、单元
名各自独立硬编码的安装脚本硬拗一个共享抽象）+ `bin/biga-reap`（固化默认值
为 `--apply`，同 `bin/biga-notify` 固化默认值为 `--deliverer feishu` 的
理由一样：运维工具的默认安全姿态是"不小心跑一下不改库"，但**调度用**的
调用不该每次都要记得带一个 flag），装上并 `enable --now`。

## 执行

```bash
# 每个 finding 各自的定向测试先跑
python3 -m pytest tests/test_inbound_trigger.py tests/test_notification_outbox.py \
  tests/test_orchestrator.py tests/test_stale_run_reaper.py tests/test_reap_timer.py -q

# sabotage 验证（每处都真的改坏、确认具体报错、再恢复）——例见"坑"一节

# 全量 + 公开审查
python3 -m pytest -q
tools/verify/audit_public.sh --worktree

# 真装 reaper 的调度（dry-run 预览过一次之后）
python3 deploy/openclaw/install_reap_timer.py --apply
systemctl --user status stale-run-reaper-biga.timer
bin/biga-reap --dry-run-only   # 对真实生产库跑一次只读排查，确认接线无误
```

## 坑

1. **badge 计数在批 M 提交时就已经错了，不是这一轮改出来的**：`docs/design/
   deterministic-orchestration.md` 新增一段分析文字（不含任何测试代码）后，
   `实测` 条数从 1379 跳到 1386——第一反应是"我这段文字怎么会影响测试数"，
   查下去发现根本不是这次改动的问题：`git stash` 掉这段文字、单独在
   `7d30639` 这个提交上跑全量测试，`实测` 已经是 1379，而三处徽章写的是
   1375。批 M 提交时新增了 `docs/tutorial/40-review-c-d-reliability.md`
   这一个新文件，触发了两条通用文档扫描测试（检查文件名规则、检查开头
   声明类别与覆盖范围）与两条教程专项测试（检查有没有出现同机已有实例的
   路径/端口、检查有没有写同机已有实例相关的内容）各自新增一个参数化 case——每新增一个 `docs/tutorial/*.md` 文件带来 4 条
   新 test case，而"写教程本身也会让测试条数变化"这件事在同步徽章时被漏掉
   了。第 40 章末尾 `# 1375 条，全绿` 那行本身在写下的那一刻就是错的（不是
   后来才过期）——按教程"写完即冻结"的纪律不去改那一行，而是在本章追加
   一条 `⏩ 后续变动` 指针说明。

2. **验证一个"名字有没有说谎"的测试，要真的跑一遍 sabotage 而不是读代码
   猜**：一开始判断"这条测试测的到底是不是 TOCTOU"是靠读 `reap()` 与
   `LEGAL_TRANSITIONS` 推出来的（终态无出边 ⇒ 任何 `expected` 值都会被
   拒绝），但项目这一路上反复验证过"推理出来的结论不能替代真的跑一遍"——
   于是真的把 `reap()` 里的 `expected` 换成实时重读，实际跑测试确认第一条
   （进终态）依然通过、第二条（进别的非终态）确实报错，两条论证都得到了
   真实输出的确认，而不是停在"应该是这样"。

3. **确认"环境变量能不能到达子进程"不能只靠 Unix 常识推理**：一开始怀疑
   `BIGA_CARD_FORCE` 到不了 `orchestrator.py` 是因为 `bin/biga-card` 没有
   `export` 它——但 `VAR=val cmd` 这种写法本来就会让子进程自然继承这个
   环境变量，不需要脚本自己再 `export` 一次。回去看 `_preflight_budget()`
   的真实实现才发现问题根本不是"传不传得到"，是这个函数压根没有一行代码
   读取这个变量——环境变量组织正确，只是没人读。这类问题空想容易走偏，
   直接读被怀疑的那段代码比推理 shell 语义更快找到真因。

## 验证

```bash
python3 -m pytest -q                          # 1402 条，全绿
tools/verify/audit_public.sh --worktree       # 十一项全绿
systemctl --user is-enabled stale-run-reaper-biga.timer   # enabled
python3 tools/verify/isolation.py             # 共享命名空间三项不重叠
```

## 本章要点

| 要点 | 一句话 |
|---|---|
| 自己的修复同样需要对抗性验证 | 核实评审文档的方法论要延伸到"核实自己刚提交的修复"，不因为出自内部就免检 |
| 安全论证的证明范围可能小于代码判断范围 | 判据抓的是"状态名字"，而真正的不变量是"fact 是否已存在"——两者在原设计里恰好重合，但状态机允许它们分离 |
| relaunch 之类的新能力天然是高风险区 | 修复本身如果引入了此前不存在的行为路径（第二次执行同一个决策），这条新路径的边界正是最该对抗性验证的地方 |
| 一个状态"不计入某种判断"的理由要倒过来想一遍 | `abandoned` 不计入退出码的原始理由（"已经处理完了"）恰好是它更需要被看见的理由（"永远不会再被看见"） |
| 消费方与生产方各自有测试不代表两者之间的线也被测过 | sabotage 破坏两者之间的连接点本身（不是任一方内部逻辑），才能验证这根线真的存在 |
| 两层守卫要么都认一个开关，要么都不认 | 人工显式的"确实要跑"如果只在外层生效，内层还是会静默拒绝，等于承诺了一条走不通的路 |
| 报错指路要指向真实存在的东西 | 退出码 3（还没建 run）和退出码 4（run 建了但失败）不能共用同一句"查 run_id"的提示 |
| 测试名字要如实描述覆盖范围 | 名字里的机制词（"TOCTOU"）如果测试实际验证的是另一层性质，删掉误导性前缀，写清楚真正覆盖的是什么 |
| "新增写数据模块必须有被证明的读取方"是可以当下咬人的，不总是面向未来 | reaper 没有调度方这件事本身没有制造新故障，但让它声称要修的那一半问题从未真正被修过 |

## ⏩ 后续变动（2026-09-24，三轮对抗性复核）

reaper 接上调度之后，"按起跑时刻判过期"这件事从理论风险变成了 live 可撞：
`find_stale_runs()` 判据原来用 `decision_runs.created_at`（起跑时刻），
不是最后一次推进的时刻——一个起跑很久、但刚刚才正常推进的健康 run 会被
误判成 stale 并收成 TIMEOUT。改用 `run_events` 最新一条的 `at`。详见
CHANGELOG 本条。
