# 确定性编排升级 · 任务分发提示词

> 📄 **操作** · 自包含，可直接粘贴
> **覆盖**：批 A-I / A-II / B 的开工提示词、每批通用的纪律与验收 ｜
> **不覆盖**：升级方案本身（见 [`../design/deterministic-orchestration.md`](../design/deterministic-orchestration.md)）、
> 各批的实际结果（做完写进 `../tutorial/`）

---

## 怎么用

1. **每批开一个新会话**，`cd ~/.openclaw-biga/workspace`
2. 粘「**通用前置**」+ 「**批 X 正文**」两段（中间不用加任何话）
3. 那个会话做完 ⇒ **回到另一个会话做评审**，评审要求见本文末尾

### 🔴 为什么开工与评审必须分不同会话

本仓库的头号复发模式是 **L-13：守卫查的地方，和它声称守的地方，不是同一处** ——
到 2026-09-21 已经**十次**，两轮外部评审里都是复发率最高的。

十次的共同形状不是「写得草率」，而是：

> 实现者带着「我知道我想干什么」的上下文去看自己的判据，
> 于是**看到的是意图，不是代码**。

评审会话没有那个上下文，它只能读代码实际查了什么。
`docs/guide/review-prompt.md` 里那句话对本文同样成立 ——
**参与过建造的会话会带着「我知道为什么这么写」的上下文，那恰恰是评审要避开的东西。**

### 批 A 为什么拆成两个会话

设计文档 §6 把契约硬化列为「批 A，八项」。那是**设计口径**。
分发时拆成两段，因为它们的耦合面完全不同：

| 会话 | 内容 | 性质 |
|---|---|---|
| **A-I** | A3 写边界重校验 + A4 严格 JSON | 只动 `_store` 的三个写函数。**独立、高价值、低耦合** |
| **A-II** | A1/A2/A5/A6/A7/A8 值对象与不变量 | 动类型层，涟漪到全仓 |

A-I 先做，因为它给 A-II **兜底**：类型层大改期间若产生非法对象，
写边界会当场拒绝，而不是悄悄写进只追加表里变成删不掉的毒行。

⚠️ 这是分发口径，**不是设计变更** —— 设计文档仍是 SSOT，不要去改它的编号。

---

## 通用前置（每批都要粘这一段）

