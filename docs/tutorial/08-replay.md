# 第 08 章 · 回放：让「换个模型试试」变成一个可控实验

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：用冻结的证据重跑合成，且保证在线与回放**共用同一份组装代码**。
>
> **本章产出**：`skills/decision-card/` + 15 条测试，累计 117 条。
>
> 注：本章的代码在第 07 章（端到端）之前就写好了 —— 因为 Supervisor 的
> 工作流程里要用到它，不能等跑通了再补。

---

## 1. 回放要回答什么问题

「回放」听起来像个调试功能。它不是。它要回答的是三个真问题：

1. **换个模型，同一批证据会得出不同结论吗？**（模型选型的依据）
2. **改了 prompt，历史案例的结论会变吗？**（prompt 改动的回归测试）
3. **当时看到的证据，足以支撑当时那个结论吗？**（事后复盘）

三个问题有一个共同前提：**除了你要变的那一个变量，其余全部不变。**

---

## 2. 因此：在线与回放必须共用一份组装代码

这是本章唯一的硬性要求，来自架构文档：

> 回放必须与在线路径共用同一份合成代码，不许另写一套。

原因很直接：如果两条路径各写一套组装逻辑，你观察到的差异里就混进了**代码差异**，
而你本来只想看**模型差异**。实验作废。

而且这种污染是**看不出来的** —— 两份代码都能跑、都输出一张 Card，
只是某个字段的聚合方式悄悄不一样。

### 职责怎么切

难点在于：合成这件事里，一部分是代码干的，一部分是模型干的。

| 代码负责 | 模型负责 |
|---|---|
| 聚合缺失项、校验契约、落库、渲染 | `status` / `headline` / `synthesis` |

**代码负责「怎么拼」，模型负责「拼出什么结论」。** 共用的是前者。

于是有了 `Judgment` 这个结构 —— 把「模型给的那部分」单独封起来：

```python
@dataclass(frozen=True)
class Judgment:
    status: CardStatus
    headline: str
    synthesis: str = ""
    extra_missing: list[str] = dc_field(default_factory=list)
```

和一个**纯函数**：

```python
def synthesize(*, decision_id, verdicts, judgment, model_ref,
               elapsed_ms=0, generated_at="") -> DecisionCard:
    """把 Verdict 组装成 Card。**纯函数，不碰 IO。**

    纯函数是「在线与回放一致」的前提：
    只要输入相同，两条路径的输出必然逐字节相同，不依赖当时的库状态或网络。
    """
```

`synthesize.py`（在线）和 `replay.py`（回放）都只是它的 CLI 外壳。

### 用测试钉死这件事

口头约定「都要用 card_ops」是不够的。写了两条 AST 测试：

```python
def test_两条路径都从card_ops导入synthesize(self):
    # 断言 synthesize.py 和 replay.py 都 from card_ops import synthesize

def test_两个脚本都不自己构造DecisionCard(self):
    """只有 card_ops 能直接 new 一张 Card；CLI 必须走它。"""
    direct = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "DecisionCard"]
    assert not direct, f"{f}.py 绕过 card_ops 直接构造了 DecisionCard"
```

第二条比第一条重要：**导入了共用函数，不代表用了它。**
完全可以一边 import 一边在旁边自己 `DecisionCard(...)`。

---

## 3. 回放不覆盖原始记录

如果回放覆盖了原记录，问题 1「换模型结论会变吗」就永远没法回答 ——
你把对照组抹掉了。

方案是**同表 + 自引用列**（第 04 章介绍过）：

```sql
replay_of INTEGER REFERENCES decision_records(record_id)   -- 在线路径为 NULL

CREATE UNIQUE INDEX ux_decision_online
    ON decision_records(decision_id) WHERE replay_of IS NULL;
```

实测：

```
#1 在线           WAIT   missing=3 anthropic/claude-sonnet-5 (synth/1)
#2 回放(of 1)     AVOID  missing=3 anthropic/claude-opus-5 (synth/1)
```

同一个 `decision_id`，两条记录，原始那条纹丝不动。

### `model_ref` 带上组装版本

