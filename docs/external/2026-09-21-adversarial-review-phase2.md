# EasyUp BigA Phase 2 —— 对抗性评审报告

> 📄 **只读** · 外部材料，永不修改
> **覆盖**：针对 `phase2` 分支 commit `f7f7d1e` 的一次对抗性工程评审（2026-09-21）——五个攻击面（守卫是否真的会红 / 检查是否"无事可查却报绿" / 静默 fail-open / 契约与数据层 / Agent 契约与实际行为）的完整发现 ｜ **不覆盖**：是否采纳、怎么修（见 `TODO.md` 与后续 commit）；评审方法本身的工具细节

> 评审对象：`easyup168/easyup-biga`，`phase2` 分支
> 评审提示词：[`../guide/review-prompt.md`](../guide/review-prompt.md)
> 评审日期：2026-09-21

---

## 〇、方法论

- 五个攻击面并行执行，每个攻击面在**独立的 git worktree 副本**里工作（可以自由地临时改坏代码、构造边界输入、跑并发压测，互不干扰，也不影响主仓库）。
- 基线：`python3 -m pytest -q` → **447 passed**（对照发现 F22：这个数字与两份现存文档的说法都不一致）。
- **一个需要如实披露的方法论意外**：**5 个 worktree 全部**初始 checkout 落在同一个落后 `phase2` 尖端 44 个提交的旧引用上（缺市场/板块/技术面/风控五个 skill）。五路评审全部在开工阶段各自独立发现了这一点（评审提示词要求"先跑 `git log -1 --oneline` 确认状态"起了作用），并各自用不同的手段取出真正的 `phase2` 尖端内容重新验证——`git show <ref>:<path>`、`git archive`、`git worktree add --detach` 到 scratchpad、`git fetch && git reset --hard origin/phase2`、`git reset --hard phase2`。过程中其中一路还反向抓到了自己的一次假阳性（对照旧版 AGENTS.md 准备报告"命令示例过期"，对齐后发现现版本已经修好，已丢弃）。**下文全部结论均基于 `f7f7d1e` 的真实内容**，但这次意外本身被记为发现 F20（评审工具自身的一个盲区，与项目代码无关）。
- 安全边界：全程未以写模式打开过同机另一套独立实例的任何文件；数据库实验一律走 `tools/verify/probe.sh` 的一次性库；未执行任何真实 `bin/biga-card` 出卡（不产生真实 spawn 成本）。

---

## 一、发现（按严重度排序，共 23 条）

### 🔴 高危 —— 会产生错误但看起来正常的结论（7 条）

```
[F1] decision_ids 是唯一没有 append-only 触发器保护的核心表，一条普通 DELETE 就能重现 L-11 事故
  证据 —— 两个攻击面（守卫是否会红 / 契约与数据层）各自独立发现并复现。
  skills/_store/schema.py 里 V1 的三张表、V3 的 agent_verdicts 建表后都紧跟拼接了
  `_append_only("decision_records", ...)` 等调用；唯独 V4 的 decision_ids 建表 SQL
  里没有任何 _append_only 调用。用 tools/verify/probe.sh 在一次性库上逐表尝试
  UPDATE/DELETE：decision_records / agent_runs / raw_market_snapshot / agent_verdicts
  全部被 AppendOnlyViolation 正确拦下；唯独 decision_ids 的 UPDATE 和 DELETE 都
  "未报错！"直接成功。
  两个攻击面都用同一个探针脚本完整复现了完整事故链：
    [run-A] 占到 BIGA-20260921-001（run-A 还没跑完、没出卡）
    [维护动作] 删掉了 BIGA-20260921-001 的预留行（无需绕过任何东西，一条普通 DELETE）
    [run-B] 占到 BIGA-20260921-001
    *** 重号：run-A 与 run-B 拿到了同一个 decision_id ***
  这正是 architecture.md §5.3.2 记录的那次真实生产事故（2026-09-21 两次端到端相隔
  两分钟共用同一条判定原件）的完整复现机制。
  失败场景 —— 任何在 decision_records 落卡之前对 decision_ids 做 DELETE/UPDATE 的代码
  （最自然的触发源：一个"清理过期占位"的运维脚本——没有任何地方说过这张表不能删），
  都会让号被重新分配。更糟的是：号一旦被合法复用，两次运行的 verdict 现在**共享同一个
  task_id**——而 synthesize.py 用来发现"混血"的检测（`len({v.task_id for v in verdicts}) > 1`）
  恰恰是靠 task_id 不一致来报警的，号一旦复用这个检测天然失明，因为从系统视角看这就是
  一次干净、内部自洽的决策：agent 齐全、时间戳相近、缺失项照常上浮。
  为什么现有守卫没抓到 —— 没有任何测试对 decision_ids 做过 UPDATE/DELETE 探针；
  test_decision_id_ownership.py 只测占号分配是否重复，不测这一行本身能不能被改写。
  CLAUDE.md 当前状态表写的是"schema v4，五张表，只追加由触发器强制"——这句话本身就是
  错的（见收尾三问第 2 条）。
  修复成本 —— 极低：在 schema.py 里补一行 `_append_only("decision_ids", ...)`，
  照抄其余四张表已验证过的模式。
```

```
[F2] tools/verify/isolation.py 对项目"第一不变式"I-1 的验证，在目标真的不存在时会无差别判真
  证据（攻击面：检查是否无事可查却报绿）—— 环境里确实有真网关在跑（pid 12488/12629）。
  用同一份判据（cwd 在 BIGA 根下，或 cmdline 含 BIGA 路径字符串）现场重放，但显式
  排除这两个真网关 pid、模拟"网关这一刻没在跑"：
    排除真实网关后，仍被判定为 BigA 进程的数量: 10
    checked fds=251, offenders=0
    若排除真实网关，I-1 仍会判定为 ✅ 通过： True
  剩下的 10 个"BigA 进程"是 VSCode server、bash、Claude Code 原生二进制——纯属因为
  评审会话本身 cwd 就在这个仓库里。也就是说：只要有人开着终端/编辑器 cwd 在
  `~/.openclaw-biga` 下，即使真实网关早已崩溃/未启动，I-1 也会报"检查了 N 个进程，
  ✅ 通过"，而这 N 个里可能一个真正的 BigA 运行时都没有——它验证的其实是
  "我这个终端会话没有泄漏 fd 到邻居"，这几乎永远为真，与真实网关是否安全无关。
  紧邻的**端口检查**问题更直接：剔除双方已知端口模拟"此刻两边网关都没在监听"：
    模拟双方网关都未运行后 ours=set() theirs=set()，clash=set()
    端口检查仍会判 ✅ 不冲突： True
  I-1 至少还写了一行 `if not pids: 判 False` 的护栏（模块 docstring 也专门讲过
  "前缀陷阱"的教训），紧挨着它的端口检查完全没有对应的"if not listening: ..."分支。
  失败场景 —— 机器刚启动、两边网关都还没拉起来，或 CLAUDE.md 描述的 kill -9 演练
  场景中，这两项检查会稳定报"✅ 全绿"，且没有任何文案提示"其实什么都没在监听/运行"。
  为什么现有守卫没抓到 —— I-1 的护栏防住了它设计时想到的那个反例（0 个进程），
  没有防住一个更常见的反例（找到了，但找到的是无关的东西）；端口检查连"0 个反例"
  都没防。这类检查正是 CLAUDE.md 指示"改动环境后跑一次"的第一道关卡——它本身
  不可靠，意味着这道关卡此刻不能证明自己声称要证明的事。根因与 F18/F19 相关。
```

```
[F3] "agent_runs 表是唯一凭证"这条根契约硬约束，Phase 2 新增的五个 specialist 完全没有自动化验证
  证据（攻击面：Agent 契约与实际行为）—— 根 AGENTS.md 写着："每次调用都会在 agent_runs
  表留下一行。那张表是唯一凭证——你在回答里声称调用过，不算数。"但真正写这行的代码
  （synthesize.py 的 record_verdict_run）只是把 verdict JSON 自带的 agent 字段字符串
  抄进表里，`started_at`/`finished_at` 被写死成 Card 生成的同一时刻，不是真实起止时间。
  真正能做交叉验证的 tools/verify/phase1_acceptance.py::check_1_spawned（核对 OpenClaw
  运行时自己的 subagent_runs 表——那张表由 spawn 机制写入，BigA 业务代码碰不到它）
  ①只能手工调用，不在 pytest（pyproject.toml 的 testpaths=["tests"] 不含 tools/verify/）、
  不在任何 git hook 里；②硬编码只查 "emotion" 一个 agent 名字，Phase 2 新增的
  market/sector/technical/news/risk 完全没有对应版本。
  失败场景 —— Supervisor（或任何手工调试的人）不走真实 spawn，而是直接跑
  `python3 skills/emotion-calc/scripts/emotion_calc.py --task-id ...` 自己算一遍，
  拿到 verdict_ref 喂给 synthesize.py，产出的 Card 和 agent_runs 记录与"真的 spawn 了
  emotion"的情况逐字节相同——没有任何环节能分辨这两种情况。
  为什么现有守卫没抓到 —— 唯一能做真区分的检查从 Phase 1 起就没有随 agent 数量增长
  而扩展，也从未被纳入自动化流程。Phase 2 现在产出的每一张 Card，"Specialist 真的被
  调用过"这条最硬的约束事实上没有任何机器在管。
```

