# 从零搭一套 Multi-Agent 决策系统 —— BigA 2.0 开发教程

> 📄 **过程** · 每章写完即冻结，只追加「⏩ 后续变动」指针
> **覆盖**：施工过程 —— 踩了什么坑、怎么发现的 ｜ **不覆盖**：当前设计是什么（会随时间失真，见 [`../design/`](../design/architecture.md)）


> 这不是一份「读完就会」的框架文档，而是一份**真实的建造日志**。
> 每一章对应项目里实际完成的一段工作：做完才写，写的是真跑过的命令和真踩过的坑。
>
> 配套代码就是本仓库。每章结尾的验证命令，你在自己机器上应该能跑出同样的结果。

---

## 这套系统是什么

**EasyUp for BigA 2.0** —— 基于 [OpenClaw](https://docs.openclaw.ai) 的 Multi-Agent
A 股短线**决策辅助**系统。

- **不自动下单。** 产出是一张证据可追溯、可回放的 Decision Card，最终决策由人做。
- **八个 Agent**：1 个 Supervisor + 7 个 Specialist（市场 / 板块 / 新闻 / 技术 / 情绪 / 风险 / 纪律）。
- **三条地基**：`UNKNOWN` ≠ `PASS`、数据与智能分离、事实可追溯。

完整设计见 [`docs/design/architecture.md`](../design/architecture.md)。

---

## 这份教程的特别之处：它是在「雷区」里施工

大多数教程假设你从一台干净的机器开始。本项目不是 ——
**同一台机器上已经跑着另一个 OpenClaw 实例**，它有自己的状态目录、
自己的端口、几十个定时任务。新系统必须在它旁边长出来，且**不能碰它一根手指**。

这个约束贯穿全部章节，也是这份教程最值得看的部分：

> 多数「把新系统装崩老系统」的事故，不是因为有人做了危险操作，
> 而是因为**默认行为在共享资源上悄悄生效**了。

所以教程里大量篇幅在讲「为什么这条看起来无害的命令有毒」。

---

## 章节

| # | 章节 | 主题 | 状态 |
|---|---|---|---|
| 01 | [隔离安装](01-isolated-install.md) | 在已有 OpenClaw 实例旁边装第二套，两个必踩的陷阱 | ✅ |
| 02 | [Profile 初始化](02-profile-setup.md) | `setup --baseline` + `config patch`，为什么不走 onboarding 向导 | ✅ |
| 03 | [契约层](03-contract-layer.md) | `Evidence` / `AgentVerdict` / `DecisionCard`，用 AST 扫描钉死「只有一份实现」 | ✅ |
| 04 | [数据层](04-store-layer.md) | SQLite (WAL) 单一入口、只追加触发器、回放不覆盖 | ✅ |
| 05 | [第一个技能](05-first-skill.md) | 真采 A 股情绪数据；与会静默骗人的接口打交道 | ✅ |
| 06 | [建 Agent](06-agents.md) | 脚手架默认值多半不是你要的；角色契约写在 `AGENTS.md` | ✅ |
| 07 | [端到端](07-end-to-end.md) | 三层认证迷宫；怎么**证明** Specialist 真的被调用过 | ✅ |
| 08 | [回放](08-replay.md) | 冻结证据；在线与回放共用同一份组装代码 | ✅ |
| 09 | [隔离演练](09-isolation-drill.md) | `kill -9` 自己，逐项核对已有实例毫发无伤 | ✅ |
| 10 | [延迟与成本](10-latency-and-cost.md) | 216s→75s；延迟其实是正确性 bug 的症状；一个被证伪的验收指标 | ✅ |
| 11 | [第二个 Specialist](11-second-specialist.md) | 边界划在哪；怎么**证明**它们真的并行；并行解决扇出、解决不了汇聚 | ✅ |
| 12 | [可追溯性的三个空白](12-traceability-gaps.md) | 读评审的三个筐；stance / raw_hash / 机器可读缺失；新规矩对旧数据只要求可读 | ✅ |
| 13 | [制衡层](13-checks-and-balances.md) | 否决权该放哪个字段；risk 在结构上不许采数据；在正确行为上报红的检查比漏报更糟 | ✅ |
| 14 | [复制一个 Specialist](14-copying-a-specialist.md) | 「没有新机制」本身是判据；不同端点对「新一天」的表现相反；守卫比的是字段名，同义重复溜了过去 | ✅ |
| 15 | [第一次盘中运行](15-first-intraday-run.md) | 两次运行的证据合成进同一张卡；身份必须先于证据；并发度调大反而更慢 | ✅ |
| 16 | [第一个必须读懂的 Agent](16-the-agent-that-must-read.md) | skill 算不出结论时回放怎么定义；读了 16% 却报 PASS；白名单的失败是静默的 | ✅ |
| 17 | [收官：能做什么、怎么用、下一步](17-closing-phase-2.md) | 功能清单与四个「确认它没骗你」的检查；八个收尾的坑；Phase 3 展望 | ✅ |
| 18 | [请外人来拆](18-external-review.md) | 对抗性评审提示词怎么写；23 条发现的形状；**修复过程翻了 5 次车，5 次都是探针抓的**；被复查推翻的那一条 | ✅ |
| 19 | [不会红的守卫](19-guards-that-cannot-fail.md) | 第二轮深度评审六条发现**全是同一个形状**；判据该落在哪里的对照表；「不许联网」原来只是一句注释；探针没红也是结论 | ✅ |
| 20 | [写边界重校验](20-write-boundary-revalidation.md) | 确定性编排批 A-I：写的时候不校验、读的时候才炸；`from_dict()` 默认值坑；严格 JSON 为什么拆成两个函数 | ✅ |
| 21 | [值对象与不变量](21-value-objects-and-invariants.md) | 确定性编排批 A-II：`MissingItem` 身份改基于 code；冻结对象为什么要两步；重复 vs 缺席该用不同的严格度；schema 版本号被提前占用 | ✅ |
| 22 | [运行身份与状态机](22-run-identity-and-state-machine.md) | 确定性编排批 B：一个 id 扛五件事的代价；状态为什么事件溯源而不是一个 `state` 列；CAS 靠唯一约束不靠「先查再写」；13 个状态凭什么是 13 个；每个状态都要能指出谁写谁读 | ✅ |
| 23 | [Python 驱动 spawn：运行时适配层](23-runtime-adapter.md) | 确定性编排批 C-I：不经 LLM 轮次 spawn Specialist；状态归一化为什么值一层；grant 生命周期与 groupId 硬约束；先抓真实响应形状再写解析（别测自己的假货）；cancel 的意外（active 顺序≠spawn 顺序） | ✅ |
| 24 | [把编排变成程序：生产入口切换](24-deterministic-orchestrator.md) | 确定性编排批 C-II：「谁能启动」从「拦住」变成「够不到」；判官为什么是叶子 agent；一个被总闸掩盖的 `set -u` bug；**付费点挪了地方而守卫盯着老地方——我在这一批亲手踩了它、真花了钱** | ✅ |
| 25 | [冻结一次、多处读：SnapshotCoordinator 的地基](25-snapshot-coordinator.md) | 确定性编排批 D-I：把抓取与读取拆开；为不写第二套解析而拆 `fetch`/`parse`（L-3）；manifest 要能反查不是好看；只建地基不改 Specialist（显式登记的施工空档）；分发提示词里一个过期的数字被实测抓出（120 不是 25） | ✅ |
| 26 | [让 Specialist 改口读冻结快照](26-specialists-read-frozen.md) | 确定性编排批 D-II：切三个 skill 的实际行为；`--evidence-set-id` 为何可选、坏号为何 fail-closed；`raw_hash` 取冻结集那份不对切片重算；恒真检查**改判据**而不是删；两个 fail-closed 点互相兜底、探针从没预期处报红 | ✅ |
| 27 | [把事实和判断拆开](27-facts-and-assessment.md) | 确定性编排批 E-I：`amend_verdict.py` 的存在就是那条断层的证据；三个新类型一条边界一个；跨型铁律归谁校验；事实层铁律共用一份防 L-3；`load_verdict` 多态让消费方零改动；读宽写严让旧格式自然清零；只迁 emotion 一个试点；顺带还 D-II 的 `evidence_set_id` 账 | ✅ |
| 28 | [Orchestrator 健壮性四处收尾](28-orchestrator-robustness.md) | 确定性编排批 C-III：外部复审四条独立小修复；本项目**第一次两批并行**（为什么开 worktree 而不是动对方的未提交草稿）；只追加表里的假「已落库」记录删不掉；`cancel()` 三批以来第一个真调用方；探针要因**对的原因**红 | ✅ |
| 29 | [把其余四个 Specialist 迁到 FactBundle](29-migrate-four-to-factbundle.md) | 确定性编排批 E-II：一次「机械迁移」真正难的那一小块 —— ①「Agent 补的限制归哪」要**查真实数据库**不照抄例子；判断「缺口 vs 判断边界」看数据在不在；`market.trend.no_history` 实测有整段序列 ⇒ 范围外（同 sector 持续性）；修 Agent 模板反成核心交付物（旧 `--add-missing … --verdict` 是覆盖 skill 完整度的洞，不改就是 F9 抖动） | ✅ |
| 30 | [迁 risk 并退役旧修订路径](30-migrate-risk-retire-amend.md) | 确定性编排批 E-III（收官）：六个里最危险的偏偏形状最普通 —— risk 的 stance 是 `VETO_STANCE`，迁错=「真该拦的决策放行了」；核心是 **VETO 穿透**（否决从 AgentAssessment 穿到 DecisionCard 真拦 BUY），而这条链「不用改代码」正是最该测的；「退役 amend」≠ 删 `save_verdict`；`无法判定` 可挂 WARNING ⇒ risk 不需要 `--verdict` 事后降级 | ✅ |
| 31 | [收敛 `run_id` 三同名](31-run-id-namespace.md) | 确定性编排批 J-II：一个名字被三个东西共用、且不报错（「一个 id 扛五件事」的反面）；三处必须一起改；「改名免费」要用 grep 证明；`RENAME COLUMN` 会**改坏触发器体也不报错** ⇒ 真跑 SQL；结构化 join 照抄 `CROSS_CHECK_PAIRS` 不发明第二种；「命名空间是否一致」这种猜错也不红的前提必须 live 验一次 | ✅ |
| 32 | [让 `run_id` 贯穿全链（capture）](32-run-id-capture.md) | 确定性编排批 J-I：capture 与 enforce 拆开做（区别在**攒这一列的边际成本**，不在有没有人用）；判断行的 run_id **从事实行继承**不让 Agent 传（判据别建在可篡改输入上），「不可能不一致」用结构＋行为两条证据证明；加 nullable 字段两个 `from_dict` 都用 `.get` 否则历史卡 `KeyError`；属于「这次执行」的字段必须进 `comparable()` 剥离清单；跨文件模块污染只在全量红（`_load` 要幂等）；可派生守卫替新列自动把关 | ✅ |
| 33 | [把 risk 的事实挪进编排器](33-risk-facts-into-orchestrator.md) | 确定性编排批 F：纯函数包在 LLM 会话里跑，省钱切口是「拎进程序直接调」不是让 LLM 跑快；只有确定性终局（`fb.status=='failed'`）值得跳过 spawn，判据别用会误伤的 `verdict==UNKNOWN`；fact 行 `amends` 恒 NULL **逃出 v6 线性索引** ⇒ 双写静默并存（v11 分区唯一索引补）；`IntegrityError` 报**列名**不报索引名（判据落错=L-13，探针抓到）；早退不 spawn 但 risk 进卡 ⇒ 别记账本行（L-8 + spawn_check 误判 forged）；不留兜底自跑；全员缺席不再「零证据 FAILED」改出满缺失的卡；VETO 全路径回归一环没断 | ✅ |
| 34 | [外发通知 outbox](34-outbound-notifications.md) | 确定性编排批 G-I（只推不收）：投递状态用**追加日志**不给 outbox 开 `delivered_at` 原地改（同 `run_events`/`amends` 先例）；**card 通知与卡同事务、run_failed 尽力而为**（原子性看「谁离了谁更糟」）；`NOTIFICATION_PENDING` 只代表入队、`COMPLETED` 不等投递；run_failed 两处触发共用一份实现、没塞进 `transition()`；`ON CONFLICT DO NOTHING` 冲突时 `lastrowid` 不可靠 ⇒ 看 `rowcount`；加状态靠既有「每个状态都有消费方」守卫兜底；迁移版本号是共享命名空间、多批并行会静默撞（本批实测撞上批 F，占了 v12） | ✅ |
| 35 | [Agent Registry：roster 从五处收成一处](35-agent-registry.md) | 确定性编排批 K：名册散**五处**（含一处零测试覆盖的 `adapter_spike.STAGE1`）收成一份 `AGENT_REGISTRY`，派生照抄 `RUN_STATES` 的 `vars()` 内省形状；字段只装有消费方的（不装被推迟的 `required_datasets`＝L-1）；`RISK_AGENT` 是会**炸**的纯函数（Stage 2 spawn 非恰好一个就 import 时 `RuntimeError`）；卡级**冻结**期望 roster 堵「同一张历史卡不同时间给不同答案」，回放**原样透传**不重算 ⇒ `--check` 不误报；`discipline` 在册但不 spawn ⇒ 永不进 `absent_agents` 权威（裁定 13）；`skipif` 缺口按 R-3 收窄（不需配置的半边照跑、需配置的半边发**可见**警告再 skip）；探针自己会看错大小写、会打到别的守卫上；并行撞树第二次 ⇒ worktree 隔离 | ✅ |
| 36 | [让 raw 层真的存 raw](36-raw-artifact.md) | 确定性编排批 I：`content_sha256` 此前是我们 `sort_keys` 重排后的指纹、证明不了源字节；改存储列语义前先普查读取方（coordinator 把 `payload` 当解析后对象用 `len`/切片）⇒ **新增 `raw_text` 列而非替换 `payload_json`**；损失点在 `get_json` 内部故加姊妹 `get_json_and_text` 把原文带到落库点；「原始」要落到具体字节（腾讯存整段 body、多页源存各页 body 的 JSON 数组）；建表注释改成分列陈述真话；旧行不回填、旧口径 `payload_sha256` 保留、别跨 v13 比 sha；拆函数调用前查它有没有搭便车的副作用（NaN 守卫原搭在 `payload_sha256` 上）；改网络边界函数名后测试桩会静默落空；与批 K 各自独立选中「第 35 章」撞车，按落地先后顺序处理，K 先落地保住 35，本批改记 36 | ✅ |
| 37 | [飞书出卡变成结构性 trigger](37-inbound-trigger.md) | 确定性编排批 G-II（Inbound Trigger）：要钉的不变式是「出卡编排绝不在 agent 会话进程树里跑」（比「main 全程不参与」更本质）；🔴 走死了一条路——零 LLM 的 `command-dispatch: tool` 够不到**会话内才连接**的 MCP 工具（只有 live 暴露）；一个飞书机器人 + `bindings` 按 peer 路由 ⇒ 私聊没法按内容分流 ⇒ 退回 BigA 全仓一致的「技能 + shell 跑脚本」（main 认出请求 → 跑 `inbound.py`）；幂等键绑 `decision_ids` 不绑 `decision_runs`（误伤重试）；异步用 `systemd-run` 把出卡拉出 agent 进程树 ⇒ entry_guard 判 HUMAN（pivot 后这是**唯一**的 L-14 止血点，P2 改成「launcher 必须脱树」）；人工 CLI 与飞书走同一个 `bin/biga-card`；`tools.deny:[ask_user]` 只给非交互流水线 agent、main 不在名单、名单从 `_contract` 派生；P6 live 又暴露又修好两处（预算闸门自拒、飞书投递缺凭据改走 `bin/biga message send`）；schema/章节号在 21 会话并行下连撞 v13→v14、35→36→37 | ✅ |
| 38 | [交易日历：第一张真实的 `fact_*` 表](38-trading-calendar.md) | 确定性编排批 L：**选源是探活不是查资料**——timor（办公日历≠交易所日历、调休周末交易所不开市，新浪日线实测核实）、东财（脏数据、哨兵行）都「看着能用一打露馅」，新浪日历要 JS 引擎解密；最干净的深交所官方源**本机连不通**却仍选它（换脏源=拿正确性换本机绿灯），解析层离线测/落库链桩测/真实抓取留给能连通环境；「连不通」≠「零消费方」；回退方向绝不倒（`is_trading_day` 查不到=`None`=可能开市，绝不当休市）；`session_in_progress` **不改**（问的不是同一件事，加节假日感知会让 emotion 把节假日 0 当真冰点）；改无测试的函数先补特征测试锁现状；第一张 `fact_*` 表**不接 SnapshotCoordinator**（日历是低频只读查表）；文件按源名 `szse.py` 躲标准库撞名；加新组件=向一批没写的守卫报到 | ✅ |
| 39 | [三个基础设施包搬进 `src/easyup_biga/`](39-package-restructure.md) | 确定性编排批 H-I：只搬设计文档 §8 讨论过的三个包，`_runtime`/`_snapshot` 与 `application`/`integrations`/`cli` 留白（H-II，没设计依据的目录不预建）；旧路径留**薄壳**让 199 处既有导入一字不改；子模块壳用 `sys.modules[__name__]=真实模块` 做身份等同，不用 `import *`；🔴 `__file__` 自定位代码不是「纯移动」安全的——深一层，`DEFAULT_DB_PATH` 悄悄指向不存在的 `src/data/biga.db`，全套测试因为都绕开默认路径而没抓到，只有真实命令用默认配置跑一次才撞见；AST 守卫的路径常量光改提示词点名的两处不够，重新跑一遍 grep 才找出另外两处同形状字面量（L-13）；「改回旧常量会静默漏过」被实测推翻——真实类定义已搬走，旧路径下守卫反而对所有真实域文件报红，比预想更容易发现 | ✅ |
| 40 | [飞书可靠性 + 生命周期收敛（外部评审 C/D 部分）](40-review-c-d-reliability.md) | 核实一份外部评审后修复五点：Trigger 重试只在「从未启动到」或「已是失败终态」两种精确场景放行，不撞 Fact 唯一约束；`notification_deliveries` 加 `abandoned` 状态区分「还会重试」与「已放弃」；`OrchestratorTimeout` 子类必须排在父类 except 之前，否则分支永远进不去；stale-run reaper 严格只做纯函数、不接调度（项目自己划过这条线）；🔴 CAS 的 `expected` 必须用扫描时刻的旧状态，重新读取「当前状态」会让 TOCTOU 保护形同虚设（自己的 sabotage 测试才抓到这个设计缺陷）；flock 是排他资源，不能像纯判断那样简单下沉，子进程重新加锁会与父进程互斥，需要 `BIGA_CARD_LOCK_HELD` 环境变量显式传递「锁已持有」 | ✅ |
| 41 | [二轮对抗性复核：真机 PoC 攻破的两处](41-adversarial-review-round-2.md) | 对批 M 提交本身做对抗性复核（不是核实评审文档，是拿真代码跑 sabotage/PoC 攻击刚写完的修复）：🔴 C-1 的安全论证范围小于代码判断范围——Stage 1 完成后一样能合法转移到失败终态，relaunch 因此可能用几小时前的旧证据合成一张时间戳写着「现在」的卡，比原症状更隐蔽；`abandoned` 不计入退出码的理由是反的——它恰恰是永远不会再被看见的那种，重新关闭了上一次真实事故能被发现的信号来源；两层安全闸门必须认同一个开关，FORCE 只在外层生效等于承诺了一条走不通的恢复路径；测试名字宣称验证 TOCTOU，sabotage 证明它验证的是状态机「终态无出边」这条不相关的性质；给 stale-run reaper 补上 systemd timer——没有调度方的写数据模块，声称要修的那一半问题从未真正被修过 | ✅ |
| 42 | [Run Provenance（外部评审 B 部分，前 9 项）](42-run-provenance.md) | schema v16 把「哪次执行、哪份切片、哪些原件」变成可查询的列；🔴 B 不是可以缓的加固——评审 §6.3 提前写出了批 M 的 C-1 会造出的那个 bug，真实 PoC 复现：Stage 1 之后失败再重放，五个 Specialist 全撞唯一约束落不下 fact，而编排器不会停，用几小时前的旧 ref 合成一张 `generated_at` 是现在的卡，spawn 核验照样过；**安全论证别建在状态名上**（状态名是推断，「这个号名下有没有 fact」是事实）；没照抄评审的 `NOT NULL`——先读生产库（43 张卡只有 6 张有 run_id），可空列 + 写边界必填，读宽写严；分区唯一索引不加 `WHERE` 也不报错、只是语义错了，要有反向探针；判据取**库里那一行**不取卡上那份拷贝（只核被验证方自己出具的数据等于没核）；加约束与拆守卫必须同批做，分开做的失败是静默的；🔴 **sabotage 前先提交**——`git checkout --` 还原到 HEAD，把未提交的工作一起删了，5 个文件重打一遍 | ✅ |
| 43 | [把一个坑填了两次的地方，填第三次：动态同名模块 monkeypatch 错位](43-module-identity-idempotent-load.md) | 外部评审 A 节：批 J-II（教程 32 章）只修了 `test_run_id_capture.py` 一处的"无条件覆写 sys.modules"坑，全仓还有 11 处一模一样的复制体从未被推广修复；真机复现 `pytest tests/test_orchestrator.py tests/test_facts_split_e3.py` 会红、反序不会；🔴 只有 `card_ops`/`risk_check` 是 `orchestrator.py` 直接 import、真有生产碰撞证据的，其余五个按评审点名的**模式**统一修，不因为"目前没坑到人"而留着；修法是把手写的加载逻辑补回标准 `import` 本该有的幂等语义（已加载就复用，不重新覆写）；⚠️ **同一天第四轮复核推翻了本章"新回归测试绕开顺序本身"这条要点**——运行时身份断言实测依赖收集顺序，真正不依赖顺序的是后补的源码级 AST 扫描，且改过的文件里还漏了一处（详见 `docs/tutorial/README.md` 追加说明与 CHANGELOG）；🔴 **sabotage 也要用对触发顺序**——顺序选反了，全绿证明不了任何事 | ✅ |
| 44 | [Fact 的身份换成「哪次执行」（外部评审 B 部分收官）](44-run-scoped-facts.md) | schema v17 把 `ux_fact_per_task_agent` 拆成 `(run_id,agent)` + `(task_id,agent) WHERE run_id IS NULL` 两条分区索引；🔴 **唯一约束表达的是「什么是同一个东西」**——批 M 的 C-1 造出第二个 run 之后，「一个决策一份 fact」这句话就不再等于「一次执行一份」，而约束不会因此报错、只是开始拦错东西；旧那条**必须 DROP**（留着就把新约束架空，整批等于没做）；**SQLite 多行 NULL 不算重复** ⇒ 只建 `(run_id,agent)` 会让历史行静默失去保护，必须两条分区、且要有反向探针；`latest_verdict_ids` 退役换 `load_verdict_ids_for_run`，退役判据用 **AST 不用文本匹配**（文本匹配会把「说明它被谁取代」判成违例，唯一过法是删说明）；🔴 **加约束与拆守卫必须同批做**——C-1 守卫判据从「有 fact 就拒」收窄成「已出过卡就拒」，分开做的失败是静默的（旧守卫的测试照样绿，只有产品行为退回去）；测试夹具共用一个 run_id 会在第二个决策号上撞唯一约束，红的是夹具不是被测代码 | ✅ |
| 45 | [证据得支持那个值，缺席得对得上号（外部评审 E 节 · 一）](45-evidence-must-support.md) | 契约只查「result 的键有没有同名 Evidence」、不查两边的**值** ⇒ `result=100` 配 `Evidence(value=1)` 能落库上卡，每一道既有检查都通过（R-3 的形状）；判等取 canonical JSON **与回放同口径**，否则会出现「契约说相等、回放说不等」；🔴 第一版把「相等」和「合法」搞混——`allow_nan=False` 让两边本来相同的 NaN 被判成对不上，抢在写边界那道真检查前面报了个不相干的错，**一个检查只回答一个问题**；🔴 **更正我自己上一轮报错的结论**：「五个 agent 缺席只显示成一条」端到端复现证明不成立（synthesize 按「代码+文本」复合键去重），我把 `MissingItem` 判等只看 code 这个**局部属性**当成了端到端数据丢失——局部观察只能支撑局部结论；真正的洞是 roster 只比条数，5 个缺席配 5 条毫不相干的 missing 照样放行；解法不是去破 L-13（猜文本），是把 agent 名字挪进代码让对应关系变成解析；连带改掉约 25 处「凑数占位 missing」——那些夹具一直在演示那个洞 | ✅ |
| 46 | [测试和生产检查项漂移：干净机器上的假红（外部评审 A 节 · 二）](46-isolation-registry-drift.md) | 评审给的四个短语，先核实再动手：A4（ZIP/Git 双模式）发现在更早一轮评审（`d83ff2c`/`d20bed1`/`72cce1b`）里已经修过，`tests/test_scan_fallback.py` 真的把仓库剥掉 `.git` 跑真实子进程验证；A3（stdout/stderr 契约）、A5（subprocess cleanup）排查后没找到可复现的缺陷，不强行改；A2（Isolation Registry 漂移）是真的——`main()` 调 5 个检查，测试的 monkeypatch 名单手抄漏了 `check_namespaces`，这台机器因为已装好 3 个服务、`check_namespaces` 恰好返回 ok 而掩盖了漏洞，🔴 真实复现：把 `SYSTEMD_USER` 指到不存在的路径模拟"干净机器"，`assert codes["ok"] == 0` 假红成 `assert 2 == 0`；修法不是加一致性检查，是把两份名单消灭成一份（`main()` 与测试共用 `isolation.checks()`），`check_i2` 多一个 `before` 参数用 `functools.partial` 统一形状 | ✅ |
| 47 | [冻结得冻到底，时间得说清基准，出处得问来源要](47-frozen-provenance-and-time.md) | 外部评审 E 节（二）：`frozen=True`+`MappingProxyType` **只盖第一层**，嵌套 list/dict 仍是外部那个对象，而铁律在 `__post_init__` 校验、穿透在那之后 ⇒ 落库的不是被校验的那份（实测 14.7% 的证据值是可变结构，且与 `result` 顶层值**常是同一对象** ⇒ 一次 mutate 同时满足批 P 的交叉校验）；🔴 **本批差点把上一批的门推开**——冻结后 `json.dumps` 抛错退回 `==`，`1==1.0` 又成立，全程不报错且旧测试全绿 ⇒ 改了数据形状要回头跑**消费它的守卫**；`thaw` 让序列化逐字节不变，这是能落地的前提；**docstring 拦不住的名字拦得住**——`staleness_sec` 量的是取数滞后却被当年龄报，但实测最大只差 87 秒且不闸任何东西，**潜伏 ≠ 正在发生**，别说过头；问年龄必须说清基准 ⇒ `age_at()` 强制传参；可注入的依赖+硬编码的标识 = 必然说谎的记录；一个从不被检验的指纹只是让人放心；🔴 **为「验不了的那部分」设计降级前先确认它真的验不了**——差点写成 R-3 违规，实测 376 行全部可校验；sabotage 的 `tail -3` 截掉了分母（同型第四次） | ✅ |
| 48 | [独立复核撞见的漏网之鱼：合并进主分支的未清理冲突标记](48-unresolved-conflict-markers.md) | 复核 `e-two` 分支时发现 5 个文件、6 处冲突标记是**已提交**内容，不是工作区脏状态——两边冲突内容恰好相同（都是当时的测试条数），手工解决冲突时保留了内容却删错了标记行；🔴 **三道闸门全部合法地视而不见**：`pytest -q` 不解析 Markdown、`audit_public.sh` 只找敏感内容形状、既有 docs 检查都是子串/正则匹配特定内容——冲突标记不落在任何一类里，损坏混进去了两轮才被人工看出来；补的新守卫判据要精确到 git 真实吐出来的形状（`=======` 整行只有 7 个字符，不能只看"开头像不像"）——第一版在真仓库上就撞上 `architecture.md` 的 RST 表格分隔线假阳性 | ✅ |
| 49 | [给证据一个身份（裁定 16 · 批 1）](49-evidence-identity.md) | 裁定 16 的第一批，**只加字段不强制**：`Evidence` 加内容寻址的 `evidence_id`、`kind` 三分类、`input_evidence_ids`；🔴 需求的**前置条件不存在**时先把前置条件单独落地 ——`Evidence` 本来没有 id，引用无从谈起；内容寻址胜出是因为 risk 要引用**别的 verdict** 里的证据，序号类 id 得先限定 verdict；`init=False` —— 能由内容算出来的就别让人传，参数的存在本身就是「你可以给个不一样的值」的承诺；`kind` 的真正用途是**关掉 L-13**（否则批 2 的规则只能靠 `source.startswith("derived:")` 判）；🔴 **sabotage 抓到一条空测试** —— 两个入参经 `deep_freeze` 归一成同一对象，断言恒真，「通过」不说明它在守东西；铁律 4 的守卫拦下了我自己手搓的字典版契约，被逼出来的那版测试比原版更能说明问题；🔴 **又一次用 `tail` 截掉了失败清单**（三条只看见两条，第 47 章刚写过）；1246 组 id 重复来自修订行重述同样的证据 ——`evidence_id` 标识「哪份内容」不是「哪一行」 | ✅ |
| 50 | [五份口径收成一份，顺便发现规则写宽了（裁定 16 · 批 2）](50-provenance-single-impl.md) | 「这条证据出自哪份原始响应」原先散在**五个 skill 各写一遍**且写法都不同 ——判断 L-3 的标准是「改了一处另几处会不会跟着对」，不是代码像不像；🔴 **规则的理由对，范围写宽了**：「派生值没有单一来源」只对一部分成立，规则却按全部写，实测 770 条派生证据的溯源一直都在、白白被丢掉；匹配方向只能有一个（更具体⇒命中，更泛⇒不命中），反方向是把「没法确定是哪一份」翻译成「就算是某一份吧」= R-3 形状；🔴 **更正批 1 的一个诊断** ——「机制描述正确」不等于「结论正确」，我准确描述了 `startswith` 方向却把它当成病因，实际那 3 个是真跨源；🔴 **工具不等于守卫** —— `probe.sh` 存在，测试写进生产库仍然重演了第二次 ⇒ 加 autouse 围栏（读也拦，按绝对路径判，按文件名判会误杀 22 个文件）；又一次 `git checkout` 毁掉未提交改动 —— sabotage 的还原手段与备份范围必须同时确定 | ✅ |
| 51 | [让 `src/easyup_biga/` 真的自足（F 节 · 批 U-I）](51-package-self-containment.md) | 迁移「对外接口能跑」与「包自足」是两件事，前者达成不蕴含后者：`pyproject.toml` 的 `pythonpath` 同时挂着 `skills/` 与 `src/`，包内部一行改回旧写法的 `from _contract import ...`，`test_store.py` 依然 47 条全绿，只挂 `src/` 的隔离 import 却当场 `ModuleNotFoundError`；🔴 本批真正的交付物是**只挂 `src/` 的隔离判据**，不是那 9 个文件 13 行 import——判据在，写坏当场红；守卫判据必须是 AST 不是 grep（文档字符串用法示例、相对导入下划线兄弟模块会误报，函数内惰性 import、`__import__(...)` 会漏报，四种形状 grep 错三漏二）；惰性 import 需要单独一条「真把函数调起来」的测试——模块级 import 测试走到包边界就结束，函数体里那行从未被求值，sabotage 实测三条守卫里两条抓到、剩下那条全绿等于什么都没查；顺手删的一处 `sys.path.insert` 不属于批 U-III 的范围，因为它的唯一用途就是同一次编辑删掉的那一行 | ✅ |
| 52 | [让这个仓库变成一个装得上的包（F 节 · 批 U-II）](52-packaging-metadata.md) | 外部评审 F 节批 U-II：`pyproject.toml` 补 `[build-system]`+`[project]`，五个问题全部**核实**不凭空定；`skills/` 不进包三条理由任一都够（连字符目录做不成包 / 无仓库外消费方 / 装进去等于占 site-packages 这个共享命名空间的通用名＝R-2 的形状）；依赖是**扫出来的**——结论零依赖，网络走 stdlib `urllib.request`；🔴 **探针前提不成立时重新设计而不是跳过**（没有依赖项可删 ⇒ 换成「声明⇔代码双向一致」）；版本写 `0.3.0.dev0` 不写 `0.2.0`（写已发布版等于宣称「这棵树就是那个 tag」），代价是版本号有两个出处 ⇒ 守卫判「不矛盾」非「字面相同」；`requires-python` 宁可窄，未经测试的下界是空头宣称；console scripts **裁定不做**且守卫钉的是**当初的前提**（`tools/` 不是包、8/10 脚本仓库绑定）而非结果；🔴 **探针当场证伪我自己刚写的判据**——按 glob 重算那版只看 `include` 漏了 `exclude`，改成**问 setuptools** 而不是补写 glob；陈旧 `build/` 会让配置改动看着没生效（非 pip 缓存）；🔴 `pip install` 造出的 `build/lib/` 第二份拷贝让降级扫描把它当成**契约的第二份实现**，五条守卫集体误报——修在**扫描器**里而非测试的复制清单里，且沙盒不再复现 ⇒ 必须另有单独测试钉住；editable 安装**测不出打包范围**，`pip install .` 才是验收；并行撞树第三次（撞上进行中的 merge）⇒ 不解别人的 merge，挪进独立 worktree | ✅ |
| 53 | [让证据说清自己是什么（裁定 16 · 批 3）](53-evidence-kind-and-lineage.md) | 铁律落地：`kind="derived"` ⇒ 必须有 `raw_hash` **或** `input_evidence_ids`；🔴 规则不是「派生一律要 inputs」—— technical 的 MA 从整份 K 线算出，`raw_hash` 已足够，再要一串 id 就是硬凑 ⇒ **铁律按证据实际长什么样定，不按哪条听起来更严**；`kind` **不给默认值**（默认值把「未决定」伪装成「已决定」），41 个调用点逐个声明；🔴 **留白不是漏做** —— 6 个跨源聚合字段故意留 `kind=None` 并写明理由，因为 Evidence 今天只能记一个 raw_hash 而它们有多个来源；🔴 铁律一上线就抓到真问题：emotion 把**指纹计算写进了 `if store` 分支**，于是 `--no-store` 跑出来的证据全无 raw_hash —— 同样的输入、不同的可追溯性；正则改**调用**会漏多行（单位是配平括号块不是行）；🔴 **`kind` 是语义判断不能批量填** —— 自动补全脚本把三个 observed 标成了 derived | ✅ |
| 54 | [接上 lint，拿一个**有人会看**的基线（F 节 · 批 U-IV）](54-lint-baseline.md) | 外部评审 F 节批 U-IV，范围刻意收窄成**只加配置 + 拿基线，不承诺清零**；🔴 `line-length` 是唯一重要的决定——ruff 默认 88 报 **741** 条（其中 E501 占 81%、63% 含中文），取 100 报 **169** 条，差别全在这一个值；原因是 **ruff 的 E501 按显示列宽算**，中日韩字符占两列 ⇒ 88 列≈44 个汉字；阈值要**量**不能拍（实测列宽 p99.9=101/max=114 ⇒ 仓库事实约定就是 100，阈值的作用是标出异常值不是宣布整仓是异常值）；🔴 **一份没人看的 lint 配置比没有更糟——它训练人忽略输出**；只开 `E`/`F`/`I`，`B`/`UP`/`SIM` 引出的是写法偏好属于另一件事；工具放 `optional-dependencies` 否则当场违反第 52 章那条「声明⇔代码双向一致」守卫；顺手修掉真 bug 3 处 `F821`（注解引用了本文件没 import 的 `datetime`）——**但说清它潜伏而非正在发生**（`get_type_hints` 实测 NameError，普通 import 不受影响）；基线光给总数没用，35 条 mypy 里 **11 条同一根因**；8 条 `int(lastrowid)` 指的正是第 34 章记过的事；🔴 坑：`index("[tool.x]")` 匹配到**注释里**的同名表头把注释劈成两半，新表头还只能加在当前表所有键之后 —— 改完立刻 `tomllib.loads()` 一次；基线要在**最终会发布的那棵树**上量（合 batch3 后重量过一遍）| ✅ |
| 56 | [删路径操作，和「全绿是因为别处兜住了」（F 节 · 批 U-III）](56-sys-path-cleanup.md) | 126 处 `sys.path.insert` 删 56 留 70；🔴 **提示词给的判据对测试文件不成立**——`sys.path.insert` 是进程级副作用而 pytest 把所有模块导进同一进程 ⇒ 删一处再跑全量**必然全绿**，因为别处兜住了（实测：`test_store.py` 自己一行 insert 都没有，靠字母序第一个自举的文件活着）；判断「这行还有没有用」先问**它的作用域有多大**，进程级副作用必须单独待在一个进程里才判得准；真正的分界线是**谁在跑它**（`tests/` 经 pytest 有 pythonpath ⇒ 冗余；`skills`/`tools`/`deploy` 直接跑 ⇒ 承重），且这条线要核实不能假设（`tests/` 无 `__main__`、无 `python3 tests/...` 调用方）；🔴 **同一批里按字符串形状分类栽了三次**——`parents[2]` 解析是 `skills` 却因字面不含该词被放行、ruff 给了行号我却用文件名+字符串匹配删错行（3 条测试当场失败）、守卫第一版把写在一个字面量里的`"skills/market-calc/scripts"` 误判成冗余；孤儿 import 用 ruff F401 找但要取**差集**（仓库本有 25 条，一把 `--fix` 会连历史违规一起改）；剩 69 处删不掉——被 233 处旧名字 import 钉住，前提是先给薄壳定退役，那是独立架构决定 ⇒ **留白要带理由不带到期日不明的承诺** | ✅ |
| 55 | [一个字段装下四种出处（裁定 16 · 批 4）](55-origin-refs.md) | `input_evidence_ids` 只能说「从另几条证据算出来」，装不下 risk 的「哪些 verdict 到场」、跨源聚合的「多份 raw」、`market_open` 的「交易日历那一行」⇒ 结构化 `OriginRef(kind, ref)`；🔴 **不用 `"verdict:123"` 前缀字符串** —— 那是把「这是什么」交给正则猜，L-13 刚收拾完；校验按类别分开（只有 raw/evidence 的 ref 是哈希）；🔴 **留白要带到期日** ——批 3 的 6 处 `kind=None` 理由消失后全部消化，并用 AST 扫描钉死不许复发；🔴 **`grep -c FAILED == 0` 有两种含义**：全过了 / 根本没跑（收集阶段就炸了）——同一家族第三次「没看分母」，要看 summary 行；🔴 **sabotage 抓到一条不存在的守卫** —— `OriginRef.kind` 的校验从没有测试守着；文本扫描被 docstring 绊住 ⇒ 判「有没有这样的调用」要用 AST | ✅ |
---

## 阅读约定

- 所有路径用 `~` / `$HOME`，不写真实家目录。
- 命令前缀统一为 `BIGA=~/.openclaw-biga/bin/biga`，原因见第 01 章。
- 「已有实例」= 同机上先存在的那个 OpenClaw。**教程只涉及与它共存所需的隔离措施，不涉及它本身。**
- 每章末尾的「验证」小节是**可执行的**，不是描述。跑不出预期结果就别往下走。
