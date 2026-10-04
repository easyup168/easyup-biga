# 第 29 章 · 把其余四个 Specialist 迁到 FactBundle（一次「机械迁移」里真正难的那一小块）

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 E-II —— 四个 skill 从产 `AgentVerdict` 迁到产 `FactBundle`；
> 一个看起来纯机械、实际卡在一个设计判断上的迁移；为什么「Agent 追加的限制」要去查
> **真实数据库**而不是照抄设计文档的例子；`market.trend.no_history` 为什么是「范围外」
> 而不是「数据缺口」；为什么修一份 Agent 模板反而是这一批的核心交付物
> **不覆盖**：迁 `risk` 与退役 `amend_verdict.py`（批 E-III）；新三型本身怎么设计的（第 27 章）

---

## 目标 / 产出

- `market_calc.py` / `sector_calc.py` / `technical_calc.py` / `news_scan.py`：
  `build_verdict → build_fact_bundle`，返回 `FactBundle`（只事实、无 stance）。
- ①的裁定落地：四个 skill 历史上靠 `amend_verdict.py --add-missing` 补的限制，
  **确认全部已由 skill 自检**；唯一的例外 `market.trend.no_history` 裁定为「范围外」，
  skill 不产它。
- 第五交付物：修 `agents/market/AGENTS.md` 模板，删掉那条现在会被拒的 `--add-missing`
  命令，趋势 caveat 改走「需要注意」自由文本。
- `tests/test_facts_split_e2.py`：六道探针（P1–P6）+ 四道红灯演练。

---

## 为什么这么做

### 机械的部分是真机械 —— 难的是它顺带逼你回答的那个问题

四个 skill 的迁移本身，跟第 27 章迁 `emotion` 一字不差：把 `AgentVerdict` 换成
`FactBundle`（后者就是前者**减去 `stance`**），把 `save_verdict` 换成 `save_fact_bundle`。
这些 skill 本来就不填 `stance`（`stance` 一直是 Agent 事后补的），所以换个返回类型，
其余逻辑一行不动 —— 包括 market/sector/technical 读冻结快照、填 `evidence_set_id`
那部分（那是批 D-II / E-I 的地基，这一批不碰）。

真正要想清楚的是 E-I 交下来的一个设计问题：

> 老 `amend_verdict.py --add-missing` 是让 Agent 事后往一份判定里补一条「限制」
> （最典型的那句「只有单日快照，无法判断趋势」）。事实和判断拆开之后，`stance`
> 有家了（`AgentAssessment`），**这条「限制」该去哪？**

分发提示词给的裁定是：**改成 skill 自己在 `build_fact_bundle` 里检测，写进
`FactBundle.missing`**。理由很硬 —— 「这次抓到几天的数据」是抓取过程本身的输出，
skill 一开始就知道，不需要等 Agent 事后指出。

### 第一步不是写代码，是去查真实数据库

裁定里有一句话是这一章的整个重点：

> 做法：查历史行 —— **先看真实数据库里到底出现过哪些**，不要只照抄上面那个例子。

照抄例子会直接走错。我查了 `data/biga.db` 里四个 skill 所有带 `amend_reason` 的行，
去掉 `stance=…` 那类（那是加判断，不是加限制），剩下的**只有三个形状**：

| skill | 历史上 `--add-missing` 的限制 | skill 现在自检了吗 |
|---|---|---|
| market | 涨跌家数 0/0/0，分母为零 | ✅ `market.breadth.not_yet_formed` |
| market | 涨跌家数源（东财）不可用 | ✅ `market.breadth.unavailable` |
| sector | 盘前，板块榜尚无数据 | ✅ `sector.board.pre_session`（**同名同码**） |

`technical` 和 `news` 历史上**一条 `--add-missing` 限制都没有**（全是 `stance=`）。

结论出乎意料：**①的裁定对这四个 skill 不需要新增任何检测代码。** 真正的数据缺口
skill 早就在自己的 `missing[]` 里报了 —— Agent 当年用 `--add-missing` 补的，多半是在
**重复** skill 已经报过的东西。

### 唯一的例外，正好是设计文档预留的那个「逃生舱」

只有一条对不上：market 的 `market.trend.no_history` —— 「市场趋势 —— 只有单日快照，
无指数历史序列，无法判断趋势方向」。它出现了 5 次，而且 skill 里**没有**对应的自检。

裁定里留了一个 ⚠️ 逃生舱：

> 如果找到一条真的**不是「skill 明知道的阈值」、而是需要 Agent 主观判断的限制**
> —— 停下来，回评审确认，不要强行套用。

