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


_V9 = """
-- ───────────────────────────────────────────────────────────────
-- v9：收敛 `run_id` 三同名（批 J-II）
--
-- 🔴 §4 实测：`run_id` 这个字面量在仓库里同时指三个互不相同的东西 ——
--    编排的执行尝试（decision_runs.run_id，32 位 hex）、这张表的账本行号
--    （INTEGER 自增）、以及运行时返回的 spawn id（SpawnHandle）。三个共用一个
--    名字，任何一条 join / 报表都会拿到「语义正确但指向错误」的数字且不报错。
--    这与 v4「decision_id 被迫承担五件事」是同一个病的反面。
--
-- 这一批把后两个从 `run_id` 里搬走。分两半、同一条迁移，因为都动 agent_runs 这
-- 一张只追加表 —— 拆开就是对同一张表连开两刀。
--
-- ① agent_runs.run_id → ledger_id
--    它从 Phase 1 起就是 INTEGER 自增账本行号，与编排的 run_id 毫无关系。
--    实测全仓没有任何代码读这一列的**值**（唯一的引用是 list_agent_runs 的
--    ORDER BY，随迁移一并改名）⇒ 现在改是免费的，等它有了第一个真实读取方就不是。
--    🔴 不改 _V1 的建表语句：全新库先按 v1 建出 run_id，再由这条改名，是对的。
--    ⚠️ RENAME COLUMN 会自动改写引用该列的触发器体；agent_runs 上的两个只追加
--       触发器**不引用**任何列（RAISE 常量串），因此不受影响 —— 但要真跑 SQL 验
--       （见 tests/test_store.py 的 J-II 探针），名字还在而触发器体被改坏正是
--       RENAME COLUMN 可能的静默失败形状。
--
-- ② 加 runtime_run_id TEXT（nullable）
--    落 SpawnHandle 里那个「运行时返回的真实 spawn id」（OpenClaw
--    subagent_runs.run_id 的 UUID）。它有一个现成的、已在生产路径上的消费方：
--    tools/verify/spawn_check.py 原先靠 payload_json LIKE '%<决策号>%' 文本匹配
--    认 spawn（F3 残留），有了这一列就能升级成结构化 join。
--    · nullable：历史行没有它，NULL 如实表达「迁移前落的账，不知道 spawn id」。
--    · 不设 NOT NULL / 外键：subagent_runs 在**另一个库**（运行时的），外键建不了；
--      回放路径不重新执行 agent，也不该有真实 spawn id。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE agent_runs RENAME COLUMN run_id TO ledger_id;
ALTER TABLE agent_runs ADD COLUMN runtime_run_id TEXT;
"""


_V10 = """
-- ───────────────────────────────────────────────────────────────
-- v10：run_id capture 贯穿全链（批 J-I）
--
-- 让「这条判定原件 / 这份冻结切片是哪次 run 产生的」能被查到 —— §4 身份模型里
-- run_id 从批 B 就存在（decision_runs / run_events），但 agent_verdicts /
-- evidence_sets 一直没有它，于是「所有 Specialist 看的是同一份数据、都属于同一次
-- 执行尝试」这句话在这两张表上无法验证。
--
-- 🔴 **只做 capture，不做 enforce**（设计文档 §2 追加 5.1）：只是把 run_id 存下来、
--    传下去，**不改任何现有的判定/过滤逻辑**。latest_verdict_ids() 仍按 decision_id
--    聚合 —— 按 run_id 过滤要等真正的重试路径出现才做。
--
-- · agent_verdicts.run_id：fact 行由 skill 经 --run-id 带下来；assessment 行**不**
--   自己传，而是从它 amends 的那条 fact 行**继承**（save_assessment 里做）——
--   判据别建在 Agent 手传的可篡改输入上，继承在结构上不可能与事实行不一致。
-- · evidence_sets.run_id：SnapshotCoordinator.freeze_index_daily 冻结时由编排器
--   把 ctx.run_id 直接传进来（Python 内部调用，不经 CLI）。decision_runs 早就有
--   evidence_set_id 反向指针，但那是 run→set；这一列是 set→run，直接、不用 join。
--
-- 🔴 都 nullable、不回填：历史行没有这个值，NULL 如实表达「迁移前落的，不知道是
--    哪次 run」。做成 NOT NULL 等于强迫历史数据造假。
-- ⚠️ J-II 的 TestRunIdNamespace 守卫会自动把这两列纳入判据（任何名为 run_id 的列
--    必须 TEXT、值可追到 decision_runs.run_id）—— 这里声明 TEXT 正是为了过它。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE agent_verdicts ADD COLUMN run_id TEXT;
ALTER TABLE evidence_sets  ADD COLUMN run_id TEXT;
"""


