"""BigA 事实层 schema —— 按版本号递增的迁移列表。

为什么 schema 单独一个模块而不是散在 db.py 里：
切 PostgreSQL 时，需要改的只有「方言相关的 SQL」和「连接逻辑」两处，
把它们分开放，届时一眼能看出改哪些。

🔴 **每一张表都只追加不修改**，由 SQLite 触发器强制（不是靠约定）。
   理由见 architecture.md §9 L-8：状态被原地 UPDATE 之后，
   「当时看到的是什么」就永久不可重建了，事后归因直接残废。

⚠️ 这句话原本写的是「三张表」—— 而 v4 加第五张表时漏了触发器，
   没有任何东西会因此报错（见 _V5 的注释）。所以改成了无须随表数更新的说法，
   并由 `tests/test_store.py::test_每张表都有只追加触发器` 兜底：
   **新表默认就该在清单里，例外必须自己举手。**
"""

from __future__ import annotations

__all__ = ["MIGRATIONS", "SCHEMA_VERSION"]


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


_V1 = """
-- ───────────────────────────────────────────────────────────────
-- decision_records —— Decision Card 冻结存档，回放的唯一真相源
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_records (
    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT    NOT NULL,
    -- 回放产生的记录指向它回放的那条；在线路径为 NULL。
    -- 回放绝不覆盖原始记录 —— 覆盖了就没法回答「换模型后结论变了吗」。
    replay_of     INTEGER REFERENCES decision_records(record_id),
    status        TEXT    NOT NULL,
    headline      TEXT    NOT NULL,
    model_ref     TEXT    NOT NULL,
    missing_count INTEGER NOT NULL,
    -- 🔴 真相源。上面那些列都是从它派生出来的查询用副本，写入时统一由卡对象生成，
    --    调用方无法单独指定，因此不可能与 card_json 不一致。
    card_json     TEXT    NOT NULL,
    generated_at  TEXT    NOT NULL,
    elapsed_ms    INTEGER NOT NULL,
    created_at    TEXT    NOT NULL
);

-- 一个 decision_id 只能有一条「在线」记录，回放记录不限条数。
CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_online
    ON decision_records(decision_id) WHERE replay_of IS NULL;
CREATE INDEX IF NOT EXISTS ix_decision_id ON decision_records(decision_id);
CREATE INDEX IF NOT EXISTS ix_decision_created ON decision_records(created_at);

-- ───────────────────────────────────────────────────────────────
-- agent_runs —— 每次 Agent 执行的**账本**
--
-- 用途：成本与延迟可观测（模型分层要靠它的数据来定，不靠拍脑袋）。
--
-- 🔴 它**不是** spawn 的证明。这一行原本写的是
--    「证明 Supervisor 确实 spawn 了 Specialist，而不是自己编了个答案」——
--    那是错的，外部评审 P2-3 指出的就是它。
--
--    因为 **BigA 自己的代码就在写这张表**：`synthesize.py` 会按已有的
--    verdict 调 `record_verdict_run()`。人手工跑一遍合成脚本，
--    这张表照样多出几行。
--
--    ⇒ 它能证明的只有「我们记下了一次执行」，不能证明「运行时真的起过它」。
--
--    真正的 spawn 证明在**运行时自己的库**里（`subagent_runs` / `task_runs`），
--    那是被验证方写不到的地方。读取方是 `tools/verify/agent_trace.py`
--    与 `latency_report.py`。
--
--    ⚠️ 教程第 7 章早就记下了这个教训（「那行 agent_runs 是我手工插进去的」），
--       但第 4 章与本注释没跟着改 —— 两套口径并存了很久（L-3）。
--
-- （本次只改注释，不改 DDL：追加式那条规则管的是 schema 变更。）
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   TEXT,
    task_id       TEXT    NOT NULL,
    agent         TEXT    NOT NULL,
    model         TEXT,
    status        TEXT    NOT NULL,
    verdict       TEXT,
    missing_count INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER NOT NULL,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    error         TEXT,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_runs_decision ON agent_runs(decision_id);
CREATE INDEX IF NOT EXISTS ix_runs_agent    ON agent_runs(agent, finished_at);

-- ───────────────────────────────────────────────────────────────
-- raw_market_snapshot —— 采集原样落盘，永不改写
--
-- 这里存的是「当时从数据源拿到的字节」，不做任何归一化。
-- 上层算错了可以重算；raw 丢了就永远重算不了。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_market_snapshot (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT    NOT NULL,
    as_of          TEXT    NOT NULL,
    retrieved_at   TEXT    NOT NULL,
    payload_json   TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_raw_source_asof ON raw_market_snapshot(source, as_of);
CREATE INDEX IF NOT EXISTS ix_raw_sha         ON raw_market_snapshot(content_sha256);
""" + _append_only("decision_records", "回放追加新行，不覆盖原始判断") \
    + _append_only("agent_runs", "执行账本改了就不是账本了") \
    + _append_only("raw_market_snapshot", "raw 层永不改写（L-8）")


