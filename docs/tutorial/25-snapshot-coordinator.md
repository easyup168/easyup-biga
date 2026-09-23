# 第 25 章 · 冻结一次、多处读：SnapshotCoordinator 的地基

> 📚 **过程** · 写完即冻结（后续变动只在末尾追加「⏩」指针）
> **覆盖**：确定性编排批 D-I —— 为什么把「抓取」和「读取」拆开；为了不写第二套
> 解析而把 `fetch_index_daily` 拆成 `fetch` + `parse`；manifest 为什么必须能反查而
> 不是好看；以及**只建地基、不改 Specialist** 这个刻意的分批；分发提示词里一个
> 过期的数字（technical 是 120 不是 25）怎么被实测抓出来
> **不覆盖**：让 Specialist 真的改口读冻结快照（批 D-II）；breadth/pool/news/emotion
> 的迁移（批 D 后两段）；Facts/Assessment 拆分（批 E）

---

## 目标 / 产出

- `skills/_snapshot/`（`SnapshotCoordinator`）—— `freeze_index_daily` / `read_index_daily`
  / `frozen_snapshot_ids`，证明「一次决策里同一份数据只抓一次、多个消费者读同一份」
- `_sources/sina.py` 抽出纯函数 `parse_index_daily`（读端重建 `IndexDaily` 不复制解析）
- `_contract.new_evidence_set_id()`；`_store.save_evidence_set` / `load_evidence_set`
- `tests/test_snapshot.py`：五道探针 + fail-closed + 解析抽取锁，985 条离线全绿（956 → 985；
  其中 `test_snapshot.py` 25 条，另 4 条是本章这份教程文件被文档守卫参数化扫到的）
- 🔴 **market/sector/technical 一个字没改** —— 这一批不改变任何 Specialist 的行为

---

## 为什么这么做

### 问题：三个 skill 各抓一次同一份日线，而「同一份」无人保证

`skills/_sources/sina.py::fetch_index_daily` 现在被三处**各自独立**调用：

```
market-calc     fetch_index_daily("sh000001"/"sz399106", bars=25)
sector-calc     fetch_index_daily("sh000001",            bars=2)
technical-calc  fetch_index_daily("sh000001",            bars=120)
```

三次网络调用，各落一行 `raw_market_snapshot`。它们**理论上**该拿到同一份数据 ——
但没有任何机制保证。盘中两次调用之间指数在动，三个 Specialist 完全可能站在
三个不同的瞬间上，而卡面上看不出来。设计文档 §4 的身份模型早就给这件事留了位子
（`evidence_set_id`：一片被冻结的数据），只是一直是空的 —— 「所有 Specialist 看的是
同一份数据」这句话**当前无法验证**。

### 解法：把「抓取」和「读取」拆成两个动作

```
freeze_index_daily(decision_id, symbols, bars) -> evidence_set_id
    每个 symbol 只真实抓一次（取全量），原样落 raw，登记一行 evidence_sets

read_index_daily(evidence_set_id, symbol, bars) -> IndexDaily
    从已冻结的 raw 切出要的根数，不联网
```

一次决策开始时冻结一次；之后所有消费者都从冻结集里**切片**。sector 要 2 根、
market 要 25 根、technical 要 120 根 —— 都从同一份底层数据切，是**同一次抓取**，
不是三份偶然相等的结果。

> 🔴 为什么不做成「一个 `get_index_daily`，第一次调用去抓、后面的走缓存」？
> 那样「什么时候抓」还是由**第一个碰它的消费者**隐式决定 —— 谁先跑、用多少根，
> 都会影响冻的是什么。拆成显式的 `freeze`（编排器在 Stage 0 主动做一次）+ `read`
> （消费者只读），才把「这次决策站在哪个数据基准上」变成一个**在一处做出的决定**，
> 而不是六个 Specialist 各自的运气。这正是设计文档 §6 批 D 那句「从各自的发现变成
> 冻结集的一个声明属性」。

### 为什么把 `fetch_index_daily` 拆成 `fetch` + `parse`

读端要从**存下来的 raw** 重建 `IndexDaily`。raw 存的就是新浪返回的那个 dict 数组
（`raw_market_snapshot.payload_json`）。重建它需要一套解析：dict → `DailyBar`、日期
归一化、升序/去重校验。这套解析**已经存在** —— 就在 `fetch_index_daily` 里。

如果在 `_snapshot` 里再写一遍，就是同一判据两份实现（L-3，本仓库最贵的教训之一）。
两份解析某天漂开一点（比如一处放宽了日期格式），两条路径对「什么样的日线算合法」
给出不同答案，而且**不报错**。

