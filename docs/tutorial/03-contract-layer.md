# 第 03 章 · 契约层：让非法状态无法被表示

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：写出 `Evidence` / `AgentVerdict` / `DecisionCard`，
> 并用 AST 全仓扫描钉死「它们只有一份实现」。
>
> **本章产出**：`skills/_contract/`（3 个模块）+ `tests/` 两个测试文件，共 51 条测试。

---

## 1. 为什么第一行业务代码是契约，不是数据采集

直觉上应该先写采集 —— 有数据才有得分析。但多 Agent 系统里，**第一个该固定下来的是
Agent 之间说话的格式**。

原因很具体：Agent 之间传的不是函数调用，是**消息**。消息没有类型检查，
一个 Agent 多写了个字段、另一个 Agent 少读了个字段，谁都不会报错。
等系统跑起来再统一格式，你要改的不是代码，是**已经落库的历史数据**。

更重要的是：契约是**那些「绝不能妥协」的规则的物理载体**。
本项目有三条设计地基，其中最硬的一条是：

> **`UNKNOWN` ≠ `PASS`** —— 算不出来必须说算不出来。

这条规则如果只写在文档里，它就会在某个赶时间的下午被绕过。
写进契约的构造函数里，它就绕不过去。

---

## 2. 核心手法：让非法状态无法被表示

这是本章唯一真正重要的思想，其余都是它的展开。

考虑一个具体场景：情绪 Agent 要算「最高连板数」，但数据源超时了。它该返回什么？

```python
# 写法一：最自然，也最危险
return {"verdict": "PASS", "result": {"limit_up": 42}}     # 连板数就……不提了
```

这段代码**没有任何错误**。它会通过 code review、通过单元测试、在生产里跑几个月。
而它实际表达的是：「我没算出连板数，所以我当它不存在，并且我说一切正常。」

这就是本项目最优先防范的失败模式，它有一个名字：**静默 fail-open**。
它的致命之处在于 —— **放行与通过的日志长得一模一样**。

三种可能的对策：

| 对策 | 问题 |
|---|---|
| 写进文档，靠人记住 | 三个月后没人记得。新来的人根本不知道有这条 |
| 运行时打 warning | 日志里多一行，没人看。而且它仍然返回了 PASS |
| **构造时直接拒绝** | ✅ 这个状态**根本无法被创建** |

所以契约层的每个 `__post_init__` 都在做同一件事：**抛异常，不是记日志**。

```python
if self.missing:
    if self.verdict == "PASS":
        raise ValueError(
            f"[{self.agent}] missing={self.missing} 非空却给出 verdict='PASS' —— "
            "UNKNOWN ≠ PASS，算不出来必须说算不出来（铁律 1）"
        )
```

> **通用原则**：当一条规则「违反时不会自然报错」，就必须让违反它的状态**在类型层面
> 构造不出来**。文档和日志都拦不住它，因为它们不参与执行。

顺带一提，报错信息里带上铁律编号和具体数值，是给三个月后的自己看的 ——
那时你只会看到一行 traceback。

---

## 3. 三个结构

### `Evidence` —— 一条可追溯的事实

```python
@dataclass(frozen=True)
class Evidence:
    field: str              # 它支撑 result 里的哪个键
    source: str             # 'biga.db:raw_market_snapshot' / 'em:api/clist'
    value: Any
    as_of: datetime         # 🔴 数据本身的时间
    retrieved_at: datetime  # 取回这份数据的时间
    calc_version: str | None = None
    label: str | None = None
```

三个细节值得说。

**① `as_of` vs `retrieved_at` 必须分开。**
一个是「这份数据描述的是哪一刻的市场」，一个是「我什么时候拿到它的」。
混为一谈，盘中数据和收盘数据就会被当成同一时刻的事实汇总 —— 而这是上游需求文档
点名要防的问题。分开之后可以免费得到一个指标：

```python
@property
def staleness_sec(self) -> int:
    return int((self.retrieved_at - self.as_of).total_seconds())
```

**② naive datetime 直接拒绝，不猜时区。**

```python
if v.tzinfo is None:
    raise ValueError(f"Evidence.{name} 必须带时区（tzinfo），收到 naive datetime")
```

「猜一个默认时区」是又一个 fail-open：猜对了没人注意，猜错了跨日聚合会静默错位 8 小时。

**③ `frozen=True`。**
证据一旦产生就不该被修改。这既是语义要求（事实不会变），
也让「冻结证据后重跑合成」这件事在实现上是安全的。

