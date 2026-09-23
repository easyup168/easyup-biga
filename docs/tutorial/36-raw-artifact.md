# 第 36 章 · 让 raw 层真的存 raw

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 I —— raw 层此前存的不是 raw（`json.loads` 后又
> `json.dumps(sort_keys=True)` 落盘，`content_sha256` 是我们自己重排后的指纹）。
> 这一章给 `raw_market_snapshot` 加一列 `raw_text` 存**原始响应文本**，把
> `content_sha256` 改成基于它算（schema **v13**）；重点在两个判断：**为什么必须
> 新增字段而不是替换旧列**、**多次请求聚合的源，「原始文本」到底是什么**。
> **不覆盖**：`get_text` 的编码假设是否正确（字节级原始性，本批明确排除）；旧行的
> 回填（raw 层只追加，旧行语义就是旧语义）；Parquet / Provider 归档数据面（方向已
> 定但没有消费方之前不建）。

---

## 目标 / 产出

系统的卖点是「证据可追溯、可回放」。可追溯链的最底层是 `Evidence.raw_hash` →
`raw_market_snapshot.content_sha256` → 那份原始响应。做这一章之前，这条链的**根**
是假的：

```text
get_text()   HTTP body → str
      ↓
get_json()   str → Python 对象（json.loads），原始文本就地丢弃  ← 第一次损失
      ↓
各 collector 把解析后的对象揣进 c.raw
      ↓
save_raw_snapshot()   json.dumps(payload, sort_keys=True) 落盘   ← 原始设计只点名了这一步
                      content_sha256 = 对重排后对象取 sha
```

`content_sha256` 算的是**我们自己 `sort_keys` 重排后**的字节。它证明不了数据源发来
的是什么 —— 键序、空白、浮点表示、原始编码全丢了。更糟的是 `schema.py` 的建表注释
当时写着「这里存的是从数据源拿到的字节，**不做任何归一化**」——`sort_keys` 就是归一
化，那句话是假的（L-3 的标准形状：注释断言了一件没发生的事）。

做完这一章：

- `_sources/http.py` 加 `get_json_and_text()`，**同时**交出解析结果与原始文本；
  `get_json()` 收敛成它的薄封装。
- 四个适配器把原文一路带出来（`IndexDaily` / `PoolResult` / `BreadthResult` /
  `BoardResult` / `NewsFeed` 各加 `raw_text`；`fetch_index_quote` 改返回
  `(quotes, body)`）。
- schema **v13**：`raw_market_snapshot` 加 `raw_text` 列；`db.raw_text_sha256()` 是
  `content_sha256` 的新口径。
- 建表注释改回真话；`Evidence.raw_hash` 的文档说明口径在 v13 变了。
- `tests/test_raw_artifact.py`：七道探针（P1–P7）。

一句话：**让「原始响应文本」这段字节真正被保留下来，且不改变现有消费方已经在依赖
的「payload 是解析后对象」这个语义。**

---

## 为什么这么做

### 一、新增字段，不是替换 `payload_json` —— 一个会被静默破坏的消费方

最容易想到的做法是「把 `payload_json` 存的东西从『重排后的对象』换成『原始文本』」。
开工前把这条链从头跟了一遍，普查到**一个会被这么改静默破坏的消费方**：

```python
# _snapshot/coordinator.py::read_index_daily
snap = load_raw_snapshot(...)
raw = snap["payload"]          # 当前期望：一个可切片的 list（K 线数组）
if bars > len(raw): ...        # len() 数的是 K 线根数
return parse_index_daily(symbol, raw[-bars:])   # 切片
```

它把 `payload` 当**已解析对象**在用。如果 `payload_json` 改存原始文本，这里会拿到一个
`str`：`len(raw)` 数的是**字符数**不是 K 线根数，切片切的是字符不是 K 线，**而且不会
报错**。这正是本仓库最想防的那种「看起来正常、其实错了」的静默破坏 —— 卡照常出，只是
所有「够不够根数」的判断全错了。

⇒ 这一批必须是**新增字段**：`payload_json` / `load_raw_snapshot()` 返回的 `payload`
继续是解析后的对象（coordinator 一行都不用改），原始文本另存一个新列 `raw_text`，
`content_sha256` 改成基于 `raw_text` 算。

> 通用原则：**改一个存储列的语义之前，先普查它的读取方。** 一个字段被当作「解析后
> 对象」在用（`len` / 切片 / `.get`），你把它换成字符串，多数读取方不会崩 —— 会
> **安静地算错**。新增一列、让旧读法继续成立，比原地改语义安全一个量级。这与 E-I 的
> `LegacyAdapter`、K 的卡级冻结名单回退是同一个模式，不发明第四种写法。