_V2 = """
-- ───────────────────────────────────────────────────────────────
-- v2：删掉从未被写入的 token 列
--
-- 背景：v1 建表时预留了 tokens_in / tokens_out，想用来做成本核算。
-- 实际上**从建表起就没有任何生产方**（8 行记录，0 行有值）——
-- 这是「零消费方」模式的反面：有人建了列，但没有人写。
--
-- 为什么是删而不是补上写入：
--   1. 真实数据在 OpenClaw 运行时自己的 trajectory 里，那是**唯一真相源**。
--      在这里再存一份，就是第二套口径，且必然滞后。
--   2. synthesize.py 是在 agent 轮次**中途**调用的，那一轮的 token 用量
--      此刻还没结算完，根本写不进来。
--
-- ⇒ 成本核算改为按需读运行时：`tools/verify/latency_report.py`。
-- ⚠️ 代价已知：运行时若清理旧 trajectory，历史成本就没了。
--    等 Phase 3 有了自己的调度域，再加一个对账任务把它固化进来。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE agent_runs DROP COLUMN tokens_in;
ALTER TABLE agent_runs DROP COLUMN tokens_out;
"""


_V3 = """
-- ───────────────────────────────────────────────────────────────
-- v3：agent_verdicts —— Specialist 的判定原件
--
-- 🔴 它解决的不是「多存一份」，而是「**不让 LLM 搬运结构化数据**」。
--
-- 实测（2026-09-20，BIGA-20260920-002）：契约要求 Specialist
-- 「把 skill 的 JSON 原样带上」、Supervisor 再把它抄进 heredoc。两层复述的结果：
--
--   · skill 实际输出 15 条 evidence，每条都有 retrieved_at
--   · Specialist 转述后：as_of 34 条，retrieved_at **0 条**
--   · 落库 Card 上 25 条 evidence 的 retrieved_at 全部是 20:44:34
--     —— 那是 Supervisor 敲命令的时刻，而真实采集时刻是 20:42:48
--
-- 也就是说「事实可追溯」这条地基，在最后一公里被 LLM 的复述打穿了：
-- retrieved_at 是合成时现编的，部分 evidence 条目是从 result 反向重建的。
--
-- 附带代价同样可观：那一轮里有 47 秒零工具调用，纯粹在重打 6460 字符的 JSON；
-- 之后 synthesize.py 因为缺字段失败两次，又花掉 49 秒自救。
--
-- ⇒ skill 写这张表并返回一个 id，agent 只传 id。
--    搬运成本从 O(evidence 数) 变成 O(1)，且数据根本不经过 LLM。
--
-- 修订不覆盖：Specialist 追加缺失项时写**新行**并用 amends 指回原行，
-- 与 decision_records 的 replay_of 是同一套做法（L-8：当时看到的必须可重建）。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_verdicts (
    verdict_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT    NOT NULL,
    agent          TEXT    NOT NULL,
    -- 修订链：本行修订的是哪一行。原始行为 NULL。
    amends         INTEGER REFERENCES agent_verdicts(verdict_id),
    amend_reason   TEXT,
    -- 🔴 真相源。不派生 status/verdict 到单独的列 ——
    --    agent_runs 已经是执行账本，在这里再存一份就是第二套口径（L-3）。
    verdict_json   TEXT    NOT NULL,
    content_sha256 TEXT    NOT NULL,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_verdict_task  ON agent_verdicts(task_id, agent);
CREATE INDEX IF NOT EXISTS ix_verdict_sha   ON agent_verdicts(content_sha256);
CREATE INDEX IF NOT EXISTS ix_verdict_chain ON agent_verdicts(amends);
""" + _append_only("agent_verdicts", "判定原件改了，就没法证明 Card 上的数字来自采集而非复述")