```text
你在 ~/.openclaw-biga/workspace，这是 EasyUp for BigA 2.0 的代码仓库 ——
一套基于 OpenClaw 的 Multi-Agent A 股短线决策辅助系统（不自动下单，
产出一张证据可追溯、可回放的 Decision Card，人做最终决策）。

## 第一步：读这五份，别跳

1. CLAUDE.md                                   三条红线 + 四条不变式 + 15 条已定裁定。已定的事不要重新讨论
2. docs/design/deterministic-orchestration.md  本次升级的设计 SSOT。你要做的那一批写在 §6
3. docs/design/architecture.md §9              失败模式清单 L-1…L-14。这是本仓库最重要的一节
4. .claude/skills/dev-workflow/                本仓库的开发流程（🔴 用仓库内这份，不要用用户级那份）
5. docs/README.md                              文档规约。写任何文档之前读

## 第二步：开工前跑一次环境自检

    BIGA=~/.openclaw-biga/bin/biga
    $BIGA --version                                  # 期望 OpenClaw 2026.9.5
    [ ! -e ~/.nvm/versions/node/v24.21.0/bin/openclaw ] && echo "✅ 红线 R-2 完好"
    python3 -m pytest -q | tail -2                   # 基线必须全绿
    git status --short                               # 确认工作区干净（DREAMS.md 是运行时产物，可忽略）

任何一项不符就停下来查清楚，不要往下做。

## 🔴 六条硬纪律

R-1  所有 OpenClaw 命令走 ~/.openclaw-biga/bin/biga，绝不用裸 openclaw
R-2  不占用共享命名空间里的默认名（nvm bin / systemd 单元名 / 端口）
R-3  UNKNOWN ≠ PASS。算不出来必须说算不出来，进 missing[] 并显示在 Card 上
G-1  🔴 每一道新守卫先用探针验证它会红 —— 把被守的东西真的弄坏，确认报红，然后还原。
     红不了就说明判据落错了地方。这条是 L-13 的唯一解药，不许跳过
G-2  docs/external/ 只读，永不修改
G-3  仓库 Public，push 即发布。commit 前跑 tools/verify/audit_public.sh --worktree

## 交付物（五样，缺一不算完成）

1. 代码
2. 测试（新增的不变量测试 + 现有回归全绿）
3. 🔴 探针记录 —— 每道新守卫「怎么弄坏的 / 报红的输出 / 已还原」，写进 CHANGELOG
4. CHANGELOG.md [未发布] 条目，🔴 写「为什么」不只是「做了什么」
5. 教程章节 docs/tutorial/20-*.md 起（裁定 10：每完成一段先写教程再往下做）

## 完成判据（五条闸门，全过才算这一批做完）

1. python3 -m pytest 全绿（当前基线 748；加了测试就跑 tools/verify/sync_test_count.sh）
2. 新增的契约/不变量测试全绿
3. 🔴 每道新守卫的探针都见过红
4. bin/biga-card --check <一个已有决策号> 回放一致
5. tools/verify/audit_public.sh --worktree 十一项全绿

⚠️ 第 4 条需要一个已落库的决策号，用 bin/biga-card --list 3 挑一个。
   不要为了验收去出新卡 —— 一次约 $1.2，而且 .biga-card-stop 总闸当前是开着的。

## 🔴 这一批明确不做

- 不改任何 Agent 的提示词 / AGENTS.md
- 不动 bin/biga-card 的五道守卫顺序（熔断 → ownership → 锁 → 预算 → 第一次付费）
- 不出新卡、不调用任何付费模型
- 不做设计文档里不属于这一批的项 —— 想到了就记进 TODO.md，不要顺手做

## 做完之后

不要自己宣布通过。把下面三样交出来，由另一个会话评审：
  · git diff 的摘要
  · 每道探针的红灯输出
  · 你自己认为最可能被攻破的一处，以及为什么
```

---

## 批 A-I · 写边界重校验 + 严格 JSON