⇒ 把解析抽成纯函数 `parse_index_daily(symbol, payload)`：

```
fetch_index_daily = 校验 symbol + 拼 URL + get_json + parse_index_daily
read_index_daily  = 从冻结的 raw 切片 + parse_index_daily
```

两条路径共用同一套形状校验。抽取是**纯空操作**（解析逻辑一字未改），由
`TestParseExtraction` 锁住：既测正常解析，也逐条测每一个 `raise` 仍在 —— 证明抽取
没顺手丢掉某个校验。

### fail-closed：读不出就报错，绝不给「少一点」

冻了 25 根、消费者要 120 根，怎么办？**抛错**，不是静默返回 25 根。

静默返回更少 = 上层拿到一个「看起来正常、其实不够」的结果，然后拿它去算 120 日
均线 —— 算出来的是个用 25 根凑的数，带着完整的格式和错误的含义上卡。这是 R-3 /
L-2 的形状（算不出来必须说算不出来）。所以 `read_index_daily` 里 `bars > len(raw)`
一律 `SnapshotReadError`。

### manifest 必须能反查，不是好看

`evidence_sets.manifest_json` 存这次冻结的登记。它的判据不是「有几个字段」，而是
**能不能从 evidence_set_id 走回它冻的那几行 `raw_market_snapshot`**。所以每个 symbol
的条目记 `snapshot_id`（raw 那行的主键）。`frozen_snapshot_ids()` 就是这条反查 ——
有了它，「所有 Specialist 看同一份数据」才是一句可以被程序核对的话。

> 存储层（`_store.save_evidence_set`）对 manifest 的**结构不做假设**，只负责严格 JSON
> 落库。manifest 长什么样、怎么反查，是冻结方（`_snapshot`）的事。存储层若也内嵌
> 一份「manifest 该有哪些键」，就成了第二处要跟着 manifest 演进的地方 —— 又是 L-3。

### 为什么只建地基、不改 Specialist

切 market/sector/technical 去读冻结快照，会改变现有六个 skill 的**实际行为** ——
那是 D-II，风险高得多。D-I 先把「冻结一次、多处读」这件事在 `fetch_index_daily`
（共用最多的那个源）上建好、用探针验实。地基没验实就切生产调用点，等于带着
「这层大概没问题」的假设改六个 skill 的行为。

代价要诚实记下来：D-I 做完之后，`freeze_index_daily` **还没有编排层调用方**。它现在
的读取方是 `read_index_daily` + 探针。这是分批施工里一段**显式登记**的中间态（和批 B
「只建 `evidence_sets` 表、无生产方」同形），不是零消费方死配置（L-1）—— 区别在于
它被写进了 `TODO.md`「批 D-I 施工空档」，而不是悄悄躺着等人发现。

> 通用原则：分批迁移里，一段「建了但还没接」的中间态是可以的 —— 前提是它被**显式
> 登记**在案，有明确的下一步接它。没登记的那种，才是孤儿。

---

## 坑

### 坑 1 · 分发提示词说 technical 取 25 根，实测是 120

分发提示词把三个调用点列成 `technical … bars=BAR_COUNT # 默认 25`。照着写探针会得到
「冻 25 根就够所有人」的结论。实测 `technical_calc.py`：

```
BAR_COUNT = 120     # 不是 25
```

market 才是 25，sector 是 2。所以 `sh000001` 的最大消费者是 technical 的 **120**。
这不改变 D-I 的机制（`freeze` 的 `bars` 本来就是调用方传的参数），但它是 D-II 接线时
的一个硬输入：冻结 `bars` 要取 120，否则 technical 读 120 会撞 fail-closed。已记进
`TODO.md`「批 D-II 输入」。

> 教训还是那条：**先看代码里的字面量，别信转述的数字**（哪怕转述来自你自己写的
> 分发提示词）。差一个数不会报错，只会让 D-II 接线时莫名其妙撞一个 fail-closed。

### 坑 2 · 测试的假数据必须是真解析器**认**的数据

第一版计数桩这样造日期：`f"2026-08-{i+1:02d}"`。i 到 119 时得到 `"2026-08-120"`。
而 `parse_index_daily` 归一化日期用 `str(row["day"])[:10].replace("-","")` —— 取前 10 个
字符，`"2026-08-120"[:10]` = `"2026-08-12"`。于是第 12 天和第 120 天归一化成同一个日期，
顺序乱了，解析器当场抛「未按日期升序」。

