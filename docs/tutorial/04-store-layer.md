# 第 04 章 · 数据层：把「不可重建」这件事彻底堵死

> 📄 **过程** · 写完即冻结（只追加「⏩ 后续变动」指针）
> **覆盖**：这一段是怎么建起来的 ｜ **不覆盖**：当前设计（见 [`../design/`](../design/architecture.md)）


> **本章目标**：三张表 + 唯一 DB 入口，并让「改历史」在数据库层面就做不到。
>
> **本章产出**：`skills/_store/`（2 个模块）+ 2 个测试文件，累计 75 条测试。

---

## 1. 一处对上游文档的刻意偏离：不上 PostgreSQL + Redis

上游需求文档的数据架构是 PostgreSQL（历史事实）+ Redis（实时状态）。本项目两个都不用。

| | 文档主张 | 本实现 | 理由 |
|---|---|---|---|
| 事实层 | PostgreSQL | **SQLite (WAL)** | 当前是单机、单用户、单写进程。PG 带来的是运维面，不是能力 |
| 实时层 | Redis | 同库 + 进程内缓存 | 无跨机需求。Redis 的用途在单机下用 SQLite + 内存就够 |

偏离上游文档是件需要谨慎的事。让它变得可接受的，是下面这个动作：

### 把「什么时候该切」写死，而不是「以后再说」

```python
# 切 PostgreSQL 的触发条件（任一成立即切）：
#   1. 出现 >1 个并发写进程（例如采集与决策分离部署）
#   2. 需要跨机访问同一份事实层
#   3. 单表 > 5000 万行
```

为什么这一步关键：

> 「先用简单的，不够了再换」这句话，如果不附带**可判定的换用条件**，
> 实际含义就是「永远不换」。因为「不够了」永远可以再忍一天。

反过来，写死条件之后，「要不要换」就从一个会反复消耗精力的争论，
变成一个查一下就有答案的事实问题。

> **通用原则**：选一个「够用但会过时」的方案时，同时写下它过时的**可判定信号**。
> 没有退出条件的临时方案就是永久方案。

---

## 2. 唯一入口：为什么值得为它写一条规则

规则很简单：

> 🔴 业务代码里不许出现裸 `sqlite3.connect`，一律走 `skills/_store/db.py`。

理由也很简单：上面那三个切换条件总有一天会成立，届时要改的地方**只有一个文件**。

但这条规则的真正难点不是定规则，是**让它不被悄悄破坏**。
因为破坏它的代价在当下是零 —— 某个 skill 里图省事写一行 `sqlite3.connect(...)`，
它跑得好好的、测试全绿、review 也看不出问题。等切库那天才发现有十几处，
而那时每一处都要重新理解它在干什么。

所以配一个 AST 扫描（`tests/test_no_raw_sqlite.py`），拦两种形状：

```python
import sqlite3                      # 形状一
sqlite3.connect("data/biga.db")     # 形状二：绕过 import 检查（别名、importlib）
```

第二条是独立于第一条的 —— 不能因为「import 已经拦了」就省掉它。
守卫要拦的是**结果**，不是**某一种到达结果的路径**。

还有一条反向断言，容易被忽略但很重要：

```python
def test_store层自身确实是唯一入口():
    """反向断言：`_store/` 里确实有 sqlite3，否则说明这条规则在守一个空壳。"""
    src = "\n".join(p.read_text() for p in STORE_DIR.rglob("*.py"))
    assert "import sqlite3" in src, "_store/ 里没有 sqlite3 —— 这条规则守着一个不存在的入口"
```

如果哪天有人把 `_store/` 的实现搬走了，上面两条扫描仍然会全绿（因为确实没人用裸 sqlite3 了），
但规则已经失去意义。这条反向断言就是防这个。

> **通用原则**：一条「禁止在 X 之外做 Y」的规则，要同时断言 **X 里确实在做 Y**。
> 否则规则会在被架空之后继续显示绿色。

---

## 3. 三张表

### `decision_records` —— 回放的唯一真相源