### ⚠️ 一处对设计文档的偏离：`Evidence` 多了 `field`

架构文档 §4.1 里的 `Evidence` 没有 `field`。这里加上了，理由是**不加就有一条铁律无法执行**：

> 铁律 3：每个 `result` 字段必须能追到至少一条 `Evidence`。

没有 `field`，这条只能靠人读代码判断。有了它，一行就能钉死：

```python
covered = {e.field for e in self.evidence}
orphan = sorted(set(self.result) - covered)
if orphan:
    raise ValueError(f"result 字段无证据支撑: {orphan}")
```

另外，文档 §7.1 给出的 Card 渲染样例本身就带字段名
（`[Market] 涨跌家数 1842/3105 as_of 10:00`），不加 `field` / `label` 连卡片都渲染不出来。
所以这与其说是偏离，不如说是补上了文档自己隐含要求的东西。

### `AgentVerdict` —— Specialist 的唯一返回结构

关键字段是 `missing`：**必填项里没算出来的那些**。
它非空就代表结论不完整，这是整个系统里最重要的一个列表。

契约在这里管了五件事：

| 规则 | 含义 |
|---|---|
| `missing` 非空 ⇒ 不许 `verdict='PASS'` | 铁律 1 |
| `missing` 非空 ⇒ 不许 `status='completed'` | 应为 `partial` |
| `verdict='UNKNOWN'` ⇒ `missing` 必须非空 | 反向约束，见下 |
| `status='failed'` ⇒ `verdict` 只能是 `UNKNOWN` | 挂了就不该带结论 |
| `result` 的每个键必须被证据覆盖 | 铁律 3 |

第三条是反向的，容易被忽略但同样重要：
**一个没有 `missing` 的 `UNKNOWN`，会在 Card 上渲染成一个空的「缺失项」段落。**
读者看到的是「系统说它不知道，但说不出不知道什么」—— 这种输出比没有输出更糟，
因为它看起来像是系统在工作。

### `DecisionCard` —— 交给人的产物

渲染原则来自上游文档：

> 优先展示**证据项、风险项、缺失项、状态**，而不是一个看似精确的模型分数。

契约在这里管三件事：

1. **铁律 2**：`missing` 非空 ⇒ 不得给 `BUY`。
2. **缺失项必须完整上浮**：Card 的 `missing` 必须 ⊇ 各 Verdict 的 `missing` 之并集。
   汇总时丢掉一条，就是在掩盖系统自己不知道的事。
3. **否决权不可绕过**：任一 Verdict 是 `BLOCK` ⇒ 不得给 `BUY`。

第 2 条是这三条里最容易漏的。Verdict 层面已经拦住了「缺数据还说 PASS」，
但**汇总**是另一个环节 —— Supervisor 完全可以在合成时「顺手」把某条缺失项忘掉。
所以要在 Card 层再拦一次：

```python
upstream = {m for v in self.verdicts for m in v.missing}
dropped = sorted(upstream - set(self.missing))
if dropped:
    raise ValueError(f"Verdict 报告的缺失项没有上浮到 Card: {dropped}")
```

还有一个小而关键的渲染决定：**缺失项段落永远显示，哪怕是 0 条。**

```
缺失项：无
```

因为「没有缺失项」本身是一条信息。如果空的时候就不渲染，读者无法区分
「系统检查过，什么都不缺」和「系统压根没做这个检查」。

---

## 4. 铁律 4 的守卫：AST 全仓扫描

前三条铁律靠 `__post_init__`。第四条不一样：

> 铁律 4：契约只有一份实现。任何 agent / 脚本不得自建第二套。

这条无法在运行时检查 —— 因为第二套实现**根本不会 import 第一套**。
它只能靠静态扫描。

### 为什么这条值得单独写一个测试

同一个判断散在多处各写一遍时，**错法全是静默的**：每一份都返回一个看似合理的值，
没有任何一处会抛异常。等到你发现两处口径不一致，中间可能已经过了几个月，
而基于这些数字做出的每一个结论都需要重新审。

### 要拦的三种形状

```python
# A. 重名类 —— 最好抓
class Evidence: ...

# B. 近名类 —— 契约漂移最常见的起点
class EmotionVerdict: ...
class MarketEvidence: ...

# C. 字典版契约 —— 最隐蔽
return {"task_id": ..., "agent": ..., "verdict": "PASS",
        "missing": [], "elapsed_ms": 10}
```

