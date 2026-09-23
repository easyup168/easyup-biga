# 确定性编排升级 · 任务分发提示词

> 📄 **操作** · 自包含，可直接粘贴
> **覆盖**：已写好的各批开工提示词（A-I / A-II / B / C-I / C-II / C-III /
> D-I / D-II / E-I / E-II / E-III / J-I / J-II / F / G-I / G-II / K / I / L / **H-I**）、
> 每批通用的纪律与验收 ｜
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

1. python3 -m pytest 全绿。🔴 **基线数字开工时自己跑一次记下来，不要照抄本文里的数** ——
   本文不在 test_docs_convention 的「活数字」白名单里，写死在这儿的数必然漂
   （实测：这行曾长期写着 748，而那时真实基线已经过千）。加了测试跑 tools/verify/sync_test_count.sh
2. 新增的契约/不变量测试全绿
3. 🔴 每道新守卫的探针都见过红
4. bin/biga-card --check <一个已有决策号> 回放一致
5. tools/verify/audit_public.sh --worktree 十一项全绿

⚠️ 第 4 条需要一个已落库的决策号，用 bin/biga-card --list 3 挑一个。
   不要为了验收去出新卡 —— 一次约 $1.2，而且 .biga-card-stop 总闸当前是开着的。

## 🔴 这一批明确不做

- 不改任何 Agent 的提示词 / AGENTS.md —— **这也是默认值，不是绝对禁令**，
  同上一条「不出新卡」的道理：正文明确写出要改哪个 AGENTS.md、改哪一节、
  为什么，就以正文为准（批 F 撞到这条：它要求改 `agents/risk/AGENTS.md`
  的「约束 1」，因为 risk 不再自己跑 `risk_check.py` 了，契约文本不跟着改
  就是 L-6 契约与实际行为对不上）
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

## 批 C-III · Orchestrator 健壮性收尾（外部评审）

⚠️ **不依赖 D/E 系列，可以在批 E-I 进行时并行开工。** 这一批只碰
`orchestrator.py` / `bin/biga-card` / `_runtime/adapter.py` / `_store/db.py`
里几处与 Facts/Assessment 拆分无关的既有代码；批 E-I 改的是契约层新增类型，
两者不会撞文件。

🔴 **来源**：2026-09-22 一份外部架构复审（`docs/design/deterministic-
orchestration.md` §2 追加 5 已经复核过它的断言，✅/⚠️/❌ 三种结论都有——
先读追加 5 全文，尤其是 5.1、5.2 两条，不要重新论证哪些断言现在还成立）。
这一批只做追加 5 里标了 ✅ 且判定"值得马上修"的那几条，**不做**标了 ⚠️
（已被 D-II 解决）或者 追加 5.1 说的"暂不建议现在做"（run_id 贯穿全链）
的部分。

```text
做四件独立的小修复，都来自对同一份外部评审的复核（先读设计文档 §2 追加 5，
不要跳）。四件之间没有依赖，可以按任意顺序做，但每件都要有自己的探针。

## C3-1 · CARD_PERSISTED 的转移必须写在 persist() 成功之后

现在 `orchestrator.py::run()` 里 `transition(..., CARD_PERSISTED, ...)`
先于 `card_ops.persist(card)` 调用。如果 `persist()` 抛错，`run_events`
会留下一条"已落库"的假记录，而库里其实没有这张卡。

⇒ 调换顺序：`persist()` 成功拿到 `record_id` 之后才转移，`detail` 里带上
真实的 `record_id`（不是现在的占位字符串）。

## C3-2 · Stage 1 部分启动失败时，已启动的 handle 要被取消

`handles = [ad.start(a, ...) for a in STAGE1_AGENTS]` 中途某个 `start()`
抛错时，之前已经成功 start 的 handle 被直接丢弃——那些 Specialist 会话
继续跑，直到自己的 `runTimeoutSeconds`，白白花钱。

🔴 这恰好是 `OpenClawRuntimeAdapter.cancel()` 一直缺的真实调用方——
批 C-I/D-I/D-II 三次评审都记录过同一条残留风险："cancel() 的 active[]/
tasks[] 同序映射只在 N=2、无 drain 场景下验证过"（`TODO.md`「批 C-II 残留
风险」）。这一批用真实的部分失败场景把它补上，不是另起一个人工场景。

⇒ 改成显式循环 + 已启动 handle 列表，`except` 分支里对已启动的逐个调
`cancel()`。

## C3-3 · verify_verdict_refs 补上 agent 字段核对

当前只核对 `verdict_id` 存在、`content_sha256` 一致，不核对
`ref.agent == 存量.agent`。理论上一张手工拼出来的 Card 可以让 VerdictRef
声称"这是 market 的原件"，实际指向的却是 news 的行（`verdict_id` 相同、
hash 恰好也一致——注意 `verdict_id` 是跨 agent 的全局自增，不是按 agent
namespace 的）。

⇒ `verify_verdict_refs()` 加一行比较，agent 不一致就报不一致，不是"能
找到就算过"。

## C3-4 · Orchestrator 感知总预算，各阶段按剩余时间收窄

`stage1_sec` / `risk_sec` / `synth_sec` 现在各自独立，互不感知
`deadline_sec` 还剩多少。Stage 1 如果吃满自己的预算，Risk 与 Synth 仍然
各自等自己的全额——三段之和可以超过 `deadline_sec`，只靠外层 bash 的
`timeout` 硬顶（纵深防御的最后一层，不该是唯一一层）。

⇒ `run()` 里维护一个 `deadline = time.monotonic() + self.deadline_sec`，
每进入一个阶段用 `min(该阶段自己的预算, deadline - time.monotonic())`
作为这次 `ad.wait()` 的 timeout；`remaining <= 0` 时不再等，直接按
TIMEOUT 处理进入 FAILED。

## 不要做

- 不要碰 §26（直接跑 `orchestrator.py` 绕过预算/单实例锁）——ownership
  这一层已经被 C-II 堵住，budget/flock 那两层要把校验逻辑从 bash 挪进
  Python、两个入口共用，是比这四件事更大的一次改动，留给独立的一批
- 不要碰 §24-25（stale-run reaper 的自动调度）——**可以**写一个纯函数/
  手工调用的"扫非终态 run、按 deadline 判 TIMEOUT"的工具（放
  `tools/verify/` 或新开 `tools/maintenance/`），但**不要**接 cron 或
  systemd timer——那是批 G 的地盘（"配置进仓库"本来就包含这类调度配置）
- 不要动 `run_id` 贯穿 `agent_verdicts`/`evidence_sets`/`VerdictRef`/
  `DecisionCard` 这条链——这件事有专门的一批（「批 J-I · run_id
  贯穿全链」），且明确等**这一批也合并之后**才开工，因为它要改
  `orchestrator.py::_specialist_task()` 的调用参数，跟 C-III 改的
  `run()` 内部逻辑是同一个文件。这里不是"没有消费方"（设计文档 §2
  追加 5.1 2026-09-22 已修正这个判断），只是排在 C-III 后面
- 不要碰批 E-I 正在改的 `_contract/verdict.py`、`_contract/evidence.py`、
  `_store/schema.py`——两批同时在跑，文件层面不该有交集；如果发现非改
  不可，停下来先确认没有跟 E-I 撞车

## 必须做的探针（G-1）

P1  人工让 `persist()` 抛异常，断言 `run_events` 里不存在
    `CARD_PERSISTED`（这条本身就是外部评审建议的回归测试，直接照做）
P2  Stage 1 五个里让第 3 个 `start()` 抛错，断言前两个已启动的 handle
    收到了 `cancel()` 调用（离线用桩 Adapter 验证调用发生；如果要在真实
    Adapter 上验证 N=5/drain 场景，那是 live 补验，不在这一批的离线判据里，
    但要在交接里说清楚这条残留风险是不是已经随之解决）
P3  伪造一条 `VerdictRef`，`agent` 字段与它指向的存量行不一致、其余字段
    全部正确，断言 `verify_verdict_refs()` 报不一致
P4  把某个阶段的预算故意设成会超过 `deadline_sec`，断言后面的阶段拿到的
    实际等待时间被压缩到剩余预算以内，不是继续用自己那段固定值

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

## 批 J-I · run_id capture 贯穿全链

✅ **前置条件已满足（2026-09-23）**：批 E-II（`359b97c`）、E-III（`619d35e`）、
J-II（`933aa6d`）都已合并进 `orchestration`。原来的顾虑是「三批人马抢同一批
skill 文件」，现在那三批都落定了。

🔴 **schema 取 v10。** J-II 已经占了 v9（`ALTER TABLE agent_runs RENAME COLUMN …`）。
开工第一件事仍然 `grep -n "(9, _V9)\|(10, _V10)" skills/_store/schema.py` 核一下。

⚠️ **`orchestrator.py` 的争用对象现在换成了批 F。** 这一批要改
`_specialist_task()`（加 `--run-id`），批 F 要改 risk 的 spawn 方式（前移进编排器）。
两批都还没开工，谁先开工谁占这个文件。**建议 J-I 先**（范围更小、提示词已就绪），
F 排在它后面。

🔴 **改名说明**：这一批 2026-09-23 之前叫「批 E-I 收尾 · run_id 贯穿全链」。
**范围一字未改**，只是连同新增的 J-II 一起并进「批 J · 身份闭环」。
改名理由见设计文档 §6 批 J。

🔴 **来源**：`docs/design/deterministic-orchestration.md` §2 追加 5.1
2026-09-22 的复盘。原判断把「把 run_id 字段补上存下来」和「靠 run_id
做强制校验（拒绝跨 run 串读）」当成一件事，认为都没有消费方、都该无限期
排期。复盘发现前者其实一直有消费方——`run_id` 从批 B 就存在，这里只是让
`agent_verdicts` 等表顺手多存一列，且**现在**恰好是最便宜的时机：批 E
系列正在这一层做迁移，晚一步就要在同一层再开一次刀。**只做 capture，
不做 enforce**——后者仍然要等真正的重试路径出现才做，读追加 5.1 全文。

```text
只做一件事：让 run_id 能够被存下来、传下去，但不改变任何现有的判定/
过滤逻辑。做完之后，`agent_verdicts`/`evidence_sets`/`VerdictRef`/
`DecisionCard` 都能查到自己是哪次 run 产生的——仅此而已，不多做。

## 做什么

1. `_store/schema.py`：给 `agent_verdicts` 加 `run_id TEXT`（nullable，
   历史行读回来是 None，不回填、不假装知道）。检查 `evidence_sets`
   是否已经有等价字段——如果批 D 系列已经顺手加过，这里就不用重复加。
2. 六个 skill 脚本（market/sector/technical/emotion/news/risk）各加一个
   可选的 `--run-id`（参照 `--evidence-set-id` 已经用的那套 CLI 参数
   模式：可选、默认 None、直接传进落库函数，不是新发明一套传参方式）。
   ⚠️ **2026-09-23 更新**：E-III 把 `risk` 也迁完了 ⇒ 六个**全部**产 FactBundle、
   全部走 `save_fact_bundle(fb, *, path)`——**只有一条落库路径**，不是两条。
   本节的旧版写着「risk 走 save_verdict，两条都要改」，那是 E-III 之前的事实。
   `save_verdict` 还在（没删），但六个 skill 都不再用它，这一批不用管它。

2b. 🔴 **`save_assessment` 那条路不要再加一个 CLI 参数。** 事实行由 skill 写，
   **判断行由 Agent 经 `amend_verdict.py --ref <fact_id> --stance …` 写**——
   它天然带着 `amends`→事实行的指针，而事实行已经有 run_id 了。
   ⇒ 让 `save_assessment` **从它 amends 的那一行继承 run_id**，不要让 Agent
   在命令行上再传一遍。理由不是省事：Agent 手传就可能传错，而「继承」
   在结构上不可能与事实行不一致——这正是本仓库反复说的「判据别建在
   可被篡改的输入上」。
3. `orchestrator.py::_specialist_task()`：给**每一个** agent 的任务文本
   都带上 `--run-id {ctx.run_id}`——不是只给读冻结快照的那三个，六个
   都要有，因为六个都在产生落库记录。
4. `orchestrator.py` 里调 `SnapshotCoordinator.freeze_index_daily()` /
   `save_evidence_set()` 的地方，直接把 `ctx.run_id` 传进去——这是
   Python 内部调用，不经过 CLI，不要多绕一层。
5. `skills/_contract/verdict_ref.py::VerdictRef` 加
   `run_id: str | None = None`，从存量行的 `run_id` 列直接搬过来。
6. `DecisionCard` 加 `run_id: str | None = None`，`card_ops.synthesize()`
   从 `ctx.run_id` 填入。

## 不要做

- 不要改 `latest_verdict_ids()` 的过滤逻辑——它现在按 `decision_id` 聚合，
  这一批不改成按 `run_id`，那是 enforce，属于追加 5.1 说的「等重试路径
  出现再做」
