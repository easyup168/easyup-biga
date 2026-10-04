# 第 47 章 · 冻结得冻到底，时间得说清基准，出处得问来源要（外部评审 E 节 · 二）

> 📄 **过程** · 写完即冻结
> **覆盖**：E-17（递归 `deep_freeze`）、E-19（`source_lag_sec` + `age_at(evaluated_at)`）、
> E-20（采集出处三件事：时刻 / 来源 / 指纹）｜
> **不覆盖**：E-16 的后半（派生值声明 `input_evidence_ids` + `calc_version`）——
> 生产库 411 个 result 字段里 244 个是 `derived:*`，跨 6 个 skill、39 个
> `(agent, field)` 组合，得单开一批；E 节其余项见 `TODO.md` 台账

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `domain/_freeze.py` | `deep_freeze` / `thaw` —— 递归冻结的**唯一实现** |
| `Evidence.source_lag_sec` | `staleness_sec` 改名；它量的是取数滞后，不是年龄 |
| `Evidence.age_at(evaluated_at)` | 问年龄必须说清基准时刻 |
| `AgentVerdict.max_source_lag_sec` / `max_age_at()` | 聚合口径跟着分家 |
| `IndexDaily.provider_id` / `.adapter_version` / `.source` | 出处由 provider 自己声明 |
| `_verify_snapshot_hash` | 读回 raw 时重算指纹，两种口径，fail-closed |
| 探针 | 新增 37 条（17 + 8 + 12），9 处 sabotage 全部验证会红 |

## 为什么这么做

### 1. `frozen=True` 加 `MappingProxyType` 看起来严丝合缝，其实只盖了第一层

契约类早就做了两件事，而且**两件都对**：

```python
@dataclass(frozen=True)          # 挡住重新赋值 v.result = {...}
object.__setattr__(self, "result", MappingProxyType(dict(self.result)))
                                 # 挡住往顶层加删键，且先复制再包（不是视图）
```

代码旁边的注释甚至写明了「frozen 只挡得住重新赋值，挡不住对同一个可变对象原地
mutate —— 必须两者都做」。结论是对的，**执行只做到了第一层**：

```python
inner = [1, 2]
fb = FactBundle(..., result={"k": inner}, evidence=(Evidence(value=inner),))
inner.append(777)
fb.result["k"]          # → [1, 2, 777]
```

`Evidence.value` 更彻底 —— 它连第一层都没包，外部还能事后往里**加新键**。

> 通用原则：一道防护写了正确的理由，不等于它覆盖了那个理由涵盖的范围。
> 注释说「必须两者都做」，代码做了两者——但都只做在最外层。
> **读注释会以为查过了，这正是它比没有防护更难发现的原因。**

要害不是「值变了」，是**时序**：铁律在 `__post_init__` 里校验，穿透发生在那之后。
落库的那个对象与被校验的那个对象不是同一份内容，且全程不报错。

#### 先量一量：这是理论风险还是真的

```
生产库 3152 条证据，value 类型分布：
  float 1401 / int 778 / str 370 / bool 137 / dict 129 / list 335 / None 2
  ⇒ 可变结构 464 条，占 14.7%
```

而且这 464 条**同时也是 `result` 的顶层值** —— 两边经常是同一个对象。

这一点单独值得说：批 P 刚加了「证据值必须与 result 对得上」的交叉校验
（`_same_value`）。当 `result[k]` 与 `evidence.value` 指向同一个对象时，
一次原地 mutate **同时改两边**，那条交叉校验永远相等。

> 通用原则：一道交叉校验被「两个指针指同一处」架空，是最难看出来的那种绿。
> 它不会漏报——它会**如实报告两个相同的值**，只是那两个值是同一个。

### 2. 本批差点把上一批刚关上的门重新推开

`deep_freeze` 接上之后我顺手跑了一下 `_same_value`：

```python
_same_value(deep_freeze({"n": 1}), deep_freeze({"n": 1.0}))
# → True        ← 冻结前是 False
```

原因链条：`_same_value` 取 canonical JSON 判等（`1` 与 `1.0` 算不同，与回放同口径）。
递归冻结之后它拿到的是 `mappingproxy` → `json.dumps` 不认 → 抛 `TypeError` →
函数里那个「序列化不了就退回 `==`」的兜底接住 → `1 == 1.0` 成立。