```text
做设计文档 §6 批 A 的 A3 与 A4 两项。先读 §2「评审断言的复核」里的追加 1。

## 要解决的问题（已实测复现，不是假想）

    save_verdict(非法对象)   →  ✅ 成功落库
    load_verdict(同一行)     →  ❌ ValueError（from_dict 复校验，铁律 1）

写入不校验、读取校验。而 agent_verdicts / decision_records 都有只追加触发器，
删不掉也改不掉 ⇒ 一次误写让那次决策永久无法回放。
**一个可修复的错误被变成了不可修复的错误。**

复现方式（构造合法 → 事后改成非法）：

    v = AgentVerdict(..., verdict="PASS", missing=[])   # 合法
    v.missing.append(MissingItem("事后塞的", "market.turnover.unavailable"))
    save_verdict(v)                                      # 目前会成功

⚠️ 当前生产库是干净的：239 条 verdict / 37 张卡，读不回来的 0 条，
   含 NaN/Infinity 的 0 条。**这是预防性修复，不是救火。**
   顺带加一条巡检，让「有没有毒行」从此可查。

## A3 · 写边界重校验

_store/db.py 的三个写函数（save_verdict / save_card / save_raw_snapshot）
在 INSERT 之前必须走一遍：

    Domain Object → 规范序列化 → 严格重建 → 不变量校验 → DB

也就是：不信任调用方交进来的那个对象，只信任「它序列化之后还能不能重建出来」。

🔴 必须保住现有的三段式语义，不许顺手改薄：

    新造的卡        —— 契约层直接拒绝
    从库里读的旧卡   —— 可读，但把问题记下来并显示在卡面上（from_store=True）
    落库            —— 永远拒绝（save_card 已有的 foreign task_id 检查）

「能不能重建当时看到的东西」优先于形式一致 —— 这是本仓库反复裁定过的口径。
⚠️ 特别注意 replay.py --store 这条路：它存的是 synthesize(historical=True) 出来的卡。
   重校验不能把合法的回放落库路径一起挡掉。跑一次真实回放确认。

## A4 · 严格 JSON

所有持久化拒绝 NaN / Infinity / -Infinity。

🔴 但不要照抄外部评审的 drop-in，它有一处会出事：

评审建议统一 json.dumps(..., ensure_ascii=False, sort_keys=True,
allow_nan=False, separators=(",", ":"))。而 _store/db.py::payload_sha256
目前**不带 separators**，Evidence.raw_hash 与 raw 层的对应关系就建在这个哈希上。
加上 separators 会改变所有历史哈希，而失效是静默的（两串 sha 都「看起来正常」）。

⇒ 拆成两个函数：
   · 新的规范序列化（含 separators）—— 只用于新增载荷
   · payload_sha256 —— **只加 allow_nan=False，分隔符维持现状**

## 必须做的探针（G-1）

P1  构造「合法 → 事后改成非法」的 verdict，断言 save_verdict 拒绝
P2  同上对 save_card
P3  把 A3 的重校验注释掉，断言 P1/P2 变红（证明测试真的挂在那段代码上）
P4  写入 {"x": float("nan")} 与 float("inf")，断言被拒
P5  🔴 历史哈希向量不变。**这一步必须是这一批的第一个动作** ——
    在改任何代码之前，用**当时的** payload_sha256 生成固定向量并落进 fixture：

        # 🔴 改动之前跑，不是改完之后
        python3 - <<'EOF' > tests/fixtures/payload-sha256-vectors.json
        import sys, json; sys.path.insert(0, "skills")
        from _store.db import payload_sha256
        cases = [
            {"a": 1, "b": "中文", "c": [1, 2, {"d": None}]},
            {"z": 1.5, "a": True, "nested": {"k": "v", "l": [",", ":"]}},
        ]
        print(json.dumps([{"payload": c, "sha256": payload_sha256(c)} for c in cases],
                         ensure_ascii=False, indent=2))
        EOF

    ⚠️ 绝对不要在改完之后重新生成一遍 —— 那样这条测试就变成 new == new，
       什么都没查。这正是 L-13 的形状。
       ⇒ 生成之后**立刻 git add**，让 diff 能证明它早于改动。

    🔴 你会撞上一个审查误报，先看这里再动手：
       tools/verify/audit_public.sh 的「Gateway token」一项包含 \b[0-9a-f]{64}\b，
       而 sha256 正好是 64 位十六进制 ⇒ **fixture 一落地，审查就报 2 处命中**。
       它不是误伤 —— 64 位十六进制确实高度可疑，这条规则本身是对的。

       ⇒ 这一批要顺带把这条检查改成能区分「令牌」与「内容哈希」，并且：
         · 判据不要写成「文件名像 fixture 就放行」（那是 L-13 的形状：
           按名字豁免，改个名就绕过去了）
         · 建议方向：只在**赋值/键值上下文**里把 64 位十六进制判成令牌
           （token = <hex> / "token": "<hex>"），裸出现的内容哈希不算
         · 🔴 改完必须用探针验证它仍会红：造一行「网关令牌的赋值形式」
           （该项检查自己列的那两个字面量之一，后面跟 64 位十六进制），
           审查必须命中。
           ⚠️ 探针内容写进**临时文件**，跑完删掉 —— 不要写进仓库里的任何文档。
              把那个字面量抄进文档，会被这条检查当场拦下（本仓库已犯过五次）
P6  巡检：新增一个只读检查，遍历 agent_verdicts / decision_records，
    报告「存在但读不回来」的行数。探针：手工造一行毒行（用测试库，不要用 data/biga.db），
    断言巡检报红

## 不要做

- 不要动 MissingItem / frozen dataclass —— 那是 A-II
- 不要改 schema（A-I 不需要迁移）
- 不要去「修」生产库里的历史数据（raw 层永不改写，L-8）
```

---

## 批 A-II · 值对象与不变量

⚠️ **A-I 合并之后才开这一批。** A-I 已于 `33fc55a` 合并并通过评审复核。

🔴 **A-I 的评审留下两条硬账，必须在这一批还掉** —— 见正文 F-4 与 A6 两处。

