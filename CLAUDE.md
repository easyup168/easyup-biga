# CLAUDE.md

本文件为 Claude Code / OpenClaw 在**本仓库**工作时提供指引。

---

## 这是什么

**EasyUp for BigA 2.0** —— 基于 OpenClaw 的 Multi-Agent A 股短线**决策辅助**系统。
不自动执行交易。产出是一张证据可追溯、可回放的 Decision Card，最终决策由人做。

⚠️ 本仓库是**全新系统**，与同机上另一套长期运行的量化交易实例（`~/.openclaw/workspace`）
**完全独立、零共享可写状态**。两者的关系见「不变式」一节。

---

## 会话启动先读

1. 本文件
2. `README.md` —— 项目概览与当前状态表
3. `docs/design/architecture.md` —— **架构 SSOT**。任何结构性问题先查它
4. `TODO.md` —— 当前进度与待办
5. `CHANGELOG.md` —— 版本与变更历史
5. `docs/reference/source-design-v1.md` —— 上游需求文档（只读，不改）

---

## 🔴 三条红线

### R-1 · 所有 OpenClaw 命令必须走 `bin/biga`

```bash
~/.openclaw-biga/bin/biga <subcommand>      # ✅
openclaw <subcommand>                        # ❌ 会读写另一套实例的状态目录
```

`openclaw` CLI **会自动跑 doctor 迁移，不是只读操作**。少打一次 `--profile` 就会改到
另一套系统的 state。wrapper 的存在就是为了消灭这个可能性 —— 本项目作者在调研阶段已实际踩过一次。

### R-2 · openclaw 本体绝不能装进 nvm 的 bin 目录

本机另一套实例用「扫 nvm、挑**装了 openclaw 的最高版本**」的方式给它的几十个 systemd unit 注入 PATH。
BigA 的 node（`v24.21.0`）比它的（`v24.18.0`）高 —— 一旦在 BigA 的 node 下跑
`npm i -g openclaw`，那套系统的 cron 会**静默改用 BigA 的 binary**。

所以：**nvm 只提供 node 本体**，openclaw 装在 `~/.openclaw-biga/runtime/`。

升级 openclaw 用：
```bash
npm i --prefix ~/.openclaw-biga/runtime openclaw@latest
```
❌ 绝不用 `npm i -g openclaw`。

守卫测试放在**另一套仓库**里（受害者是它）—— 断言它的 PATH 解析结果没有被改掉。

### R-3 · `UNKNOWN` ≠ `PASS`

算不出来必须说算不出来，进 `missing[]` 并显示在 Card 上。
**静默 fail-open 是本项目最优先防范的失败模式** —— 完整清单见 `architecture.md` §9。

---

## 不变式

| # | 不变式 | 验证方式 |
|---|---|---|
| **I-1** | BigA 的任何进程**不得以写模式**打开 `~/.openclaw/` 下的任何文件 | `tools/verify/isolation.py`（待建） |
| **I-2** | BigA 崩溃 / 写坏自己的库 / 占死端口，另一套系统必须毫发无伤 | `kill -9` 演练后对方 gateway pid 不变 |
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
| 11 | anthropic 凭据 | **复用邻居实例的 *****（*** 已禁 `***`）。⇒ 见下方「已知耦合」 |
| 13 | `discipline` agent | **推到 Phase 3。** 它的输入是人的交易行为史，而 BigA 不下单不接账户 ⇒ 现在没有输入源，硬建只能编。Phase 2 只建 7 个 |
| 14 | Phase 2 的开发分支 | **独立分支 `phase2`。** `main` 只保留已验收状态 —— 徽章与验收数字必须始终描述一个真被测过的提交 |

---

## ⚠️ 已知耦合：anthropic 凭据来自邻居实例

**这是本项目唯一一处对「完全独立」（裁定 1）的让步，是有意识做出的，不是疏漏。**

### 事实

BigA 的 anthropic token **不是自己申请的**，而是从同机另一套实例复制过来的同一条
***。profile id 刻意命名为 `anthropic:***`，
让它在 `models auth list` 里自曝身份。

### 为什么

本机 Claude 账号是 ***，`claude ***`（生成长期 token）已被***。
邻居那条 token 生成于***之前，是账号下**唯一还能用**的 anthropic 凭据。
备选方案（管理员开 API key / 换 provider）各有更大的代价，见 `TODO.md` 的裁定记录。

### 🔴 这条耦合会怎样咬人

> token 轮换或失效时，**两套系统同时停**。
> 而你的第一反应会是「BigA 这个新系统又出问题了」—— **排查方向天生是错的。**

所以认证类故障的排查顺序**必须**是：

1. 先跑 `~/.openclaw-biga/bin/biga models auth list --agent main`
   —— 看到 `anthropic:***` 就立刻想起这一节
2. 再确认邻居侧是否也在报同样的错（用**它自己的** CLI，不要用 BigA 的 —— 红线 R-1）
3. 两边都报 ⇒ 是 token 本身的问题，不是 BigA 的问题

### 边界：复制的是凭据，不是状态

- BigA 存的是**自己 agentDir 里的一份副本**，不挂载、不软链邻居的任何文件
- 不变式 I-1（不以写模式打开 `~/.openclaw/`）**未被破坏** —— 取值是一次性读取
- 两套仍然各自采数据、各自落库

### 退出条件

Phase 3 之前必须换成 BigA 自己的凭据。触发条件任一成立即换：

1. 管理员批下了独立的 Anthropic API key
2. *** 策略放开 `***`
3. 出现第一次「因这条耦合导致的误判排查」

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

- 教程面向「在一台有邻居的机器上施工」这个真实约束，这是它区别于普通教程的地方
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

⚠️ 最后一条最容易漏 —— 它**不是密钥，脱敏扫描不会报**。
`architecture.md` §9 已按此改写过一遍：保留工程教训与防护机制，抽掉具体数字与内部标识。
写新文档时沿用同样的标准。

快速自查：
```bash
grep -rniE 'cli_[a-z0-9]{8}|/home/[a-z]+|api[_-]?key *[:=]|appsecret' --include='*.md' --include='*.py' .
```

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

### 隔离自检（改动环境后跑一次）

```bash
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 记下
$BIGA agents list
stat -c '%y' ~/.openclaw/state/openclaw.sqlite     # 必须没变
[ ! -e ~/.nvm/versions/node/v24.21.0/bin/openclaw ] && echo "✅ R-2 未被破坏"
```

---

## 当前状态

**Phase 1 · 环境已就绪，业务代码尚未开始。**

已完成：隔离安装（node v24.21.0 + openclaw 2026.9.5）、`biga` wrapper、
workspace 骨架、git、设计与安装文档。

未开始：`biga setup`（profile 初始化）、契约层、数据层、任何 agent。

详见 `TODO.md`。
