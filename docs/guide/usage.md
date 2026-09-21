# 怎么用 · 操作手册

> 🔧 **操作** · 跑不通就是错的（本文档的每条命令都实跑过）
> **覆盖**：日常怎么用、怎么确认它没骗你 ｜ **不覆盖**：为什么这么设计（见 [`../design/`](../design/architecture.md)）

---

## 30 秒版本

```bash
cd ~/.openclaw-biga/workspace

bin/biga-card              # 出一张新卡（约 3 分钟、$1.2）
bin/biga-card --list       # 最近出过哪些卡
bin/biga-card --show <号>   # 看某一张
```

⚠️ **它不是交易信号。** 产出是一张证据可追溯的决策卡，最终决定由人做。
系统**不下单、不接账户**。

---

## 一、出一张卡

```bash
bin/biga-card
```

```
▸ 会话 card-124512   起 12:45:12
  （实测 3 分钟左右、约 $1.2；期间可以做别的事）
▸ 等待落库 ....... → BIGA-20260921-017  12:48:31
```

然后它会把卡渲染出来。

### 卡面怎么读

```
BIGA DECISION CARD   BIGA-20260921-016   11:04:13   耗时 0s
──────────────────────────────────────────────────────────────
market       UNKNOWN  无法判定   advance_count=4259; …
technical    PASS     空头     close=3911.87; …
emotion      WARNING  修复     broken_rate=0.1685; …
sector       PASS     主线明确   concept_advance_ratio=0.9702; …
news         PASS     平静     item_count=67; …
risk         UNKNOWN  无法判定   coverage_ratio=1.0; …

状态：WAIT
核心矛盾：…
⚠ 缺失项（8）
证据
```

**每一行有两个判断，它们回答的不是同一个问题**：

| 列 | 问的是 | 取值 |
|---|---|---|
| 第 2 列 `verdict` | **数据全不全** | `PASS` / `WARNING` / `UNKNOWN` |
| 第 3 列 `stance` | **它的判断是什么** | 各 agent 有自己的词表 |

🔴 只看第 2 列会把 `PASS` 误读成「看好」。它只是说这个 agent 的数据是齐的。

### 状态的含义

| 状态 | 意思 |
|---|---|
| `BUY` | 证据支持买入。**缺失项非空时契约层会直接拒绝这个状态** |
| `WAIT` | 再看看 |
| `AVOID` | 不要做 |
| `BLOCK` | 同上，更强。`risk` 给出 `否决` 时必须落在 `AVOID`/`BLOCK` |

### 🔴 缺失项永远显示，哪怕是 0 条

「没有缺失项」本身是一条信息。这是本项目最优先防范的失败模式的反面 ——
**算不出来必须说算不出来**，不能静默当成通过。

每条缺失项带一个机器可读的代码（`market.turnover.date_mismatch`），
便于日后统计「哪个源最常缺」。

---

## 二、怎么确认它没骗你

这是本节的重点。**四个互相独立的检查**，任何一个都能单独拆穿。

### 1. 证据可回放 —— 结论能不能重建

```bash
bin/biga-card --check BIGA-20260921-016
```

```
✅ 组装一致：BIGA-20260921-016 用冻结证据重跑，逐字段相同。
   ⚠️ 它验的是**组装管线无损**（落库→取回→再拼一次），不是「这个结论能被独立复算出来」。
   判断本身（status / headline / synthesis）在 --check 下照抄原卡，不参与比对。
```

它用**落库时冻结的证据**重新跑一遍合成，断言每个字段都一样。
不一致就说明卡上的数字不是从证据算出来的。

🔴 **别把它当成「结论被复算过」** —— 名字暗示的范围比它实际验的大。
外部评审 F14 把 headline 改成一句与证据毫无关系的话，`--check` 照样报一致：
`status` / `headline` / `synthesis` 在 `--check` 模式下 **100% 照抄原卡**，
两侧本质是同一个纯函数喂入被证明相等的输入，数学上必然相等。

⚠️ 但它并非测了个寂寞 —— 评审把缺失项的计算改坏之后，`--check` 正确报出了不一致。
它守的是这条管线无损、且合成没有隐藏的非确定性。要的就是这个，不多也不少。

### 2. Specialist 真的被调用了 —— 不是 Supervisor 自己编的

```bash
python3 tools/verify/agent_trace.py
```

```
── market
   09-21 11:01:58    32.7s  工具调用  2 次   65a31ec8-dd2
── news
   09-21 11:01:58    78.3s  工具调用  2 次   c92f1f17-ecf
```

