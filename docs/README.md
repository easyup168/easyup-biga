# 文档规约

> 📄 **常青** · 随目录结构同步
> **覆盖**：文档放哪、叫什么、谁该更新它 ｜ **不覆盖**：任何业务内容

新写一份文档之前先读这一页。规则由 `tests/test_docs_convention.py` 强制。

---

## 🔴 按「生命周期」分类，不按「主题」分类

主题会重叠（market 的设计算 design 还是 tutorial？），生命周期不会。
而真正会出问题的恰恰是生命周期 —— **一份该随代码更新的文档被当成一次性产物放着**。

| 目录 | 类别 | 生命周期 | 文件名 |
|---|---|---|---|
| `design/` | **常青** | 永远描述**当前**状态，改代码就要改它 | `<主题>.md` |
| `design/` | **阶段** | 进行中常青，**完成后冻结** | `phase-<N>-<主题>.md` |
| `tutorial/` | **过程** | 写完即冻结，只许追加「⏩ 后续变动」指针 | `NN-<主题>.md` |
| `guide/` | **操作** | 随环境更新；**跑不通就是错的** | `<动作>.md` |
| `external/` | **只读** | **永不修改** —— 别人的东西 | `YYYY-MM-DD-<来源>-<主题>.md` |

### 阶段文档：`phase-<N>-<主题>.md`，主题不能省

`phase2.md` 这种名字答不出「**什么的** phase 2」。
一年后打开仓库的人（包括你自己）只会看到一串数字。

```
✅ phase-1-walking-skeleton.md
✅ phase-2-specialists.md
❌ phase2.md          什么的 phase 2？
❌ phase2-market.md   按步骤切 —— 见下一节
```

#### 🔴 阶段完成后要冻结，并从常青文档里搬出来

Phase 1 的设计原本是 `architecture.md` 的 §11 —— 一段**历史**躺在**常青**文档里。
后果是实测过的：那份文档顶部长期写着「设计中，未开工」，
而那时 Phase 2 都做完两步了。**读者没有办法判断哪些还作数。**

⇒ 阶段结束时：抽成独立文档、标注「已完成并冻结」、在原处留一行指针。

### 为什么只有 `external/` 的文件名带日期

我们自己的文档，历史在 git 里。文件名带日期或版本号，等于邀请别人新建
`xxx-v2.md` 而把旧的留在原地 —— 于是同一件事有两份说法，
**而读者没有办法知道哪份是现在的**。

`external/` 反过来：它是**别人在某个时刻的想法的快照**，日期就是它的一部分。
它永远不会被更新，所以文件名里钉死日期反而最诚实。

---

## 🔴 常青文档不许按阶段/步骤切分

一份 `phase-2-specialists.md` 覆盖整个 Phase 2。**不要** `phase2-market.md`、`phase2-risk.md`。

这条是踩出来的：`phase2-market.md` 写于 2.1 开工前，2.1 做完之后
后来建的七样东西（`agent_verdicts` / `stance` / `raw_hash` / `MissingItem` /
`verdict_ref` / `risk` / Stage 2）它一样都没有 —— 而它的名字看起来像
「Phase 2 的设计文档」。

> **按步骤切分常青文档，就会有 N 份各自过期的文档。**

施工过程要留档，写进 `tutorial/` —— 那里的文档**本来就是冻结的**，过期是它的正常状态。

---

## 每份文档开头必须声明覆盖范围

```markdown
> 📄 **常青** · 随代码同步
> **覆盖**：Agent 拓扑、通信契约、数据架构 ｜ **不覆盖**：阶段进度（见 phase-2-specialists.md）
```

五个类别标记：`常青` / `阶段` / `过程` / `操作` / `只读`。

为什么「**不覆盖**」也要写：这次的根因不是缺一份文档，
是没人说清每份文档的边界 —— 于是内容就往**最近的那份**里落。
写明边界，才有地方可指。

---

## 当前文档

| 文档 | 类别 | 覆盖 |
|---|---|---|
| [`design/architecture.md`](design/architecture.md) | 常青 | 架构 SSOT：拓扑、契约、数据层、失败模式清单 |
| [`design/phase-1-walking-skeleton.md`](design/phase-1-walking-skeleton.md) | 阶段 · **已冻结** | Phase 1：范围、步骤、验收与最终结果 |
| [`design/phase-2-specialists.md`](design/phase-2-specialists.md) | 阶段 · 进行中 | Phase 2：范围、步骤、关键取舍、出口条件 |
| [`guide/install.md`](guide/install.md) | 操作 | 在已有 OpenClaw 实例旁并排装第二套 |
| [`guide/phase1-kickoff-prompt.md`](guide/phase1-kickoff-prompt.md) | 操作 | Phase 1 启动提示词（自包含，可直接粘贴） |
| [`tutorial/`](tutorial/README.md) | 过程 | 开发教程 13 章，与代码同步推进 |
| [`external/`](external/) | 只读 | 上游需求文档、外部设计评审 |

仓库根还有四份，不在 `docs/` 下但同样受本规约约束：

| 文档 | 类别 | 覆盖 |
|---|---|---|
| `CLAUDE.md` | 常青 | 红线、不变式、裁定、开发约定 |
| `README.md` | 常青 | 项目概览与当前状态 |
| `TODO.md` | 常青 | 勾选状态（**不写为什么** —— 那在设计文档里） |
| `CHANGELOG.md` | 追加 | 变更历史，每条写「为什么」 |
