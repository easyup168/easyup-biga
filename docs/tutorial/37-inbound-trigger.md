# 第 37 章 · 飞书出卡变成结构性 trigger（批 G-II）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 G-II（Inbound Trigger）的建造过程 —— 飞书「出卡」怎么从
> 一次自由对话变成一个「main 只负责发起、编排绝不在 main 进程树里跑」的结构化触发。
> 尤其记一条**走死了的路**（零 LLM 的 command-dispatch → MCP 工具）和为什么绕开它。
> **不覆盖**：批 G-I 的外发通道（第 34 章）、飞书渠道本身怎么装。

## 目标 / 产出

批 G-I 让卡跑完能**推**回飞书。这一章做反方向、也是风险大得多的那半：让飞书能
**触发**出卡 —— 但**出卡的编排绝不在 main 的会话进程树里跑**（2026-09-21 两次事故的
根子）。做完得到：

- `card` 技能：main 认出出卡请求 → 跑一条 `skills/card/scripts/inbound.py` → 幂等
  受理 → 异步、**脱离进程树**拉起出卡（照 BigA 全仓「SKILL.md + shell 跑脚本」的形态）；
- `decision_ids.trigger_id`（schema **v14**）做入站幂等键，同一个飞书 event 重投不重跑；
- `bin/biga-card` 长出一条异步入口，人工 CLI 的同步体验一字不变；
- `feishu_deliverer.py` 把批 G-I 的 `Deliverer` 接口接上真飞书 API；
- `deploy/openclaw/` + `apply_config.py`：出卡流水线 agent 禁 `ask_user`、命令面进
  配置，全部经 `bin/biga` 落地（R-2 安全）。

## 为什么这么做（占篇幅最大的一节）

### 一、根子不是「让 main 更听话」，是「编排绝不能在 main 的进程树里」

2026-09-21 两次事故（19:31 四个孤儿 spawn、21:03 出卡递归 L-14）根子相同：
**出卡的编排跑在了一个 agent 会话里，于是它能自己拼一套 `sessions_spawn`、还能递归
再拉一次出卡**。L-14 那次的事后总结已经说过——契约文字（AGENTS.md 写「不要自己
编排」）这个量级**不够**。

所以这一批要钉死的**不变式**是：**出卡的编排必须跑在一个不是 agent 会话的进程里。**
谁「按下按钮」是次要的，「按下之后那一大坨编排在哪跑」才是命门。这条比「main 全程
不参与」更本质 —— 后者只是实现前者的一种（后面会看到它这一版走不通）。

### 二、探活先纠正了三个隐含假设

分发提示词让「开工第一件事读设计探活」。照做，代码库现状纠正了三处想当然：

**飞书是 websocket 长连接，不是 webhook。** 事件从网关进程内部到达，没有一个我
能指向的 HTTP 端点。⇒ 「起一个自己的 webhook server 绕开 main」这条路根本不存在。

**仓库里一行飞书代码都没有。** 真实飞书接入（渠道配置、凭据、装好的插件）全在
仓库外的运行时配置里。⇒ 这一批要造的是**第一个真正把 `origin="feishu"` 填上**的
调用点。

**`bin/biga-card` 今天完全同步。** 全文一次 `wait "$_ORCH_PID"`，没有任何「快速
ACK、后台完成」的半成品可复用。而一次出卡要 170~200 秒，塞不进一次触发的 ACK
窗口。⇒ **异步执行本身**是这一批最大的一块新增基础设施。

### 三、🔴 走死了的路：零 LLM 的 `command-dispatch: tool` → MCP 工具

最初的立场比现在激进：**main 压根收不到出卡请求**。翻 OpenClaw 文档
（`docs/tools/slash-commands.md`）翻到关键一句：技能可以声明 `command-dispatch: tool`，
让斜杠命令**直接派发到一个工具、绕过 model**。于是把 `/card` 做成这样一个技能，
`command-tool` 指向一个专门做的 stdio MCP 工具 `biga_card_trigger`（`command-tool`
不能指 `exec` —— `command-arg-mode: raw` 会把用户 `/card` 后面打的字当 shell 命令，
等于把聊天框变 host shell）。理论链路：owner 发 `/card` → 命令层直达工具 →
**main 的 LLM 没有一步**。

**live 上它直接报 `Tool not available: biga-card-trigger__biga_card_trigger`。**
两次「修复」（补 MCP server 名前缀、补 `ToolAnnotations`）都是真 bug、但都没打在根上。
真根子（网关日志 + 官方文档双证）：

