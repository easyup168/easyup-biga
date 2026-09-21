# 第 12 章 · 让结论可以被追问：stance、raw_hash 与机器可读的缺失

> 这一章来自一次**外部设计评审**。评审文档提了 40 多条，其中三条指出了
> 我们确实有、而且自己没看见的空白。另有四条会把刚修好的东西推回去。
> 本章记录怎么分辨这两类。

---

## 目标 / 产出

- `AgentVerdict.stance` —— 方向判断成为结构化字段
- `Evidence.raw_hash` —— 每条证据指回具体哪一份原始响应
- `MissingItem` —— 缺失项 = 机器可读代码 + 人话
- 一条把设计文档与代码钉在一起的测试

---

## 为什么这么做

### 0. 先说怎么读一份评审

评审文档提的每一条，只能落进三个筐：

| 筐 | 怎么判断 | 处理 |
|---|---|---|
| **确认** | 我们已经在做 | 不动，但值得知道别人也是这么想的 |
| **空白** | 我们没做，且**说不出不做的理由** | 采纳 |
| **冲突** | 我们没做，但**有实测支撑的理由** | 不采纳，把理由写下来 |

第三筐最危险 —— 因为「文档说了」很容易盖过「我们量过」。
这次有四条落在第三筐，其中一条是「**Offline First, Live Last**」：
先写 fixture、最后接真实数据源。

听起来很专业，但对我们**恰好是反的**：整个 market 的数据源选型，
来自先探测真接口时发现的 `涨跌幅=-29971017728.0` 和「`lmt=25` 被忽略」。
先写 fixture 等于**把一个坏掉的 API 形状固化进测试**。

> **通用原则：测试要离线，设计要先探活。**
> 把这两件事都叫「Offline First」，就会用测试的纪律去约束设计的探索。

### 1. `stance` —— 方向判断没落库，等于每跑一次丢一次

我们的 `verdict` 只有 `PASS / WARNING / UNKNOWN`，它回答的是**数据全不全**。
而「市场偏哪边」这个判断，此前**只存在于 Agent 的自然语言回复里**。

后果要到很久以后才显形：

> Phase 4 要检验「BigA 说强的时候，后面几天到底怎么样」。
> 那时翻历史记录会发现 —— **那一列根本不存在。**

三个字段必须分开：

| 字段 | 回答 | 谁填 |
|---|---|---|
| `status` | 这次执行跑完了吗 | skill |
| `verdict` | 判断**有效吗**（数据全不全） | skill |
| `stance` | 判断**是什么**（市场偏哪边） | **Agent** |

🔴 `verdict=PASS` 是「数据完整」，**不是「看好」**。
混在一起，「没发现问题」和「看多」就再也分不开 —— 那正是 L-2 的形状。

#### 为什么用固定词表而不是自由文本

`stance` 存在的唯一理由是**能被聚合**。今天写「偏强」、明天写「震荡偏强」、
后天写「结构性走强」，三个月后它就是一列自由文本，做不了任何相关性检验。

所以词表写死在契约层：

```python
STANCE_VOCAB = {
    "market":  ("放量上涨", "缩量上涨", "缩量调整", "放量下跌", "分化", "无法判定"),
    "emotion": ("冰点", "修复", "亢奋", "衰退", "恐慌", "无法判定"),
}
```

并加一条**耦合校验**：`verdict='UNKNOWN'` 时不许有方向。
数据都不够，方向是从哪来的？

### 2. `raw_hash` —— 「基于哪份数据」不该靠时间戳猜

`Evidence` 本来有 `source` / `as_of` / `retrieved_at`，听起来够了。
但它回答不了这个问题：

> 这条证据，出自**具体哪一次**采集？

`raw_market_snapshot` 里本来就存了 `content_sha256`，
只是 `Evidence` 没有引用它 —— 差的就是一个字段。

⚠️ 派生字段（`source` 以 `derived:` 开头）**允许为空**，不硬凑一个哈希。

> **通用原则：凑出来的溯源比没有溯源更糟。**
> 空值告诉你「查不到」，假值让你以为「查得到」。

### 3. 缺失项要能被聚合

原来的 `missing[]` 是纯中文散文。上卡很好读，但：

- Phase 2 的出口条件是「`missing[]` 真实非空 ≥5 次」——
  散文只能数**次数**，说不出是不是同一个源挂了五遍