_V4 = """
-- ───────────────────────────────────────────────────────────────
-- v4：decision_ids —— 决策编号的**分配器**
--
-- 🔴 它解决的是一次实测事故：**两次运行的证据被合成进了同一张卡。**
--
-- 2026-09-21 09:37 与 09:39 各起了一次盘中端到端。结果：
--
--   · 每个 specialist 的 verdict 都写着 task_id = BIGA-20260921-001
--   · 而合成出来的卡是 BIGA-20260921-006
--   · 两次运行的 verdict 混在一起，**没有任何字段能把它们分开**
--
-- 两层原因：
--
-- 1. 五个 specialist 都写着 `new_task_id(1)` —— 序号硬编码。
--    这个 bug 在 synthesize.py 上修过一次，**兄弟模块一个没查**。
-- 2. 更根本的：编号原本在**合成时**才分配，而那时证据早就采完了。
--    一个决策在收集证据之前就该有身份，否则证据无处归属。
--
-- ⇒ 编号改为 Stage 0 分配，并且**原子占号**：
--    主键冲突是唯一可靠的并发仲裁，靠「先查再插」必然有竞态窗口。
--
-- 为什么不直接用 decision_records 的 decision_id 占位：
-- 那张表是追加式的、且要求一张完整的卡。占号发生在有卡之前。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS decision_ids (
    decision_id TEXT PRIMARY KEY,
    reserved_at TEXT NOT NULL,
    -- 谁占的号。排查「这个号哪来的」时唯一有用的线索。
    reserved_by TEXT
);
"""


_V5 = """
-- ───────────────────────────────────────────────────────────────
-- v5：给 decision_ids 补上只追加触发器
--
-- 🔴 v4 建这张表时**漏了它** —— 另外四张表都有，唯独分配器没有。
-- 外部评审 F1 指出，探针复现（`tools/verify/probe.sh`）：
--
--   A 占到 BIGA-20260921-001
--   DELETE 成功 —— 1 行
--   B 占到 BIGA-20260921-001      ← 同一个号被发了两次
--
-- 为什么这个洞比它看起来严重：
--
-- FIX-01（卡不能装外来判定）与 FIX-02（risk 核对上游归属）两道防线，
-- 校验的都是「这些判定的 task_id 是不是同一个」。号被回收之后，
-- 两次运行的判定在 id 字段上**真实自洽** —— 两道闸门会一致放行，
-- 而合成出来的卡恰恰就是 v4 要防的那种「两次运行混在一起」。
--
-- ⇒ 身份的前提是**发出去就不能收回**。没有这对触发器，
--    v4 建立的整套决策身份机制建在一个可撤销的地基上。
--
-- 为什么单开 v5 而不是改 _V4：迁移列表已发布的条目不许改动
-- （见 MIGRATIONS 的注释）—— 已经跑过 v4 的库不会重放它。
-- ───────────────────────────────────────────────────────────────
""" + _append_only("decision_ids", "号发出去就不能收回，否则两次运行会共用一个身份")


_V6 = """
-- ───────────────────────────────────────────────────────────────
-- v6：修订链只能线性，不许分叉（设计文档 §6 A8）
--
-- 🔴 它解决的是一个实测能构造出来的洞：`agent_verdicts.amends` 原来只是
-- 一个普通索引（`ix_verdict_chain`），同一个 verdict_id 可以被**两条不同
-- 的修订**同时指向——一条原件分叉出两条历史，读者不知道该信哪条「当前」。
--
-- 「先查这个 verdict_id 有没有被修订过，没有才写」中间有窗口——
-- 和 v4/v5 的 decision_ids 是同一类竞态，唯一可靠的仲裁是数据库自己的
-- 唯一约束，不是应用层的「先查再插」。
--
-- ⇒ 补一条局部唯一索引：非 NULL 的 amends 值不许重复。
--    应用层（`_store.db.save_verdict`）额外校验 `new.task_id == old.task_id`
--    且 `new.agent == old.agent`——那两条数据库管不了，只能代码管。
-- ───────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS ux_verdict_amends_linear
    ON agent_verdicts(amends) WHERE amends IS NOT NULL;
"""


