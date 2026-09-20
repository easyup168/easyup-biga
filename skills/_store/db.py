"""BigA 数据访问层 —— 全仓**唯一**的 DB 入口。

🔴 业务代码里不许出现裸 `sqlite3.connect`。由 tests/test_no_raw_sqlite.py 钉死。

为什么值得为此专门写一条规则：
一旦切换到 PostgreSQL（触发条件写死在 architecture.md §5.1），
需要改的地方只有这一个文件。散落各处的 `sqlite3.connect` 会让「切库」
从一次重构变成一场考古。

同样重要的是：所有写入都在这里做**契约校验**。
Card 只能整个对象地存进来，不能拆成字段分别写 —— 这样派生列不可能与真相源不一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from _contract import AgentVerdict, DecisionCard, now_cn

from .schema import MIGRATIONS, SCHEMA_VERSION

__all__ = [
    "DEFAULT_DB_PATH",
    "connect",
    "db_path",
    "init_schema",
    "save_card",
    "load_card",
    "load_verdicts",
    "next_decision_id",
    "record_agent_run",
    "list_agent_runs",
    "save_raw_snapshot",
    "load_raw_snapshot",
    "AppendOnlyViolation",
]

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "biga.db"


class AppendOnlyViolation(RuntimeError):
    """试图 UPDATE / DELETE 只追加的表。"""


def db_path() -> pathlib.Path:
    """当前库路径。`BIGA_DB_PATH` 可覆盖（测试与回放用）。"""
    return pathlib.Path(os.environ.get("BIGA_DB_PATH") or DEFAULT_DB_PATH)


@contextmanager
def connect(path: pathlib.Path | str | None = None, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """唯一的连接入口。

    Args:
        path: 库文件路径，缺省取 `db_path()`。
        readonly: 以只读模式打开。
            🔴 这个开关存在的理由不是性能，而是**隔离**：回放、巡检这类
            「只该读」的路径用它打开，写操作会在数据库层被拒绝，
            而不是靠调用方自觉。
    """
    p = pathlib.Path(path) if path is not None else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    if readonly:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(p)

    conn.row_factory = sqlite3.Row
    try:
        if not readonly:
            # WAL：读写不互相阻塞。单机单写进程下这是最省事的并发模型。
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        if not readonly:
            conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "只追加" in str(e):
            raise AppendOnlyViolation(str(e)) from e
        raise
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(path: pathlib.Path | str | None = None) -> int:
    """建表 / 升级到最新版本，返回当前 schema 版本。幂等。"""
    with connect(path) as conn:
        cur = conn.execute("PRAGMA user_version")
        current = int(cur.fetchone()[0])
        for version, sql in MIGRATIONS:
            if version > current:
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version={version}")
                current = version
    return current


# ────────────────────────────────────────────────────────── decision_records


def save_card(
    card: DecisionCard,
    *,
    replay_of: int | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """冻结一张 Decision Card，返回 `record_id`。

    只接受完整的 `DecisionCard` 对象 —— 派生列全部由它生成，
    调用方无法单独指定，因此不可能出现「查询列说 WAIT、card_json 说 BUY」。

    Args:
        replay_of: 回放时填被回放记录的 `record_id`；在线路径留空。
    """
    if not isinstance(card, DecisionCard):
        raise TypeError(
            f"save_card 只接受 _contract.DecisionCard，收到 {type(card).__name__}"
        )
    payload = json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True)
    try:
        return _insert_card(card, payload, replay_of, path)
    except sqlite3.IntegrityError as e:
        if "decision_records.decision_id" not in str(e):
            raise
        # 报错要能自解释：否则调用方只能去猜，然后手动查库推序号（实测发生过）
        raise ValueError(
            f"decision_id {card.decision_id!r} 已被占用。"
            f"同一天的第 N 次决策要用不同序号 —— "
            f"用 `_store.next_decision_id()` 自动分配，不要硬编码 001。"
        ) from e


def _insert_card(card: DecisionCard, payload: str, replay_of: int | None,
                 path: pathlib.Path | str | None) -> int:
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO decision_records
               (decision_id, replay_of, status, headline, model_ref,
                missing_count, card_json, generated_at, elapsed_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                card.decision_id,
                replay_of,
                card.status,
                card.headline,
                card.model_ref,
                len(card.missing),
                payload,
                card.generated_at,
                card.elapsed_ms,
                now_cn().isoformat(),
            ),
        )
        return int(cur.lastrowid)


def next_decision_id(
    *, day: str | None = None, path: pathlib.Path | str | None = None
) -> str:
    """分配当天下一个未被占用的 ``BIGA-YYYYMMDD-NNN``。

    🔴 这个函数存在的理由是一个真实的 bug：
    `synthesize.py` 原本写的是 ``new_task_id(1)`` —— 序号硬编码为 1，
    于是**当天第二次决策必然撞主键**（partial unique index 拒绝）。

    症状不是报错退出，而是 Supervisor 每次都去「自救」：
    找库在哪 → 查已有的 decision_id → 自己推下一个 → 重试，
    一轮下来多花一百多秒。**系统看起来能跑，只是每次都在做三倍的功。**

    只看「Card 出来了没有」永远发现不了它 —— 得看耗时分解。
    """
    from _contract import new_task_id, now_cn

    day = day or now_cn().strftime("%Y%m%d")
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT decision_id FROM decision_records WHERE decision_id LIKE ?",
            (f"BIGA-{day}-%",),
        ).fetchall()
    used = set()
    for r in rows:
        tail = r["decision_id"].rsplit("-", 1)[-1]
        if tail.isdigit():
            used.add(int(tail))
    seq = next(i for i in range(1, 1000) if i not in used)
    return new_task_id(seq, day=day)


def load_card(
    decision_id: str,
    *,
    record_id: int | None = None,
    path: pathlib.Path | str | None = None,
) -> DecisionCard | None:
    """取回一张冻结的 Card。缺省取该 decision_id 的**在线**那条（非回放）。"""
    with connect(path, readonly=True) as conn:
        if record_id is not None:
            row = conn.execute(
                "SELECT card_json FROM decision_records WHERE record_id=?", (record_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT card_json FROM decision_records "
                "WHERE decision_id=? AND replay_of IS NULL",
                (decision_id,),
            ).fetchone()
    return DecisionCard.from_dict(json.loads(row["card_json"])) if row else None


def load_verdicts(
    decision_id: str, *, path: pathlib.Path | str | None = None
) -> list[AgentVerdict]:
    """取回某次决策的冻结证据 —— 回放的输入。"""
    card = load_card(decision_id, path=path)
    return list(card.verdicts) if card else []


# ────────────────────────────────────────────────────────── agent_verdicts


def save_verdict(
    v: AgentVerdict,
    *,
    amends: int | None = None,
    amend_reason: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """把一份 `AgentVerdict` 原件落库，返回 `verdict_id`。

    🔴 **这张表存在的意义是：结构化数据不经过 LLM。**

    skill 算完直接写这里并返回一个 id，agent 只传 id。
    在此之前，契约要求 Specialist「把 JSON 原样带上」、Supervisor 再抄一遍 ——
    实测两层复述之后，15 条 evidence 的 `retrieved_at` 一条不剩，
    落库 Card 上的 `retrieved_at` 变成了「Supervisor 敲命令的时刻」。

    Args:
        amends: 本行修订的是哪一行。Specialist 追加缺失项时用 ——
            **写新行，不覆盖原行**（与 `decision_records.replay_of` 同一套做法）。
        amend_reason: 为什么修订。没有它，修订链读起来只是「有两行」。
    """
    if not isinstance(v, AgentVerdict):
        raise TypeError(
            f"save_verdict 只接受 _contract.AgentVerdict，收到 {type(v).__name__}")
    if amends is not None and amend_reason is None:
        # 修订不写理由，三个月后没人知道这一行为什么存在。
        raise ValueError("amends 非空时必须给 amend_reason —— 修订要写为什么")
    blob = json.dumps(v.to_dict(), ensure_ascii=False, sort_keys=True)
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO agent_verdicts
               (task_id, agent, amends, amend_reason,
                verdict_json, content_sha256, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (v.task_id, v.agent, amends, amend_reason, blob,
             hashlib.sha256(blob.encode("utf-8")).hexdigest(),
             now_cn().isoformat()),
        )
        return int(cur.lastrowid)


def load_verdict(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> AgentVerdict | None:
    """按 id 取回判定原件。找不到返回 None —— 由调用方决定这算不算缺失。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT verdict_json FROM agent_verdicts WHERE verdict_id=?",
            (int(verdict_id),),
        ).fetchone()
    return AgentVerdict.from_dict(json.loads(row["verdict_json"])) if row else None