🔴 **这份数据来自 OpenClaw 运行时自己的表，不是我们写的。**
被验证方控制不到的地方，才算证据。

展开某一次看它到底做了什么：

```bash
python3 tools/verify/agent_trace.py --agent news -n 1
```

### 3. 真的并行了 —— 不是串行还装作很快

```bash
python3 tools/verify/latency_report.py --parallel-check
```

```
Stage 1 并行检查
  news      11:01:58 – 11:03:16    78.3s
  sector    11:01:58 – 11:03:00    61.9s
  …
  区间两两相交，最小重叠 29.9s  ⇒  ✅ **真并行**
  stage1 墙钟 78.8s   个体之和 238.4s   省下 67%
```

判据是**时间区间是否相交**这个结构性证据，不是「这次跑得快不快」。
串行的表现是「一切正常，只是慢一倍」—— 没有任何东西会报错。

同一条命令还会给出成本分解（逐 agent 的 token 与 $）。

### 3.5 Specialist 是**真被 spawn 的**，不是有人手工跑了脚本

出卡流程结束时会自动打一行：

```
▸ spawn 核验：6 个 agent 两份独立记录都齐 ['emotion', 'market', 'news', 'risk', 'sector', 'technical']
```

🔴 **为什么需要单独一条**：`agent_runs` 是 BigA 自己写的账本 ——
手工跑一遍 skill、把 verdict_ref 喂给合成脚本，产出的记录与真 spawn
**逐字节相同**。自己写的东西证明不了自己。

第二份记录来自 OpenClaw 运行时自己记的表，**BigA 的业务代码碰不到它**。
两份对不上就会报出来。要手工查某一张历史卡：

```bash
python3 tools/verify/spawn_check.py <决策号>
```

退出码：`0` 对上 · `1` 有伪造 · `2` 判不了（读不到运行时表 —— 不算通过）。

### 4. 没有影响同机的其他服务

```bash
B=$(python3 tools/verify/isolation.py --print-mtime)
python3 tools/verify/isolation.py --before "$B"
```

```
✅ I-1 · BigA 无写模式打开已有实例的文件（15 个进程，其中真运行时 2 个 / 438 个 fd）
✅ I-2 · 已有实例状态库在 BigA 操作前后未变
✅ R-2 · nvm bin 里没有 openclaw
✅ 端口 · BigA 与已有实例不重叠

══ 隔离自检全绿 ══
```

**改动过环境之后跑一次。**

### 🔴 它有第三态，而且 `🔶` 不是「基本没问题」

```
🔶 I-1 · 判不了 —— 没有 BigA 运行时在跑（12 个进程，其中真运行时 0 个 / 330 个 fd）
     扫到的都是恰好 cwd 在仓库下的会话（终端 / 编辑器 / 本脚本自己）。
     它们不碰已有实例是自然的，证明不了「BigA 运行时是安全的」。

══ 1 项判不了 —— **本次未构成隔离证据** ══
```

「进程数」和「能不能算证据」是两件事：只要你开着终端 cwd 在仓库里，
就总能扫到十几个进程 —— 而**真网关可能一个都没在跑**。
这一档原来不存在，于是网关崩了也照样报「✅ 通过」。

退出码：`0` 全过 · `1` 有失败 · `2` 有判不了。
🔴 **判不了是非零** —— 否则「跑完没报错」这个最常用的读法会把它读成通过。

---

## 三、单独跑一个 Specialist

不想等整张卡时，可以只看某一块。**加 `--no-store` 就不会落库**：

```bash
python3 skills/market-calc/scripts/market_calc.py    --no-store --render
python3 skills/sector-calc/scripts/sector_calc.py    --no-store --render
python3 skills/technical-calc/scripts/technical_calc.py --no-store --render
python3 skills/emotion-calc/scripts/emotion_calc.py  --no-store --render
python3 skills/news-scan/scripts/news_scan.py        --no-store --render
```

```
news  completed/PASS  耗时 7.1s  (未落库)
  此刻是否连续竞价时段  = True
  回看窗口(分钟)      = 60
  窗口内条数         = 79
  交给 agent 读的条数  = 79
  因上限未展示的条数    = 0
```

🔴 **不加 `--no-store` 会报错**，而且是有意的：

```
ValueError: [technical] task_id=BIGA-20260921-000 是临时号（序号 000），不能落库。
  原因：决策编号归 Supervisor 所有，specialist 自己编的号无法归属。
```

