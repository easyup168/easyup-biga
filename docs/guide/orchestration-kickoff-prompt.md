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
- 不出新卡、不调用任何付费模型 —— **这是默认值，不是绝对禁令**。如果这一批的正文
  明确写出「需要真实调用，理由是什么，预算上限多少」，以正文为准，不要拿这条默认值
  把正文的要求废掉。（批 C-I 撞过这个坑：正文要求三项 live 补验，通用前置这条却原样
  照抄自批 A——那时纯 Python 探针确实不需要真实调用。见教程第 23 章「坑」一节）
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

## 批 C-I · Runtime Adapter + spike 补验

⚠️ **批 B 合并之后才开这一批。** B 已于 `d401cc2` 合并并通过评审复核。

🔴 **批 C 在分发时拆成 C-I / C-II 两个会话** —— 与批 A 拆成 A-I/A-II
是同一个理由：两者耦合面不同，且 C-I 能给 C-II 兜底。C-I 是纯粹的
"Python 能不能在不经过 LLM 轮次的情况下驱动一次 spawn 并拿到结构化
结果"，可以完全独立测试，不碰 `bin/biga-card` 一个字。C-II 要把它接进
生产入口——那是这次升级里第一次改动"出卡到底怎么被触发"这条路径，
必须建在一个已经被验实的地基上，不能带着"Adapter 大概没问题"的假设
往前走。⚠️ 这是分发口径，不是设计变更 —— 设计文档仍是 SSOT。

```text
做设计文档 §6 批 C 的 OpenClawRuntimeAdapter 部分 + §7「全案的单点风险」
剩下未验的三项。先读 §7 全文——三条硬约束（groupId / grant 生命周期 /
token 用量）是 spike 实测出来的 API 约束，不是设计选择，不要重新论证
要不要遵守。

## 第一步：把 spike 留下的三个未知数测掉

spike 只验证了 1 次 spawn。这里要验的是"批 C 会依赖的那几种用法"，
不是再跑一次一样的东西：

1. **五路并行 fan-out 真的并行** —— 五个 sessions_spawn 共用同一个
   groupId（硬约束：collect=true 没有 requesting run id 时必须带
   groupId），断言五个运行的起止区间在时间上**相交**，不是排队执行完
   一个再执行下一个。判据是区间重叠，不是"五个都成功了"——成功但排队
   执行，看起来完全正常，那正是本仓库在别处已经踩过的「延迟其实是
   正确性 bug 的症状」形状。
2. **grant 在长跑里不会中途失效** —— TTL 设成 ≥
   `BIGA_CARD_DEADLINE_SEC`（当前 780s），实际跑够这个时长（真实
   specialist 调用，不是 sleep 模拟），断言 grant 全程可用。设计文档
   §7 记的"grant 不会被 attach 自己回收，只会到期"这句话本身没被验证
   过，跑一次真实场景来验它。
3. **spawn 失败时的错误面是结构化的，不是裸异常** —— 目前只见过一种
   失败形状（缺 groupId）。故意造一个真实失败（塞一个不存在的 agent
   名，或让某个 specialist 本身超时），确认能把它翻译成一个可判断的
   状态，不是让原始异常直接从 Adapter 里往外抛，调用方接不住。

三项不过，就不要往下写 Orchestrator —— C-II 的地基还没有。

## 做什么

`OpenClawRuntimeAdapter`：

    start(agent, task_id, *, group_id) -> handle
    wait(handles, timeout) -> list[Result]
    cancel(handle) -> None
    status(handle) -> 归一化后的状态（不是运行时原始字符串）

🔴 **状态归一化**：`sessions_spawn`/`agents_wait` 返回的原始状态是运行时
自己的措辞，会随运行时版本变。Adapter 对外只暴露自己定义的一小组状态
（比如 running/succeeded/failed/timeout），内部做翻译 —— 上层（C-II 的
Orchestrator）不该知道运行时原始状态长什么样，否则运行时改一个措辞，
整条编排链都要跟着改。

grant 生命周期（spike §7-2 定死，缺一不可）：
  · `biga attach --print-config --session
     agent:main:orchestrator-<run_id> --ttl <TTL>`，TTL 取
     `BIGA_CARD_DEADLINE_SEC` 或更长
  · run 进终态后**主动删掉**那个临时 `.mcp.json` —— grant 不会自己
    回收，只会到期，不删就是每次运行都在攒的小泄漏
  · Stage 1 五路 fan-out **共用一个 groupId** —— API 硬约束，不是
    "要不要"的设计选择

token 用量：`agents_wait` 返回带 `usage: {inputTokens, outputTokens}`。
写进 **`run_events` 的 `detail`**，不要写进 `agent_runs` —— v2 已经删掉
`tokens_in`/`tokens_out` 两列，理由是"唯一真相源是运行时 trajectory，
这里再存一份就是滞后的第二套口径"，那条理由现在仍然成立（L-1）。

## 不要做

- 不要碰 `bin/biga-card` —— 它一个字都不改，这一批不接生产入口
- 不要写 `DecisionOrchestrator` —— 那是 C-II，要等这一批的探针全部
  见过红才开
- 不要动 `ORCHESTRATION.md` —— 那也是 C-II 的事

## 必须做的探针（G-1）

P1  五个真实 specialist 用同一个 groupId 并行 fan-out，断言运行区间
    在时间上相交（不是先后排队）
P2  grant 设 TTL=780s+，真实跑够这个时长（不是 sleep），断言全程可用；
    run 结束后确认对应的 `.mcp.json` 已被删除
P3  故意让一次 spawn 失败（不存在的 agent 名 / 制造超时），断言 Adapter
    返回的是一个结构化的失败状态，不是未捕获异常
P4  `status()` 对外只暴露归一化状态 —— 找一处直接把运行时原始状态字符串
    传出去的代码，临时改回原始状态，断言依赖归一化状态的测试报红

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 C-II · DecisionOrchestrator + 生产入口切换 ★

⚠️ **C-I 合并之后才开这一批。** C-I 已于 `d8e7ea6` 合并并通过评审复核。
它是这次升级里第一次改动生产入口（`bin/biga-card`）—— C-I 的 Adapter
探针全部见过红，才有地基做这一步。

🔴 **C-I 评审留下一条没验的残留风险，见下面 P5** —— `cancel()` 的
`active[]/tasks[] `同序映射只在 N=2、无 drain 场景下实测过；离线复核过
算法本身没有 N=2 专属的 bug，但"运行时是否在更大规模、有 drain 时仍
保持同位对应"这件事无法靠读代码验证。

```text
做设计文档 §6 批 C 剩下的部分：DecisionOrchestrator、bin/biga-card 收缩、
ORCHESTRATION.md 收缩、main 失去启动管线的能力。

