# 第 38 章 · 交易日历：第一张真实的 `fact_*` 表

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 L —— 给 `cn.trading_calendar` 走一遍 Provider → Raw →
> Normalize → Quality → Snapshot 链，建这个仓库**第一张真实的 `fact_*` 表**
> （`fact_trading_calendar`，schema **v15**），并让 `market_is_open()` 认法定节假日。
> 重点在三个判断：**数据源怎么探活选出来的**（这一批最花时间的地方）、**一个连不通
> 的源怎么诚实地建**、**为什么 `session_in_progress()` 偏偏不改**。
> **不覆盖**：其余五个 P0 数据集的 schema（等 §45 选股闭环那一批，形状由消费方定）；
> 把日历接进 `SnapshotCoordinator`（那解决的是另一个问题，见正文）；真实抓取的
> live 验证（本环境连不通深交所，见「坑」）。

---

## 目标 / 产出

做完这一章，仓库里多出：

- `skills/_sources/szse.py` —— 深交所官方日历 Provider（`fetch` + `parse` + `refresh`）
- `fact_trading_calendar` 表（schema v15）+ `save_trading_calendar` / `is_trading_day`
- `market_is_open()` 认节假日（有日历数据查表、没有回退 weekday）
- `tests/test_tradetime.py`（21 条特征测试）+ `tests/test_szse.py`（P1/P2/P4/P5/P6）

一句话：**把「某天开不开市」从一条 weekday 猜测，变成一份可追溯到交易所官方响应的
事实**——在能连通交易所的环境里。

---

## 为什么这么做

### 一、这一批真正的工作量在「选源」，不在写代码

`tradetime.py` 的 `market_is_open()` 一直是纯 weekday 判据：周末返回 False，工作日按
竞价时段返回。它的文档字符串自己写着「已知边界：不认节假日」——工作日上的法定节假日
（元旦、国庆首日……）会被当成开市。修它需要一份**知道节假日、且能看到未来**的日历。

难点在于：A 股交易日历的权威源，要么连不通、要么要重依赖、要么根本不是交易所口径。
dev-workflow 第 1 条要求「设计先探活」——**先用 curl 打真接口**，别对着想象写 fixture。
于是把能免鉴权拿到的都真打了一遍：

| 源 | 本机可达？ | 干净？ | 含未来？ | 结论 |
|---|---|---|---|---|
| **深交所官方 `monthList`** | ❌ TCP 握手后挂死（45s） | ✅ 最干净的官方 JSON | ✅ | 判据最清楚，但本机连不通 |
| 新浪 `klc_td_sh.txt` | ✅ | ❌ 返回**加密串**，要 JS 引擎解 | ✅ | 重依赖，不收 |
| 东财 `RPTA_WEB_TRADE_DATE` | ✅ | ❌ 列周日、无未来、塞 `20311231` 哨兵 | ❌ | 建不出可靠日历 |
| timor.tech 放假 API | ✅ | ❌ **办公日历**不是交易所日历 | ✅ | 会把非交易日当交易日 |
| 新浪日线 bars（已在用） | ✅ | ✅ 口径准 | ❌ 只有过去 | 盘中判不了今天是不是节假日 |

两处最值得记的坑，见下面「坑」一节。这里先说结论：**深交所官方 monthList 是唯一
一手官方、结构化、每天带交易标志、缺日当场抛错的源**，判据最清楚，字段形状与仓库
既定的接口参考 `a-stock-data` 一致（`jyrq` 交易日期 / `jybz` 交易标志）。

> 通用原则：**选数据源是一次探活，不是一次查资料。** 「哪个源最权威」要用 curl 的
> 真实响应回答，不是靠印象——本章至少有两个候选（timor、东财）在文档上看着能用，
> 一打就露馅。

### 二、一个连不通的源，怎么诚实地建

深交所站点从本项目当前唯一部署环境（WSL）**连不通**——TCP 能握手、随后挂死。这不是
适配器的 bug，是这台机器到交易所站点的网络事实。那还建它吗？建。因为：

1. 它是**判据最清楚的那个源**，选它是对的；换一个能连通但脏/错的源，是拿正确性换
   一次能在本机看到的绿灯——那正是最坏的交换。
