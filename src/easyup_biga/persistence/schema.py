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
-- 🔴 哪一列才是「原始字节」（批 I，见 v13 迁移）：
--   · payload_json —— 解析后再 `json.dumps(sort_keys=True)` 的**规范表示**，给消费方
--     回读用（load_raw_snapshot 反序列化回对象）。它是**归一化过的**，不是源字节。
--     （原注释曾写「这里存的是从数据源拿到的字节，不做任何归一化」——sort_keys 就是
--      归一化，那句话对 payload_json 是假的。L-3：注释断言了一件没发生的事。）
--   · raw_text（v13 起）—— 数据源发来的**原始响应文本**，不做归一化。content_sha256
--     基于**这一列**算。v13 之前的行没有它（留 NULL），其 sha 是旧口径，别拿新旧直接比。
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
-- v13：raw_market_snapshot 加 raw_text —— 让 raw 层真的存 raw（批 I / 数据架构 §9）
--
-- 🔴 它修的是证据链**最底层**的一个谎：raw 层此前存的不是 raw。链路是
--    get_json() → json.loads → json.dumps(sort_keys=True) 落盘，content_sha256
--    因此是**我们自己重排后**的指纹，证明不了数据源发来的字节 —— 键序 / 空白 /
--    浮点表示 / 原始编码全丢，上游改序列化而没改数据也看不见。对一个卖点是
--    「证据可追溯、可回放」的系统，这是最不能含糊的一处。
--
-- 🔴 **新增列，不是替换 payload_json**（这条边界是设计探活点名的坑）：
--    load_raw_snapshot() 返回的 payload 必须继续是**解析后的对象** ——
--    _snapshot/coordinator.py 的 read_index_daily 对它做 len()/切片。若把
--    payload_json 的语义直接换成原始文本，那个消费方会拿到 str，len() 数的是
--    字符数不是 K 线根数，且不报错 —— 正是本仓库最想防的「看起来正常、其实错了」。
--    ⇒ payload_json 原样保留（回读用），原始文本另存 raw_text，content_sha256
--    改成基于 raw_text 算（db.raw_text_sha256）。
--
-- 为什么可空、为什么不回填：raw 层只追加（L-8）。v13 之前落的行本来就没有原始
--    文本可填（那段文本在 get_json 内部早被丢弃了，重建不出来），硬回填只能编。
--    所以 raw_text 可空，旧行留 NULL、其 content_sha256 保持旧口径（payload_sha256）
--    —— 新行语义变了、旧行不受影响，是 schema 演进的标准形状。新行由
--    save_raw_snapshot 强制非空（漏传当场报错），不给「静默存个空值」留缝（探针 P4）。
--
-- ⚠️ ADD COLUMN 不触发行 UPDATE，也不动 raw_market_snapshot 已有的只追加触发器
--    —— 加完这一列，UPDATE/DELETE 仍然被拒（探针 P5）。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE raw_market_snapshot ADD COLUMN raw_text TEXT;
"""


_V14 = """
-- v14：入站幂等 —— decision_ids 的号绑一次外部请求（批 G-II，Inbound Trigger）
--
-- ⚠️ **schema 版本号撞车，已解决（21 会话并行）**：本迁移开工时 v13 是下一个空号，
--    但并行的批 I（RawArtifact，raw_market_snapshot 加 raw_text）也占了 v13。批 I 先
--    合进 orchestration ⇒ 按既有先例（F/G-I 的 v11、J 的 v9/v10）「谁先落地谁保留
--    编号」：批 I 保住 v13，本迁移改占 **v14**，迁移体一字未动。两条迁移各自独立
--    （一个动 raw 层、一个动号分配器），无先后依赖。
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


