# 第 36 章 · 飞书出卡变成结构性 trigger（批 G-II）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 G-II（Inbound Trigger）的建造过程 —— 飞书「出卡」怎么从
> 一次自由对话变成一个 main 全程不参与路由的结构性命令。
> **不覆盖**：批 G-I 的外发通道（第 34 章）、飞书渠道本身怎么装。

## 目标 / 产出

批 G-I 让卡跑完能**推**回飞书。这一章做反方向、也是风险大得多的那半：让飞书能
**触发**出卡 —— 但**不经过 main 的自由判断**。做完得到：

- `/card` 命令（飞书 owner 发）→ 直达一个工具 → 幂等受理 → 异步拉起出卡；
- `decision_ids.trigger_id`（schema v13）做入站幂等键，同一个飞书 event 重投不重跑；
- `bin/biga-card` 长出一条异步入口，人工 CLI 的同步体验一字不变；
- `feishu_deliverer.py` 把批 G-I 的 `Deliverer` 接口接上真飞书 API；
- `deploy/openclaw/` + `apply_config.py`：出卡流水线 agent 禁 `ask_user`、命令面
  与工具服务进配置，全部经 `bin/biga` 落地（R-2 安全）。

## 为什么这么做（占篇幅最大的一节）

### 一、根子不是「让 main 更听话」，是「飞书触发根本不该经过 main」

2026-09-21 两次事故（19:31 四个孤儿 spawn、21:03 出卡递归 L-14）根子相同：
**入口在 prompt 层面，不在代码层面**。L-14 那次的事后总结已经说过——契约文字
（AGENTS.md 写「不要自己编排」）这个量级**不够**。批 C-II 把 main 移出了
`bin/biga-card` 的转发链，但飞书这条路当时还没建，唯一挡着「main 收到『出一张卡』
就自己拼一套 spawn」的，仍然只是 AGENTS.md 里的一句话。

所以这一批的立场从一开始就定死：**不是教 main 认出出卡请求再转发，是 main 压根
收不到这类请求。** 反事实检验（探针 P2）把这句话变成可测的：**把 main 的 system
prompt 整个清空，`/card` 还能不能正常出卡？** 能，才算「结构性」。

### 二、探活先纠正了三个隐含假设

分发提示词让「开工第一件事读设计探活」。照做，代码库现状纠正了三处想当然：

**飞书是 websocket 长连接，不是 webhook。** 事件从网关进程内部到达，没有一个我
能指向的 HTTP 端点。⇒ 「起一个自己的 webhook server 绕开 main」这条路根本不存在。
结构性拦截**只能表达成配置**（让某类消息不走 main）+ OpenClaw 自己的命令层。

**仓库里一行飞书代码都没有。** 真实飞书接入（渠道配置、凭据、装好的插件）全在
仓库外的运行时配置里。⇒ 这一批要造的是**第一个真正把 `origin="feishu"` 填上**的
调用点，不是接一个半成品。

**`bin/biga-card` 今天完全同步。** 全文一次 `wait "$_ORCH_PID"`，没有任何「快速
ACK、后台完成」的半成品可复用。而一次出卡要 170~200 秒，塞不进一次命令的 ACK
窗口。⇒ **异步执行本身**是这一批最大的一块新增基础设施，不是「把已有异步接上飞书」。

### 三、结构性拦截怎么落地：`command-dispatch: tool`

翻 OpenClaw 文档翻到关键一句（`docs/tools/slash-commands.md`）：技能可以声明
`command-dispatch: tool`，让斜杠命令**直接派发到一个工具、绕过 model**；而且
「来自白名单发送者的纯命令消息**绕过 queue + model**」。

这正是要找的结构。于是 `/card` 做成一个技能（`skills/card/SKILL.md`）：

```yaml
command-dispatch: tool          # 命令直达工具，不经过 model
command-tool: biga_card_trigger # 派发到这个工具
command-arg-mode: raw           # 原样把参数给工具
disable-model-invocation: true  # model 自己选不到它，只能人显式 /card
```

owner 发 `/card` → OpenClaw 命令层识别 → 直接调 `biga_card_trigger` 工具 →
**main 的 LLM 没有一步**。这比外部材料自己推荐的拓扑（还让 Main Agent 留在转发链
上）更保守，但与批 C-II 已采用的立场一致 —— 不是新裁定，是老裁定的延伸。

> ⚠️ `command-tool` 不能指 `exec`：`command-arg-mode: raw` 会把用户在 `/card` 后
> 打的字当 shell 命令跑（`/card` 后面接什么就执行什么），那是把聊天框变成 shell。
> ⇒ 必须指一个**专用工具**。用 `mcp` 包做一个极小的 stdio MCP 服务暴露
> `biga_card_trigger`，它只干一件事：调 `inbound.accept_trigger`。

### 四、幂等键为什么绑在 `decision_ids`，不绑在 `decision_runs`

身份模型是 **Trigger → Decision → 多个 Run**（一次外部请求 → 一次业务决策 →
可能多次执行尝试，重试/回放各是一个 run）。飞书 event 重投必须映射到**同一个
决策**、绝不新起。

