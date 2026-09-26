# CLAUDE.md

本文件为 Claude Code / OpenClaw 在**本仓库**工作时提供指引。

---

## 这是什么

**EasyUp for BigA 2.0** —— 基于 OpenClaw 的 Multi-Agent A 股短线**决策辅助**系统。
不自动执行交易。产出是一张证据可追溯、可回放的 Decision Card，最终决策由人做。

⚠️ 本仓库是**全新系统**，与同机上另一套长期运行的量化交易实例（`~/.openclaw/workspace`）
**完全独立、零共享可写状态**。两者的关系见「不变式」一节。

⚠️ **「BigA 2.0」里的「2.0」不是语义化版本号**，是产品代际名——上一代是「第一代
EasyUp」，这是从零重建的第二代，名字固定不变，不随开发进度变。真正的语义化版本
号在 `CHANGELOG.md`（`0.0.1` → `0.1.0` → `0.2.0` → 现在的 `[未发布]`）与对应的
git tag 里，跟这个「2.0」无关——两者恰好都带「2.0」纯属巧合，容易看串。

---

## 会话启动先读

1. 本文件
2. `README.md` —— 项目概览与当前状态表
3. `docs/design/architecture.md` —— **架构 SSOT**。任何结构性问题先查它
4. `TODO.md` —— 当前进度与待办
7. `CHANGELOG.md` —— 版本与变更历史
5. `docs/README.md` —— **文档规约**：新写文档前必读
6. `docs/external/2026-09-19-upstream-source-design-v1.md` —— 上游需求文档（只读，不改）

---

## 🔴 三条红线

### R-1 · 所有 OpenClaw 命令必须走 `bin/biga`

```bash
~/.openclaw-biga/bin/biga <subcommand>      # ✅
openclaw <subcommand>                        # ❌ 会读写另一套实例的状态目录
```

`openclaw` CLI **会自动跑 doctor 迁移，不是只读操作**。少打一次 `--profile` 就会改到
另一套系统的 state。wrapper 的存在就是为了消灭这个可能性 —— 本项目作者在调研阶段已实际踩过一次。

### R-2 · 不许占用共享命名空间里的默认名字

**端口分开了、状态目录分开了，还有一类东西是共享的：名字。**
两套实例装在同一个账号下，凡是「按约定取默认名」的地方都可能互相顶掉 ——
而顶掉是**静默**的：文件被覆写，服务照常起来，只是指向了另一套。

#### 已知的三处

| # | 共享命名空间 | 撞车会怎样 | BigA 的做法 |
|---|---|---|---|
| 1 | **nvm 的 bin 目录** | 同机已有实例按「扫 nvm、挑**装了 openclaw 的最高版本**」给它的几十个 systemd unit 注入 PATH。BigA 的 node 版本更高 ⇒ 一旦 `npm i -g openclaw`，那套系统的定时任务会**静默改用 BigA 的 binary**，而那些命令不带 `--profile` | openclaw 装在 `~/.openclaw-biga/runtime/`，nvm 只提供 node |
| 2 | **systemd 用户单元名** | 默认名是 `openclaw-gateway.service`，同机已有实例正用着它。装服务时若走到 override 分支，会**直接覆写正在跑的生产单元** | 靠 profile 后缀取 `openclaw-gateway-biga.service`，装前核对、装后比对生产单元的 sha256 |
| 3 | 端口 / CDP 区间 | 见「路径与端口」一节 | 间距 1000 |

#### 怎么升级 openclaw

```bash
npm i --prefix ~/.openclaw-biga/runtime openclaw@latest
```
❌ 绝不用 `npm i -g openclaw`。

#### 怎么装 / 重启 gateway 服务

```bash
~/.openclaw-biga/bin/biga gateway install   # 写 openclaw-gateway-biga.service
~/.openclaw-biga/bin/biga gateway start
journalctl --user -u openclaw-gateway-biga.service -f
```