```
[F4] raw_market_snapshot 的 as_of 与 Evidence 层分裂：今天刚修的 af91a0d 是打了个补丁，不是通用机制
  证据（攻击面：静默 fail-open）—— `git show af91a0d` 显示这次修复给 market_calc.py /
  sector_calc.py 各自的 Evidence 构造加了 add_live()，用真实抓取到的 qdate 现算 as_of。
  但同一次提交没有改 save_raw_snapshot 的调用——那里仍用旧的、全局共享的 as_of 变量。
  实测（探针库连续跑两次 market_calc.py）：
    raw_market_snapshot 表：
      snapshot_id=4  source=em:push2delay/ulist.np  as_of=2026-09-18T15:00:00+08:00
      snapshot_id=6  source=em:push2delay/ulist.np  as_of=2026-09-18T15:00:00+08:00
    两行 content_sha256 不同，payload 也确实不同（是两份真实不同的市场快照），
    却共用同一个 as_of 键——而这个 (source, as_of) 组合正是 ix_raw_source_asof
    索引存在的理由（按"某一刻的原始响应"查询）。
    更进一步：tencent:quote 这个**有自己日期**的源也中招——payload 里嵌的时间戳
    明明是 20260921144345（今天），raw_market_snapshot.as_of 却写着上周五。
    market_calc.py 里已经算出、也已经用来做交叉校验的 q.trade_date="20260921"，
    到了落 raw 这一步被弃之不用，回退成日线的 as_of。
  失败场景 —— 只要 market_calc/sector_calc 一天跑不止一次（盘中多次调用是正常用法），
  这几个 source 的 raw_market_snapshot 记录就会不断在同一个 as_of 键下堆积互不相同的
  payload。一旦 Evidence.raw_hash（已知暂无消费方）将来接上消费方，或任何回放/审计
  工具想按 as_of 对齐 raw 层，拿到的是内容对不上却看起来正常的一行。
  为什么现有守卫没抓到 —— 测 Evidence 层 as_of 的测试和测存储层 save_raw_snapshot
  的测试各自全绿，bug 正好落在两者之间从未被同时检查过的缝隙里。对照组：
  emotion_calc.py 的 collect_pool() 在存 raw 前会用该池自己的 qdate 现算 as_of
  （逐源正确）——证明代码库里已经有"对"的写法，af91a0d 改 market/sector 时
  没有把这个模式搬过去，只改了 Evidence 构造那一层，没有触及几十行外那条并行路径。
```

```
[F5] technical-calc 的 60 日高/低点距离没有量级围栏，单根坏 tick 就能吃出荒谬数字，verdict 仍 PASS/无 warning
  证据（攻击面：静默 fail-open）—— 构造 120 根正常日线（3000 附近连续上涨），
  仅把窗口内第 91 根（不是最新一根）的 low 改成 0.01（正数，不触发任何"≤0"守卫、
  不会撞 ZeroDivisionError——正是本项目自己在别处反复强调的"rc=0、字段齐全、
  类型正确"那类垃圾值的形状）。跑 technical_calc.build_verdict()：
    verdict: PASS completed / missing: [] / warnings: []
    dist_to_low60_pct = 31189900.0（即 3118.99 万 %）
  失败场景 —— 60 日窗口内任意一根历史 K 线的 high/low 出现异常值（不需要是最新一根，
  唯一的守卫只查最新收盘价 daily.last.close<=0），dist_to_high/low60_pct 就会被
  这一根污染。真实世界更危险的是"偏离没那么离谱"的坏值（本项目自己记录过腾讯量纲
  ×100、东财延迟源垃圾值等真实先例）——那种情况下算出的百分比会是一个看起来完全
  合理的错误数字，直接印上卡面。
  为什么现有守卫没抓到 —— 唯一覆盖这两个字段的测试只断言符号方向，不检查量级，
  所有测试数据都是干净单调序列。market_calc.py 里"点位≤0 或涨跌幅>20%"那套围栏
  （有专门常量 PCT_ABS_LIMIT）的教训没有被搬到 technical_calc——同一类"单文件
  打补丁、没抽成共享校验"的问题在另一处的重演。
```

```
[F6] sector-calc 的"主力净流入前 5"在资金字段被吞成全零时，给出一个任意但格式完整的"第一名"
  证据（攻击面：静默 fail-open）—— 根因在解析层：_sources/eastmoney.py 的
  fetch_boards() 用 `main_inflow=float(r.get("f62") or 0.0)`、
  `advance=int(r.get("f104") or 0)` 把"字段缺失/为 None"与"数值真的是 0"合并成
  同一个结果——而同一份代码对 f3（pct）的处理是相反的：f3 为 None 或 "-" 时直接
  raise SourceError，绝不吞成 0。构造 20 个板块（pct 各不相同非零，能通过现有的
  nonzero_count 守卫），main_inflow 全部为 0.0，跑 sector_calc.build_verdict()：
    verdict: PASS completed / missing: [] / warnings: []
    main_inflow_top = [{'name': '板块0', 'inflow_yi': 0.0, ...}, ...]
  失败场景 —— 东财板块榜接口在某次响应里让 f62/f104/f105 缺席或为 null 而 f3 仍
  正常（比如集合竞价阶段指示价已变但资金流统计尚未开始——这与本项目自己记录的
  "同一时刻不同字段新鲜度可以相反"是同一类现象），"主力净流入前 5"就会带着虚构
  排名和看似正常的"0.0亿"上卡。
  为什么现有守卫没抓到 —— sector.board.pre_session 这条守卫的判据硬编码在 pct 上，
  从设计上没有考虑同一响应里其它字段可能独立失效。这正是项目自己已经修过一次的
  "排序榜全 0"问题形状，只是换了一个字段、换了一条没被同一次修复覆盖到的路径。
```

```
[F7] architecture.md 自己的失败模式清单里，两条护栏从未被建过——而它们本该守的风险是真实、当前存在的
  证据（攻击面：守卫是否会红 + 检查是否无事可查却报绿，两路独立发现并互相补强）——
  `git log --all --oneline -- '**/reachability.py'` 与 `'**/placebo.py'` 都是空输出
  （不是曾经有过又删了，是从来没有过一次提交）。但 architecture.md §2.4 的目录树图
  把 `placebo.py  reachability.py  isolation.py` 三个并排列在 `tools/verify/` 下
  （isolation.py 是真的），§9 表格里 L-4/L-7 用与真实机制相同的陈述句式，
  点名这两个文件为"新系统的防护机制"。
  **这不是纯粹的文档问题**：risk_check.py 的 THRESHOLDS 是一张真实的、当前在跑的
  规则表（6 条阈值），但只有 3 条在 tests/test_risk_check.py 里被断言真正触发过
  （risk.emotion.broken_rate_high / risk.market.volume_spike / risk.market.volume_dry）。
  另外 3 条（risk.market.breadth_weak / risk.emotion.streak_extreme /
  risk.emotion.limit_down_many）在测试里完全没出现过——字段名虽然确实由上游
  （sector/emotion/market 的 calc 脚本）产出，但没有任何东西每天确认它们真的还能
  触发，而理应承担这个职责的 reachability.py 根本不存在。
  失败场景 —— 未来任何一次重构（比如给 max_streak 换单位、给 advance_ratio 改字段名）
  如果不小心让某条阈值再也匹配不上，THRESHOLDS 表会"照常参与计算，只是永远不生效"
  （L-7 原文）——risk agent 从此对这类风险永久沉默，而没有任何日跑机制能发现，
  产出的 Card 会一直显得"风险已核查、无异常"。
  为什么现有守卫没抓到 —— 公开仓库审查（audit_public.sh）和文档规约测试
  （test_docs_convention.py）都不检查"设计文档提到的文件路径是否真实存在"；
  TODO.md 正确地把"安慰剂基准"列为 Phase 4（有披露，只是不在 architecture.md
  自己那张表里），但"可达性巡检"完全没有任何阶段归属说明，读者只能从 architecture.md
  本身得出"已经建成"的错误印象。
```

### 🟡 中危 —— 守卫失效，或验收证据不成立（12 条）

> **模式观察**：F8/F9/F10/F7/F19 是同一个形状的五个独立实例——**手工维护的名单
> （测试的 parametrize 列表、运行时配置的 allow 数组、架构文档里的组件清单、
> 同一不变式的重复实现）不会随 agent/组件数量增长而自动同步或收敛**，而且这个
> 形状里至少有两处（F10、F19）是"一个坑已经真实炸过、专门补了守卫，另一处结构
> 相同的地方至今没人管"。这比任何一条单独的发现都更值得记住：清单/实现类约定
> 第一次踩坑后，团队本能地去补**那一个**，而不去问"还有哪些地方是同样的结构"。