> MCP stdio server 是**会话内**按需连接的（`docs/gateway/cli-backends.md`:
> *"Session-scoped bundled MCP runtimes … do not outlive the run"*）。而
> `command-dispatch` 在**任何会话/MCP 连接建立之前**就把 `toolSchema` 定死了 ——
> 会话内才连接的工具根本不在那张表里。**main 自己用 LLM 调同一个工具反而能算对**，
> 因为它在一个真会话里、MCP 当场连得上；只有 command-dispatch 这条**静态声明**路径
> 够不到。离线探针只查「SKILL.md 里字符串在不在」，测不出这个 —— 只有 live 暴露
> （这正是这批留一道 live 探针 P6 的理由）。

⇒ **「零 LLM 的 command-dispatch 桥」在这个 OpenClaw 版本不成立。** 记下来，别让下一个
人再走一遍。

### 四、🔴 pivot：两个约束把设计收敛成「纯 skill + 脱树编排」

死胡同之后，是运营者给的两个约束把设计一锤定音：

1. **「我就是通过一个飞书机器人和系统沟通，别的不考虑。」** ⇒ 一个机器人、一个私聊里
   既日常对话又触发出卡。而 OpenClaw 的 `bindings` **按 peer 路由、不按内容** —— 一个
   私聊要么整条给 main、要么整条给别的 agent，没法「/card 归 A、闲聊归 main」。
   ⇒ 「给出卡建一个专用非-main agent、靠 binding 分流」这条**也死了**。
2. **「可以使用 LLM，不追求 0 LLM。」** ⇒ 允许 main 的 LLM **发起**。

两条合起来：既然一个私聊里没有「零 LLM 的内容分流器」（command-dispatch 是唯一一个、
且够不到工具），那就**让 main 的 LLM 认出出卡请求来发起** —— 但把真正要防的东西
（§一那条不变式）落在**发起之后**：出卡编排用 `systemd-run` 脱离 main 的进程树跑。

同时第二条约束点破了一件更基本的事：**BigA 全仓的技能都是同一个形态** ——
SKILL.md 写一条 `python3 .../script.py`，agent 读了技能就用 shell 跑那个脚本
（`decision-card` / 各 `*-calc` / `news-scan` / `risk-check` 无一例外）。那个为了迁就
command-dispatch 才引入的 MCP server，**与全仓形态不一致、且已经没用了**。⇒ 整体删掉。
`/card` 回归成一个**普通技能**：main 认出出卡请求 → 用 shell 跑 `inbound.py`。

> 通用原则：当一个「绕过模型」的机制在你的运行时里够不到你要调的东西时，先别加桥、
> 加垫片、加中介去凑它 —— 退回到这套系统**本来就在用**的那个机制（这里是「技能 +
> shell 跑脚本」），往往又简单又一致。三个多余的抽象（MCP server、专用 agent、
> command-dispatch 桥）就是这么被一句「不用 MCP，封装成 skill 就行」全删掉的。

**诚实的降级**：不变式从「main 全程不参与路由」弱化成「main 只发起、**编排绝不在
main 进程树里**」。放弃的是「清空 main 的 prompt 这条路还能走」这个漂亮的反事实；
**没放弃**的是这批真正要防的东西 —— 会话自己拼 ad-hoc spawn 递归出卡（那靠 §五的
脱树挡死，与谁发起无关）。

### 五、异步 + entry_guard：`systemd-run` 脱树，是这批的命门

受理要快（立刻 ACK），出卡要慢（后台跑 170~200s）。所以 `inbound.py` **拉起后台运行、
立刻返回**。但拉起谁、怎么拉，撞上一个已有护栏 —— 而 pivot 之后这道恰恰成了**唯一**
的 L-14 止血点，份量比 command-dispatch 那版更重：

`entry_guard.classify_caller()` 把「祖先里有运行时 / env 里有 `OPENCLAW_SERVICE_*`」
判成 AGENT 并拒绝。main 用 shell 跑的 `inbound.py` 是在网关进程树里 —— 它直接 fork 出的
`bin/biga-card` 会继承这两样、被判 AGENT。解法是**新增一条被允许的路径**：用
`systemd-run --user` 把 `bin/biga-card` 拉成一个瞬态 systemd 单元 —— 祖先变成
`systemd --user`、不继承网关的 service env ⇒ `classify_caller` 判成 HUMAN。entry_guard
的 docstring 里，允许的发起方本就写着「人 / CLI / cron / **外部 Trigger**」。**这不是
绕过守卫，是走它本就留的那道门。**（没有 systemd-run 时回退 `setsid` + **显式清掉**
`OPENCLAW_SERVICE_*`，语义相同。）