🔴 **`OPENCLAW_SYSTEMD_UNIT` 这个环境变量会覆盖 profile 推导出来的名字。**
它一旦从别处漏进 shell（比如复制了生产单元的 env），`gateway install`
就会写到生产那个文件上。装之前确认它是空的。

#### 判据

```bash
python3 tools/verify/isolation.py     # 「共享命名空间」是其中一项
```

⚠️ 第 1 处的守卫放在**另一套仓库**里（受害者是它）——
断言它的 PATH 解析结果没有被改掉。
第 2、3 处 BigA 自己能查，已进 `isolation.py`。

> 🔴 **这条红线是「模式级」的，不是三条具体规则。**
> 第 2 处是 2026-09-21 装服务时才发现的 —— 当时 R-2 只写了 nvm 一处，
> 差点按「R-2 说的是 nvm，这里不是 nvm」放行。
> **再发现第四处，补进上面那张表，别新开一条红线。**

### R-3 · `UNKNOWN` ≠ `PASS`

算不出来必须说算不出来，进 `missing[]` 并显示在 Card 上。
**静默 fail-open 是本项目最优先防范的失败模式** —— 完整清单见 `architecture.md` §9。

---

## 不变式

| # | 不变式 | 验证方式 |
|---|---|---|
| **I-1** | BigA 的任何进程**不得以写模式**打开 `~/.openclaw/` 下的任何文件 | `tools/verify/isolation.py` ✅ |
| **I-2** | BigA 崩溃 / 写坏自己的库 / 占死端口，另一套系统必须毫发无伤 | `kill -9` 演练后对方 gateway pid 不变 |
| **I-5** | BigA **不占用共享命名空间里的默认名**（nvm bin / systemd 单元名 / 端口）| `tools/verify/isolation.py` 的「共享命名空间」一项（三态）|
| **I-3** | 契约（Evidence / AgentVerdict / DecisionCard）**只有一份实现** | AST 全仓扫描 |
| **I-4** | 业务代码里不出现裸 `sqlite3.connect`，一律走 `skills/_store/db.py` | AST 扫描 |

---

## 路径与端口

| 项 | 值 |
|---|---|
| 仓库根 = `main`(Supervisor) 的 workspace | `~/.openclaw-biga/workspace/` |
| profile 状态根 | `~/.openclaw-biga/` |
| openclaw 本体 | `~/.openclaw-biga/runtime/node_modules/openclaw/` |
| CLI | `~/.openclaw-biga/bin/biga` → 软链到仓库内 `bin/biga` |
| agentDir（auth + 会话） | `~/.openclaw-biga/agents/<id>/agent/` ⚠️ **绝不跨 agent 复用** |
| Node | `~/.nvm/versions/node/v24.21.0/`（只有 node，无 openclaw） |
| Gateway 端口 | **19789**（另一套是 18789，间距 1000 ≥ 官方要求的 120） |
| 派生 browser / CDP | 19791 / 19800–19899 |

---

## 已定裁定 —— 不要重新讨论