- 绑在 `decision_runs.trigger_id`（每次尝试一行）会**误伤将来的重试**：重试
  Run B 复用同一个 trigger，会撞唯一约束。
- 绑在 `decision_ids`（每个决策一行、号分配器）正好：**重试复用同一个决策号、
  不重新占号 ⇒ 不撞约束**。而且号分配器本就是决策身份的**原子仲裁点**
  （`reserve_decision_id` 靠主键冲突占号）。把「这个号为哪次外部请求占的」记在
  同一处，幂等就与占号是**同一个原子写**。

⇒ schema v13：给 `decision_ids` 加一列 `trigger_id` + 一个 partial unique index
（`WHERE trigger_id IS NOT NULL`——CLI 每次生成唯一 trigger，NULL 老行互不冲突）。
`reserve_decision_for_trigger` 返回 `(decision_id, created)`：`created=False` 就是
「这个 event 之前占过号」。**「先查 trigger 在不在、不在就占号」中间有竞态窗口
——唯一约束才是唯一可靠的并发仲裁**（这句话本仓库在 `decision_ids`/`run_events`
上已经写过两遍，这是第三次用它）。

### 五、异步 + entry_guard 的张力：为什么用 `systemd-run`

受理要快（立刻 ACK），出卡要慢（后台跑 170~200s）。所以适配器**拉起后台运行、
立刻返回**。但拉起谁、怎么拉，撞上一个已有护栏：

`entry_guard.classify_caller()` 把「祖先里有运行时 / env 里有 `OPENCLAW_SERVICE_*`」
判成 AGENT 并拒绝（防 main 递归拉起出卡）。而命令工具是在网关进程里跑的——直接
fork 出的 `bin/biga-card` 会继承这两样、被判成 AGENT、被拒。

不能改 entry_guard 已经在拦的那条（分发提示词明确不许）。解法是**新增一条被允许
的路径**：用 `systemd-run --user` 把 `bin/biga-card` 拉成一个瞬态 systemd 单元——
祖先变成 `systemd --user`、不继承网关的 service env ⇒ `classify_caller` 判成
HUMAN。而 entry_guard 的 docstring 里，允许的发起方本就写着「人 / CLI / cron /
**外部 Trigger**」——飞书 inbound 正是「外部 Trigger」。**这不是绕过守卫，是走它
本就留的那道门。**

> 通用原则：一道安全守卫拦的是**某一类主体**（这里是「agent 会话」），不是「所有
> 子进程」。给一条**结构上不同类**的新路径放行，正确做法是让它真的落在被允许的
> 那一类里（systemd 拉起 ⇒ 血缘真的是外部触发），而不是在守卫上开一个按名字的口子。

### 六、人工 CLI 与飞书 inbound 必须走到同一个 orchestrator

分发提示词点名：两条路不许分叉出两套决策逻辑。而出卡的五道守卫（总闸 →
ownership → 单实例锁 → 预算 → 第一次付费）全在 `bin/biga-card` 里。⇒ 飞书路径
异步拉起的**就是 `bin/biga-card`**（带三个环境变量透传 origin/trigger/decision），
不是绕过守卫直插 orchestrator。异步只发生在 `inbound.py → bin/biga-card` 这条边界
（脱离进程树 + 立刻 ACK），`bin/biga-card` 内部照旧从头同步跑到卡落库。人工 CLI
没有那三个环境变量 ⇒ `origin=cli` ⇒ 一字不变。

### 七、config-as-code 撞 R-2，但机关早就在

`apply_config.py` 要装 systemd 服务 —— 撞红线 R-2（systemd 单元名是共享命名空间）。
但判据机关已经在：`isolation.py::check_namespaces()` 已经会查「引用 BigA 的单元
必须带 `-biga`」，`bin/biga` 已经强制 `--profile biga`（由它推导 `-biga` 名）。
⇒ `apply_config.py` 要做的是**走这条已有的路**，不是新发明一套 R-2 检查：一切经
`bin/biga`（绝不裸 `openclaw`）、装前拒绝任何非 `-biga` 单元名（`OPENCLAW_SYSTEMD_UNIT`
这个 env 覆盖是唯一能绕过推导的口子，堵上它）、装后用 `isolation.py` 核对。

而 `tools.deny:[ask_user]` 这条**半年前不能做、现在能做**：配置级 `tools.deny` 粒度
按 agent，批 C-II 之前 main 同时是交互入口和出卡入口，禁它的 `ask_user` 会连累
飞书/TUI 正常交互。批 C-II 之后 main 不再是出卡入口 —— 现在禁的是**被 spawn 的
流水线 agent**（它们非交互、调 `ask_user` 就是确定性死锁），main 根本不在名单里。
⚠️ agent 名单**从 `_contract` 派生、不手写**（裁定 15 / dev-workflow 第五问）：
`discipline` 在 STAGE2 名单里但故意没建（裁定 13）⇒ 按「真有 AGENTS.md 才算建成」
过滤掉，不给一个不存在的 agent 写配置。