_V15 = """
-- v15：fact_trading_calendar —— 这个仓库**第一张真正的 fact_* 表**（批 L）
--
-- 🔴 它落地的是 architecture.md §5.2 画了很久的「raw → fact → derived」分层图里
--    的**中间那层**：在此之前十四张表里，raw 层（raw_market_snapshot）是唯一被真正
--    实例化过的一层，「归一化事实层」只存在于文档。批 L 用一个**非行情、体量小、
--    判据清楚**的数据集（交易日历）把这一层第一次做成真实 schema —— 既补上
--    market_is_open() 长期「不认节假日」的缺陷，也给 §45「第一版完整市场数据」那一批
--    打样（那一批的 security_master / adjustment_factors / EOD bars 都要走同一条
--    Provider → Raw → Normalize → Quality → Snapshot 链）。
--
-- 一行 = 某个自然日开不开市。归一化自深交所官方 monthList（jyrq/jybz），每月每天
--    一行（含休市日 is_open=0），所以「某日有没有行」= 「这个月抓没抓过」——
--    读的一方据此区分「已知休市」(is_open=0) 与「日历没覆盖到」(查无此行 ⇒ 回退
--    weekday 判据)。
--
-- 🔴 只追加，历史事实一旦落地不 UPDATE：交易所若事后补发调整（临时增/删一个交易
--    日），用**更晚 retrieved_at 的新行**表达修正，读的一方按 retrieved_at 取最新一条
--    （is_trading_day 的 ORDER BY retrieved_at DESC）。覆盖旧行就没法回答「我们当时
--    看到的日历是什么」—— 与 raw 层同一条 L-8 先例。接 _append_only()，且
--    test_每张表都有只追加触发器 会自动把这张新表也扫进去（新表默认受保护）。
--
-- snapshot_id 指回它归一化自哪一份 raw（raw_market_snapshot.snapshot_id）：事实能
--    一路溯回数据源发来的原始字节，正是「证据可追溯」这条卖点在 fact 层的体现。
-- ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fact_trading_calendar (
    fact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT    NOT NULL,   -- YYYYMMDD（与 DailyBar.day 同口径）
    is_open       INTEGER NOT NULL,   -- 1 开市 / 0 休市
    source        TEXT    NOT NULL,   -- 归一化自哪个源，如 szse:calendar/2026-09
    as_of         TEXT    NOT NULL,   -- 这份日历描述的时刻
    retrieved_at  TEXT    NOT NULL,   -- 取回时刻 —— 读最新一条按它排序
    snapshot_id   INTEGER REFERENCES raw_market_snapshot(snapshot_id),  -- 溯源到 raw
    created_at    TEXT    NOT NULL,
    CHECK (is_open IN (0, 1))
);

CREATE INDEX IF NOT EXISTS ix_cal_date ON fact_trading_calendar(trade_date);
""" + _append_only(
    "fact_trading_calendar",
    "历史日历一旦落地不覆盖；交易所补发调整用更晚 retrieved_at 的新行表达")



_V16 = """
-- ───────────────────────────────────────────────────────────────
-- v16：Run Provenance —— 把「这张卡/这条账本属于哪次执行尝试」变成可查询的列（批 N）
--
-- 外部评审 B 节（Run Provenance Closure）§7-9 的三条断言，实测全部成立：
--   · `decision_records` 完全没有 run_id / evidence_set_id 两列 —— 一张卡属于
--     哪次执行尝试、看的是哪份冻结切片，只能把 card_json 解开来读，SQL 答不了。
--   · `agent_runs` 有 runtime_run_id（OpenClaw 的 spawn id），却没有反向指回
--     **BigA 自己**那次编排的列 ⇒ 「某次 BigA Run 启动了哪些运行时 Run」这个
--     问题没有结构化答案，只能按 decision_id 做文本匹配（而一个 decision 可以
--     有多个 run —— 那正是 B 节存在的理由）。
--   · `evidence_sets.run_id` 可空且无唯一约束 ⇒ 「一个 Run 只绑一套 EvidenceSet」
--     这句话没有任何东西在守。
--
-- 🔴 三列一律 nullable、不回填（同 v10 的立场，L-8）
-- ------------------------------------------------------------------
-- 评审原文写的是 `NOT NULL`。**实测生产库后没有照抄**：
--     decision_records   43 张在线卡，只有 6 张的 card_json 带 run_id
--     agent_runs         166 行，只有 36 行有 runtime_run_id
--     evidence_sets      7 行，1 行 run_id 为空
-- 这些行是迁移之前落的，它们**确实不知道**自己属于哪次 run。NOT NULL 会让迁移
-- 当场失败；先回填再加约束则是给历史数据编一个当时并不存在的答案 —— 两条都比
-- 「NULL 如实表达不知道」更糟。
-- ⇒ 列可空，**必填由写边界强制**（`save_card_with_notifications` 的在线分支），
--   档位与 `_check_identity` 那条三段式完全一致：读可以宽，写必须严。
--
-- 🔴 evidence_sets 的唯一约束做成**分区**索引
-- ------------------------------------------------------------------
-- `UNIQUE(run_id)` 会把 7 行里那 1 行 NULL 一并纳入 —— SQLite 里多行 NULL 不算
-- 重复，所以不会立刻失败，但语义是错的：它声称「没有 run_id 的切片也受唯一约束」，
-- 而那恰恰是唯一不该被约束的一类。加 `WHERE run_id IS NOT NULL` 说的才是评审真正
-- 要的那句话：**每个真实的 Run 至多一套 EvidenceSet**。
-- ⚠️ 加索引前实测过生产库：`run_id IS NOT NULL` 的 6 行里没有任何重复
--    （同 v11 的先例 —— CREATE UNIQUE INDEX 遇存量重复会直接报错，必须先确认干净）。
--
-- 🔴 为什么叫 orchestration_run_id 而不是 run_id
-- ------------------------------------------------------------------
-- `agent_runs` 里已经有一个 runtime_run_id（OpenClaw 运行时的 spawn id），那是
-- **另一个命名空间**（J-II 收敛过一次「三个不同东西共用 run_id」）。再往同一张表
-- 里塞一个叫 run_id 的列，等于把刚收敛掉的歧义原样请回来。评审给的名字正好把
-- 「谁的 run」写进了列名，照用。
--    decision_runs.run_id → agent_runs.orchestration_run_id → agent_runs.runtime_run_id
-- `tests/test_store.py::TestRunIdNamespace` 的判据同步扩到这个名字（值仍必须追得到
-- decision_runs.run_id）；runtime_run_id **不**纳入 —— 它本来就是别人的 id。
-- ───────────────────────────────────────────────────────────────
ALTER TABLE decision_records ADD COLUMN run_id          TEXT;
ALTER TABLE decision_records ADD COLUMN evidence_set_id TEXT;
ALTER TABLE agent_runs       ADD COLUMN orchestration_run_id TEXT;

-- 一个 Run 至多一套 EvidenceSet（评审 §9「Every Run has exactly one EvidenceSet」
-- 的「至多」那一半；「至少」那一半由写边界要求在线卡必填 evidence_set_id 来保证）。
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_set_per_run
    ON evidence_sets(run_id) WHERE run_id IS NOT NULL;
"""



