# 第 50 章 · 五份口径收成一份，顺便发现规则写宽了（裁定 16 · 批 2）

> 📄 **过程** · 写完即冻结
> **覆盖**：`domain/provenance.py`（溯源归属的唯一实现）、派生值拿回溯源的政策改动、
> 生产库围栏（测试进程不许开 `data/biga.db`）｜
> **不覆盖**：`kind` 在各 skill 的显式声明、`input_evidence_ids` 的接线 ——
> 两者都要「引用别的证据」这套管道，与 risk 跨 verdict 是同一块，留给批 3

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `domain/provenance.py` | `resolve_provenance` / `underlying_source` —— 唯一实现 |
| 政策改动 | 派生值**点名了单一来源**就保留溯源（实测 770 条受益）|
| 五处本地实现删除 | market / emotion / sector / technical / news |
| `conftest._no_production_db` | 测试进程打开 `data/biga.db` 直接报错 |
| 探针 | `test_provenance_resolution.py` 12 条 + `test_production_db_fence.py` 8 条，7 处 sabotage |

## 为什么这么做

### 1. 同一个判断，五个 skill 各写了一遍

```python
market     _lookup(source, table)            # 带最长前缀匹配
emotion    _raw_hash_for(source)             # 纯 dict.get
sector     raw_hash_for(source)              # 纯 dict.get
technical  None if derived else raw_hash     # 布尔标志 + 单值
news       None if source.startswith("derived:") else ...
```

五份都在回答「这条证据出自哪份原始响应」。**重复不是主要代价** ——
真正的代价是改一处忘四处时，剩下那四处仍然静默照旧。

> 通用原则：判断 L-3 的标准不是「代码长得像」，是「**改了一处，另几处会不会跟着对**」。
> 这五份连写法都不一样，正说明它们已经各自漂过一次了。

### 2. 规则的理由是对的，范围写宽了

五份的共同规则是「`derived:` 开头 ⇒ 一律返回 None」，注释写得很好：

> 派生字段没有单一来源，返回 None —— **不硬凑**：凑出来的溯源比没有溯源更糟，
> 它会让人以为查得到。

理由无可挑剔。但很多派生值的 `source` **本身就点名了单一来源**：

```
derived:sina:kline/sh000001     ← 「我是从这一份 K 线算出来的」
```

剥掉前缀就是一个真实的表键。它**确实**出自那一份原始响应，指回去不是硬凑，
是如实记录。实测生产库 1721 条派生证据里 **770 条**属于此类 ——
溯源信息一直都在，只是被这条过宽的规则丢掉了。

> 通用原则：一条规则的**理由**正确，不代表它的**范围**正确。
> 「派生值没有单一来源」对一部分派生值成立，规则却按全部派生值写。
> 复查规则时要分别问：理由对不对？范围是不是正好覆盖理由成立的那些情况？

### 3. 匹配方向只能有一个，而我一开始把它判反了

第一版的唯一实现写成了「**只认精确命中**，不做前缀匹配」，理由写得振振有词：
前缀匹配会带来歧义。

然后 `test_market单源证据都有raw_hash` 红了：

```
这些单源证据没有 raw_hash，追不回原始响应：['turnover_sh', 'turnover_sz']
```

查下去发现 `turnover_sh` 的 source 是 `tencent:quote/sh000001`，
而表键是 `tencent:quote` —— source **比表键更具体**。
前缀匹配在这里是承重的，不是历史包袱。

正确的规则是**方向只能有一个**：

| 情形 | 判定 | 理由 |
|---|---|---|
| source 比表键**更具体**（子路径）| 命中 | 子路径只是在那一份响应里定位，溯源仍成立 |
| source 比表键**更泛** | **不命中** | 它跨多份快照，挑任何一个都是硬凑 |

`derived:sina:kline`（market 的 `volume_total`，值是沪深两市之和）落在第二行 ——
返回 None 是对的。那种多输入要用 `input_evidence_ids` 表达。

> 通用原则：反方向的匹配（「表键以 source 为前缀也算」）看起来更宽容，
> 实际是把「我没法确定是哪一份」翻译成「就算它是某一份吧」——
> **R-3 的形状**，最该防的那种。

#### 🔴 顺带更正我自己上一批报错的一个诊断

批 1 的 TODO 与 commit 里写着：

> `market` 的 source 写成泛化的 `sina:kline`，而冻结表的键是 `sina:kline/sh000001`，
> 前缀匹配要求 source 比键更长 ⇒ 匹配不上，**静默返回 None**。病因已定位。

「前缀匹配要求 source 比键更长」这句**是对的**（外部复核也核实过）。
但由它推出的结论**是错的** —— 那不是 bug：

```python
today_vol = sum(d.last.volume for d in c.daily.values())   # 沪 + 深
add("volume_total", ..., "sina:kline")
```

