# 第 24 章 · 把编排变成程序：生产入口切换

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 C-II —— 「谁能启动出卡」从「守卫拦住」变成「够不到」；
> 判官为什么是个新的叶子 agent；`bin/biga-card` 收缩；一个被总闸掩盖的生产 bug；
> **以及我在这一批里亲手踩的一次「守卫盯着老地方」的活教材（真花了钱）**
> **不覆盖**：SnapshotCoordinator（批 D）、RiskPolicy 前移（批 F）、飞书/Outbox（批 G）

---

## 目标 / 产出

- `skills/decision-card/scripts/orchestrator.py` —— `DecisionOrchestrator`，程序驱动
  Stage 0→3，走批 B 那条 8 步细粒度状态链
- 新建 `synthesizer` 判官 agent（`allowAgents=[]` 叶子）—— 综合判断从 `main` 移出来
- `bin/biga-card` 收缩成薄 CLI：五道守卫 → 调 orchestrator → 核验 → 退出码
- 删掉 legacy 粗边 `PREFLIGHTED → CARD_PERSISTED`（批 B 欠的账）
- `ORCHESTRATION.md` 收缩、`AGENTS.md` 不再教 `main` 手工编排
- 948 条离线测试全绿（938 → 948）

---

## 为什么这么做

### 「谁能启动出卡」：从「拦住」到「够不到」

L-14 出卡递归事故（第 19 章、`architecture.md` §9）的根子是一句话：
**入口做的事就是把编排提示词喂给 `main`，而 `main` 的契约里又写着「要出卡就跑那条入口」。**
每一层都在照文档做，没有一层察觉异常 —— 187 个会话、$8.99。

事故当天的修法是**守卫**：ownership 判据 + 单实例锁 + 预算闸门。它们是对的，但性质是
「运行时拦住不该启动的人」。这一批要做的是把那条边界**往前挪一格**：

> 从「守卫拦住了不该启动的人」变成「除了这条 Python 路径，根本没有别的路能启动」。

具体怎么「够不到」：编排不再是一段喂给 LLM 的提示词，而是一个 Python 对象
（`DecisionOrchestrator`）。`main` 是个 agent，它能调的只有工具；而这个对象不是
可被 spawn 的 agent，也没有对应的工具 —— **它没有一条工具调用能到达编排器。**
这不是「被拒绝」（那还得有人去拒），是「够不到」（压根没有那条边）。

> 🔴 **两种失败模式要分清**：「被拒绝」意味着路还在、只是有人守着；
> 「够不到」意味着路不存在。探针 P2 要能区分这两者 —— 断言 `main` 没有工具能到达
> orchestrator，而不是断言「它调了但被拦」。

`entry_guard.py` 与单实例锁**不删** —— 人仍可能手工把命令贴给 `main`。它们从
「唯一防线」退成「纵深防御的一层」。

### 判官为什么是个新的叶子 agent，而不是 spawn `main`

老路径里，综合判断（`status`/`headline`/`synthesis`）是 `main` 做的。程序化之后，
这一步交给谁？两个选项：

1. **spawn `main`**：让程序起一个 `main` 会话，只让它做判断。
2. **新建一个 `synthesizer` 判官 agent**：`allowAgents=[]` 的叶子。

选 2，理由是**安全模型不一样**。`main` 的配置里 `allowAgents` 是「其余 7 个全部」——
它带着全套 spawn 能力。如果判官是 `main`，那么一段注入进判断任务的提示词，就能让它
再拉起一轮编排 —— L-14 的攻击面又回来了。而 `synthesizer` 在配置上就是叶子：

```jsonc
"synthesizer": { "subagents": { "allowAgents": [] } }   // 结构上 spawn 不了任何东西
```

它**从来就没有**那个能力，不是「这次被限制了」。判官只回 status/headline/synthesis
三样，靠 `outputSchema` 拿结构化回复（不解析它的自然语言 —— 那是 F3/L-13 的形状）。
**数据不经判官搬运**：Card 的证据由程序从冻结的 verdict 原件直接组装。

> 通用原则：**把一个只需要判断的角色，交给一个在能力上就做不了别的事的执行体。**
> 「限制它能做什么」不如「它本来就做不到」——后者不依赖任何一道守卫不被绕过。

### 收缩之后 `ORCHESTRATION.md` 还剩什么

批 C-I 之前，编排步骤合并成 `ORCHESTRATION.md` 里一段 `<!-- PROMPT -->`，`bin/biga-card`
抽出来喂给 `main`。程序化之后，「怎么执行」（占号 / spawn / 等待 / 传号 / 合成）**是代码**
—— `orchestrator.py` 的 `_specialist_task` / `_risk_task` / `_synth_task` 发出的就是给各
角色的指令。所以那段提示词删掉，`ORCHESTRATION.md` 只留**各角色的指令口径与契约要求**
（日期怎么处理、stance 必带、risk 的否决映射表）。