- 不要加任何「run_id 不一致就拒绝」的校验——同上，没有消费方
- 不要把 `run_id` 做成必填/NOT NULL——历史行没有这个值，做成必填等于
  强迫历史数据造假
- 不要碰 `agent_runs` 那张表——它的 `run_id` 是**另一个东西**（账本行号），
  改名是批 J-II 的事。这一批**一个字都不要动它**，否则两批的 diff 会缠在
  一起，出问题时分不清是 capture 错了还是改名错了

## 必须做的探针（G-1）

P1  跑一次完整的在线落库，断言存量行的 `run_id` 列等于 `ctx.run_id`
    （不是「没报错」，是取出来比对字符串）
P2  拿一条**没有** `run_id` 的历史行（模拟迁移前数据）走现有的读取/校验
    路径，断言不因为 `run_id` 是 None 就报错或被拒
P3  合成一张不经过 replay 的 Card，断言 `DecisionCard.run_id` 与
    `VerdictRef.run_id` 都能追到同一个 `ctx.run_id`
P4  回放（`replay_of` 不为 None）一张历史 Card，断言不会给它凭空捏造一个
    `run_id`——回放路径不生产新事实，这条铁律不因为加了新字段就松

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 J-II · `agent_runs.run_id` 改名 ＋ `runtime_run_id` 落库

⚠️ **与 J-I 谁先开都行**（两批文件不重叠：J-I 动 skill + 契约，J-II 动
`_store`/`_runtime`/`orchestrator`/`tools/verify`），但**两批共用 schema 版本号**：
先开的那一批占 v9，后开的占 v10。开工第一件事是 `git log --oneline -5` 看另一批
有没有已经落地，别两批都写 v9。

🔴 **来源**：设计文档 §4 的「`run_id` 这个名字现在指三个互不相同的东西」。
2026-09-23 实测出来的，不是读文档推的。三处必须一起改 —— 改一半留下的
半新半旧命名空间比现在更难读（读者无法判断手上这个 `run_id` 属于已改还是未改的那半）。

```text
把 `run_id` 这个名字在仓库里的三个含义收敛成一个。做完之后，
`run_id` 在 BigA 自己的代码与数据库里**只有一个意思**：编排的一次执行尝试。

## 先读这两处，它们是这一批的全部依据

- 设计文档 §4 「`run_id` 这个名字现在指三个互不相同的东西」——三同名对照表
- `tools/verify/phase1_acceptance.py::_runtime_spawn_records()` 的 docstring
  ——它自己写着「决策号本来就明文写在 payload_json 里，只是没被拿来做绑定」，
  这一批给它一个比文本匹配更硬的绑定

## 做什么

### J2-1 · `agent_runs.run_id` → `ledger_id`

它是 `INTEGER PRIMARY KEY AUTOINCREMENT`，从 Phase 1 起就是**账本行号**，
与编排的 `run_id`（32 位 hex）毫无关系。

🔴 **实测：全仓没有任何代码读这一列**（`grep` 过 skills/tools/bin/tests）。
⇒ 现在改是免费的。等 `agent_runs` 有了第一个真实读取方再改就不是了。

- 新迁移条目 `_V9`（或 v10，见上面的版本号约定）：
  `ALTER TABLE agent_runs RENAME COLUMN run_id TO ledger_id;`
  本机 SQLite 3.45.1，`RENAME COLUMN` 自 3.25 起支持。
- 🔴 **不要改 `_V1` 的建表语句。** `MIGRATIONS` 的规则是「只许在末尾追加，
  不许改动已发布的条目」——全新库会先按 v1 建出 `run_id`，再由这条迁移改名，
  这是对的，不是绕远路。
- ⚠️ `agent_runs` 上挂着 `agent_runs_no_update` / `agent_runs_no_delete`
  两个只追加触发器。SQLite 的 `RENAME COLUMN` 会自动改写触发器体，**但要验**
  （见探针 P2）——触发器被迁移悄悄改坏，是只追加这道地基被抽走，而它不会报错。

### J2-2 · `SpawnHandle.run_id` → `runtime_run_id`，并落库

`SpawnHandle.run_id` 取自 `sessions_spawn` 响应的 `runId`，也就是运行时
`subagent_runs.run_id` 那个 UUID ——在 adapter 内部它叫 `run_id` 是对的
（忠实照抄运行时的列名），但一出 adapter 就和编排的 `run_id` 撞名。

- `_runtime/adapter.py`：`SpawnHandle.run_id` → `runtime_run_id`，
  连带 `wait()` 里的 `by_id = {h.run_id: h ...}` 等内部使用一并改。
  `tools/verify/adapter_spike.py` 里那三处打印也要跟着改。
- schema 同一条迁移里给 `agent_runs` 加 `runtime_run_id TEXT`（nullable）。
- `_store` 的 `record_verdict_run()` 加一个可选参数接住它。
- `card_ops.persist()` 加 **keyword-only、默认 None** 的
  `runtime_run_ids: Mapping[str, str] | None`（agent → runtime_run_id）。
  🔴 必须有默认值：`synthesize.py` 与几条测试也在调 `persist()`，
  签名不兼容会把不相干的东西一起弄红。
- `orchestrator.py`：`r1`（Stage 1）与 `r2`（risk）里每个 `SpawnResult` 都带
  `.handle.agent` 与 `.handle.runtime_run_id` ⇒ 在调 `card_ops.persist()` 的地方
  把这个映射传进去。**Stage 3 的 synthesizer 不进这个映射**（它不产 verdict）。
- 回放路径（`replay_of is not None`）**照旧不记账本**，因此也不写
  `runtime_run_id`。回放不重新执行任何 agent，给它记一个真实 spawn id 是假账。

### J2-3 · 让 `spawn_check` 用上它（这才是 J2-2 的消费方）

`phase1_acceptance.spawn_proof()` 现在认 spawn 靠
`SELECT … FROM subagent_runs WHERE payload_json LIKE '%<决策号>%'` —— 文本匹配。

改成：`agent_runs.runtime_run_id` 非空时，直接拿它与 `subagent_runs.run_id`
做**结构化 join**；为 NULL（所有历史行）时**退回现有的 LIKE 判据**。

🔴 **形状照抄 E-I 已经验证过的那一套**：`risk_check.py` 的 `CROSS_CHECK_PAIRS`
就是「两边都有 `evidence_set_id` 就用它，任一缺失就退回比 `raw_hash`」。
逐字同形，**不要发明第二种兼容写法**。

⚠️ 退回分支不许因此变松：现有的三态（`readable=False` ⇒ UNKNOWN /
`rows==0` 且 `agent_runs` 有行 ⇒ 伪造 / `forged` ⇒ 伪造）一条都不能塌成
「查不了就放过」。R-3。

### J2-4 · 一道新守卫，防止第四个同名再长出来

加一条测试，判据要**可派生**，不是清单（`test_roster_matches_config.py`
的教训：清单式检查只加固了当时想到的那几个字段）：

> BigA 自己的 schema 里，任何名为 `run_id` 的列必须是 `TEXT`，
> 且其非 NULL 值必须能在 `decision_runs.run_id` 里找到。

这条能自动抓到「有人又加了一个语义不同的 `run_id`」——因为语义不同的那个
几乎必然过不了「值能在 decision_runs 里找到」这一关（`agent_runs.run_id`
当年就是 INTEGER，第一关就红）。

## 不要做

- **不要碰 `subagent_runs`**（OpenClaw 运行时自己的表）。它的 `run_id`
  是第四个同名，但那是运行时的命名空间，**不归我们管，也不许改**。
  我们这边叫 `runtime_run_id`，正是为了在边界上把它认出来
- 不要给 `runtime_run_id` 加 NOT NULL / 外键——历史行没有它，
  而 `subagent_runs` 在另一个库里，外键根本建不了
- 不要改 `_V1` 的建表 SQL（见 J2-1）
- 不要顺手把 `run_events.detail` 里的 usage/agents 结构也改了——
  那是 C-II 定的形状，这一批不碰
- 不要做 J-I 的事（给 `agent_verdicts` 加 `run_id` 列、六个 skill 加
  `--run-id`）。两批的 diff 缠在一起，出问题时分不清是改名错了还是 capture 错了

## 必须做的探针（G-1）

P1  🔴 **改名后没有任何东西还在读旧名**：全仓 grep `agent_runs` 相关代码
    与 `\.run_id`，确认 `SpawnHandle` 的使用点全部改到 `runtime_run_id`；
    把 `ledger_id` 改回 `run_id` 之外的第三个名字跑一遍测试，确认会红
    （证明确实有测试在盯这个列名，不是「反正没人读所以改什么都绿」）
P2  🔴 **迁移之后只追加触发器仍然有效**：对迁移后的 `agent_runs` 真的执行
    一次 `UPDATE` 和一次 `DELETE`，断言两者都被拒（`AppendOnlyViolation`）。
    这条必须真跑 SQL，不是读 `sqlite_master` 看触发器名字还在——
    名字还在但触发器体被改坏，正是 `RENAME COLUMN` 可能的失败形状
P3  🔴 **结构化 join 真的比文本匹配硬**：构造一条 `agent_runs` 行，它的
    `runtime_run_id` 在 `subagent_runs` 里**不存在**，但决策号出现在某条
    `payload_json` 里（= 蹭上别人记录的那个旧洞）。断言新判据报伪造，
    而旧的 LIKE 判据会放过它。这条是 J2-3 的全部理由，不能只断言「新判据也能过」
P4  **退回分支不塌**：历史行（`runtime_run_id IS NULL`）走 LIKE 判据，
    断言现有的三态判定逐条不变（复用 `tests/test_spawn_proof.py` 已有的用例，
    不要新写一套）
P5  **J2-4 的守卫见红**：临时给某张表加一个 `run_id INTEGER` 列，
    断言新测试报红；还原

## 🔴 一处**必须 live 验**，且线下无法替代

`SpawnHandle.runtime_run_id`（来自 `sessions_spawn` 的 `runId`）与
`subagent_runs.run_id` **是不是同一个命名空间** —— 这是 J2-3 整个结构化 join
的前提，而它在线下只能靠「两边都长得像 UUID」来猜。猜错的后果是
spawn 核验**静默退化**（join 永远匹配不上 ⇒ 每次都走退回分支 ⇒ 看起来一切正常）。

判据：起**一个**最小 spawn（参照 `tools/verify/adapter_spike.py` 的用法，
但任务文本给最短的那种，别照抄它那句「采集今日A股情绪数据」），
记下返回的 `runtime_run_id`，然后直接查运行时库：

    SELECT run_id, child_session_key FROM subagent_runs
    WHERE run_id = '<刚拿到的 runtime_run_id>'

查得到 ⇒ 同一命名空间，J2-3 成立。查不到 ⇒ **停下来**，不要把 J2-3 当成做完了，
把实测结果写进已知问题，改名（J2-1/J2-2）仍然可以合并。

⚠️ 这一条是通用前置「不出新卡、不调用任何付费模型」那条默认值的**明确例外**
（通用前置自己写了「正文明确写出理由和预算上限时以正文为准」）：
**一个最小 spawn，预算上限 $0.05，不出卡、不走 bin/biga-card。**
批 C-I 撞过反过来的坑——正文要求 live 补验，执行者拿通用前置那条默认值
把正文废掉了，见教程第 23 章「坑」一节。

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / live 那一条的
真实查询输出 / 你自己认为最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 E-II · 迁四个 Specialist（market/sector/technical/news）

⚠️ **批 E-I（`b970182`）与批 C-III（`b0155b4`，已通过 `7b3cdf8` 合并）都已落地才能开工**——
现在满足。E-I 试点只迁了 `emotion`（不参与 `CROSS_CHECK_PAIRS`、不读冻结快照的那个），
这一批第一次让**读冻结快照**（market/sector/technical）与**参与 CROSS_CHECK**
（market/technical）的 Specialist 走新形状——这两件事在 E-I 都没被真正测过。

🔴 **`risk` 不在这一批。** 它同样有 `stance`（`STANCE_VOCAB["risk"]` 里的
`"否决"` 就是 `VETO_STANCE`——制衡层最安全关键的那一位），但它还消费**其余五个**
的 verdict（`load_verdict` 逐个取）。等这一批把 market/sector/technical/news 都迁完、
形状稳定之后，`risk` 自己的迁移 + 退役 `amend_verdict.py`（前提是**全部**六个都迁完）
放在批 E-III——不要在这一批顺手带上，否则「最后一个迁移」与「退役」两件事的验收
标准会混在一起，出问题时分不清是迁移错了还是退役错了。

```text
把 market/sector/technical/news 四个 skill 从产 AgentVerdict 改成产 FactBundle，
形状与批 E-I 迁 emotion 时完全一样（build_verdict→build_fact_bundle，
save_verdict→save_fact_bundle）。先读 `docs/tutorial/27-facts-and-assessment.md`
——那是这个机械模式唯一的参照，不要重新发明。

