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

import dataclasses
import hashlib
import json
import os
import pathlib
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from typing import Any

from _contract import (
    NOTIFICATION_EVENT_TYPES,
    RUN_FAILED,
    AgentAssessment,
    AgentOutcome,
    AgentVerdict,
    DecisionCard,
    FactBundle,
    LegacyAdapter,
    is_adhoc_task_id,
    new_task_id,
    now_cn,
)

from .schema import MIGRATIONS, SCHEMA_VERSION

__all__ = [
    "DEFAULT_DB_PATH",
    "connect",
    "db_path",
    "init_schema",
    "save_card",
    "save_card_with_notifications",
    "enqueue_run_failed",
    "record_delivery",
    "undelivered_notifications",
    "list_deliveries",
    "load_online_card",
    "load_card_by_record_id",
    "load_verdicts",
    "latest_verdict_ids",
    "next_decision_id",
    "reserve_decision_id",
    "record_agent_run",
    "list_agent_runs",
    "save_raw_snapshot",
    "load_raw_snapshot",
    "save_evidence_set",
    "load_evidence_set",
    "save_fact_bundle",
    "load_fact_bundle",
    "save_assessment",
    "load_outcome",
    "AppendOnlyViolation",
]

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "biga.db"


def _canonical_dumps(payload: Any) -> str:
    """规范序列化 —— 只用于**新增**的持久化载荷（card_json / verdict_json）。

    🔴 与 `payload_sha256` 是两个不同的函数，且不能合并成一个：
    历史哈希已经建立在 `payload_sha256` **不带 `separators`** 的输出上；
    这里的 `separators` 只影响新写入的载荷，不回头改变任何已落库的哈希。
    `allow_nan=False` 保证写进去的字节本身就是合法 JSON，不是「Python 能读、
    RFC 8259 不认」的裸 `NaN`/`Infinity`。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       allow_nan=False, separators=(",", ":"))


class StoreNotInitialised(RuntimeError):
    """只读打开一个还不存在的库。

    单独一个类型，是为了让调用方能把它与「真的坏了」分开处理 ——
    全新环境里没有库是**正常**的，不该和数据损坏报同一种脸色。
    """


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
        # 🔴 外部评审 F23：库文件不存在时，`mode=ro` 抛的是
        #    `sqlite3.OperationalError: unable to open database file` ——
        #    一个不说路径、不说该做什么的裸异常。全新 clone 里跑任何
        #    巡检工具都会撞到它（`data/biga.db` 是 .gitignore'd 的）。
        #
        #    ⚠️ 修在这里、不修在某个工具里：所有只读消费方共用这一个入口，
        #      在调用方各写一遍 `if not exists` 就又是一份散开的判据。
        #
        #    区分「文件不在」和「schema 建好但零行」—— 后者是正常状态。
        if not p.exists():
            raise StoreNotInitialised(
                f"事实库不存在：{p}\n"
                f"  它是 .gitignore 的，全新 clone 里本来就没有。\n"
                f"  先出一张卡把它建起来：`bin/biga-card`\n"
                f"  只想建空库：`python3 -c \"import sys;sys.path.insert(0,'skills');"
                f"from _store import db;db.init_schema()\"`")
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

    🔴 批 G-I：这是 `save_card_with_notifications(card, ())` 的薄封装 —— 不入队任何
       通知。要在同一个事务里连带入队外发通知（在线出卡路径），走后者。回放
       （`replay.py --store`）与其余不推通知的调用方继续用这个。

    Args:
        replay_of: 回放时填被回放记录的 `record_id`；在线路径留空。
    """
    return save_card_with_notifications(card, (), replay_of=replay_of, path=path)