它**真的没有单一来源**，泛化 source 与 `raw_hash=None` 都是诚实的。
当初点名的 4 个「缺口组合」里，3 个是跨源（`volume_total`、`trade_date`、
`board_counts`），只有 1 个（`emotion` 的 `em:push2ex/qdate`）是真的键对不上。

> 通用原则：**「机制描述正确」不等于「结论正确」。**
> 我准确地描述了 `startswith` 的方向，然后把它当成了病因 ——
> 中间少走了一步：去看那个 source 为什么是泛化的。
> 这是本次会话第二次犯同一个形状（上一次是 E-18，由用户拦下）。

### 4. `probe.sh` 存在，同一件事还是又发生了一次

外部复核清点派生证据时发现六类相加差 2 条。查下去：
`task_id=BIGA-20260302-001`、`field=x`、`source=derived:x`，同一秒写入 ——
**测试里那个硬编码 TID 进了真库**，来自一次没设 `BIGA_DB_PATH` 的探针。

`tools/verify/probe.sh` 正是为第一次同类事故写的。它存在，事故仍然重演。

> 通用原则：**工具不等于守卫。**
> `probe.sh` 让「正确做法」变得更短，但没有让「错误做法」变得不可能。
> 只要错误路径仍然畅通，剩下的就全靠记性 ——
> 而记性正是追加式存储惩罚的东西。

修法与本仓库那条禁网围栏同款：判据从文档搬进进程里。
`conftest.py` 加一条 autouse fixture，测试进程打开 `data/biga.db` 直接抛错。

⚠️ **读也拦。** 只读打开会让测试依赖这台机器上恰好有什么数据 —— 与禁网同一个理由。
真要生产数据就搬去 `tools/verify/`，那里本来就是干这个的。

#### 围栏一上线就抓到 17 条

加完围栏跑全量，`test_news_scan.py` **17 条当场红**：

```
tests/conftest.py:116: ProductionDbUsedInTest
  这条测试打开了生产库 .../data/biga.db
  ← is_trading_day() 不传 path ⇒ 一路走到 DEFAULT_DB_PATH
```

它们调 `is_trading_day()` 判断「今天是不是交易日」，没传 `path`，
于是读的是**生产库的交易日历**。这些测试从此依赖这台机器上恰好有什么数据，
而没有任何一行代码说过它想要这个 —— 它们只是"碰巧"一直是绿的。

> 通用原则：一道新守卫**第一次运行就抓到东西**，是它值得存在的最强证据。
> 反过来，装上去就全绿的守卫要多看一眼：它可能在守一个不会发生的事。

于是补了**第二层**：一条 autouse fixture 让每条测试默认拿到自己的 tmp 库路径。
两层的分工是清楚的 —— **默认安全**（fixture）+ **绕开就报错**（围栏）。
只有围栏而没有默认值，等于让几十条测试各自记得设环境变量，又回到靠记性。

## 执行

```bash
src/easyup_biga/domain/provenance.py      # 新增：唯一实现
src/easyup_biga/domain/__init__.py        # 导出（_contract 薄壳自动转发）

skills/{market,emotion,sector}-calc/...   # 删本地实现，改调 resolve_provenance
skills/technical-calc/...                 # 单来源 → 建一条目的表，走同一份实现
skills/news-scan/...                      # 同上

tests/conftest.py                         # _no_production_db 围栏
```

## 坑

### 坑 1 · 守卫互相绊了一下：围栏自己要用裸 `sqlite3`

围栏要补丁 `sqlite3.connect` 才能拦住**任何**打开方式，而仓库有另一条守卫
「`_store` 之外不许 import sqlite3 / 调 sqlite3.connect」。于是新围栏一写完，
那条守卫就把 `conftest.py` 和围栏的探针文件判成违例。

两条守卫都是对的，冲突是真实的：走 `_store` 只能证明**那一条路**被拦，
证明不了裸调用被拦。仓库已经给了正解 —— `# store-exempt:` 标记加理由，
`runtime.py` 早就在用（它读 OpenClaw 的外部库）。

> 通用原则：守卫之间冲突时，先找仓库**既有的豁免机制**，别急着改守卫。
> 豁免要写理由，理由本身就是给下一个人的说明。

### 坑 2 · 我又用 `git checkout` 毁了自己未提交的改动

sabotage S5 破坏了 `news_scan.py`，还原时顺手敲了 `git checkout <file>` ——
它恢复到 **HEAD**，而批 2 尚未提交，于是那个文件的全部改动没了。

这是第二次。第一次记在教程里的结论是「改用 `cp` 从备份还原」，
而我**给其他文件备份了，唯独 sabotage 到的这个没有**。

