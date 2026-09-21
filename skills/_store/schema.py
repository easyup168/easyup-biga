"""BigA 事实层 schema —— 按版本号递增的迁移列表。

为什么 schema 单独一个模块而不是散在 db.py 里：
切 PostgreSQL 时，需要改的只有「方言相关的 SQL」和「连接逻辑」两处，
把它们分开放，届时一眼能看出改哪些。

🔴 三张表全部**只追加不修改**，由 SQLite 触发器强制（不是靠约定）。
   理由见 architecture.md §9 L-8：状态被原地 UPDATE 之后，
   「当时看到的是什么」就永久不可重建了，事后归因直接残废。
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
-- agent_runs —— 每次 Agent 执行的账本
--
-- 它同时承担两件事：
--   1. 成本与延迟可观测（模型分层要靠它的数据来定，不靠拍脑袋）
--   2. 🔴 证明「Supervisor 确实 spawn 了 Specialist」，而不是自己编了个答案
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


#: (版本号, SQL)。只许在末尾追加，不许改动已发布的条目。
MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1),
    (2, _V2),
    (3, _V3),
    (4, _V4),
]

SCHEMA_VERSION: int = MIGRATIONS[-1][0]