```sql
CREATE TABLE decision_records (
    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT    NOT NULL,
    replay_of     INTEGER REFERENCES decision_records(record_id),
    status        TEXT    NOT NULL,      -- ┐
    headline      TEXT    NOT NULL,      -- ├ 查询用派生列
    model_ref     TEXT    NOT NULL,      -- │
    missing_count INTEGER NOT NULL,      -- ┘
    card_json     TEXT    NOT NULL,      -- 🔴 真相源
    generated_at  TEXT    NOT NULL,
    elapsed_ms    INTEGER NOT NULL,
    created_at    TEXT    NOT NULL
);
```

这里有一个看起来像「反范式」的设计：`status` / `headline` 既在 `card_json` 里，
又单独存了一列。通常这是数据不一致的温床 —— 有人改了一边忘了另一边。

**这里不会发生，因为调用方根本没有机会分别指定它们：**

```python
def save_card(card: DecisionCard, *, replay_of: int | None = None) -> int:
    if not isinstance(card, DecisionCard):
        raise TypeError(f"save_card 只接受 _contract.DecisionCard，收到 {type(card).__name__}")
    payload = json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True)
    conn.execute("INSERT INTO decision_records (...) VALUES (?,?,...)",
                 (card.decision_id, replay_of, card.status, card.headline, ...))
```

接口只收**整个卡对象**，派生列全部从它身上取。想让两者不一致，
你得先构造一个自相矛盾的 `DecisionCard` —— 而第 03 章的契约层已经让那件事做不到了。

> **通用原则**：反范式本身不危险，**「允许分别写入」才危险**。
> 把派生值的生成收进唯一的写入口，不一致就从「要靠纪律避免」变成「构造不出来」。

### `agent_runs` —— 兼任「证明 Agent 真的被调用过」

> ⏩ **后续变动（2026-09-21，外部评审 P2-3）：这一节的判断是错的。**
>
> `agent_runs` **不能**证明 Agent 被调用过 —— 因为 **BigA 自己的代码就在写它**
> （`synthesize.py` 按已有 verdict 调 `record_verdict_run()`）。
> 人手工跑一遍合成脚本，这张表照样多出几行。
>
> 第 07 章在端到端实测时就撞到了这件事并记了下来
> （「那行 `agent_runs` 是我手工跑 `synthesize.py` 插进去的」），
> 但**本章与 `schema.py` 的注释都没跟着改** —— 两套口径并存了很久（L-3）。
>
> 真正的 spawn 证明在**运行时自己的库**里（`subagent_runs` / `task_runs`），
> 那是被验证方写不到的地方。读取方是 `tools/verify/agent_trace.py`。
>
> 下面的原文保留不动（过程文档写完即冻结），读的时候请带着这条修正。

```sql
CREATE TABLE agent_runs (
    run_id, decision_id, task_id, agent, model, status, verdict,
    missing_count, elapsed_ms, tokens_in, tokens_out, error,
    started_at, finished_at
);
```

它有两个用途，第二个比第一个重要：

1. 成本与延迟可观测 —— 模型分层要靠它的数据来定（见第 02 章「先记账后优化」）。
2. 🔴 **它是「Supervisor 确实调用了 Specialist」的唯一凭证。**

第二点值得展开。在多 Agent 系统里，Supervisor 的回答里写着
「我调用了 Emotion Agent，它返回情绪分 48」——**这句话本身不能作为证据**。
LLM 完全可以把整段调用过程编出来，而且编得非常像。

区分「真的调用了」和「说自己调用了」的唯一办法，是看**调用方之外的记录**。
所以本项目的验收标准第 1 条写的是：

> Supervisor **确实 spawn 了** `emotion`（`agent_runs` 有该行，不是自己编的）

为了让记账不出错，提供一个直接吃 `AgentVerdict` 的入口：

```python
def record_verdict_run(v: AgentVerdict, *, started_at, finished_at, ...) -> int:
    """从一个 AgentVerdict 直接记账，省得调用方手抄字段（抄错就是口径分裂）。"""
```