## 为什么这是全案的中心，也是风险最集中的一批

这一批做完之后，「谁能启动出卡流程」这句话的答案从"守卫拦住了不该启动
的人"变成"除了这条 Python 路径，没有别的路能启动"。前者是运行时挡，
后者是根本不存在 —— L-14 出卡递归事故就是在"谁能启动"这条边界上出的事，
这一批恰好是把那条边界从提示词约定改成程序结构。改错的代价不是一个
测试报红，是生产入口的行为改变。

## 做什么

### 状态机改用细粒度链

批 B 建的 LEGAL_TRANSITIONS 里有两条编排主干：一条细粒度（8 步，
RECEIVED → PREFLIGHTED → SNAPSHOT_FROZEN → STAGE1_RUNNING →
STAGE1_COMPLETED → RISK_RUNNING → SYNTHESIZING → CARD_PERSISTED →
COMPLETED），一条 legacy 粗边（PREFLIGHTED → CARD_PERSISTED，专门留给
"编排整个交给一个被 spawn 的 LLM、中间态对 CLI 不透明"这种情况）。
DecisionOrchestrator 走前者 —— 它自己驱动每一步，理应知道走到哪了。

⚠️ SNAPSHOT_FROZEN 这一步现在还没有真东西 —— SnapshotCoordinator 是
批 D。转移到这个状态是合法的（状态机不关心背后有没有真的冻结逻辑），
但转移的 detail 里不要写"已冻结"这类只有批 D 做完才成立的断言；
Specialist 在这一批仍然各自联网取数（老行为，批 D 才改）。

### 占号时机

Orchestrator.run(ctx) 在 open_run() **之前**用 reserve_decision_id()
占好号 —— RunContext.decision_id 从一开始就非空。批 B 留的"legacy 路径
开 run 时还没号，可以是 None"这个口子是给老路径用的，DecisionOrchestrator
不该继续留它。

### bin/biga-card 收缩

🔴 **五道守卫的顺序原样保留**（熔断→ownership→单实例锁→预算闸门→
第一次付费调用），这是 L-14 防护顺序，继续由现有测试钉住 —— 收缩时
最容易犯的错是"重构的时候手滑挪了顺序"，收缩前后跑一次守卫顺序的测试
逐字节对比。