> 通用原则：一道安全守卫拦的是**某一类主体**（这里是「agent 会话」），不是「所有
> 子进程」。给一条**结构上不同类**的新路径放行，正确做法是让它真的落在被允许的
> 那一类里（脱树 ⇒ 血缘真的是外部触发），而不是在守卫上开一个按名字的口子。

⇒ 探针 P2 因此从「清空 main 的 prompt 照跑」改成**结构判据**：`detached_biga_card_launcher`
必须用 `systemd-run --user`（脱树），或在回退路径显式清掉 `OPENCLAW_SERVICE_*`。
把 `--user` 去掉、或把清 env 那段删掉，测试当场翻红（红灯记录见 CHANGELOG）。

### 六、幂等键为什么绑在 `decision_ids`，不绑在 `decision_runs`

身份模型是 **Trigger → Decision → 多个 Run**（一次外部请求 → 一次业务决策 →
可能多次执行尝试）。飞书 event 重投必须映射到**同一个决策**、绝不新起。

- 绑在 `decision_runs.trigger_id`（每次尝试一行）会**误伤将来的重试**：重试
  Run B 复用同一个 trigger，会撞唯一约束。
- 绑在 `decision_ids`（每个决策一行、号分配器）正好：重试复用同一个决策号、不重新
  占号 ⇒ 不撞约束。而号分配器本就是决策身份的**原子仲裁点**。把「这个号为哪次外部
  请求占的」记在同一处，幂等就与占号是**同一个原子写**。

⇒ schema **v14**：给 `decision_ids` 加一列 `trigger_id` + 一个 partial unique index
（`WHERE trigger_id IS NOT NULL` —— CLI 每次生成唯一 trigger，NULL 老行互不冲突）。
`reserve_decision_for_trigger` 返回 `(decision_id, created)`：`created=False` 就是
「这个 event 之前占过号」。**「先查 trigger 在不在、不在就占号」中间有竞态窗口 ——
唯一约束才是唯一可靠的并发仲裁**（本仓库第三次用这句话）。

⚠️ 幂等键的**可靠性**在 pivot 后有一层诚实的降级：event id 现在由 main 的 LLM 从消息
里读出、作为 `--trigger-id` 传入（可能拿不到）。好在飞书事件的**首道去重**在 OpenClaw
渠道层（durable events），多数重投到不了 main；`decision_ids` 唯一约束是**兜底**。
真实 event 落在哪个字段、上下文能不能不经 LLM 直接给到，留 live（P6）核实。

### 七、人工 CLI 与飞书 inbound 必须走到同一个 orchestrator

分发提示词点名：两条路不许分叉出两套决策逻辑。出卡的五道守卫（总闸 → ownership →
单实例锁 → 预算 → 第一次付费）全在 `bin/biga-card` 里。⇒ 飞书路径异步拉起的**就是
`bin/biga-card`**（带三个环境变量透传 origin/trigger/decision），不是绕过守卫直插
orchestrator。异步只发生在 `inbound.py → bin/biga-card` 这条边界，`bin/biga-card`
内部照旧从头同步跑到卡落库。人工 CLI 没有那三个环境变量 ⇒ `origin=cli` ⇒ 一字不变。

### 八、config-as-code 撞 R-2，但机关早就在

`apply_config.py` 要装 systemd 服务 —— 撞红线 R-2（systemd 单元名是共享命名空间）。
但判据机关已经在：`isolation.py::check_namespaces()` 已经会查「引用 BigA 的单元必须
带 `-biga`」，`bin/biga` 已经强制 `--profile biga`。⇒ `apply_config.py` 走这条已有的
路：一切经 `bin/biga`（绝不裸 `openclaw`）、装前拒绝任何非 `-biga` 单元名
（`OPENCLAW_SYSTEMD_UNIT` 这个 env 覆盖是唯一能绕过推导的口子，堵上它）、装后用
`isolation.py` 核对。