这条边界不是我现场拍的 —— 是设计探活写在分发提示词里、要求「做之前完整读一遍、不要
重新排查」的。**普查一次、把结论写进提示词**，比每个建造会话各查一遍靠谱：查漏了不
报错，正是它的危险之处。

### 二、损失点比「`sort_keys` 那一步」更早

原始设计描述只点名了最后一步（`json.dumps(sort_keys=True)` 落盘）。但真正第一次损失
发生在 `get_json()` **内部**：原始响应文本被 `json.loads` 解析成对象后，那段文本没有
被保留到任何地方，六个 collector 攒进 `c.raw` 的**从一开始就是解析后的对象**。

⇒ 只改 `save_raw_snapshot` 的序列化方式不够 —— `c.raw` 这条累积路径本身就已经丢了
原文，得往回追到 `get_json()` 才能补上。所以加了 `get_json_and_text()`：

```python
def get_json_and_text(url, *, referer) -> tuple[Any, str]:
    body = get_text(url, referer=referer)
    return json.loads(body), body          # 解析结果与原文成对交出
```

为什么是**姊妹函数**而不是让 `get_json` 返回二元组：一个名叫 `get_json` 的函数返回
`(对象, str)` 会让每个读代码的人愣一下（「它不是该返回 JSON 吗」）。留 `get_json` 做
「只要解析结果」的薄封装、新函数名字直说返回什么，是最不容易误读的形状。代价是
**测试桩要跟着改**（见「坑」一节）—— 但那笔代价换任何一种形状都躲不掉。

### 三、多次请求聚合的源：「原始文本」到底是什么

不是每个源都「一次请求 = 一份响应体」。三种形状：

| 源 | 请求次数 | `payload`（落 `payload_json`） | `raw_text` |
|---|---|---|---|
| sina 日线 / 东财股池·涨跌家数 | 1 | `json.loads(body)` | **就是 body，逐字节** |
| 腾讯行情 | 1（一次拿回所有代码）| `{code: 片段}`，从 body 抠出再组装的**派生物** | **整段 body**（非 JSON，是 `v_code="…";` 文本）|
| 东财板块榜 / 新浪快讯 | **多页** | `{"pages": [各页对象]}` | **各页 body 的 JSON 数组** |

两个要点：

1. **腾讯的 `payload` 不等于 `json.loads(raw_text)`。** raw 层里那份 `{code: 片段}`
   是我们从 body 里用正则抠出各段再重新组装的 —— 它是派生视图，不是源字节。要让
   `content_sha256` 证明源字节，只能哈希整段 `body`。所以 `fetch_index_quote` 改成
   返回 `(quotes, body)`：body 只此一份，不塞进每个 `IndexQuote`（那样每条都存一份
   整体的冗余）。

2. **多页源的 `raw_text` 是「各页 body 的 JSON 数组」，不是单一响应体。** 板块榜和快讯
   本来就是 N 次独立请求聚合成一份快照，不存在「一个原始响应体」。诚实的表示是把 N 段
   body 各自逐字节保留进一个数组：`json.dumps([body_1, …, body_n])`，`json.loads` 回来
   每个元素逐字节等于对应那次的响应体。这一层的 `json.dumps` 只是把多页装进数组、不碰
   各页内部的字节 —— 与「把整个 payload `sort_keys` 重排」是两回事。

> 通用原则：**「原始」这个词要落到一个具体的字节序列上。** 当一份快照由多次请求
> 聚合、或由响应派生而来，先问「哪一段才是数据源真正发过的字节」，再决定 `raw_text`
> 存什么。含糊地存「差不多的东西」，等于给指纹注水。

因此建表注释也得写清楚**分列**说：`payload_json` 是解析后的规范表示（归一化过、给回读
用），`raw_text` 才是未归一化的原文、`content_sha256` 基于它算。一句笼统的「不做任何
归一化」放在整张表上，对 `payload_json` 就是假话 —— 改成分列陈述才为真。

### 四、为什么旧行不迁移、`payload_sha256` 为什么留着

raw 层只追加（L-8）。v13 之前落的行**没有原文可填** —— 那段文本在 `get_json` 内部早被
丢弃、重建不出来，硬回填只能编。所以：

- `raw_text` 列可空，旧行留 `NULL`，其 `content_sha256` 保持**旧口径**
  （`payload_sha256`，对重排后对象取 sha）。
- 新行由 `save_raw_snapshot` 强制 `raw_text` 非空、`content_sha256` 走新口径
  （`raw_text_sha256`）。