| # | 议题 | 裁定 |
|---|---|---|
| 1 | 与另一套系统的关系 | 完全独立，自己重新采数据。不挂载对方的库 |
| 2 | 交易执行 | 两套互不干扰；**将来**都有自动下单、最终替代对方 —— 但那是很久以后，**Phase 1 不下单** |
| 3 | 第一阶段范围 | 先装环境，跑通最简功能验证 |
| 4 | Agent 形态 | **真八 Agent**（Supervisor + 7 Specialist），完全照上游文档 |
| 5 | 仓库位置 | `~/.openclaw-biga/workspace/` |
| 6 | Supervisor 的 agentId | 复用 **`main`**，其 workspace = 仓库根（OpenClaw 零配置默认） |
| 7 | 数据层 | **SQLite (WAL)**。切 PostgreSQL 的触发条件写死在 `architecture.md` §5.1 |
| 8 | 模型 | 与另一套一致：`primary anthropic/claude-sonnet-5` + `fallback haiku-4.5`。**Phase 1 不做模型分层** |
| 9 | 仓库可见性 | **Public**。⇒ 见下方「公开仓库纪律」 |
| 10 | 教程 | **每完成一段就先写 `docs/tutorial/` 对应章节，再往下做**。⇒ 见下方「教程纪律」 |
| 12 | 变更记录 | **每完成一段同时更新 `CHANGELOG.md` 的 `[未发布]`**。条目要写「为什么」 |
| 11 | anthropic 凭据 | **与邻居实例共享**（本机无法另行签发）。⇒ 见下方「已知耦合」 |
| 13 | `discipline` agent | **推到 Phase 3。** 它的输入是人的交易行为史，而 BigA 不下单不接账户 ⇒ 现在没有输入源，硬建只能编。Phase 2 只建 7 个 |
| 14 | 分支与 `main` 的关系 | 开发在 `phase2` 等分支上做，**`main` 跟随开发主线**（2026-09-21 改）。<br>🔴 保留的是原裁定真正想防的那半句：**徽章与验收数字必须始终描述一个真被测过的提交** —— 未达成就写 `⬜`/`🔶`，不写 `✅`。<br>⚠️ 原文是「`main` 只保留已验收状态」。改的理由：仓库 Public，`main` 是访客唯一会看的分支，让它长期落后 59 个提交，**展示的是一份过期的自我描述** —— 那比「进行中」更失真 |
| 15 | 事实的归属 | **同一个事实只能有一个生产 agent。** 首次适用：市场宽度归 `market`，`emotion` 删掉。两个消费方各算一遍不会报错，只会某天悄悄给出两个数。<br>⚠️ **本裁定约束 agent，不约束 provider。** 同一个 dataset 允许有主源 / 备用源 / 验证源（数据架构材料 §27）—— 那不是「两套口径」，因为它要求冲突时把快照判成 `QUARANTINED`、**不静默选边**，正是本裁定想防的「某天悄悄给出两个数」的正解。写在这里免得将来被读成矛盾 |
| 16 | 证据的类别与派生值的溯源 | **`Evidence` 必须显式声明自己是哪一类**，不靠 `source` 的字符串前缀猜（今天靠 `source.startswith("derived:")` 判，那是 **L-13**：按字符串形状分类）。三类：`observed`（源数据直接落，指回 raw）/ `derived`（从别的值算出，**必须声明输入**）/ `parameter`（我们自己的设定，两者都不要求）。<br>派生值的输入引用**粒度按字段定**，不搞一刀切：元数据类（如 `coverage_ratio`，输入是「上游 verdict 存不存在」）只引 verdict；值类引支撑那个值的具体 Evidence。<br>🔴 理由是仓库已有的那条原则：**「不硬凑：凑出来的溯源比没有溯源更糟，它会让人以为查得到」**—— 给 `coverage_ratio` 硬塞一串 Evidence id 就是硬凑。<br>⚠️ `parameter` 这一类与既有做法对齐：risk 的 `THRESHOLDS` 本来就不进 evidence、只靠 `CALC_VERSION` 版本化。实测生产库只有 `news.window_min` 一个字段（47 条）属于此类 |
| 17 | Phase 2 的延迟门槛 | **盘中 Decision SLO = 180s，是 Phase 2 唯一的正式延迟门槛**；唯一定义在 `tools/verify/latency_report.py::DEFAULT_BUDGET_MS`（180s 的推导过程也在那儿）。<br>**盘后运行不纳入 Phase 2 SLO** —— 收盘后一小时快讯量翻倍（184 vs ~70 条），那是**另一种负载形态**，不是同一条线上的超标。它归后续 Data Platform / Post-close Job Profile 单独定义。<br>🔴 这条裁定要防的是把出口条件写成一个**动的**数：盘后一超就重推一次预算，等于让被考核的负载自己决定考核线。<br>⚠️ 历史文档里的 `90s` / `105s` 是早期假设与旧实测对照，保留用于解释为什么必须重推，**都不再是验收线**

---

## ⚠️ 已知耦合：anthropic 凭据与邻居实例共享

**这是本项目唯一一处对「完全独立」（裁定 1）的让步，是有意识做出的，不是疏漏。**