2. 「连不通」和「零消费方」是两回事。消费方 `market_is_open()` 真实存在、真实读这张表，
   读取关系被测；只是**数据的写入**取决于网络可达性。真实抓取会在能连通交易所的运行
   环境里把日历填进来，届时 `market_is_open()` 自动从回退切到查表。

诚实建法的三条：

- **解析层与联网层分开**（照 `sina.py` 的形状）：`parse_trading_calendar` 是不联网的
  纯函数，由离线 fixture 完整测；`fetch_trading_calendar` 是联网薄函数，不进离线测试。
- **落库链用可注入的 fetcher 测**：`refresh_trading_calendar(..., fetcher=桩)` 让离线
  测试喂一份固定 `TradingCalendar`，断言 raw 落盘、fact 落表——不出网也能验证整条链。
- **把约束写进代码注释和 CHANGELOG**：`szse.py` 模块头、`market_is_open` 文档字符串、
  `architecture.md` §5.3.6 都写明「本机连不通⇒空表⇒回退 weekday（安全方向）」。

> 通用原则：**认证/取数类故障，第一反应总会怪新系统。** 一个「空表」在本环境是网络
> 事实、不是缺陷，但排查方向天生容易错。把它写在三个地方，是为了让下一个人在
> `fact_trading_calendar` 为空时，先想到「是不是连不通」而不是「是不是代码坏了」。

### 三、回退方向绝不能倒（红线 R-3）

`is_trading_day()` 有三种返回：`True`（已知交易日）、`False`（已知休市）、
`None`（日历没覆盖到）。`market_is_open` 对 `None` 的处理是**回退到 weekday 判据**，
绝不把 `None` 当 `False`：

```
查不到 → 当「可能开市」→ 依赖它的静默判据顶多多报一条缺失项 → Card 更保守   ✅ 安全
查不到 → 当「休市」    → 真实交易日里以为休市 → 该报的故障不报            ❌ 危险
```

这不是洁癖。`market_is_open` 的一个消费方 `news-scan` 用它决定「此刻是否连续竞价、
源静默算不算故障」。把「查不到」当「休市」，会在真正的交易日里静默关掉故障判据。
探针 P5 钉死：无日历数据 / 超出已抓范围时，结果与批 L 之前**逐一相同**。

### 四、为什么 `session_in_progress()` 偏偏不改

分发提示词把这个判断留给了建造会话。答案是**不改**，理由值得写下来：

`session_in_progress(trade_date, retrieved_at)` 回答的是「**这批数据声明的交易日**过完
了没」——纯时间比较：声明的 `trade_date` 是不是今天、且未到收盘。它跟「今天是不是
节假日」不是同一个问题。给它加节假日感知会把 `emotion-calc` 推向危险方向：

```
节假日今天，emotion 源返回 qdate=今天、涨停=0
  现状：session_in_progress=True  → emotion 判「这一天还没产生这个数」→ 报缺失   ✅ 安全
  若加节假日感知：today 是节假日 → session_in_progress=False
                                → emotion 判「这个 0 是真的」→ 冰点            ❌ 危险
```

它在周六上午也返回 True（那天的数据确实还没「过完」）——这是它文档字符串写明的
**设计**，不是缺陷。所以这一批不但不改它，还给它补了特征测试，防后续会话看它「也不认
节假日」就顺手一起改了。

> 通用原则：**两个名字相近的判断，先确认它们回答的是不是同一个问题。** `market_is_open`
> 问「此刻」、`session_in_progress` 问「这批数据声明的那天」——同样一句「认不认节假日」，
> 对前者是修缺陷，对后者是引入危险。改之前把问题写清楚，比改之后回滚便宜。

### 五、先写特征测试，再改

`market_is_open` / `session_in_progress` 改之前 `grep -rl market_is_open tests/` 零命中
——没有基线，改完就没法说「行为变了没变」。所以先写 21 条特征测试**锁住改之前的
行为**（含元旦 09:31 现状返回 True 这条缺陷——特征测试固定的是「现状」，不是「正确
答案」），跑绿，再改。改完这 21 条**仍然绿**（回退分支 = 改之前），正确答案由
`test_szse.py` 里喂了日历数据的新测试断言。

> 通用原则：**改一个没有测试的函数，第一步是给它补特征测试，不是改它。** 特征测试
> 锁的是「现状」（哪怕现状有缺陷），它让你之后能区分「我修的那一处」和「我不小心
> 碰坏的别处」。

