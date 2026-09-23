# 第 33 章 · 把 risk 的事实挪进编排器（确定性早退省一次 LLM 调用）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 F —— 把 `risk_check.py::build_fact_bundle()` 的调用从「risk 被
> spawn 后自己在 LLM 会话里跑」挪到「编排器在决定要不要 spawn 之前直接 import、免费地跑」；
> 两种确定性早退（`foreign` / 无上游）不再 spawn risk；落地时独立发现并修掉两处设计没
> 点名的交互（fact 双写静默并存、早退场景 spawn_check 误判）。
> **不覆盖**：risk 的判断实质（词表、什么时候该否决 —— 那不动，见 `phase-2-specialists.md`）；
> Outbox / 飞书触发（批 G）。

---

## 目标 / 产出

做完这一章，得到：

- 编排器 `import build_fact_bundle` —— 与 `risk_check.py` 的 CLI **共用同一份纯函数**，
  在 spawn risk 之前把风险事实**免费**算好、落库。
- 两种确定性早退（`foreign` 证据跨决策污染 / 完全没有上游）⇒ **不 spawn risk**，直接拿
  `verdict_ref` 参与合成。
- 其余情况仍 spawn risk，但它**不再自己跑 skill**，只解读编排器算好的 `verdict_ref`。
- schema **v11**：`ux_fact_per_task_agent` 分区唯一索引，堵掉「同一 `(task_id, agent)`
  第二次写 fact 静默并存」。
- `card_ops.persist()` 只给真被 spawn 的 agent 记账本行。
- P1–P5 探针 + 两道 G-1 红灯。

---

## 为什么这么做

### risk 早就是纯 Python，问题不在「拆」，在「位置」

设计文档把批 F 叫「Risk 拆两层」（`RiskPolicy` 硬 + `RiskAssessment` 软）。开工前读
`risk_check.py` 全文（414 行、三个顶层函数）才发现：**它本来就是拆好的**。
`build_fact_bundle(*, verdict_ids, store, task_id)` 是个不碰 LLM、不碰 spawn 的纯函数——
`main()` 只是给它套了一层 argparse + `save_fact_bundle()`。不需要「先拆出来」。

真正的浪费不在「算得慢」，在**为了触发这次算，先花一次 LLM 调用**：

```
老路径：编排器 spawn risk → LLM 读提示词 → LLM exec risk_check.py（本地子进程，免费、快）
        → LLM 读 JSON、按判断表定 stance → LLM 跑 amend_verdict.py
        ↑ 花钱耗时的是那两次 LLM turn，中间那次脚本调用免费
```

编排器现在就能直接 `import build_fact_bundle` 调它。于是：

```
新路径：编排器直接 build_fact_bundle()（免费）→ save_fact_bundle 落库拿 verdict_ref
        → 值不值得 spawn？值 ⇒ spawn risk 只解读；不值 ⇒ 根本不 spawn
```

> **通用原则**：一个「纯函数被包在 LLM 会话里跑」的组件，省钱的切口往往不是「让 LLM
> 跑得快」，而是**把那次纯计算从 LLM 会话里拎出来，在程序里直接调**。花钱的是包着它的
> 那层 turn，不是它自己。

### 不是所有结果都值得省这次调用 —— 判据是 `fb.status == 'failed'`

读 `build_fact_bundle` 的返回路径，分三种：

| 路径 | 触发 | LLM 解读还有信息增量吗 |
|---|---|---|
| 提前 return（归属） | `foreign`：上游证据来自别的决策 | **没有** —— `verdict=UNKNOWN` 是机械判定，`missing` 已写清哪几个决策串进来，stance 只能「无法判定」 |
| 提前 return（无上游） | 一条上游都没拿到 | **没有** —— 同上，唯一合法结论就是「无法判定」 |
| 走到底 | 覆盖不足 / 交易日不一致 / 阈值命中 / 正常 PASS | **有** —— 即使 stance 已被判断表钉死，卡面给人看的解释性文字仍需 LLM 组织 |

只有前两条值得**完全跳过 spawn**。它们的共同信号：都产 `status='failed'`。而走到底那条
（342–348 行）永远只产 `completed`/`partial`。契约铁律又钉死 `status='failed' ⟹
verdict='UNKNOWN'`（`check_fact_invariants`）。所以：

