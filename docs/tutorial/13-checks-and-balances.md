# 第 13 章 · 制衡层：一个说不上话的风控，必须让人看出它说不上话

> 对应 Phase 2 的 2.2。这一章建 `risk` —— 系统里唯一有否决权的 Agent。

---

## 目标 / 产出

- `skills/risk-check/` —— **只读冻结证据**，结构上不可能采数据
- `risk` Agent 与它的否决权
- Stage 2 与 Stage 1 的顺序约束，以及一条会在正确行为上报红的检查（修掉了）

---

## 为什么这么做

### 1. 先解决一个自相矛盾，再写代码

`DecisionCard` 一直用 `verdict == 'BLOCK'` 作为否决权的判据。
而我们在第 12 章刚刚写死：**`verdict` = 数据完整度**。

这不只是措辞问题。摆开看：

> risk 数据完整、且要否决。`verdict` 该填什么？

填 `BLOCK`，就再也说不出「它的数据是全的」；填 `PASS`，否决就丢了。
**一个字段装不下两件事** —— 而「否决」与「凭什么否决」恰恰都需要知道。

所以 `BLOCK` 从 `VerdictLevel` 移到 `stance`：

```python
VETO_STANCE = "否决"
STANCE_VOCAB["risk"] = ("放行", "警示", VETO_STANCE, "无法判定")
```

🔴 **这个字面量只许有一处定义。** 改了词表却忘了改判据，
否决权会**静默失效** —— 而那正是本项目最怕的 fail-open。测试钉死两者同源。

#### 顺手补的反向约束

原来只拦「否决 + BUY」。但这样一来，**否决可以被显示成 `WAIT`**。

`WAIT` 的意思是「再看看」，否决的意思是「不要做」。
把后者显示成前者，就是在合成阶段软化制衡层 —— 而且从卡面上看不出来。

⇒ 有人否决，Card 状态**必须**是 `AVOID` 或 `BLOCK`。

### 2. 🔴 risk 不许自己采数据 —— 这是结构，不是纪律

制衡的意义在于审**别人据以下结论的那份证据**。

risk 自己重采一遍会怎样？它看到的可能是另一个市场（尤其盘中）。
那时候它审的是自己的幻觉，不是这次决策的依据。更糟的是回放：
两边看到的数据不一样，「当时为什么放行」永远查不清。

所以这条不能只写在契约里靠 Agent 自觉，要**在代码结构上保证**：

```python
def test_不import任何采集层():
    assert "_sources" not in mods
    assert not (mods & {"urllib", "requests", "httpx", "socket"})
```

`risk_check.py` 的输入只有一串 `verdict_id` —— 而这串 id 正是第 11 章
为了「不让 LLM 搬运数据」建的 `agent_verdicts` 表。**两件事自然接上了**：
一个为了省 token 建的机制，成了制衡层读冻结证据的天然入口。

### 3. skill 报「碰到了哪些线」，不报「有多危险」

阈值写死并带版本号：

```python
THRESHOLDS = (
    ("broken_rate",  ">", 0.50, "risk.emotion.broken_rate_high", "炸板率超过 50%…"),
    ("volume_ratio", ">", 2.00, "risk.market.volume_spike",      "量能 2 倍以上…"),
    ...
)
```

「炸板率 62% 算不算该否决」是 Agent 的判断（铁律 4）。
skill 只把「哪些线被碰到了」变成可复查的事实。

⚠️ 这些数字**未经本项目验证**，与情绪分同属「业内常见口径」。
它们的区分力要到测量阶段检验过才算数 —— 现在只是让判断有个共同的参照物。

---

## 执行

```bash
# risk 的全部输入就是 Stage 1 的编号
python3 skills/risk-check/scripts/risk_check.py --verdict-ids 32,33
```

```
risk  partial/WARNING
  到场的上游 Agent          = ['emotion', 'market']
  Stage 1 覆盖率(应到 5)    = 0.4
  上游交易日是否一致          = True
  最旧证据的年龄(秒)          = 234045
  证据所属交易时段是否仍在进行     = False
  被触发的风险阈值            = []
  上游判断互相矛盾之处          = []
  ⚠ 缺失 [risk.upstream.coverage_incomplete] Stage 1 缺席：sector、news、technical
```

三条否决路径本地验过：