收缩之后它只做：解析参数 → 校验 → 调 DecisionOrchestrator.run(ctx) →
渲染结果 → 退出码。等待 / 传号 / 合成这些"怎么执行"的逻辑全部挪进
Python，bash 里不再有。

做完这一步，批 B 那条 PREFLIGHTED → CARD_PERSISTED legacy 粗边**没有
调用方了** —— 按 TODO.md 已经记的提醒，从 LEGAL_TRANSITIONS 里删掉它，
不要留着变成一条恒不被走的死边（L-7）。

### ORCHESTRATION.md 收缩

只留给 Specialist 的指令文本（第几步该判断什么、契约要求什么）。
顺序 / 等待 / 传号 / 合成这类"怎么执行"的内容全部删 —— 那些现在是
代码，不需要再用提示词说一遍给 LLM 看。「为什么这么设计」的理由留在
设计文档，不要复制过来（复制过来的后果就是又一处会漂的第二套口径）。

🔴 收缩之前先修正它现在的三处自相矛盾（设计文档 §2 追加 3 记的
`--decision-id` 到底要不要填的三种互相矛盾的说法）—— 收缩时把矛盾一并
搬进代码，代价是把一个提示词层面的漂移变成一个代码层面的 bug，改起来
更贵。

### main 失去启动管线的能力

不是"多一道守卫拦住它"—— 是 DecisionOrchestrator 是一个 Python 对象，
不是可以被 spawn 的 agent，main 没有一条工具调用能到达它。
entry_guard.py **不删除**（人仍然可能手工照抄命令贴给 main），但它的
性质变了：从"唯一防线"退成"纵深防御的一层"。

## 不要做

- 不要建 SnapshotCoordinator —— 那是批 D，SNAPSHOT_FROZEN 这一步只需要
  状态存在，不需要真实现
- 不要拆 RiskPolicy 两层 —— 那是批 F，risk_check.py 原样保留，只是被
  Orchestrator 在什么时机调用可能提前
- 不要碰 Outbox / 飞书 —— 那是批 G
- 不要在这一批"顺手"给 main 加回任何形式的启动能力当"过渡期兼容"——
  三段式（有守卫拦 / 纵深防御 / 没有路可走）里，这一批要落到最后一种，
  留个开关走回第一种就是这批唯一的目的没达成

## 必须做的探针（G-1）

P1  真跑一次端到端出卡，断言 run_events 序列走的是 8 步细粒度链，不是
    legacy 粗边
P2  尝试让 main 直接触发出卡（复述 L-14 事故当天的那句提示词/贴那条
    命令），断言它**没有工具能到达** DecisionOrchestrator —— 不是"被
    拒绝"，是"够不到"，这是两种不同的失败模式，探针要能分清
P3  收缩前后各跑一次 bin/biga-card 的守卫顺序测试，逐字节对比通过/
    拒绝的判据没有变
P4  删掉 legacy 粗边之后，确认没有任何测试/代码还在引用
    PREFLIGHTED → CARD_PERSISTED 这条转移 —— 如果有，说明还有调用方
    没迁完，不能删
P5  🔴 **仅当这一批的 Orchestrator 会在硬超时/部分失败时调用
    `adapter.cancel()` 取消还在跑的兄弟 spawn 才适用**：补验 N≥3、且
    至少一个 spawn 已经完成离场（离开 active[] 进 recent[]）的真实场景，
    断言取消命中的仍是对的那一个 —— C-I 只验过 N=2、无 drain。
    如果这一批的 Orchestrator 不调用 cancel()，把这条残留风险原样写进
    TODO.md「已知问题」，不要因为这一批用不到就让它悄悄消失。

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 D-I · SnapshotCoordinator 基础设施

⚠️ **批 C-II 合并之后才开这一批。** C-II 已于 `d35d286` 合并并通过两轮评审复核。
它不依赖 C-II 收尾的 P1/P2 live（那是操作层面的补验，不改代码），可以并行。

🔴 **批 D 在分发时拆成 D-I / D-II 两个会话** —— 与批 A、批 C 是同一个理由：
D-I 是"建 SnapshotCoordinator、在一个已经实测过三次重复抓取的地方（`fetch_index_daily`）
证明它工作"，**不改任何 Specialist 的行为** —— market/sector/technical 仍各自调
`fetch_index_daily`，跟今天一样。D-II 才是真正让 Specialist 改口去读冻结快照，
那一步会改变现有六个 skill 的实际行为，需要 D-I 先把地基验实。
⚠️ 这是分发口径，不是设计变更 —— 设计文档仍是 SSOT，不要去改它的编号。