一条无法归属的判定原件比没有更糟 —— 它看起来是正经证据，却说不清属于哪次决策。

### 演练：源挂掉时它怎么表现

```bash
python3 skills/news-scan/scripts/news_scan.py --no-store --break-source feed --render
```

```
news  partial/UNKNOWN
  ⚠ 缺失 [news.feed.source_broken] 全部快讯 —— 数据源被人为中断（--break-source feed）
```

**验的是「它会不会假装没事」。** 这类演练注入的缺失在统计时会被单独剔除
（见 `tools/verify/missing_ledger.py`）。

---

## 四、日常体检

```bash
python3 tools/verify/missing_ledger.py       # 哪些源在真实地缺数据
tools/verify/audit_public.sh --worktree      # 提交前：有没有不该公开的内容
python3 -m pytest -q                         # 全部测试
```

---

## 五、现在还不能做什么

| | 状态 |
|---|---|
| 自动下单 | **永不**（本阶段）。系统不接账户 |
| 定时自动出卡 | 未建（Phase 3 才有第一条 cron）⇒ 现在只能手工跑 |
| 盘中给出有把握的结论 | 🔴 **做不到，且原因是结构性的** —— 见下 |
| 个股层面的判断 | 未做。当前只做指数与板块层面 |
| 历史回测 / 区分力检验 | Phase 4 |

### 🔴 为什么盘中总是 `WAIT`

盘中跑一次，六个 Agent 会报出**两个交易日**：

| agent | `trade_date` | 为什么 |
|---|---|---|
| `emotion` / `news` | 今天 | 实时源 ⇒ 说的是**此刻** |
| `market` / `technical` / `sector` | 上一交易日 | 日线 ⇒ **今天的还没收** |

`risk` 拒绝把它们当成同一天的事实一起审，于是每次都是「无法判定」。

**这是对的行为** —— 把「此刻的情绪」和「上周五的技术面」并排当成同一天的证据，
本来就是在编造一个不存在的时点。

🔴 **「收盘后就对齐」是错的 —— 实测证伪。**

15:21（收盘 21 分钟后）跑了一次，交易日**仍然分裂**：

```
technical/market/sector  20260918     ← 日线
emotion/news             20260921     ← 实时源
```

查源：15:22 时新浪日线的最后一根还是 `20260918`。
**当天的日线不在 15:00 发布，有一段未知长度的延迟。**

⚠️ 这条「收盘后跑就好了」是**没有验证就写下的推断**，
写进了 README、操作手册和教程三处。现在全部改掉。

> 通用原则：**「应该会……」和「实测是……」之间隔着一次运行。**
> 而这类推断特别危险，因为它听起来太合理了 —— 谁会怀疑「收盘后日线就有了」。

⇒ 正确的说法是：**要等当天日线真正发布之后**。

**实测结果（2026-09-21，n=1）**：当天日线发布在 **15:32:47 与 15:37:50 之间** ——
收盘后约 33~38 分钟。5 分钟一探的记录：

```
15:22:42  20260918
15:27:45  20260918
15:32:47  20260918
15:37:50  20260921   ← 发布
```

⚠️ **这是一天、一个源（新浪 `sh000001`）的一次测量，不是规律。**
写成「15:40 之后跑就行」就是在重犯刚刚那个错误 ——
把一次观测当成结论。判断方法仍然是直接查最后一根的日期。

判断方法：

```bash
python3 -c "
import sys;sys.path.insert(0,'skills')
from _sources.sina import fetch_index_daily
print(fetch_index_daily('sh000001',bars=1).bars[-1].day)"
```

打印出今天的日期，才是出卡的时机。

---

## 六、出问题时看哪里

| 症状 | 先看 |
|---|---|
| 卡迟迟不出来 | `python3 tools/verify/agent_trace.py --agent main -n 1` —— 它最后在做什么 |
| 某个 agent 很慢 | 同上，换 `--agent <名字>`。**先看工具调用序列，再改提示词** |
| 认证类报错 | `~/.openclaw-biga/bin/biga models auth list --agent main`，然后看 `CLAUDE.md`「已知耦合」 |
| 结论看起来不对 | `bin/biga-card --check <号>` 先确认不是合成环节的问题 |

🔴 **所有 OpenClaw 命令都要走 `~/.openclaw-biga/bin/biga`**，不要直接敲 `openclaw` ——
那会读写同机另一套实例的状态目录（红线 R-1）。