## 要解决的两个悬而未决的设计问题（E-I 交下来的输入①②，本批必须给出答案）

### ①「Agent 追加的限制」缺失项归哪

老 `amend_verdict.py --add-missing` 唯一反复出现的真实用例（`amend_verdict.py`
自己的 help 示例、`SKILL.md`、教程第 12/27 章都引用同一个）是：

    --add-missing emotion.cycle.no_history "情绪周期趋势 —— 只有单日快照，无法判断方向"

🔴 **这不是判断，是 skill 自己就知道的事实**：「这次抓到几天的数据」是抓取过程
本身的输出，不需要等 Agent 事后指出。把它留给 Agent 用 `--add-missing` 补，
是在用一次额外的命令、一次额外的往返，去补一个 skill 一开始就该报的
`missing`——这正是批 E 想消灭的那类"事实靠人搬运"。

⇒ **裁定**：**四个 skill 里任何一处历史上靠 `--add-missing` 补过限制的地方，
改成 skill 自己在 `build_fact_bundle` 里检测并写进 `FactBundle.missing`**
（比如"实际抓到的 bars 数 < 判断趋势/周期所需的最小 bars 数"这类阈值判断，
具体阈值按每个 skill 自己的既有逃生阈值定，不要发明新概念）。
做法：`grep -n "amend_reason" data/biga.db`（或直接查
`SELECT amend_reason FROM agent_verdicts WHERE amend_reason LIKE '%add-missing%'`
风格的历史行——**先看真实数据库里到底出现过哪些**，不要只照抄上面这一个例子）
找出全部历史实例，每一条都要能对应到一个"skill 明知道、可以直接判断"的阈值。

⚠️ **如果找到一条真的不是"skill 明知道的阈值"、而是需要 Agent 主观判断的限制**
（目前没有实测证据表明存在，但如果这一批实际迁移时撞见了）——停下来，
不要强行塞进 skill，也不要自己在 `AgentAssessment` 加字段，回来找评审会话
重新确认设计（那是给 `AgentAssessment` 加新字段的事，影响面比这一批大）。

### ② 消费方要不要改成直接读 `AgentOutcome`

**裁定：不改，继续吃 `load_verdict` 的 `to_agent_verdict()` 兼容垫。**
`card_ops`/`risk_check`/`DecisionCard` 现在零改动就能同时消费新旧两种形状，
且已经被 E-I 的 P3（新旧并存）探针证明过。让消费方直接读 `AgentOutcome`
不会让任何**现在存在**的功能变得可能——没有消费方需要"事实与判断分开看"
这件事本身，提前做是 L-1。这一批**不要**顺手"顺便"把这层也收敛掉。

## 做什么

1. **四个 skill 改产 FactBundle**：`market_calc.py`/`sector_calc.py`/
   `technical_calc.py`/`news_scan.py` 各自的 `build_verdict`→`build_fact_bundle`，
   `save_verdict`→`save_fact_bundle`，返回类型 `AgentVerdict`→`FactBundle`。
   market/sector/technical 已经在读冻结快照、已经在填 `evidence_set_id`
   （批 D-II/E-I 留下的），这一批**不改**那部分逻辑，只改"产出的是哪个类型"。
2. **①的裁定落地**：按上面裁定，把历史上靠 `--add-missing` 补的限制改成这四个
   skill 里对应的机械判断，写进各自 `FactBundle.missing`。
3. **`amend_verdict.py` 认得这四个 skill 的 fact 行**：复用 E-I 已经写好的
   `_assess_fact()` 分支——**不需要新代码**，`kind == "fact"` 判据是通用的，
   E-I 迁 emotion 时就已经把这条路铺给所有未来试点用了。如果发现这一步
   还要改 `amend_verdict.py` 才能工作，说明 E-I 那条路没铺对，退回去看，
   不要在这一批重新发明一套。
4. **对应的四份 skill 测试**改成断言 `FactBundle`（参照 E-I 改
   `tests/test_emotion_calc.py` 的方式）。
5. **`--add-missing` 明确拒绝的分支**（E-I 在 `_assess_fact` 里写的那句
   「E-I 只支持给它加 --stance...留给批 E-II」提示语）——四个 skill 都迁完后，
   这句提示语对它们已经不成立（E-II 已经把限制挪进 skill 了），但**依然成立**
   的是"fact 行不接受 --add-missing/--add-warning"这条硬约束本身，只是不再
   有人会撞上它（因为该加的限制 skill 已经加了）。提示语的具体措辞要不要因此
   调整，你决定，但硬约束不能松。

## 不要做

- 不要迁 `risk`——批 E-III 的事（它还要消费这四个 + market 的产出，等这四个
  形状稳定、经过评审复核之后才安全动）
- 不要退役 `amend_verdict.py`——前提是全部六个都迁完，这一批只迁了五个（含
  E-I 的 emotion）
- 不要让消费方直接读 `AgentOutcome`——见上面②的裁定
- 不要碰 `risk_check.py` 的 `CROSS_CHECK_PAIRS` 判据逻辑本身——它已经在 E-I
  升级成优先比 `evidence_set_id`，这一批只是让 market/technical 产出的
  `Evidence` 从"AgentVerdict 里的"变成"FactBundle 里的"，`evidence_set_id`
  字段的填法不变，`CROSS_CHECK` 不应该感知到任何差异——这正是 P 探针要证明的

## 必须做的探针（G-1）

P1  四个 skill 各自跑一次，断言输出是合法的 `FactBundle`（不是 `AgentVerdict`），
    落库之后 `kind='fact'`
P2  🔴 CROSS_CHECK 穿透新形状：market 与 technical 都迁移之后，用同一个
    `evidence_set_id` 跑一次，断言 `cross_check_conflict == []`；再用不同的
    `evidence_set_id` 跑一次，断言报冲突且冲突信息里带着"冻结集"与两个
    es-id 值（复用 E-I 评审复核时锚死的那条判据，不要退化成只断言"报了冲突"）
P3  ①的裁定：对每一个历史上靠 `--add-missing` 补过限制的 skill，构造触发那个
    阈值的输入，断言 skill 自己（不经过 amend）就把对应的 `MissingItem` 放进了
    `FactBundle.missing`
P4  回归：`risk`（还没迁）与 `emotion`（E-I 已迁）在这四个迁移**之后**仍然正常
    工作——`risk` 读这四个新形状 + 自己走老路径产 `AgentVerdict`，`card_ops`
    照常聚合六个，一个都不丢（比照 E-I 的 P3，但换成这一批实际迁移的组合）
P5  `amend_verdict.py` 对这四个 skill 的 fact 行正确路由到 `_assess_fact()`，
    加 `--stance` 成功、加 `--add-missing` 被拒绝（复用 E-I 已有的判据，
    只是换四个新 agent 名字，证明它是通用的，不是 emotion 专属）

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 E-III · 迁 `risk` + 退役 `amend_verdict.py`（六个全迁完）

⚠️ **批 E-II（`359b97c`）已落地才能开工**——现在满足。这是 Facts/Assessment
拆分的最后一棒：`risk` 迁完之后六个 Specialist 全部产 `FactBundle`，
`amend_verdict.py` 的旧路径（操作合体 `AgentVerdict` 那部分）才能真正退役。

🔴 **`risk` 不是市场/板块/技术/新闻那种迁移——它的 `stance` 是
`VETO_STANCE`（"否决"），是制衡层唯一能拦住 BUY 的信号。** 前五个迁移里，
`stance` 判断错了后果是「这句话不准」；这一个判断错了后果是「一个真该被拦的
决策放行了」。这一批的探针清单因此比前面几批多一条不能省的：VETO 必须能从
`AgentAssessment` 一路穿透到 `DecisionCard` 检查它的那一层，不能只测到
"FactBundle 迁移成功"就停。

```text
把 risk 迁到 FactBundle（形状与前五个一致），然后退役 amend_verdict.py
里操作旧 AgentVerdict 的那部分代码（不是整个文件——`_assess_fact()`
那条 fact 行路径永久保留，六个 Specialist 以后都走它）。

## 先读

- `docs/tutorial/29-migrate-four-to-factbundle.md`——机械迁移那部分照抄
- 找到 `DecisionCard`/`card_ops` 里读 `VETO_STANCE` 或读 risk 的 `.stance`
  来决定是否拦截的那一处代码——这一批唯一的高风险点在那里，不在 risk_check.py
  本身的迁移（那部分和前五个一样机械）

## 做什么

1. **`risk_check.py` 改产 FactBundle**：`build_verdict→build_fact_bundle`、
   `save_verdict→save_fact_bundle`。risk 读其余五个 verdict 用的
   `load_verdict()`（多态）不用动——它已经对新旧形状都返回 AgentVerdict，
   risk 自己是消费方不是产出方那一半的形状问题跟这一批无关。
2. **`amend_verdict.py` 退役旧路径**：删掉操作合体 `AgentVerdict` 的那部分
   （`--add-missing`/`--add-warning` 对旧形状生效的分支、`--verdict` 覆盖
   逻辑、`dataclasses.replace(original, ...)` 那条 old-style 修订）。
   `_assess_fact()`（fact 行只加 `--stance`）**保留，不动**——六个 Specialist
   以后永远走这一条。
   🔴 **`save_verdict()` 不要删、也不要标记废弃**：`grep` 一下就知道
   `test_decision_id_ownership.py`/`test_orchestrator.py`/
   `test_readback_check.py`/`test_spawn_proof.py`/`test_verdict_provenance.py`/
   `test_write_boundary.py`/`tools/verify/phase1_acceptance.py` 都还在用它
   构造"老形状"的测试数据——这是证明 `LegacyAdapter` 读路径宽这件事**唯一**
   的手段，退役的是"活的 skill 还在写这个形状"，不是"这个形状不该再被测试到"。
3. **补 risk 的 AGENTS.md**：跟 E-II 给 market 做的一样——如果 risk 的
   AGENTS.md 里有类似"事后加缺失项/覆盖 verdict"的旧命令模板，同样删掉、
   caveat 走"需要注意"。**先查真实数据库**里 risk 历史上到底有没有用过
   `--add-missing`，不要假设它跟 market 同款（risk 的输出结构和市场类
   Specialist 不一样，别照搬结论）。
4. **VETO 穿透验证**（这一批的核心交付物，不是顺带）：找到消费 risk `.stance`
   来判断是否拦截的那处代码，确认它读到的 `.stance` 是 `load_verdict()`
   多态转换出来的那个字段，不需要改；如果发现它绕过 `load_verdict()`
   直接读别的东西（比如原始 verdict_json），停下来——那是这一批要修的
   真问题，不是顺手的事。
5. 迁移用到 amend_verdict.py 旧路径断言的测试文件（`test_verdict_refs.py`
   等，先 `grep -rln amend_verdict tests/` 拿到准确清单）——旧路径测试要么
   删、要么改成测 `_assess_fact()` 那条路径，不能留着测一个已经删除的分支。

## 不要做

- 不要删 `save_verdict()` 或它的任何调用点——见上面第 2 条
- 不要碰批 J（`run_id` 贯穿 / `agent_runs` 改名）正在动的
  `_store/schema.py`/`_store/db.py`/`orchestrator.py`——J-I/J-II 正在
  另一个会话里进行，发现要改这几个文件先停下来确认没有撞车
- 不要顺手把 `AgentOutcome` 暴露给 `card_ops`/`DecisionCard`——这仍然是
  E-II 定过的裁定（②：没有消费方，L-1），六个全迁完也不改变这个结论

## 必须做的探针（G-1）

P1  risk 产 `FactBundle`（不是 `AgentVerdict`），落库 `kind='fact'`
P2  🔴 **VETO 穿透**：构造一个 risk fact 行 + `AgentAssessment(stance=VETO_STANCE)`，
    走到 `DecisionCard` 实际检查拦截的那一层，断言它真的判定"拦截"——不是
    断言"stance 字段等于'否决'"就算过，要断到消费方真正用它做判断的地方
P3  旧路径确认退役：对一个 fact 行跑 `amend_verdict.py` 已删除的那些旧参数
    组合（如果还residual 存在），断言明确报错/找不到该分支，不是静默成功
P4  `_assess_fact()` 对 risk 的 fact 行正常工作（复用 E-II 已有判据，换成
    risk 这个 agent 名字，证明保留的路径没有因为这一批的改动被破坏）
P5  回归：`card_ops.load_verdicts_and_refs()` 对六个全新形状的 Specialist
    聚合不丢——这是 Facts/Assessment 拆分做完的最终验收，跟 E-I 的 P3、
    E-II 的 P4 是同一条判据的最后一次扩展（六个而不是五个/六个混一个旧的）

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 F · Risk 拆两层——硬规则挪进编排器，省掉注定白花的 LLM 调用

⚠️ **依赖已清**（2026-09-23）：E 系列（含 `risk` 迁到 FactBundle，`619d35e`）
与批 J-I（`005af7f`，`--run-id` capture）都已合并，`orchestrator.py` 现在没有
别的批次占着。

🔴 **这一批动的是 `risk` 的核心调用路径，不是它的判断逻辑。** `risk` 的
`stance` 是 `VETO_STANCE`（"否决"），是制衡层唯一能拦住 BUY 的信号——批 E-III
已经把 VETO 一路验穿。这一批**不改变** risk 会给出什么结论，只改变
「这份事实是谁算的、什么时候算的」。如果改完之后 VETO 穿透的验证有任何一处
不如 E-III 时那么硬，就是这一批出了问题，不是可以将就的细节。

```text
把 risk_check.py::build_fact_bundle() 的调用从"risk 被 spawn 之后自己在
LLM 会话里跑"挪到"编排器在决定要不要 spawn risk 之前，直接、免费地跑"。
两种确定性结论（证据跨决策污染 / 完全没有上游）不再 spawn risk；其余情况
仍然 spawn，但 risk 不再自己跑 risk_check.py，只负责解读编排器已经算好的
verdict_ref。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 F」小节
  （2026-09-23 设计探活，含设计方向 1-5、两处故意留白的核实点）——
  这是这一批的设计依据，不是背景资料，做之前完整读一遍