**新行语义变了、旧行不受影响** —— 这是 schema 演进的标准形状。`payload_sha256` 因此
**保留未删**（它是旧行的口径，且 `tests/fixtures/payload-sha256-vectors.json` 钉死它的
输出格式）。`Evidence.raw_hash` 的文档补了一句：别拿跨 v13 的两个 `content_sha256` 直接
比 —— 它们来自不同口径，都「看起来正常」，却对不上。

`ALTER TABLE ... ADD COLUMN` 本身也要留意：它**不触发行 UPDATE**，也不动
`raw_market_snapshot` 已有的只追加触发器 —— 加完这一列，UPDATE/DELETE 仍然被拒（探针
P5 专门验这条）。

---

## 执行

### 每道守卫先弄坏、见过红、再还原（G-1）

`tests/test_raw_artifact.py` 的探针，逐个把被守的东西真的弄坏、确认报红、再还原：

```text
P2 哈希基于原文：把 content_sha256 改回 payload_sha256(payload)
  → 两次「数据相同、键序不同」的响应产出同一个 sha：
    assert 'd8497d…' != 'd8497d…'   ← AssertionError（改前正是判成「一样」的）

P1/P6 原始性：把 INSERT 改成往 raw_text 列写重排后的 blob
  → assert stored == _BODY 翻红，diff 直接摆出 bug：
    + [{"close": "10.50", "day": …}]        （sort_keys、无空白）
    - [\n  {"volume": "1000", "close": …}]   （原始的乱序 key + 多余空白）

P3 消费方不被破坏：去掉 load 里的 json.loads，让 payload 变 str
  → 探针 isinstance(payload, list) 翻红，**且真实消费方**
    test_snapshot / test_snapshot_wiring 的冻结读取（len/切片）一并翻红

P4 漏传即报错：删掉 raw_text 非空校验
  → raw_text="" 时 DID NOT RAISE ValueError

P5 加列后仍只追加：从 _V1 拿掉 raw_market_snapshot 的只追加触发器
  → UPDATE … SET raw_text=… : DID NOT RAISE AppendOnlyViolation

P7 多页源按页保真：把 fetch_feed 的 raw_text 改成 json.dumps(解析后的页)
  → json.loads(raw_text) 取回的是 {'result': {...}} 解析对象，不是原始 body 字符串
```

P3 那条尤其值 —— 它红的时候，连 coordinator 那条**真实**冻结读取路径也一起红了。证明
这道探针守的不是我自己写的断言，是生产代码那条路径。P7 是设计探活规定的六道之外我自己
补的：P1/P6 只覆盖**单次请求**的源，而多页聚合的源（快讯 / 板块榜）恰恰是量最大的，
它们的 `raw_text` 是各页 body 的 JSON 数组 —— 得单独有一道证明每页逐字节进了数组。

### 全套回归

```bash
python3 -m pytest -q          # 全绿；新增 10 条探针（test_raw_artifact.py，P1–P7）
```

（全量条数 1170 → 1184：+10 探针，再加本章带来的 4 条 `test_docs_convention` 参数化
用例；`tools/verify/sync_test_count.sh` 把这个数同步进 README / CLAUDE.md /
review-prompt.md。）

---

## 坑

### 坑一：一道 NaN 守卫「搭便车」搭在了要拆的函数上

`save_raw_snapshot` 原来是这样：

```python
blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)   # 没有 allow_nan=False
sha  = payload_sha256(payload)   # payload_sha256 内部带 allow_nan=False
```

`payload` 里若混进 `NaN`/`Infinity`（RFC 8259 不认），是靠 `payload_sha256` 那次
`json.dumps(allow_nan=False)` 在**写库前**抛错拦下的 —— `blob` 那行反而没带这个参数。

这一批把 `sha` 改成 `raw_text_sha256(raw_text)`（哈希文本，不再碰 `payload`）。于是那道
NaN 守卫**跟着没了** —— `blob` 会静默写进一个 `NaN`。`tests/test_write_boundary.py` 的
`test_save_raw_snapshot拒绝非法浮点` 当场翻红，把这事抓了出来。修复：把 `allow_nan=False`
补回 `blob` 那一行，让它自己带上这道检查。

> 通用原则：**拆掉一个函数调用前，先查它有没有「顺带」做别的事。** 一个纯粹为算哈希
> 而调的函数，可能正扛着一道校验的副作用 —— 拆了它，校验就无声地消失了。

### 坑二：测试桩打在 `get_json` 上，改用姊妹函数后桩就落空了