### 六、这张 `fact_*` 表为什么是「第一张」

`architecture.md` §5.2 画了很久的 `raw → fact → derived` 分层图，在这一批之前
**只有 raw 层被实例化过**——十四张表里没有一张 `fact_*`，「归一化事实层」只存在于
文档。交易日历体量小、判据清楚、非行情，正好用来把这一层第一次做成真实 schema，
给 §45「第一版完整市场数据」那一批（security_master / adjustment_factors / EOD
bars……）打样。

⚠️ **不接 `SnapshotCoordinator`。** 那套（批 D）解决的是「同一次决策运行内，多个
Specialist 必须看同一份易变网络数据」——冻一次、大家从冻结切片读。交易日历完全不是
这个形状：一年抓几次、只读查表，「两个 Specialist 会不会看到不同版本的日历」根本不是
要防的风险。硬套会引入一套不必要的每次-运行开销。日历落 raw 直接调 `save_raw_snapshot`。

---

## 执行

选源阶段的真实探活（节选，全部在临时目录跑）：

```console
$ curl -s -m20 "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt" | head -c 80
var datelist="LC/AAApNDXCw6mHbaPgkryxXv10eAJP1LW0SD39aT7+NV44Xba3PxCgTdrp5Bk...   # 加密串

$ curl -s -m45 "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=2026-9"
# HTTP 000，45s 超时 —— TCP 握手成功后挂死

# 用已在用的新浪日线交叉核实「交易所到底开不开市」（调休日的关键一问）
$ python3 - <<'PY'
# sh000001 最近 200 根日线里，这些「调休上班日」有没有成交
2026-01-04 (Sun): traded=False    # timor 标它是工作日，交易所不开市
2026-02-14 (Sat): traded=False
2026-09-20 (Sun): traded=False
2026-01-01 (Thu): traded=False    # 元旦：工作日上的节假日 —— 要修的正是它
PY
```

建表与迁移：

```console
$ python3 -c "import sys;sys.path.insert(0,'skills');from _store.schema import SCHEMA_VERSION;print(SCHEMA_VERSION)"
15
```

特征测试先跑绿（改之前）：

```console
$ python3 -m pytest tests/test_tradetime.py -q
.....................                                                     [100%]
21 passed
```

改完 `market_is_open` 后，全套探针测试：

```console
$ python3 -m pytest tests/test_szse.py tests/test_tradetime.py -q
.....................................                                     [100%]
```

---

## 坑

### 坑 1 · 最干净的源连不通，能连通的源全有毒

选源探活里，「文档上能用」和「真能用」差得很远：

- **新浪 `klc_td_sh.txt`**：`akshare` 用它，但响应是**加密串**，解密要起一个
  JS 引擎（`py_mini_racer` 跑一段混淆过的 `hk_js_decode`）。为一张日历引一个 JS
  解释器，与本仓库「薄适配器」的口径冲突。
- **东财 `RPTA_WEB_TRADE_DATE`**：不带 filter 时头几行是干净的历史日期，一加 filter
  排序就冒出一堆重复的 `20311231` 哨兵行；按日期区间查，2026-09-20（周日）也被列成
  「交易日」。**看着能用、一细查就是脏的**。
- **timor.tech 放假 API**：可达、结构干净，但它是**办公日历**——调休上班的周末
  （2026-02-14 等）它标 `holiday=false`（上班日）。而交易所那天**不开市**。用它算
  日历，会把非交易日当交易日——最危险的那种静默错。这条是靠新浪日线**实测**核实的
  （那几天 `traded=False`），不是靠印象。

结论是深交所官方源，但它从本机 TCP 握手后挂死。**不因为它连不通就退而求其次**——
换脏源是拿正确性换一次本机绿灯。改成：解析层离线测、落库链桩测、真实抓取留给能连通
的运行环境；本机 `market_is_open` 恒走安全回退。

### 坑 2 · 文件名叫 `calendar.py` 会和标准库撞

第一版想把模块命名为 `calendar.py`，但解析要算「某月有多少天」，`import calendar`
在一个自己也叫 `calendar.py` 的文件里是**歧义源头**。两个办法：不 import 标准库
`calendar`（改用 `date` 相减算月末），以及**按既定惯例给适配器起源名**——现有四个
适配器都叫 `sina.py`/`eastmoney.py`/`tencent.py`（按数据源主机命名），日历来自
深交所，就叫 `szse.py`。既合惯例，又躲开撞名。