- `skills/risk-check/scripts/risk_check.py` 全文（414 行）——
  `build_fact_bundle()`（107-357 行）是纯函数，`main()`（364-410 行）只是
  给它套了一层 argparse + `save_fact_bundle()`；哪些字段触发提前 return
  （`foreign` 归属污染 / 无上游）看 107-195 行
- `skills/decision-card/scripts/orchestrator.py`：risk 的 spawn 调用在
  189 行，`_risk_task()`（给 risk 的提示词文本）在 312 行
- `agents/risk/AGENTS.md`：「四条硬约束」第 1 条——现在整段在教 risk
  怎么跑 `risk_check.py`，这一批做完之后大部分场景下它不会再跑这个脚本

## 做什么

1. **编排器直接 `import build_fact_bundle, save_fact_bundle`**（同一份函数，
   不新写一套），在原本 189 行 `ad.start(RISK_AGENT, ...)` 的位置之前，
   用已经拿到的 Stage 1 `verdict_ref`（186 行的 `s1_refs`）在本地算一遍，
   `run_id` 走 `ctx.run_id`（批 J-I 已经打通，跟 Stage 1 六个 skill 同一种
   capture 方式，不要发明第二种写法）。
2. **提前 return 的两种情况（`foreign`/无上游）⇒ 不 spawn risk**：编排器自己
   `save_fact_bundle()` 落库（不追加 assessment——设计探活已确认这不会被
   `absent_agents` 误判成"没跑"），直接拿这个 `verdict_ref` 参与合成，
   跳过 189 行那次 spawn。
3. **其余情况 ⇒ 仍然 spawn risk**，但编排器先把 `build_fact_bundle()` 的结果
   落库拿到 `verdict_ref`，`_risk_task()`（312 行）的提示词整个换掉：
   不再让它跑 `risk_check.py`，改成「事实已经算好，编号 verdict_ref=NN，
   不要重新跑 skill，读它、按判断表给 stance、跑 `amend_verdict.py --ref NN
   --stance <词>`」。
4. **`risk_check.py` 的 CLI 原样保留**——手工调试/人工复核仍然要能独立跑它。
   两条路径（CLI 手跑 / 编排器直接 import）共用同一个
   `build_fact_bundle`/`save_fact_bundle`，不要为编排器这条路径另写一份。
5. **改 `agents/risk/AGENTS.md` 的「约束 1」**：现在的写法（怎么跑
   `risk_check.py`、`--task-id` 不能省……）大部分场景下不再适用。改成新流程
   （拿到 verdict_ref、直接读、不要重新采集/不要重新计算）。**要不要保留一条
   「万一编排器没能算出来、risk 自己兜底跑一遍」的路径**，你自己判断并写清楚
   理由——这里不预先拍板，但**不管选哪种都要在探针里验一遍**（见 P4）。

## 不要做

- 不要碰批 K 会动的 `RISK_AGENT`/`STAGE2_AGENTS`/`SNAPSHOT_INDEX_AGENTS`
  这几个 orchestrator.py 里的常量本身——K 打算把它们改成从 `AGENT_REGISTRY`
  派生，这一批只用它们、不改它们的定义方式，避免两批在同一批文件里二次冲突
  （K 排在这一批之后开工）
- 不要改 `agents/risk/AGENTS.md` 里「判断口径」「什么时候该否决」「词表」
  那几节——那是 risk 的判断实质，这一批只改"事实从哪来"，不改"怎么判断"
- 不要给 `save_fact_bundle`/`build_fact_bundle` 加新参数或新分支去"兼容"
  编排器这条新调用路径——它们已经是纯函数，编排器应该跟 CLI 一样直接调用
  它们本来的签名，不需要为调用方是谁而分叉

## 必须做的探针（G-1）

P1  `foreign`（证据跨决策污染）场景：编排器不 spawn risk（断言 `ad.start`
    没有被以 `RISK_AGENT` 调用），FactBundle 正确落库、`verdict_ref` 正确
    参与合成，卡面上 risk 显示"给出事实、无 stance"而不是"缺席"
P2  无上游场景：同 P1，换成"完全没有 Stage 1 verdict_ref 可审"这个触发条件
P3  正常场景（覆盖齐备、无冲突）：risk 仍被 spawn，但新提示词生效——验证
    编排器预先落库的 `verdict_ref` 与最终卡上 risk 那条判定引用的是**同一
    个**，不是 risk 自己又产生了一条新的
P4  🔴 **边界情况必须真跑，不能只看代码**：模拟 risk 没听新提示词、自己又跑了
    一遍 `risk_check.py --verdict-ids ... --task-id <同一个 did>` 这个场景，
    断言 `save_fact_bundle` 对同一个 `(task_id, agent)` 第二次写 `fact` 行
    时的实际行为——报错要报得明确（不是裸 `IntegrityError`），不能静默
    覆盖或静默产生两条并存的判定原件
P5  **VETO 回归**（不是顺带，是这一批不能退步的底线）：构造一个 risk fact
    行 + `AgentAssessment(stance=VETO_STANCE)`，走到 `DecisionCard` 实际
    检查拦截的那一层，断言真的判定"拦截"——复用 E-III 的 P2，验的是"改完
    调用路径之后，VETO 穿透这条链一个环节都没被打断"

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 G-I · Outbox（Outbound Only）——只推送通知，不接受任何飞书输入

⚠️ **依赖已清**：批 E/J 系列都已合并，`_store/schema.py` 现在没有别的批次占着
（批 F 正在动 `orchestrator.py`/`card_ops.py`/`risk_check.py`，跟这一批要动的
文件基本不相交——`card_ops.persist()` 是唯一可能撞车的点，开工第一件事
`git log --oneline -5` 看批 F 有没有已经落地，落地了就以那份为准重新核对
行号，不要假设本提示词里的行号还准）。

🔴 **这一批只做"推"，不做"收"。** 不接受任何飞书方向的输入，没有新的攻击面，
不涉及 R-2。真正有风险的"飞书变成 trigger、绕开 main 自由判断"是下一批
（批 G-II，现在不写）的事，这一批只是给它准备好地基。

```text
给「Card 完成 / UNKNOWN / Risk BLOCK / 运行失败」这四类事件建一条推送通道：
新建 notification_outbox 表（与 Card 同一个事务写入），一个 worker 把它
投递出去（先接一个可替换的投递接口，不要求这一批真的打通飞书 API），
RunState 加 NOTIFICATION_PENDING。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 G」小节
  （2026-09-23 设计探活）——设计依据，做之前完整读一遍，尤其是
  "outbox 是真新表""异步执行是真新基础设施"这两条纠正
- `skills/_contract/run.py` 全文（50-170 行左右）——`RunState` 13 个状态、
  `RUN_STATES`/`TERMINAL_STATES` 怎么从类属性派生、`_ORCHESTRATED`/
  `_FAILURE`/`_INPUT_REQUIRED` 这几条转移表怎么拼成 `LEGAL_TRANSITIONS`。
  60-61 行就是 `NOTIFICATION_PENDING` 当初被推迟的那条注释
- `skills/_store/schema.py`：读 `_V7`（`evidence_sets`/`decision_runs`/
  `run_events` 三张表怎么建、只追加触发器怎么写）作为新表的样板，不要
  发明新的建表风格。看一眼 `decision_runs` 的 `trigger_id` 列（这一批不用
  它，但要知道它已经存在，将来批 G-II 会接上，命名上不要撞车）
- `skills/decision-card/scripts/card_ops.py::persist()`——Card 落库的地方，
  新的 outbox 写入要在**同一个 `connect()` 事务**里，不是两次独立提交
- `skills/_store/db.py` 里任何一个 `save_*` 函数的写边界重校验写法
  （规范序列化 → 严格重建 → 校验 → INSERT）——新表的写入函数照这个模式写，
  不要跳过重校验这一步

## 做什么

1. **schema 新迁移**（当前 `SCHEMA_VERSION` 见 `schema.py` 尾部，用下一个
   号）：`notification_outbox` 表。至少要有 `event_type` / `aggregate`
   （建议就是 `decision_id`）/ `payload_json` / `created_at`，`UNIQUE
   (event_type, aggregate)` 做幂等键——同一个决策的同一类事件只能入队一次。
   🔴 **一个没有预先答案、需要你自己判断并写清楚理由的设计问题**：这张表
   要不要跟其余 8 张表一样是纯只追加（投递状态另开一张
   `notification_deliveries`/类似的追加日志表记录"第几次投递、结果如何"，
   "有没有投递成功"变成一条派生查询），还是给 `notification_outbox` 本身
   开一个例外、允许 `delivered_at` 这一列被 UPDATE 一次。两条路都要在
   `CHANGELOG.md` 写清楚为什么选这条、跟本仓库现有的"revision 链用
   `amends` 而不是原地改"（`agent_verdicts`）这个先例是不是一致。
2. **`RunState` 加 `NOTIFICATION_PENDING`**，插在 `CARD_PERSISTED` 与
   `COMPLETED` 之间：`CARD_PERSISTED → NOTIFICATION_PENDING → COMPLETED`。
   🔴 这个状态只代表"outbox 行已入队"，**不代表"已经投递成功"**——
   worker 投递是异步的，不能让一次 Feishu API 抽风把 Run 卡在非终态。
   入队这一步必须快（跟其余转移一样是纯 DB 操作），不要在这条转移里等
   worker 真的把消息发出去。
3. **`orchestrator.py` 里 `CARD_PERSISTED` 之后的那次转移**（批 F 可能已经
   在动这附近的代码，先 `git log`/`git blame` 确认现状）：Card 落库的同一个
   事务里，同时写一行 `notification_outbox`（`event_type` 从 Card 的
   `status`/`missing` 推出来——`BUY`/`WAIT` 之类正常收尾算
   `card_completed`，`status` 里能看出 risk 否决的算 `risk_block`，`missing`
   非空到某个程度或 `verdict=UNKNOWN` 的算 `card_unknown`；`FAILED`/
   `TIMEOUT`/`CANCELLED` 这几个终态各自的失败通知在各自转移那里写，不要
   全塞进 `CARD_PERSISTED` 那一步——那几个终态压根不会经过它）。
4. **worker**：一个独立的、可以单独跑的脚本（放 `tools/` 还是 `skills/`
   你自己判断，参照现有类似脚本的位置），扫 `notification_outbox` 里没投递
   成功的行，调用一个**投递接口**（先做成一个可替换/可 mock 的抽象——
   这一批不需要真的接通飞书 API，接口先对接一个"打印到 stdout"或类似的
   桩实现，真正的飞书 adapter 是批 G-II 才有生产方）。
5. **`bin/biga-card` 现在完全同步**（`wait "$_ORCH_PID"`），这一批**不改**
   这个同步模型——`NOTIFICATION_PENDING` 是运行内部的一个转移，不是
   "先 ACK 用户、后台继续跑"的那个异步（那是批 G-II 的核心工作）。
   这一批的 worker 是在 Card 已经写完之后另起一个进程/cron 去补投递，
   跟 `bin/biga-card` 本身的调用者体验完全无关。

## 不要做

- 不要碰 `entry_guard.py`、根 `AGENTS.md`、`main` 的 `allowAgents`——
  "飞书触发绕开 main 自由判断"是批 G-II 的核心交付物，这一批不涉及任何
  入站的东西
- 不要建 `deploy/openclaw/` 或任何 `agents.yaml`/`tool-policy.yaml`/
  `apply_config.py`——那是批 G-II，跟这一批没有依赖关系但不要顺手做
- 不要给 `RUN_ORIGINS` 加新值或改它的生产方——这一批不产生任何
  `origin="feishu"` 的 run，通知是"任何 run 完成后都推"，跟 run 从哪来无关
- 不要真的去接通真实飞书 API——这一批的验收不依赖真实投递成功，见下面
  G-1 探针 P4 的范围
- 不要把 `notification_outbox` 的幂等键设计成需要外部系统配合才能生效的
  形状——`(event_type, aggregate)` 必须在 BigA 自己的库里就能保证幂等，
  不能依赖"飞书那边也会去重"

## 必须做的探针（G-1）

P1  同事务：构造一个会在写 `notification_outbox` 那步失败的场景（比如
    `event_type` 传非法值），断言 Card **也不会**被落库——两者要么一起
    成功要么一起失败，不能有 Card 进去了、通知没进去的中间状态
P2  幂等：同一个 `(event_type, aggregate)` 入队两次，断言只有一行生效
    （或者第二次入队被拒/被合并——具体行为你定，但要有一条测试钉住
    "不会造成同一个通知被投递两次"）
P3  🔴 只追加：对 `notification_outbox`（以及如果你新开了投递日志表）真跑
    UPDATE/DELETE，断言被拒——跟这仓库其余 8 张表同一个判据，不能因为是
    新表就漏掉这一条
P4  worker 投递：四类事件（`card_completed`/`card_unknown`/`risk_block`/
    运行失败）**各自**至少构造一个场景，断言 worker 真的调用了投递接口、
    传的 payload 里有对应决策号——用 mock/桩接口验证，不要求真实调用飞书
    API 成功
P5  `NOTIFICATION_PENDING` 转移的合法性：`LEGAL_TRANSITIONS` 更新之后，
    `test_run_transitions`（或同类测试）要能证明"从 `CARD_PERSISTED` 跳过
    `NOTIFICATION_PENDING` 直接到 `COMPLETED`"现在是非法转移（如果你的
    设计确实要求必须经过这个状态），以及"worker 还没投递成功"不会让 Run
    卡在非终态——`COMPLETED` 的达成不能依赖 outbox 投递的结果

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 G-II · Inbound Trigger —— 飞书出卡变成结构化事件，main 全程不参与路由

⚠️ **依赖已清**：批 G-I 已合并（`0484e2e`），`notification_outbox`/
`notification_deliveries`（schema v12）与 `RunState.NOTIFICATION_PENDING`
状态转移都已定型，`notify_worker.py` 的 `Deliverer` 接口与桩
`StdoutDeliverer` 已经在跑。开工第一件事 `git log --oneline -5` 确认没有
更晚的合并改过这几处，不要假设本提示词里的行号还准。

🔴 **这是批 G 里真正有攻击面、真正动"钱怎么被花掉"这条链入口的一批，
风险与批 E-III/J-II 同一量级**——上两批最终都是靠一次小额真实验证才把
一个关键假设彻底澄清（J-II 是命名空间共享，E-III 是迁移路径），这一批
大概率也需要类似的收尾，正文最后一条探针已经把这个预算留出来了。

```text
把飞书"出卡"从一次自由对话，变成一个结构化的、main 的 LLM 全程不参与
路由决策的 trigger：真实接上 decision_runs.trigger_id 做入站幂等键，
给 bin/biga-card 发明一条异步执行路径（快速 ACK + 后台完成，因为
170-200s 没法在一次 webhook 响应里同步等完），把 notify_worker.py 的
Deliverer 接口接上真实飞书 API，并且从结构上让 main 压根看不到这类
请求——不是"教它认出来再转发"。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 G」小节全文，
  尤其"结构性修法"那一段（2026-09-21 两次事故的根因分析：19:31 的 4 个
  孤儿 spawn 与 21:03 的出卡递归 L-14，根子都是"入口在 prompt 层面，不在
  代码层面"）和"六个指纹只有一个是真的"那一段——六个指纹里五个没有
  任何消费方，这一批不做它们