- 可达性巡检（L-7）要知道哪条缺失项**从未出现过** —— 那说明它是死分支

#### 🔴 改类型会踩的坑，以及怎么绕开

直觉做法是把 `missing: list[str]` 换成 `list[MissingItem]` dataclass。
但仓库里到处是：

```python
f"{m}"                      # 渲染上卡
"某某" in m                  # 测试里的断言
sorted(upstream - set(...)) # Card 的上浮校验
```

换成普通 dataclass，这些地方会**静默改变行为** —— `f"{m}"` 会往卡上打出
`MissingItem(code=...)` 这种东西。

解法是让它做 **`str` 的子类**：字符串值就是给人看的那句话，代码挂在 `.code` 上。

```python
class MissingItem(str):
    __slots__ = ("code",)
```

改完之后 **193 条既有测试一条没动**。

#### 旧数据怎么办

Phase 1/2 早期落库的卡里 `missing` 是裸字符串。回放必须还能读它们 ——
`coerce()` 把裸字符串收成 `code="legacy.unclassified"`。

**不给它们编一个像样的代码**：那等于伪造分类。`legacy.unclassified`
一眼就能看出是历史遗留。

---

## 执行

```text
skills/_contract/missing.py          MissingItem（str 子类）
skills/_contract/evidence.py         + raw_hash
skills/_contract/verdict.py          + stance + STANCE_VOCAB
skills/_store/db.py                  + payload_sha256（与 raw 层同一个哈希函数）
skills/decision-card/scripts/
    amend_verdict.py                 + --stance、--add-missing CODE TEXT
    synthesize.py                    + --extra-missing CODE TEXT、缺 stance 拒绝出卡
```

一次完整链路：

```bash
M=$(python3 skills/market-calc/scripts/market_calc.py 2>&1 >/dev/null | grep -o '[0-9]*$')
python3 skills/decision-card/scripts/amend_verdict.py --ref $M --stance 缩量上涨
python3 skills/decision-card/scripts/synthesize.py --verdict-ids <refs> \
  --extra-missing supervisor.agent_offline "risk agent 尚未上线" ...
```

卡面：

```text
market       PASS     缩量上涨   advance_count=4277; ...
emotion      WARNING  修复      broken_board_count=25; ...

⚠ 缺失项（2）
  · 情绪周期趋势 —— 只有单日快照，无法区分衰退与修复
      [emotion.cycle.no_history]
  · risk agent 尚未上线，本卡未经风险审查
      [supervisor.agent_offline]
```

`verdict` 与 `stance` 并列显示 —— 只显示前者，读者会把 `PASS` 误读成「看好」。

---

## 坑

### 坑 1 · 新校验会把历史数据挡在门外

「每个 Specialist 都必须有 stance」这条如果写进契约层的 `__post_init__`，
它会在 `from_dict` 时**同样生效** —— 于是 Phase 1/2 落的四张卡再也回放不了。

所以这条校验放在**在线合成路径**（`synthesize.py`），不放在契约层。

> **通用原则：新规矩对新数据严格，对旧数据只要求可读。**
> 「能重建当时看到的东西」优先于「形式上处处一致」。

同理，`MissingItem.coerce()` 接受裸字符串 —— 否则回放直接崩。

### 坑 2 · 守卫抓到了写守卫的人

刚加完测试，`test_contract_single_impl`（AST 扫描「手搓字典版契约」）就报红 ——
命中的是我自己新写的测试辅助函数：

```python
base = dict(task_id=..., agent=..., status=..., verdict=...)
```

它拼的是构造 `AgentVerdict` 的 kwargs，不是第二套契约。
扫描器留了豁免口，**但豁免注释必须写理由**：

```python
# contract-exempt: 不是第二套契约，是同一个契约的入参
```

> 这次误报是好事：说明扫描器真的在扫，而不是一个长期全绿的摆设。

### 坑 3 · 文档和代码会各自漂

`STANCE_VOCAB` 在契约层，而 Agent 看的是自己 `AGENTS.md` 里的判断表 ——
**同一套口径两个地方写**。改了一边忘了另一边，Agent 会给出一个契约层拒绝的词，
然后花几轮去猜为什么被拒。

所以加了一条测试，把两边逐项对上：

```python
listed = {w.strip() for w in line.split("：", 1)[1].split("/")}
assert listed == set(STANCE_VOCAB[agent])
```