```text
做设计文档 §6 批 A 的 A1 / A2 / A5 / A6 / A7 / A8。先读 §2 的追加 2 与追加 4——
追加 4 是 A-I 评审复核留下的两条硬约束（A2 的探针清单要加什么、A6 的核对
必须走哪条路径），下面 A2/A6 两节只是它的复述，读追加 4 能看到完整的
「为什么」。

## A1 · MissingItem 脱离 str，成为 (code, detail) 值对象

它现在是 str 的子类，身份因此落在**文本**上：

    MissingItem("数据源不可用", "market.turnover.unavailable")
    == MissingItem("数据源不可用", "news.feed.unavailable")      → True
    放进 set 塌成一条

🔴 这不是洁癖，已实测在回放路径上咬人：
card_ops.synthesize() 按 (code, text) 去重，而 replay.py 反推 extra_missing
时用 `m not in from_verdicts` —— 只按文本。结果：

    合成 Card.missing = 2 条（文本相同、代码不同）
    回放反推 extra_missing = 0 条  ⇒  重建出的 Card 只剩 1 条缺失项

**回放悄悄让卡变好看** —— 与教程第 8 章当年那个 bug 同形状。

⚠️ 当初做成 str 子类是有理由的（现有代码里到处是 f"{m}"、"x" in m、sorted(missing)），
   那份理由写在 skills/_contract/missing.py 的 docstring 里，**先读它**。
   改法不是推翻它，是把「显示」与「身份」分开：
   保留 __str__ = detail，而 __eq__ / __hash__ 基于 code。
   ⇒ 用 AST 扫一遍全仓，找出所有把 MissingItem 当字符串用的地方，逐个改掉。
   MissingItem.coerce() 仍是唯一入口，legacy.unclassified 的收编规则不变。

## A2 · 核心对象 frozen

Evidence 已经是 frozen。AgentVerdict / DecisionCard / MissingItem 改成 frozen，
list/dict → tuple/Mapping。

⚠️ __post_init__ 里现在有几处**自我赋值**（把 missing 收成 MissingItem、
   补 generated_at、写 identity_warning / restate_warning）。frozen 之后
   要用 object.__setattr__，或把这些前移到构造之前。**不要为此放宽校验。**

## A5 · Card roster 结构化

Card 现在允许同一个 agent 出现两次，也允许只有 1 个 agent（已实测）。

🔴 不要用外部评审 §8 的六个具名字段 DecisionInputs ——
   写死 roster，discipline（裁定 13 故意不建）一上线就要改类型定义。
   **roster 是配置，不该是类型。**

⇒ 用本仓库已验证过的结构性核对：_contract 的 STAGE1_AGENTS / STAGE2_AGENTS
  + tests/_consistency.py 的 built_agents()（F8/F10 修复用的就是这一套）。
  拒绝重复 agent；缺席按「已建成的 roster」判定，例外必须显式登记、不许静默。

## A6 · VerdictRef 上卡

VerdictRef(agent, verdict_id, content_sha256, contract_version)，
Card 记录 input_verdict_refs。这样回放能证明「这张卡用的是哪一条判定原件」。
探针：改掉库里某条原件的 sha，断言回放核对报红。

🔴 **核对必须哈希「库里存的那段 verdict_json 文本」，绝不能从对象重新序列化。**

这是 A-I 评审的 F-3。A-I 给 `verdict_json` 加了 `separators`，同一份 verdict
的存储字节从 206 变成 185 —— 也就是说 `_canonical_dumps` 的格式**不是冻结的**。
如果核对写成「把对象重新序列化再比哈希」，A-I 之前落库的 **239 条原件**
会集体对不上，而且是静默的：两串 sha256 都「看起来正常」。

⚠️ `tests/test_write_boundary.py::TestVerdictContentShaIsHashOfStoredText`
已经把这条钉住了 —— **先去读它**，不要再自己推一遍。

## A7 · confidence → data_completeness

六个 skill 里它全是 len(result) / _EXPECTED_FIELDS —— 那是**字段覆盖率**，
不是模型置信度。from_dict 双键兼容，历史卡仍要能读。

⚠️ 不要顺手加 assessment_confidence —— 没有消费方的字段就是一条 L-1 死配置。
   等真的有人要用再加。

## A8 · 修订与回放的血缘

实测目前无约束：修订可以指向另一个 agent、另一次决策的原件；
一条原件可以有多条分叉修订；load_card(decision_id=乱写, record_id=真) 会
静默忽略 decision_id 正常返回。

⇒ save_verdict(amends=...) 校验 new.task_id == old.task_id 且 new.agent == old.agent；
  线性修订加 DB unique 约束；
  load_card 拆成 load_online_card(decision_id) 与 load_card_by_record_id(record_id)
  两个 API，**不要再同时接受两个 id 并悄悄忽略其中一个**。

## F-4 · 给 `readback_check.py` 接一个真实调用方（A-I 评审欠的账）

A-I 建了毒行巡检，但它**只有测试和文档，没有任何自动路径会跑它**。
对照 `spawn_check.py`：它在 `bin/biga-card`、`budget_report.py`、三个测试里都有调用点。

两者是同一个形状：需要一个**真实的库**才有东西可查，而 `data/biga.db`
只在出过卡之后才存在 ⇒ pytest 覆盖不到生产库。

> 一条不会红的守卫，比没有守卫更糟：它占着「这件事已经有人管」的位置。

⇒ 接进 `bin/biga-card` 出卡之后那一段，紧挨 `spawn_check`（只读、零成本）。

🔴 **退出码必须被用上。** `spawn_check` 当年就栽在这里 ——
调用在、退出码被丢，守卫照样绿（`bin/biga-card` 用的是 `set -uo pipefail`，没有 `-e`）。
探针：把巡检改成必然失败，断言 `bin/biga-card` 的退出码跟着变。

⚠️ 毒行是**历史遗留**，不是本次运行的错 ⇒ 它不该让出卡这条命令失败。
   用与 `spawn_check`（`exit 4`）不同的信号，或只在摘要里显著提示 ——
   **先想清楚「什么时候它不该红」再写**，否则又是一个每次都亮、很快没人看的告警。

## 必须做的探针（G-1）

每一项至少一条：把守的东西弄坏 → 报红 → 还原。特别是：
  · A1：同文本异代码断言 !=，且 replay 反推能找回那一条（用追加 2 的场景做回归）
  · A5：造一个重复 agent、造一个缺席 agent，各自报红
  · A6：改掉 sha，回放核对报红
  · A8：跨 agent / 跨决策的修订被拒；两条分叉修订被 DB 拒
  · A6：把核对改成「对象重算」，断言 A-I 那条测试报红（证明你没写成重算式）
  · F-4：把巡检改成必然失败，断言 `bin/biga-card` 的退出码跟着变（不只是打印）

🔴 **探针本身也要先证明它会红。** A-I 的评审自己栽过一次：
用 `数据源不可用。` 对 `数据源不可用` 做探针，而契约层的 `_PUNCT`
把句号归一化了 —— 两条正文根本没碰撞，**探针从头到尾没触发它声称要触发的检查**，
差点把一个有效的修法误判成无效。
⇒ 写完探针先加一步「前置断言」：确认不改代码时它**确实**命中了目标条件。

## 不要做

- 不要碰 Orchestrator / SnapshotCoordinator / Risk 拆分 —— 那是后面几批
- 不要改任何 Agent 的 AGENTS.md
```