- `skills/decision-card/scripts/entry_guard.py` 全文，尤其
  `classify_caller()`（107 行起）——它挡的是"入口脚本被递归调用"（L-14），
  这一批要加的是新的一条防线（"飞书事件根本不给 main 机会去调用
  `sessions_spawn`"），是新增路径，不是改这条已有护栏，先弄清楚两者的
  边界在哪
- `bin/biga-card` 全文，尤其 247-263 行的 `_ORCH_PID`/`_reap_orch`/`wait`
  模式——这是"今天完全同步"的证据，也是这一批要新建异步路径时，人工
  CLI 调用**不能被破坏**的那个基线行为
- `skills/_contract/run.py` 的 `RUN_ORIGINS`（120-125 行左右，`feishu`
  值已经预留但零生产方）与 `skills/_store/schema.py` 里 `decision_runs`
  的 `trigger_id` 列（`_V7` 附近，建表注释原话是"一次外部请求的幂等键"，
  同样零生产方）——这一批是这两处**第一次真正有人写它们**
- `skills/decision-card/scripts/notify_worker.py` 全文——`Deliverer`
  协议与桩 `StdoutDeliverer`，这一批要接的就是这个接口，不要绕开它
  另起一套投递逻辑
- `skills/_contract/verdict_ref.py` 的 `CONTRACT_VERSION`（41 行，
  `"contract/1"`）——六个指纹里唯一是真的那个，粒度是每条 `VerdictRef`，
  不是一条独立的按次运行记录，这一批不用把它拔高成后者
- 如果 `docs/external/` 下能找到飞书最佳实践材料，通读它 §17（两种工作
  模式彻底分开）与 §25（Outbound Only → Preflight → Inbound Trigger →
  Question Bridge 的分阶段建议）——本批只做第三阶段，其余明确不做（见下）

## 做什么

1. **`deploy/openclaw/`（未建）+ `apply_config.py`**：`agents.yaml` /
   `tool-policy.yaml` / `profile.template.json` / `apply_config.py`。
   🔴 撞 R-2：必须走 `bin/biga`，绝不允许写出按默认名推导的 systemd
   单元名。判据机关已经在——`tools/verify/isolation.py::
   check_namespaces()` 已经实现"systemd 单元名必须带 `-biga`"这条，
   `bin/biga` 已经强制 `--profile biga`——这一批是让 `apply_config.py`
   走这条已有的路，不是新发明一套 R-2 检查。
2. **异步执行模型**：`bin/biga-card` 今天全程 `wait "$_ORCH_PID"`，飞书
   inbound 这条路径不能这样等（webhook 通常有秒级 ACK 期限）。需要
   "快速 ACK + 后台完成"——具体是新脚本、新 CLI flag，还是别的形状，
   你自己判断，但**人工 CLI 与飞书 inbound 最终必须走到同一个
   orchestrator**，不要分叉出两套决策逻辑，且人工 CLI 现有的同步体验
   不能被这一批改坏。
3. **Inbound trigger 端点/adapter**：真正把 `origin="feishu"` 填上
   （`RUN_ORIGINS` 已预留），`decision_runs.trigger_id` 接上真实飞书
   event id 做幂等键——同一个 event id 重投必须被识别为重复，不能重跑
   一次决策（2026-09-21 19:31 事故的直接解法）。
4. **`main` 结构性移出这条路径的路由决策**：飞书"出卡"事件必须直接从
   gateway/adapter 层调用 `bin/biga-card` 等价的入口，`main` 的 LLM
   全程不参与——不是"教 main 认出这是出卡请求再转发"，是"main 压根不会
   看到这类请求"。这一立场比外部材料 §18 自己推荐的拓扑更保守，但与
   批 C-II 已经采用的立场一致，不是新裁定。
5. **`tools.deny: [ask_user]`**：给自动出卡的 agent 配上，并**补一条
   测试**证明 `main` 的正常飞书/TUI 交互不受影响——`stall_watchdog.py`
   的注释记录过半年前这条被否决的历史（那时 `main` 同时是交互入口和
   出卡入口），批 C-II 之后前提已经不成立，但不能只凭推理认为安全。
6. **`notify_worker.py` 的 `Deliverer` 接口接上真实飞书 API**——这正是
   它当初被设计成"可替换接口"而不是写死 `StdoutDeliverer` 的原因。

## 不要做

- 不做 Preflight Interaction（外部材料的阶段二）与 Question Bridge
  （阶段四）——设计文档明确推迟：阶段四"只有出现真实需求后才实现"，
  阶段二在没有阶段三（本批）之前没有意义
- 不建 `contract_version` 之外的其余五个指纹字段（`git_commit` /
  `openclaw_version` / `agent_config_hash` / `tool_policy_hash` /
  `prompt_hash`）——零消费方（L-1），留给以后真正需要审计这些维度的
  时候再建
- 不改 `entry_guard.py::classify_caller()` 已有的护栏逻辑本身——这一批
  加的是新增路径（飞书事件不经过 `main`），不是修改它已经在拦的那条
  （入口脚本被递归调用）。如果你发现两者确实必须合并，在正文写清楚
  为什么，不要默默改掉一条已经在生产上生效的判据
- 不要把异步执行模型做成"只对飞书生效"的特例——人工 CLI 出卡
  （`bin/biga-card` 当前的同步体验）不能被这一批改坏，两种调用方式
  都要有测试覆盖
- 不要在这一批实现真实飞书 API 的完整功能面（消息卡片富文本格式、
  按钮交互等）——探针只需要证明"收到一个飞书 event → 幂等去重 → 不
  经过 main → 触发 bin/biga-card 等价路径 → 产出 Card → 通知投出去"
  这条链闭环，不是把飞书生态全量对接完

## 必须做的探针（G-1）

P1  幂等：同一个 `trigger_id`（模拟同一个飞书 event id）重投两次，断言
    只触发一次决策/一次 orchestrator 调用，第二次被识别为重复而不是
    重新走一遍
P2  🔴 main 不参与路由：构造一次飞书出卡事件，证明这条路径不依赖 main
    的 LLM 判断——具体怎么证明你自己定，但要经得起"如果把 main 的
    system prompt 整个清空，这条路径还能不能正常出卡"这个反事实检验
P3  异步：飞书 inbound 调用必须在一个短时间窗口内返回 ACK，不能等
    170-200s 的 orchestrator 跑完；同时验证人工 CLI 同步调用
    （`bin/biga-card`）的现有行为没有被破坏
P4  R-2：`apply_config.py` 装前装后跑 `isolation.py`，断言 systemd 单元
    名带 `-biga` 后缀；试着让它写一个不带 `-biga` 后缀的单元名，断言
    被拒
P5  `tools.deny: [ask_user]`：证明自动出卡路径配了这条之后，main 的
    正常飞书/TUI 交互（非出卡类请求）确实不受影响——这是设计文档明确
    要求补的那条测试，不能只凭推理
P6  🔴 一处必须 live 验（参照 J-II 与批 C-I 的先例，线下 mock 无法替代）：
    至少一次真实（或最接近真实、确认无法再线下模拟的）飞书 event 走
    完全链路到 Card 产出与通知投递成功。预算参照 J-II 的量级，不要为了
    这条探针反复出真卡——一次讲清楚就够

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 K · Pipeline Registry + Agent Registry —— roster 从五处收成一处

⚠️ **依赖已清**：批 F 已合并（`e5b959f`），`orchestrator.py` 里
`RISK_AGENT`（67 行）/`SNAPSHOT_INDEX_AGENTS`（82 行）这两处这一批要收编
的独立字面量已经定型，没有更晚的改动。开工第一件事 `git log --oneline -5`
确认这一点，不要假设本提示词里的行号还准。

这一批不是新增能力，是**收编**：2026-09-21 `news` 没被 spawn 那次事故
（进了契约名单、agent 建好了，但 `allowAgents` 白名单漏了它，Card 照常
出、只是少一个领域，没有任何报错）现在只靠 `test_roster_matches_config.py`
做数据驱动对账兜底——对账不是单一源。这一批的设计探活（2026-09-23，见
设计 SSOT 同名小节）普查出 roster 实际上散在**五处**，其中 `tools/verify/
adapter_spike.py` 那处零测试覆盖、`test_roster_matches_config.py` 本身还
有个 `skipif` 静默跳过的口子——都比原始事故描述更细，做之前完整读一遍
普查结论，不要重新普查一遍。