而 `tools.deny:[ask_user]` 这条**半年前不能做、现在能做**：批 C-II 之前 main 同时是
交互入口和出卡入口，禁它的 `ask_user` 会连累飞书/TUI 正常交互。批 C-II 之后禁的是
**被 spawn 的流水线 agent**（非交互、调 `ask_user` 就是确定性死锁），main 根本不在
名单里。⚠️ agent 名单**从 `_contract` 派生、不手写**（裁定 15）：`discipline` 在
STAGE2 名单里但故意没建（裁定 13）⇒ 按「真有 AGENTS.md 才算建成」过滤掉。

### 九、🔴 P6 real 真跑：闸门拒了自己、投递缺凭据——两处只有真链路才暴露

前面八节 + 离线探针把能想到的都钉住了，但 P6 真正接上飞书那一刻，还是暴露了两个
新问题——都不是这一批之前没考虑过的东西坍了，是**两段各自独立正确的逻辑，头一次
真的串在一条调用链上跑**时才互相咬合出的坑。

**闸门拒了自己刚占的号。** `bin/biga-card` 自带的预算闸门（`check_budget()`，批
G-I 就有）查「decision_ids 最近一行、距现在多久」。CLI 路径下，闸门先跑、orchestrator
后占号，"最近一行"自然是**别的**、更早的尝试。飞书路径反过来——`accept_trigger`
为了给出幂等 ACK，**先**占号、**再**拉起 `bin/biga-card`；闸门跑的时候，"最近一行"
就是它自己刚占的那个。gap 恒为 0s——**这条路径上闸门必然拒绝每一次真实触发，不是
偶发**。第一次真飞书 `/card`（`BIGA-20260923-003`）就是这样被拦下的：ACK 已经说
"正在出卡"，`bin/biga-card` 却在几毫秒后报"距上次占号只有 0s"，两条理由都指向它
自己。修法：`check_budget()` 加 `exclude_decision_id` 参数，把"跟自己比"这一条摘出去
（`DAILY_CAP`——今天总共占了几个号——不受影响，这次自己确实算一次）。

> 通用原则：一个"查最近一次别的尝试"的判据，一旦调用方自己先往同一张表里写了一行、
> 再去跑这个判据，就会把自己算成"最近一次"。这类闸门只要**在调用它之前，同一张表
> 会不会已经多了一行**这个问题，答案在两条路径上不一样，就该显式排除自己，不能假设
> "查的时候号还不存在"对所有路径都成立。

**飞书投递缺凭据。** 卡真的生成了、正确入队了通知（`notification_outbox`），但
`notify_worker.py --deliverer feishu` 报错缺 `BIGA_FEISHU_APPID`/`APPSECRET`/
`OWNER_ID`——根子是 `notify_worker.py` 从批 G-I 起就没有调度方，意味着**也没有
"谁在正确环境里带着这三个值跑它"这件事本身的答案**。而网关那一刻正用一条真实、
已认证的飞书连接（刚处理完触发出卡的那条消息）。改法：`FeishuDeliverer.deliver()`
不再自己实现 `tenant_access_token` + `im/v1/messages` 两步 HTTP 调用，改成 shell 一次
`bin/biga message send --channel feishu`（R-1，同 `apply_config.py` 的 `_run_biga`
idiom）——走网关自己的飞书通道，appId/appSecret 从此不出现在这个类里，只剩一个
标识符（`BIGA_FEISHU_OWNER_ID`，收件人，不是凭据）。真的手动跑通一次：
`bin/biga message send` 返回 Feishu API 的真实 `messageId` + `receipt`（不是本地
exit code 自证成功），运营者在飞书里确认收到了。

> 通用原则：重构把业务逻辑挪出了原来隐式拥有某个能力（这里是"已认证的飞书连接"）
> 的那层，容易第一反应是"那就让新的那层自己实现一遍这个能力"。先检查旧的那层
> 是否还活着、是否已经有一个入口能借用——借用比重新发明更少一套凭据来源，也更少
> 一个"这套凭据该由谁维护"的悬而未决的问题。

两处都只有**真的接上飞书跑一次**才暴露——离线探针分别测了各自的一段（P1 幂等、
P3 异步、`test_apply_config.py` 的 R-2/tools.deny），没有一道把"占号→拉起→闸门"
或"卡完成→入队→投递"串成一条真实链路去跑，所以都没测出来。这不是探针写得不够
细，是这类"两段各自独立正确，串起来才咬合出问题"的坑，本质上只有 live 才照得到。

## 执行

真跑过的命令（都在独立 worktree 里，见「坑」一节）：