> 通用原则：sabotage 的还原手段必须和备份范围**同时**确定。
> 「我备份了」和「我备份了**这一个**」是两回事。

好在全量立刻变红（少了一处 import），一眼看得出来。

### 坑 3 · 断言写得比意图宽

批量插 import 时写了 `assert "resolve_provenance" not in "".join(lines)` ——
意图是「import 块里还没有它」，写出来是「**整个文件**里还没有它」。
而 market 早就被我改过、文件里有它的**调用**，于是第一次运行就炸在 market 上。

> 通用原则：断言的范围要等于意图的范围。这条断言没造成损害（它保守地拦下了），
> 但它拦的理由和我想拦的理由不是一回事 —— 下次可能就反过来。

### 坑 4 · 围栏按文件名判会误杀几十处

第一版差点写成「文件名是 `biga.db` 就拦」。sabotage 验证时改成这样，
**22 个测试文件当场红** —— 全仓几十处 tmp 夹具都叫 `tmp_path / "biga.db"`。

判据必须是**解析后的绝对路径**。这条已经写成测试
（`test_同名但不同目录的库不被误拦`），免得将来有人「简化」它。

## 验证

```bash
cd ~/.openclaw-biga/workspace

python3 -m pytest tests/test_provenance_resolution.py \
                  tests/test_production_db_fence.py -q        # 期望 20 passed

# 派生值真的拿回了溯源（离线，用桩）
tools/verify/probe.sh python3 - <<'PY'
import sys, json, importlib.util, pathlib
from datetime import date, timedelta
sys.path.insert(0, "skills")
spec = importlib.util.spec_from_file_location(
    "technical_calc", "skills/technical-calc/scripts/technical_calc.py")
tc = importlib.util.module_from_spec(spec); sys.modules["technical_calc"] = tc
spec.loader.exec_module(tc)
from _sources import parse_index_daily
rows = lambda n: [{"day": (date(2026,3,2)+timedelta(days=i)).strftime("%Y-%m-%d"),
                   "open":3000.0+i,"high":3010.0+i,"low":2990.0+i,
                   "close":3000.0+i,"volume":10_000_000+i} for i in range(n)]
tc.fetch_index_daily = lambda s, bars=25: parse_index_daily(
    s, rows(bars), raw_text=json.dumps(rows(bars)))
v = tc.build_fact_bundle(store=False, task_id="BIGA-20260302-001", break_source=set())
der = [e for e in v.evidence if e.source.startswith("derived:")]
print(f"派生 {len(der)} 条，带 raw_hash 的 {sum(1 for e in der if e.raw_hash)} 条")
PY
# 期望：派生 12 条，带 raw_hash 的 12 条（改动前是 0 条）
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 判断 L-3 的标准是「改了一处，另几处会不会跟着对」，不是「代码长得像」 |
| 2 | 五份写法各不相同，本身就说明它们已经各自漂过一次 |
| 3 | 🔴 一条规则的**理由**正确，不代表它的**范围**正确 —— 要分别检查 |
| 4 | 「派生值没有单一来源」对一部分成立，规则却按全部写 ⇒ 770 条白白丢了溯源 |
| 5 | 匹配方向只能有一个：source 比表键更具体 ⇒ 命中；更泛 ⇒ 不命中 |
| 6 | 反方向匹配是把「没法确定是哪一份」翻译成「就算是某一份吧」—— R-3 的形状 |
| 7 | 🔴 **「机制描述正确」不等于「结论正确」** —— 我准确描述了 `startswith` 的方向，却把它当成了病因 |
| 8 | 本次会话第二次犯同一形状（上次是 E-18）：少走了「这个值为什么长这样」那一步 |
| 9 | 🔴 **工具不等于守卫** —— `probe.sh` 让正确做法更短，没让错误做法不可能 |
| 10 | 只要错误路径畅通，剩下的全靠记性，而记性正是追加式存储惩罚的东西 |
| 11 | 生产库围栏**读也拦** —— 只读会让测试依赖本机数据，与禁网同一个理由 |
| 11b | 🔴 围栏一上线就抓到 **17 条** news_scan 测试在读生产库的交易日历 —— 新守卫第一次就抓到东西，是它值得存在的最强证据 |
| 11c | 两层分工：autouse fixture 让**默认安全**，围栏负责**绕开就报错**。只有围栏 = 又回到靠记性 |
| 11d | 守卫之间冲突时先找**既有的豁免机制**（`# store-exempt:`），别急着改守卫 |
| 12 | 围栏判据必须是解析后的**绝对路径**；按文件名判会误杀 22 个测试文件 |
| 13 | sabotage 的还原手段与备份范围必须**同时**确定 —— 我备份了，但没备份被 sabotage 的那个 |
| 14 | 断言的范围要等于意图的范围：写 `not in 整个文件`，意图是 `not in import 块` |