### 🔴 这条耦合会怎样咬人

> 凭据失效时，**两套系统同时停**。
> 而你的第一反应会是「BigA 这个新系统又出问题了」—— **排查方向天生是错的。**

所以认证类故障的排查顺序**必须**是：

1. 先看 BigA 自己的 profile：`~/.openclaw-biga/bin/biga models auth list --agent main`
2. 再确认邻居侧是否也在报同样的错（用**它自己的** CLI —— 红线 R-1）
3. 两边都报 ⇒ 是凭据本身的问题，不是 BigA 的问题

### 边界：复制的是凭据，不是状态

- BigA 存的是**自己 agentDir 里的一份副本**，不挂载、不软链邻居的任何文件
- 不变式 I-1（不以写模式打开 `~/.openclaw/`）**未被破坏** —— 取值是一次性读取
- 两套仍然各自采数据、各自落库

### 退出条件

Phase 3 之前必须换成 BigA 自己的凭据。触发条件任一成立即换：
① 能够申请到独立凭据 ② 出现第一次「因这条耦合导致的误判排查」

### 🔴 具体怎么配的，**不写在这个仓库里**

本机的运行时选择、凭据来源、以及组织侧的限制，属于**这台机器与这个账号的
特殊情况** —— 是个人/组织信息，不该出现在一个公开仓库里，
**即使是以「踩坑记录」的形式**。

⇒ 记在仓库外：`~/.openclaw-biga/AUTH-NOTES.local.md`。

仓库里只保留**可迁移的工程结论**（见 `docs/tutorial/07-end-to-end.md`）。
由 `tools/verify/audit_public.sh` 的第 9 项「环境/账号策略」守着。

## 开发流程

🔴 **本仓库用仓库内的 `.claude/skills/dev-workflow/`，不要用用户级那份。**

用户级 `~/.claude/skills/dev-workflow` 是**邻居项目**的流程 ——
它的 Track D 要求同时改 `~/.openclaw/cron/jobs.json`，**照做会直接破坏 I-1**。

🔴 **不要靠 `/dev-workflow` 自动选中仓库内那份 —— 实测它命中的是用户级那份。**
2026-09-25（P3-0 开工）实测：`/dev-workflow` 加载进来的 base dir 是
`~/.claude/skills/dev-workflow`，正文满篇是邻居项目的目录与远端部署流程。
本句原文写的是「项目级那份优先，`/dev-workflow` 会命中它」——**那是假的**，
而它的危害正是这类断言的典型形状：读的人以为有机制兜着，于是不再核对。

⇒ 动手前**直接读** `.claude/skills/dev-workflow/SKILL.md`，
或者加载完先确认 base dir 落在仓库内。

十条要点（详见那份文件）：设计先探活 / 加东西的**五个**必答问题（第五问
「这份清单有没有孪生、谁派生谁」是外部评审加的）/
守卫用探针验证会红 / 重构要两个独立信号 / **诊断先看工具调用序列再改提示词** /
端到端必须开新会话且等结算 / 指标先问「什么时候不该红」/ 报错要指路 /
文档与 CHANGELOG 同 commit / 明确列出哪些邻居流程不适用。

---

## 文档纪律

新写任何文档之前先读 `docs/README.md`。规约由 `tests/test_docs_convention.py` 强制。

一句话版本：**按生命周期分类，不按主题分类。**

| 目录 | 类别 | 谁该更新它 |
|---|---|---|
| `docs/design/` | 常青 / 阶段 | 改代码的人 |
| `docs/tutorial/` | 过程 | 写完即冻结 |
| `docs/guide/` | 操作 | 跑不通就是错的 |
| `docs/external/` | 只读 | **永不修改** |

两条踩出来的硬规矩：

1. **常青文档不许按阶段/步骤切分** —— `phase2-market.md` 写于 2.1 开工前，
   做完就过期了，而名字看起来像「Phase 2 的设计文档」
2. **阶段完成后要冻结并搬出常青文档** —— Phase 1 的设计原本是
   `architecture.md` §11，导致那份文档顶部长期写着「设计中，未开工」，
   而那时 Phase 2 都做完两步了