**全程不报错。** 批 P 的守卫静默失效，而批 P 自己的测试**照样全绿**——
它们比的是没冻结过的裸 dict。

修法是在 `_same_value` 里先 `thaw` 再 `dumps`。真正值得记的是发现方式：

> 通用原则：一处修正可能让另一处的守卫**换条路径**执行。
> 守卫的测试只覆盖它自己那条老路径时，这种失效是全绿的。
> ⇒ 改了被广泛消费的数据形状之后，**回头把消费它的守卫各跑一遍**，
> 别只跑自己这批的测试。

这条最后落成了一条测试（`TestE17不能把批P的门重新打开`），它守的是
**本批自己差点造成的回归**，不是上游的洞。

### 3. 名字把口径说错了，于是用的人也就用错了

`Evidence.staleness_sec` = `retrieved_at - as_of`，量的是「从数据产生到我们取到它，
隔了多久」。它的 docstring 写得很清楚，甚至点名了 F15：

> ⚠️ 这是个差值，对共模误差免疫 —— as_of 和 retrieved_at 一起被算错时它不会有
> 任何异常表现。真正的「离现在多久」用 `age_sec`。

然后 `risk_check.py` 这样用它：

```python
ages = [e.staleness_sec for v in upstream for e in v.evidence]
add("max_staleness_sec", max(ages), "最旧证据的年龄(秒)")   # ← 标签是「年龄」
```

一份三天前冻结的快照，只要当初抓取只花了 2 秒，这个数就是 **2**。

> 通用原则：**docstring 拦不住的，名字拦得住。** 这条 docstring 把陷阱说得明明白白，
> 而误用就发生在同一个仓库里、由同一批人写的代码中。
> 名字是唯一一处**每次使用都会被读到**的文档。

#### 诚实地说清它今天的严重性

我量了生产库，没有把它说得比实际更糟：

```
3152 条证据：source_lag 与「决策时真实年龄」的最大差距 = 87 秒
超过 1 小时的 0 条，超过 1 天的 0 条
```

差距小是因为**每次运行都现抓**，于是 `retrieved_at ≈ 决策时刻`。而且
`max_staleness_sec` **不在 `THRESHOLDS` 里，不闸任何东西** ——
它只是印在 Decision Card 上给人看的一个数。

所以这不是一个正在发生的自动 fail-open，是一个**潜伏的错名字**：
今天两个量碰巧接近，一旦冻结快照开始跨运行复用，它们就分家，
而那时没人会想起去查一个叫「陈旧度」的字段是不是陈旧度。

> 通用原则：报告缺陷时把「今天的实际影响」和「机制上的错误」分开说。
> 把潜伏问题说成正在发生，下一次真的正在发生时就没人信了。
> —— 这条是上一轮的教训（我把一个不成立的结论当成立报了出去）。

#### `age_at(evaluated_at)` 为什么要强制传基准

已经有一个 `age_sec` 了，它锚在 `now_cn()` 上。问题是读**历史证据**时：
「它有多旧」只在「相对于哪一刻」下才有意义。拿今天去减一个月前的决策，
得到的是一个每天变大、与当时判断毫无关系的数。

`age_at` 要求调用方把基准说出来，于是就没法不小心用错基准。
`age_sec` 保留，但 docstring 里写死「任何要落库、上卡、或被回放比对的数字都不能用它」。

⚠️ `news_scan` 的 result 里也有个 `staleness_sec`，意思是「最新一条快讯距现在多久」
—— 量的是新闻源静不静，与取数滞后是两回事。**没动它**，而是在两处各加了注释
说明同名不同义。改名只动契约层。

### 4. 出处三件事：时刻记早了、来源写死了、指纹没人验

`freeze_index_daily` 里原来是这样：

```python
retrieved = now_cn()                    # ← 抓取「之前」
d = self._fetch(symbol, bars=bars)
source = f"sina:kline/{symbol}"         # ← 写死
```

**20.1**：`retrieved_at` 记的是「我打算去抓」的时刻，抓取耗时全被算进「数据有多新」。
一次 30 秒的慢响应，落库的时刻比真正拿到数据早 30 秒。
偏差是**单向的** —— 只会高估新鲜度，不会低估。