🔴 **设计文档 §6 批 D 的五段管线名字（`Provider → RawArtifact →
NormalizedSnapshot → FactBundle → EvidenceSet`）目前只是名字，没有字段级定义。**
`evidence_sets` 表的 `manifest_json` 注释写得很清楚：「结构批 D 定，批 B 只建表」。
D-I 的任务之一就是把其中一段变成具体、能跑的形状——但不需要五段都变成独立的
Python 类。你可以合并中间几段，只要 `evidence_sets.manifest_json` 的形状足够
回答「这个决策的冻结数据，是从哪一行/哪几行 `raw_market_snapshot` 来的」，
因为这是批 E 会复用的唯一硬约束（批 E 迁移表写着
`旧 AgentVerdict → LegacyAdapter → FactBundle + AgentAssessment + AgentOutcome`，
`FactBundle` 是共享概念，其余中间态是你的实现细节）。

```text
做设计文档 §6 批 D 的第一段：SnapshotCoordinator，只针对 `fetch_index_daily`
证明「冻结一次、多个消费者读同一份」这件事成立。先读 §6 批 D 全文（很短，
只有一段管线图 + 三句话）——它不完整，这一批要把其中一部分定下来，读的时候
不要以为漏读了什么。

## 要解决的问题（已实测复现，不是假想）

`skills/_sources/sina.py::fetch_index_daily` 被三个 skill 各自独立调用：

    skills/market-calc/scripts/market_calc.py:181     fetch_index_daily(symbol, bars=BAR_COUNT)   # BAR_COUNT=25
    skills/sector-calc/scripts/sector_calc.py:176      fetch_index_daily(_DATE_SYMBOL, bars=2)
    skills/technical-calc/scripts/technical_calc.py:153 fetch_index_daily(SYMBOL, bars=BAR_COUNT)   # BAR_COUNT=120（不是 25——D-I 评审时曾在这里写错，见 TODO.md「D-II 输入 1」）

三次独立网络调用，各自落一行 `raw_market_snapshot`。§2 表格已经把这条断言
复核成立（"§15 各 Specialist 各自抓同一份数据"）。三次调用理论上应该拿到
同一份数据，但没有任何机制保证——"所有 Specialist 看的是同一份数据"这句话
目前无法验证（设计文档 §4 的身份模型表格里，`evidence_set_id` 这一行写的
正是这个）。

## 做什么

`skills/_snapshot/`（新目录，参照 `_contract/` `_store/` `_runtime/` 的既有
命名习惯）：

1. `SnapshotCoordinator`：一个方法，形如
   `freeze_index_daily(decision_id, symbols, *, bars) -> evidence_set_id`。
   每个 `(source, symbol)` 组合在一次决策里**最多真实抓取一次**——
   第一次调用用请求里最大的 `bars`（三个消费者里最大的是 technical 的
   **120**，不是 market 的 25——先核对 `technical_calc.py::BAR_COUNT` 的
   真实值，不要假设它和 market 一样），之后同一决策的重复调用直接从已冻结
   的数据切片。
2. 冻结结果写一行 `evidence_sets`（`manifest_json` 至少要能回答上面那个
   "从哪几行 raw_market_snapshot 来"的问题——具体字段你定，但**必须能被
   反向查询**，不能是一段只用于展示的自由文本）。
3. 读接口，形如 `read_index_daily(evidence_set_id, symbol, *, bars) -> IndexDaily`：
   从已冻结的数据切出调用方要的根数（sector 要 2 根、market 要 25 根、
   technical 要 120 根，都从同一份底层数据切，不是三份独立结果）。
4. `raw_market_snapshot` 的落盘次数：一次决策、两个指数代码（sh/sz）
   ⇒ 最多 2 行，不是今天的最多 6 行（3 个 skill × 2 个代码）。

## 不要做

- 不要改 `market_calc.py` / `sector_calc.py` / `technical_calc.py` 的调用点——
  它们仍然调 `fetch_index_daily`，跟今天一模一样。**这一批不改变任何 Specialist
  的实际行为**，只是在旁边把"冻结一次、多处读"这件事建好、验实。切实际调用点
  是 D-II，需要先有这一批的地基。
- 不要迁移 breadth / pool / news / emotion 的抓取——设计文档 §6 自己写的顺序
  是"先迁共用最多的，再迁 breadth/pool，最后 news/emotion"，这一批只做第一段。
- 不要动 `orchestrator.py` 的 `SNAPSHOT_FROZEN` 转移——它现在的 detail 诚实地
  写着"批 D 之前还没有真东西"；这一批做完之后也还没有真正的消费方（Specialist
  没改口读它），所以那句话仍然诚实，不要提前改成"已冻结"。这是 D-II 的事。
- 不要动 `CROSS_CHECK_PAIRS`（`market.sh_close` ↔ `technical.close`）——
  设计文档 §6 说得很清楚，它会在 Specialist **真的**共享快照之后变成恒真，
  但这一批 Specialist 还各自抓取，这条检查现在仍然是有意义的，删早了是
  自己造一个假阴性窗口。

## 必须做的探针（G-1）

P1  同一个 decision_id，模拟三个消费者按 sector(bars=2)/market(bars=25)/
    technical(bars=120) 各调一次 `SnapshotCoordinator` 的读接口，断言底层
    `fetch_index_daily`（或它下面那层网络调用）只真实发生 **1 次**，不是 3 次
P2  三个消费者拿到的结果必须是**同一份数据切出来的**，不是三份独立结果偶然
    相等——断言 sector 拿到的 2 根，与 market 的 25 根、technical 的 120 根
    里最后 2 根，逐字段相同（不只是数值相等，是同一次抓取）
P3  同一个决策的 `raw_market_snapshot` 落盘行数：两个指数代码 ⇒ 至多 2 行，
    不是 6 行
P4  `evidence_sets` 表已经有只追加触发器（`test_store.py::test_每张表都有
    只追加触发器` 动态发现，理论上自动覆盖）——这一批第一次真的往这张表写行，
    补一条探针确认覆盖仍然成立：造一行、尝试 UPDATE/DELETE，断言被拒
P5  给定一个 `evidence_set_id`，能反查出它到底冻结了哪几行 `raw_market_snapshot`
    ——这是 manifest_json 形状的判据，不是"字段存在就算过"

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 D-II · Specialist 改口读冻结快照 ★

⚠️ **批 D-I 合并之后才开这一批。** D-I 已于 `4a8841c` 合并并通过评审复核。

🔴 **这一批改变现有三个 skill 的实际行为**（`market_calc.py` /
`sector_calc.py` / `technical_calc.py`），是 D 系列里真正的高风险段——D-I
只是在旁边把机制建好，这一批才真正切调用点。三处改动彼此独立、形状相同，
可以在同一个会话里按顺序做，不需要再拆会话；但探针要三处各来一遍，不能
证明了 market 就当 sector/technical 也对。

```text
做设计文档 §6 批 D 剩下的部分：把 market/sector/technical 的
`fetch_index_daily` 调用点，改成走 `SnapshotCoordinator.read_index_daily()`
——真正让「所有 Specialist 看同一份数据」从「D-I 证明机制可行」变成
「这次决策真的发生了」。先读 D-I 的教程第 25 章与 `skills/_snapshot/
coordinator.py` 的 docstring，尤其是 `TODO.md`「批 D-I 施工空档 + 批 D-II
输入」那两条——那是 D-I 评审复核时特意为这一批留的两个具体坑位。