要判断它属于哪种，我去看了那 5 次修订的**原件**：

```
vid=2   原件有 volume_ratio=True  15/15 字段  0 缺失（满字段 PASS）
vid=18  原件有 volume_ratio=True  15/15 字段  0 缺失
vid=33  原件有 volume_ratio=True  15/15 字段  0 缺失
vid=80  原件有 volume_ratio=True  12 字段     2 缺失
vid=115 原件有 volume_ratio=True  12 字段     2 缺失
```

`volume_ratio` 是今日量比近 **20 日**均量 —— 它在场，就说明 market 手里**有整段序列**。
也就是说「只有单日快照，无指数历史序列」这句话**从来不是真的**：market 每一次都有
25 根日线。Agent 说的其实是「我看不出**趋势方向**」——那是一个**判断**（铁律 4：
skill 给事实，趋势归 Agent），不是「这次抓少了」。

一旦看清这一点，答案就有了现成的先例：`sector_calc.py` 顶部早就裁定过一模一样的东西 ——

> 「这个板块连涨几天了」需要历史序列，Phase 2 不做历史回补。这是**范围外**，
> 不是数据缺失 —— 所以它**不进 `missing`**，而是写进 Sector Agent 的契约。
> `missing[]` 是给「本该有却这次没有」的。范围外每次都在，混进去就把真正的缺失淹没了。

`market.trend.no_history` 与「板块持续性」是同一条线。⇒ **裁定：范围外，skill 不产它。**
不进 `missing`、不给 `AgentAssessment` 加字段、skill 源码里连 `trend` 这个词都不该出现。

> 通用原则：一条「限制」到底是**数据缺口**还是**判断边界**，不看它的措辞（两者都能写成
> 「无法判断 X」），要看**数据在不在**。数据在、只是结论给不出 ⇒ 判断边界，归 Agent。
> 数据不在 ⇒ 缺口，归 skill 的 `missing[]`。

### 为什么修一份 Agent 模板，反而是这一批的核心交付物

裁定到这里其实可以收尾了 —— 但设计 owner 追进去看了 `agents/market/AGENTS.md`
的模板，发现问题比「归哪」更深一层。

那条历史命令的完整形状是：

```bash
amend_verdict.py --ref <ref> \
  --add-missing market.trend.no_history "..." \
  --verdict WARNING \
  --stance 缩量上涨          # ← 一个**真**判断，不是「无法判定」
```

注意 `--verdict WARNING`：Agent 明明有一个真判断（`缩量上涨`），却把这份卡的
`verdict` 从 skill 算出来的 `PASS` **事后降级**成 `WARNING`，用来表达「这张卡别当
满分信」的保留。而 `verdict`（数据完整度）是 **skill 算的、可回放的事实** ——
Agent 本不该有能力事后覆盖它。

**这正是批 E 拆事实/判断要焊死的那条缝。** `market.trend.no_history` 不是一个需要
新家的信号，它是**旧契约漏的一个洞**，新契约恰好把它关上了：迁移后 fact 行会**直接
拒绝** `--add-missing`。

⇒ 所以修模板不是「顺手清理文档」，是**必须做、且必须这一批做**：如果留着旧模板，
market Agent 下一次真实出卡就会照着它敲那条命令、当场被拒，然后大概率像 **F9** 那样
抖动（实测 62 秒、12 次工具调用去找正确命令）。caveat 改走 Agent 回答里本来就有的
「需要注意」自由文本字段即可。

> 🔴 「不改 AGENTS.md」是本仓库的默认硬纪律。这一批是**设计 owner 明确授权的例外**
> —— 因为不改的代价不是文档陈旧，是一个可预测的运行时故障。默认值挡的是「顺手改提示词」，
> 不是「有充分理由、被授权地改一处」。

### ②：消费方要不要改成直接读 `AgentOutcome`？不改

裁定：不改。`card_ops`/`risk_check`/`DecisionCard` 现在靠 `load_verdict` 的
`to_agent_verdict()` 兼容垫，零改动就同时吃新旧两种形状，且 E-I 的 P3 探针已经证明过。
没有消费方需要「事实与判断分开看」这件事本身 —— 提前把这层也收敛掉是 L-1。

---

## 执行