手抄字段是口径分裂的经典起点：`missing_count` 抄成了 `len(warnings)`，
没人会发现，而所有基于它的统计从此全错。

### `raw_market_snapshot` —— 原样落盘

```sql
CREATE TABLE raw_market_snapshot (
    snapshot_id, source, as_of, retrieved_at,
    payload_json, content_sha256, created_at
);
```

存的是**当时从数据源拿到的东西**，不做任何归一化。上层算错了可以重算，
raw 丢了就永远重算不了。

一个刻意的决定：**相同内容不去重。**

```python
def test_相同内容不去重(self, db):
    """采了两次就是两个事实，都留着 —— 去重会丢掉「这一刻也采到了」这条信息。"""
```

`content_sha256` 只用来**识别**重复，不用来**消除**重复。
因为「10:00 和 10:05 采到的内容完全相同」本身就是一条有意义的观测
（说明数据源在这 5 分钟里没更新），去重会把它抹掉。

---

## 4. 只追加：用触发器，不用约定

架构里有一条从真实事故里学来的规则：

> **raw 层永不改写**；状态变更一律追加而非 `UPDATE`，让「当时看到的」可重建。

来源是一类很具体的失败：交易记录表被原地 `UPDATE` 更新状态，
结果「下单当时系统看到的是什么」永久不可重建，事后归因直接残废。
这种问题的特点是 —— **出问题的当下毫无症状**，等你需要追溯时才发现追溯不了，
而那时已经太晚。

通常的做法是在规范里写一句「不要 UPDATE 这些表」。本项目不这么做：

```python
def _append_only(table: str, note: str) -> str:
    """生成一对拒绝 UPDATE / DELETE 的触发器。

    用触发器而不是「代码里不写 UPDATE」——
    约定靠人守，触发器靠数据库守。多一个人、多一个脚本都不会绕过它。
    """
    return f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update
BEFORE UPDATE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} 只追加不修改：{note}');
END;

CREATE TRIGGER IF NOT EXISTS {table}_no_delete
BEFORE DELETE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} 只追加不删除：{note}');
END;
"""
```

三张表各一对，共 6 个触发器。效果：

```python
with pytest.raises(AppendOnlyViolation, match="只追加"):
    with connect(db) as c:
        c.execute("UPDATE decision_records SET status='BUY'")
```

**这道防线连 `sqlite3` 命令行都绕不过去**，因为它在数据库里，不在代码里。
一个临时写的修数据脚本、一个手滑的 `UPDATE`、一个三年后不知道这条规则的新人，
都会当场被拒绝。

`connect()` 把 SQLite 的 `IntegrityError` 翻译成语义明确的异常：

```python
except sqlite3.IntegrityError as e:
    conn.rollback()
    if "只追加" in str(e):
        raise AppendOnlyViolation(str(e)) from e
    raise
```

> **通用原则**：一条规则如果能下沉到**比代码更底层的地方**（数据库约束、文件权限、
> 类型系统），就把它放到那里。代码层面的约定，只能约束记得它的人。

---

## 5. 回放不覆盖原始记录：一个 partial index 的用法

回放的定义是「用冻结的证据重跑合成」，用途之一是换个模型对比结论。
那么问题来了：回放产生的新卡存哪？

- 覆盖原记录 → 「换模型后结论变了吗」这个问题永远没法回答，回放就白做了
- 存到另一张表 → 两套 schema，查询要 UNION，回放和在线的字段慢慢就漂了

采用的方案是**同表 + 一个自引用列**：

```sql
replay_of INTEGER REFERENCES decision_records(record_id)   -- 在线路径为 NULL
```

约束用**部分唯一索引**表达：

```sql
CREATE UNIQUE INDEX ux_decision_online
    ON decision_records(decision_id) WHERE replay_of IS NULL;
```

读作：*一个 `decision_id` 只能有一条在线记录，回放记录不限条数。*

一行 DDL 就精确表达了业务规则，不需要在代码里写「保存前先查一下有没有重复」——
那种写法在并发下还有竞态。

