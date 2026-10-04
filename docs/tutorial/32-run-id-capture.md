# 第 32 章 · 让 `run_id` 贯穿全链（capture，先不 enforce）

> 📁 **过程文档** · 写完即冻结
> **覆盖**：确定性编排批 J-I —— 给 `agent_verdicts` / `evidence_sets` / `VerdictRef` /
> `DecisionCard` 各加一个 `run_id`，让「这条判定、这份冻结、这张卡是哪次执行尝试产生的」
> 能被查到；六个 skill 加 `--run-id`；判断行从事实行**继承** run_id。
> **不覆盖**：按 `run_id` 强制过滤（拒绝跨 run 串读）—— 那是 enforce，等真正的重试路径
> 出现再做；`run_id` 三同名的收敛（那是批 J-II，第 31 章）。

---

## 目标 / 产出

`run_id` 从批 B 就存在于 `decision_runs` / `run_events`，但一直没往下贯穿。做完这一章：

- `agent_verdicts.run_id` / `evidence_sets.run_id`（v10，都 nullable、不回填）。
- 六个 skill 各有可选 `--run-id`，落进 `save_fact_bundle`。
- `save_assessment` 从它 `amends` 的 fact 行**继承** run_id（不加 CLI 参数）。
- `VerdictRef.run_id` / `DecisionCard.run_id`，回放能追到「这张卡用的原件是哪次 run 的」。

---

## 为什么这么做

### 为什么只做一半（capture，不 enforce）

复盘（设计文档 §2 追加 5.1）曾把两件事当成一件、一起无限期排期：

| | 把 `run_id` **存下来**（capture） | 按 `run_id` **强制校验**（enforce） |
|---|---|---|
| 现在有消费方吗 | 有 —— `run_id` 从批 B 就在写 `decision_runs`，这里只是让**已经在写别的字段**的表顺手多存一列 | 没有 —— 「按 run_id 过滤、拒绝跨 run 串读」只有重试路径存在才有意义 |
| 不做会怎样 | 越晚越贵：批 E 系列正在**同一层**做迁移，晚一步要再开一次刀，还要处理期间新落的历史数据 | 不会变贵 —— 加一个 `WHERE run_id=?` 不会因为多等几批而更难写 |

所以拆开：capture 现在做（最便宜的时机），enforce 等消费方出现。

> 通用原则：「填了但没人查」的时间不是浪费，是在为将来的消费方攒数据 —— **前提是这一列
> 几乎零成本地搭在一次已经在做的迁移上**。反过来，一个没有消费方、还要单独开刀的字段，
> 就是 L-1 死配置。区别不在「有没有人用」，在「攒它的边际成本」。

### 2b：判断行的 run_id 为什么「继承」而不是「传参」

事实行由 skill 写（带 `--run-id`）。判断行由 Agent 经
`amend_verdict.py --ref <fact_id> --stance …` 写 —— 它天然带着 `amends`→事实行的指针，
而事实行已经有 run_id 了。有两条路可选：

1. 给 `amend_verdict.py` 也加一个 `--run-id`，让 Agent 传。
2. 让 `save_assessment` 从它 `amends` 的那行 fact **读** run_id。

选 2。理由不是省一个参数：

> Agent 手传就**可能传错**（传成别的 run、或干脆漏传）；而「从 fact 行读出来复制」在
> **结构上**不可能与事实行不一致 —— 因为那个值不经过 Agent 的手。

这是本仓库反复出现的一条：**判据别建在可被篡改的输入上**（同 spawn 证明不信 LLM 复述的
`verdict_ref=NN`、跨源校验不信自由文本）。怎么**证明**「不可能不一致」？两条独立证据：

- **结构**：`inspect.signature(save_assessment)` 里没有 `run_id` 参数，`amend_verdict.py`
  的 argparse 里也没有 `--run-id` —— Agent 在命令行上根本**够不到**这个值。
- **行为**：存进去的 `assessment.run_id` 永远逐字节等于它 `amends` 的 `fact.run_id`
  （fact 有值继承有值、fact 是 None 继承 None）。