```python
if risk_fb.status == "failed":   # foreign / 无上游 —— 机械终局，不 spawn
    r2 = []
else:                             # 其余 —— 仍 spawn，但 risk 只解读
    rh = ad.start(RISK_AGENT, did, self._risk_task(did, risk_ref, risk_fb), …)
```

> **通用原则**：判据要落在**不会误伤**的信号上。这里没用「verdict==UNKNOWN」——走到底那条
> 覆盖不足时也会是 UNKNOWN（但值得解读）。`status=='failed'` 才唯一对应「机械终局」。
> 探针 P1/P2/P3 分别把三条路径钉住，判据落错就红。

### 「present 但没 stance」不会被误判成「缺席」

编排器落 fact、**不追加 assessment**，卡上 risk 就是「有 fact、stance=None」。查过
`load_verdict` 的多态：一行只有 `kind='fact'`、从未被 `save_assessment` 追加过的记录，
`load_outcome()`/`to_agent_verdict()` 依然能压成一个合法 `AgentVerdict`（`stance=None`），
正常计入 `card.verdicts`。`absent_agents`（`STANCE_VOCAB` key 减去有判定的 agent）因此**不**
把它算成缺席——「没给判断」与「没响应」是两件事，后者 `absent_agents` 单独管。

### 坑一：fact 行天生逃出了「线性修订唯一索引」

设计文档给批 F 留了一个「未解的边界情况」让建造会话核实、不预先拍板：**万一 risk 没听
新提示词、又自己跑了一遍 `risk_check.py --task-id <同一个 did>`，会怎样？**

读 `save_fact_bundle` + schema：fact 行的 `amends` **恒为 NULL**，而 v6 的
`ux_verdict_amends_linear` 是 `UNIQUE(amends) WHERE amends IS NOT NULL` —— **fact 行天生
在它管辖之外**。SQLite 里多个 NULL 在 UNIQUE 索引里互不冲突。于是对同一个
`(task_id, agent)` 第二次写 fact，两行都是 `amends=NULL`，**静默并存两条判定原件**。

后果不是「某处算错」，而是 `latest_verdict_ids()` 取 `MAX(verdict_id)` 会悄悄改用 risk
双跑那条，编排器预先算的那条被架空——「这次决策的 risk 事实原件是哪一条」从此有歧义。
对一个卖点是「证据可追溯、可回放」的系统，这最不能有。

**修法**：schema v11 补一条分区唯一索引，与 `ux_decision_online`（一决策一在线卡）、
`ux_verdict_amends_linear`（一原件至多一修订）同形：

```sql
CREATE UNIQUE INDEX ux_fact_per_task_agent
    ON agent_verdicts(task_id, agent) WHERE kind = 'fact';
```

> **通用原则**：唯一约束由数据库兜底，不靠应用层「先查再插」（那有竞态窗口）。这条是
> 本仓库反复用的成例（`decision_ids` 主键仲裁、`run_events` 的 `UNIQUE(run_id, seq)`）。

🔴 加索引前**实测生产库**：`CREATE UNIQUE INDEX` 在有存量重复时会直接报错，而 append-only
下没法事后清洗——所以必须先确认 `kind='fact'` 里没有 `(task_id, agent)` 重复（实测唯一
1 条 fact 行）再加。

### 坑二：报错判据落在索引名上 —— 又一次 L-13，被探针当场抓到

`save_fact_bundle` 要把 `IntegrityError` 翻成指路的 `ValueError`。第一版判据是
`if "ux_fact_per_task_agent" not in str(e): raise`——照抄了「匹配索引名」的直觉。探针
一跑就红：SQLite 的报错是

```
UNIQUE constraint failed: agent_verdicts.task_id, agent_verdicts.agent
```

**报的是列名，不是索引名**。判据落在一个报错里根本不出现的字符串上，于是所有真实冲突都
被原样重抛成裸 `IntegrityError`。改成匹配列名（`agent_verdicts.task_id` +
`agent_verdicts.agent` 同时在）才对——这也正是 `save_verdict` 里匹配 `agent_verdicts.amends`
的同一招。

