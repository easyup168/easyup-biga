"""SnapshotCoordinator —— 冻结一次、多处读（设计文档 §6 批 D 第一段）。

要解决的问题（已实测复现，不是假想）
------------------------------------
`skills/_sources/sina.py::fetch_index_daily` 被三个 skill **各自独立调用**：

    market-calc     fetch_index_daily(symbol, bars=25)    # sh + sz
    sector-calc     fetch_index_daily("sh000001", bars=2)
    technical-calc  fetch_index_daily("sh000001", bars=120)

每个 skill 自己联网、自己落一行 `raw_market_snapshot`。三次网络调用理论上应该
拿到同一份数据，但没有任何机制保证 —— 「所有 Specialist 看的是同一份数据」
这句话（设计文档 §4 `evidence_set_id` 那一行）目前**无法验证**。

这一层怎么解决：把「抓取」与「读取」拆开
----------------------------------------
    freeze_index_daily(decision_id, symbols, bars) -> evidence_set_id
        每个 symbol 在这次决策里**只真实抓一次**（用调用方给的 bars 取全量），
        原样落 `raw_market_snapshot`，登记一行 `evidence_sets`。

    read_index_daily(evidence_set_id, symbol, bars) -> IndexDaily
        从已冻结的 raw **切片**出调用方要的根数，**不联网**。
        sector 要 2 根、market 要 25 根、technical 要 120 根，都从同一份底层
        数据切 —— 是**同一次抓取**，不是三份偶然相等的结果。

🔴 这一批（D-I）不改变任何 Specialist 的行为
--------------------------------------------
market/sector/technical 仍各自调 `fetch_index_daily`，跟今天一模一样。这里只是
在旁边把机制建好、验实。真正让 Specialist 改口读冻结快照是 **D-II**，它会改变
现有 skill 的实际行为，需要先有这层被验实的地基。

⇒ 因此现在 `freeze_index_daily` **还没有生产调用方**（编排器没接它）。它的读取方
  是 `read_index_daily` 本身 + 探针（`tests/test_snapshot.py`）。调度层的消费方
  在 D-II 出现。这是分批施工里一段**显式登记**的空档（与批 B 建 `evidence_sets`
  表时「只建表、无生产方」同形），不是零消费方死配置（L-1）——见
  `TODO.md`「批 D 施工空档」。误删这层不会有测试变红，但会把一段被记录在案的
  中间态变成「谁建的、干嘛的」都答不上来的孤儿。
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable
from typing import Any, Callable

from _contract import new_evidence_set_id, now_cn
from _sources import IndexDaily, fetch_index_daily, parse_index_daily
from _store import (
    load_evidence_set,
    load_raw_snapshot,
    payload_sha256,
    save_evidence_set,
    save_raw_snapshot,
)

__all__ = ["SnapshotCoordinator", "SnapshotReadError", "MANIFEST_KIND"]

#: 这一批只冻日线。manifest 用它标记冻的是什么 —— 将来 breadth/pool 冻结进来
#: （设计文档 §6 批 D 的后两段）时各有各的 kind，read 端据此拒绝张冠李戴。
MANIFEST_KIND = "index_daily"

Fetcher = Callable[..., IndexDaily]


class SnapshotReadError(RuntimeError):
    """读冻结快照时的**可判断**失败：symbol 不在这次冻结里、要的根数超过冻结的
    根数、或 evidence set 不存在。

    单独一个类型、不复用 `SourceError`：它不是「数据源不可用」，而是
    「你问的东西这次决策没冻过 / 冻得不够」。调用方（D-II 的编排器）对两者的
    处理方式不同 —— 前者重试或记 missing，后者是编排把 bars 传小了的 bug。
    """


class SnapshotCoordinator:
    """一次决策里指数日线的冻结与读取。

    无状态（除了注入的 fetcher 与 db 路径）—— 真相全在库里（`raw_market_snapshot`
    + `evidence_sets`）。可以每次决策 new 一个，也可以共用一个，行为一致。
    """

    def __init__(
        self,
        *,
        fetcher: Fetcher | None = None,
        path: pathlib.Path | str | None = None,
    ) -> None:
        """
        Args:
            fetcher: 真实抓取函数，默认 `_sources.fetch_index_daily`。
                🔴 可注入的唯一理由是**可测**：离线测试（`conftest.py` 的禁网
                围栏）传一个返回固定数据、并且**记调用次数**的桩，就能断言
                「冻结一次、多处读」里那个「一次」是真的一次（探针 P1）。
                这与 L-12 的「真实但一次性的外部状态入口」是同一招 ——
                不 mock 掉被测逻辑本身，只替换最外层那个会出网的边界。
            path: 库路径，透传给所有 `_store` 调用（测试指向 tmp 库）。缺省走
                `BIGA_DB_PATH` / 默认库。
        """
        self._fetch: Fetcher = fetcher or fetch_index_daily
        self._path = path

    # ── 冻结 ────────────────────────────────────────────────────────────────

    def freeze_index_daily(
        self, decision_id: str | None, symbols: Iterable[str], *, bars: int
    ) -> str:
        """把这次决策要用到的指数日线**冻结一次**，返回 `evidence_set_id`。

        每个 symbol 只真实抓一次（用 `bars` 取全量），原样落 raw，登记一行
        `evidence_sets`。重复的 symbol 去重（只抓一次）。

        Args:
            symbols: 指数代码序列，如 ``["sh000001", "sz399106"]``。
            bars: 取多少根 —— 调用方（D-II 的编排器）传**所有消费者里最大的
                那个**，这样每个读取方都能从这一份里切出自己要的根数。
                🔴 今天 `sh000001` 的最大消费者是 **technical（120 根）**，
                不是 market 的 25。谁把这层接进编排器，`bars` 就得取 120，
                否则 technical 读 120 会 fail-closed（见 `read_index_daily`）。
                这条写进 `TODO.md`「批 D-II 输入」。
        """
        if not isinstance(bars, int) or bars <= 0:
            raise ValueError(f"bars 必须是正整数，收到 {bars!r}")
        # 去重但**保序**：dict.fromkeys 比 set 稳定，日志/manifest 里 symbol
        # 顺序可预期，排查时不必每次都重新排。
        uniq = list(dict.fromkeys(symbols))
        if not uniq:
            raise ValueError("freeze_index_daily 至少要一个 symbol —— 空冻结没有意义")

        entries: dict[str, dict[str, Any]] = {}
        for symbol in uniq:
            retrieved = now_cn()
            d = self._fetch(symbol, bars=bars)      # ← 唯一的真实抓取点
            source = f"sina:kline/{symbol}"
            as_of = d.server_as_of or retrieved
            snapshot_id = save_raw_snapshot(
                source=source,
                as_of=as_of.isoformat(),
                retrieved_at=retrieved.isoformat(),
                payload=d.raw,
                path=self._path,
            )
            entries[symbol] = {
                "snapshot_id": snapshot_id,
                "source": source,
                # 与 Evidence.raw_hash 同一个函数（唯一实现），冻结集因此能声明
                # 「我这份 raw 的指纹是这个」，将来核对用得上。
                "content_sha256": payload_sha256(d.raw),
                "bar_count": len(d.bars),
                "trade_date": d.trade_date,
            }

        evidence_set_id = new_evidence_set_id()
        manifest = {
            "kind": MANIFEST_KIND,
            "frozen_bars": int(bars),
            # 🔴 每个 symbol 记 snapshot_id —— manifest 必须能被**反向走通**回
            #    raw 层（探针 P5），不是一段只用于展示的自由文本。
            "symbols": entries,
        }
        save_evidence_set(
            evidence_set_id=evidence_set_id,
            decision_id=decision_id,
            manifest=manifest,
            path=self._path,
        )
        return evidence_set_id

    # ── 读取 ────────────────────────────────────────────────────────────────

    def read_index_daily(
        self, evidence_set_id: str, symbol: str, *, bars: int
    ) -> IndexDaily:
        """从已冻结的数据切出 `symbol` 最近 `bars` 根，返回 `IndexDaily`。**不联网。**

        🔴 切片而不是重抓：这正是「冻结一次、多处读」的读端。三个消费者按不同
        根数读同一个 symbol，走的都是这里，底层那次网络调用发生在
        `freeze_index_daily`、**只发生过一次**。

        🔴 fail-closed：要的根数超过冻结的根数 ⇒ 抛 `SnapshotReadError`，绝不
        静默返回更少的根数。静默返回更少 = 上层拿到一个「看起来正常、其实不够」
        的结果，是 R-3 / L-2 的形状（算不出来必须说算不出来，不 fail-open）。
        """
        if not isinstance(bars, int) or bars <= 0:
            raise ValueError(f"bars 必须是正整数，收到 {bars!r}")
        entry = self._entry(evidence_set_id, symbol)
        snap = load_raw_snapshot(int(entry["snapshot_id"]), path=self._path)
        if snap is None:
            raise SnapshotReadError(
                f"evidence set {evidence_set_id} 记着 {symbol} 冻在 "
                f"snapshot_id={entry['snapshot_id']}，但那行 raw_market_snapshot "
                f"找不到了 —— 冻结登记与 raw 层对不上（raw 层只追加，这本不该发生）。")
        raw = snap["payload"]
        if bars > len(raw):
            raise SnapshotReadError(
                f"{symbol} 这次只冻了 {len(raw)} 根，读不出 {bars} 根。\n"
                f"  冻结时的 bars 要取所有消费者里最大的那个 —— "
                f"freeze_index_daily(..., bars=至少 {bars})。")
        # 切片后重解析，走的是与 fetch_index_daily 同一套解析（parse_index_daily，
        # 唯一实现）—— 切片仍升序、无重复，不会新触发那两条断言。
        return parse_index_daily(symbol, raw[-bars:])

    # ── 反查（manifest 形状的判据）──────────────────────────────────────────

    def frozen_snapshot_ids(self, evidence_set_id: str) -> list[int]:
        """反查：这个 evidence set 冻结了哪几行 `raw_market_snapshot`。

        🔴 这是探针 P5 的判据 —— manifest 必须能被**反向走通**回 raw 层，不是
        「有个字段存在」就算数。冻结方（本类）定义 manifest 形状，所以这条反查
        放在这里；存储层（`_store.load_evidence_set`）对 manifest 结构不做假设。
        """
        symbols = self._manifest_symbols(evidence_set_id)
        return [int(e["snapshot_id"]) for e in symbols.values()]

    def frozen_content_sha256(self, evidence_set_id: str, symbol: str) -> str:
        """这个 evidence set 里 `symbol` 那份**整份** raw 的 `content_sha256`。

        🔴 批 D-II 要用它给 `Evidence.raw_hash` 赋值：改口读冻结快照的 Specialist，
        `raw_hash` 应指向**冻结集登记的**这份哈希（整份 raw 的指纹），**不能**对自己
        读到的那一截（`read_index_daily` 切出来的 N 根）重新 `payload_sha256`——
        不同消费者读不同根数（sector 2 / market 25 / technical 120），对切片重算会得到
        三个不同的哈希，而它们本该指向同一份冻结数据。用这一份，三者天然相等
        （探针 P4），risk 的 CROSS_CHECK 才能靠「raw_hash 是否相同」判断两个 Specialist
        是不是真的共享了同一份数据。

        这是**新增的只读访问器**，不改 `read_index_daily` 的签名（D-I 已评审通过）——
        读端要的这份哈希，D-I 的读接口没有暴露，属于分发提示词说的「签名接不上」。
        """
        symbols = self._manifest_symbols(evidence_set_id)
        entry = symbols.get(symbol)
        if entry is None:
            raise SnapshotReadError(
                f"{symbol} 不在 evidence set {evidence_set_id} 里。这次冻的是：{sorted(symbols)}。")
        return str(entry["content_sha256"])

    def _manifest_symbols(self, evidence_set_id: str) -> dict[str, Any]:
        es = load_evidence_set(evidence_set_id, path=self._path)
        if es is None:
            raise SnapshotReadError(
                f"evidence_set_id={evidence_set_id!r} 不存在 —— 无法反查它冻了哪些 raw。")
        return es["manifest"].get("symbols", {})

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _entry(self, evidence_set_id: str, symbol: str) -> dict[str, Any]:
        es = load_evidence_set(evidence_set_id, path=self._path)
        if es is None:
            raise SnapshotReadError(
                f"evidence_set_id={evidence_set_id!r} 不存在。先 freeze_index_daily。")
        manifest = es["manifest"]
        if manifest.get("kind") != MANIFEST_KIND:
            raise SnapshotReadError(
                f"evidence set {evidence_set_id} 冻的是 {manifest.get('kind')!r}，"
                f"不是 {MANIFEST_KIND!r} —— read_index_daily 只读日线冻结。")
        entry = manifest.get("symbols", {}).get(symbol)
        if entry is None:
            frozen = sorted(manifest.get("symbols", {}))
            raise SnapshotReadError(
                f"{symbol} 不在 evidence set {evidence_set_id} 里。这次冻的是：{frozen}。")
        return entry