_V7 = """
-- ───────────────────────────────────────────────────────────────
-- v7：运行身份 + 显式状态机（设计文档 §4 / §5）
--
-- 🔴 它解决的是「decision_id 一个身份被迫承担五件事」，实测踩过三次：
--   · 飞书事件重投 = 重跑一次决策（没有 trigger_id 做幂等键）
--   · 硬超时重试的两次尝试挤在同一个 decision_id 上，事后分不开
--   · 「所有 Specialist 看同一份数据」无法验证（没有 evidence_set_id）
--
-- 三张表：
--   decision_runs   —— 一次执行尝试的**不可变身份头**
--   run_events      —— 状态转移日志（事件溯源）。当前状态 = 最新一行的 to_state
--   evidence_sets   —— 冻结数据切片登记（批 D 的 SnapshotCoordinator 填，批 B 只建表）
--
-- 🔴 状态**不在 decision_runs 上原地 UPDATE** —— 那张表是只追加的。
--    当前状态由 run_events 的最新一行给出；transition() 靠
--    UNIQUE(run_id, seq) 做 compare-and-set：并发两次同转移都算 seq=N+1，
--    唯一约束只让一个落地。这与 decision_ids 用主键冲突仲裁占号是同一招 ——
--    「先查再写」永远有竞态窗口，唯一约束没有。
--
-- 🔴 建表时**就**带只追加触发器 —— v4 建 decision_ids 时漏过一次（F1），
--    代价是整套决策身份机制建在可撤销的地基上。
--    tests/test_store.py::test_每张表都有只追加触发器 兜底：新表默认受保护。
--
-- 建表顺序：evidence_sets 先建，decision_runs FK 指向它，run_events FK 指向
-- decision_runs —— 被引用的表先出现，避免前向引用。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence_sets (
    evidence_set_id TEXT PRIMARY KEY,
    decision_id     TEXT,
    frozen_at       TEXT NOT NULL,
    -- 冻结了哪些 raw snapshot（sha 列表等）。批 D 定它的结构，批 B 只建表。
    manifest_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_evsets_decision ON evidence_sets(decision_id);

CREATE TABLE IF NOT EXISTS decision_runs (
    run_id          TEXT PRIMARY KEY,
    -- 属于哪个决策。🔴 可空：legacy 路径（bin/biga-card）在 RECEIVED 时还
    --   不知道号 —— LLM 的 Stage 0 才占号，事后把发现的号记进 run_events.detail。
    --   批 C 的 Orchestrator 在开 run 之前占号，那时它非空。
    decision_id     TEXT,
    -- 一次外部请求的幂等键（飞书 event id / CLI 每次一个 / cron）。
    trigger_id      TEXT    NOT NULL,
    -- 被冻结的数据切片（批 D 填；批 B 恒 NULL）。
    evidence_set_id TEXT    REFERENCES evidence_sets(evidence_set_id),
    origin          TEXT    NOT NULL,
    non_interactive INTEGER NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_runs_decision2 ON decision_runs(decision_id);
CREATE INDEX IF NOT EXISTS ix_runs_trigger   ON decision_runs(trigger_id);

CREATE TABLE IF NOT EXISTS run_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT    NOT NULL REFERENCES decision_runs(run_id),
    -- 每个 run 内单调递增，从 1（进入 RECEIVED）开始。
    seq        INTEGER NOT NULL,
    -- 从哪个状态来。NULL = 初始事件（进入 RECEIVED 之前没有状态）。
    from_state TEXT,
    to_state   TEXT    NOT NULL,
    at         TEXT    NOT NULL,
    -- 转移的附加信息（JSON）：如 legacy 路径发现的 decision_id、失败原因。
    detail     TEXT,
    -- 🔴 CAS 的并发仲裁：同一个 run 的同一个 seq 只能有一行。
    UNIQUE(run_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_events_run ON run_events(run_id, seq);
""" + _append_only("decision_runs", "运行身份的头发出去就不能改，否则两次尝试会串味") \
    + _append_only("run_events", "转移日志改了，就没法复述这次运行走过的路（也就没法做 CAS）") \
    + _append_only("evidence_sets", "冻结切片一旦改写，「所有 Specialist 看同一份数据」就成了空话")


_V8 = """
-- ───────────────────────────────────────────────────────────────
-- v8：agent_verdicts 加 kind，区分「旧 AgentVerdict / 新 FactBundle / 新 AgentAssessment」
--
-- 🔴 批 E-I 把「事实」与「判断」拆开（FactBundle + AgentAssessment）。这两种新形状
--    和旧的合体 AgentVerdict 同住 agent_verdicts 一张表 —— 复用它已有的只追加触发器
--    与线性修订唯一索引（`ux_verdict_amends_linear`），不另起一张表再维护一套同样的
--    约束（那就是第二套要跟着演进的口径，L-3）。
--
-- kind 取值：
--    NULL / 'verdict'  —— 旧合体 AgentVerdict（历史行全是 NULL，读路径靠 LegacyAdapter 拆）
--    'fact'            —— FactBundle（skill 产，无 stance）
--    'assessment'      —— AgentAssessment（Agent 产，只 stance + fact_ref，amends 指回 fact 行）
--
-- 🔴 **判据用列，不用「猜 json 形状」**：按「有没有 stance 键」推断 kind 是 L-13 的形状
--    （判据落在字符串存在性上）。显式一列，读的时候不含糊。
--
-- 为什么不设 NOT NULL DEFAULT：历史行就是没有 kind 的旧 AgentVerdict，NULL 正好如实
--    表达「它是拆分之前的合体形状」，读路径据此走 LegacyAdapter。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE agent_verdicts ADD COLUMN kind TEXT;
"""


#: (版本号, SQL)。只许在末尾追加，不许改动已发布的条目。
MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1),
    (2, _V2),
    (3, _V3),
    (4, _V4),
    (5, _V5),
    (6, _V6),
    (7, _V7),
    (8, _V8),
]

SCHEMA_VERSION: int = MIGRATIONS[-1][0]