| 组合 | 结果 |
|---|---|
| 否决 + `BUY` | ❌ 铁律 2 拒绝（missing 非空） |
| 否决 + `WAIT` | ❌ 「否决必须体现为 AVOID 或 BLOCK」 |
| 否决 + `AVOID` | ✅ |

真 agent 端到端（`BIGA-20260921-004`）：

```
emotion  PASS     stance='修复'
market   WARNING  stance='缩量上涨'
risk     UNKNOWN  stance='无法判定'
状态：WAIT · 情绪与大盘读数偏暖，但风控因板块/消息/技术面缺席无法判定
```

**risk 没有安静放行** —— 它说「无法判定」并点名了缺席的三个领域。
这一条是整章的重点：

> **一个说不上话的风控，必须让人看出它说不上话。**
> 放行与通过的日志长得一模一样，这是本项目最优先防范的失败模式。

端到端 **87.9s**（三个 Agent），仍在 90s 预算内。

---

## 坑

### 坑 1 · 一个在周末必然全红的指标

risk-check 第一版里有个字段：「超过 120 秒的证据有几条」。
非交易日跑出来 —— **25 条证据全部陈旧**。

因为 `as_of` 停在上一个交易日收盘，距今几十小时。这没有错，但它是**常态**。

> **一个在周末必然全红的指标，等于没有指标。**
> 它每周都会报一次假警报，而假警报的终点是所有人都不再看它。

改成只报两个事实：`max_staleness_sec`（年龄）+ `session_live`（那个交易时段还开着吗）。
「几十小时算不算陈旧」由 Agent 结合后者判断 —— 盘中 120 秒就该警惕，
收盘后几十小时完全正常。

### 坑 2 · 并行判据在**正确行为**上报红了

`risk` 上线后第一次跑，`--parallel-check` 报：

```
emotion 与 risk 的区间**不相交**（间隔 13.9s）  ⇒  ❌ 串行
```

但 risk 是 Stage 2，**它本来就该在 Stage 1 之后跑** —— 那是冻结证据的前提。
检查把所有非 Supervisor 的 Agent 都当成了 Stage 1。

这比漏报更糟：**在正确行为上报红的检查，很快就没人看了**，
等它哪天报的是真问题，也已经被训练成噪音了。

修法不是加个例外，是把**拓扑写进契约层**：

```python
STAGE1_AGENTS = ("market", "sector", "news", "technical", "emotion")
STAGE2_AGENTS = ("risk", "discipline")
```

`risk-check`（算覆盖率）和 `latency_report`（判并行）都从这里读 ——
各写一份名单就会漂，而漂开的表现正是上面那种误报。

#### 顺带得到一条新检查

既然知道了谁是 Stage 2，就能反过来查一件**正确性**的事：

> Stage 2 如果与 Stage 1 **重叠**，说明它读的是还没冻结的证据。

```
🔴 risk（Stage 2）在 10:00:10 就开始了，而 Stage 1 到 10:00:30 才结束
   —— 它读到的证据尚未冻结
```

这不是性能问题。制衡层审的必须是定稿，否则回放时重建不出「当时为什么放行」。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest -q                                    # 251 条

# risk 结构上不采数据
python3 -m pytest tests/test_risk_check.py -q -k NeverCollects

# 否决权三条路径
python3 -m pytest tests/test_contract_behavior.py -q -k Veto

# Stage 2 必须在 Stage 1 之后
python3 tools/verify/latency_report.py --parallel-check
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 一个字段装不下两件事：`verdict` 说数据全不全，否决是判断，必须分开 |
| 2 | 否决这种「权限」字面量只许一处定义 —— 改了词表忘了改判据，它会**静默失效** |
| 3 | 只拦「否决 + BUY」不够：否决被显示成 `WAIT`，同样是软化制衡层 |
| 4 | 制衡层审的必须是**别人据以下结论的那份证据**，自己重采就是审自己的幻觉 |
| 5 | 「不许采数据」要在**结构上**保证（不 import 采集层），不能只写进契约 |
| 6 | 覆盖不足是「无法判定」的理由，**不是「放行」的理由** |
| 7 | 一个在周末必然全红的指标等于没有指标 —— 先问「它什么时候不该红」 |
| 8 | **在正确行为上报红的检查，比漏报更糟** —— 它会被训练成噪音 |
| 9 | 同一份拓扑被两处使用，就把它提到契约层；各写一份必然漂 |