**C 是最危险的一种**，因为它不引入任何新符号。
grep `class Evidence` 搜不到它，grep `AgentVerdict` 也搜不到它。
它就是一个普通的 dict，看起来像临时数据，实际上是一份完整的、平行的契约实现。

抓 C 的办法是**按键集合的形状匹配**：

```python
DICT_SHAPES = {
    "AgentVerdict": (
        {"task_id", "agent", "status", "verdict", "result",
         "confidence", "evidence", "warnings", "missing", "elapsed_ms"},   # 全集
        {"task_id", "verdict", "elapsed_ms", "missing"},                   # 特征键
    ),
    ...
}
```

判定条件是「命中 ≥4 个键，且至少命中 1 个**特征键**」。
特征键的作用是压误报 —— `{"field", "source", "value"}` 这种词太普通，
任何一个配置字典都可能撞上；而 `as_of` / `retrieved_at` / `elapsed_ms` 就相当独特了。

### 豁免机制：留一个口子，但要求写理由

测试里需要故意造一个「长得像契约的假对象」来验证契约会拒绝它。这是合法的。

```python
# contract-exempt: 鸭子类型，用于断言契约会拒绝它
class FakeEvidence:
    field = "limit_up"
```

扫描器检查该行或上一行有没有 `contract-exempt:` 标记。
**要求写在注释里而不是配置文件里**，是为了让豁免的理由和被豁免的代码待在一起 ——
配置文件里的豁免清单会腐烂，代码旁边的注释不会。

### 一个真实的迭代：规则精确化 ≠ 逐个豁免

第一次跑扫描，规则 B 报了一片红：

```
tests/test_contract_behavior.py:63  class TestEvidence
tests/test_contract_behavior.py:98  class TestVerdictIronLaw1
tests/test_contract_behavior.py:123 class TestVerdictIronLaw3
...
```

这些是 pytest 的测试分组容器，名字里带 `Evidence` / `Verdict` 纯属巧合。

当时有两条路：

- **给每个加一行 `# contract-exempt`** —— 5 分钟搞定
- **改规则：跳过 `Test` 开头的类** —— 需要想清楚这个跳过是否安全

选了后者。理由：

> 豁免应该用在「代码确实特殊」的地方，不该用来补偿「规则不够准」。
>
> 如果规则的误报要靠一堆豁免注释来压，那这些注释很快就会变成噪音 ——
> 下次真有一条该看的豁免混在里面，没人会注意到。

`Test` 前缀是 pytest 的**收集约定**，不是巧合命名，所以按前缀跳过是有依据的，
不是为了让测试变绿而开的后门。

---

## 5. 守卫的守卫：怎么证明这个测试真的会红

一个永远通过的测试和没有测试是一回事 —— 而且更糟，因为它让你觉得有保障。

两道保险。

**① 扫描范围非空自检**（写在测试里，常驻）：

```python
def test_扫描范围非空():
    assert len(ALL_FILES) >= 3, f"只扫到 {len(ALL_FILES)} 个 .py，扫描范围可能坏了"
    assert any(_in_contract(p) for p in ALL_FILES), "没扫到 _contract/ 本身"
```

排除目录的规则一旦写错（比如某天加了个把整个 `skills/` 排除掉的规则），
扫描器会安静地扫 0 个文件然后全绿。这个自检就是防这个的。

**② 探针实验**（手动，写完当场做一次）：

```bash
mkdir -p skills/_scan_probe
cat > skills/_scan_probe/violation_a.py <<'PY'
from dataclasses import dataclass
@dataclass
class Evidence:          # A：重名类
    source: str
PY
# ... B 和 C 同理

python3 -m pytest tests/test_contract_single_impl.py
```

实际输出：

```
E   skills/_scan_probe/violation_a.py:3 class Evidence
E   skills/_scan_probe/violation_b.py:1 class EmotionVerdict
E   skills/_scan_probe/violation_c.py:2 疑似手搓 AgentVerdict，命中键 ['agent', 'elapsed_ms', 'missing', 'task_id', 'verdict']
3 failed, 5 passed
```

然后 `rm -rf skills/_scan_probe`，恢复全绿。

> **通用原则**：写完一个守卫，立刻造一个它应该抓到的违规，确认它真的报红。
> 「测试通过」只证明当前代码没问题，不证明测试本身有效。

---

## 6. 测试怎么组织

51 条测试分两个文件，分工明确：

| 文件 | 管什么 | 条数 |
|---|---|---|
| `tests/test_contract_behavior.py` | 四条铁律在运行时确实拦得住 | 43 |
| `tests/test_contract_single_impl.py` | 全仓没有第二份实现 | 8 |