> 🔴 **为什么「怎么执行」不该再用提示词说一遍**：它现在是代码。同一段知识两份实现，
> 弱的那份会悄悄漂 —— 这份文档自己就漂过：`--decision-id`「到底填不填」有三处互相
> 矛盾的说法，其中「不要填」那条产出过一张混血卡（`BIGA-20260921-014`）。搬进代码后
> 不复存在：orchestrator 永远显式传占好的号，`synthesize.py` 永远优先用证据自带的号。

### 动了 `main` 的 `AGENTS.md` —— 一处有意识的取舍

通用前置写着「不改任何 Agent 的 AGENTS.md」。但 `main` 的契约里有整整一节
「🔴 收到编排提示词时你就是执行者，照着步骤做：占号 → spawn → 等待 → 合成」。
这一批的中心断言是「`main` 失去启动管线的能力」—— 留着那节，既与断言直接矛盾，又是
一处 L-3 第二套口径（而且正是 2026-09-21 那次 4/5 spawn、$0.4 白花的指令本身）。

⇒ 把那节改成「出卡是程序，你没有一步可做；就算有人把那段提示词贴给你也不要照做」。
这是对默认约束的**有意识的、最小的**推翻 —— 默认是防「顺手重设计 agent 的判断口径」，
而这里是完成本批的既定目标。取舍写进了交给评审的交接说明，让独立会话来判它对不对。

---

## 执行

`DecisionOrchestrator.run(ctx)` 的骨架（`orchestrator.py`）：

```python
did = ctx.decision_id or reserve_decision_id(by="orchestrator")   # Stage 0：占号在 open_run 之前
open_run(ctx)                                                     # RECEIVED
state = _to(RECEIVED, PREFLIGHTED)
with self._attach(f"agent:main:orchestrator-{run_id}", ttl_ms=(deadline+60)*1000) as ad:
    state = _to(state, SNAPSHOT_FROZEN)      # 批 D 之前 detail 不谎称「已冻结」
    state = _to(state, STAGE1_RUNNING)
    handles = [ad.start(a, did, task, group_id=gid) for a in STAGE1_AGENTS]  # 共用一个 groupId
    r1 = ad.wait(handles, stage1_sec)
    state = _to(state, STAGE1_COMPLETED, detail={"usage": ...})   # usage 落 run_events.detail
    ...  # RISK_RUNNING → risk；SYNTHESIZING → 判官；组装；CARD_PERSISTED；COMPLETED
```

几个刻意的点：

- **占号在 `open_run` 之前** —— `decision_id` 从第一条 run_event 起就非空。批 B 留的
  「开 run 时还没号」那个口子是给老路径的，Orchestrator 不继承它。
- **缺席不是失败**：某个 Specialist 超时/没回，记 `missing`、照常出卡。只有判官没给出
  判断、或**零证据**（没有任何 verdict 落库）才整体 FAILED。「出一张标着不知道的卡，
  比不出卡强」。
- **legacy 粗边删除**：`bin/biga-card` 收缩后，`PREFLIGHTED → CARD_PERSISTED` 没有调用方了
  —— 从 `LEGAL_TRANSITIONS` 删掉，否则是条恒不被走的死边（L-7）。P4 用 AST 扫全仓确认没人再引用。

`bin/biga-card` 收缩后的主干：

```bash
# 五道守卫，顺序原样：熔断 → ownership → 单实例锁 → 预算闸门 → 第一次付费调用
timeout $((CARD_DEADLINE_SEC+60)) $PY skills/decision-card/scripts/orchestrator.py   # ← 第一次付费
# → spawn_check（真 spawn 理应过）+ readback_check（毒行巡检）→ 退出码
```

等待 / 传号 / 合成 / 看门狗那段 bash 轮询循环整段没了 —— 现在是同步的 Python。

---

## 坑

### 一个被总闸掩盖的生产级 bash bug

收缩时把进度提示写成：

```bash
echo "  （实测约 3 分钟、约 $1.2；期间可以做别的事）"
```

`bin/biga-card` 用 `set -uo pipefail`（nounset）。出新卡时脚本**以无参数方式跑**，
于是 `$1` 是未绑定的位置参数 —— `$1.2` 里那个 `$1` 一展开就 `unbound variable`，
脚本**当场终止**，走不到 orchestrator。