每份文档开头必须写 **覆盖什么 / 不覆盖什么**。
不写边界，内容就会往**最近的那份**里落 —— 这是本次混乱的根因。

---

## 变更记录纪律

`CHANGELOG.md` 跟踪版本与变更历史，格式参考 Keep a Changelog。

- 每完成一段工作，往 `[未发布]` 追加条目，**与教程章节同步**
- 分类只用六种：`新增` / `变更` / `修复` / `移除` / `废弃` / `安全`
- 🔴 **写「为什么」，不只是「做了什么」** —— 三个月后有价值的是前者。
  「加了触发器」没用，「只追加由触发器强制，因为代码约定只能约束记得它的人」才有用
- 已知问题单列一节，**不要藏在正文里**

---

## 教程纪律

本仓库同时是一份**开源开发教程**（`docs/tutorial/`）。

🔴 **每完成一段工作，先写该段的教程章节，再开始下一段。** 不要攒到最后补。

原因：教程的价值在于**真实的建造过程** —— 踩过的坑、放弃的方案、为什么这条命令有毒。
这些细节在当下是活的，一周后回忆就只剩「装上了、能跑」。

每章的固定结构：

| 小节 | 内容 |
|---|---|
| 目标 / 产出 | 这一章做完得到什么 |
| 为什么这么做 | **占篇幅最大的部分** —— 方案对比与取舍，不是命令清单 |
| 执行 | 真跑过的命令 + 真实输出 |
| 坑 | 本段实际遇到的失败与错误信息 |
| 验证 | **可执行的**命令 + 预期结果，跑不出来就不算完成 |
| 本章要点 | 一张表，每条一句话，可脱离上下文阅读 |

写作口径：

- 🔴 **教程只写正常操作。** 与同机已有服务相关的**操作**不进教程 ——
  隔离本身（独立目录 / 独立端口 / 崩溃不波及）是通用工程内容，要写；
  「那套系统是干什么的、怎么配的、我怎么读了它的配置」不写。
  措辞一律用中性的「同机已有实例」，不要拟人化成「邻居」
  ⚠️ 这条**推翻了原来的写法**（原文说教程的差异化在于「有邻居的机器」）。
  改的理由：那让教程变成一份共存故障记录，而共存细节属于本机情况
- 通用原则单独用引用块标出（`> 通用原则：...`），便于读者抽走复用
- 章节索引在 `docs/tutorial/README.md`，写完一章就更新那张状态表
- 同样受下方「公开仓库纪律」约束 —— 教程尤其容易顺手写出可识别细节

---

## 公开仓库纪律

本仓库公开。**提交前必须确认不含**：

- 任何密钥 / token / appId / appSecret
- 真实用户家目录路径（用 `$HOME` 或 `~`）
- 另一套实盘系统的**可识别细节**：内部模块名、文件路径、具体缺陷百分比、
  持续天数、绩效数字、账户信息

🔴 **描述一条「不要写 X」的规则时，不要把 X 抄进去。** 已经犯过**五次**：

| # | 在哪里 | 抄进去的是 |
|---|---|---|
| 1 | 记录「别泄露邮箱」的 CHANGELOG 条目 | 邮箱本身 |
| 2 | 把审查脚本固化进 `TODO.md` | 脚本自己的正则（从此永远自匹配 3 处） |
| 3 | 开发流程里「哪些邻居内容不适用」那张表 | 邻居的模块名（被 pre-push 拦下） |
| 4 | 记录「审查脚本新增 IM 凭据检查」的 CHANGELOG 条目 | 那几个变量名本身（**被新加的那一项当场拦下**）|
| 5 | 写「审查会抓到 64 位十六进制」的探针说明（2026-09-22） | 网关令牌那个参数名本身（**又被同一条检查当场拦下**）|

五次都是同一个形状：**举例子的时候用了真值。**
第 4 次尤其说明问题 —— 它发生在**写那条检查的同一次提交里**。 用「它引用的那些数据库与脚本名」
这种指代就够了 —— 读者需要知道的是**类别**，不是**实例**。