def save_card_with_notifications(
    card: DecisionCard,
    notifications: "list | tuple",
    *,
    replay_of: int | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """在**同一个事务**里落 Card 并入队外发通知，返回 `record_id`（批 G-I）。

    🔴 **探针 P1（同事务原子性）**：Card 落库与通知入队要么一起成功、要么一起失败。
       `notifications` 里任一条入队失败（如 `event_type` 非法、payload 非严格 JSON）
       ⇒ 抛异常 ⇒ `connect()` 回滚整段 ⇒ **Card 也不落库**。不会出现「卡进去了、
       通知没进去」的中间状态。这正是分发提示词点名的那条：outbox 与 Card 同事务。

    Args:
        notifications: 形如 `[{"event_type": ..., "aggregate": ..., "payload": {...}}, ...]`。
            在线出卡路径由 `card_ops.persist` 用 `_contract.card_event_type` 从卡分类得来
            （通常一条）。回放路径（`replay_of` 非空）**不推通知**（回放不重新执行、也不
            重推），传空即可 —— `save_card` 就是这么委托的。
    """
    if not isinstance(card, DecisionCard):
        raise TypeError(
            f"save_card 只接受 _contract.DecisionCard，收到 {type(card).__name__}"
        )
    # 🔴 第三道：**落库永远拒绝身份不一致的卡。**
    #
    # 契约层对「新造的卡」是硬拒绝，但对 `from_dict()` 读回来的历史卡放行
    # （否则 31 张里有 20 张再也读不出来）。那个放行口在这里必须堵上 ——
    # 否则「读一张旧卡 → 原样存回去」就把非法状态重新写进了库。
    #
    # ⇒ 读可以宽，**写必须严**。
    foreign = [(v.agent, v.task_id) for v in card.verdicts
               if v.task_id != card.decision_id]
    if foreign:
        raise ValueError(
            f"拒绝落库：Card {card.decision_id} 装着不属于它的判定 —— "
            + "；".join(f"{a} 写着 {t}" for a, t in foreign) + "\n"
            "  一张卡上的每一条判定都必须属于同一次决策。\n"
            "  历史卡可以读（回放），但不能再写回库。")
    payload = _canonical_dumps(card.to_dict())
    # 🔴 写边界重校验（设计文档 §6 A3）：不信任调用方交进来的对象本身，
    #    只信任「它序列化之后还能不能重建出来」——
    #    `card.missing.append(...)` 这类构造后直接改字段的写法会绕过
    #    `__post_init__`，上面的 `foreign` 检查只堵得住身份这一项，
    #    其余不变量（铁律 2、重述缺失项……）全靠这一行兜底。
    #
    #    ⚠️ 不能直接调 `DecisionCard.from_dict(json.loads(payload))`——
    #    它默认 `from_store=True`，会把新卡的严格校验悄悄降级成历史卡的
    #    宽松校验。
    #
    #    ⚠️ 也不能传 `card.from_store`——那是 `DecisionCard` 一个**可变**属性
    #    （它还不是 `frozen`，那是 A-II 的 A2），`card.from_store = True`
    #    不会报错，会把这一行重校验连同上面的意图一起绕过：
    #    「新卡严、旧卡宽」的档位就被对象自己说了算，而对象正是这次要防的
    #    篡改对象。改用 `replay_of` 这个**调用参数**推导档位——它由
    #    `card_ops.persist()` 的调用方（`synthesize.py` 在线路径永远不传，
    #    `replay.py --store` 永远传原始 `record_id`）决定，不受 `card` 本身
    #    的状态影响，档位判断因此挪出了可被篡改的对象。
    DecisionCard.from_dict(json.loads(payload), from_store=replay_of is not None)
    try:
        # 🔴 一个 connect() = 一个事务：卡的 INSERT 与通知的 INSERT 全在里面。
        #    `connect()` 正常退出才 commit；中途任一 INSERT 抛错 → 它 rollback 整段。
        with connect(path) as conn:
            record_id = _insert_card_row(conn, card, payload, replay_of)
            for n in notifications:
                _insert_notification(conn, event_type=n["event_type"],
                                     aggregate=n["aggregate"], payload=n["payload"])
        return record_id
    except sqlite3.IntegrityError as e:
        if "decision_records.decision_id" not in str(e):
            raise
        # 报错要能自解释：否则调用方只能去猜，然后手动查库推序号（实测发生过）
        raise ValueError(
            f"decision_id {card.decision_id!r} 已被占用。"
            f"同一天的第 N 次决策要用不同序号 —— "
            f"用 `_store.next_decision_id()` 自动分配，不要硬编码 001。"
        ) from e


def _insert_card_row(conn: sqlite3.Connection, card: DecisionCard, payload: str,
                     replay_of: int | None) -> int:
    """在**给定连接**（= 调用方的事务）里插一行 decision_records。返回 record_id。

    抽出来是为了让 `save_card_with_notifications` 能把它和通知入队放进同一个事务 ——
    不各开各的 `connect()`（那就成了两次独立提交，通知失败也拦不住卡已落库）。
    """
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


# ─────────────────────────────────────────────── notification_outbox / deliveries
#
# 外发通知（批 G-I，Outbound Only）。设计文档 §6 批 G-I。
# outbox = 「该推哪件事」的队列（幂等键 (event_type, aggregate)）；deliveries =
# 「投递尝试」的追加日志。两张都只追加（触发器强制），「投没投成」是派生查询
# （见 schema.py _V11 的设计裁定）。


def _insert_notification(conn: sqlite3.Connection, *, event_type: str,
                         aggregate: str, payload: Any) -> int | None:
    """在**给定连接**（= 调用方的事务）里入队一行 notification_outbox。

    返回新 `outbox_id`；若 `(event_type, aggregate)` 已入队（幂等冲突）返回 None。

    🔴 `event_type` 白名单 **fail-closed**：非法值抛 `ValueError`。因为它在调用方的
       事务里抛，`save_card_with_notifications` 的「Card + 通知同成同败」就靠它 ——
       非法 event_type ⇒ 这里抛 ⇒ 整段回滚 ⇒ 卡不落库（探针 P1）。未知事件类型当 bug
       拒绝，不静默入队一条 worker 投不出去的通知（与 `RUN_ORIGINS` 同立场）。

    🔴 幂等走 `ON CONFLICT(event_type, aggregate) DO NOTHING` —— 重复入队是无害 no-op
       （探针 P2：不会造成同一通知被投两次）。它只吃 UNIQUE 冲突，不碰 append-only
       触发器（那对触发器管 UPDATE/DELETE，不管 INSERT）。用它而不是「先查再插」：
       同一招唯一约束仲裁，没有竞态窗口（与 decision_ids 占号同形）。
    """
    if event_type not in NOTIFICATION_EVENT_TYPES:
        raise ValueError(
            f"event_type={event_type!r} 不在白名单 "
            f"{sorted(NOTIFICATION_EVENT_TYPES)} —— 未知事件类型当 bug 拒绝"
            "（fail-closed），不静默入队一条投不出去的通知。")
    if not isinstance(aggregate, str) or not aggregate.strip():
        raise ValueError(
            f"aggregate 必须是非空字符串（幂等键的一半），收到 {aggregate!r}")
    blob = _canonical_dumps(payload)
    # 写边界重校验（A3）：只信「序列化之后还能读回来」。非严格 JSON（NaN/Infinity）
    # 在这里就地抛 —— 与卡在同一事务，于是也会把卡一起回滚（P1）。
    json.loads(blob)
    cur = conn.execute(
        "INSERT INTO notification_outbox (event_type, aggregate, payload_json, created_at) "
        "VALUES (?,?,?,?) ON CONFLICT(event_type, aggregate) DO NOTHING",
        (event_type, aggregate, blob, now_cn().isoformat()),
    )
    # ON CONFLICT DO NOTHING 命中冲突时 rowcount==0、lastrowid 不可靠 ⇒ 返回 None。
    return int(cur.lastrowid) if cur.rowcount else None


def enqueue_run_failed(
    run_id: str, *, reason: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int | None:
    """给一次进入失败终态（FAILED/TIMEOUT/CANCELLED）的运行入队 run_failed 通知。

    幂等（`aggregate=run_id`，一次执行尝试至多失败一次）；返回 `outbox_id` 或 None（已入队）。

    🔴 **自己开事务，不与终态转移强绑**：分发提示词的 P1 同事务原子性只对 Card+outbox
       要求。运行失败的通知是**尽力而为**——通知入队失败绝不能回滚「这次运行失败了」
       这条 run_events 记录（那比漏一条通知糟得多）。调用方（`orchestrator._fail`
       best-effort、`run_ledger.cmd_move` 终态分支）在转移**之后**调它；进程若在中间被杀，
       顶多漏一条通知（幂等 ⇒ reaper/重跑可安全补），run 的终态已如实落库。

    payload 带 `decision_id`（P4 的「对应决策号」）——可能为 None：legacy 早退在占号
    之前就失败。run_id 一定有。
    """
    with connect(path) as conn:
        row = conn.execute(
            "SELECT decision_id, origin FROM decision_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            # 没 open_run 过就想推它失败 —— 调用方 bug，fail-loud（不静默吞）。
            raise ValueError(
                f"run {run_id!r} 不在 decision_runs 里，无法入队 run_failed 通知 —— "
                "先 open_run()。")
        payload = {"run_id": run_id, "decision_id": row["decision_id"],
                   "origin": row["origin"], "reason": reason}
        return _insert_notification(conn, event_type=RUN_FAILED,
                                    aggregate=run_id, payload=payload)


def record_delivery(
    *, outbox_id: int, attempt: int, status: str, channel: str,
    error: str | None = None, path: pathlib.Path | str | None = None,
) -> int:
    """追加一条投递尝试记录，返回 `delivery_id`。

    `status` ∈ {'delivered','failed'}（fail-closed 白名单）。「这条 outbox 投没投成」=
    有没有一条 status='delivered' 的记录（`undelivered_notifications` 就按它过滤）。
    """
    if status not in ("delivered", "failed"):
        raise ValueError(
            f"delivery status 只能 'delivered' / 'failed'，收到 {status!r}")
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO notification_deliveries "
            "(outbox_id, attempt, status, channel, error, at) VALUES (?,?,?,?,?,?)",
            (int(outbox_id), int(attempt), status, channel, error, now_cn().isoformat()),
        )
        return int(cur.lastrowid)


def undelivered_notifications(
    *, limit: int = 100, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    """还没投递成功的 outbox 行 —— **worker 的读取方**（批 G-I 的 L-1 消费方）。

    「没投成」= `notification_deliveries` 里没有这条 outbox 的 status='delivered' 行。
    附 `attempt_count`（已尝试几次）供 worker 决定第几次投递 / 是否放弃（这一批只投一遍）。
    `payload` 已从 JSON 解析回 dict。按 outbox_id 升序（先入队先投）。
    """
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            """SELECT o.outbox_id, o.event_type, o.aggregate, o.payload_json, o.created_at,
                      (SELECT COUNT(*) FROM notification_deliveries d
                       WHERE d.outbox_id = o.outbox_id) AS attempt_count
                 FROM notification_outbox o
                WHERE NOT EXISTS (
                      SELECT 1 FROM notification_deliveries d
                       WHERE d.outbox_id = o.outbox_id AND d.status = 'delivered')
                ORDER BY o.outbox_id ASC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def list_deliveries(
    outbox_id: int, *, path: pathlib.Path | str | None = None
) -> list[dict[str, Any]]:
    """一条 outbox 的全部投递尝试，按 attempt 升序 —— 排查 / 断言用。"""
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT delivery_id, outbox_id, attempt, status, channel, error, at "
            "FROM notification_deliveries WHERE outbox_id=? ORDER BY attempt ASC",
            (int(outbox_id),),
        ).fetchall()
    return [dict(r) for r in rows]


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
    day = day or now_cn().strftime("%Y%m%d")
    return new_task_id(_next_free_seq(day, path), day=day)


def _next_free_seq(day: str, path: pathlib.Path | str | None) -> int:
    """当天第一个没被占用的序号。

    🔴 两张表都要看：已出的卡 **和** 已占但还没出卡的号。
    只看前者，Stage 0 占了号而 Stage 3 还没落卡的那段窗口里，
    第二次运行会拿到同一个号 —— 这正是 v4 要消灭的竞态。
    """
    used = set()
    with connect(path, readonly=True) as conn:
        for tbl in ("decision_records", "decision_ids"):
            for r in conn.execute(
                f"SELECT decision_id FROM {tbl} WHERE decision_id LIKE ?",
                (f"BIGA-{day}-%",),
            ):
                tail = r["decision_id"].rsplit("-", 1)[-1]
                if tail.isdigit():
                    used.add(int(tail))
    # 从 1 开始：0 是临时号，永远不分配给真决策
    return next(i for i in range(1, 1000) if i not in used)


def reserve_decision_id(
    *, by: str | None = None, day: str | None = None,
    path: pathlib.Path | str | None = None,
) -> str:
    """**原子地**占一个决策编号。Stage 0 调用，把它传给所有 specialist。

    🔴 为什么必须原子：主键冲突是唯一可靠的并发仲裁。
    「先查空位再插入」中间有窗口 —— 两次同时起的运行会拿到同一个号，
    然后它们的证据合进同一张卡，事后**没有任何字段能把它们分开**。
    这不是假想：2026-09-21 盘中的两次端到端就是这么混的。

    🔴 会自己建 schema。
    外部评审 P2-2：`new_decision.py` 在**全新的库**上直接抛
    `sqlite3.OperationalError: unable to open database file` ——
    因为算下一个序号走的是 readonly 连接，而文件还不存在。

    Stage 0 是整条链路的**第一步**，它必须能在空环境里独立跑起来 ——
    否则「自包含的入口」这个说法不成立。
    ⇒ 建 schema 放在这里而不是 CLI 里：任何调用方都受益，
    而放在 CLI 就只有那一个入口受益。
    """
    init_schema(path)
    for _ in range(1000):
        cand = new_task_id(_next_free_seq(day or now_cn().strftime("%Y%m%d"), path),
                           day=day)
        try:
            with connect(path) as conn:
                conn.execute(
                    "INSERT INTO decision_ids (decision_id, reserved_at, reserved_by)"
                    " VALUES (?,?,?)", (cand, now_cn().isoformat(), by))
            return cand
        except sqlite3.IntegrityError:
            continue  # 被人抢先，重算下一个
    raise RuntimeError("当天 1000 个决策编号全被占用 —— 这不正常，先查 decision_ids 表")


def reserve_decision_for_trigger(
    trigger_id: str, *, by: str | None = None, day: str | None = None,
    path: pathlib.Path | str | None = None,
) -> tuple[str, bool]:
    """幂等地为一次外部请求占号。返回 `(decision_id, created)`（批 G-II）。

    `created=False` ⇒ 这个 `trigger_id` 之前已经占过号（飞书事件**重投**），返回那次
    的号、**不起新决策**；`created=True` ⇒ 这次是新占的号。入站适配器据 `created`
    决定「起一次真出卡」还是「回一句已在处理」。

    🔴 为什么它必须原子，而不是「先查 trigger 在不在、不在就占号」
    ------------------------------------------------------------------
    「先查再占」中间有窗口：两次同时到达的重投都查到「没占过」，各占一个号，
    最后合出两张卡 —— 与 `reserve_decision_id` docstring 记的 2026-09-21 盘中
    「两次端到端混进同一个号」同形状，只是这次的触发源是**同一个飞书 event 的重投**。
    唯一可靠的并发仲裁是数据库自己的唯一约束（`decision_ids.trigger_id`，schema v13）：
    两个并发 INSERT 只有一个成功，另一个撞 UNIQUE、回退到「返回已占的那个号」。

    与 `reserve_decision_id` 的关系：它是后者的**幂等包装** —— 复用同一套「主键冲突
    仲裁占号」的候选号循环，只是每个候选号带上 `trigger_id` 一起 INSERT，于是占号与
    「这个号是为哪次外部请求占的」是**同一个原子写**。绑在号分配器上而不是新开一张
    入站幂等表：设计探活点名「这一列已经在等着被用」，且身份模型本就是
    Trigger → Decision（一个 trigger 一个决策；重试复用同一号、不重占 ⇒ 不撞约束）。
    """
    if not isinstance(trigger_id, str) or not trigger_id.strip():
        raise ValueError(f"trigger_id 必须是非空字符串，收到 {trigger_id!r}")
    init_schema(path)

    def _lookup() -> str | None:
        with connect(path, readonly=True) as conn:
            row = conn.execute(
                "SELECT decision_id FROM decision_ids WHERE trigger_id=?", (trigger_id,)
            ).fetchone()
        return row["decision_id"] if row else None

    # 快路径：这个 trigger 之前占过号就直接返回（重投的常见情形 —— 顺序重投，非并发）。
    existing = _lookup()
    if existing is not None:
        return existing, False

    for _ in range(1000):
        cand = new_task_id(_next_free_seq(day or now_cn().strftime("%Y%m%d"), path),
                           day=day)
        try:
            with connect(path) as conn:
                conn.execute(
                    "INSERT INTO decision_ids (decision_id, reserved_at, reserved_by,"
                    " trigger_id) VALUES (?,?,?,?)",
                    (cand, now_cn().isoformat(), by, trigger_id))
            return cand, True
        except sqlite3.IntegrityError as e:
            # 🔴 两种撞法必须分开：trigger_id 撞 = 并发同 trigger 抢先，回退返回它的号
            #    （幂等）；decision_id 主键撞 = 号被别的决策占了，换下一个号继续。
            if "trigger_id" in str(e):
                won = _lookup()
                if won is not None:
                    return won, False
                raise  # trigger 撞了却查不到 —— 不是并发占号，是真异常，别吞
            continue  # decision_id 被抢，重算下一个
    raise RuntimeError("当天 1000 个决策编号全被占用 —— 这不正常，先查 decision_ids 表")


def load_online_card(
    decision_id: str,
    *,
    path: pathlib.Path | str | None = None,
) -> DecisionCard | None:
    """取回某个 decision_id 的**在线**记录（非回放）。找不到返回 None。

    🔴 设计文档 §6 A8：这里原本是一个 `load_card(decision_id, *, record_id=None)`，
    两个 id 都收，`record_id` 给了就按它查、`decision_id` 参数被静默忽略 ——
    `load_card("乱写的号", record_id=真实id)` 会正常返回，调用方以为自己按
    decision_id 校验过了，其实那个校验从未发生。
    ⇒ 拆成两个 API：这一个只认 `decision_id`（在线记录），
    `load_card_by_record_id()` 只认 `record_id`。不再有「两个 id 都能传，
    其中一个被悄悄忽略」这条路。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT card_json FROM decision_records "
            "WHERE decision_id=? AND replay_of IS NULL",
            (decision_id,),
        ).fetchone()
    return DecisionCard.from_dict(json.loads(row["card_json"])) if row else None


def load_card_by_record_id(
    record_id: int,
    *,
    path: pathlib.Path | str | None = None,
) -> DecisionCard | None:
    """按 `record_id` 精确取回一行 —— 在线记录或某一次回放都能取，不猜测哪个是「当前」。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT card_json FROM decision_records WHERE record_id=?", (int(record_id),)
        ).fetchone()
    return DecisionCard.from_dict(json.loads(row["card_json"])) if row else None


def load_verdicts(
    decision_id: str, *, path: pathlib.Path | str | None = None
) -> list[AgentVerdict]:
    """取回某次决策的冻结证据 —— 回放的输入。"""
    card = load_online_card(decision_id, path=path)
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
            🔴 设计文档 §6 A8：修订不许跨 agent、跨决策，且只能线性
            （一条原件最多被修订一次，不许分叉）——前者代码校验，
            后者由 schema v6 的 `ux_verdict_amends_linear` 唯一索引兜底
            （应用层「先查再插」有竞态窗口，唯一约束没有）。
        amend_reason: 为什么修订。没有它，修订链读起来只是「有两行」。
    """
    if not isinstance(v, AgentVerdict):
        raise TypeError(
            f"save_verdict 只接受 _contract.AgentVerdict，收到 {type(v).__name__}")
    if amends is not None and amend_reason is None:
        # 修订不写理由，三个月后没人知道这一行为什么存在。
        raise ValueError("amends 非空时必须给 amend_reason —— 修订要写为什么")
    if amends is not None:
        original_meta = load_verdict_meta(amends, path=path)
        if original_meta is None:
            raise ValueError(
                f"amends={amends} 在 agent_verdicts 里不存在 —— "
                "修订必须指向一条真实落库的原件。")
        if original_meta["task_id"] != v.task_id or original_meta["agent"] != v.agent:
            raise ValueError(
                f"修订必须同一次决策、同一个 agent：原件 #{amends} 是 "
                f"{original_meta['agent']}/{original_meta['task_id']}，"
                f"这次却是 {v.agent}/{v.task_id} —— "
                "修订不能跨 agent 或跨决策，那样读者会以为是同一条判定的两个版本。")
    # 🔴 临时号不得入账。守卫放在这里而不是五个 specialist 里 ——
    #    调用点会越来越多，而这里是**唯一**的写入口（铁律 2）。
    if is_adhoc_task_id(v.task_id):
        raise ValueError(
            f"[{v.agent}] task_id={v.task_id} 是临时号（序号 000），不能落库。\n"
            "  原因：决策编号归 Supervisor 所有，specialist 自己编的号无法归属。\n"
            "  怎么办：\n"
            "    · 由 Supervisor 在 Stage 0 占号并用 --task-id 传下来；\n"
            "      占号命令 python3 skills/decision-card/scripts/new_decision.py\n"
            "    · 只是手工看一眼输出 ⇒ 加 --no-store")
    blob = _canonical_dumps(v.to_dict())
    # 🔴 写边界重校验（设计文档 §6 A3）：同上，不信任对象本身，
    #    只信任「序列化之后还能不能重建出来」。
    #    这里没有 `from_store` 这道口子要留 —— AgentVerdict 没有历史宽松语义，
    #    from_dict 就是它唯一的重建路径。
    AgentVerdict.from_dict(json.loads(blob))
    # 🔴 `content_sha256` 是**这次写入的 `blob` 文本**的哈希，不是从
    #    `AgentVerdict` 对象独立重算出来的哈希——它直接对刚刚生成的
    #    `blob` 取 sha256，因此永远与同一行的 `verdict_json` 自洽，
    #    但**不代表**「用今天的 `_canonical_dumps` 重新序列化这个对象
    #    也会得到同一个哈希」。`_canonical_dumps` 的格式本身不是冻结的
    #    （这一批就刚加过 `separators`）——历史行的哈希锚定的是「当时」
    #    的序列化格式，不是这个对象的规范形式。
    #    ⚠️ 批 A-II 的 A6（`VerdictRef.content_sha256` 上卡校验）如果改成
    #    「重新序列化对象再比对」而不是「直接比对存量 `verdict_json` 文本
    #    的哈希」，这一批之前落库的行会集体核对不上——而且是静默的。
    #    `tests/test_write_boundary.py` 钉了这一点，别让它变红。
    try:
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
    except sqlite3.IntegrityError as e:
        if "ux_verdict_amends_linear" not in str(e) and "agent_verdicts.amends" not in str(e):
            raise
        # 报错要能自解释：这是 schema v6 的唯一索引在拦，不是随便一个约束炸了。
        raise ValueError(
            f"verdict_id={amends} 已经被修订过一次了 —— 修订链只能线性，不许分叉。\n"
            f"  如果这是有意的第二次修订：先看已有的那条修订说了什么"
            f"（load_verdict_meta 或 --ref 查一下它的 amends 链），"
            f"在它的基础上再修一次，不要指回同一条原件。"
        ) from e


def latest_verdict_ids(
    task_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, int]:
    """每个 agent 在这次决策下**最新**那条判定原件的 id（agent → verdict_id）。

    🔴 DecisionOrchestrator 靠它拿 verdict_id，而不是去解析 Specialist 回复文本里的
    `verdict_ref=NN`——那种解析是 F3/L-13 的形状（把判据落在 LLM 复述的文本上）。
    这里直接按 `(task_id, agent)` 查库，是结构化、确定性的。

    「最新」= amend 链的 tip。amendment 的 verdict_id 一定比它改的原件大
    （autoincrement，后写），且 schema v6 保证修订线性、同 agent 同 task ——
    所以 `MAX(verdict_id) GROUP BY agent` 就是每个 agent 的当前判定。
    """
    with connect(path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT agent, MAX(verdict_id) AS vid FROM agent_verdicts "
            "WHERE task_id=? GROUP BY agent",
            (task_id,),
        ).fetchall()
    return {r["agent"]: int(r["vid"]) for r in rows}


def load_verdict(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> AgentVerdict | None:
    """按 id 取回判定，**压成旧消费者认识的 AgentVerdict**。找不到返回 None。

    🔴 批 E-I：一行可能是三种形状之一（`kind` 列）——
      · 旧 `AgentVerdict`（kind NULL/'verdict'）：直接 `from_dict`。
      · 新 `FactBundle`（kind='fact'）/`AgentAssessment`（kind='assessment'）：
        走 `load_outcome` 拼成 `AgentOutcome`，再 `to_agent_verdict()` 压回。
    这样 `card_ops` / `risk_check` / `DecisionCard` **零改动**——它们拿到的永远是
    一个 AgentVerdict，不管底下是新是旧（分发提示词「让消费方返回值都长一样」）。
    要看拆开的 FactBundle/AgentAssessment 用 `load_outcome`。
    """
    kind = _verdict_kind(verdict_id, path=path)
    if kind is _MISSING:
        return None
    if kind in (None, "verdict"):
        with connect(path, readonly=True) as conn:
            row = conn.execute(
                "SELECT verdict_json FROM agent_verdicts WHERE verdict_id=?",
                (int(verdict_id),),
            ).fetchone()
        return AgentVerdict.from_dict(json.loads(row["verdict_json"])) if row else None
    oc = load_outcome(verdict_id, path=path)
    return oc.to_agent_verdict() if oc else None


def load_verdict_meta(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """取回一行的元信息（含修订链 + `kind` + `run_id`），不构造契约对象。

    🔴 批 J-I：`run_id` 列一并取回 —— 两个消费方都靠它：`save_assessment` 从被 amends
    的 fact 行继承 run_id，`card_ops` / `synthesize.py` 从存量行搬进 `VerdictRef.run_id`。
    历史行没有这一列时读回来是 None（nullable，不回填）。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT verdict_id, task_id, agent, amends, amend_reason, "
            "content_sha256, created_at, kind, run_id FROM agent_verdicts WHERE verdict_id=?",
            (int(verdict_id),),
        ).fetchone()
    return dict(row) if row else None


def verify_verdict_refs(
    card: DecisionCard, *, path: pathlib.Path | str | None = None
) -> list[str]:
    """核对 `card.input_verdict_refs` 是否仍与 `agent_verdicts` 现状一致。

    返回问题列表，**空列表 = 全部一致**（含「这张卡没有任何 ref 可核」——
    历史卡没有这份数据，不算不一致，见 `DecisionCard.input_verdict_refs`）。

    🔴 设计文档 §6 A6 + 追加 4（约束 A6）：核对必须走
    `hashlib.sha256(存量 verdict_json 文本)`，不能走「把 AgentVerdict
    对象重新序列化再比对」——`agent_verdicts.content_sha256` 这一列
    本来就是**当时写入那段文本**的哈希（`save_verdict` 里现算的），
    这里只是原样取回来比对，不重新计算，所以天然不会踩中
    `tests/test_write_boundary.py::TestVerdictContentShaIsHashOfStoredText`
    钉住的那个坑。

    🔴 批 C-III（设计文档 §2 追加 5 §8-16）：还要核对 `ref.agent == 存量.agent`。
    `verdict_id` 是**跨 agent 的全局自增**，不按 agent 分号段 —— 一条手工拼出来
    的 ref 可以声称「这是 market 的原件」，`verdict_id`/`content_sha256` 却全指向
    news 那一行，此时「能找到 + hash 对」两道检查都通过，只有 agent 核对能拦下它。
    `VerdictRef.agent` 的 docstring 早就写明它「必须与被引用那条 `AgentVerdict.agent`
    一致」，这里补上真正强制那句话的检查。
    """
    problems: list[str] = []
    for ref in card.input_verdict_refs:
        meta = load_verdict_meta(ref.verdict_id, path=path)
        if meta is None:
            problems.append(
                f"[{ref.agent}] verdict_id={ref.verdict_id} 在 agent_verdicts "
                f"里已经找不到了 —— 这张卡引用的原件消失了")
            continue
        if meta["agent"] != ref.agent:
            problems.append(
                f"[{ref.agent}] verdict_id={ref.verdict_id} 声称是 {ref.agent} 的原件，"
                f"但 agent_verdicts 里这一行其实是 {meta['agent']} 的 —— "
                f"verdict_id 是跨 agent 的全局自增，光靠「能找到 + hash 对」"
                f"核不出这种张冠李戴")
        if meta["content_sha256"] != ref.content_sha256:
            problems.append(
                f"[{ref.agent}] verdict_id={ref.verdict_id} 的哈希对不上："
                f"卡上记的是 {ref.content_sha256[:12]}…，"
                f"agent_verdicts 里现在是 {meta['content_sha256'][:12]}… —— "
                f"这条原件在合成之后被改变过")
    return problems


# ──────────────────── FactBundle / AgentAssessment（批 E-I：事实与判断拆开）
#
# 新形状与旧 AgentVerdict 同住 agent_verdicts（`kind` 列区分）—— 复用它已有的只追加
# 触发器与线性修订唯一索引，不另起一张表再维护一套同样的约束（L-3）。


_MISSING = object()  # 区分「行不存在」与「kind 是 NULL（旧 AgentVerdict）」


def _verdict_kind(verdict_id: int, *, path: pathlib.Path | str | None = None):
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT kind FROM agent_verdicts WHERE verdict_id=?", (int(verdict_id),)
        ).fetchone()
    return _MISSING if row is None else row["kind"]


def save_fact_bundle(
    fb: FactBundle, *, run_id: str | None = None,
    path: pathlib.Path | str | None = None
) -> int:
    """落一份 `FactBundle`（skill 产出的事实，无 stance），返回行号。

    🔴 **写路径严**（§9）：只收 `FactBundle`。旧 `AgentVerdict` 走 `save_verdict`——
    新落库路径不接受旧形状，旧格式才会随时间自然清零，不变成第二套要跟着演进的口径。

    `run_id`（批 J-I，可选、默认 None）：这条事实是哪次编排执行尝试产生的
    （`RunContext.run_id`），由 skill 经 `--run-id` 带下来。只 capture 不 enforce ——
    不传就是 None（历史行 / 手工跑 skill），读路径不因此报错。
    """
    if not isinstance(fb, FactBundle):
        raise TypeError(
            f"save_fact_bundle 只接受 _contract.FactBundle，收到 {type(fb).__name__} —— "
            "旧 AgentVerdict 走 save_verdict；新落库路径不收旧形状（写路径严，§9）。")
    if is_adhoc_task_id(fb.task_id):
        raise ValueError(
            f"[{fb.agent}] task_id={fb.task_id} 是临时号（序号 000），不能落库。\n"
            "  由 Supervisor 在 Stage 0 占号并用 --task-id 传下来；只看输出加 --no-store。")
    blob = _canonical_dumps(fb.to_dict())
    # 🔴 写边界重校验（A3）：只信「序列化之后还能重建出来」。
    FactBundle.from_dict(json.loads(blob))
    try:
        with connect(path) as conn:
            cur = conn.execute(
                "INSERT INTO agent_verdicts "
                "(task_id, agent, amends, amend_reason, verdict_json, content_sha256, "
                " created_at, kind, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (fb.task_id, fb.agent, None, None, blob,
                 hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                 now_cn().isoformat(), "fact", run_id),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as e:
        # 🔴 批 F：schema v11 的 `ux_fact_per_task_agent` 在拦「同一个 (task_id, agent)
        #    第二份 fact」。fact 行 amends 恒 NULL，v6 的线性索引管不到它 —— 没有这条
        #    分区唯一索引，第二次写会静默产生两条并存原件（编排器算的那条被 MAX 架空）。
        #    报错要报得明确（不是裸 IntegrityError）并指路：手工复核只想看输出加 --no-store。
        # SQLite 报的是**列名**（`agent_verdicts.task_id, agent_verdicts.agent`），
        # 不是索引名 —— 与 save_verdict 匹配 `agent_verdicts.amends` 同理。只有
        # ux_fact_per_task_agent 同时涉及这两列，两者都在才是它。
        msg = str(e)
        if not ("agent_verdicts.task_id" in msg and "agent_verdicts.agent" in msg):
            raise
        existing = None
        with suppress(sqlite3.Error):
            with connect(path, readonly=True) as conn:
                row = conn.execute(
                    "SELECT verdict_id FROM agent_verdicts "
                    "WHERE task_id=? AND agent=? AND kind='fact' "
                    "ORDER BY verdict_id LIMIT 1",
                    (fb.task_id, fb.agent),
                ).fetchone()
            existing = row["verdict_id"] if row else None
        raise ValueError(
            f"[{fb.agent}] 决策 {fb.task_id} 已经有一份 fact 原件了"
            + (f"（verdict_ref={existing}）" if existing else "")
            + " —— 一个 (task_id, agent) 至多一份事实，不许第二次落 fact。\n"
            "  批 F：risk 的事实由编排器在 spawn 之前算好落库，你不该再自己跑一遍 "
            "risk_check.py。\n"
            "  · 要给这份事实加判断：amend_verdict.py --ref "
            + (str(existing) if existing else "<那条 fact 的 verdict_ref>")
            + " --stance <词>；\n"
            "  · 只是手工看一眼 skill 的输出：加 --no-store（不落库）。"
        ) from e


def load_fact_bundle(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> FactBundle | None:
    """按 id 取回一条 `FactBundle`。不是 fact 行返回 None。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT verdict_json, kind FROM agent_verdicts WHERE verdict_id=?",
            (int(verdict_id),),
        ).fetchone()
    if row is None or row["kind"] != "fact":
        return None
    return FactBundle.from_dict(json.loads(row["verdict_json"]))


def save_assessment(
    a: AgentAssessment, *, fact_id: int,
    path: pathlib.Path | str | None = None,
) -> int:
    """落一份 `AgentAssessment`（Agent 的 stance，指回 `fact_id`），返回行号。

    🔴 **不抄事实**：只存 stance + `fact_ref`。`amends=fact_id` 复用线性修订唯一
    索引 —— 一份事实**最多一个判断**。写库前构造 `AgentOutcome(fact, a)` 走一遍
    跨型铁律（UNKNOWN 的事实上不许有方向判断）；`fact_id` 必须指向一条同
    `(task_id, agent)` 的 `fact` 行。
    """
    if not isinstance(a, AgentAssessment):
        raise TypeError(
            f"save_assessment 只接受 _contract.AgentAssessment，收到 {type(a).__name__}")
    meta = load_verdict_meta(fact_id, path=path)
    if meta is None:
        raise ValueError(
            f"fact_id={fact_id} 不存在 —— assessment 必须指向一条真实落库的 FactBundle。")
    if meta["kind"] != "fact":
        raise ValueError(
            f"fact_id={fact_id} 不是 FactBundle（kind={meta['kind']!r}）—— assessment "
            "只能挂在 fact 行上，不能挂到旧 AgentVerdict 或另一个 assessment 上。")
    if meta["task_id"] != a.task_id or meta["agent"] != a.agent:
        raise ValueError(
            f"assessment（{a.agent}/{a.task_id}）与 fact #{fact_id}"
            f"（{meta['agent']}/{meta['task_id']}）不是同一个 (task_id, agent) —— "
            "判断不能挂到别人的事实上。")
    # 🔴 跨型铁律在写库前校验：加载 fact，构造 AgentOutcome 会在 UNKNOWN+方向判断时抛错。
    fact = load_fact_bundle(fact_id, path=path)
    AgentOutcome(fact=fact, assessment=dataclasses.replace(a, fact_ref=fact_id))
    a = dataclasses.replace(a, fact_ref=fact_id)  # 自描述：json 里也带上它指的 fact 行
    blob = _canonical_dumps(a.to_dict())
    AgentAssessment.from_dict(json.loads(blob))  # 写边界重校验
    # 🔴 批 J-I（2b）：assessment 的 run_id **从被 amends 的 fact 行继承**，不由 Agent
    #    在命令行上传。理由不是省事：Agent 手传就可能传错，而「从 meta 直接搬」在结构上
    #    不可能与事实行不一致 —— 判据别建在可篡改的输入上。meta 就是上面按 fact_id 取的
    #    那一行（已校验 kind=='fact' 且同 (task_id, agent)），run_id 直接取它的。
    inherited_run_id = meta["run_id"]
    try:
        with connect(path) as conn:
            cur = conn.execute(
                "INSERT INTO agent_verdicts "
                "(task_id, agent, amends, amend_reason, verdict_json, content_sha256, "
                " created_at, kind, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (a.task_id, a.agent, fact_id, f"assessment stance={a.stance}", blob,
                 hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                 now_cn().isoformat(), "assessment", inherited_run_id),
            )
            return int(cur.lastrowid)
    except sqlite3.IntegrityError as e:
        if "ux_verdict_amends_linear" not in str(e) and "agent_verdicts.amends" not in str(e):
            raise
        raise ValueError(
            f"fact #{fact_id} 已经有一个 assessment 了 —— 一份事实最多一个判断（线性修订）。\n"
            "  要改判断：在已有 assessment 的基础上再修一次，不要指回同一条 fact。"
        ) from e


def load_outcome(
    verdict_id: int, *, path: pathlib.Path | str | None = None
) -> AgentOutcome | None:
    """按 id 取回一个 `AgentOutcome`（FactBundle + 可选 AgentAssessment）。找不到返回 None。

    🔴 三种落库形状都能读回（读路径宽，§9）：
      · 旧 AgentVerdict → `LegacyAdapter.to_outcome` 拆成新三型；
      · fact 行 → `AgentOutcome(fact, None)`；
      · assessment 行 → 顺着 `amends` 找到它的 fact，拼成完整 outcome。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT verdict_json, amends, kind FROM agent_verdicts WHERE verdict_id=?",
            (int(verdict_id),),
        ).fetchone()
        if row is None:
            return None
        kind, d = row["kind"], json.loads(row["verdict_json"])
        if kind == "fact":
            return AgentOutcome(fact=FactBundle.from_dict(d), assessment=None)
        if kind == "assessment":
            frow = conn.execute(
                "SELECT verdict_json FROM agent_verdicts WHERE verdict_id=?",
                (int(row["amends"]),),
            ).fetchone()
            if frow is None:
                raise ValueError(
                    f"assessment #{verdict_id} 的 fact #{row['amends']} 找不到了 —— "
                    "assessment 与 fact 的链断了（只追加表本不该发生）。")
            return AgentOutcome(fact=FactBundle.from_dict(json.loads(frow["verdict_json"])),
                                assessment=AgentAssessment.from_dict(d))
    # 旧 AgentVerdict（kind NULL/'verdict'）—— 出了 with 块再拆，避免嵌套连接。
    return LegacyAdapter.to_outcome(AgentVerdict.from_dict(d))


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
    runtime_run_id: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """记一次 Agent 执行，返回 `ledger_id`（账本行号，批 J-II 从 `run_id` 改名）。

    `runtime_run_id`（可选）：运行时返回的真实 spawn id（`SpawnHandle.runtime_run_id`，
    即 OpenClaw `subagent_runs.run_id`）。在线路径由编排器传入，落库后
    `tools/verify/spawn_check.py` 拿它与运行时做结构化 join（比原先的文本匹配硬）。
    历史行 / 回放路径为 None ⇒ 该列 NULL，核验退回按决策号的 LIKE 判据。

    🔴 **这不是 spawn 的证明**（外部评审 P2-3）
    ------------------------------------------
    BigA 自己的代码就在写这张表 —— 人手工跑一遍 `synthesize.py`，
    它照样多出几行。它能证明的只有「**我们记下了**一次执行」。

    要证明「运行时真的起过那个 Agent」，看**运行时自己的库**：
    `subagent_runs` / `task_runs`。判据在 `tools/verify/spawn_check.py`
    （两份独立记录都齐才算），排查用 `tools/verify/agent_trace.py`。

    ⚠️ 这段话曾经写的是相反的：「这张表是……的唯一凭证」。
       P2-3 的修订把新结论插在了**旧结论上面**、没删旧的，
       于是同一个 docstring 里两句话互相打脸 —— 谁先读到哪句就信哪句。
       深度评审在三处找到这套旧口径。
       **改口径要去删旧的那一句，不是在它上面再写一句。**

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
                missing_count, elapsed_ms, error, started_at, finished_at,
                runtime_run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision_id, task_id, agent, model, status, verdict, missing_count,
             elapsed_ms, error, started_at, finished_at, runtime_run_id),
        )
        return int(cur.lastrowid)