```python
def test_回放追加新行且不覆盖原始(self, db):
    rid = save_card(make_card(status="WAIT"), path=db)
    save_card(make_card(status="AVOID", model_ref="anthropic/claude-opus-5"),
              replay_of=rid, path=db)

    assert load_card(TID, path=db).status == "WAIT"        # 在线那条没被动
    assert 数据库里有 2 条记录
```

---

## 6. `readonly=True` 是隔离手段，不是性能优化

```python
@contextmanager
def connect(path=None, *, readonly: bool = False):
    if readonly:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
```

回放、巡检、渲染这些「只该读」的路径统统用它打开。
写操作会被 **SQLite 拒绝**，而不是靠调用方自觉。

这与第 01 章的隔离思路是同一个：**能让错误在物理层被拒绝，就别让它依赖纪律。**

顺带一提，`load_card` / `list_agent_runs` / `load_raw_snapshot` 内部全部用只读连接。
这意味着「读操作意外写了库」这件事在这一层就不可能发生。

---

## 7. 验证

```bash
cd ~/.openclaw-biga/workspace
python3 -m pytest
# → 75 passed
```

确认表和触发器都建出来了：

```bash
python3 -c "
import sys, os, tempfile; sys.path.insert(0,'skills')
p = tempfile.mktemp(suffix='.db')
from _store import init_schema, connect
print('schema version =', init_schema(p))
with connect(p, readonly=True) as c:
    q = lambda t: [r[0] for r in c.execute(
        \"SELECT name FROM sqlite_master WHERE type=? ORDER BY name\", (t,))]
    print('tables   =', q('table'))
    print('triggers =', q('trigger'))
os.remove(p)
"
```

```
schema version = 1
tables   = ['agent_runs', 'decision_records', 'raw_market_snapshot', 'sqlite_sequence']
triggers = ['agent_runs_no_delete', 'agent_runs_no_update',
            'decision_records_no_delete', 'decision_records_no_update',
            'raw_market_snapshot_no_delete', 'raw_market_snapshot_no_update']
```

探针验证扫描真的会红（和第 03 章同样的做法）：

```bash
mkdir -p skills/_scan_probe
printf 'import sqlite3\ndef go():\n    return sqlite3.connect("data/biga.db")\n' \
    > skills/_scan_probe/bad_db.py

python3 -m pytest tests/test_no_raw_sqlite.py
```

```
E   skills/_scan_probe/bad_db.py:1
E   skills/_scan_probe/bad_db.py:3 sqlite3.connect(...)
FAILED tests/test_no_raw_sqlite.py::test_store之外不许import_sqlite3
FAILED tests/test_no_raw_sqlite.py::test_store之外不许调用sqlite3_connect
2 failed, 2 passed
```

```bash
rm -rf skills/_scan_probe    # 恢复全绿
```

---

## 8. 本章要点

| 要点 | 一句话 |
|---|---|
| 临时方案要写退出条件 | 没有可判定信号的「以后再换」就是「永远不换」 |
| 唯一 DB 入口 | 切库时改一个文件 vs 做一次考古 |
| 守卫要拦结果不拦路径 | `import` 检查和 `connect` 调用检查是两条独立的规则 |
| 规则要反向自检 | 断言「`_store/` 里确实有 sqlite3」，否则规则被架空后仍显示绿色 |
| 反范式不危险，分别写入才危险 | 派生列只由唯一写入口从完整对象生成 |
| ~~`agent_runs` 是 Agent 被调用过的唯一凭证~~ ⏩ **已修正，见上** | 它是账本，不是证明 —— 我们自己的代码就在写它 |
| raw 层不去重 | 「这一刻也采到了相同内容」本身是观测 |
| 规则能下沉就下沉 | 触发器连 `sqlite3` 命令行都绕不过，代码约定只能约束记得它的人 |
| 部分唯一索引表达业务规则 | 一行 DDL 顶掉一段有竞态的「先查再写」 |
| 只读连接是隔离手段 | 读路径意外写库这件事，在这一层就不可能发生 |

---

上一章：[03 · 契约层](03-contract-layer.md)　|　下一章：05 · 第一个技能（编写中）