> **通用原则**（L-13）：守卫的判据要落在「被守的东西真实产生的那个信号」上，不是你**以为**
> 它长什么样。这条差点让一个「指路报错」退化成裸异常，是探针（不是 code review）抓到的。

### 坑三：早退不 spawn，但 risk 仍在卡上 —— spawn_check 会把它误判成伪造

这一处**设计文档没点名**，是落地时独立发现的。早退不 spawn risk，但 risk 的 fact 仍进卡
⇒ `persist()` 原本会给 risk 写一行 `agent_runs`（执行账本）。而 risk 根本没被 spawn
（没 LLM turn、没花钱、运行时 `subagent_runs` 里没它）⇒ `tools/verify/spawn_check.py` 看到
「`agent_runs` 有行、`subagent_runs` 没有」，把 risk 判成 **forged（伪造）**，
`bin/biga-card` 据此 `exit 4`——一张正确产出的卡被判失败。

给一个没执行过的 agent 记账本行，本身就是 **L-8 幽灵账本行**（记了没发生的事）。修法在
`persist()`：提供 `runtime_run_ids` 时，它的 **key 集**就是「本次真正被 spawn 的 agent」的
权威名单，只给名单里的 agent 记账本行。

```python
if runtime_run_ids is not None and v.agent not in runtime_run_ids:
    continue   # 没被 spawn（早退里的 risk）⇒ 不记账本行
```

判据是 **key 在不在**，不是 `.get()` 的值——被 spawn 但没拿到 runtime id 的是「key 在、
值 None」，仍要记账。不提供该映射（`synthesize.py` / 测试 / 回放）时维持原样。

> **通用原则**：「谁执行过」这类账本，只能记**真发生过的执行**。一个免费算出来的事实不是
> 一次 agent 执行，给它记一行会同时踩两个坑：L-8（幽灵行）和「下游校验器把它当异常」。

### 一个决定：不留「兜底自己跑一遍」的路径

设计把「要不要保留 risk 兜底自跑 `risk_check.py`」留给建造会话定。**定为不留**，理由：

1. 编排器的 `build_fact_bundle` 是纯函数，**永远返回一个 FactBundle**（缺上游就返回
   UNKNOWN，不抛异常）。所以 risk 收到的 `verdict_ref` **一定有效**——没有「编排器算不
   出来」的现实场景（真抛了，run 会在 spawn risk 之前就 FAILED）。
2. 兜底自跑 = risk 成了 risk 事实的**第二个生产方**，既违背「一个事实一个生产方」，又会
   撞上 v11 唯一索引（编排器已落过一份）。
3. 万一 risk 真手滑跑了，撞索引得到的是一句**指路的报错**（P4 验证），不是静默腐败。

所以 `agents/risk/AGENTS.md` 约束 1 改成「事实已算好、你只解读」，并写明「没有兜底自跑
这条路」。契约不跟着改就是 L-6（契约与实际行为对不上）。

### 行为变化：全员缺席不再「零证据 FAILED」

因为 risk 现在**总会**落一条 fact（连无上游都落），编排器 Stage 3 的 `ordered` 至少有
risk。老行为（全员 Stage 1 缺席 → 库里零 verdict → 「零证据 FAILED」）不再成立：现在出
一张只有 risk「无上游」、满是缺失项的卡。这与「出一张标着不知道的卡，比不出卡强」一致，
也正是 P2 要的（无上游早退一路走到 COMPLETED）。那道 `if not ordered` 退成纵深防御
（契约层 `synthesize` 也拦零 verdict，且抢在 synthesizer spawn 前 fail-fast）。

---

## 执行

真跑过的关键改动：

```
skills/_store/schema.py         v11：ux_fact_per_task_agent 分区唯一索引
skills/_store/db.py             save_fact_bundle 接住 IntegrityError → 指路 ValueError（匹配列名）
skills/decision-card/scripts/orchestrator.py
                                import build_fact_bundle；Stage 2 先算 fact 落库；
                                status=='failed' 不 spawn；_risk_task 换新提示词（不跑 skill）
skills/decision-card/scripts/card_ops.py
                                persist() 只给 runtime_run_ids 里的 agent 记账本行
skills/risk-check/scripts/risk_check.py
                                main() 接住 ValueError 干净退出码 2（CLI 双跑不再裸 traceback）
agents/risk/AGENTS.md           约束 1 改写：事实已算好、只解读、无兜底自跑
```