## 要解决的问题

D-I 建好了「冻结一次、多处读」的机制，但 `freeze_index_daily` 现在没有任何
调用方，market/sector/technical 仍各自联网。「所有 Specialist 看同一份
数据」这句话依然只是「机制上可以」，不是「这次决策真的如此」——没有人在
真实运行里调用过 `freeze`，也没有任何检查会在**没调用**时报警。

## 做什么

1. **`orchestrator.py` 的 `SNAPSHOT_FROZEN` 转移接真东西**：在 Stage 1
   fan-out 之前，调 `SnapshotCoordinator.freeze_index_daily(did,
   ["sh000001", "sz399106"], bars=120)`——**120，不是 25**（D-I 评审复核时
   核对过 `technical_calc.py::BAR_COUNT` 的真实值，三个消费者里最大的是
   technical）。把返回的 `evidence_set_id` 存进这次转移的 detail，也存进
   `RunContext`（它从批 B 起就有这个字段，一直是 `None`——这一批第一次
   真的填它）。
2. **三个 skill 的调用点改口**：`market_calc.py` / `sector_calc.py` /
   `technical_calc.py` 各加一个 `--evidence-set-id`（可选，参照现有
   `--task-id` 的写法——缺省 `None`）。给了就调
   `SnapshotCoordinator.read_index_daily(evidence_set_id, symbol,
   bars=N)`；没给就跟今天一样调 `fetch_index_daily(symbol, bars=N)`。
   🔴 **不要把"没给就报错"当成更严格的版本去做**——手工单独跑某个 skill
   调试（`spawn_check.py` 的文档明确提到这种用法仍然存在）不该被这一批
   连坐拦掉，那是把「生产路径该收紧」和「调试路径该保留」混成一件事。