两条都固化成测试。缺一不可：只有行为证明，将来有人给 `save_assessment` 加个 `run_id`
参数、行为测试仍可能碰巧过；只有结构证明，不能保证读的就是 fact 那一行。

### 历史行没有它 —— nullable、`.get()`、不回填

`run_id` 一律可空，历史行读回来是 `None`。两个反序列化入口（`VerdictRef.from_dict` /
`DecisionCard.from_dict`）都用 `d.get("run_id")` 而不是 `d["run_id"]`：

> 迁移前落的卡，JSON 里**根本没有这个键**。用 `d["run_id"]` 会让每一张旧卡在读取时
> `KeyError` —— 一个「加了新字段」的动作，把**所有历史数据变成读不回来的**。

做成 `NOT NULL` 是同一个错的更硬版本：等于强迫历史数据造假。capture 的整个前提是
「不知道就诚实说不知道（None）」，不是「凭空补一个」。

### 回放不许捏造 run_id —— 连 `comparable()` 一起改

回放**不是**原来那次执行尝试。它诚实地把 `card.run_id` 记成 `None`（`replay.py` 不给
`synthesize` 传 run_id），而原卡带着它真实的 run_id。这就带出一个隐藏的坑：

`replay.py --check` 靠 `comparable(原卡) == comparable(回放卡)` 判「组装无损」。一旦在线
路径开始产出**带 run_id** 的卡，回放它 —— 原卡 `run_id="abc"`、回放卡 `run_id=None` ——
`--check` 就会因为「这次执行 ≠ 上次执行」误报**组装不一致**，把一个正确的无损回放判成坏的。

⇒ `comparable()` 把 `run_id` 与 `generated_at` / `elapsed_ms` 一同剥掉：它们描述的都是
**这次执行**，不是**这个结论**。

> 通用原则：给一个会被回放/比对的对象加字段时，先问一句「这个字段属于**结论**还是属于
> **这次执行**」。属于执行的，必须同时进 `comparable()` 的剥离清单 —— 否则一致性守卫会
> 从「验结论」悄悄退化成「验执行」，然后永远报警。

⚠️ 但 `input_verdict_refs` 里各 ref **自带**的 run_id 不剥 —— 那是判定原件的血缘，回放
照原样带过去（两边相同），是要被核对的证据的一部分。「剥顶层、留嵌套」这条差别，正是
第 27 章 A6 那套「Card 记它用了哪一行原件」在这一章的延续。

---

## 执行

```bash
BIGA=~/.openclaw-biga/bin/biga
cd ~/.openclaw-biga/workspace   # 实际在干净的独立 worktree 上做

python3 -m pytest -q | tail -2               # 基线（本 worktree 实测，不照抄）
grep -n "(9, _V9)\|(10, _V10)" skills/_store/schema.py   # 确认取 v10（J-II 占了 v9）

# v10：两张表各加一列
#   ALTER TABLE agent_verdicts ADD COLUMN run_id TEXT;
#   ALTER TABLE evidence_sets  ADD COLUMN run_id TEXT;

# 六个 skill：--run-id → save_fact_bundle(fb, run_id=args.run_id)
# save_assessment：从 load_verdict_meta(fact_id)["run_id"] 继承（不加参数）
# orchestrator：_specialist_task/_risk_task 带 --run-id；freeze_index_daily(run_id=ctx.run_id)
# VerdictRef/DecisionCard：加 run_id 字段；两处 from_dict 用 .get；comparable() 剥 run_id

python3 -m pytest -q | tail -2               # 全绿
bash tools/verify/sync_test_count.sh         # 同步条数（新增探针）
```

一处**跨批的意外收获**：J-II 那道守卫 `TestRunIdNamespace`（任何名为 `run_id` 的列必须
`TEXT`、值可追到 `decision_runs.run_id`）**自动**把我新加的两列纳入了判据 —— 我什么都没
改它就覆盖到了。这正是「守卫要可派生、不写清单」的回报：新列一落地，守卫立刻替它把关。