```
[F8] STANCE_VOCAB 只在 agent 名字精确命中字典 key 时生效，命不中就静默变成"1~16 字任意词都收"
  证据（攻击面：Agent 契约与实际行为）—— 直接构造 AgentVerdict：
    agent="market", stance="超级看多"（表外词）→ 正确抛 ValueError
    agent="Market"（大小写 typo）, stance="随便乱写的词" → 无异常，正常构造成功
    agent="discipline"（Phase 3 即将建的第 7 个 specialist）, stance="瞎编的方向" → 无异常
  根因：verdict.py 里 `vocab = STANCE_VOCAB.get(self.agent)`，命中才做词表校验，
  不命中就只查字符串长度 1~16。amend_verdict.py 里同一段逻辑又抄了一遍，共享同一个盲区。
  失败场景 —— Phase 3 建 discipline 时如果忘记在 STANCE_VOCAB 里加一行（纯手工步骤，
  没有任何清单强制），它的 stance 字段会从当天起完全不受约束，而 AGENTS.md 里
  "契约层会直接拒绝表外词"这句话，读者会以为对全系统成立。**目前 6 个已注册 agent
  的词表约束是真实生效的**（对照组已验证，见「跑了但没攻破」）——这是一颗定时炸弹，
  不是已经发生的事故。
  为什么现有守卫没抓到 —— 唯一的交叉校验测试用 `@pytest.mark.parametrize("agent",
  sorted(STANCE_VOCAB))`，天然只测"已经正确登记"的 agent，无法覆盖"忘记登记"分支。
```

```
[F9] news/AGENTS.md 缺少其余 5 份契约都有的"记下 verdict_ref"指令，真实生产 trace 两次证明了后果
  证据（攻击面：Agent 契约与实际行为）—— 6 份 specialist 契约里，"stderr 最后一行是
  verdict_ref=NN，记下这个数字"这句话逐字出现在 emotion/market/sector/technical/risk
  的 AGENTS.md 里，唯独 news/AGENTS.md 没有（该文件只在后面的示例/格式模板里提到
  "verdict_ref"这个词）。用 `agent_trace.py --agent news -n 2` 看真实历史（均发生在
  2026-09-21）：两次真实调用里 news 都把 task_id 字符串误当成 --ref 传给
  amend_verdict.py，报错后不得不去读 --help / 源码，重跑一次才对——每次多花
  2~3 次工具调用、约 20~30 秒。该文件 git 历史只有建 agent 那一次提交，此后从未修订过。
  失败场景 —— 每次 Supervisor 调 news 都要多等这几十秒——与契约自己反复强调的
  "不要为了确认参数去读源码"形成直接讽刺。
  为什么现有守卫没抓到 —— tests/test_verdict_refs.py 确实有一条专门设计防这类缺口
  的检查（断言 "verdict_ref" 出现在契约文本里），但它的 parametrize 列表自
  sector/technical/news/risk 建立以来从未更新，只覆盖 3/6 份契约；即使把 news
  加进去，子串匹配本身也抓不住这个真实缺口——"verdict_ref"这个词在 news/AGENTS.md
  里本来就出现了 3 次（都在示例段落），纯子串匹配会判定"通过"，因为它检查的是
  "提到了这个词"而不是"讲清楚了第一次去哪拿"。
```

```
[F10] 运行时配置 tools.agentToAgent.allow 缺 news，是姊妹配置已出过事故的同型坑
  证据（攻击面：Agent 契约与实际行为）—— 运行时配置里
  `agents.entries.main.subagents.allowAgents` 正确包含 news，但
  `tools.agentToAgent.allow` 缺了它。tests/test_roster_matches_config.py 的文件头
  记录着一次真实事故（2026-09-21 10:37）：news 建好了、注册了，端到端却只 spawn
  了四个，原因正是 allowAgents 漏了 news——为此项目专门写了守卫盯住 allowAgents。
  但全仓没有任何测试检查 tools.agentToAgent.allow（grep 不到一处引用）。
  更值得注意：architecture.md §3.2 用确定性口吻写"官方：只列一半=互相够不着"，
  但用 agent_trace.py 核对真实历史，news 该天被成功调用了至少 3 次——与文档描述的
  后果直接矛盾（见收尾三问第 2 条）。
  失败场景 —— 与 allowAgents 那次一模一样的失败形状（两处名册各写一遍，改一处
  忘另一处），只是这次发生在没人守的那一半，且这类 json 配置漂移天然不出现在
  git diff 里被 review 到（该文件不受版本控制）。
  为什么现有守卫没抓到 —— 清单类配置的"两处对齐"只被验证过一次就被当成已解决，
  没人推广到第二处。
```

```
[F11] test_contract_single_impl.py 的字典字面量扫描可被逐键赋值 / type() 动态建类绕过——但读取时有真实的第二道防线
  证据（攻击面：守卫是否会红 + 契约与数据层，两路独立发现并互相补充验证）——
  该测试只处理 ast.Dict 节点和 `dict(...)` 调用。用 `ev = {}; ev["field"] = ...`
  逐键拼出完整 Evidence 的全部 7 个字段，或用 `type("MarketVerdict", (), {...})`
  动态建类，两种写法都是极普通的 Python idiom（尤其字段要条件性追加时），
  `pytest tests/test_contract_single_impl.py` 全绿，无 offender。
  **关键的补充实验**（契约与数据层攻击面做的）：把一条违反契约铁律的 verdict_json
  （verdict=PASS 但 missing 非空、result 有字段但 evidence 为空）用绕过 db.py 的
  裸连接直接 INSERT 进 agent_verdicts——写入不报错；但用正规的 load_verdict()
  读回来时**立刻**抛出引用具体铁律编号的 ValueError（"result 字段无证据支撑…
  无据之言不入 Card（铁律 3）"）。也就是说 from_dict() 在每次反序列化时都重新跑
  一遍 __post_init__，构成运行时的第二道防线——静态扫描的盲区不等于坏数据会被
  正常读取路径无声接受，**前提是所有消费方都老实走 from_dict**（目前检查过的
  synthesize.py 和 card_ops.py 都是）。
  失败场景 —— 如果未来有新工具绕开 from_dict 直接读 verdict_json 列使用，
  这道补偿防线才会真正失效；在此之前，风险被限定在"提交前静态扫描不可靠"，
  不等于"运行时会无声吃下坏数据"。
  为什么综合评级是 🟡 而非 🔴 —— 静态扫描确实形同虚设，但唯一已知的下游消费路径
  仍然安全，这是两个攻击面交叉验证后达成的更准确判断，而不是任何一路单独的结论。
```

```
[F12] test_no_raw_sqlite.py 的两条 AST 检查均可被 importlib / __import__ 绕过，且这条没有补偿机制
  证据（攻击面：守卫是否会红 + 契约与数据层，两路独立发现）—— 第二条测试的文档
  字符串明确写着"独立于上一条——有人可能通过 importlib 或别名绕过 import 检查"，
  但它的判定逻辑仍只认 `X.connect(...)` 里 X 是字面量 `ast.Name` 且 `id=="sqlite3"`。
  用 `__import__("sqlite3").connect(path)` 或 `importlib.import_module("sql"+"ite3")`
  + getattr，两条检查全部通过；实测这不是文字游戏——是一条真实、完整可读写的
  sqlite3.Connection，能打开磁盘上任意 sqlite 文件。
  （对照：`import sqlite3 as s3; s3.connect(...)` 起别名**不会**绕过，第一条测试
  比较的是 ast.Import.names[i].name 原始模块名，不受 asname 影响，会精确报出该行。）
  失败场景 —— 只要不写字面量 `import sqlite3`，代码在切 PostgreSQL 时会被完全漏掉——
  这与 F11 不同，这里没有"读取时二次校验"这道后备：一条绕开 db.py 的裸连接可以
  被拿去做任何读写，不经过任何契约层，除非有人恰好用等价的手法专门去写这样的代码
  （不是一个自然会不小心写出的模式，与 F11 的逐键赋值不同）。
  为什么现有守卫没抓到 —— 这条测试虽然专门针对"字符串驱动的间接访问"立了规则，
  查的仍是"访问方式的语法形状"而非"运行时对象从哪来"，对通过值而非语法暴露目标的
  写法没有覆盖。它真正的意义（"以后切 PostgreSQL 只改一个文件"）会被这类连接
  静默破坏，属于结构性/可维护性风险，不是当下的数据完整性风险。
```

```
[F13] docs/tutorial 的"不写既存系统细节"检查是 8 词字面量黑名单，同义改写即可通过
  证据（攻击面：守卫是否会红）—— test_docs_convention.py 用固定词条
  ("邻居","既存","实盘","量化交易","交易系统","下单通道","长期运行的系统","生产环境")
  做判据。在教程文件末尾插入一句完整复刻黑名单注释里明确禁止内容的话（"另一套系统"
  代替"邻居"、"现有生产系统"代替"生产环境"），`pytest -k "教程不写"` 18 个文件全绿，
  含被污染的那个。已用 git checkout -- 撤销。
  失败场景 —— 任何教程作者用 CLAUDE.md 自己反复使用的词汇（"另一套系统"就是
  CLAUDE.md 通篇的说法）而不是恰好用到黑名单里那 8 个词，泄露内容会静默通过审查
  直接被 push——仓库已 Public，pre-push hook 调的也是同一份"文本关键词"哲学，
  没有第二道语义层面的检查。
  为什么现有守卫没抓到 —— 目标是开放式自然语言，没有"开关名"这种稳定锚点可以切换到
  （这与本项目已经修好的 missing_ledger.py 那次不同，见 F9 的判据切换）。
```

