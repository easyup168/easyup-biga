# 出卡编排 —— 各角色的指令口径与契约要求

> 📄 **常青** · 随代码同步
> **覆盖**：给各 Specialist / risk / 判官的**指令口径与契约要求** ｜
> **不覆盖**：编排怎么执行（那是代码 —— `scripts/orchestrator.py`）、
> 为什么这么设计（设计文档 `docs/design/deterministic-orchestration.md`）、
> 各 Specialist 自己的判断口径（`agents/<名>/AGENTS.md`）

## 🔴 「怎么执行」已经是代码，不在这份里

批 C-II 之前，这份文件顶部有一段 `<!-- PROMPT -->` 提示词块，`bin/biga-card`
从里面**抽出来喂给 `main`**，由 `main` 手工编排（占号 / spawn / 等待 / 传号 / 合成）。

那条路已经拆掉。现在编排是一段**程序** ——
[`scripts/orchestrator.py`](scripts/orchestrator.py) 里的 `DecisionOrchestrator`：
它自己占号、并行 spawn 五个 Specialist、同步等待、spawn risk、spawn 判官、
合成并落库。**顺序 / 等待 / 传号 / 合成全在代码里**，不再用提示词跟 LLM 说一遍。

⇒ 这份文件收缩成**给各角色的指令口径与契约要求**。`orchestrator.py` 的
`_specialist_task` / `_risk_task` / `_synth_task` 发出的，就是下面这些口径的
可执行版本；契约层（`_contract/card.py`）强制其中的硬约束。

> 🔴 **为什么删掉那段提示词**：它是「怎么执行」，而怎么执行现在是代码。
> 同一段知识两份实现，弱的那份会悄悄漂 —— 这就是 L-3。原来那段提示词自己
> 内部就漂过一次（`--decision-id` 到底填不填有三种互相矛盾的说法，产出过一张
> 混血卡）。搬进代码之后不再有这个口子：`orchestrator` 永远用占好的那个号
> 显式合成，`synthesize.py` 也永远优先用证据自带的号（见其 `decision_id` 一段）。
> 「为什么这么设计」的完整理由在设计文档，不在这里复制。

---

## 给 Specialist 的指令口径

`orchestrator._specialist_task` 发给每个 Stage 1 Specialist 的，就是下面这条口径。

### 🔴 日期：非交易日也要能回答，但必须写明是哪天

**指令里绝不出现日期**（除非人明确说了某一天）。走宽松模式，取数据源给出的
**最近一个交易日**，并在回答里**明确写出那是哪一天**。

> 人说「今天」**不等于**「请用今天的日期做严格核对」。

#### 实测：2026-09-20（周日）违反这条的后果

曾要求 Specialist 以 `--date 20260920` 严格核对，它们照做，双双返回 `UNKNOWN`、
`evidence` 为空，产出一张**零证据空卡** —— 而当时 09-18 的完整数据是拿得到的。

> 周六问「今天情绪怎么样」，有用的回答是「最近一个交易日（周五）是这样的：……」，
> 而不是「今天没开盘，我什么都不知道」。后者技术上没错，却把一个**可以回答的
> 问题**变成一张全是缺失项的空卡。**诚实不等于无用 —— 标注日期就够诚实了。**

### 🔴 每个 Specialist 必须带 stance

除了 `verdict`（数据完整度）还要有 `stance`（方向判断）——
它们回答的是两个不同问题：verdict=数据全不全，stance=市场偏哪边。
缺了 stance，合成会**直接拒绝**（契约层强制），并告诉你让谁去补。
方向判断是那个 Specialist 的职责，**别人替它填就等于替它当了一次那个 agent**。

---

## 给 risk（制衡层）的契约要求

risk 是本系统**唯一有否决权**的 agent，依据 Stage 1 的**冻结证据**审查
（不自己重采 —— 否则它审的是自己的幻觉，回放也对不上）。

### 🔴 risk 的 stance → Card status 映射（契约层强制）

| risk 的 stance | Card status |
|---|---|
| `放行` | 按 Stage 1 的证据正常判断 |
| `警示` | 正常判断，但 `synthesis` 里要说明它警示了什么 |
| `否决` | **只能 `AVOID` 或 `BLOCK`** |
| `无法判定` | **不许 `BUY`**（它没看清，不等于没风险） |

给出与之冲突的 status 会被契约层**直接拒绝**。制衡层的否决权不可在合成阶段被软化。
特别地：`无法判定` **不是**放行 —— 一个说不上话的风控，必须让人在卡上看出它说不上话。

---

## 给判官（synthesizer）的契约要求

判官只产出整张卡的三样：`status` / `headline` / `synthesis`。它**不采数据、不产
verdict、不搬运证据**（证据由程序从冻结的 verdict 原件直接组装，不经过它），也
**没有 spawn 能力**（配置上是 `allowAgents=[]` 的叶子节点）。它必须照办上面 risk 的
否决映射。完整角色契约见 [`../../agents/synthesizer/AGENTS.md`](../../agents/synthesizer/AGENTS.md)。