行为测试按铁律分组，类名直接就是规则编号：

```python
class TestVerdictIronLaw1:
    """铁律 1：UNKNOWN ≠ PASS。算不出来必须说算不出来。"""

    def test_missing非空时不许PASS(self): ...
    def test_missing非空时不许completed(self): ...
    def test_missing非空时WARNING加partial是合法的(self): ...
    def test_UNKNOWN必须说明缺什么(self): ...
```

注意每组里都有一条**正例**（`test_missing非空时WARNING加partial是合法的`）。
只测「非法的被拒绝」是不够的 —— 规则写得太严会让合法用法也过不去，
而这种问题在实现 Agent 时才会暴露，那时排查成本高得多。

测试名用中文是刻意的：这些测试的读者是三个月后要改契约的人，
`test_missing非空时不许PASS` 比 `test_pass_with_missing_raises` 更快说清楚发生了什么。

---

## 7. 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest
# → 51 passed
```

`pyproject.toml` 里让 `skills/` 成为包根，业务代码一律 `from _contract import ...`：

```toml
[tool.pytest.ini_options]
pythonpath = ["skills", "."]
testpaths = ["tests"]
addopts = "-q --strict-markers"
```

手工验一下卡片长什么样：

```python
python3 -c "
import sys; sys.path.insert(0,'skills')
from datetime import timedelta
from _contract import Evidence, AgentVerdict, DecisionCard, now_cn, new_task_id
t = now_cn()
e = lambda f,v,l: Evidence(field=f, source='biga.db:raw_market_snapshot', value=v,
                           as_of=t-timedelta(seconds=300), retrieved_at=t, label=l)
v = AgentVerdict(task_id=new_task_id(1), agent='emotion', status='partial',
                 verdict='WARNING', result={'limit_up':42,'broken_rate':0.31},
                 confidence=0.7, evidence=[e('limit_up',42,'涨停家数'),
                                           e('broken_rate',0.31,'炸板率')],
                 missing=['最高板 —— 连板数据源超时'], elapsed_ms=8400)
print(DecisionCard(decision_id=new_task_id(1), status='WAIT',
      headline='情绪指标分歧，且连板高度缺失，不足以支持买入判断。',
      verdicts=[v], synthesis='', model_ref='anthropic/claude-sonnet-5',
      missing=['最高板 —— 连板数据源超时'], elapsed_ms=41000).render())
"
```

输出：

```
BIGA DECISION CARD   BIGA-20260919-001   15:20:14   耗时 41s
──────────────────────────────────────────────────────────────
emotion      WARNING  limit_up=42; broken_rate=0.31

状态：WAIT

核心矛盾：情绪指标分歧，且连板高度缺失，不足以支持买入判断。

⚠ 缺失项（1）
  · 最高板 —— 连板数据源超时

证据
  [emotion] 涨停家数 = 42   as_of 09-19 15:15   src biga.db:raw_market_snapshot
  [emotion] 炸板率 = 0.31   as_of 09-19 15:15   src biga.db:raw_market_snapshot
──────────────────────────────────────────────────────────────
model_ref: anthropic/claude-sonnet-5   ·   本卡为决策辅助，不构成投资建议
```

---

## 8. 本章要点

| 要点 | 一句话 |
|---|---|
| 先定契约再写采集 | Agent 之间传的是消息，消息没有类型检查 |
| 让非法状态无法被表示 | 规则违反时不会自然报错 ⇒ 必须在构造时拒绝 |
| 抛异常，不是打 warning | 日志里的 warning 等于没有 |
| `as_of` ≠ `retrieved_at` | 混淆这两者会让盘中数据和收盘数据被当成同一时刻 |
| naive datetime 直接拒绝 | 「猜一个默认时区」是又一种 fail-open |
| 空的缺失项段落也要渲染 | 「没有缺失项」和「没做这个检查」必须能区分 |
| 字典版契约最隐蔽 | 它不引入新符号，grep 搜不到，只能按键的形状匹配 |
| 豁免要写理由且贴着代码 | 配置文件里的豁免清单会腐烂 |
| 规则不准就改规则 | 用豁免补偿误报，会让真正该看的豁免淹没在噪音里 |
| 守卫写完当场造违规验证 | 「测试通过」不证明测试有效 |

---

上一章：[02 · Profile 初始化](02-profile-setup.md)　|　下一章：04 · 数据层（编写中）