3. **`orchestrator.py` 的 `_specialist_task()` 把 `evidence_set_id` 写进
   任务文本**，让 Specialist 知道要在跑 skill 时带上
   `--evidence-set-id`——这与 `--task-id` 已经是同一种机制（提示词说要做
   什么，跑起来之后靠代码核实是否真的做了），不是新发明一套。
4. **`raw_hash` 语义**（D-I 评审复核留的第二条）：改口读冻结快照的
   Specialist，`Evidence.raw_hash` 应该指向冻结集登记的
   `content_sha256`（`manifest_json` 里那份，D-I 已经记好），不能对自己
   读到的那一截 raw 重新算——那会算出一个数值上"看起来正常"、但与冻结集
   对不上的哈希，而且不报错。
5. **`CROSS_CHECK_PAIRS` 改判据，不是删掉**：`market.sh_close` ↔
   `technical.close` 在两者真的共享同一份冻结数据之后会变成恒真（同一份
   数据算出来的数字必然相等），恒真检查是 L-7 死配置。改成核对**两者的
   `evidence_set_id`（或 `content_sha256`）是否相同**——这样它守的东西
   从「数值凑巧对上」变成「真的共享了同一份数据」，且**仍然能报红**：
   如果某个 Specialist 因为没传 `--evidence-set-id` 而悄悄退回独立抓取，
   两者的 `evidence_set_id` 会不一致，这条检查应该抓到。

## 不要做

- 不要碰 sector/breadth/pool/news/emotion 的抓取——D-I 的范围就只到
  `fetch_index_daily`，这一批只是把已经建好的机制接上，不新建别的冻结管线
- 不要把 `--evidence-set-id` 做成必填——见上面「做什么」第 2 条的理由
- 不要动 `SnapshotCoordinator` 本身的接口——D-I 已评审通过，除非这一批
  发现它的签名接不上（比如 `bars` 该怎么传），否则改接口是另一次设计变更
- 不要顺手给 `discipline` 或 `sector`/`breadth` 建冻结——那不属于这一批
  「三个日线消费者改口」的范围

## 必须做的探针（G-1）

P1  真跑一次（或用桩 Adapter 模拟）编排全链路，断言 market / sector /
    technical 三条 `AgentVerdict` 的 `Evidence` 最终都能反查到**同一个**
    `evidence_set_id`——不是三条分别看起来合理，是三条真的指向同一份
P2  🔴 CROSS_CHECK_PAIRS 改判据之后要证明它还会红：造一个场景让 market 与
    technical 的 `evidence_set_id` 不一致（模拟某个 Specialist 没传
    `--evidence-set-id` 悄悄退回独立抓取），断言检查报红——这是把恒真检查
    改判据之后**必须**补的探针，否则不知道新判据是不是又变成另一个恒真
P3  三个 skill 不传 `--evidence-set-id` 单独跑，仍然各自正常联网出结果
    （手工调试路径没被连坐拦掉）
P4  给一个某 Specialist 只读了部分根数（比如 sector 的 2 根）的场景，断言
    它的 `Evidence.raw_hash` 与读了全量 120 根的 technical 那次记的
    `content_sha256` 相同——不是各自对自己看到的那截重新算
P5  故意让 `evidence_set_id` 传一个不存在的号给某个 skill，断言它 fail
    closed（`SnapshotReadError` 或等价的明确失败），不是静默退回独立抓取

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 E-I · Facts / Assessment 拆分 —— 契约基础设施 + 一个试点 Specialist

⚠️ **批 D-II 合并之后才开这一批。** D-II 已于 `e81f94b` 合并并通过评审复核
（另有 `e8518ed` 修 `agent_runs` 回归、`71789b5` 记 C-II 收尾——三个都要在这
之前）。

🔴 **设计文档自己说这是七批里最贵的一批**（六个 skill 的输出结构、契约层、
存储层、回放、以及大量测试都会被触及）。分发时收紧成两层：**E-I 只建基础
设施 + 迁一个试点 Specialist**（建议 `emotion`——Phase 1 第一个建成的
skill，形状最简单、不参与 `risk_check.py` 的 `CROSS_CHECK_PAIRS`，出问题
影响面最小）；**E-II 起才迁剩下五个**，且要等 E-I 落地的真实类型形状之后
才写（同 D 的道理——现在写只会是一份很快过期的清单）。
⚠️ 这是分发口径，不是设计变更——设计文档仍是 SSOT。