```text
把 STAGE1_AGENTS/STAGE2_AGENTS/RISK_AGENT/SNAPSHOT_INDEX_AGENTS 这几个
散落的独立字面量，改成从一份新的 AGENT_REGISTRY 派生；给 Card 加一个
生成时冻结的期望 roster 字段，让 absent_agents 不再"现算现取今天的
Registry"。只做 Pipeline + Agent 两个 Registry，不做 Dataset/Provider。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 K」小节全文
  （设计探活 + 设计方向，2026-09-23）——roster 五处普查结论、"Pipeline
  版本化"为什么要拆成两半、`test_roster_matches_config.py` 的 `skipif`
  缺口，都已经写清楚，不要重新调查一遍
- `skills/_contract/verdict.py` 全文，尤其 `STAGE1_AGENTS`/
  `STAGE2_AGENTS`（93-94 行）与 `STANCE_VOCAB`（113 行起）——`discipline`
  在 `STAGE2_AGENTS` 里但**故意不在** `STANCE_VOCAB` 的 key 集里（裁定 13：
  它从不被 spawn），Registry 引入之后这条区分**必须继续成立**
- `skills/_contract/run.py` 的 `RunState`——`RUN_STATES`/`TERMINAL_STATES`
  怎么用 `vars()`/内省从类属性派生 `frozenset`，不手抄第二份清单。K 要对
  `AGENT_REGISTRY` 做同样的事，照抄这个已验证过的形状，不发明新写法
- `skills/decision-card/scripts/orchestrator.py` 的 `RISK_AGENT`（67 行）/
  `SNAPSHOT_INDEX_AGENTS`（82 行）两处独立字面量，以及它们各自的用法
  （214/223/296/355 行附近）——这是这一批要收编的两处
- `skills/_contract/card.py` 的 `absent_agents`（362 行起，`@property`）
  ——现算现取今天的 `STANCE_VOCAB` key 集的证据，这一批要给它加一条
  "生成时冻结优先"的读取路径
- `tools/verify/adapter_spike.py` 的 `STAGE1`（41 行）——独立字面量，
  只 import `_runtime`、不 import `_contract`，不在 pytest 下跑，
  `test_roster_matches_config.py` 完全看不到它，是普查找到的第三处漂移
  风险
- `tests/test_roster_matches_config.py` 的 `skipif`（84 行，
  `not CONFIG.exists()`）——本机没有运行时配置时**不报红，直接跳过**，
  是否在这一批一并修，由你自己判断范围（见下）

## 做什么

1. **`AGENT_REGISTRY`**（`_contract` 新增）：一个 agent 一条
   `AgentDefinition`（`agent_id` / `stage` / 是否真的会被 spawn / 是否
   消费 `SnapshotCoordinator` 冻结的快照）。只装**已经在生产路径上有
   消费方**的字段——不装外部材料示意稿里 `required_datasets` 之类的
   字段（绑定 Dataset Registry，裁定表已明确推迟，装了就是 L-1 的死
   配置）。
2. `STAGE1_AGENTS` / `STAGE2_AGENTS` / `RISK_AGENT` / `SNAPSHOT_INDEX_AGENTS`
   全部改成从 `AGENT_REGISTRY` 派生的模块级常量，不再手写字面量。
   `STANCE_VOCAB` 的 key 集**保持独立**，但改成对 Registry 断言子集
   关系——不能因为 Registry 存在就把 `discipline` 意外带回
   `absent_agents` 的权威里。
3. **`tools/verify/adapter_spike.py` 迁到 Registry**——不是顺手，是这一批
   "是否还有消费方在读独立字面量"验收判据之一，普查已经点名这一处。
4. **卡级冻结名单**：`orchestrator.py` 构建 Card 时，把当时 `AGENT_REGISTRY`
   算出的期望 roster 写进 `card_json`（新字段，回放路径照旧不重算）；
   `absent_agents` 优先读这个冻结字段，只有老卡（字段不存在）才回退到
   读**今天**的 Registry——`E-I` 的 `LegacyAdapter`、`J-II` 的
   `spawn_check` 结构化 join 都是这个"有就用、缺就退回"模式，不发明
   第四种写法。
5. `test_roster_matches_config.py` 的 `skipif` 缺口——修不修、修成什么样
   （比如改成 R-3 式的"报 UNKNOWN/missing"而不是静默跳过）你自己判断
   范围，判断结果写进 CHANGELOG，不要不声不响地扩大或缩小这一批的范围。
6. 明确不做：不生成/不写 `~/.openclaw-biga/openclaw.json`——Registry
   最多提供"照 Registry 应该长什么样的 patch 建议"给人工核对着跑
   `biga config patch`（R-1：仍然只走 `bin/biga`）。

## 不要做

- 不建 Dataset Registry / Provider Registry——裁定表已明确只做 Pipeline
  + Agent 两个，其余四个（见总体设计 §34 的六个建议）没有已证明的消费方
- 不要让 `discipline` 因为 Registry 的引入而意外出现在 `absent_agents`
  的权威集合里——它在 `STAGE2_AGENTS` 但故意不在 `STANCE_VOCAB`，这条
  区分是裁定 13 的直接后果，不是疏漏
- 不要给老卡（`card_json` 里没有冻结 roster 字段的历史卡）回填这个新
  字段——raw/历史记录永不改写的先例（L-8）同样适用在这里，老卡就是没有
  这个字段，靠"缺就回退到读当下 Registry"兜底，不是靠事后写入补齐
- 不要顺手把 `apply_config.py`/`deploy/openclaw/`（那是批 G-II 的范围）
  或 Dataset/Provider Registry 的雏形也做了——想到了记进 TODO.md

## 必须做的探针（G-1）

P1  派生一致性：`AGENT_REGISTRY` 派生出的 `STAGE1_AGENTS`/`STAGE2_AGENTS`/
    `RISK_AGENT`/`SNAPSHOT_INDEX_AGENTS`，在当前 roster 下必须与重构前的
    手写字面量逐项相等——这条验证的是"重构没有改变行为"，不是新增能力
P2  🔴 冻结名单：构造两张卡，中间人为改变 `AGENT_REGISTRY`（比如临时加一个
    agent），断言先生成的那张卡的 `absent_agents` 读到的还是**生成时**
    冻结的名单，不随 Registry 后续变化而变化——这正是要堵的"同一张历史卡
    在不同时间点给出不同答案"那个静默漂移，普查里说过还没有实例发生过，
    但机制上成立
P3  老卡兼容：构造一张没有冻结字段的老卡（模拟老 `card_json`），
    `absent_agents` 正确回退到读当下 Registry，不报错、不炸
P4  `adapter_spike` 迁移：改 `AGENT_REGISTRY` 里增删一个 Stage 1 agent，
    断言 `adapter_spike.py` 的 `STAGE1` 跟着变——不再是独立字面量
P5  `STANCE_VOCAB` 边界：断言 Registry 存在之后，即使 `AGENT_REGISTRY`
    给 `discipline` 也建了一条 `AgentDefinition`（`STAGE2_AGENTS` 本来就
    含它），`discipline` 依然没有出现在 `absent_agents` 的权威集合里
P6  如果这一批决定顺手修 `skipif` 缺口：模拟"本机没有运行时配置"的场景，
    断言不再是静默 skip，而是给出一个显式的、R-3 式的信号

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 I · RawArtifact —— raw 层第一次真正存 raw

⚠️ **依赖已清**：批 F 已合并（`e5b959f`），raw 溯源字段该指向哪个 `run_id`
已有确切答案（`ctx.run_id`，见下文）；`architecture.md` §5.1 已按四个
存储平面重写，本批不再需要为"这算不算撞了切 PostgreSQL 的触发条件"
纠结。

🔴 **开工第一步、不是可选项：先建独立 worktree，不要直接在共享工作区
`~/.openclaw-biga/workspace` 里改。** 2026-09-23 核对时确认批 G-II 正在
（很可能还有别的批次也在）**直接在共享工作区里**改
`skills/_store/schema.py`/`skills/_store/db.py`——这两个文件本批也要改
（新迁移、`save_raw_snapshot`/`load_raw_snapshot`），是真实的、当下正在
发生的文件级冲突，不是预防性建议。做法照 C-III/J-I/J-II/G-I 的既定
先例：

```bash
git worktree add .claude/worktrees/i -b i orchestration   # 基于最后一次干净提交，不含其他会话的未提交改动
cd .claude/worktrees/i
```

在这个独立 worktree 里开工、测试、提交，**不要碰共享工作区里那些不属于
本批的未提交改动**。做完之后（评审通过后）参照既定流程：先把
`orchestration` 合并进这个 worktree 解冲突（大概率是 schema 版本号
撞车——本批的新迁移与批 G-II/其他并行批次谁先落地谁保留编号，后落地的
重新编号，迁移 SQL 本体不动），验绿之后再合回 `orchestration`。

开工后 `git log --oneline -5` 确认没有更晚的改动，不要假设本提示词里的
行号还准。

🔴 **这一批的核心风险不是"改不动"，是"改完之后静默破坏一个现有消费方"**
——设计探活已经找到那个消费方并给出了必须遵守的边界（见下文"不要做"第
一条），做之前完整读一遍，不要重新排查一遍。

```text
raw 层现在存的不是 raw：HTTP 响应体在 get_json() 里被 json.loads 解析后，
原始文本就地丢弃；六个 collector 攒的都是解析后的对象；落盘时又用
json.dumps(sort_keys=True) 重新序列化一遍。content_sha256 因此是"我们
自己重排后"的指纹，证明不了任何关于数据源原始字节的事。这一批要让
原始文本真正被保留下来，且不能改变现有消费方已经在依赖的"payload 是
解析后对象"这个语义。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 I」小节全文（设计
  探活，2026-09-23）——完整链路怎么走、原始文本具体在哪一步丢失、
  `coordinator.py` 那个会被静默破坏的消费方，都已经查清楚并写明必须
  遵守的边界，不要重新排查一遍
- `skills/_sources/http.py` 全文，尤其 `get_text()`（48 行起，HTTP body
  → str，按 `encoding` 参数解码）与 `get_json()`（90 行起，`json.loads`
  之后原始文本没有被保留到任何地方）
- `skills/_store/db.py` 的 `payload_sha256()`（1081 行起）/
  `save_raw_snapshot()`（1105 行起）/`load_raw_snapshot()`（1140 行起）
  ——现状：存的和哈希的都是解析后再重新 `json.dumps` 的对象
- 🔴 `skills/_snapshot/coordinator.py` 191-198 行——`load_raw_snapshot()`
  之后 `raw = snap["payload"]` 被当**已解析对象**用（`len(raw)`、切片）。
  这是这一批最容易踩的坑，做之前务必确认自己理解了它为什么不能被改坏
- `skills/_contract/evidence.py` 的 `raw_hash` 字段（48/65 行附近）——
  它的文档写着指向 `raw_market_snapshot.content_sha256`，这一批会改变
  这一列的计算依据
- `skills/_store/schema.py` 的 `raw_market_snapshot` 建表语句（`_V1`）
  与它的建表注释——"这里存的是当时从数据源拿到的字节，不做任何归一化"
  这句话现在是假的（L-3：注释断言了一件没发生的事），这一批要么让它
  变成真话，要么改措辞说清楚哪一列才是真正未归一化的
- 六个调用 `save_raw_snapshot` 的文件（`market_calc.py`/`emotion_calc.py`/
  `technical_calc.py`/`sector_calc.py`/`news_scan.py`/
  `_snapshot/coordinator.py`）——各自怎么把 `get_json()` 的返回值攒进
  `c.raw: list[(source, payload, src_as_of)]`，这一批都要跟着改

## 做什么

1. **让 `get_json()`/`get_text()` 能同时交出原始文本**——不改变现有六个
   collector 目前"拿到解析后对象"的用法，但让原始文本不再在 `get_json()`
   内部就地丢弃。具体形状（返回二元组、新增可选参数、还是新增一个姊妹
   函数）你自己判断。
2. **六个 collector 的 `c.raw` 累积路径要连带原始文本**——目前是
   `list[(source, payload, src_as_of)]`，原始文本需要一并带到
   `save_raw_snapshot` 调用点。
3. **schema 新增字段，不是替换 `payload_json`**：`raw_market_snapshot`
   加一列存原始文本（列名你定，比如 `raw_text`）。新迁移号见 `schema.py`
   尾部 `MIGRATIONS`，用下一个号，不要硬编码猜测的版本号（这个数字最近
   连续撞车两次）。`content_sha256` 改成基于这个新列计算。
4. **`save_raw_snapshot`/`load_raw_snapshot` 签名跟着变**，但
   🔴 **`load_raw_snapshot()` 返回的 `payload` 字段必须继续是解析后的
   对象**——`coordinator.py` 的 `len(raw)`/切片用法不能变成对字符串操作。
5. 六个调用方改造：`market_calc.py`/`emotion_calc.py`/`technical_calc.py`/
   `sector_calc.py`/`news_scan.py`/`_snapshot/coordinator.py`。
6. **`schema.py` 建表注释改回真话**——现在断言"不做任何归一化"，加完
   新列之后要么让它变成真话（点名哪一列是真正未归一化的原始文本），
   要么改措辞讲清楚 `payload_json` 是解析后的规范表示、新列才是原始的。
7. `skills/_contract/evidence.py` 的 `raw_hash` 文档补一句：这一列的
   计算依据在某个 schema 版本之后变了（新行如此，旧行不受影响，raw 层
   只追加，不需要迁移旧行）。

## 不要做