迁移生产库（自 J-I 合并后停在 v9，`load_verdict_meta` 读 v10 的 `run_id` 列会
`OperationalError`——这是既有环境欠账，不是批 F 引入）：

```bash
python3 -c "import sys; sys.path.insert(0,'skills'); from _store import init_schema; \
  print(init_schema('data/biga.db'))"     # v9 → v11，run_id 列 + 唯一索引，数据行数不变
```

---

## 坑

本章的坑都在上面「为什么」里就地讲了，汇总一句话版：

- **fact 行 amends 恒 NULL**，v6 线性索引管不到它 ⇒ 双写静默并存（v11 补索引）。
- **`IntegrityError` 报列名不报索引名** ⇒ 判据别落在索引名上（L-13，探针抓到）。
- **早退不 spawn 但 risk 进卡** ⇒ `persist` 别给它记账本行（L-8 + spawn_check 误判）。
- **`test_persist抛异常…` 只在 `pytest test_orchestrator.py test_facts_split_e3.py`
  这个文件顺序下红**——是 e3 模块级 `_load("card_ops")` 污染 `sys.modules` 的**既有**
  脆弱性（在原始提交 f9bc68c 上同样复现），全量套件（字母序）不触发。记进 `TODO.md`，
  本批不修（不属于批 F）。

---

## 验证

```bash
# 三条路径 + 双写 + VETO 全路径
python3 -m pytest tests/test_orchestrator.py::TestBatchFRiskInline \
  tests/test_facts_split_e3.py::TestBatchFDoubleFactRejected -q     # 全绿

# G-1 红灯（弄坏 → 报红 → 还原）
#  A) MIGRATIONS 去掉 (11,_V11) → test_P4_CLI双跑… FAILED（stderr 出现两条 verdict_ref）
#  B) 判据改 `if False and …` → P1/P2 FAILED（[risk] verdict='UNKNOWN' 却给出 stance）

# 回放一致 + 公开审查
bin/biga-card --check BIGA-20260922-001      # ✅ 逐字段相同
tools/verify/audit_public.sh --worktree      # ✅ 十一项
```

预期：`TestBatchFRiskInline` 5 条、`TestBatchFDoubleFactRejected` 2 条全绿；两道 G-1
探针弄坏后真红、还原后绿；replay `--check` 逐字段一致；audit 十一项绿。

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 纯函数包在 LLM 会话里跑 —— 省钱切口是「把它拎进程序直接调」，不是让 LLM 跑快 |
| 2 | 只有确定性终局（`fb.status=='failed'`）值得跳过 spawn；判据别用会误伤的 `verdict==UNKNOWN` |
| 3 | fact 行 `amends` 恒 NULL，逃出了 v6 线性索引 ⇒ 双写静默并存，v11 分区唯一索引补上 |
| 4 | `IntegrityError` 报**列名**不报索引名 —— 翻译报错的判据要落在列名上（L-13） |
| 5 | 早退不 spawn 但 risk 进卡 ⇒ 别给它记账本行（L-8 幽灵行 + spawn_check 误判 forged） |
| 6 | 不留兜底自跑：编排器永远先算好，risk 是纯解读方；真手滑撞索引得到指路报错 |
| 7 | 全员缺席不再「零证据 FAILED」，改出满缺失的卡（出标不知道的卡 > 不出卡） |
| 8 | VETO 不改实质：P5 验证否决从 amend 一路穿到 DecisionCard 拦 BUY，一个环节没断 |

> ⏩ **后续变动（2026-09-23，批 H-I）**：本章出现的 `skills/_contract` / `skills/_store` /
> `skills/_sources` 三个共享包，其**真实实现**已迁至
> `src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export 薄壳 ⇒
> 本章正文里的 `from _contract import ...` 等导入语句与位置描述**照旧成立**，只是代码
> 本体不在那儿了。见 `CHANGELOG.md` 批 H-I。