```
[F14] bin/biga-card --check 的"一致性"只验证组装代码的确定性，不验证判断本身能否复现
  证据（攻击面：契约与数据层）—— 读 replay.py 源码：`--check` 模式下
  status/headline/synthesis 100% 照抄原卡（args 均为 None）。用 synthesize.py
  造一张卡，故意把 headline 写成与证据完全无关的"外星人今日登陆陆家嘴，沪指熔断"，
  跑 --check：
    ✅ 一致：BIGA-20260921-777 用冻结证据重跑，结论逐字段相同。
  --check 完全不介意 headline 是否符合证据——两侧本质是"用同一个纯函数、
  喂入被证明相等的输入，跑两次"，数学上必然相等。
  **但它并非测了个寂寞**：把 `extra_missing = [m for m in original.missing if m
  not in from_verdicts]` 临时改成 `extra_missing = []`（复现一个真实历史 bug 类型），
  --check 正确报了不一致（missing 字段：在线 [...] / 回放 []）。
  失败场景 —— 如果有人把 --check 通过的输出当作"这次决策的结论用同一份证据能被
  独立复算出来"的验收证据，是过度解读：它验证的是"落库→取回→再拼一次"这条管线
  无损、且 synthesize() 没有隐藏的非确定性，范围比命令名暗示的小得多。
  为什么现有守卫没抓到 —— 不是"没抓到"，是这条命令的能力边界被文档/命令名
  （"一致性检查"）暗示得比实际验证范围更大。
```

```
[F15] Evidence.staleness_sec 不核对真实当前时刻，系统性错位的时间戳会显得"很新鲜"
  证据（攻击面：契约与数据层）—— Evidence.__post_init__ 唯一的时间校验是"两者都带
  tzinfo"和"as_of <= retrieved_at"，从不与 now_cn() 比较。构造一条 as_of/retrieved_at
  都比真实当前时刻晚了 3 天、但彼此只差 60 秒的 Evidence：
    staleness_sec: 60（1.0 分钟——看起来非常新鲜）
    但这条证据的 as_of 实际上比真实当前时刻早了 3.0 天。
  risk-check 把这个数字原样报到 Card 上（注释写"年龄照报，解读交给 Agent"——
  这个设计本身没错），但这也意味着它对"as_of 到底对不对"完全没有纠错能力。
  失败场景 —— 只要 F4 那类错位是"系统性地同时影响 as_of 和 retrieved_at"
  （比如两者由同一段错误的日期推断逻辑派生），staleness_sec 不会有任何异常表现，
  会把上游的日期错误完整伪装成"数据是新鲜的"。
  为什么现有守卫没抓到 —— 现有测试只验证"给定两个时间戳，算出来的秒数对不对"，
  输入本身是测试手写的已知正确值，从未测试"两个时间戳一起被系统性算错"这种场景。
```

```
[F16] as_of / 跨源一致性检查是手工维护的字面量白名单，新字段/新源不会被自动纳入
  证据（攻击面：静默 fail-open）—— test_as_of_attribution.py 的 _LIVE_FIELDS 是
  按文件路径+字面量字段名枚举的显式字典（9 个字段）；_contract/verdict.py 的
  CROSS_CHECK_PAIRS 只有一条硬编码元组。两者都是运行时白名单，不是"凡是无日期
  端点必须如何"这类可自动推导的结构性检查。
  失败场景 —— 未来任何新 skill（或现有 skill 的新字段）只要产出一个无日期端点的
  事实，只要开发者没有主动想起改这份白名单，就不会有任何测试提醒——这正是 F4
  本身想解决又在同一次提交里于 raw 层重新犯错的根源。
  为什么现有守卫没抓到 —— 这条本身就是"守卫是什么形状"的结构性问题。
```

```
[F17] TestReservationIsAtomic 名不副实（测的是顺序调用），但底层机制本身被两路独立压测证明是安全的
  证据（攻击面：守卫是否会红 + 契约与数据层，两路独立压测）—— 该测试类文档字符串
  宣称"占号靠主键冲突仲裁，不靠「先查再插」"，但测试体对 reserve_decision_id 的
  调用全部是同进程同线程内的顺序调用，没有 threading/multiprocessing，没有任何
  真正的竞争窗口——与本项目自己已经抓到过的"并发测试探针改错地方"是同一种形状。
  **两路评审各自用 multiprocessing 补了这个测试没做的事**：一路起 80 个真实 OS
  进程（Barrier 同步启动，两轮），一路起 30 个真实 OS 进程——结果一致：编号
  全部唯一，无异常。说明"先算候选、INSERT 失败就吃 IntegrityError 重算"配合
  SQLite 写事务的天然串行化，是正确的乐观并发实现，真正的仲裁点是 INSERT 语句
  本身的主键冲突。
  为什么标为 🟡 而不是"无问题"——测试名字和文档字符串描述的是并发属性，通过 CI
  的方式却完全没有并发；如果未来有人把连接重试逻辑改坏（比如误吞了
  OperationalError 而不只是 IntegrityError），这条测试不会发现。
```

```
[F18] isolation.py 的 Result 只有布尔态，没有"存不成证据"这一档——是 F2 的根子之一
  证据（攻击面：检查是否无事可查却报绿）—— check_i2 的代码注释原文："⚠️ 没给
  --before，这一项不构成证据"，但紧接着仍然执行 `res.add(True, ...)`，实测输出：
    ✅ I-2 · 邻居状态库 mtime（仅记录）
         ⚠️ 没给 --before，这一项不构成证据 —— 邻居自己也在写它。
  该行与 I-1/R-2/端口一起被汇总进最终的"══ 隔离自检全绿 ══"。
  为什么现有守卫没抓到 —— `Result.add(self, ok: bool, ...)` 的类型就是纯布尔，代码里
  没有第三态可用来表达"我知道这不算数"。对比它要取代的 phase1_acceptance.py 里的
  `Check` 类：三态 PASS/FAIL/PENDING，`check_6_neighbour` 在没给 baseline 时正确返回
  `c.pending(...)`，不会计入 PASS。isolation.py 在这一点上是相对于旧脚本的倒退——
  而"UNKNOWN ≠ PASS"正是 CLAUDE.md 里排第一的红线（R-3）。作者显然知道这个道理
  （注释写得很清楚），但数据结构没跟上认知，于是"知道"没能变成"报出来"。
```

```
[F19] 同一个不变式 I-1，仓库里有两份独立实现，同一时刻给出的进程数相差 3 倍以上，旧的那份还在 README 里被推荐
  证据（攻击面：检查是否无事可查却报绿）—— 同一分钟内，用两份代码各自的判据现场重放：
    isolation.py 口径（cwd 或 cmdline 含 BIGA 路径）: 14 个进程 / 412 个 fd
    phase1_acceptance.py::biga_fds_into_neighbour() 口径（仅 cmdline 含"openclaw-biga"）: 4 个进程
  两者都报 0 命中（✅ 通过），但检查对象的集合完全不同——isolation.py 判据更宽，
  会把开发者的 VSCode/bash 会话也算进去；phase1_acceptance.py 判据更窄，只认
  cmdline 字面量。如果 I-1 真的被违反，但违反它的进程恰好只满足其中一种判据，
  两份实现会给出相反的结论，而使用者根本不知道该信哪一份。
  失败场景 —— 见上。
  为什么现有守卫没抓到 —— 这正是项目自己在 architecture.md §9 L-3 里点名的失败模式
  ("同一判据多份实现……错法全是静默的")，但它发生在项目最看重的 I-1 自己的验证代码
  里。更关键的是 phase1_acceptance.py 不是废弃脚本——README.md 仍在建议运行它，
  CLAUDE.md 的不变式表却只点名 isolation.py 作为 I-1 的验证方式，两份都在，没人删旧的。
```

### 🟢 低危 —— 可维护性、一致性（4 条）

```
[F20] AST/文档扫描器用裸文件系统遍历，嵌套 worktree 会污染扫描结果（本次评审自己踩中过一次）
  证据 —— test_contract_single_impl.py / test_no_raw_sqlite.py / test_docs_convention.py
  的文件枚举全部是 `REPO.rglob(...)`，不检查 git 追踪状态，.gitignore 也没排除
  `.claude/`。本次评审环境本身就是"仓库根下嵌套多个并行 worktree"——某一路评审
  误在主仓库路径下跑单元测试时，扫描器真的把另外几路并行评审 worktree 里的契约
  模块也扫了进来，报出一堆假阳性（该现象是操作失误时意外撞见，随后已在正确范围内
  重新验证过其余结论，不受此问题影响）。
  失败场景 —— 在"多个并行 worktree 嵌套在仓库目录下"这种工作方式中（本项目自己
  这次评审就是这么做的），任意一个 worktree 里跑全量 pytest，结果会依赖于当时
  磁盘上恰好还存在哪些兄弟 worktree。
  为什么现有守卫没抓到 —— "扫描范围非空"的自检只验证"扫到了 ≥3 个文件、扫到了
  _contract 自己"，没有验证"只扫到了应该扫的文件"。
```