---

## 坑

**给测试造样本时撞上「不许手搓契约」守卫**。P2 要验「历史卡 JSON 没有 run_id 键也能
读回」，于是手写了一个 card 形状的 `dict`。铁律 4 的静态扫描（`test_C_不许手搓字典版契约`）
当场把它判成「第二套字典契约」。这不是误报 —— 我确实写了个 card 形状的 dict。但用例
**必须**这么写：`DecisionCard().to_dict()` 造不出「缺 run_id 键」的样本（它总会带上）。
⇒ 加 `# contract-exempt: <理由>` 豁免。⚠️ 豁免标记只在**节点行或紧邻上一行**才被识别
（`_is_exempt` 查 `lineno` 与 `lineno-1`）——把它写在三行注释块的最上面，等于没写。

**跨文件的模块污染，只在全量里复现**。新测试文件按仓库惯例用 `_load("card_ops")` 从
文件路径加载脚本，它会 `sys.modules["card_ops"] = 新实例`。而 `orchestrator.py` 在**它
自己**import 时早已绑定了另一个 card_ops 实例 —— 于是 `test_orchestrator` 里
`monkeypatch.setattr(card_ops, "persist", …)` 打在新实例上、orchestrator 却调旧实例，
patch **静默落空**。单跑那个文件绿，全量红。修法：让 `_load` **幂等** —— 已在
`sys.modules` 就复用，绝不用新实例覆写（模块本就该按名字单例）。

> 通用原则：一个测试单跑绿、全量红，几乎总是**全局状态被别的文件改过**。`sys.modules`、
> `os.environ`、类属性、单例 —— 先查这些，别改被污染的那个测试本身。

---

## 验证

```bash
BIGA=~/.openclaw-biga/bin/biga
cd ~/.openclaw-biga/workspace

python3 -m pytest -q | tail -2                                   # 全绿

# 四道探针
python3 -m pytest tests/test_run_id_capture.py -q                # P1–P4 + 2b 继承

# orchestrator 接线（item 3/4）
python3 -m pytest tests/test_orchestrator.py -k JI -q            # evidence_sets.run_id / 六 agent --run-id

# 回放一致（新卡带 run_id 也不误判）
bin/biga-card --check <某个已落库决策号>
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | capture 与 enforce 拆开做：区别不在「有没有人用」，在「攒这一列的**边际成本**」—— 搭在一次正在做的迁移上几乎零成本，就该现在做 |
| 2 | 判断行的 run_id **从事实行继承**，不让 Agent 传 —— 手传可能传错，「继承」在结构上不可能不一致（判据别建在可篡改的输入上）|
| 3 | 「不可能不一致」要用**两条独立证据**证明：结构（根本没有那个参数）＋ 行为（存进去的值永远等于来源）|
| 4 | 加 nullable 新字段，两个 `from_dict` 都用 `.get`，不是 `[]` —— 否则历史 JSON 缺键就 `KeyError`，一个新字段让旧数据集体读不回来 |
| 5 | 给会被回放/比对的对象加字段，先问「属于结论还是这次执行」；属于执行的必须同进 `comparable()` 剥离清单（剥顶层 run_id、留 ref 自带的血缘）|
| 6 | 本批最软的一环是**捕获率取决于 Agent 是否照提示词加 `--run-id`** —— capture 不 enforce 的固有性质，堵死它要等 enforce 那一批 |
| 7 | 可派生的守卫会替新数据自动把关：J-II 的 `run_id` 列守卫无需改动就覆盖了本批新加的两列 |

> ⏩ **后续变动（2026-09-23，批 H-I）**：本章出现的 `skills/_contract` / `skills/_store` /
> `skills/_sources` 三个共享包，其**真实实现**已迁至
> `src/easyup_biga/{domain,persistence,providers}/`。旧路径原地保留 re-export 薄壳 ⇒
> 本章正文里的 `from _contract import ...` 等导入语句与位置描述**照旧成立**，只是代码
> 本体不在那儿了。见 `CHANGELOG.md` 批 H-I。