### 坑 3 · 新表 / 新源会触发一批「你没想到要满足」的守卫

代码写完跑全量，红了四处，全是既有守卫在尽责：

- `test_每张表都有只追加触发器`：新表默认要接 `_append_only()`，不接就红（这正是
  探针 P3 要的）。
- `test_每个源都要声明自己有没有服务端时刻`：`_sources` 里每个带 `raw` 字段的结果类
  都要显式声明 `server_as_of`——日历没有单一时刻，显式写 `server_as_of = None`。
- `test_每个入口都在设计文档里被提过`：`skills/_*/*.py` 算「入口」，`szse.py` 必须在
  `docs/design/` 里被提到（补进 `architecture.md` §5.3.6）。
- `test_文档里的测试条数与实测一致`：加了测试，条数要 `sync_test_count.sh` 同步。

> 通用原则：**加一个新组件，等于向一批你没写的守卫报到。** 它们红不是坏事——每一条都在
> 逼你补一件真该补的东西（触发器 / 时刻声明 / 文档 / 计数）。按它们指的路补齐即可。

---

## 验证

```console
# 1. schema 到 v15，表和触发器都在
$ python3 -c "import sys;sys.path.insert(0,'skills');import tempfile,os;from _store import db;\
d=tempfile.mkdtemp();p=os.path.join(d,'t.db');db.init_schema(p);\
c=db.connect(p,readonly=True).__enter__();\
print('trigs',[r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'fact_trading_calendar%'\")])"
trigs ['fact_trading_calendar_no_update', 'fact_trading_calendar_no_delete']

# 2. 探针测试全绿
$ python3 -m pytest tests/test_szse.py tests/test_tradetime.py tests/test_store.py -q
（全绿）

# 3. 全量回归
$ python3 -m pytest -q
（全绿）
```

预期：三条都绿。真实抓取（`fetch_trading_calendar`）在本环境会 `SourceError`——
那是网络事实，不是验证项；验证项是解析、落库、日历感知、安全回退这四条离线可测的链。

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | **选数据源是一次探活，不是查资料**——timor（办公日历≠交易所日历）、东财（脏数据）都是「看着能用、一打露馅」，靠 curl + 交叉核实才排掉 |
| 2 | A 股交易日 = **工作日 AND 非法定节假日**；调休上班的周末交易所**不开市**（新浪日线实测），所以办公日历不能直接当交易日历用 |
| 3 | 最干净的源（深交所官方）本机连不通就**诚实地建**：解析层离线测、落库链桩测、真实抓取留给能连通的环境；本机恒走安全回退 |
| 4 | 「连不通」≠「零消费方」：消费方 `market_is_open` 真实读表、关系被测，只是**写入**取决于网络可达性 |
| 5 | 回退方向绝不能倒（R-3）：`is_trading_day` 查不到返回 `None`，当「可能开市」多报缺失（安全），绝不当「休市」（危险） |
| 6 | `session_in_progress` **不改**：它问「这批数据声明的那天过完没」，不是「今天是不是节假日」；加节假日感知会让 emotion 把节假日的 0 当真冰点 |
| 7 | 改无测试的函数，第一步是**补特征测试锁住现状**（哪怕现状是缺陷），再改 |
| 8 | 第一张真实 `fact_*` 表：**不接 `SnapshotCoordinator`**——那解决「同一次运行内多消费方看同一份易变数据」，日历是低频只读查表 |
| 9 | 文件按源名（`szse.py`）不按功能名（`calendar.py`）——合惯例，且躲开标准库撞名 |
| 10 | 加新组件 = 向一批没写的守卫报到（只追加触发器 / `server_as_of` 声明 / 入口进设计文档 / 测试计数）——每条红都在逼你补一件真该补的 |

> ⏩ **后续变动（2026-09-23，批 H-I）**：本章出现的 `skills/_contract` / `skills/_store` /
> `skills/_sources` 三个共享包，其**真实实现**已迁至
> `src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export 薄壳 ⇒
> 本章正文里的 `from _contract import ...` 等导入语句与位置描述**照旧成立**，只是代码
> 本体不在那儿了。见 `CHANGELOG.md` 批 H-I。