```
[F21] EXCLUDE_DIRS 是静态目录名单，新建同名目录会被静默排除出 AST 扫描范围（目前休眠，未触发）
  证据 —— test_no_raw_sqlite.py / test_contract_single_impl.py 硬编码
  EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "runtime", ".pytest_cache", "data"}。
  当前仓库里没有真实的 runtime/ 目录，agents/ 和 data/ 下也没有 .py 文件，
  所以目前不是活跃漏洞（当前会被扫描的 .py 文件是 51 个）。
  失败场景 —— 如果将来有人新建一个恰好叫 runtime/ 或 data/ 的目录并放真实业务代码
  （不是完全不可能——skills/_store/runtime.py 这个文件名已经很接近了），这个目录
  会被两条 AST 守卫静默跳过，不会有任何测试变红提示"覆盖率下降了"。
  为什么现有守卫没抓到 —— 这是防御机制的设计方式本身（白名单式排除），依赖人记得
  "排除名单需要跟仓库结构同步"。
```

```
[F22] 测试数量的文档漂移：CLAUDE.md 写 417、评审提示词写 445，实测 447
  证据（本次评审的编排过程自己发现，不属于任何一个攻击面）—— 在开始五路并行评审
  之前跑的基线：`python3 -m pytest -q` → `447 passed in 2.21s`。
  CLAUDE.md「当前状态」表写"测试 417 条"；docs/guide/review-prompt.md 写
  "445 条测试"。两份文档相差 2 条、与实测相差 30 条。
  失败场景 —— 无（纯文档数字漂移，不影响任何运行行为）。
  为什么现有守卫没抓到 —— 这正是 architecture.md §9 L-6"文档漂移"点名的失败模式
  本身——"关键事实由测试生成或校验，不手抄"——这两处都是手抄的数字，而且是
  在"会话启动先读"清单第一位的文件里手抄的。
```

```
[F23] latency_report.py：data/biga.db 文件完全不存在时是裸 Python 异常，不是文明的提示
  证据（攻击面：检查是否无事可查却报绿）—— 一次全新的 worktree 里没有真实
  data/biga.db（.gitignore'd），直接跑 `latency_report.py --parallel-check` 得到
  `sqlite3.OperationalError: unable to open database file` 的完整 traceback。
  这不属于"静默通过"（失败很响、退出码非零，没人会误读成成功），只是体验糙——
  db 文件缺失（区别于"schema 建好但零行"）没有被当成一种要专门提示的情况。
  为什么现有守卫没抓到 —— 未覆盖这个特定边界；不影响结论正确性，仅影响排错体验。
```

---

## 二、跑了但没攻破的地方（哪些防护是真的）

这部分同样是评审的产出，不是陪衬——它告诉我们哪些防护经得住真实攻击：

- **decision_ids 的原子占号算法本身**（不涉及 F1 那种"删行"复合场景）：两路独立压测（80 进程 ×2 轮 / 30 进程 ×1 轮）均无重复、无异常。问题精确定位在"表没有 append-only 保护"，不在分配算法。
- **其余四张只追加表的触发器**（decision_records / agent_runs / raw_market_snapshot / agent_verdicts）：UPDATE 和 DELETE 均被正确拦下，报错清晰、指向具体原因，是显式异常+事务回滚，不是静默失败。
- **from_dict() 的运行时二次校验**：即使坏数据绕过静态扫描被裸写入库，正规读取路径会在读取瞬间报错并点名具体铁律编号，是真实存在的第二道防线（前提：消费方老实走 from_dict——目前检查过的路径都是）。
- **pre-push hook 的两条历史高危路径，用真实 git 操作实测均已修复**：①全新分支首次推送（无任何 remote-tracking ref）——用独立 fake-origin bare 仓库、真实 clone/push，即使 `--remotes` 一个引用都不指向，也正确扫描了 local 可达的全部历史，抓到注入的假密钥；②本地与远端完全无共同祖先的 orphan 强推——同样用真实 push 测试，正确识别"远端 sha 不是本地 sha 的祖先"并退化为全历史扫描，抓到注入的 IP 地址。这条历史上确实出过"空区间→假绿"的事故，但当前实现对两种最危险的触发路径都做了正确兜底。
- **市场/情绪计算里的分母为零场景**（breadth、volume_ratio、broken_rate、seal_never_broken_rate）：逐一读代码 + 构造真实触发条件，全部有显式 `if denom: ... else missing/None` 守卫，没能构造出绕过路径。
- **`_sources/` 下除两个端点外都有服务端时间戳**：sina/tencent/eastmoney.fetch_pool/sina_news 都逐条带真实日期；唯一"无日期"的两个端点（breadth、boards）在 Evidence 层已被 af91a0d 的 add_live() 覆盖到（问题出在更深的 raw 落盘层，即 F4）。
- **STANCE_VOCAB 对 6 个已注册 agent 是真实生效的**：`agent="market", stance="超级看多"` 被正确拒绝——F8 的漏洞精确限定在"未注册 agent"这个分支，不是词表机制整体失效。
- **S-1/S-2 两条硬约束**：在检查过的 6 份 specialist 契约里没有找到"agent 悄悄做算术"或"skill 悄悄做分类判断"的反例。
- **Card 层的核心否决机制**：missing 非空却给 BUY、给了否决却不是 AVOID/BLOCK、缺失项没有完整上浮——三种情况都在 `__post_init__` 里直接 raise，是硬性约束，不是 prompt 层面的君子协定。
- **risk / emotion / market / technical 的真实调用序列**：用 agent_trace.py 核对最近几次真实历史，工具调用顺序与次数均与契约描述的最小工作流一致。
- **别名 import 不能绕过 sqlite 检查**：`import sqlite3 as s3` 会被第一条 AST 检查精确抓到，只有 importlib/`__import__` 这类通过值而非语法暴露目标的写法才能绕过（即 F12）。
- **missing_ledger.py 与 test_docs_convention.py 的零输入/越界边界**：分别用一次性空库（零 verdict 记录）和真实构造的越界目录/空文件跑过，输出文案与退出码都清晰可辨，不含糊、不静默跳过。
- **latency_report.py 的 0/1-span 分支**（代码审查 + 相邻路径实测，非完全 live 实测）：代码明确处理 `len(spans) < 2` 为"判不了"并以非零退出码返回，相邻的"零决策记录"路径已用一次性空库验证为 fail-closed；唯独没有构造出"恰好 1 个 Stage1 agent"这个具体历史记录去触发内部分支（工作量与收益的权衡取舍），如实标注为未 100% 实证，而非声称已验证。

---

## 三、收尾三问

**1. 如果只能修一个，修哪个？**

看权衡角度给两个答案，而不是回避取舍：

- **按风险敞口排序**：F2（isolation.py 对 I-1/端口的验证在目标不存在时无差别判真）。I-1 是 CLAUDE.md 排在最前面的不变式，保护的是"BigA 绝不能碰同机另一套独立生产系统"这条底线，而 CLAUDE.md 明确要求"改动环境后跑一次"把它当作第一道关卡。这道关卡此刻不能证明自己声称要证明的事——它能在真实网关不存在、真实端口无人监听的情况下稳定报"全绿"。这条不修，后面所有其它发现的"环境是安全的"这个前提本身就没有被真正验证过。
- **按修复成本/确定性排序**：F1（decision_ids 缺失 append-only 触发器）。修复是照抄其余四张表已经验证过的一行代码模式，风险极低，而它已经不是假设——是这个项目自己命名的真实生产事故（L-11）的直接复现路径，复现不需要绕过任何检查，只需要一次看起来完全合理的运维操作。

如果只能选一个今天就做：F1，因为投入产出比最确定。如果问的是"哪个此刻风险最大"：F2。

**2. 哪一条"已被证明"的结论，证据其实不成立？**

最尖锐的一条：usage.md 第 4 个"确认它没骗你"的检查——"没有影响同机的其他服务"——展示的示例输出是"✅ I-1 · BigA 无写模式打开已有实例的文件（16 个进程 / 413 个 fd）"，CLAUDE.md 当前状态表也写"隔离 `tools/verify/isolation.py` 四项全绿"。但 F2/F18/F19 合起来证明：这"16 个进程"里大多数只是恰好 cwd 在仓库里的开发者会话，与真实网关是否存在、是否安全无关；即使真实网关完全不存在，这项检查也会稳定报"✅ 通过"。**这份文档自己用来证明"我们没有自欺"的示范检查，其中至少两项本身就是会自欺的**——这比任何一条数据层的 bug 都更值得警惕，因为它是整个项目"不要相信文档，相信你跑出来的结果"这条评审原则的直接映射：连"跑出来的结果"本身，在这一项上也不构成证据。

同一形状的另外两处：CLAUDE.md 的"schema v4，五张表，只追加由触发器强制"（F1：五取四）；architecture.md §3.2 的"官方：只列一半=互相够不着"（F10：news 缺了一半配置，真实历史却显示它被成功调用了至少 3 次）。三条断言的共同点：都以"已验证事实"的语气写下，而实际上都没有被这个项目自己的真实运行数据检验过。

**3. 有没有哪个地方，攻了但没攻破？**