```bash
# schema v14 迁移在新旧库上都干净跑通、幂等（在真实 prod 库的副本上验过，18 行原样保留）
python3 -c "import sys;sys.path.insert(0,'skills');from _store import db;print(db.init_schema('/tmp/t.db'))"   # 14

# 入站幂等 + 异步 + 脱树 + 配置，离线全绿
python3 -m pytest tests/test_inbound_trigger.py tests/test_feishu_deliverer.py tests/test_apply_config.py -q

# 配置 dry-run：patch 对着 live schema 校验通过、且落在 ~/.openclaw-biga（不是同机另一套）
python3 deploy/openclaw/apply_config.py            # Dry run：只 patch tools.deny + commands.text
```

## 坑

**零 LLM 的 command-dispatch 走死了（本章 §三）。** 花了三次「修复」才确认根因不在
工具名/标注，而在「command-dispatch 够不到会话内才连接的 MCP 工具」。教训有二：
① 一道**只有 live 才暴露**的机制（静态 toolSchema vs 会话内工具），离线探针天然测不
到，得留一道 live 探针；② 撞墙时先别加桥加垫片，退回系统本来就在用的机制（skill +
shell）。

**为了迁就 command-dispatch 引入的 MCP server 是多余的。** 一句「不用 MCP，封装成
skill 就行」删掉了三个抽象（MCP server 脚本、专用 agent、command-dispatch 桥）。
**保留的**是真有用的那几块：`inbound.py` 的幂等 + 脱树、schema v14、feishu deliverer。
—— 走过弯路不等于全推倒，认清哪几块是「迁就死路才有的」、哪几块是「本来就对的」。

**P2 探针的第一版 substring 检查抓到了自己的 docstring。** 我把「触发路径不许出现
`sessions_spawn`」写成裸字符串包含，结果 `inbound.py` 的 docstring 里为了解释「防的
是什么」正好提到了这个词 —— 测试红在自己身上。这与 CLAUDE.md「描述『不要写 X』时
别把 X 抄进去」是同一个形状。改成 **AST 查真实 import**（`inbound.py` 是纯 Python，
`sessions_spawn` 是 agent 工具、根本不可能被它 import；真正要防的是 import LLM SDK）。

**红-verify 时一个测试裸 `import sqlite3` 被守卫拦下。** `test_no_raw_sqlite.py` 钉死
「`_store` 之外不许 `import sqlite3`」。改成用 `_store.connect` + 断言异常消息里有
`unique`。**一个坏掉时会破坏隔离的守卫，正确的反应是顺着它改，不是给它开豁免。**

**并行批各开 worktree、不动对方未提交草稿。** 这批开工时撞上另外几批在同一棵工作树
上开工（Agent Registry / raw 层）。处理：把本批挪进独立 worktree，只还原自己碰过的
文件。第 28 章记过这条纪律，这次是复发提醒。

**预算闸门拒了它自己刚占的号（本章 §九）。** 离线探针把幂等/异步/脱树都测过了，但
没有一道探针真的把"占号 → 拉起 bin/biga-card → 闸门检查"串成一条链路跑——直到真飞书
`/card` 第一次触发，才看见闸门拿"刚占的这个号"当"上一次尝试"，100% 自拒。教训：
一条链路被拆成好几段各自测试时，"每段都对"不等于"串起来也对"，尤其是当某一段的
副作用（占号）恰好是下一段的判据（上次占号）的输入。

**飞书投递缺凭据，根子是投递从来没有过一个"谁在正确环境里跑"的答案（本章 §九）。**
不是漏配了三个环境变量，是 `notify_worker.py` 从批 G-I 起就没有调度方——补环境变量
只是让本地手跑那一次不报错，没回答"部署好之后这三个值日常该从哪来"。改成借用网关自己
已认证的连接（`bin/biga message send`）之后，这个问题从"需要一套凭据管理"变成"不需要
凭据"，问题本身消失了，不是被绕过。

## 验证