⚠️ 邻居可识别细节容易漏，因为它**不是密钥**。
`architecture.md` §9 已按此改写过一遍：保留工程教训与防护机制，抽掉具体数字与内部标识。
好消息是审查脚本**真的会抓到它** —— 第 3 次是「邻居可识别细节」拦下的，第 4、5 次是「IM 应用凭据」与「Gateway token」拦下的。
写新文档时沿用同样的标准。

### 审查只有一份实现，且已接成 hook

```bash
tools/verify/audit_public.sh --worktree   # commit 之前：扫工作区
tools/verify/audit_public.sh              # 手工体检：扫全历史
git config core.hooksPath tools/git-hooks # 装 pre-push（克隆后每人执行一次）
```

`pre-push` hook 会在每次推送前扫**本次新增的提交**，不过就拦下。

🔴 **仓库已 Public ⇒ push 即发布。** 原来的纪律是「转可见性前跑一遍审查」，
那句话隐含一个不再存在的窗口（先推上去、等转公开时再检查）。
已公开的内容撤不回来 —— 删分支也可能留在 fork、缓存与镜像里。

⚠️ 这**十一项**检查**只有一份实现**（`tools/verify/audit_public.sh`）。
本节曾另有一段「快速自查」用另一套正则，那就是 L-3 的第二套口径 ——
**改了一份忘了另一份时，剩下那份仍然报绿。**

---

## 开发约定

### 代码

1. **契约唯一实现** —— `skills/_contract/` 是 Evidence / AgentVerdict / DecisionCard 的唯一定义。
   任何 agent 或脚本不得自建第二套。
2. **DB 唯一入口** —— `skills/_store/db.py`。业务代码不许出现裸 `sqlite3.connect`。
   这样将来切 PostgreSQL 只改一个文件。
3. **Agent 不做算术** —— 任何数字必须来自 skill 返回值并附 `Evidence`。
   LLM 算错不报错，且不可回放。
4. **skill 不做判断性归类** —— skill 返回事实与分数，「这算不算强势」的判断留给 agent。
   判断逻辑散进 skill = 产生第二套口径。
5. **raw 层永不改写** —— 采集原样落盘，状态变更一律追加而非 `UPDATE`，
   让「当时看到的」可重建。
6. **时间一律北京时间（`Asia/Shanghai`）** —— 写库、上卡、打日志、写文档全部用它。
   我们自己产生的时间走 `_contract.now_cn()`，不用 `datetime.now()`（跟系统时区走）。

   ⚠️ **唯一的例外来自外部**：OpenClaw 运行时的 trajectory 里 `ts` 是 **UTC**。
   转换**只在 `_store/runtime.py` 的读取边界做一次**，消费方拿到的已经是北京时间 ——
   不要在各处自己 `.astimezone()`。

   🔴 这类 bug 的形状是**差 8 小时但仍然是个合法时刻**，不报错。
   实测踩过：同一件事在延迟报告里是 08:00、在临时脚本里是 00:00，白白对不上号。

### 验收数字与徽章

🔴 **`tools/verify/sync_test_count.sh` 只在干净树上跑。**

它把实测条数同步进 `README.md` / `CLAUDE.md` / `docs/guide/review-prompt.md`。
工具本身没问题——它忠实同步「此刻实测」。问题在于**「此刻实测」取决于工作区里
有什么**，而不只是取决于被提交的那棵树：

`tests/test_docs_convention.py` 的两条检查是**按文件参数化**的（`docs/` 下每多
一个 `.md`，就多两条 test case）。于是工作区里任何**未跟踪**的 `.md`——哪怕它被
`.gitignore` 挡着、永远不会进仓库——都会把条数抬高。

实测踩过（2026-09-23）：主工作区当时有一批未提交的材料躺在 `docs/external/` 下，
同步出来的数比干净 checkout 高 16 条。那个数被提交了，而**任何人 clone 下来都跑
不出它**。