见上方「二、跑了但没攻破的地方」整节——尤其是：decision_ids 占号算法本身的并发正确性（两路独立压测收敛到同一个结论）；from_dict() 的读取时二次校验（证明"静态扫描能被绕过"不等于"坏数据会被无声接受"）；pre-push hook 两条历史高危路径的真实 git 操作复测（证明"空区间假绿"这个已知历史 bug 的两个最危险变体现在都被正确兜住了）。这三点合起来说明：本项目在**算法、运行时校验、以及安全类 git hook 的边界处理**上的工程质量是扎实的，真正的系统性缺口集中在**验证覆盖面会不会随系统增长同步扩展**（F1/F8/F9/F10/F11/F12/F19 全部是这个形状的变体），以及**自检工具本身有没有被同样严格地验证过**（F2/F18/F19）。

另有两处**尝试攻击但受限于评审边界、无法坐实**，如实记录而非猜测结论：
- "不许用通用子 agent 工具扮演某个 specialist"这条约束，到底是 OpenClaw 运行时在工具可见性层面的结构性限制，还是纯靠 agent 自觉——需要检查一个真实会话的工具集，而评审规则明确禁止为此开真实付费会话。
- `tools.agentToAgent.allow` 缺 news 为什么至今没有引发可观测故障——只验证到"历史上被成功调用过"这一结果，其确切生效机制在 OpenClaw 闭源运行时内部，仓库里没有可进一步验证的渠道。

---

## 四、附：评审定稿之后的一次并发进展（如实记录，不做全量核对）

本报告定稿过程中（基线 commit `f7f7d1e` 之后），同一个 `phase2` 分支上出现了两个新提交（`d83ff2c`、`5be69f6`，相隔 13 分钟），来自另一个并行会话，修复的是**另一份独立的外部评审**（参见同目录下的 `easyup-biga-phase2-code-review.zip`）提出的问题。两份评审显然互不知情，却在多处指向同一根因——独立方法收敛到同一批问题，这本身是一个比任何单份报告都更有说服力的信号。

时间紧迫，选择只核实以下两点，而不追平到最新 tip 去逐条核对全部 23 条发现（原因见下）：

- **F20/F21（worktree 污染 AST 扫描）：已被 `d83ff2c` 修复，并已实测确认。** 该提交抽出 `tests/_scan.py`，统一改用 `git ls-files -co --exclude-standard` 取代三处手工维护的 `EXCLUDE_DIRS`——与本报告 F21 指出的问题诊断完全一致。在本次评审遗留的 3 个 worktree 仍然存在于磁盘的情况下（正是 F20 描述的污染条件），重跑 `pytest tests/test_contract_single_impl.py tests/test_no_raw_sqlite.py` → **12 passed，无污染**。这是一次干净、正确的修复。
- **F1（decision_ids 缺失 append-only 触发器）：未被这两个提交解决，依然成立。** `d83ff2c` 的 FIX-01 与 `5be69f6` 的 FIX-02 都在处理"决策身份"这同一大类问题，但走的是不同机制——前者在 `save_card` 加了"卡片不能装着不属于自己的判定"的校验，后者在 risk 聚合阶段加了"上游判定是否属于同一次决策"的校验。这两道新防线都建立在**任务/决策 id 字段本身可信**的前提上；F1 指出的洞恰恰在这个前提之下——号本身可以被重新分配（决策表没有 append-only 保护），一旦重新分配，两次运行各自产出的判定在 id 字段上都是**真实自洽**的，不会被"这条判定是不是装错了地方"这类校验揪出来。已重新核对：`skills/_store/schema.py` 在 `5be69f6` 仍未包含 decision_ids 的 `_append_only` 调用。**F1 依然是一个独立、尚未被覆盖的洞**，即使它所属的"决策身份"这个问题类别已经被那份并发的评审大幅加固。

另外值得记录：`5be69f6` 的 FIX-06（"agent_runs 不是 spawn 证明"）与本报告 F3 的问题描述高度相似，但据提交信息，它修的是"教程第 4/7 章两套口径矛盾"这个文档层面的表现，未提及是否把 `check_1_spawned` 式的运行时交叉核验自动化、扩展到 Phase 2 新增的五个 specialist——如果只修了文档矛盾，F3 指出的机制性缺口（没有任何自动化区分"真 spawn"和"手工跑脚本"）应该仍然成立，但这一点本报告**没有去精确核验**，如实标注为未确认，而不是假装查过。

其余发现未做逐条核对——两个并发提交之间只隔 13 分钟，且第二个提交本身还带出了一条**双方评审都没找到**的新 bug（HTTP 截断响应会绕过重试层的异常捕获，影响全部六个 skill）。以这个速度追下去，任何一次核对都会在写完前过期。这份报告已经明确钉在 `f7f7d1e` 这一个时间点——保持这个边界，比试图追平一个仍在变化的目标更诚实。

**建议**：两份评审显然在重叠区域独立做了功，值得由人通读另一份评审的问题清单（`easyup-biga-phase2-code-review.zip`），与本报告去重、合并后续待办，而不是两边各自为政地继续修。

---

## 五、与另一份并发评审的系统对比

已完整读取 `easyup-biga-phase2-code-review.zip` 里的评审全文（`docs/review/2026-09-21-phase2-code-review.md`，P1×3 + P2×3，六条 FIX-01…06 均已在 `d83ff2c`/`5be69f6` 落地）。逐条对照后的结论：**没有发现任何一处两份评审给出相反结论**——两者对"哪些设计是对的、不该动"的判断完全一致（Stage 0 先占号、verdict 原件不经 LLM 搬运、`stance`/`verdict` 分离、raw 层不许改写这几条，两份评审都明确认可）。差异体现在**覆盖面**和**一处严重度判断**上。

### 5.1 对方找到、本报告没找到的（已核实：六条已全部修复）

| 编号 | 问题 | 与本报告的关系 |
|---|---|---|
| P1-3 | news 的 Evidence `as_of` 用交易日收盘时间（`as_of_for_trade_date()`）代替最新快讯的真实时间戳——盘中恰好数值对得上，收盘后会把"2 分钟前的新闻"算成"陈旧 5 小时"。 | 本报告静默 fail-open 攻击面查了 market/sector/technical 的 as_of，唯独没有专门验证 news_scan.py 这条——是一处真实的覆盖盲区，不是判断分歧。已修（`5be69f6` FIX-03）。 |
| P2-1 | news 的单源 Evidence 没有携带 `raw_hash`，即使对应的 raw snapshot 已经落盘。 | 与本报告引用的"已知问题 #4：`Evidence.raw_hash` 暂无消费方"同属一个不成熟领域，但那是**消费方**视角，这条是**生产方**视角的具体缺口——本报告没有专门核实生产方是否补全。已修（`5be69f6` FIX-04）。 |
| P2-2 | `new_decision.py` 在全新数据库（文件不存在）上无法启动——底层 `reserve_decision_id()` 用只读连接读 sequence，报 `sqlite3.OperationalError`。 | 与本报告 F23（`latency_report.py` 在库文件缺失时抛裸异常）是同一个"全新/缺失数据库该怎么对待"的形状，但发生在不同脚本、不同触发路径——本报告没有专门测过 `new_decision.py` 这条冷启动路径。已修（`5be69f6` FIX-05）。 |

三条都已确认修复落地，不再是待办；列在这里是为了如实说明本报告的覆盖盲区，而不是暗示还需要谁去修。

### 5.2 本报告找到、对方评审没找到的

对方评审的范围声明（第 1 节）明确限定在"Contract 不变量 / Decision & Evidence Identity / 五个 Specialist / Store 持久化 / Replay / OpenClaw runtime 证明"——**不包含"攻击自己的守卫是否真的会生效"这一整个维度**，而这正是本报告五个攻击面里两个半（守卫是否会红、检查是否无事可查却报绿、部分契约与数据层）的全部内容。所以以下几乎是必然的覆盖差，而不是对方疏漏：

- **F1**（`decision_ids` 缺 append-only 触发器）——对方的 P1-1/P1-2 都在"decision_id 本身可信"这个前提之上加固，从未检验这个前提是否成立。三者合起来看：P1-1/P1-2 关闭了"卡片/风控**装错**判定"这条路，F1 关闭的应该是"号本身**被重新分配**"这条路——目前只有前两道关上了。
- **F2**（`isolation.py` 对 I-1/端口的验证在目标不存在时无差别判真）——对方评审范围完全不含环境隔离检查。
- 本报告的 🟡/🟢 部分整体（F8~F19、F21~F23：STANCE_VOCAB 未登记 agent 零约束、`agentToAgent.allow` 配置漂移、两个 AST 扫描器可被绕过、`--check` 实际验证范围小于命令名暗示、`staleness_sec` 无法核对真实时刻、`TestReservationIsAtomic` 假并发、`placebo.py`/`reachability.py` 从未建过等）——都属于"守卫本身是否可信"这个维度，对方评审没有涉猎。

### 5.3 唯一的严重度判断分歧：`agent_runs` 是否构成 spawn 证明（本报告 F3 vs 对方 P2-3）

两份评审独立发现了同一件事，但给出的定级和修法深度不同：