_V11 = """
-- ───────────────────────────────────────────────────────────────
-- v11：一个 (task_id, agent) 至多一份 fact 原件（批 F）
--
-- 🔴 它堵的是一个实测能构造出来的静默洞：`save_fact_bundle` 落 fact 行时
--    `amends` 恒为 NULL，而 v6 的 `ux_verdict_amends_linear` 只管
--    `WHERE amends IS NOT NULL` —— fact 行天生在它管辖之外。于是对同一个
--    `(task_id, agent)` **第二次**写 fact，两行都是 amends=NULL，唯一索引
--    一个都拦不住 ⇒ 静默产生两条并存的判定原件。
--
-- 批 F 把 risk 的事实从「risk 被 spawn 后自己跑 risk_check.py」挪成「编排器
-- 在 spawn 之前直接算好、落库」。这条路径下，如果 risk 没听新提示词、又自己
-- 跑了一遍 risk_check.py --task-id <同一个决策号>，就正好触发上面那个双写：
--   · `latest_verdict_ids()` 取 MAX(verdict_id) ⇒ 悄悄改用 risk 双跑那条，
--     编排器预先算的那条被架空；
--   · 「这次决策的 risk 事实原件是哪一条」从此有歧义，而这正是一个卖点为
--     「证据可追溯、可回放」的系统最不能有的东西。
--
-- ⇒ 补一条分区唯一索引：kind='fact' 的行里，(task_id, agent) 不许重复。
--    与 `ux_decision_online`（一个 decision 一条在线卡）、`ux_verdict_amends_linear`
--    （一条原件至多一条修订）同形 —— 都是「唯一约束由数据库兜底，不靠应用层
--    先查再插」。assessment 行（kind='assessment'）与历史合体行（kind NULL/'verdict'）
--    不在 WHERE 内，不受影响：一份事实仍可挂一个判断，历史行照旧只读。
--
-- 🔴 加索引前实测过生产库：kind='fact' 的行里没有任何 (task_id, agent) 重复
--    （唯一 1 条 fact 行）—— 迁移不会因存量重复而失败。CREATE UNIQUE INDEX
--    在有重复时会直接报错，那正是 append-only 下不能事后清洗的处境，所以必须
--    先确认干净再加。
-- ───────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_per_task_agent
    ON agent_verdicts(task_id, agent) WHERE kind = 'fact';
"""