⇒ 同步之前先 `git status --short`；不确定就在一个新建 worktree 里跑一次对照。

**为什么这条值得单独写**：它不是「手滑」，而是裁定 14 真正想防的那件事——
**徽章与验收数字必须始终描述一个真被测过的提交**。一个 clone 不出来的数字，
比写 `⬜` 更糟：`⬜` 诚实，它不诚实。

✅ **2026-09-23 已补上守卫**（`cee4045`）：`test_文档里的测试条数与实测一致`
现在只数「参数不是**未跟踪** `docs/*.md`」的 test case ——
条数因此只描述**被提交的那棵树**，在脏工作区跑同步也得到同一个数。

⚠️ 未跟踪文档**照样受其余检查**（新写的文档漏了类别头，提交前仍然查得出来），
只是不计入条数。关掉那道检查换条数干净 = 拿一道有用的守卫换一个数字。

⇒ 所以上面那条纪律现在是「建议」而不是「唯一防线」——
但仍然建议同步前 `git status --short` 看一眼，守卫只管条数，不管你别的手滑。

🔴 **2026-09-25 踩到同一条纪律的另一个方向：这次是「少算」，不是「多算」。**

那道守卫排除的是**未跟踪**的 `docs/*.md`。它假设「未跟踪 ⇒ 不会进仓库」——
对 2026-09-23 那批躺在本地的材料成立，对**刚写好、马上要提交的新文档不成立**。

实测：P3-0 收尾时在脏树上同步，教程新章节当时还未跟踪 ⇒ 它的 4 条参数化 case
被排除，同步出 `1908`。提交之后那份文档变成已跟踪，实测立刻变成 `1913` ——
**刚同步完的数字在下一次 `pytest` 就红了**。

⇒ 正确顺序是 **先 commit，再同步，再 amend**，不是「同步完一起 commit」。
两个方向合起来才是完整的那句话：**同步必须跑在「这棵树就是要发布的那棵树」的时刻**。

### Agent

角色契约写在**各自 workspace 的 `AGENTS.md`**，不是 `SOUL.md`。
OpenClaw 官方明确：**spawned session 不加载 `SOUL.md` / `IDENTITY.md`**。
只有直接与人对话的 `main` 才额外需要那两个文件。

⚠️ `agents.entries.*.skills` 是**替换语义，不与 `agents.defaults.skills` 合并**。
给某个 agent 配白名单时必须把共享基线整份抄进去，否则静默丢技能。

⚠️ `agents/` 子目录**按需创建，不预建空目录** —— 避免造出「建了但无消费方」的占位组件。

### 新增任何写数据的模块

必须同时存在被证明的**读取方**。判据是**调度命令的字面量**，不是「谁调用了它」。
这是 `architecture.md` §9 L-1 要防的头号失败模式。

---

## 常用命令

```bash
BIGA=~/.openclaw-biga/bin/biga

$BIGA --version                 # 版本
$BIGA agents list               # agent 名册（应显示 workspace 在 .openclaw-biga 下）
$BIGA agents list --bindings    # 含路由绑定
$BIGA chat                      # 本地 TUI（Phase 1 唯一交互入口，未接 IM）
$BIGA gateway --port 19789      # 前台起 gateway
$BIGA doctor                    # 健康检查
```

### 诊断「为什么这么慢」

```bash
python3 tools/verify/agent_trace.py                      # 各 agent 最近几次会话的调用次数
python3 tools/verify/agent_trace.py --agent market -n 1  # 展开最近一次的调用序列
python3 tools/verify/latency_report.py --parallel-check  # 延迟/成本分解 + Stage 1 并行判据
```

🔴 **先看工具调用序列，再改提示词。** 本项目两次最有价值的诊断都来自这个动作：
Supervisor 那 159 秒里有 47 秒零工具调用（在重打 JSON）；
Specialist 加 stance 后慢一倍，是因为 62 秒都在 grep 源码找词表。
两次的第一反应都是「提示词写得不好」，**两次都错**。

> Agent 的「慢」，多数时候是它在**找东西**，不是在想事情。

