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

---

## 阅读约定

- 所有路径用 `~` / `$HOME`，不写真实家目录。
- 命令前缀统一为 `BIGA=~/.openclaw-biga/bin/biga`，原因见第 01 章。
- 「已有实例」= 同机上先存在的那个 OpenClaw。**教程只涉及与它共存所需的隔离措施，不涉及它本身。**
- 每章末尾的「验证」小节是**可执行的**，不是描述。跑不出预期结果就别往下走。