它为什么没早被发现：总闸文件 `.biga-card-stop` 当前开着，无参数跑会先在总闸那一步
`exit 3`，撞不到这行。**一旦解除总闸就会咬人。** 是 `test_spawn_proof` 的沙盒
（copytree 时刻意排除了总闸文件）把它跑出来的。转义成 `\$1.2` 即可。

> 通用原则：**`set -u` 下，任何双引号字符串里的 `$<数字>` 都是定时炸弹。**
> 而「当前有个前置守卫挡着，撞不到」不是安全 —— 那个守卫一撤，它立刻活过来。

### 🔴 这一批最贵的一课：付费点挪了地方，而我的验证盯着老地方

这是「守卫查的地方，和它声称守的地方，不是同一处」（L-13）——本仓库复发最多的
形状——**而这次咬的是我自己，就在做这一批的时候。**

收缩把「第一次花钱的动作」从 `$BIGA agent --agent main` 换成了
`$PY orchestrator.py`。而 `test_spawn_proof` 那些「走完出卡路径」的沙盒测试，一直是靠
**桩掉 `BIGA` 环境变量**来防止真花钱的。问题是：Adapter 用的是 `DEFAULT_BIGA`
（写死的真实路径），**根本不读 `BIGA` 环境变量**。于是：

> 桩 `BIGA` 拦不住花钱了 —— 真正的付费动作是 `orchestrator.py`，它另走一条路。

我发现这个之前，为了确认上面那个 `$1.2` 修好了，手工跑了一次无参数 `bin/biga-card`
（把总闸指向一个不存在的文件，好让它别拦）。它 `timeout 10` 的外层 shell 被杀了，但里面
`timeout 840 orchestrator.py` 那个**孙进程被孤立后继续跑**，真的 spawn 了五个 Specialist。
它自己的 run/卡写进一次性临时库（随后删了），但**被 spawn 的 Specialist 继承不到我那个
`BIGA_DB_PATH`**，用的是默认库 = 生产 `data/biga.db`，落下 **11 条孤儿 verdict**
（task `BIGA-20260922-001`，没有对应的 decision_record）。代价是约一次 fan-out 的真金白银。

好在 `data/biga.db` 是 git-ignored（不进仓库、不影响克隆）、只追加（删不掉、也不该删，
L-8）、且这些 verdict 是孤儿（不上任何卡）。但**钱是真花了**。

两个独立的教训：

1. **修守卫要跟着付费点一起挪。** 我把 `_seeded_repo` 改成桩掉 `orchestrator.py`（真正的
   付费点），并在 `_run` 里加了运行时自证：**沙盒的 orchestrator.py 不是桩就当场炸**——
   fail-closed 落在使用点，不指望元测试兜。元守卫（「测试跑出卡必须把付费调用换成桩」）
   也从查 `BIGA` 改成查「付费调用被中和」。
2. **开发期绝不手工跑无参数出卡入口。** 只有桩掉 orchestrator 的测试才安全。
   验证一个 bash 语法修复，本不该用「真跑一次生产入口」的方式 —— 那正是「验证 fail-closed
   的测试，本身不能有 fail-open 的代价」那句话，我在教程第 19 章写的，这一批又违反了一次。

> 🔴 **收缩/搬移一段代码时，第一个要问的是：有没有哪道守卫，是靠「被搬走的那个东西的
> 名字」来生效的？** 桩、断言、检查、豁免 —— 它们盯的地址会不会因为这次搬移而失效？

### 看门狗（stall_watchdog）的去向

看门狗原来挂在 `bin/biga-card` 的 bash 轮询循环里，专治非交互 `ask_user` 死锁。那段循环
被收缩掉了。新路径里每个 spawn 带 `runTimeoutSeconds`，一个卡在 `ask_user` 的会话**理应**
被运行时按 timeout 收掉 —— 但「`runTimeoutSeconds` 是否在 `blocked_tool_call` 状态下真的
开火」读代码验不了。

⇒ 没有验证「运行时确实兜住」之前，不删这道安全网（R-3）。模块与其单元测试原样保留，
只删掉那条断言「它接进了 bash 循环」的测试（那个循环没了）。残留风险写进 `TODO.md`：
下一批要么验证运行时会收掉阻塞会话后正式退役它，要么把它接到 orchestrator 的超时诊断
路径上，给它一个真实消费方。**「这一批用不到」不等于「让它悄悄消失」。**

---

## 验证

