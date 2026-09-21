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
  --verdict-ids 17,18 \
  --status WAIT \
  --headline "核心矛盾一句话" \
  --synthesis "合成说明（可选）" \
  --extra-missing "risk agent 尚未上线，本卡未经风险审查" \
  --model-ref anthropic/claude-sonnet-5
```

### 🔴 为什么是 id，不是 JSON

`--verdict-ids` 里的数字是 skill 落库时在 stderr 打出的 `verdict_ref=NN`。
**判定数据直接从库里取，不经过语言模型。**

`--verdicts <文件>` 仍然保留，但它要求 agent 逐字搬运结构化数据。
实测（2026-09-20，`BIGA-20260920-002`）那条路的代价：

| 观察 | 数字 |
|---|---|
| Supervisor 零工具调用、纯粹生成 JSON 的时间 | 47 秒 |
| 随后因缺字段失败重试 | 49 秒 |
| Specialist 转述后 15 条 evidence 里剩下的 `retrieved_at` | **0 条** |
| 落库 `retrieved_at` 与真实采集时刻的偏差 | **106 秒** |

⇒ **agent 走 `--verdict-ids`。** `--verdicts` 只留给手工调试与回放排查。

### 追加缺失项

Specialist 要在 skill 的事实之上补自己的局限时，**不重打 JSON**：

```bash
python3 skills/decision-card/scripts/amend_verdict.py --ref 17 \
  --add-missing "市场趋势 —— 只有单日快照，无法判断方向" \
  --verdict WARNING
# → stderr: verdict_ref=18（原件 #17 不动，这是一行指回它的修订）
```

落库时会：
1. 为每个 Verdict 在 `agent_runs` 记一行 —— 那是**执行账本**，不是调用证明
2. 把整张 Card 冻结进 `decision_records.card_json`

⚠️ 这里曾经写着「那是『确实调用过』的唯一凭证」。**不成立** ——
写那一行的代码就是 `synthesize.py` 自己，人手工跑一遍它照样多出几行。
真正的 spawn 证明在运行时自己的库里，判据是 `tools/verify/spawn_check.py`
（`agent_runs` + 运行时 `subagent_runs` **两份独立记录都齐**才算）。

判定原件本身在 `agent_verdicts`（schema v3）：skill 写、synthesize 读，
只追加不修改，修订用 `amends` 指回原行。

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