- 🔴 不要改变 `load_raw_snapshot()` 返回的 `payload` 现有语义（解析后
  对象）——这是本批最容易踩的坑，`coordinator.py` 会静默读错而不报错
- 不要解决 `get_text()` 的编码假设问题（比如某数据源实际是 GBK 而不是
  utf-8）——这是设计探活里明确排除在范围外的独立问题，除非你核实后
  发现现用数据源确实编码错了，那就在正文里写清楚为什么这一批也要顺手
  解决
- 不要对旧的 `raw_market_snapshot` 行做任何回填/迁移——raw 层只追加，
  旧行的 `content_sha256` 语义就是旧语义，不用改
- 不要顺手把 `Evidence.raw_hash` 从「可选」改成「必填」——那是另一个
  决定，不在这一批范围
- 不要碰批 K 正在动的 roster 相关常量，也不要碰批 G-II 的异步执行/
  飞书路径——三批文件基本不相交，如果 `git log` 发现有交叉，先确认
  谁先落地，不要假设

## 必须做的探针（G-1）

P1  🔴 原始性：构造一个响应体（比如带乱序 key、多余空白的 JSON 文本），
    走完整链路存进去，断言存下来的原始文本与最初的响应体**逐字节相同**
    ——不是"看起来一样"，是真的按字节比较
P2  hash 正确性：同一段原始文本，新的 `content_sha256` 与手算的 sha256
    一致；且两次采到**内容相同但序列化不同**（key 顺序不同）的响应体，
    产出的 `content_sha256` 不同——这是要修的问题的反面验证：改之前
    这两次会被判成"一样"，改之后能分辨
P3  🔴 现有消费方不被破坏：`coordinator.py` 的 `load_raw_snapshot` 消费
    路径（`len(raw)`/切片）现有测试全绿，且新增一条测试显式断言
    `payload` 字段返回的还是解析后的对象，不是字符串
P4  六个调用方改完之后，各自现有测试套件全绿——不能有一个漏改导致该
    collector 往新列里存了个 `None` 或空字符串却不报错
P5  只追加：新列所在的表仍然只追加，UPDATE/DELETE 断言被拒——确认新列
    没有意外绕开 `raw_market_snapshot` 已有的触发器
P6  建表注释真实性：取一条真实存的行，比对它的原始文本列与构造的原始
    响应体逐字节相同，用这条测试证明"不做任何归一化"这句注释现在是
    真话，不是又一句断言了没发生的事

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 L · `cn.trading_calendar` —— Provider→Raw→Normalize→Quality→Snapshot 第二遍，第一次真的建 `fact_*` 表

⚠️ **依赖已清**：批 I 已合并（`c082205`），`raw_text` / `content_sha256`
（基于原始响应文本）的形状有真实先例可抄；`architecture.md` §5.1 已按四个
存储平面重写，本批不需要为"这算不算撞了切 PostgreSQL 的触发条件"纠结。

🔴 **开工第一步、不是可选项：先建独立 worktree。** 做法照 C-III/J-I/J-II/
G-I/I 的既定先例：

```bash
git worktree add .claude/worktrees/l -b l orchestration   # 基于最后一次干净提交
cd .claude/worktrees/l
```

开工后 `git log --oneline -5` 确认没有更晚的改动，不要假设本提示词里的
行号还准。本批要改的文件（`skills/_sources/` 下新模块、`skills/_store/
schema.py`、`skills/_sources/tradetime.py`）与批 G-II 那个仍在另一个
worktree 里进行的修复（`skills/card/`、`deploy/openclaw/`）不相交，
但 `schema.py` 的下一个迁移号是**所有并行批次共享的单点**——开工时读一遍
`MIGRATIONS` 列表尾部取当时的下一个号，不要硬编码，这个数字最近连续
撞车两次（v9/v10、v13/v14）。

```text
market_is_open() 现在认周末不认节假日（tradetime.py 53 行起，文档字符串
已写明「已知边界」与朝安全方向偏的理由——节假日返回 True 只会多报 missing，
不会让 Card 更激进）。今天没有任何交易日历数据。这是数据架构材料 §45 点名的
六个市场数据集里，今天唯一已经证明有生产消费方（news-scan/emotion-calc）
且体量小、免鉴权能拿到的一个，定位是给那一批打样：把 Provider → Raw →
Normalize → Quality → Snapshot 这条链在一个非行情数据集上走第二遍
（第一遍是已完成的 index_daily）。

## 先读

- `docs/design/deterministic-orchestration.md` 的「批 L」小节全文（含
  2026-09-23 补的设计探活）——为什么这一批不接 `SnapshotCoordinator`、
  为什么这是第一次真正落地 `fact_*` 层、真实消费方是谁、Provider 选型的
  既有惯例
- `skills/_sources/tradetime.py` 全文——`market_is_open()`（53 行起）与
  `session_in_progress()`（128 行起）现状。两个函数的文档字符串已经写明
  「已知边界：不认节假日」与朝安全方向偏的理由，改的时候不要削弱这条方向
  ——查不到日历数据时必须继续偏向"多报 missing"，不能变成"没查到就当
  放假"（那是更危险的方向：会让系统在真实交易日里以为休市）
- `skills/_sources/sina.py` 的 `IndexDaily`（67 行起）/`fetch_index_daily`
  （96 行起）/`parse_index_daily`（116 行起）——三者的分层是本批新
  Provider 该抄的形状：一个不联网的纯函数负责解析（能对着已存的 raw
  重放，不用第二次实现同一套判断），一个联网的薄函数调用它
- `skills/_snapshot/coordinator.py` 的 `freeze_index_daily`（104 行起）
  ——抓取之后 `save_raw_snapshot(payload=d.raw, raw_text=d.raw_text)`
  的真实调用写法。🔴 **只抄这一段落盘方式，不要接 `SnapshotCoordinator`
  本身或 `evidence_sets`**——那解决的是"同一次决策运行内、多个 Specialist
  必须看到同一份易变网络数据"，交易日历是低频更新的参考表，不是这个形状
  （设计探活已展开这一条，不要重新论证一遍）
- `skills/_store/db.py` 的 `save_raw_snapshot`（1184 行起）/
  `raw_text_sha256`（1165 行起）——批 I 已经把两者通用化，本批直接用，
  不需要改
- `skills/_store/schema.py` 开头的 `_append_only()`（22 行起）——新表怎么
  接上「只追加」触发器；`tests/test_store.py::test_每张表都有只追加触发器`
  会自动发现新表并要求它接上，不接会真的红（不是装饰，见该测试实现）
- `docs/external/` 数据架构材料点名的 `a-stock-data`
  （`github.com/t4ol1n/a-stock-data`）——这个仓库对它的定位一直是「Provider
  Catalog / 接口实现参考」，不是依赖；开工前点开它的交易日历实现看一眼有
  没有免鉴权端点可抄

## 做什么

1. **新 Provider 适配器**：`skills/_sources/` 下新增一个模块（模块名你
   定，比如 `calendar.py`），仿 `sina.py` 的 `fetch_index_daily`/
   `parse_index_daily` 分层——一个纯函数解析响应、一个薄函数联网调用它。
   端点自己选：先看 `a-stock-data` 的交易日历实现作参考，按现有四个适配器
   （sina/eastmoney/tencent/sina_news）的既定惯例选一个**免鉴权**的公开
   端点。如果确实找不到免鉴权的、只能考虑 Tushare 这类需要 token 的源，
   在正文里写清楚为什么值得为这一批引入凭据管理这项新复杂度，不要静默
   选上——这类需要凭据管理的源本身就是批 G-II 刚踩过的坑。
2. **落 raw**：抓取结果原样调 `save_raw_snapshot(source=, as_of=,
   retrieved_at=, payload=, raw_text=)`——直接调用，不接
   `SnapshotCoordinator`（理由见"先读"）。
3. **新增 `fact_trading_calendar` 表**（`skills/_store/schema.py`，新迁移号
   见上文"开工第一步"那条约束）。这是这个仓库第一张真正的 `fact_*` 表，
   列你自己定，但至少要能回答"某个交易日是否开市"；接上 `_append_only()`。
   历史事实一旦落地不该被 UPDATE——如果交易所事后补发调整（临时增加/
   取消一天），用新的一行（更晚的 `retrieved_at`）表达修正，不要覆盖旧行，
   读的一方按 `retrieved_at` 取最新一条。
4. **先给 `market_is_open()`/`session_in_progress()` 补特征测试
   （characterization tests），再改**：两个函数目前 `grep -rl
   market_is_open tests/` 零命中，改之前没有基线可比。至少固定：一个
   已知的、落在工作日上的法定节假日（例如 2026-01-01，元旦，周四——
   `market_is_open` 现状对这一天 09:31 会返回 `True`，这正是要修的缺陷，
   不是这一步该断言的"正确答案"）、一个普通交易日、一个周末日。有了基线
   之后，再让 `market_is_open()` 在有日历数据时优先查
   `fact_trading_calendar`；查不到时（数据还没抓到、或问的日期超出已抓
   范围）回退到现在这套 weekday 判据——回退分支的结果必须与"改之前"对
   同一输入完全一致，不能变成第三种沉默失败。
5. **`session_in_progress()` 要不要跟着改，你自己判断并在正文里说清楚**
   ——它回答的是"这一天是不是还没过完"，跟节假日感知是不是同一个问题、
   改了会不会影响 `emotion-calc` 现在的行为，设计探活没有替你做这个决定。

## 不要做

- 不要接 `SnapshotCoordinator`/`evidence_sets`——见上，这解决的是另一个
  问题
- 不要现在就把其余五个 P0 数据集的 schema 定下来——那是 §46 选股闭环的
  输入，没开工之前按猜测定型是要撞的坑
- 不要把 `market_is_open()` "查不到就多报 missing"这个朝安全方向偏的
  失败方向改成相反方向——见上
- 不要碰批 G-II 还在另一个 worktree 里修的 `skills/card/`/
  `deploy/openclaw/` 相关文件——两批文件基本不相交，如果 `git log` 发现
  有交叉，先确认谁先落地，不要假设

## 必须做的探针（G-1）

P1  Provider 纯函数可离线测：给解析函数喂一段固定响应文本，断言解析出的
    "是否开市"结果正确；联网函数本身不在离线测试范围内跑（沿用既有的
    禁网围栏）
P2  raw 层真的存 raw：抓取路径调 `save_raw_snapshot` 时，落盘的 `raw_text`
    与构造的响应体逐字节相同，`content_sha256` 与手算的 `raw_text_sha256`
    一致（沿用批 I 的判法，不该有第二套）
P3  🔴 只追加：`fact_trading_calendar` 的 UPDATE/DELETE 被拒——先确认
    `test_每张表都有只追加触发器` 真的把这张新表也扫进去了（比如故意在
    迁移里漏写 `_append_only()` 调用，跑一次看这道测试是否真的报红，
    再补上验证它变绿），不要只信"新表默认在清单里"这句话
P4  🔴 特征测试锁基线：改 `market_is_open()` 之前先跑一遍第 4 步写的特征
    测试，确认它们精确刻画了"改之前"的行为；改完之后同一批日期重新断言
    ——有日历数据的分支给出正确答案，没有数据的分支与"改之前"的结果
    逐一相同，不是"看起来差不多"
P5  fail-direction 不倒转：构造"日历数据完全空"与"查询的日期超出已抓
    范围"两种场景，断言 `market_is_open()` 的回退结果与改之前对同一
    输入完全一致
P6  新 Provider 的选型站得住：如果最终选了免鉴权源，断言测试套件里没有
    引入任何真实凭据依赖；如果选了需要 token 的源，断言正文里有一段
    解释为什么这一批值得引入凭据管理

## 做完之后

不要自己宣布通过。把 git diff 摘要 / 每道探针的红灯输出 / 你自己认为
最可能被攻破的一处交出来，由另一个会话评审。
```

---

## 批 H-I · 三个基础设施包迁移（`_contract`/`_store`/`_sources` → `src/easyup_biga/`）

⚠️ **依赖已清**：A 到 L 全部批次（含 J-I/J-II、K、I、L）都已评审复核通过、
合并进 `orchestration`。批 H 原计划「排在最后」，理由是结构重组会让期间
所有其他批次的 diff 变脏——现在没有更晚的批次了，轮到它。开工第一件事
`git log --oneline -10` 确认这一点，并亲手重新跑一遍下面「先读」第 4 条
提到的几处 grep——本提示词写的行号/清单是**这一刻**验证过的，你开工时
可能已经不是这一刻。

🔴 **批 H 拆成 H-I（这一批）/ H-II（留白）**，理由与 A/C/D/E/J 拆分完全
一致——耦合面不同：

- **H-I（这一批）**：只搬设计文档 §8 自己讨论过、给出了具体缓解方案的
  三个包——`_contract`/`_store`/`_sources`。纯**目录搬迁**，文件内容
  与内部相对导入**原样不动**，行为不变靠 `replay --check` 与「测试条数
  不减」验证，不靠新增业务判断。