```python
SYNTHESIS_VERSION = "synth/1"
model_ref=f"{model_ref} ({SYNTHESIS_VERSION})"
```

三个月后看到两张结论不同的 Card，第一个要问的问题是
「是模型变了，还是我改过组装代码」。把组装版本写进 `model_ref`，这个问题一秒就有答案。

⚠️ 有个细节：回放时要把取回的 `model_ref` 里的版本后缀**剥掉**再重新加，
否则回放一次叠一层，变成 `sonnet-5 (synth/1) (synth/1)`。有测试守着：

```python
def test_model_ref不层层累积版本后缀(self, db):
    assert m.count(card_ops.SYNTHESIS_VERSION) == 1
```

---

## 4. `--check`：一致性检查

最基础的一种回放：**不换任何东西，重跑一遍，断言结论逐字段相同。**

```bash
python3 skills/decision-card/scripts/replay.py BIGA-20260919-001 --check
```

它验证的是「组装是确定性的」。这条要是不成立，后面所有的对比实验都没有基准。

### 比较时要剥掉什么

```python
def comparable(card: DecisionCard) -> dict:
    """剥掉「每次必然不同」的字段。

    去掉 `generated_at` / `elapsed_ms` —— 它们描述的是**这次执行**，
    不是**这个结论**。拿它们比较会让任何两次回放都「不一致」，
    于是一致性检查就退化成永远报警，很快没人看（又一个被忽略的守卫）。
    """
```

反过来，证据的时间戳**不剥**。回放用的是冻结证据，它的 `as_of` 本来就必须一模一样；
如果它变了，那是真出问题了。

> **通用原则**：做「两次运行是否一致」的比较时，要精确区分
> **描述结论的字段**和**描述这次执行的字段**。
> 剥少了，检查永远报警，很快被忽略；剥多了，真正的差异会被藏起来。

---

## 5. `--check` 当场抓到一个真 bug

写完第一版，跑一致性检查，红了：

```
❌ 不一致 —— 在线与回放的组装结果不同：
  missing:
    在线 ['情绪周期趋势 —— 只有单日快照…', '风险审查 —— risk agent 尚未上线…',
          '交易纪律审查 —— discipline agent 尚未上线']
    回放 []
```

**回放出的 Card 比原始 Card 少了三条缺失项。**

原因：Supervisor 自己发现的缺失项（「risk agent 尚未上线」这类）是通过
`--extra-missing` 传进来的，属于**判断的一部分**。
但它没有单独存列，第一版回放没有还原它。

这个 bug 的形状非常典型 —— **它让 Card 悄悄变好看了**。
一张缺三项的卡变成一张什么都不缺的卡，没有任何报错。
如果不是 `--check` 逐字段比对，它会一直存在下去。

### 修法：从原卡精确反推

不加新列，因为聚合规则本身是可逆的：

```python
# 聚合规则是「先 Verdict 后 extra、去重、保序」，所以这个反推是无损的
from_verdicts = {m for v in original.verdicts for m in v.missing}
extra_missing = [m for m in original.missing if m not in from_verdicts]
```

修完：

```
✅ 一致：BIGA-20260919-001 用冻结证据重跑，结论逐字段相同。
```

### 然后给 `--check` 本身加了个测试

抓到一个 bug 只说明它当时有用。要保证它**以后还有用**：

```python
def test_检查能发现不一致(self, db, monkeypatch, capsys):
    """守卫的守卫：--check 必须真的会红，否则它是空转的。"""
    self._seed(db)
    real = card_ops.synthesize

    def drifted(**kw):            # 人为让回放的组装结果漂掉
        kw["judgment"] = card_ops.Judgment(status="AVOID", ...)
        return real(**kw)

    monkeypatch.setattr(replay, "synthesize", drifted)
    assert replay.main([DID, "--check"]) == 2
```

这是第 03、04 章那个「探针」做法的测试化版本 ——
把「手工造一个违规看它红不红」固化成一条常驻测试。

---

## 6. 一个顺手修掉的渲染不一致

跑完回放，肉眼对比两张卡，发现摘要行不一样：