| | 本报告 F3 | 对方 P2-3 |
|---|---|---|
| 定级 | 🔴 高 | P2，"Merge blocker: NO" |
| 问题定位 | 缺一套**自动化**、覆盖全部 specialist 的运行时交叉核验 | 主要是文档措辞容易让人误读 `agent_runs` 的证明力 |
| 建议修法 | 把 `check_1_spawned` 式核验自动化并扩展到 5 个新 specialist | 改 docstring/README/architecture/tutorial 措辞 + Live E2E 加一项人工核对 |

已核对实际落地的 FIX-06（`5be69f6`）：只做了文档语言修正（且比对方建议更谨慎——没有回改已冻结的教程第 4 章，只追加了指针），**没有新增任何自动化测试把 `subagent_runs` 交叉核验扩展到 market/sector/news/technical/risk**。也就是说，即使 FIX-06 完全落地，本报告 F3 指出的结构性缺口——"Phase 2 现在产出的每张卡，除 emotion 外，没有任何机器能证明 specialist 真的被调用过"——依然成立。这是本次系统对比中唯一一处本报告会明确表态"更认同自己的判断"的地方：文档措辞不清楚是症状，缺自动化校验才是会真正咬人的根子。

### 5.4 状态

本节写于两份评审的已知修复（`d83ff2c`、`5be69f6`）之后。**联合确认已完成，见第七节**——全部 23 条已逐条复查，其中 15 条完整修复、7 条修复了具体实例但仍有可定位的残留缺口、1 条被明确承认无法根治。

---

## 七、复查结果：23 条逐条确认（commit `4fd015e`）

对全部 23 条发现做了一次系统复查——原则与初次评审一致：**不读 diff 就下结论，重新跑原始的攻击/构造手法**，能做差分测试（还原修复前代码、用同一输入重放，确认原始漏洞确实复现）的都做了差分测试。复查过程本身也用了并行的独立 worktree，交叉核对。

结论速览：

| 结果分类 | 数量 | 编号 |
|---|---|---|
| **完整修复**（含差分测试确认、部分附带红灯验证） | 15 | F1, F4, F6, F9, F11, F12, F14, F15, F17, F18, F19, F20, F21, F22, F23 |
| **修了具体实例，模式/根因仍有残留缺口** | 7 | F2, F3, F5, F7, F8, F10, F16 |
| **明确承认无法根治，已如实标注边界** | 1 | F13 |

### 7.1 完整修复的 15 条（简述，详见各条原文）

- **F1**：`decision_ids` 补 append-only 触发器，且判据从"手抄四张表清单"改成扫 `sqlite_master` 取全部表的差集——结构性修复。红灯验证：临时移除迁移，两条测试均正确失败并精确指向 `decision_ids`。原始"占号→删除→重号"事故链路现在第一步（删除）就被拒绝。
- **F4**：raw 层 as_of 归属已按每个源的真实性质分别处理（无日期端点用取回时刻、有自带时间戳的源用自己的时间戳、日线用收盘时刻），差分测试确认修复前代码完整复现原漏洞（4 个不同 source 共用同一个错误 as_of）。
- **F6**：`_num()` 不再用 `or 0.0` 吞掉 `None`，字段缺席与真实为零被正确区分，两种场景都变成显式 `missing` 而非虚构排名。
- **F9**：`test_verdict_refs.py` 改用 `glob("agents/*/AGENTS.md")` 自动发现契约文件——用新建一个假 agent 目录（不含所需指令）实测确认自动被纳入检查并报错，是真正的结构性修复。
- **F11**：选择"划清边界+加强 `from_dict()` 一侧"而非追平扫描器，路子正确；副作用中真的抓到并修了一处此前未被本报告发现的渗漏（`missing_ledger.py` 曾直接 `json.loads` 而不经 `_store`）。
- **F12**：新增"业务代码不许出现任何间接 import"的通用禁令（不只挡 sqlite），实测两种原始绕过手法均被拦下，并顺带修了一处无关但真实存在的间接 import。
- **F14**：不改变 `--check` 的验证范围（那是合理的，不是 bug），而是让工具在每次成功时主动打印"验的是组装管线，不是判断本身"这条边界说明，文档同步。
- **F15**：`Evidence` 新增独立于 as_of/retrieved_at 差值的"未来时钟锚"，堵住"两个时间戳一起被系统性算错"这类共模误差；边界测试（119s 放行/121s 拒绝）与文档声明的 120s 容差精确吻合。
- **F17**：`TestReservationIsAtomic` 换成真实 16 进程 + `Barrier` 制造真实竞争窗口；复查用"故意换成有竞态漏洞的朴素实现"验证了这条测试真的具备探测力（16 进程里 14 个报 UNIQUE 冲突）。
- **F18/F19**：`isolation.py` 的 `Result` 改三态（PASS/FAIL/UNKNOWN），`UNKNOWN` 不计入通过、退出码非零；`phase1_acceptance.py` 的独立实现已删除，改为直接调用 `isolation.check_i1`，同一时刻两个入口输出逐字节相同。
- **F20/F21**（本次复查前已确认，此次顺带复核仍然成立）：三个 AST 扫描器统一改用 `git ls-files -co --exclude-standard`，遗留 worktree 存在时重跑相关测试无污染。
- **F22**：`tools/verify/sync_test_count.sh` 从守卫自身的失败信息里提取真实数字同步四处文档，而非另外手抄一份"正确答案"；当前 566 条与 CLAUDE.md 记载一致。
- **F23**：根因修在 `_store/db.py::connect()` 唯一入口，全新数据库场景下改为清晰的 `StoreNotInitialised` 提示而非裸 traceback，任何调用方自动受益。

### 7.2 修了实例、仍有残留缺口的 7 条

```
[F2] 残留：换了一种更窄的"文本匹配充当身份证据"
  两个原始复现场景（排除真实网关 pid / 排除真实端口后仍判真）已确认修复，
  现在正确返回 UNKNOWN 且退出码非零。但新判据"是否存在真运行时"仍然是
  cmdline 子串匹配（含 `~/.openclaw-biga/runtime` 路径字符串即算数）。复查
  实测：起一个完全无关的进程，只要命令行里偶然带有这个路径字符串（哪怕只是
  把路径当参数传给某个无关脚本——这在日常操作里并不罕见），排除两个真实
  网关 pid 后检查仍会判 ✅ 通过，理由是"找到了真运行时"，而那其实是一个
  不相关的进程。触发门槛比原来窄了很多，但"用文本匹配代替进程身份"这个
  根子没有变。
```

```
[F3] 残留：运行时校验没有绑定具体决策，正常使用节奏下的伪造几乎必然得逞
  这条的机制升级是真的——覆盖面从"只查 emotion"扩到全部 6 个 Stage1/2
  specialist，接进了 `bin/biga-card` 的真实出卡流程，还有"守卫的守卫"
  （校验 SQL 里真的查了 `subagent_runs`、校验出卡流程真的调了这个检查）。
  但复查用**未伪造任何底层数据、走合法 API**的方式暴露了一个新问题：
  `_runtime_spawn_records()` 只按"最近 50 条运行时记录"取样，**不按
  decision_id 过滤**——只要某个 agent 名字出现在最近 50 条里的任意一条，
  就算这次决策"两份记录都齐"。复查用一个从未真实存在过的决策号，配合
  合法 API 写入的 6 行记录重放，因为同一台机器当天确实跑过其它真实
  e2e（几分钟前），伪造的决策号"蹭"上了那些属于**别的决策**的真实
  spawn 记录，检查判定 ✅ 通过。由于 `bin/biga-card` 在正常使用下本来
  就会反复运行，这个"能蹭上"的条件在真实机器上几乎总是成立，不是刁钻的
  边角案例。值得注意：运行时记录的 `payload_json` 里其实明文写着决策号，
  只是当前实现没有用它做真正的决策级绑定——修复的数据基础已经具备，
  只是没有把关联逻辑接上。另外，`db.py::record_agent_run()` 的
  docstring 里，这次修复要推翻的旧论断（"这张表是唯一凭证"）原样残留
  在新警告下方几行，没有被这次提交清理。**建议不要把 F3 标记为完全解决。**
```

```
[F5] 残留：围栏压缩了问题的量级，没有消除"看起来合理的错误数字"
  天文数字级的攻击（3118.99 万%）已被真实拦住——差分测试确认围栏确实
  接在调用路径上（禁用围栏立即精确复现原始数字）。但复查构造了一个
  刚好卡在阈值内侧（约 4.2 倍中位价）的坏值，完整走完整个流程后
  verdict 仍是 PASS/completed、无 warning，dist_to_low60_pct=323.94%——
  不再是一眼假的数字，但依然是编造出来的错误百分比，会原样印上卡面。
  另有一个目前休眠、值得记录的设计风险：围栏阈值是按"整个窗口只有一个
  标的（指数）"校准的，如果这个共享模块将来被用到个股粒度（个股连续
  跌停时 60 日跌幅可轻松超过阈值），会在没有任何测试提醒的情况下
  误杀真实存在过的合法行情。
```