def load_verdict_meta(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """取回一行的元信息（含修订链），不构造契约对象。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT verdict_id, task_id, agent, amends, amend_reason, "
            "content_sha256, created_at FROM agent_verdicts WHERE verdict_id=?",
            (int(verdict_id),),
        ).fetchone()
    return dict(row) if row else None


# ───────────────────────────────────────────────────────────────── agent_runs


def record_agent_run(
    *,
    task_id: str,
    agent: str,
    status: str,
    started_at: str,
    finished_at: str,
    elapsed_ms: int,
    decision_id: str | None = None,
    model: str | None = None,
    verdict: str | None = None,
    missing_count: int = 0,
    error: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """记一次 Agent 执行，返回 `run_id`。

    🔴 这张表是「Supervisor 确实调用了 Specialist」的唯一凭证。
    Agent 在回答里声称自己调用过，不算数 —— LLM 完全可以把整段调用编出来。

    ⚠️ 这里**不记 token 与成本**。那些数据的唯一真相源是 OpenClaw 运行时的
    trajectory，在这里存第二份必然滞后且会产生第二套口径。
    成本核算走 `tools/verify/latency_report.py`（读 `_store.runtime`）。
    另注意 `elapsed_ms` 记的是**技能**耗时，不是 agent 的 LLM 轮次耗时 ——
    做延迟分析要用后者。
    """
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO agent_runs
               (decision_id, task_id, agent, model, status, verdict,
                missing_count, elapsed_ms, error, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, task_id, agent, model, status, verdict, missing_count,
             elapsed_ms, error, started_at, finished_at),
        )
        return int(cur.lastrowid)


def record_verdict_run(
    v: AgentVerdict,
    *,
    started_at: str,
    finished_at: str,
    decision_id: str | None = None,
    model: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """从一个 `AgentVerdict` 直接记账，省得调用方手抄字段（抄错就是口径分裂）。"""
    return record_agent_run(
        task_id=v.task_id, agent=v.agent, status=v.status, verdict=v.verdict,
        missing_count=len(v.missing), elapsed_ms=v.elapsed_ms,
        decision_id=decision_id, model=model,
        started_at=started_at, finished_at=finished_at, path=path,
    )


def list_agent_runs(
    *,
    decision_id: str | None = None,
    agent: str | None = None,
    limit: int = 100,
    path: pathlib.Path | str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM agent_runs"
    where, args = [], []
    if decision_id is not None:
        where.append("decision_id=?")
        args.append(decision_id)
    if agent is not None:
        where.append("agent=?")
        args.append(agent)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY run_id DESC LIMIT ?"
    args.append(limit)
    with connect(path, readonly=True) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ──────────────────────────────────────────────────────── raw_market_snapshot


def payload_sha256(payload: Any) -> str:
    """原始响应的内容哈希 —— **唯一实现**。

    `save_raw_snapshot` 用它算入库的 `content_sha256`，
    采集层用它给 `Evidence.raw_hash` 赋值。两边必须是同一个函数：
    各算各的，某天序列化参数改了一处，`raw_hash` 就再也对不上 raw 层 ——
    而那种失效是静默的（两串 sha 都「看起来正常」）。
    """
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def save_raw_snapshot(
    *,
    source: str,
    as_of: str,
    retrieved_at: str,
    payload: Any,
    path: pathlib.Path | str | None = None,
) -> int:
    """原样落盘一份采集结果，返回 `snapshot_id`。

    不做去重 —— 采了两次就是两个事实，都留着。

    ⚠️ `content_sha256` 是**整个响应体**的哈希，不是「市场数据」的哈希。
    很多接口的响应里带易变字段（服务器编号、请求序号等），
    因此内容相同的两次采集，sha 通常也不同。
    它能回答「这两条记录的原始字节是否完全一样」，
    **不能**回答「这两次采到的市场数据是否一致」—— 后者需要先归一化，
    而归一化规则是各数据源特有的，不属于通用存储层。
    """
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sha = payload_sha256(payload)
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO raw_market_snapshot
               (source, as_of, retrieved_at, payload_json, content_sha256, created_at)
               VALUES (?,?,?,?,?,?)""",
            (source, as_of, retrieved_at, blob, sha, now_cn().isoformat()),
        )
        return int(cur.lastrowid)


def load_raw_snapshot(
    snapshot_id: int, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM raw_market_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d