不少测试用 `monkeypatch.setattr(mod, "get_json", …)` 打桩网络边界、注入假 payload。适配器
改调 `get_json_and_text` 之后，这些桩**打在了没人再调的函数上** —— 桩看起来还在，却不再
拦截网络。改法是把桩retarget 到 `get_json_and_text`、返回 `(payload, text)`：

```python
# 改前
monkeypatch.setattr(em, "get_json", self._fake(350))
# 改后
monkeypatch.setattr(em, "get_json_and_text", self._fake(350))   # _fake 返回 (payload, json.dumps(payload))
```

同理，`fetch_index_quote` 改返回 `(quotes, body)` 后，所有返回裸 dict 的假桩
（`lambda codes: {…}`）都得改成返回二元组，否则 `q, body = fetch_index_quote(...)` 解包
失败。这类桩散在十来个测试文件里，靠「跑一遍全套、顺着红点逐个改」收敛，比事先靠
grep 猜全更可靠。

### 坑三：假数据对象漏了 `raw_text` → `None.encode()`

`save_raw_snapshot(raw_text=...)` 必填非空，`_keep_raw` 里 `raw_text_sha256(raw_text)`
也要一段真字符串。测试里手搓的假 `IndexDaily(...)` / `BoardResult(...)` 等如果不给
`raw_text`（默认 `None`），跑到哈希那步就 `None.encode()` → `AttributeError`。把这些假
对象的构造统一补上 `raw_text=json.dumps(<payload>)`（一段能解析回 payload 的合理原文）
即可。

---

## 验证

```bash
# 1) 七道探针全绿
python3 -m pytest tests/test_raw_artifact.py -q
# 预期：10 passed

# 2) content_sha256 真的基于原文（手算对得上），且能分辨不同序列化
python3 - <<'PY'
import sys, hashlib, json; sys.path.insert(0, "skills")
from _store import init_schema, save_raw_snapshot, load_raw_snapshot, payload_sha256
import tempfile, pathlib
db = pathlib.Path(tempfile.mkdtemp()) / "t.db"; init_schema(db)
a, b = '{"x": 1, "y": 2}', '{"y": 2, "x": 1}'      # 同数据、键序不同
sa = save_raw_snapshot(source="t:a", as_of="a", retrieved_at="b",
                       payload=json.loads(a), raw_text=a, path=db)
sb = save_raw_snapshot(source="t:b", as_of="a", retrieved_at="b",
                       payload=json.loads(b), raw_text=b, path=db)
ra, rb = load_raw_snapshot(sa, path=db), load_raw_snapshot(sb, path=db)
assert ra["content_sha256"] == hashlib.sha256(a.encode()).hexdigest()   # 基于原文
assert ra["content_sha256"] != rb["content_sha256"]                     # 能分辨序列化
assert payload_sha256(json.loads(a)) == payload_sha256(json.loads(b))   # 旧口径会判成一样
assert isinstance(ra["payload"], dict)                                  # payload 仍是解析后对象
print("OK: content_sha256 基于原文、能分辨序列化、payload 仍是对象")
PY

# 3) 加列后 raw 层仍只追加
python3 -m pytest tests/test_raw_artifact.py::TestP5StillAppendOnly -q
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | raw 层此前存的不是 raw：`content_sha256` 是我们 `sort_keys` 重排后的指纹，证明不了源字节 |
| 2 | 改存储列语义前先普查读取方 —— 被当「解析后对象」用的列换成字符串，多数读取方不崩、会**安静算错** |
| 3 | 所以是**新增列**（`raw_text`）不是替换 `payload_json`；`load_raw_snapshot().payload` 继续是解析后对象 |
| 4 | 损失点在 `get_json` 内部（原文 `json.loads` 后就丢了），故加姊妹函数 `get_json_and_text` 把原文一路带到落库点 |
| 5 | 「原始」要落到具体字节：腾讯存整段 body（payload 是派生物）；板块榜/快讯存各页 body 的 JSON 数组 |
| 6 | 建表注释改成**分列**陈述真话：`payload_json` 归一化过、`raw_text` 才是原文 —— 笼统的「不做归一化」对 `payload_json` 是假的 |
| 7 | 旧行不回填（原文重建不出来），旧口径 `payload_sha256` 保留；别拿跨 v13 的两个 `content_sha256` 直接比 |
| 8 | 拆一个函数调用前查它有没有「搭便车」的副作用 —— NaN 守卫原来搭在 `payload_sha256` 上，拆了要补回 `allow_nan=False` |
| 9 | 改网络边界函数名/签名，测试桩会静默落空（打在没人调的函数上）—— 顺着红点逐个 retarget，别靠 grep 猜全 |