```
[F7] 残留：只修了"文档失真"那一半，风险本体（阈值缺测试）原样未动
  Part A（architecture.md 把不存在的文件当已建成描述）已被结构性修复：
  新测试自动扫描设计文档里点名的文件路径是否真实存在，红灯验证有效，
  且实测覆盖范围比原报告更广（又扫出 4 处此前没发现的幽灵引用）。
  但 Part B——`risk_check.py` 的 6 条阈值里有 3 条
  （`breadth_weak`/`streak_extreme`/`limit_down_many`）从未被任何测试
  验证过真的会触发——**复查确认零新增测试覆盖**，与本报告最初发现时
  完全一样。项目的 TODO 台账把整条 F7 标成单一的"✅ 已修"复选框，
  这个标记只对应 Part A，容易让人误以为風险已经解除。
```

```
[F8] 残留：行为修复是真的，结构性回归测试没有写
  未登记的 agent（拼写错误、未来新增的 agent）现在会被直接拒绝构造，
  而不是原来的"静默放行任意 1~16 字"——这是真实的运行时行为改变，
  用构造实验确认过。但报告 6.2 建议的"断言 STANCE_VOCAB.keys() 与权威
  agent 名单一致"这条结构性测试没有写，下一次忘记登记只会在**第一次
  真实调用**时才响亮崩溃，CI 阶段还是发现不了。
```

```
[F10] 残留：两个具体字段对上了账，但机制仍是逐字段硬编码比较
  `tools.agentToAgent.allow` 现在包含 news，且新增测试断言它与
  `subagents.allowAgents` 两个集合相等——用构造 fixture 确认这条新
  测试的检测逻辑真实有效。但这仍是"这两个字段之间"的一次性比较，
  不是"配置里所有列 agent 名单的字段互相相等"的通用机制。如果将来
  出现第三个同形状的字段，需要有人再手写一条同型测试，不会被现有
  测试自动覆盖。
```

```
[F16] 残留：source 层已结构化，但两处原始点名的白名单原样未动
  「哪些数据源没有服务端时间戳」现在由自动扫描全部 `_sources` 子模块
  强制声明（不能不声明），这正是 F4 那次事故本该被挡住的地方，现在
  真的挡住了。但报告原文点名的两处白名单——`test_as_of_attribution.py`
  的 `_LIVE_FIELDS`、`_contract/verdict.py` 的 `CROSS_CHECK_PAIRS`——
  复查确认字面量原样存在，未变成自动派生。复查用实际探针验证了
  "失败场景"完全成立：新增一个字段、故意用错误的 `add()`（而非
  `add_live()`）且不登记进白名单，完整测试套件（除一个无关的字段数
  断言巧合失败外）全绿，没有一条测试发现这个字段的 as_of 来源判断错了。
```

### 7.3 明确承认无法根治的 1 条

```
[F13] docs/tutorial 的黑名单式检查——根因是"防不住开放式自然语言"，团队自己也这么说
  修复补齐了评审当时用过的具体同义词，并新增了一条独立的兜底检查
  （抓同机另一实例的真实路径/端口字面量，这条是真管用的结构性检查，
  用真实端口号 18789 实测确认会被抓到）。但复查用一个全新的、不在
  黑名单里的同义句子（"本机同时运行着一套历史悠久、从未间断过的旧
  程序，其内部模块与账户细节我们完全不碰"）重放，完整测试套件没有
  任何一条标红。这与团队自己在代码里留下的说明一致——他们承认这条
  没有彻底解法，黑名单只能拦住"想到过的说法"。这是本次复查里**唯一
  一条团队自己就没有声称"已解决"、而是诚实标注"仍是残留风险"**的发现，
  这份诚实本身值得记录。
```

### 7.4 复查方法论说明

五路复查全部在独立 git worktree 里进行，且**全部**在开工时发现自己的 worktree 落后于 phase2 真实尖端（`4fd015e`）——这次的落后原因与上一轮不同：不是 worktree 落在旧提交，而是当时 `origin/phase2` 本身还没追上本地 `phase2` 分支（修复提交当时只推到了本地）。五路都正确识别出"要对齐的是本地分支尖端而非 origin"并自行纠正，过程记录在案，供后续参考——这本身也是"评审工具在多 worktree 场景下需要更清晰的对齐指引"这一经验的又一次印证（呼应 F20/F21）。

---

## 六、修复方向建议

本报告 23 条发现里，🟡 类的大多数不是孤立疏漏，而是同一个形状的重复出现（见「模式观察」）。与其逐条单独修，不如先解决这个形状本身——一次结构性改动能同时堵住好几条发现，而且能防住**还没出现**的第 N+1 个实例。

### 6.1 已经证明可行的样板：改手工清单为结构性派生

`tests/_scan.py` 的重构（`d83ff2c`，修复本报告 F20/F21）已经是一个完整、且当天就被验证生效的案例：两个 AST 扫描器原来各自手抄一份 `EXCLUDE_DIRS`，改成统一调用 `git ls-files -co --exclude-standard`——不再由人维护"哪些目录要排除"，而是直接问 git"这个仓库认哪些文件属于自己"。这不是一个孤立的修复技巧，是一条可以推广的原则：

> **凡是"两处（或更多）地方必须列出同一批东西"的约定，找一个唯一权威源，让其余各处从它派生或与它做结构性比对——而不是分别手抄。**

### 6.2 同一原则可以直接套用的几处

| 发现 | 现状（手工清单，会漏） | 建议改法（从权威源派生 / 结构性比对） |
|---|---|---|
| F8 | `STANCE_VOCAB` 没登记的 agent（拼写错、未来新增的 agent）→ 静默放行任意 1~16 字 | 没登记 → 直接拒绝构造，不再退化成弱校验；另加一条测试断言 `STANCE_VOCAB.keys()` 与"当前注册的 agent 名单"这个唯一权威源完全一致 |
| F10 | `tools.agentToAgent.allow` 单独手抄一份 agent 名单，与 `subagents.allowAgents` 各写各的 | 两者从同一份 `ROSTER` 派生，或写一条通用测试断言配置里所有列 agent 名单的字段彼此相等（而不是只测其中一对，像 `test_roster_matches_config.py` 目前只护住了 `subagents.allowAgents`） |
| F9 | `test_verdict_refs.py` 手工列举 3/6 份 `AGENTS.md` 做 parametrize | 改成 `glob("agents/*/AGENTS.md")` 自动发现，新建 specialist 的契约自动被纳入覆盖，不依赖有人记得去改列表 |
| F1 | `decision_ids` 建表时漏抄了 `_append_only(...)`——靠"逐张表判断要不要保护" | 遍历 schema 里**全部**表，默认要求 append-only，例外需显式列入白名单并附理由——把举证责任从"记得加保护"翻转成"要主动申请豁免" |
| F7 / F15 | `architecture.md` 把从未建过的 `placebo.py`/`reachability.py` 与真实存在的 `isolation.py` 并列展示 | 加一条测试：文档里 `tools/verify/` 目录树提到的每个文件名，要么真实存在于磁盘，要么在同一处标注阶段归属（"计划于 Phase N"） |

### 6.3 第二条原则：布尔值会掩盖"根本没验证"这一档

`isolation.py`（F2/F18）与 `TestReservationIsAtomic`（F17）的共同根子是：把"真的验证过且通过"和"根本没找到东西可验证"压缩成了同一个布尔值 `True`。这个项目自己更早的脚本 `phase1_acceptance.py` 已经用三态 `Check`（PASS/FAIL/PENDING）正确处理过同一个问题（`check_6_neighbour` 在缺基线时正确返回 `PENDING`，不计入 PASS）——`isolation.py` 是后写的、却退化回了布尔值。这说明问题不是"不会做"，而是**新代码没有沿用已经在仓库里验证过的旧模式**。建议：

- 任何安全/身份相关的自检，一旦存在"样本量不足以下结论"这种可能性（0 个目标进程、0 个可比较区间、没给 `--before` 基线……），必须有第三态可以表达，且第三态不计入"全绿"汇总。
- `isolation.py` 与 `phase1_acceptance.py::biga_fds_into_neighbour`（F19）是同一个不变式的两份实现，建议二选一保留、删除另一份，而不是让 README 和 CLAUDE.md 各推荐一份。

### 6.4 建议优先级（按"改动小、杜绝力强"排序）

1. **F1**：`decision_ids` 补一行 `_append_only(...)`——成本最低，直接堵住已经复现过的真实历史事故。
2. **F8**：`STANCE_VOCAB` 未登记 agent 改为拒绝而非放行——几行改动，且对已注册的 6 个 agent 是纯收紧、不改变现有行为。
3. **F2**：`isolation.py` 加第三态、收紧进程识别口径（例如改用网关自己写的 pidfile / 端口存活探测，而不是模糊的 cwd/cmdline 子串匹配）。
4. 其余清单类发现（F9、F10、F7/F15）：逐个替换为 6.2 的派生/结构性比对模式。
5. F11/F12（AST 扫描可绕过）：现实的修法不是把扫描器写得更聪明（军备竞赛打不完），而是明确这类静态扫描只是"拦住无意写出的常见写法"，真正的正确性保证应该继续依赖已经证明有效的读取时校验（`from_dict()`）——对裸 sqlite 连接这种没有补偿机制的路径，更依赖代码评审而非自动化。