_V17 = """
-- ───────────────────────────────────────────────────────────────
-- v17：Fact 唯一约束按 **run** 分区（批 O，外部评审 B-2 / B-3）
--
-- v11 的 `ux_fact_per_task_agent` 说的是「一个 (task_id, agent) 至多一份 fact」。
-- 当时这句话是对的：一个 decision 只会有一个 run，两者等价。
--
-- 🔴 批 M 的 C-1（Trigger 失败终态可重新拉起）**造出了第二个 run**，等价关系断了。
--    评审 §6.3 提前写出了后果：
--
--      > 同一个 Decision 的第二个 Run 无法保存同一 Agent 的新 Fact
--      > ⇒ 读取时可能混入旧 Run，**写入时又可能阻止新 Run 正常产出**
--
--    真实 PoC 复现过：第二轮五个 Specialist 全部撞这条索引落不下 fact，而编排器
--    不会因此停 —— 它拿到上一轮遗留的旧 verdict_ref，用几小时前的证据合成一张
--    `generated_at` 是现在的卡。批 N 在卡这一层加了血缘核对把它拦成硬失败；
--    本批从根上让「第二个 run 写自己的 fact」**本来就合法**。
--
-- ⇒ 一份 fact 的身份是 `(run_id, agent)`，不是 `(task_id, agent)`。
--
-- 🔴 为什么是**两条分区索引**而不是一条
-- ------------------------------------------------------------------
-- 迁移前的 fact 行里有一批 `run_id IS NULL`（实测生产库 2 条）—— 手工跑 skill 落的、
-- 以及 v10 之前落的。对它们，`(run_id, agent)` 退化成 `(NULL, agent)`，而 SQLite 里
-- 多行 NULL 不算重复 ⇒ **这批历史行会完全失去唯一约束保护**，v11 堵上的那个洞
-- （同一 (task_id, agent) 静默产生两份并存原件）会对它们重新打开。
-- ⇒ 新数据按 run 管，历史数据继续按 task 管，两条各自带 WHERE，互不重叠：
--      run_id IS NOT NULL  →  UNIQUE(run_id, agent)
--      run_id IS NULL      →  UNIQUE(task_id, agent)
-- 这正是评审 §6.4 给的方案。
--
-- ⚠️ 加索引前实测生产库（同 v11/v16 的先例 —— CREATE UNIQUE INDEX 遇存量重复直接报错）：
--      kind='fact' 行：run_id 非空 42 条 / 为空 2 条
--      (run_id, agent) 重复组：0
--      run_id 为空那批里 (task_id, agent) 重复组：0
--    两条索引都建得起来。
--
-- 🔴 DROP 掉 v11 那条不是「改已发布的迁移」
-- ------------------------------------------------------------------
-- v11 的迁移体一字未动（只许在末尾追加那条规矩没破）。这里是**后续版本**用
-- DROP + CREATE 表达一次约束演进 —— 与「回头去改 _V11 的 SQL」是两回事：
-- 前者留下完整的演进史（谁在哪一版把它换掉了、为什么），后者会让已经迁移过的
-- 库和新建的库长得不一样。
-- ⚠️ 留着不 DROP 是**错的**：它按 (task_id, agent) 管全部 fact 行，会继续拦住
--    第二个 run 的合法写入 —— 那就等于这一批什么都没做。
-- ───────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS ux_fact_per_task_agent;

CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_per_run_agent
    ON agent_verdicts(run_id, agent) WHERE kind = 'fact' AND run_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_legacy_fact_per_task_agent
    ON agent_verdicts(task_id, agent) WHERE kind = 'fact' AND run_id IS NULL;
"""