```text
做设计文档 §6 批 E 的第一段：定义 `FactBundle` / `AgentAssessment` /
`AgentOutcome` 三个新契约类型 + `LegacyAdapter`，并在一个试点 Specialist
上证明「旧 `AgentVerdict`、新三型并存」这件事真的成立。先读 §9「兼容策略」
全文——里面「每次只迁一个边界」与「读路径宽、写路径严」两条硬约束，这一批
从第一行代码就要遵守，不是做完了再补。

## 要解决的问题（已有真实事故，不是假想）

`AgentVerdict` 现在把两件产生方不同、时机不同的东西焊在一个 frozen
dataclass 里：
  · **事实**（skill 算出来的，如 `result`/`evidence`/`data_completeness`/
    `missing`）——skill 跑完那一刻就有，是**机械记账**；
  · **判断**（Agent 给的，`stance`）——skill 跑完之后，Agent 才补得上。

`skills/_contract/verdict.py` 那句话点破了这条断层（读它的 docstring，
不要跳过）：`status=PASS` 且 `stance=看空` 时,"没发现问题"与"看多"分不开。

`amend_verdict.py`（skill 先落 verdict、Agent 再补 stance 的补丁路径）本身
就是这条断层已经在咬人的证据——它不是一个方便功能，是"没有它，Agent 没有
任何办法只加一个判断而不重新克隆一整份事实"这件事的补救：

  · **实测事故（`BIGA-20260920-002`）**：补丁路径建成之前，唯一办法是让
    Agent 把整份 JSON 重新吐一遍——15 条 Evidence 的 `retrieved_at` 全部
    转述丢失（L-10）。
  · **F8（外部评审）**：`amend_verdict.py` 曾经把契约层的 stance 校验逻辑
    抄了一遍，连带抄走了同一个 fail-open 盲区（`agent="Market"`
    大小写打错也能通过）——L-3 的形状，判据只该有一处。
  · **F9（外部评审）+ 实测**：Agent 曾经把 `task_id` 当 `--ref` 传错，
    报错后现读源码重试；加 `stance` 那次 Stage 1 延迟直接翻倍
    （71s→133s），62 秒都花在"找 amend 命令怎么用"上——这些成本都是
    "判断必须靠第二条命令后补"这件事本身产生的，不是 Agent 笨。

## 顺带解决批 D-II 留的一笔账

批 D-II 的 `CROSS_CHECK_PAIRS` 改成了比 `Evidence.raw_hash`，但自己承认有
盲区：如果一个 Specialist 悄悄退回独立抓取、又刚好抓到与冻结数据逐字节
相同的结果，`raw_hash` 会碰巧相同，检查漏报（D-II 评审复核认为这个漏报本身
无害，但"是否真的走了共享机制"这件事结构上验证不了）。真正的修法是给
`Evidence` 加一个字段直接声明"我用的是哪个冻结集"——`deterministic-
orchestration.md` 第 372 行明确写着这是批 E 的契约改动。这一批做。

## 做什么

1. **`skills/_contract/` 新增类型**（具体放几个文件、字段怎么分，你定；
   下面是必须满足的约束，不是逐字段的规格）：
   - `FactBundle`：只装 skill 能独立算出来的东西——`result` / `evidence` /
     `data_completeness` / `missing` / `status` / `elapsed_ms` 这一类。
     **不装 `stance`。**
   - `AgentAssessment`：Agent 的判断——`stance`，以及需要指回它评估的是
     哪一份 `FactBundle`（用 id 引用，不要把事实抄进来——抄进来就是回到
     L-10 的形状）。
   - `AgentOutcome`：`risk_check.py` / `card_ops.py` 这类下游消费方实际
     要读的组合视图（`FactBundle` + `AgentAssessment` 拼起来）。
   - 铁律不能因为拆了就松：`missing` 非空 ⇒ 不能是 PASS/completed；
     `UNKNOWN` ⇒ `stance` 必须是"无法判定"——这些跨 `FactBundle`/
     `AgentAssessment` 的约束现在归谁校验、在哪一步校验，你要给出明确答案，
     不能"以后再说"。
2. **`Evidence` 加 `evidence_set_id: str | None = None`**（可选字段，参照
   `raw_hash` 的先例）。`market_calc.py`/`sector_calc.py`/`technical_calc.py`
   读冻结快照时顺手填上（它们已经拿着 `evidence_set_id` 参数，这不是新增
   调用链，是给已有调用点多填一个字段）。
3. **`risk_check.py` 的 `CROSS_CHECK_PAIRS` 补一层判据**：两条 Evidence 都有
   `evidence_set_id` 时，直接比它（结构验证，不是内容碰巧相同）；没有时退回
   现有的 `raw_hash` 比较（老 Specialist 还没填这个字段，不能因此让检查失效）。
4. **`LegacyAdapter`**：`旧 AgentVerdict → LegacyAdapter → FactBundle +
   AgentAssessment (+ AgentOutcome)`。🔴 读路径宽、写路径严（§9 的硬约束，
   不是这一批自己发明的）：能把历史上任何一条已落库的 `AgentVerdict`
   还原成新三型，但**新的落库只允许写新形状**——不然旧格式永远不会自然
   清零，变成第二套要跟着演进的口径（L-3）。
5. **存储层**：`agent_verdicts` 表怎么装新形状（新增列、版本标记，还是新表）
   你定，但必须满足：只追加语义不能丢；`amend_verdict.py` 的线性修订约束
   （`ux_verdict_amends_linear`）在过渡期对**还没迁移的** Specialist 必须
   继续有效——它们还在用旧路径，不能因为这一批就先坏掉。
6. **迁一个试点 Specialist**（建议 `emotion`，理由见上）：改它的 skill 脚本，
   让它产出新三型而不是旧的 `AgentVerdict`；其余五个（market/sector/
   technical/news/risk）**一个字都不改**，继续走 `amend_verdict.py` 老路径。
7. **端到端验证新旧并存**：在一次（可以是离线模拟的）Card 合成里，一部分
   Specialist 给新形状、一部分给旧形状，`card_ops`/`risk_check` 都能正确
   处理，不因为"看到了不认识的类型"而出错或者悄悄漏掉某个 Specialist。

## 不要做

- 不要迁 market/sector/technical/news/risk 这五个——那是 E-II 起的事，
  且要等这一批落地的真实类型形状之后才好写它们的分发提示词
- 不要退役 `amend_verdict.py`——设计文档写得很清楚，退役的前提是**全部**
  Specialist 迁完，现在只迁了一个
- 不要因为"新形状更干净"就顺手改 `synthesizer` 判官的接口或
  `orchestrator.py` 的编排逻辑——它们消费的是 `card_ops.load_verdicts_and_refs()`
  返回的东西，这一批要做到的是让那个返回值不管来源是新是旧都长一样，
  不是去改调用它的地方
- 不要把 `evidence_set_id` 做成必填——第 2 条已经说了理由，还没接冻结快照
  的 Specialist（news/emotion 目前不读日线）没有这个值可填

## 必须做的探针（G-1）

P1  用一条真实的历史 `emotion` verdict（老 `AgentVerdict` 形状），走
    `LegacyAdapter`，断言还原出的 `FactBundle`+`AgentAssessment` 与原始
    facts/stance 逐字段一致——不是"能跑不报错"，是内容对得上
P2  尝试用新落库路径写一条旧形状（或反过来，缺了必需字段的新形状），断言
    被拒——证明"写路径严"是真的严，不是文档说说
P3  🔴 新旧并存探针：造一个场景，Card 合成时 5 个 Specialist 给旧
    `AgentVerdict`、1 个（试点）给新三型，断言 Card 正确聚合了全部 6 个
    的 missing/evidence，没有静默丢掉试点那一个（这条最容易在"看着能跑"
    但其实丢了数据的方式上出问题）
P4  CROSS_CHECK_PAIRS 的新判据：造两条都有 `evidence_set_id` 但故意不同
    的 Evidence，断言报冲突；再造一条有 `evidence_set_id`、一条没有
    （模拟老 Specialist），断言退回 `raw_hash` 比较、不因为字段缺失就
    跳过检查
P5  跨类型的铁律探针：造一个 `FactBundle.missing` 非空但对应
    `AgentAssessment` 却给出 PASS 效果的组合，断言在你选定的那一层被拒——
    先证明这条检查确实存在并且真的会红，再说它挂在哪
P6  回归：对**还没迁移**的某个 Specialist（比如 `market`），走一遍
    `amend_verdict.py` 补 stance 的旧路径，断言它跟这一批之前完全一样
    地工作——这一批不能让老路径先坏

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 E-II–H · 现在不写分发提示词

| 批 | 为什么现在不写 |
|---|---|
| E-II | 迁剩下五个 Specialist + 退役 `amend_verdict.py`；依赖 E-I 落地的真实类型形状（尚不具备） |
| F–G | 依赖 E 系列完整落地（尚不具备） |
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