```bash
# 1. 全部离线探针绿（P1 幂等 / P2 脱树 / P3 异步 / P4 R-2 / P5 tools.deny）
python3 -m pytest tests/test_inbound_trigger.py tests/test_feishu_deliverer.py \
                  tests/test_apply_config.py -q          # 期望：全绿

# 2. 每道守卫都见过红 —— 探针红灯记录见 CHANGELOG 批 G-II 一节
#    P1: 关掉 reserve_decision_for_trigger 的去重 ⇒ 幂等测试翻红
#    P2: 给 systemd-run 去掉 --user、或回退路径不清 service env ⇒ 「脱树」翻红
#    P3: 给 accept_trigger 塞一个 sleep ⇒ 「快速返回」翻红
#    P4: 让 check_r2 fail-open ⇒ 「非 -biga 被拒」翻红
#    P5: 把 main 塞进 deny patch ⇒ 「main 不在 deny」翻红

# 3. 配置 patch 落在自己的 profile、不碰同机另一套
python3 deploy/openclaw/apply_config.py 2>&1 | grep "openclaw-biga"   # 只出现 -biga 路径
```

> 🔴 P6（live）**已完成（2026-09-23）**：真在飞书里发 `/card` → main 认出 → 跑
> `inbound.py` → 脱树出卡 → 结论推回飞书，全链路走通，运营者在飞书里确认收到了
> 结论。中途真的撞见并修好了两处只有真链路才暴露的问题（本章 §九）：预算闸门
> 自拒、飞书投递缺凭据。
>
> `inbound-trigger-debug.log` 这个原计划的核实手段随 pivot 一起没了——它是
> `card_trigger_mcp.py` 的 `_debug_log_args()` 记的（专门给"MCP 调用入参里 event id
> 落在哪个键"这个问题用的），那个工具连同这份诊断日志一起删掉了。真事件的核实改走
> main 自己的 transcript（`~/.openclaw-biga/agents/main/agent/openclaw-agent.sqlite`
> 的 `transcript_events` 表——`created_at` 是 UTC 毫秒，见 CLAUDE.md 的老警告）：
> `--trigger-id` 传的是飞书 `messageId`（`om_...`），原样落进了
> `decision_ids.trigger_id`（`reserved_by='feishu:om_...'`）——对上了。

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 这批要钉的不变式是「出卡编排绝不在 agent 会话进程树里跑」——比「main 全程不参与」更本质；谁按按钮次要，按下之后那坨编排在哪跑才是命门 |
| 2 | 飞书是 websocket 长连接 ⇒ 没有能指向的 HTTP 端点 ⇒ 结构性拦截只能表达成配置 + OpenClaw 命令层/技能 |
| 3 | 🔴 零 LLM 的 `command-dispatch: tool` 走死了：它建 toolSchema 时够不到**会话内才连接**的 MCP 工具（main 在会话里反而调得到）——只有 live 暴露，离线探针测不出 |
| 4 | 🔴 一个飞书机器人 + `bindings` 按 peer 路由 ⇒ 一个私聊没法按内容分流 ⇒ 「专用非-main agent」也走不通；LLM 允许后退回「main 发起 + 脱树编排」 |
| 5 | 🔴 撞墙时退回系统本来就在用的机制（技能 + shell 跑脚本），别加桥/垫片/中介去凑——一句「不用 MCP」删掉了三个多余抽象 |
| 6 | 幂等键绑在 `decision_ids`（每决策一行）不绑 `decision_runs`（每尝试一行）——后者误伤重试；唯一约束才是可靠的并发仲裁（schema v14）|
| 7 | 异步用 `systemd-run` 把出卡拉成瞬态单元：血缘变 systemd ⇒ entry_guard 判 HUMAN。pivot 后这是**唯一**的 L-14 止血点，P2 因此改成「launcher 必须脱树」的结构判据 |
| 10 | 🔴 P6 real 才暴露：预算闸门拿"飞书路径先占号、才拉起 bin/biga-card"里自己刚占的那个号当"上一次尝试"，100% 自拒——`exclude_decision_id` 参数解决；一条链路被拆成好几段各自离线测过，不等于串起来也对 |
| 11 | 🔴 P6 real 才暴露：飞书投递缺凭据的根子不是漏配环境变量，是投递从来没有过"谁在正确环境里跑"的答案——改成借网关自己已认证的连接（`bin/biga message send`），appId/appSecret 从此不需要，问题消失而不是被绕过 |
| 8 | 人工 CLI 与飞书 inbound 走同一个 `bin/biga-card`（同五道守卫、同 orchestrator）；异步只在 `inbound → bin/biga-card` 的边界，CLI 同步体验不变 |
| 9 | `apply_config.py` 撞 R-2 但机关早在：一切经 `bin/biga`、拒绝非 `-biga` 单元名、装后 `isolation.py` 核对；`tools.deny:[ask_user]` 只给被 spawn 的流水线 agent，名单从 `_contract` 派生 |