**20.2**：`self._fetch` 是**可注入的**（`fetcher or fetch_index_daily`），
而 source 写死了 `sina`。换任何 provider 进去，raw 层照样记「sina」。
这是一条**会说谎的出处**：落库的 source 描述的是调用方的假设，不是数据的来源。

> 通用原则：可注入的依赖 + 硬编码的标识 = 一条必然说谎的记录。
> 只要那个注入点存在，就得由被注入的那一方来声明自己是谁。

**20.3**：`content_sha256` 只在**写入时**算过一次，此后从没有人验过。

> 通用原则：一个从不被检验的指纹，和没有指纹的区别只在于它让人放心。

#### 这里差点写出一个 R-3 违规

第一反应是「85.4% 的历史行 `raw_text` 是 NULL，验不了，那就跳过」。
跳过就是静默 fail-open —— 正是 R-3（`UNKNOWN` ≠ `PASS`）要防的形状。

于是先去量，而不是先去设计降级方案。schema 注释里提到旧行「保持旧口径
（`payload_sha256`）」，那个函数还在。实测：

```
raw_market_snapshot 376 行
  raw_text 有值  55 行 → raw_text_sha256  一致 55 / 不一致 0
  raw_text NULL 321 行 → payload_sha256   一致 321 / 不一致 0
```

**全部可校验，只是分两种口径。** 于是 `_verify_snapshot_hash` 里
根本不需要「验不了」这一档，直接 fail-closed —— 两种口径各自都是真校验，
既不放行也不误杀。

> 通用原则：在为「验不了的那部分」设计降级之前，先确认它真的验不了。
> 我差一点用一个 R-3 违规去解决一个不存在的问题。

## 执行

```bash
# E-17
src/easyup_biga/domain/_freeze.py              # 新增：deep_freeze / thaw
domain/{evidence,facts,verdict}.py             # 接线：__post_init__ 冻、to_dict 解
domain/verdict.py::_same_value                 # 先 thaw 再 canonical dumps

# E-19
Evidence.staleness_sec → source_lag_sec        # 改名
Evidence.age_at(evaluated_at)                  # 新增，拒绝 naive / 非 datetime
AgentVerdict.max_staleness_sec → max_source_lag_sec + max_age_at()
risk_check.py                                  # 两个数各报各的，标签叫对名字
_EXPECTED_FIELDS  10 → 11

# E-20
providers/sina.py       PROVIDER_ID / ADAPTER_VERSION / IndexDaily.source
application/coordinator.py
    retrieved = now_cn()  移到 _fetch 之后
    source = d.source
    _verify_snapshot_hash(snap)  读回时校验
```

## 坑

### 坑 1 · 加一个 result 字段，契约当场拒了整个 FactBundle

```
ValueError: data_completeness 必须在 0..1，收到 1.1
```

`risk` 的 `data_completeness = len(result) / _EXPECTED_FIELDS`，而
`_EXPECTED_FIELDS` 是手工维护的常量（10）。加了 `max_evidence_age_sec` 之后
11/10 = 1.1，**契约直接拒绝构造**。

刺耳但**正确**：如果这里截断到 1.0，口径变了就被悄悄抹平了。
已在常量旁边记下这次实例。

### 坑 2 · sabotage 的输出被 `tail -3` 截掉，差点当成「只红了 3 条」

S1 第一次跑完只看到 3 行 FAILED，而实际红了 7 条——包括全部 5 条穿透测试。
`tail -3` 把最要紧的截掉了。

这是本项目第**四**次在 sabotage 上踩同型的坑（前三次见第 44、45 章）。
形状每次都不同（`[] or [...]` 仍非空 / `-k` 选中 0 条 / `for _ in ():` 没禁用检查
/ 这次是输出被截断），但后果一样：**用一个没看全的结果下结论**。

> 通用原则：sabotage 的产出必须包含**跑了多少条**和**红了哪几条**的完整清单，
> 不是「最后几行」。看不到分母的红，和看不到分母的绿一样不可信。

### 坑 3 · 往 raw 层插测试行，漏了 `created_at`

```
sqlite3.IntegrityError: NOT NULL constraint failed: raw_market_snapshot.created_at
```

raw 层只追加、触发器挡 UPDATE，所以「构造一行指纹不对的数据」只能走裸 INSERT，
而裸 INSERT 就得自己补齐所有 NOT NULL 列。红得很清楚，一次就过。