def record_verdict_run(
    v: AgentVerdict,
    *,
    started_at: str,
    finished_at: str,
    decision_id: str | None = None,
    model: str | None = None,
    runtime_run_id: str | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """从一个 `AgentVerdict` 直接记账，省得调用方手抄字段（抄错就是口径分裂）。

    `runtime_run_id` 透传给 `record_agent_run` —— 见那里的说明。
    """
    return record_agent_run(
        task_id=v.task_id, agent=v.agent, status=v.status, verdict=v.verdict,
        missing_count=len(v.missing), elapsed_ms=v.elapsed_ms,
        decision_id=decision_id, model=model, runtime_run_id=runtime_run_id,
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
    sql += " ORDER BY ledger_id DESC LIMIT ?"
    args.append(limit)
    with connect(path, readonly=True) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ──────────────────────────────────────────────────────── raw_market_snapshot


def payload_sha256(payload: Any) -> str:
    """解析后 payload 的内容哈希 —— raw 层 `content_sha256` 的**旧口径**（schema < v13）。

    🔴 **批 I（schema v13）起，`content_sha256` 不再基于它算**，改用
    `raw_text_sha256`（见下）。原因：这个函数把 payload 先 `json.dumps(sort_keys=True)`
    再哈希 —— 那是我们**自己重排后**的字节，证明不了数据源发来的是什么（键序 / 空白 /
    浮点表示全丢了，上游改序列化而没改数据也看不见）。

    为什么留着（没删）：raw 层只追加，**v13 之前落的行**其 `content_sha256` 就是用这个
    函数算的；要重新核对那些历史行，只能继续用它。`tests/fixtures/payload-sha256-vectors.json`
    钉死它的输出格式（那份向量在本次改动之前生成，任何时候都必须能重新对上）。

    🔴 **不加 `separators`。** 历史哈希已经建立在它当前的输出格式上 —— 改格式会静默
    改变所有历史哈希（两串 sha 都「看起来正常」，只是再也对不上当时存的那个）。
    只加 `allow_nan=False`：对不含 NaN/Infinity 的历史数据输出逐字节不变。
    """
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   allow_nan=False).encode("utf-8")
    ).hexdigest()