---

## 批 B · 运行身份 + 状态机

⚠️ **A-II 合并之后才开这一批。** A-II 已于 `884f0fb` 合并并通过三轮评审复核
（F-5/F-6/F-8）。它不依赖 spike，可以与 spike 并行。

```text
做设计文档 §4「身份模型」与 §5「显式状态机」，落成 schema v7
（🔴 原设计写的是 v6——批 A-II 的 A8 先落了 v6，schema.py 的迁移列表
只许在末尾追加，这里顺号往后挪一位，不是重新设计）。

## 要解决的问题

目前只有 decision_id 一个身份，它被迫同时承担五件事。后果：
  · 飞书事件重投 = 重跑一次决策（没有 trigger_id 做幂等键）
  · 硬超时收掉一次运行后重试，两次尝试挤在同一个 decision_id 上，事后分不开
  · 「所有 Specialist 看的是同一份数据」这句话目前无法验证（没有 evidence_set_id）

## 做什么

1. RunContext(trigger_id, decision_id, run_id, evidence_set_id, origin,
   non_interactive, created_at) 进契约层
2. schema v7：decision_runs / run_events / evidence_sets
   🔴 建表时就要带只追加触发器 —— v4 建 decision_ids 时漏过一次（F1），
      代价是整套决策身份机制建在一个可撤销的地基上。
      tests/test_store.py::test_每张表都有只追加触发器 会兜底，别让它红
3. transition(run_id, expected_state, next_state) —— compare-and-set 语义，
   非法转移抛错。不允许任何代码直接 UPDATE state
4. 13 个状态，**照设计文档 §5 那张表，一个不多**
   🔴 不要把外部评审原文那 15 个照抄进来。少的那两个
      （IDENTITY_RESERVED / SNAPSHOT_COLLECTING）在本系统里没有代码能进入、
      也没有消费方会读 —— 凭空多一个状态就是一条 L-1 死配置
   NOTIFICATION_PENDING 推到批 G（outbox 存在之前它没有消费方）
5. bin/biga-card 暂时仍走老路径，但开始写 run 记录 —— 新旧并存，**不改行为**
6. biga-card --status <run_id>：能说出「死在哪一步」

## 判据

- 一条运行的 run_events 序列可以完整复述这次运行走过的路
- 非法转移（跳步 / 回退 / 并发双写）被 CAS 拒绝
- 🔴 每个状态都要能指出「谁写它、谁读它」。指不出读取方的状态，删掉

## 探针（G-1）

P1  并发两个进程对同一 run_id 做同一个 transition，断言只有一个成功
P2  尝试 UPDATE decision_runs / DELETE run_events，断言触发器拒绝
P3  把 CAS 退化成无条件 UPDATE，断言 P1 变红
P4  登记一个没有读取方的状态，断言「状态必须有消费方」的检查报红

## 不要做

- 不要开始写 Orchestrator —— 那是批 C，且它卡在一次尚未做的 spike 上
- 不要改 decision_ids 的占号逻辑（那是 v4/v5 的地基，已经被攻过）
```