## 执行

真跑过的命令（都在独立 worktree 里，见「坑」一节）：

```bash
# schema v13 迁移在新旧库上都干净跑通、幂等
python3 -c "import sys;sys.path.insert(0,'skills');from _store import db;print(db.init_schema('/tmp/t.db'))"   # 13

# 入站幂等 + 异步 + 命令工具，离线全绿
python3 -m pytest tests/test_inbound_trigger.py tests/test_feishu_deliverer.py tests/test_apply_config.py -q

# 配置 dry-run：patch 对着 live schema 校验通过、且落在 ~/.openclaw-biga（不是同机另一套）
python3 deploy/openclaw/apply_config.py            # Dry run successful: 10 update(s) validated
```

## 坑

**这一批开工时撞上了另一批在同一棵工作树上开工。** 环境默认落在
`orchestration` 主检出，而另一批（Agent Registry）正在同一目录里改
`_contract`。两批文件当时不重叠，但**共享工作树意味着任何一次提交都会带上对方
的未提交改动** —— 一批的 schema 迁移会被写进另一批的 commit。第 28 章记过本项目
「第一次两批并行」时的结论：**并行批各开一棵 worktree、绝不动对方的未提交草稿**。
这次是同一条纪律的复发提醒，处理方式一样：把本批挪进独立 worktree（`wt-g-ii`），
把落在共享检出里的自己那几个改动**只还原自己碰过的文件**（与对方不重叠 ⇒ 零影响）。

**`command-tool` 一开始想指 `exec`，是错的。** `command-arg-mode: raw` 会把用户
`/card` 后面打的字当 shell 命令，等于把聊天框变成 host shell。必须专门做一个
MCP 工具。

**红-verify 时一个测试裸 `import sqlite3` 被守卫拦下。** `test_no_raw_sqlite.py`
钉死「`_store` 之外不许 `import sqlite3`」，而我在一条测原子唯一约束的用例里图省事
`import sqlite3` 取异常类型。改成用 `_store.connect` + 断言异常消息里有 `unique`，
不碰那个 import。**一个坏掉时会花钱/破坏隔离的守卫，正确的反应是顺着它改，不是
给它开豁免。**

## 验证

```bash
# 1. 全部离线探针绿（P1 幂等 / P2 main 出局 / P3 异步 / P4 R-2 / P5 tools.deny）
python3 -m pytest tests/test_inbound_trigger.py tests/test_feishu_deliverer.py \
                  tests/test_apply_config.py -q          # 期望：全绿

# 2. 每道守卫都见过红（G-1）—— 探针红灯记录见 CHANGELOG 批 G-II 一节
#    P1: 关掉 reserve_decision_for_trigger 的去重 ⇒ 幂等测试翻红
#    P2: 从 SKILL.md 删掉 disable-model-invocation ⇒ 结构测试翻红
#    P3: 给 accept_trigger 塞一个 sleep ⇒ 「快速返回」翻红
#    P4: 让 check_r2 fail-open ⇒ 「非 -biga 被拒」翻红
#    P5: 把 main 塞进 deny patch ⇒ 「main 不在 deny」翻红

# 3. 配置 patch 落在自己的 profile、不碰同机另一套
python3 deploy/openclaw/apply_config.py 2>&1 | grep "openclaw-biga"   # 只出现 -biga 路径
```

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 飞书出卡的根子修法是「main 收不到这类请求」，不是「教 main 认出来再转发」——反事实检验：清空 main 的 prompt 这条路还能走 |
| 2 | 飞书是 websocket 长连接 ⇒ 没有能指向的 HTTP 端点 ⇒ 结构性拦截只能表达成配置 + OpenClaw 命令层 |
| 3 | `command-dispatch: tool` 让 `/card` 直达工具、绕过 model；`command-tool` 不能指 `exec`（会把聊天框变 shell），要专用 MCP 工具 |
| 4 | 幂等键绑在 `decision_ids`（每决策一行）不绑 `decision_runs`（每尝试一行）——后者会误伤重试；唯一约束才是可靠的并发仲裁 |
| 5 | 异步用 `systemd-run` 把出卡拉成瞬态单元：血缘变 systemd ⇒ entry_guard 判 HUMAN。这是走它本就留的「外部 Trigger」门，不是开口子 |
| 6 | 人工 CLI 与飞书 inbound 走同一个 `bin/biga-card`（同五道守卫、同 orchestrator）；异步只在 `inbound → bin/biga-card` 的边界，CLI 同步体验不变 |
| 7 | `apply_config.py` 撞 R-2 但机关早在：一切经 `bin/biga`、拒绝非 `-biga` 单元名、装后 `isolation.py` 核对 |
| 8 | `tools.deny:[ask_user]` 只给被 spawn 的非交互流水线 agent，main 不在名单（探针 P5）；名单从 `_contract` 派生不手写 |
| 9 | 并行批各开 worktree、不动对方未提交草稿——第 28 章记过的纪律，这次因默认落在共享检出而复发 |