def raw_text_sha256(raw_text: str) -> str:
    """数据源**原始响应文本**的内容哈希 —— raw 层 `content_sha256` 的口径（schema v13 起）。

    `save_raw_snapshot` 用它算入库的 `content_sha256`，采集层用它给 `Evidence.raw_hash`
    赋值。两边必须是同一个函数：各算各的、某天口径改了一处，`raw_hash` 就再也对不上
    raw 层 —— 而那种失效是静默的（两串 sha 都「看起来正常」）。

    🔴 与 `payload_sha256` 的根本区别：这里哈希的是**数据源发来的字节**（`get_text`
    解码后的那段文本），不是我们 `json.dumps` 重排后的对象。于是两次采到「数据相同
    但序列化不同」（键序 / 空白不同）的响应，会得到**不同**的 `content_sha256` ——
    这正是批 I 要的：指纹能证明源字节，也能分辨上游改了序列化而没改数据。

    ⚠️ 「文本级」而非「字节级」：`raw_text` 已是 `get_text` 按 `encoding` 解码后的
    `str`。这里对它的 utf-8 编码取 sha256 —— 编码假设是否正确是上游 `get_text` 的
    独立问题，不在这一层解决（批 I 明确排除）。
    """
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def save_raw_snapshot(
    *,
    source: str,
    as_of: str,
    retrieved_at: str,
    payload: Any,
    raw_text: str,
    path: pathlib.Path | str | None = None,
) -> int:
    """原样落盘一份采集结果，返回 `snapshot_id`。

    不做去重 —— 采了两次就是两个事实，都留着。

    两列各存什么（批 I）：
      · `payload_json` —— 解析后的**规范表示**（`json.dumps(sort_keys=True)`）。给消费
        方回读用（`load_raw_snapshot()["payload"]` 反序列化回对象）。它是**归一化过的**，
        不是数据源的原始字节。
      · `raw_text` —— 数据源发来的**原始响应文本**（`get_json_and_text` / `get_text`
        交出的那段）。`content_sha256` 基于**它**算（`raw_text_sha256`），指纹因此证明
        的是源字节，不是我们 `sort_keys` 重排后的字节。

    🔴 `raw_text` 必填且非空：一个 collector 漏传（None / 空串）必须当场报错，不能静默
    往新列里塞个空值再照常出卡 —— 那正是批 I 探针 P4 要防的静默破坏。

    ⚠️ `content_sha256` 是**整段响应文本**的哈希，不是「市场数据」的哈希。很多接口的
    响应里带易变字段（服务器编号、请求序号等），因此内容相同的两次采集 sha 通常也不同。
    它能回答「这两条记录的原始文本是否完全一样」，**不能**回答「这两次采到的市场数据
    是否一致」—— 后者需要先归一化，而归一化规则各源特有，不属于通用存储层。
    """
    if not isinstance(raw_text, str) or not raw_text:
        raise ValueError(
            f"save_raw_snapshot(raw_text=...) 必须是非空字符串，收到 {raw_text!r}。\n"
            "  它是数据源发来的原始响应文本，content_sha256 基于它算（批 I）——\n"
            "  漏传 = raw 层的指纹又退回「我们自己重排后的对象」，正是这一批要修的问题。\n"
            "  采集路径应从 get_json_and_text / get_text 一路把原文带到这里。")
    # 🔴 严格 JSON（设计文档 §6 A4）：payload 没有契约对象、没有不变量，能查的只有
    #    「是不是合法 JSON」。`allow_nan=False` 让 NaN/Infinity 在这行（**早于
    #    `connect()`**）就地抛错 —— 批 I 之前这道 NaN 守卫由 `payload_sha256(payload)`
    #    顺带做，现在 content_sha256 改走 `raw_text_sha256`，就得由这行自己带上。
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    sha = raw_text_sha256(raw_text)
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO raw_market_snapshot
               (source, as_of, retrieved_at, payload_json, raw_text, content_sha256, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (source, as_of, retrieved_at, blob, raw_text, sha, now_cn().isoformat()),
        )
        return int(cur.lastrowid)