---

## 批 C–H · 现在不写分发提示词

| 批 | 为什么现在不写 |
|---|---|
| C（Orchestrator + RuntimeAdapter） | ✅ spike 已于 2026-09-22 通过（设计文档 §7），不再是阻塞项。提示词等批 B 落地后再写 —— 它要引用 B 实际登记的状态与 `RunContext` 字段 |
| D–G | 依赖 A / B 的实际落地形状（新类型长什么样、状态机实际登记了哪些状态） |
| H（包结构重组） | 排在最后 —— 它会让期间所有其他批次的 diff 变脏 |

🔴 **现在把它们写出来，得到的是一份过期的分发清单** —— 那正是 L-6 文档漂移，
而本仓库已经因为「两份清单各自过期」吃过亏（`TODO.md` 与设计文档的出口条件漂了一次）。

⇒ 每批合并之后，回来补写下一批。

---

## 评审会话怎么做

开工会话交回三样（diff 摘要 / 探针红灯输出 / 它自己认为最可能被攻破的一处）之后，
评审会话按这个顺序看 —— **顺序是有意的**：

1. **先看探针，不看代码。** 每一道新守卫：它声称守什么？探针弄坏的是不是**同一样东西**？
   本仓库五次被探针抓到假守卫，四次是「测了函数，没测产品线真的走了这个函数」
2. **再挑一道守卫自己动手弄坏一遍。** 不要信提交里贴的那段输出
3. **然后才读 diff**，只问两个问题：
   · 有没有哪处把现有的三段式语义（新卡严格 / 旧卡可读 / 落库永远严）改薄了
   · 有没有新增一个没有消费方的东西（L-1）
4. 最后跑一次 `docs/guide/review-prompt.md` 里第 1 节的抽查

⚠️ **不要在评审会话里顺手修。** 发现问题写清楚交回开工会话 ——
评审者动手修，下一轮就没有独立视角了。