_V12 = """
-- ───────────────────────────────────────────────────────────────
-- v12：外发通知 outbox + 投递日志（批 G-I，第 9/10 张表）
--
-- 🔴 它解决的是「人得守着终端等卡跑完」——出卡是一次 170~200 秒的同步调用，
--    Card 完成 / UNKNOWN / risk 否决 / 运行失败这四类事件此前没有任何外发通道。
--    这一批只做**推**（Outbound Only）：与 Card 同事务入队一行，另一个 worker
--    异步投递。**不接受任何飞书方向的输入**（那是批 G-II）。
--
-- 两张表，为什么不是一张：
--   notification_outbox      —— 「该推哪件事」的队列。一件事一行。
--   notification_deliveries  —— 「投递尝试」的追加日志。一次尝试一行。
--
-- 🔴 **纯只追加，不给 outbox 开 delivered_at 的 UPDATE 例外**（分发提示词点名要
--    答的设计问题）。两条路都想过：
--
--    (A) 采用 · outbox 只追加，投递状态另开 deliveries 追加日志，「投没投成」
--        变成一条派生查询（deliveries 里有没有这条 outbox 的 status='delivered' 行）。
--    (B) 放弃 · 给 outbox 开 delivered_at 一列、投递成功 UPDATE 它一次。
--
--    选 (A)。理由不是「少写一张表更麻烦也要忍」，是本仓库的**投递状态本来就该
--    这么记**：
--      · run_events 就是这个先例 —— run 的「当前状态」不在 decision_runs 上原地
--        UPDATE，而是 run_events 追加一行、当前状态 = 最新一行。投递状态是同一
--        形状的小状态机（pending → delivered/failed → 重试再 failed…），append 日志
--        天然记得下「第几次、结果如何、什么时候」，UPDATE 一列只留得下终值。
--      · agent_verdicts 的修订用 amends 指回原件、decision_records 的回放用
--        replay_of 指回原卡 —— 本仓库**从不原地改状态**（L-8：状态一 UPDATE，
--        「当时看到的」就永久重建不出来了）。给 outbox 破这个例，就得同时给
--        `tests/test_store.py::test_每张表都有只追加触发器` 开一个它看不见的口子
--        （schema.py 顶部原话：「例外必须自己举手」）——拿一道有用的守卫换一列
--        方便，不划算。
--    ⇒ (A) 与既有先例一致，(B) 会引入本仓库第一处「被允许 UPDATE 的业务表」。
--
-- 幂等键 UNIQUE(event_type, aggregate)：同一个决策的同一类事件只能入队一次。
--   aggregate 对 card_* 是 decision_id（一个决策一张卡 ⇒ 一类事件一次），对
--   run_failed 是 run_id（一次执行尝试至多失败一次，终态 CAS 保证）。幂等在
--   **BigA 自己的库里**就成立，不依赖「飞书那边也会去重」（分发提示词的硬约束）。
--   入队走 INSERT ... ON CONFLICT DO NOTHING（见 db.enqueue_notification）——
--   重复入队是无害的 no-op，不会撞 append-only 触发器（那对触发器管的是
--   UPDATE/DELETE，不管 INSERT 的 UNIQUE 冲突）。
--
-- 🔴 建表时**就**带只追加触发器 —— v4 建 decision_ids 时漏过一次（F1），
--    代价是整套身份机制建在可撤销的地基上。两张新表都进 _append_only。
--
-- ⚠️ **schema 版本号撞车，已解决（2026-09-23）**：本迁移开工时（HEAD=1824361）
--    v11 是下一个空号，但并行的批 F（当时未合并、在另一棵工作树上）也占用了
--    v11（`ux_fact_per_task_agent`）。批 F 先合并（`e5b959f`）落地 v11 —— 合并
--    orchestration 进本批工作树时按 J-I/J-II 的先例重新编号：本迁移改占 v12，
--    迁移体本身一字未动。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notification_outbox (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    -- card_completed / card_unknown / risk_block / run_failed（白名单在
    -- _contract.NOTIFICATION_EVENT_TYPES；db.enqueue_notification 落库前校验）。
    event_type   TEXT    NOT NULL,
    -- 幂等范围：card_* 是 decision_id，run_failed 是 run_id。
    aggregate    TEXT    NOT NULL,
    -- 投递载荷（严格 JSON，_canonical_dumps）。含决策号/run_id 等，worker 原样投出。
    payload_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    -- 🔴 幂等键：同一个决策的同一类事件只能入队一次。
    UNIQUE(event_type, aggregate)
);

CREATE INDEX IF NOT EXISTS ix_outbox_event ON notification_outbox(event_type, aggregate);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id   INTEGER NOT NULL REFERENCES notification_outbox(outbox_id),
    -- 第几次投递（从 1 起）。= 这条 outbox 已有的 deliveries 行数 + 1。
    attempt     INTEGER NOT NULL,
    -- 'delivered' / 'failed'。「投没投成」= 有没有一条 status='delivered' 的行（派生）。
    status      TEXT    NOT NULL,
    -- 投递接口名（桩实现是 'stdout'；批 G-II 才有真飞书 adapter）。
    channel     TEXT    NOT NULL,
    -- 失败时的错误文本；成功为 NULL。
    error       TEXT,
    at          TEXT    NOT NULL,
    UNIQUE(outbox_id, attempt)
);

CREATE INDEX IF NOT EXISTS ix_deliveries_outbox ON notification_deliveries(outbox_id);
""" + _append_only("notification_outbox", "外发事件入队即事实，改了就说不清到底该不该推") \
    + _append_only("notification_deliveries", "投递日志改了，就没法复述这条通知投了几次、结果如何")