# v18: 一个 run 至多一个 EvidenceSet（P1-1）。
# WHERE run_id IS NOT NULL —— 历史行 run_id 为 NULL，不受约束，保持向后兼容。
#
# ⚠️ **这一条是多余的，v20 把它撤了。** 见 `_V20` 的说明。
# 已发布的迁移条目不许改动，所以它留在这里原样执行一次，再由 v20 删掉。
_V18 = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_sets_run
    ON evidence_sets(run_id)
    WHERE run_id IS NOT NULL;
"""


# v19: agent_runs 加 provenance_mode，区分在线执行行与历史/回放行（P1-2）。
# NULL = 历史行（迁移前写入）；'online' = 真实编排执行；将来可扩展 'replay'。
# ALTER TABLE 在 SQLite 里安全：只加列，不改存量行。
_V19 = """
ALTER TABLE agent_runs ADD COLUMN provenance_mode TEXT;
"""


# v20: 撤掉 v18 —— 它与 v16 的 `ux_evidence_set_per_run` 是**逐字相同的同一条索引**。
#
# ```sql
# v16  CREATE UNIQUE INDEX ux_evidence_set_per_run ON evidence_sets(run_id) WHERE run_id IS NOT NULL;
# v18  CREATE UNIQUE INDEX ux_evidence_sets_run    ON evidence_sets(run_id) WHERE run_id IS NOT NULL;
# ```
#
# 🔴 为什么值得再升一版去删它，而不是留着不管
# ------------------------------------------------
# 不是为了那点写放大（`evidence_sets` 一次决策才一行）。是因为
# **一条不变量有了两个名字**，而这正是 L-3 的形状：
# 将来有人要改「一个 run 能不能有两套切片」这条规则时，改掉其中一个，
# 剩下那个仍然在默默拦着 —— 于是「改了但没生效」，而且不报错。
#
# 成因值得记下来：v18 是照抄评审 §6.4 的建议索引加的，**没有先查这条不变量
# 是不是已经有人在守**。评审给的是形状，不是「你缺这个」。
# ⇒ 由 `tests/test_run_provenance.py::test_一个run至多一个切片的约束只应有一条` 钉住。
#
# ⚠️ 撤的是 v18 那个名字，不是这条不变量本身 —— v16 的索引仍在，约束不变。
_V20 = """
DROP INDEX IF EXISTS ux_evidence_sets_run;
"""


# v21: 一次 run 里一个 agent 至多一条**在线**执行账本行（评审 2026092502 §5.5）。
#
# 🔴 它拦的是什么
# ----------------
# 同一个 (run, agent) 有两条 online 行时，一条真的 runtime_run_id 会把另一条
# 伪造的**盖住** —— 上一版核验用 `any(rid in runtime_ids)`，两条里有一条 join
# 得上就判 PASS。核验侧已经改成逐行严格（`verify_agent_rows`），这条索引是
# 另一半：让那种状态**根本写不进来**。
#
# ⚠️ 两道一起上不是重复：索引挡新写入，核验挡**索引之前就存在的老行**
#    （实测生产库 184 行 provenance_mode 全为 NULL，不受这条索引约束）。
#
# WHERE provenance_mode = 'online' —— 只管在线行。历史行（NULL）不受约束，
# 否则这条迁移会在任何一个老库上直接失败。
#
# 🔴 将来若支持 Retry（同一 run 重跑同一个 agent），**不要放宽这条索引**，
#    而是加 `attempt_no` 列、把唯一键扩成 (run, agent, attempt_no)。
#    评审原话：不要让不可区分的多条在线行共存 —— 不可区分正是问题本身。
_V21 = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_online_agent_run_once
    ON agent_runs(orchestration_run_id, agent)
    WHERE provenance_mode = 'online';
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
    (14, _V14),
    (15, _V15),
    (16, _V16),
    (17, _V17),
    (18, _V18),
    (19, _V19),
    (20, _V20),
    (21, _V21),
]

SCHEMA_VERSION: int = MIGRATIONS[-1][0]