> **通用原则：凡是「同一个事实写在两处」，就加一条测试把它们钉在一起。**
> 靠人记得同步，等于没有同步。

---

## 坑 4 · 🔴 加完 stance，Stage 1 慢了一倍 —— 而原因和直觉相反

真 agent 验证通过，但延迟从 71.0s 涨到 133.5s。
第一反应是「让 Agent 选词，它得多想一会儿」，打算把契约里的解释压缩掉。

**先别改，先看轨迹。** 拆开 Specialist 那一轮：

```
23:47:30  跑 skill                                    ✅
23:47:58  amend_verdict.py --help
23:48:04  grep -rn "stance" ...
23:48:08  grep -n "vocab" ...
23:48:12  grep -n "STANCE_VOCAB" ...
23:48:15  grep ... skills/decision-card/scripts/_contract.py   ← 路径是猜的
23:48:18  find . -iname "_contract.py"
23:48:21  find / -iname "_contract.py"                ← 🔴 契约明令禁止的那条
23:48:49  python3 -c "from _contract import STANCE_VOCAB"
23:49:00  终于调对 amend_verdict
```

**62 秒、12 次工具调用，全花在找词表在哪。** 不是在想「今天算不算缩量」。

两个根因：

1. 我写的是 `--stance <下表里的一个词>`，而**那张表不存在** ——
   契约里只有输出格式那一行列了选项。Agent 没把两者联系起来，于是去搜
2. 🔴 **运行时把那些命令的输出吞掉了**：`exitCode=0`，
   但 Agent 看到的是 `[Malformed diagnostic JSON redacted]`。
   **它连 `--help` 都读不到** —— 所以越搜越远

第 2 条尤其值得记住：**契约必须自包含，任何「需要去查一下」的指引都是无效的。**
这正是 Phase 1 第 07 章学到的「照抄这段，不要去读源码」，这次我自己没做到。

修完之后：

| 运行 | 工具调用 | Stage 1 | 端到端 |
|---|---|---|---|
| 加 stance 前 | — | 30.0s | 71.0s ✅ |
| 加 stance 后 | **13 / 9** | 103.9s | 133.5s ❌ |
| 词表内联后 | **2 / 2** | 45.6s | **70.6s** ✅ |

> **通用原则：Agent 的「慢」，多数时候是它在找东西，不是在想事情。**
> 看轨迹里的工具调用序列，比猜「提示词是不是写得不好」有用得多。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest -q                       # 215 条

# 词表与契约文档一致
python3 -m pytest tests/test_stance_and_traceability.py -q -k Vocab

# 不提交 stance 就出不了卡
python3 -m pytest tests/test_verdict_refs.py -q -k stance

# 旧卡仍能回放（missing 是裸字符串的那几张）
python3 skills/decision-card/scripts/replay.py BIGA-20260920-002 --check
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 读评审要分三筐：确认 / 空白 / **有实测支撑的冲突** —— 第三筐最容易被「文档说了」压过去 |
| 2 | **测试要离线，设计要先探活** —— 先写 fixture 会把坏掉的 API 形状固化进测试 |
| 3 | `verdict` 是「数据全不全」，`stance` 是「偏哪边」；混在一起，「没发现问题」与「看多」就分不开 |
| 4 | 判断只写在自然语言里 = 每跑一次丢一次，等要做统计时那一列根本不存在 |
| 5 | 受控词表不是官僚主义 —— 自由文本的判断没法聚合，等于没记 |
| 6 | 凑出来的溯源比没有溯源更糟：空值说「查不到」，假值让你以为「查得到」 |
| 7 | 改类型时用 `str` 子类保住兼容：193 条既有测试一条没动 |
| 8 | 新规矩对新数据严格，对旧数据只要求**可读** —— 回放能力优先于形式一致 |
| 9 | 守卫误报到自己身上是好事：说明它真的在扫 |
| 10 | 同一个事实写在两处，就加一条测试把它们钉在一起 |
| 11 | Agent 的「慢」多数是它在**找东西**，不是在想事情 —— 先看工具调用序列 |
| 12 | 契约必须**自包含**：运行时可能吞掉命令输出，`--help` 都未必读得到 |
| 13 | 写「见下表」之前先确认那张表真的在 —— 悬空的指引会让 Agent 去 `find /` |