_V13 = """
-- ───────────────────────────────────────────────────────────────
-- v13：入站幂等 —— decision_ids 的号绑一次外部请求（批 G-II，Inbound Trigger）
--
-- 🔴 它解决的是 2026-09-21 19:31 那次事故的直接根子：飞书事件会**重投**，
--    而没有幂等键时，一次重投 = 重跑一次决策（4 spawn + yield + 占号在后，$0.4 白花）。
--    §4 身份模型早把 trigger_id 列为「一次外部请求（飞书 event id / CLI / cron）」，
--    decision_runs.trigger_id（v7）也一直留着这一列 —— 但至今零生产方在填它，更没有
--    任何东西保证「同一个 trigger 只起一次决策」。批 G-II 第一次真的填它，并把
--    「一个 trigger 至多一个决策」变成**数据库自己**保证的不变量。
--
-- 为什么绑在 decision_ids（号分配器）而不是新开一张入站幂等表：
--   · 设计探活（2026-09-23）点名：「不要另造一个入站幂等表，decision_runs.trigger_id
--     这一列已经在等着被用」。这里更进一步 —— 号分配器 decision_ids 本就是**决策身份
--     的原子仲裁点**（reserve_decision_id 靠主键冲突占号，见 db.py）。把「这个号是为哪
--     次外部请求占的」记在同一处，幂等就与占号**同一个原子操作**，不引第二套。
--   · 身份模型是 Trigger → Decision → 多个 Run。「一个 trigger 一个决策」正是这条链的
--     第一段；**重试（Run B）复用同一个 decision_id，不重新占号 ⇒ 不撞这条唯一约束**。
--     所以约束加在 decision_ids（每个决策一行）而不是 decision_runs（每次尝试一行）——
--     加在后者会误伤将来的重试。
--
-- 🔴 唯一约束是唯一可靠的并发仲裁（decision_runs.py / decision_ids 反复用的同一招）：
--    「先查 trigger 在不在、不在就占号」中间有窗口，两次同时到的重投会各占一个号、
--    合出两张卡。UNIQUE(trigger_id) 让第二个并发占号在数据库层当场撞掉，
--    reserve_decision_for_trigger 据此回退到「返回已占的那个号」（见 db.py）。
--
-- 只追加不冲突：decision_ids 是只追加的（v5 触发器）。trigger_id 在 **INSERT 时**
--   一次写定，从不 UPDATE —— ADD COLUMN 是 DDL，不触发 no_update；老行 trigger_id
--   留 NULL（它们是 trigger_id 存在之前占的号，本就没有对应的外部请求）。
--   partial index `WHERE trigger_id IS NOT NULL`：CLI 每次生成的是唯一 trigger_id
--   （不去重、各是一次独立请求），NULL 老行与彼此都不进唯一域，只有真正重复的
--   外部 event id 才会撞 —— 正是要拦的那一种。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE decision_ids ADD COLUMN trigger_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_decision_ids_trigger
    ON decision_ids(trigger_id) WHERE trigger_id IS NOT NULL;
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
    (9, _V9),
    (10, _V10),
    (11, _V11),
    (12, _V12),
    (13, _V13),
]

SCHEMA_VERSION: int = MIGRATIONS[-1][0]