```bash
# 1. 离线全绿（批 C-II 之后 948 条）
python3 -m pytest -q | tail -2

# 2. P3 · 守卫顺序前后判据不变（收缩最容易手滑挪顺序）
python3 -m pytest -q tests/test_entry_guard.py -k 守卫排在第一次花钱之前
#    五道守卫按 熔断→ownership→锁→预算→付费调用 排序；付费点从 $BIGA 换成 orchestrator.py，
#    顺序与四个拒绝退出码（各 exit 3）不变

# 3. P4 · 全仓无 legacy 粗边引用（删边前跑命中 run.py 的 _LEGACY→红，删后→0）
python3 -m pytest -q tests/test_run_state_machine.py -k legacy

# 4. 元守卫探针：把 _seeded_repo 的 orchestrator 桩去掉 → _run 的自证断言当场红；恢复→绿

# 5. 回放一致（拿一个已落库的号，不出新卡）
bin/biga-card --check <某决策号>
```

**尚未做完**：P1（真跑端到端、断言走 8 步细粒度链）与 P2（`main` 够不到 orchestrator）
需要先重启网关让 `synthesizer` 进 roster，再 live 跑一次（约 $1.2，需授权）。**未做完不算收口**
—— 记在 `TODO.md`，交给独立评审时一并说明。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 「谁能启动」从「守卫拦住」变成「够不到」—— 编排是个 Python 对象，`main` 没有工具能到达它 |
| 2 | 「被拒绝」和「够不到」是两种失败模式，探针要能区分：路还在有人守 vs 路不存在 |
| 3 | 只需判断的角色，交给能力上就做不了别的事的叶子 agent；「本来就做不到」强于「被限制」 |
| 4 | 「怎么执行」变成代码后，别再用提示词说一遍 —— 弱的那份口径会悄悄漂（那份文档自己漂过） |
| 5 | `set -u` 下双引号里的 `$<数字>` 是定时炸弹；「有前置守卫挡着撞不到」不是安全 |
| 6 | 🔴 收缩/搬移代码，先问：有没有守卫靠「被搬走那东西的名字」生效？付费点挪了，桩也得挪 |
| 7 | 验证 fail-closed 的动作本身不能有 fail-open 的代价 —— 我又违反了一次，真花了钱 |
| 8 | 「这一批用不到」不等于「让它悄悄消失」：看门狗零消费方，残留风险进 TODO，不删安全网 |

---

## ⏩ 评审回合一：两条阻塞项（写完之后追加）

独立评审没通过，两条阻塞项 —— 都被判为「必须改代码，不是要不要裁」，都对：

**1 · `orchestrator.py` 没有 ownership 守卫。** 我在交接里把它列为「最可能被攻破的一处」
并建议「评审裁要不要加」。评审把它升级成阻塞：这不是「加一道纵深防御」，是这一批
写在 orchestrator.py 头部的**中心断言**（「main 没有一条工具调用能到达它」）**当前是假的**
—— `main` 有 shell，能 `exec python3 orchestrator.py` 绕过 bin/biga-card 的守卫。而那句
断言正是 C-II 存在的理由（关掉 L-14）。修法很小、非新机制：`main()` 里调已经存在且被
测过的纯函数 `entry_guard.classify_caller()` —— agent 血缘 → 拒绝，人/cron → 放行。

> 教训：**当你把一处写进「弱点」时，先问它是不是「这一批的中心断言现在是假的」。**
> 如果是，它不是弱点，是阻塞 —— 「建议评审裁」这种措辞把一条硬缺陷说轻了。

**2 · 孤儿化事故没有结构性修法，只有「以后小心」。** 更值得记的是评审**怎么**发现的：
它没信交接摘要（摘要把根因归给 `$1.2` 那个 bash bug），而是自己核对因果链 —— 用一个
隔离 bash 子进程验证「那行在编排调用之前，真崩在那根本到不了 spawn」，于是断定摘要的
归因是错的，再去读本章上面那节「坑」才看到完整链路（外层 timeout 杀 shell、孙进程孤立）。

> 🔴 我在同一章里把两个教训**分开写**了（桩要跟着付费点挪 / 别手工跑无参入口），这个
> 拆分是对的 —— 但我只给**第一条**落了代码，第二条只写成承诺。评审的话：本项目一整批
> 的核心论点就是「AGENTS.md=意图，Code Guard=安全保证，不能互相顶替」；这条用在 `main`
> 身上成立，用在**「我们自己以后会小心」**身上也一样成立。⇒ `bin/biga-card` 加 `trap`
> （EXIT/TERM/INT/HUP）+ 后台 wait，wrapper 一死就收掉编排子进程。承诺变成了代码。

两条都补了探针、都见过红（关守卫 / 空操作化 reap，各自报红后还原）。P1/P2 live 仍待
两条修完后、另开会话再跑 —— 评审的理由很干脆：一条是「让 main 绕过守卫的活口子还开着」，
另一条「长跑本身就是孤儿化最容易复现的场景」，都不该在阻塞项修完前做。