红的是我的**桩**，不是被测代码。这正是 `dev-workflow` 第 3 条那句「探针第一次红，
先怀疑探针」。修法是让桩用真日历日期（`date(2026,3,1)+timedelta(days=i)`）——
**桩要产出真解析器认的数据**，否则你测的是桩自己的 bug。

### 坑 3 · 建了新文件，文档守卫当场拦下

`skills/_snapshot/coordinator.py` 一落地，`test_docs_convention.py::test_每个入口都在
设计文档里被提过[coordinator.py]` 就红了 —— `docs/design/` 里一个字没提它。这条守卫
（第 19 章那批「代码 → 文档」的孤儿检查）要求每个 `skills/_*` 共享层入口都在设计文档
里有交代，而且不是补一行文件名，是要说清楚**它解决什么问题**。补进 `architecture.md`
§6.3 之后才绿。守卫干的正是它该干的事。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 1. 五道探针 + fail-closed + 解析锁，全离线
python3 -m pytest tests/test_snapshot.py -q
# 期望：全绿（25 条）

# 2. 冻结一次、多处读，用一个计数桩看底层抓了几次
python3 - <<'PY'
import sys, tempfile, os
from datetime import date, timedelta
sys.path.insert(0, "skills")
db = tempfile.mktemp(suffix=".db"); os.environ["BIGA_DB_PATH"] = db
from _store import init_schema, connect; init_schema(db)
from _snapshot import SnapshotCoordinator
import _sources as S
calls = {"n": 0}
def fake(symbol, *, bars):
    calls["n"] += 1
    rows = [{"day": (date(2026,3,2)+timedelta(days=i)).strftime("%Y-%m-%d"),
             "open":3000.0+i,"high":3010.0+i,"low":2990.0+i,"close":3000.0+i,
             "volume":10_000_000+i} for i in range(bars)]
    return S.parse_index_daily(symbol, rows)
c = SnapshotCoordinator(fetcher=fake, path=db)
esid = c.freeze_index_daily("BIGA-20260302-001", ["sh000001","sz399106"], bars=120)
c.read_index_daily(esid,"sh000001",bars=2); c.read_index_daily(esid,"sh000001",bars=25)
c.read_index_daily(esid,"sh000001",bars=120); c.read_index_daily(esid,"sz399106",bars=25)
with connect(db, readonly=True) as x:
    n_raw = x.execute("SELECT COUNT(*) FROM raw_market_snapshot").fetchone()[0]
print(f"底层抓取次数={calls['n']}（期望 2）  raw 落盘行数={n_raw}（期望 2，不是 6）")
print(f"反查 snapshot_ids={c.frozen_snapshot_ids(esid)}（期望 2 个）")
os.remove(db)
PY
# 期望：底层抓取次数=2  raw 落盘行数=2  反查=2 个
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 三个 skill 各抓一次同一份日线，「同一份」当前无人保证 —— `evidence_set_id` 那个位子一直空着 |
| 2 | 把「抓取」和「读取」拆开：`freeze` 抓一次并冻结，`read` 只切片不联网 |
| 3 | 拆成显式的 freeze/read，是为了让「这次决策站在哪个数据基准上」在**一处**决定，不是六个 Specialist 各自的运气 |
| 4 | 读端要重建 `IndexDaily` ⇒ 把 `fetch_index_daily` 拆出纯 `parse_index_daily`，两条路径共用一套解析（不写第二份，L-3） |
| 5 | 读不出足够根数一律报错，绝不静默返回更少 —— 那是 fail-open（R-3 / L-2） |
| 6 | manifest 的判据是「能反查回哪几行 raw」，不是「有几个字段」；存储层对 manifest 结构不做假设 |
| 7 | D-I 只建地基、不改任何 Specialist（切生产调用点是 D-II）；`freeze` 暂无编排层调用方，是**显式登记**的施工空档，不是孤儿 |
| 8 | 坑：分发提示词说 technical 取 25 根，实测 120 —— 先看代码字面量，别信转述的数字 |
| 9 | 坑：计数桩第一版造出 `2026-08-120`，被日期归一化截成 `2026-08-12` —— 探针第一次红先怀疑探针；桩要产出真解析器认的数据 |

> ⏩ **后续变动（2026-09-23，批 H-I）**：本章出现的 `skills/_contract` / `skills/_store` /
> `skills/_sources` 三个共享包，其**真实实现**已迁至
> `src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export 薄壳 ⇒
> 本章正文里的 `from _contract import ...` 等导入语句与位置描述**照旧成立**，只是代码
> 本体不在那儿了。见 `CHANGELOG.md` 批 H-I。
