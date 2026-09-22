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

from _contract import (
    AgentVerdict,
    DecisionCard,
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

    Args:
        replay_of: 回放时填被回放记录的 `record_id`；在线路径留空。
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
    """
    problems: list[str] = []
    for ref in card.input_verdict_refs:
        meta = load_verdict_meta(ref.verdict_id, path=path)
        if meta is None:
            problems.append(
                f"[{ref.agent}] verdict_id={ref.verdict_id} 在 agent_verdicts "
                f"里已经找不到了 —— 这张卡引用的原件消失了")
            continue
        if meta["content_sha256"] != ref.content_sha256:
            problems.append(
                f"[{ref.agent}] verdict_id={ref.verdict_id} 的哈希对不上："
                f"卡上记的是 {ref.content_sha256[:12]}…，"
                f"agent_verdicts 里现在是 {meta['content_sha256'][:12]}… —— "
                f"这条原件在合成之后被改变过")
    return problems


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

    🔴 **不加 `separators`。** 这个函数早于 `_canonical_dumps` 存在，
    历史哈希已经建立在它当前的输出格式上 —— 改格式会静默改变所有历史哈希
    （两串 sha 都「看起来正常」，只是再也对不上当时存的那个）。
    `tests/fixtures/payload-sha256-vectors.json` 钉死这一点：那份向量
    是在本次改动**之前**用当时的实现生成的，任何时候都必须能重新对上。

    只加 `allow_nan=False`：对不含 NaN/Infinity 的历史数据，输出逐字节不变；
    只有本来就不该写进去的值，才会从「静默写入一个 Python 能读、
    RFC 8259 不认的裸 NaN」变成「在写入前就报错」。
    """
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   allow_nan=False).encode("utf-8")
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
    # 🔴 严格 JSON（设计文档 §6 A4）：raw payload 没有契约对象、没有不变量，
    #    这里能查的只有「是不是合法 JSON」——`payload_sha256` 已经
    #    `allow_nan=False`，且这行**在 `connect()` 之前**执行，
    #    NaN/Infinity 会在任何 DB IO 发生之前就地抛错，不需要再加一道。
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


# ──────────────────────────────────────────────────────────────── evidence_sets


def save_evidence_set(
    *,
    evidence_set_id: str,
    decision_id: str | None,
    manifest: dict[str, Any],
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
                "(evidence_set_id, decision_id, frozen_at, manifest_json, created_at) "
                "VALUES (?,?,?,?,?)",
                (evidence_set_id, decision_id, now, blob, now),
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
