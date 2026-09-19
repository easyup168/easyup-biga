---
name: decision-card
description: Decision Card 的合成、落库、渲染与回放。🔴 在线路径与回放路径**共用同一份组装代码**（card_ops.synthesize）。Supervisor 提供判断（status/headline/synthesis），脚本负责组装 + 契约校验 + 落库 + 渲染。不许手写 Card 的 JSON。
---

# decision-card —— 合成与回放

## 为什么必须共用一份代码

回放的用途是「换个模型用同一批证据重跑，看结论会不会变」。

如果在线和回放各写一套组装代码，观察到的差异里就混进了**代码差异**，
而你本来只想看**模型差异**。这个实验就废了。

因此：

- `card_ops.synthesize()` 是**唯一**的组装实现，且是**纯函数**
- `synthesize.py`（在线）和 `replay.py`（回放）都只是它的 CLI 外壳
- 有测试用 AST 断言这两个脚本都从 `card_ops` 导入 `synthesize`，
  且都没有自己 `DecisionCard(...)`

## 职责边界

| 代码负责 | 模型负责 |
|---|---|
| 聚合缺失项、校验契约、落库、渲染 | `status` / `headline` / `synthesis` |

**代码负责「怎么拼」，模型负责「拼出什么结论」。** 共用的是前者。

## 在线

```bash
python3 skills/decision-card/scripts/synthesize.py \
  --verdicts v.json \
  --status WAIT \
  --headline "核心矛盾一句话" \
  --synthesis "合成说明（可选）" \
  --extra-missing "risk agent 尚未上线，本卡未经风险审查" \
  --model-ref anthropic/claude-sonnet-5
```

`--verdicts -` 可从 stdin 读，直接接 `emotion_calc.py` 的输出。

落库时会：
1. 为每个 Verdict 在 `agent_runs` 记一行 —— **那是「确实调用过」的唯一凭证**
2. 把整张 Card 冻结进 `decision_records.card_json`

## 回放

```bash
# 一致性检查：沿用原判断重跑，断言逐字段相同
python3 skills/decision-card/scripts/replay.py BIGA-20260919-001 --check

# 换模型：新判断 + 同一批冻结证据
python3 skills/decision-card/scripts/replay.py BIGA-20260919-001 \
  --status AVOID --headline "..." --model-ref anthropic/claude-opus-5 --store
```

回放**永不覆盖**原始记录 —— 追加一行，`replay_of` 指回原始 `record_id`。
数据库层面用 partial unique index 保证「一个 decision_id 只有一条在线记录」。

### `--check` 在比什么

`comparable()` 会剥掉 `generated_at` / `elapsed_ms` —— 它们描述的是**这次执行**，
不是**这个结论**。拿它们比较会让任何两次回放都「不一致」，
于是这个检查退化成永远报警，很快没人看。

证据的时间戳**不剥** —— 回放用的是冻结证据，它的 `as_of` 本来就必须一模一样。

## 一个被 `--check` 抓到的真 bug

Supervisor 自己发现的缺失项（「risk agent 尚未上线」这类）通过
`--extra-missing` 传入，但它没有单独存列。第一版回放没有还原它，
于是回放出的 Card **悄悄少掉三条缺失项** —— 正是本项目最怕的「静默变好看」。

修法是从原卡精确反推：

```python
from_verdicts = {m for v in original.verdicts for m in v.missing}
extra_missing = [m for m in original.missing if m not in from_verdicts]
```

因为聚合规则是「先 Verdict 后 extra、去重、保序」，这个反推是无损的。

## 依赖

Python 3.12 标准库 + `skills/_contract` / `skills/_store`。无第三方依赖。