def load_raw_snapshot(
    snapshot_id: int, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """读回一行 raw 快照。返回的 dict 含解析后的 `payload` 与原始 `raw_text`。

    🔴 `payload` 字段**始终是解析后的对象**（`json.loads(payload_json)`），不是字符串
    —— `_snapshot.coordinator.read_index_daily` 对它做 `len()` / 切片，批 I 加了原始
    文本列之后这条语义必须不变（探针 P3）。原始响应文本单独走 `raw_text` 字段
    （v13 之前落的行该字段为 `None`：那时还没这一列，raw 层只追加、不回填）。
    """
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM raw_market_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


# ─────────────────────────────────────────────────────────── fact_trading_calendar


def save_trading_calendar(
    *,
    source: str,
    as_of: str,
    retrieved_at: str,
    days: Iterable[tuple[str, bool]],
    snapshot_id: int | None = None,
    path: pathlib.Path | str | None = None,
) -> int:
    """把一份**归一化后**的交易日历落进 `fact_trading_calendar`，返回写入行数。

    Args:
        days: `(trade_date, is_open)` 序列，`trade_date` 是 ``YYYYMMDD``，
            每个自然日一条（**含休市日** `is_open=False`）。完整性由 Provider 的
            parse 层保证 —— 缺日在 `parse_trading_calendar` 就抛错，到不了这里。
        snapshot_id: 这份日历归一化自哪一行 raw（`raw_market_snapshot.snapshot_id`）。
            让事实能一路溯回数据源原始字节。手工/测试可不传。

    🔴 **只追加，从不 UPDATE 旧行。** 交易所事后补发调整（临时增/删一个交易日），
       再调一次本函数写入**更晚 `retrieved_at` 的新行**；读的一方
       （`is_trading_day`）按 `retrieved_at` 取最新一条。覆盖旧行 = 没法回答
       「我们当时看到的日历是什么」，与 raw 层同一条 L-8 先例。
    """
    rows = [(d, bool(o)) for d, o in days]
    if not rows:
        raise ValueError(
            "save_trading_calendar: days 为空 —— 空日历没有意义。\n"
            "  上游 parse_trading_calendar 应已在「该月缺日/未发布」时抛错，"
            "不该把一份空日历送到这里。")
    created = now_cn().isoformat()
    with connect(path) as conn:
        conn.executemany(
            """INSERT INTO fact_trading_calendar
               (trade_date, is_open, source, as_of, retrieved_at, snapshot_id, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            [(d, 1 if o else 0, source, as_of, retrieved_at, snapshot_id, created)
             for d, o in rows],
        )
    return len(rows)


def is_trading_day(
    trade_date: str, *, path: pathlib.Path | str | None = None
) -> bool | None:
    """某个自然日（``YYYYMMDD``）开不开市 —— 查 `fact_trading_calendar`。

    Returns:
        `True`  已知交易日；
        `False` 已知休市日（周末或法定节假日）；
        `None`  **日历没覆盖到这一天**（还没抓、或问的日期超出已抓范围）。

    🔴 `None` ≠ `False`（红线 R-3）。调用方拿到 `None` 必须自己决定回退 ——
       `market_is_open` 回退到 weekday 判据，绝不把「查不到」当成「休市」：
       那会在真实交易日里以为休市，是比「多报一条缺失项」危险得多的方向。

    取最新一条：同一天可能有多行（交易所补发调整 ⇒ 更晚 `retrieved_at` 的新行），
    按 `retrieved_at` 降序取第一条，与 `save_trading_calendar` 的「只追加、不覆盖」配对。

    🔴 库不存在 / 表不存在 ⇒ 返回 `None`（视作「没覆盖到」），**不抛错**：
       全新 clone 里日历表本就是空的（`data/biga.db` 是 .gitignore'd 的），
       市场判据该回退而不是崩。
    """
    try:
        with connect(path, readonly=True) as conn:
            row = conn.execute(
                """SELECT is_open FROM fact_trading_calendar
                   WHERE trade_date=?
                   ORDER BY retrieved_at DESC, fact_id DESC LIMIT 1""",
                (trade_date,),
            ).fetchone()
    except (StoreNotInitialised, sqlite3.OperationalError):
        return None
    return bool(row[0]) if row else None


# ──────────────────────────────────────────────────────────────── evidence_sets


def save_evidence_set(
    *,
    evidence_set_id: str,
    decision_id: str | None,
    manifest: dict[str, Any],
    run_id: str | None = None,
    path: pathlib.Path | str | None = None,
) -> str:
    """登记一次数据冻结（`SnapshotCoordinator` 冻结完调它），返回 `evidence_set_id`。

    🔴 存储层对 `manifest` 的结构**不做假设** —— 它只负责严格 JSON 落库。
    manifest 长什么样、怎么反查回 `raw_market_snapshot`，是冻结方
    （`_snapshot.SnapshotCoordinator`，批 D）的事：那一层才知道自己冻的是
    日线还是别的。存储层若也内嵌一份「manifest 该有哪些键」，就成了第二处
    要跟着 manifest 演进的地方（L-3）。

    ⚠️ 用 `_canonical_dumps`（含 `allow_nan=False`，A4 严格 JSON）。这里的
    `separators` 无所谓：manifest 不是 `raw_hash`，不参与「Evidence.raw_hash ↔
    raw 层」那条历史哈希对应，改格式不会静默打穿任何东西。
    """
    if not isinstance(evidence_set_id, str) or not evidence_set_id.strip():
        raise ValueError(
            f"evidence_set_id 必须是非空字符串（它是主键），收到 {evidence_set_id!r}。"
            "  用 _contract.new_evidence_set_id() 铸一个，不要自己拼。")
    if not isinstance(manifest, dict):
        raise TypeError(
            f"manifest 必须是 dict，收到 {type(manifest).__name__} —— "
            "冻结登记要能被反查，一段自由文本不行。")
    blob = _canonical_dumps(manifest)
    now = now_cn().isoformat()
    try:
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO evidence_sets "
                "(evidence_set_id, decision_id, frozen_at, manifest_json, created_at, run_id) "
                "VALUES (?,?,?,?,?,?)",
                (evidence_set_id, decision_id, now, blob, now, run_id),
            )
    except sqlite3.IntegrityError as e:
        if "evidence_sets.evidence_set_id" not in str(e) and "evidence_set_id" not in str(e):
            raise
        raise ValueError(
            f"evidence_set_id {evidence_set_id!r} 已存在 —— 冻结登记只追加，"
            "不复用旧号。每次冻结 new_evidence_set_id() 铸一个新的。") from e
    return evidence_set_id


def load_evidence_set(
    evidence_set_id: str, *, path: pathlib.Path | str | None = None
) -> dict[str, Any] | None:
    """取回一次冻结登记，`manifest` 已从 JSON 解析回 dict。找不到返回 None。"""
    with connect(path, readonly=True) as conn:
        row = conn.execute(
            "SELECT evidence_set_id, decision_id, frozen_at, manifest_json, created_at "
            "FROM evidence_sets WHERE evidence_set_id=?",
            (evidence_set_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["manifest"] = json.loads(d.pop("manifest_json"))
    return d