```
在线：emotion PASS  limit_up_count=78; max_streak=4; streak_2plus_count=12
回放：emotion PASS  advance_count=4277; broken_board_count=25; broken_rate=0.2427
```

数据完全相同，只是渲染时取的「前 3 个字段」不同。

原因：`render()` 取的是 `list(v.result)[:3]` —— **插入顺序**。
而 Card 落库时 `json.dumps(..., sort_keys=True)` 排过序，
回放取回来的 `result` 是字典序。

`--check` 没有报警（它比的是 dict，与顺序无关），但人眼看到的两张卡不一样。

修法是让渲染顺序稳定：

```python
# 🔴 按键名排序取前 3 个，而不是按插入顺序 ——
# 回放 diff 里的每一处差异都应该是真实差异。
summary = "; ".join(f"{k}={v.result[k]}" for k in sorted(v.result)[:3]) or "—"
```

> **通用原则**：只要存在「两次运行做 diff」的场景，
> 任何依赖插入顺序 / 哈希顺序 / 时间戳的输出都要先**规范化**。
> 否则真实差异会淹没在噪音里 —— 然后人就不看 diff 了。

---

## 7. 顺带：Supervisor 不许手写 Card

`AGENTS.md` 里写死了：

```markdown
**不要手写 Card 的 JSON。** 调合成脚本：

  python3 skills/decision-card/scripts/synthesize.py --verdicts ... --status WAIT ...

你提供的是**判断**；组装、契约校验、落库、渲染由脚本完成。
```

原因就是本章第 2 节：手写的 Card 无法被回放复现 ——
它的组装逻辑只存在于当时那段对话里，而对话是不可重放的。

顺带，落库时还会为每个 Verdict 在 `agent_runs` 记一行。
那张表是「Supervisor 确实调用过 Specialist」的唯一凭证（第 04 章）。

---

## 8. 验证

```bash
cd ~/.openclaw-biga/workspace

# 在线：用真实采集的 Verdict 出一张卡
python3 skills/emotion-calc/scripts/emotion_calc.py --no-store > /tmp/v.json
python3 skills/decision-card/scripts/synthesize.py --verdicts /tmp/v.json \
  --status WAIT --headline "情绪读数与修复期一致，但只有单日快照。" \
  --extra-missing "风险审查 —— risk agent 尚未上线" \
  --model-ref anthropic/claude-sonnet-5 --decision-id BIGA-20260919-001

# 一致性
python3 skills/decision-card/scripts/replay.py BIGA-20260919-001 --check
# → ✅ 一致：BIGA-20260919-001 用冻结证据重跑，结论逐字段相同。

# 换模型
python3 skills/decision-card/scripts/replay.py BIGA-20260919-001 \
  --status AVOID --headline "换 opus 重跑：同一批证据下更保守。" \
  --model-ref anthropic/claude-opus-5 --store
# → 回放已追加 record_id=2（replay_of=1，原记录未改动）

python3 -m pytest        # → 117 passed
```

---

## 9. 本章要点

| 要点 | 一句话 |
|---|---|
| 回放是实验装置，不是调试功能 | 它的价值取决于「除了要变的那个，其余全不变」 |
| 在线与回放共用组装代码 | 否则观察到的差异里混进了代码差异，实验作废 |
| 用纯函数承载共用部分 | 输入相同 ⇒ 输出逐字节相同，不依赖库状态与网络 |
| 「导入了」不等于「用了」 | 要断言 CLI 没有绕过共用函数自己构造 |
| 回放追加不覆盖 | 覆盖原记录 = 抹掉对照组 |
| 组装版本写进 `model_ref` | 「是模型变了还是代码变了」一秒有答案 |
| 比较时精确区分结论字段与执行字段 | 剥少了永远报警，剥多了藏起真差异 |
| **让卡「悄悄变好看」的 bug 最危险** | 少三条缺失项，没有任何报错 |
| 守卫抓到 bug 之后，给守卫加测试 | 抓到一次只说明它当时有用 |
| diff 场景下先规范化输出 | 真实差异淹没在噪音里，人就不看 diff 了 |

---

上一章：[07 · 端到端](07-end-to-end.md)　|　下一章：[09 · 隔离演练](09-isolation-drill.md)