```bash
# 1. 查真实数据库里四个 skill 的 --add-missing 历史（①的判据，不照抄例子）
python3 - <<'PY'
import sqlite3, json
con = sqlite3.connect("file:data/biga.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute("""
  SELECT verdict_id, agent, amends FROM agent_verdicts
  WHERE amends IS NOT NULL AND agent IN ('market','sector','technical','news')
""").fetchall()
# 对每条修订，算它相对原件新增了哪些 missing code —— 这才是真正的「--add-missing」
# （amend_reason 只记第一条，会漏）
PY

# 2. 机械迁移（每个 skill 同一套改动）
#    build_verdict→build_fact_bundle, AgentVerdict→FactBundle, save_verdict→save_fact_bundle

# 3. 探针 + 四道红灯演练
python3 -m pytest tests/test_facts_split_e2.py -q     # 23 passed

# 4. 回归 + 计数同步 + 回放
python3 -m pytest -q                                   # 1062 passed
bash tools/verify/sync_test_count.sh                   # ▸ 已同步为 1062 条
bin/biga-card --check BIGA-20260922-001                # ✅ 组装一致，逐字段相同
```

---

## 坑

### 坑 1 · 「日线不足」不能用「少冻几根」来模拟

想给 `technical` 造一个「MA60 算不出」的缺口，第一版把冻结集只冻 30 根、再让
technical 读它。结果 `read_index_daily` 直接 `SnapshotReadError`：

```
sh000001 这次只冻了 30 根，读不出 120 根。
```

`SnapshotCoordinator` 是 **fail-closed** 的 —— 读的根数超过冻的就抛错，绝不静默返回更少
（那是 R-3 要防的）。所以「数据源给的根数不够」这种缺口，**只能在手工调试路径**
（不给 `--evidence-set-id`、走 `fetch_index_daily`）上模拟：stub 让数据源只返回 30 根。
读冻结快照那条路径永远是「要么够、要么抛」，构造不出「读到一半发现不够」。

### 坑 2 · 加了测试就得同步计数徽章，否则 `test_docs_convention` 会红

加了 23 条探针，`test_文档里的测试条数与实测一致` 立刻红（文档还写着 1039）。
跑 `tools/verify/sync_test_count.sh` 同步进 README/CLAUDE 徽章即可 —— 这条守卫存在的
意义就是逼你别让徽章说谎。

### 坑 3 · 红灯演练要真的动生产代码，而工作区已经有未提交改动

G-1 要求「把被守的东西真弄坏、看它报红、再还原」。但这一批的工作区本身就有一堆未
提交改动（迁移本身），`git checkout <file>` 还原会把迁移一起冲掉。⇒ 红灯演练一律
**先 `cp` 备份到 scratchpad、patch、跑单条探针、再 `cp` 回来**，绝不用 `git checkout`。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 六道探针全绿
python3 -m pytest tests/test_facts_split_e2.py -q          # 期望 23 passed

# 四个 skill 的 build 函数确实改名、确实产 FactBundle
python3 -m pytest tests/test_market_calc.py tests/test_sector_calc.py \
                  tests/test_technical_calc.py tests/test_news_scan.py -q

# 全套回归 + 回放一致
python3 -m pytest -q                                        # 1062 passed
bin/biga-card --check BIGA-20260922-001                     # ✅ 逐字段相同

# market 模板里不再有会被拒的 --add-missing 命令块
grep -c "add-missing" agents/market/AGENTS.md               # 只剩「不要这么做」的说明，无命令
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 四个 skill 的机械迁移与迁 emotion 一字不差：`build_verdict→build_fact_bundle`、`AgentVerdict→FactBundle`、`save_verdict→save_fact_bundle`，读冻结那部分一行不动 |
| 2 | ①「Agent 补的限制归哪」：**先查真实数据库**，不照抄设计文档的例子 —— 四个 skill 的历史 `--add-missing` 全部已被 skill 自检，①不新增任何检测代码 |
| 3 | 判断一条「限制」是数据缺口还是判断边界，看**数据在不在**，不看措辞：数据在、结论给不出 ⇒ 判断边界，归 Agent |
| 4 | `market.trend.no_history` 实测 5 次原件都带 `volume_ratio`（有整段序列）⇒ 是判断边界（同 sector「板块持续性」），范围外，skill 不产它 |
| 5 | 命中了设计预留的 escape hatch：据实测证据**停下来确认设计**，没有强行把它塞进 skill 或 AgentAssessment |
| 6 | 修 `market` 模板是核心交付物、且必须这一批做：老模板的 `--add-missing … --verdict WARNING` 是 Agent 事后覆盖 skill 完整度的旧洞，迁移后会被拒，不改就是 F9 式抖动 |
| 7 | 「不改 AGENTS.md」是默认纪律，本批是设计 owner 授权的例外 —— 例外的判据是「不改会导致可预测的运行时故障」，不是「我觉得该改」 |
| 8 | fail-closed 的冻结快照构造不出「读到一半不够」；「数据源根数不足」只能在手工抓取路径上模拟 |