- **H-II（留白，不是这一批做）**：`_runtime`/`_snapshot` 两个包往哪迁，
  以及 `application`/`integrations`/`cli` 三个命名空间该装什么——设计
  文档 §8 的缓解方案表**没有讨论过这两件事**，说明当时也还没想清楚。
  现在硬做，得到的是没有设计依据的猜测性目录，还违反本仓库自己的
  「按需创建，不预建空目录」——`agents/` 子目录已经吃过这个教训
  （CLAUDE.md「Agent」一节）。⇒ 留白，等真的有内容要往那三个空命名空间
  放的时候再建，不要现在造占位组件。

```text
把 skills/_contract/、skills/_store/、skills/_sources/ 三个共享基础设施包
的真实实现，搬进 src/easyup_biga/{domain,persistence,providers}/；三个旧
包原地留一份"薄壳"，把新位置的东西照原样重新导出，让全仓 71 处
`sys.path.insert(0, "skills")` 之后写的 `from _contract import ...` /
`from _sources.sina_news import ...` 之类的导入语句，一个字符都不用改。

## 先读

- `docs/design/deterministic-orchestration.md` §8～§11（1218-1287 行）——
  这一批唯一的设计依据。§8 是"采纳、但代价写在明处"（教程路径作废怎么办、
  `sys.path.insert(0, "skills")` 为什么是承重墙、结构重组没有行为判据怎么
  验收）；§9 兼容策略（读宽写严，旧格式自然清零）这一批不适用（没有新旧
  两种数据格式，只有新旧两个导入路径，但"旧路径只读、不再新增依赖"的精神
  一致）；§10 迁移闸门、§11 出口条件是收尾判据的来源
- `docs/external/easyup-biga-architecture-upgrade/docs/design/
  biga-architecture-upgrade-guide.md` 第 29 节（1112-1150 行）——目标形状
  的**原始出处**，`easyup_biga` 这个包名就是从这里来的，不要另起名字。
  🔴 但它给的 `domain/{models.py,contracts.py,registry.py}` 是三个文件，
  这一批要搬的 `_contract/` 有 9 个文件——**这一批只搬目录，不做这一层
  的文件合并**，见下方「不要做」。它自己也说了"不要一次全搬，通过
  Strangler Pattern 逐步迁移"——H-I 就是那个"逐步"里的第一步，不是终局
- `skills/_contract/__init__.py`、`skills/_store/__init__.py`、
  `skills/_sources/__init__.py` 三份全文——这就是"薄壳该导出什么"的完整
  规格，一个 `__all__` 名字都不能在迁移后消失。三份加起来 9+4+8=21 个真实
  子模块文件（`_contract`: card/evidence/facts/missing/notify/registry/
  run/verdict/verdict_ref；`_store`: db/runs/runtime/schema；`_sources`:
  eastmoney/http/sanity/sina/sina_news/szse/tencent/tradetime）
- 🔴 直接按子模块路径导入、绕过上面三份 `__init__.py` 聚合的地方——这些
  只留包级薄壳**不够**，个别子模块文件本身也必须留一份薄壳，否则
  `from _sources.sina_news import NewsFeed` 这类语句在真实实现搬走后
  直接 `ImportError`。已用
  `grep -rEon "from _(contract|store|sources)\.[a-z_]+ import|import _(contract|store|sources)\.[a-z_]+" --include="*.py" .`
  核实过一遍（写这份提示词时的结果，开工请自己重跑）：
  `_contract.{registry,card,evidence}`、`_store.{db,runtime}`、
  `_sources.{eastmoney,http,sanity,sina,sina_news,szse,tradetime}`。
  ⇒ 不要只shim 命中过这份清单的子模块——**21 个子模块文件全部留薄壳**，
  没被直接 import 过不代表将来不会有人这么写，逐个判断比统一处理更容易漂
- `tests/test_contract_single_impl.py` 29-31 行（`CONTRACT_DIR`/
  `STORE_DIR` 两个常量）与 84-85 行（`_in_contract`）；
  `tests/test_no_raw_sqlite.py` 25 行（`STORE_DIR`）与 46-47 行
  （`_in_store`）——**这两份文件是这一批风险最集中的地方**。它们的判据
  是"契约类 / DB 访问只能定义在这个目录下"，字面认的是 `skills/_contract`
  `skills/_store` 这两个旧路径。迁移后真实定义搬到新路径，旧路径只剩薄壳
  （薄壳里没有类定义，只有 re-export）——如果这两个常量不跟着改，"唯一
  实现"这道守卫会在新目录上**静默失效**：在新目录里另定义一个 `Evidence`
  不会被抓到，因为守卫压根没在看那个目录。这正是设计文档 §8 自己点名的
  风险，也是 L-13 的标准形状——**这一批必须把它变成一道会红的探针**（见
  下方 P2），不能只是改完常量就假设它还管用
- `pyproject.toml` 第 3 行 `pythonpath = ["skills", "."]`——pytest 专用的
  路径挂载，`src/` 要不要挂进这里、还是让薄壳自己在 `__file__` 相对路径上
  挂载 `src/`（`skills/decision-card/scripts/replay.py` 30-31 行、
  `skills/card/scripts/inbound.py` 55 行是这一手法的现成范例——每个脚本
  在自己开头算出仓库根、手动 `sys.path.insert`，不依赖 pytest 配置），
  你自己判断，但**两条路径都要**——pytest 内外都得能 import，只验证 pytest
  内的那一条会漏掉真实运行路径（`bin/biga-card` 拉起的子进程、
  `systemd-run` 脱树跑的那些，都不经过 pytest 的 `pythonpath`）

## 做什么

1. 建 `src/easyup_biga/{domain,persistence,providers}/`，每个目录一份
   `__init__.py`——内容是今天 `skills/_contract/__init__.py` 等三份文件
   的**真实聚合逻辑**（原样搬过去，包内相对导入 `.card`/`.db` 等不用改，
   因为相对导入只认包内相对位置，整包搬动不影响它们互相怎么找对方）。
2. `git mv` 21 个真实子模块文件到对应新目录（用 `git mv` 不是删了重建，
   保留 blame/history）：
   - `_contract/*.py`（9 个）→ `src/easyup_biga/domain/`
   - `_store/*.py`（4 个）→ `src/easyup_biga/persistence/`
   - `_sources/*.py`（8 个）→ `src/easyup_biga/providers/`
3. 三个旧包目录原地留下：包级薄壳（`__init__.py`，内容是从新位置
   re-export，保持 `__all__` 逐字不变）+ **21 个子模块级薄壳**（每个旧
   文件路径都留一份，内容是从新位置对应文件 re-export）——"先读"已经
   说了为什么子模块级也不能省。
4. 解决 `src/` 的可达性（pytest 内 + 真实运行路径外，两条都要，见上）。
5. 改 `tests/test_contract_single_impl.py` 的 `CONTRACT_DIR`/`STORE_DIR`
   与 `tests/test_no_raw_sqlite.py` 的 `STORE_DIR`，指向新位置
   （`src/easyup_biga/domain`、`src/easyup_biga/persistence`）——判据要
   看真实定义在哪，不是看薄壳在哪。
6. 教程冻结指针：`grep -rl "skills/_contract\|skills/_store\|skills/_sources" docs/tutorial/*.md`
   （写这份提示词时命中 15 章，自己重跑确认），逐章在文末追加一行
   「⏩ 后续变动：这一层挪到 `src/easyup_biga/...`，见批 H-I」，**不回改
   正文**——设计文档 §8 的原话，正文是历史记录，不是活文档。
7. `CHANGELOG.md` + `TODO.md` 收尾，把批 H 从「排在最后」改成「H-I 已
   落地、H-II 留白」，H-II 的留白理由（没有设计依据、不预建空目录）写
   进去，不要悄悄消失。

## 不要做

- 不把 `_contract/` 的 9 个文件合并成外部文档 §29 示意的
  `models.py`/`contracts.py`/`registry.py` 三个文件——那是**文件内容
  重组**，这一批只做**目录搬迁**，两件事混在一个 diff 里，「结构改动没有
  行为判据」这条验收方式就失效了（设计文档 §8 自己的话）。文件名、文件
  内部结构，这一批原样不动
- 不动 `skills/<skill 名>/`（`card`/`decision-card`/`emotion-calc`/
  `market-calc`/`news-scan`/`risk-check`/`sector-calc`/`technical-calc`）
  ——OpenClaw 的 skill 加载约定认的就是这个目录形状，这一批不碰任何真正
  的 skill 目录，只碰下划线开头的共享基础设施包
- 不动 `_runtime/`、`_snapshot/`——留给 H-II，理由见上面的拆分说明
- 不建 `application/`、`integrations/`、`cli/` 三个空目录占位——没有内容
  要放，先建目录就是造一个「建了但无消费方」的组件（L-1 的同一个道理，
  用在包结构上）
- 不引入真正的 Python 打包层（`[build-system]`/`[project]` 表、
  `pip install -e .`）——`src/easyup_biga` 这一批仍然是靠 `sys.path` 手动
  挂载的普通目录树，跟今天 `skills/` 的挂载方式一致，不是一个可安装的
  发行包。引入真打包是一个更大、更没设计过的决定，不要在这一批顺手做掉
- 不改 `schema.py` 的任何 SQL 内容、不建新 schema 版本——这一批只搬文件
  位置，`_store/schema.py` 的内容原样不动，没有新表新列，不触发 v16
- 不出新卡、不调用付费模型（通用前置已说明，这里重申：这一批的收尾验证
  用 `bin/biga-card --check <已有决策号>`，不需要新出一张卡）

## 必须做的探针（G-1）

P1  🔴 **导入兼容性，全仓扫出来的，不是手数的**：写一个脚本，AST 或正则
    扫全仓 `.py`，收集每一条 `from _contract... import`/`from _store...
    import`/`from _sources... import`/`import _contract`/`import _store`/
    `import _sources`（含子模块路径）语句，在一个全新的 Python 进程里
    逐条真的执行一遍，断言零 `ImportError`。这条比"跑一遍全部测试"更硬——
    测试套件本身也是通过这些语句才导入到被测代码的，"测试全绿"不能证明
    "导入语句本身没问题"，只能证明"没问题的那些语句背后的代码也对"。
    sabotage：删掉一个子模块薄壳（比如 `skills/_sources/sina_news.py`），
    确认这条探针红、错误信息指向具体是哪条导入语句失败，而不是笼统的
    测试失败；还原
P2  🔴 **"唯一实现"守卫真的在新位置生效**：迁移完成后，在
    `src/easyup_biga/domain/` 里临时定义第二个 `Evidence` 类（或近名类，
    比如 `EvidenceV2`），断言 `test_contract_single_impl.py` 真的报红——
    不是"改了 CONTRACT_DIR 就假设它管用"，是真的验证过。再把 `CONTRACT_DIR`
    临时改回旧路径 `skills/_contract`，确认同一个"第二个 Evidence"这次
    **不会**被抓到（因为守卫在看错的目录）——这一步是在证明"改常量"这个
    动作本身是必要的，不是可选的装饰。两步都做完，还原成迁移后的正确状态
P3  一致性：`bin/biga-card --check <一个已有决策号>` 在迁移前跑一次，
    迁移后再跑一次，输出逐字段相同（设计文档 §8 指定的验收方式——纯结构
    改动没有新行为可测，只能测"没引入行为差异"）
P4  测试条数不减：`pytest --collect-only -q` 的收集条数，迁移前后必须
    相等，不是"测试全绿"——数字变少而测试仍然全绿，说明某个测试文件在
    collection 阶段就被导入错误静默排除了，不会以失败的形式出现，只会以
    "少几条"的形式出现
P5  🔴 **非 pytest 路径也要能 import**：pytest 有自己的 `pythonpath` 配置
    （`pyproject.toml` 第 3 行），会掩盖"其实只有 pytest 内能 import，真实
    运行时不能"这类问题。单独起一个**不经过 pytest** 的 Python 子进程
    （比如 `python3 -c "import sys; sys.path.insert(0,'skills'); import
    _contract; print(_contract.Evidence)"`，模拟 `bin/biga-card` 真实
    拉起子进程时的路径挂载方式），断言它同样成功——两条路径都要单独证明，
    一条green 不能代表另一条也 green
P6  隔离自检不受影响：`python3 tools/verify/isolation.py` 迁移前后结果
    一致（这一批不碰任何 systemd 单元/端口/nvm 路径，理论上不该有任何
    变化，但这类"理论上不该变"的东西恰恰值得一次真跑确认，而不是假设）

## 做完之后

不要自己宣布通过。把 git diff 摘要（尤其是 21 个 `git mv` 是否真的保留了
history，用 `git log --follow` 抽查两个）/ 每道探针的红灯输出 / 你自己
认为最可能被攻破的一处交出来，由另一个会话评审。
```

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