### 隔离自检（改动环境后跑一次）

```bash
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 记下
$BIGA agents list
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 必须没变
[ ! -e ~/.nvm/versions/node/v24.21.0/bin/openclaw ] && echo "✅ R-2 未被破坏"
```

---

## 当前状态

**Phase 1 已验收（在 `main`）。Phase 2 代码收口完成，出口条件 7/8 —— 只差条件 3。**

| | 状态 |
|---|---|
| Agent | **7 / 8**。`main` + Stage 1 五个（market/sector/technical/emotion/news）+ Stage 2 `risk`。<br>第 8 个 `discipline` **故意不建**（裁定 13：没有输入源） |
| 契约 / 数据层 | `_contract` 四条铁律构造时拒绝；`_store` schema **v22**，十一张表，只追加由触发器强制（v4 时这句话是**假的** —— 分配器没有触发器，五取四；v6 起 `agent_verdicts` 加 `kind` 列区分事实/判断/合体；v9 收敛 `run_id` 三同名；v10 `run_id` capture 贯穿全链；v11 加 `ux_fact_per_task_agent`——一个 `(task_id, agent)` 至多一份 fact 原件（批 F）；v12 加外发通知 `notification_outbox` / `notification_deliveries` 两张只追加表（批 G-I）；v13 给 `raw_market_snapshot` 加 `raw_text`，`content_sha256` 改基于原始响应文本算（批 I）；v14 给 `decision_ids` 加 `trigger_id` 列做入站幂等键（批 G-II）；v15 加 `fact_trading_calendar`——**第一张真实的 `fact_*` 表**，深交所官方日历，`market_is_open` 认节假日了（批 L）；v16 加 Run Provenance 三列 + `ux_evidence_set_per_run`——一张在线卡属于哪次执行、基于哪份切片，现在是一句 SQL 而不是解 card_json（批 N）；v17 把 Fact 唯一约束从 `(task_id, agent)` 拆成按 run 分区的两条——「一次执行一份事实」，同一决策的第二个 run 因此能写自己的那份（批 O）；v18 `ux_evidence_sets_run`——一次 run 至多一个 EvidenceSet（P1-1）；v19 `agent_runs.provenance_mode`——区分在线执行行与历史行，Run 级 spawn 核验的三态判据（P1-2）；v20 撤掉 v18——它与 v16 的 `ux_evidence_set_per_run` **逐字相同**，一条不变量两个名字就是 L-3（照抄评审给的建议索引、没先 grep 一遍既有约束）；v21 `ux_online_agent_run_once`——一次 run 一个 agent 至多一条在线账本行，多条时一条真 `runtime_run_id` 会把另一条伪造的盖住；v22 `decision_runs.expected_spawn_agents`（冻结「这次该启动谁」，**不等于**卡上的 expected_roster）+ `ux_online_runtime_run_id`（一个运行时 id 不许给两行背书）） |
| 测试 | 2139 条 |
| 端到端 | 盘中 172.6s / $1.20（Decision SLO **180s**，裁定 17）；盘后 198s / $1.37 —— **不纳入 Phase 2 SLO**，收盘后一小时快讯量翻倍（184 vs ~70 条）是另一种负载形态 |
| 隔离 | `tools/verify/isolation.py` **三态**（`UNKNOWN` 不计入通过）；判据由 `tests/test_isolation.py` 钉住 |
| 外部评审 | 两份，共 29 条，**全部处理完**（见 `TODO.md` 的合并台账）|

🔴 **只差出口条件 3（一次真实 Risk Veto），而它等的是行情、不是开发。**
盘中 `emotion`/`news` 报「今天」、日线类报「上一交易日」⇒ `risk` 每次「无法判定」——
这条根因已随交易日历落地（v15 / `bin/biga-calendar`）解决，剩下的是等盘面真的
命中 risk 的阈值之一。详见 `docs/design/phase-2-specialists.md` §3.11 与 §4。

详见 `TODO.md` 与 `docs/design/phase-2-specialists.md`。