### 坑 4 · 我自己的往返测试用错了类

`FactBundle.to_dict()` 没有 `stance` 键，`AgentVerdict.to_dict()` 有。
拿 FactBundle 构造、用 AgentVerdict 往返，断言差一个 `{'stance': None}`。
**是测试写错了，不是代码错了** —— 分清这两者比修好它重要。

## 验证

```bash
cd ~/.openclaw-biga/workspace

# 三批探针
python3 -m pytest tests/test_deep_freeze.py \
                  tests/test_evidence_age.py \
                  tests/test_snapshot_provenance.py -q
# 期望：37 passed

# E-17 穿透复现（改动前应当打印被污染的值）
python3 - <<'PY'
import sys; sys.path.insert(0,'skills')
from _contract import Evidence, now_cn
from datetime import timedelta
t = now_cn() - timedelta(seconds=60)
payload = {"items": [1, 2, 3]}
e = Evidence(field="f", source="probe:x", value=payload, as_of=t,
             retrieved_at=t, calc_version="v1", raw_hash="a"*64)
payload["items"].append(999); payload["injected"] = True
assert e.value["items"] == (1, 2, 3) and "injected" not in e.value
print("✅ 外部改动没有穿透进已构造的 Evidence")
PY

# E-20.3 两种口径都真的在校验（只读生产库，不写）
python3 - <<'PY'
import sys, sqlite3, json; sys.path.insert(0,'skills'); sys.path.insert(0,'src')
from _store.db import payload_sha256, raw_text_sha256
c = sqlite3.connect('file:data/biga.db?mode=ro', uri=True)
n = b = 0
for sha, pj, rt in c.execute("SELECT content_sha256,payload_json,raw_text FROM raw_market_snapshot"):
    ok = raw_text_sha256(rt) == sha if rt is not None else payload_sha256(json.loads(pj)) == sha
    n += 1; b += (not ok)
print(f"✅ raw 层 {n} 行，指纹对不上的 {b} 行")
PY
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | `frozen=True` + `MappingProxyType` 只盖第一层；嵌套的 list/dict 仍是外部那个对象 |
| 2 | 要害是**时序**：校验在 `__post_init__`，穿透在那之后 ⇒ 落库的不是被校验的那份 |
| 3 | 一道防护写了正确的理由，不等于它覆盖了那个理由涵盖的范围 —— 读注释会以为查过了 |
| 4 | `result[k]` 与 `evidence.value` 常是同一个对象 ⇒ 一次 mutate 同时满足交叉校验 |
| 5 | 🔴 本批差点把批 P 的门推开：冻结后 `json.dumps` 抛错 → 退回 `==` → `1==1.0` 又成立，**全程不报错** |
| 6 | 改了被广泛消费的数据形状后，回头把**消费它的守卫**各跑一遍，别只跑自己这批 |
| 7 | 序列化边界 `thaw` 把 tuple 还原成 list ⇒ 卡片与落库**逐字节不变**，这是能安全落地的前提 |
| 8 | docstring 拦不住的，名字拦得住 —— 名字是唯一每次使用都会被读到的文档 |
| 9 | 实测：错名字今天最多只差 87 秒，且不闸任何东西。**潜伏 ≠ 正在发生**，别说过头 |
| 10 | 问「有多旧」必须说清相对哪一刻 ⇒ `age_at(evaluated_at)` 强制传参，`age_sec` 禁止落库 |
| 11 | 同名不同义（news 的 `staleness_sec`）就地标注，别顺手「统一」 |
| 12 | 可注入的依赖 + 硬编码的标识 = 一条必然说谎的记录 |
| 13 | 一个从不被检验的指纹，和没有指纹的区别只在于它让人放心 |
| 14 | 🔴 为「验不了的那部分」设计降级**之前**，先确认它真的验不了 —— 实测 376 行全部可校验，降级方案根本不需要 |
| 15 | 契约拒绝 `data_completeness=1.1` 是**正确行为**；截断到 1.0 会把口径变更悄悄抹平 |
| 16 | 🔴 sabotage 的产出必须含**跑了多少条**与**红了哪几条**；`tail -3` 截掉的正是分母（同型第四次） |
