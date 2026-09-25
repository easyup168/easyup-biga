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

⇒ 当时（D-I）`freeze_index_daily` **还没有生产调用方**，读取方只有
  `read_index_daily` 本身 + 探针。那是分批施工里一段**显式登记**的空档
  （与批 B 建 `evidence_sets` 表时「只建表、无生产方」同形），不是零消费方
  死配置（L-1）。

⏩ **2026-09-25 更正：那段空档早就结束了。** 批 D-II 已把它接进生产 ——
  `skills/decision-card/scripts/orchestrator.py` 在 Stage 1 之前调用它冻结
  sh/sz@120 根，market/sector/technical 带 `--evidence-set-id` 读同一份。

  🔴 本段此前一直写着「现在还没有生产调用方」，**而它已经不成立很久了**。
  这类漂移的危害不是读者少知道一件事，是**读者据此做决定**：一份说自己
  没有生产调用方的模块，看起来是可以随便改签名的。
  （外部设计 `BigA Data Architecture v1` 独立重扫源码时也点出了这处，
  列在它的 gap 表 `Docs | coordinator 注释有历史漂移 | CLEANUP`。）
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable
from typing import Any, Callable

from easyup_biga.data.datasets.index_daily import IndexDailyDatasetBridge
from easyup_biga.data.provider_registry import ProviderNotRegistered
from easyup_biga.domain import new_evidence_set_id, now_cn
from easyup_biga.providers import IndexDaily, fetch_index_daily, parse_index_daily
from easyup_biga.persistence import (
    load_evidence_set,
    load_raw_snapshot,
    payload_sha256,
    raw_text_sha256,
    save_evidence_set,
    save_raw_snapshot,
)

__all__ = ["SnapshotCoordinator", "SnapshotReadError", "MANIFEST_KIND"]

#: 这一批只冻日线。manifest 用它标记冻的是什么 —— 将来 breadth/pool 冻结进来
#: （设计文档 §6 批 D 的后两段）时各有各的 kind，read 端据此拒绝张冠李戴。
MANIFEST_KIND = "index_daily"

Fetcher = Callable[..., IndexDaily]


def _verify_snapshot_hash(snap: dict[str, Any]) -> None:
    """读回 raw 快照时重算指纹并比对，对不上就 fail-closed（批 R，评审 E-20.3）。

    🔴 以前 `content_sha256` 只在**写入时**算过一次，此后从没有人验过 ——
    一个从不被检验的指纹，和没有指纹的区别只在于它让人放心。

    两种口径，由 `raw_text` 是否为 NULL 决定（schema v13 的分界，raw 层只追加、
    旧行不回填）：

    * `raw_text` 有值 → `raw_text_sha256`（原始响应文本）
    * `raw_text` 为 NULL → `payload_sha256`（解析后重排的对象，v13 之前的口径）

    ⚠️ 这里**没有「验不了」这一档**，因此也不存在 R-3 要防的静默 fail-open：
    实测 2026-09-24 生产库 376 行 raw，55 行走新口径、321 行走旧口径，
    **两边各自全部一致，0 例外**。哪天真出现验不了的行，下面那个 else 会直接抛。
    """
    sha = snap.get("content_sha256")
    raw_text = snap.get("raw_text")
    if raw_text is not None:
        actual, how = raw_text_sha256(raw_text), "raw_text_sha256(原始响应文本)"
    elif snap.get("payload") is not None:
        actual, how = payload_sha256(snap["payload"]), "payload_sha256(v13 之前的旧口径)"
    else:
        raise SnapshotReadError(
            f"snapshot_id={snap.get('snapshot_id')} 既没有 raw_text 也没有 payload，"
            f"指纹无法重算 —— 算不出来就是算不出来，不当作通过（R-3）。")
    if actual != sha:
        raise SnapshotReadError(
            f"snapshot_id={snap.get('snapshot_id')} 的内容指纹对不上 —— raw 层这一行\n"
            f"  落库时记的  content_sha256 = {sha}\n"
            f"  现在重算得到              = {actual}\n"
            f"  口径：{how}\n"
            f"raw 层是只追加的，这本不该发生。要么库被改过，要么哈希口径变了而"
            f"历史行没跟着走。**不要**绕过这条检查去读它：回放的地基就是这份字节。")


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
        dataset_bridge: "IndexDailyDatasetBridge | None" = None,
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
            dataset_bridge: P3-2 的 DatasetSnapshot 桥。可注入只为隔离测试，
                **不是**一条「出问题就绕过」的退路 —— 冻结成功而血缘没登记，
                是半发布状态，比没有血缘更难查。
        """
        self._fetch: Fetcher = fetcher or fetch_index_daily
        self._path = path
        #: P3-2：把冻结结果桥接成通用 DatasetSnapshot。可注入，便于隔离测试。
        self._dataset_bridge = dataset_bridge or IndexDailyDatasetBridge(path=path)
        #: 注入 fetcher 是测试/调试缝。此时若它自报一个数据平台没登记的 provider，
        #: 说明这次冻结不属于任何已注册 dataset ⇒ 跳过血缘登记而不是报错。
        #: 生产（默认 fetcher）始终严格。
        self._bridge_allows_unregistered = fetcher is not None and dataset_bridge is None

    # ── 冻结 ────────────────────────────────────────────────────────────────

    def freeze_index_daily(
        self, decision_id: str | None, symbols: Iterable[str], *, bars: int,
        run_id: str | None = None,
    ) -> str:
        """把这次决策要用到的指数日线**冻结一次**，返回 `evidence_set_id`。

        每个 symbol 只真实抓一次（用 `bars` 取全量），原样落 raw，登记一行
        `evidence_sets`。重复的 symbol 去重（只抓一次）。

        Args:
            symbols: 指数代码序列，如 ``["sh000001", "sz399106"]``。
            run_id: 🔴 批 J-I（可选、默认 None）：这次冻结属于哪次编排执行尝试
                （`RunContext.run_id`）。编排器直接把 `ctx.run_id` 传进来（Python 内部
                调用，不经 CLI），登记进 `evidence_sets.run_id`。手工调用不传就是 None。
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
            d = self._fetch(symbol, bars=bars)      # ← 唯一的真实抓取点
            # 🔴 E-20.1：`retrieved_at` 记在**抓取完成之后**，不是发起之前。
            #    以前这一行在 `_fetch` 上面 —— 记的是「我打算去抓」的时刻。
            #    抓取耗时全被算进「数据有多新」：一次 30 秒的慢响应，落库的
            #    retrieved_at 比真正拿到数据早 30 秒，证据因此显得比实际新鲜。
            #    这个方向是**单向的** —— 只会高估新鲜度，不会低估。
            retrieved = now_cn()
            # 🔴 E-20.2：出处问 provider 要，不在这里拼。fetcher 是可注入的，
            #    硬编码 "sina" 会让任何替换后的数据源都被记成 sina。
            source = d.source
            as_of = d.server_as_of or retrieved
            snapshot_id = save_raw_snapshot(
                source=source,
                as_of=as_of.isoformat(),
                retrieved_at=retrieved.isoformat(),
                payload=d.raw,
                raw_text=d.raw_text,
                path=self._path,
            )
            entries[symbol] = {
                "snapshot_id": snapshot_id,
                "source": source,
                # 与 Evidence.raw_hash / raw 层 content_sha256 同一个口径（批 I：
                # raw_text_sha256，基于原始响应文本），冻结集因此能声明「我这份 raw 的
                # 指纹是这个」，且必然等于 raw_market_snapshot 那一行的 content_sha256。
                "content_sha256": raw_text_sha256(d.raw_text),
                "bar_count": len(d.bars),
                "trade_date": d.trade_date,
                # 出处三件套进 manifest（E-20.2）：回放时能回答「这份数据是谁、
                # 用哪一版解析读出来的」，而不只是「它长什么样」。
                "provider_id": d.provider_id,
                "adapter_version": d.adapter_version,
            }

        evidence_set_id = new_evidence_set_id()

        # 🔴 P3-2：在**同一批不可变字节**上再发布一份通用 DatasetSnapshot 血缘。
        #    现有读端仍然读下面的 `symbols`，Agent / Card 行为一个字节都不变；
        #    新血缘是**追加**的，供将来的 SnapshotResolver / Replay / Review 用。
        #
        # ⚠️ 只有**注入了 fetcher**（测试/调试路径）且那个 provider 不在数据平台
        #    名册里时，才跳过登记 —— 此时这次冻结根本不属于任何已注册 dataset，
        #    没有可登记的血缘。默认（生产）fetcher 走严格路径，注册表查不到就抛。
        #
        # 🔴 捕获的是 `ProviderNotRegistered` 这个**专有异常**，不是裸 KeyError。
        #    外部 P3-2 实现写的是 `except KeyError:` —— 而 `publish()` 里
        #    `entry["snapshot_id"]` 之类的字段缺失**也抛 KeyError**，等于把
        #    「这个源没登记」和「我的数据结构坏了」当成同一件事，后者被静默咽掉
        #    （R-3：静默 fail-open，本项目最优先防范的形状）。
        #
        # ⚠️ 这条 seam 一度被我整个删掉，理由是「生产路径与测试路径应当是同一条」。
        #    **那是错的** —— 批 E-20.2 的 `test_换个provider落库的source跟着变`
        #    必须注入一个未注册的 provider 才能验「出处不会说谎」，删掉 seam
        #    等于要求那条测试改用注册过的源，而那样它就测不到它要测的东西了。
        dataset_snapshot = None
        try:
            dataset_snapshot = self._dataset_bridge.publish(
                evidence_set_id=evidence_set_id,
                decision_id=decision_id,
                run_id=run_id,
                entries=entries,
            )
        except ProviderNotRegistered:
            if not self._bridge_allows_unregistered:
                raise

        manifest = {
            # 没登记血缘就不是 v2 —— 版本号描述的是 manifest 的**形状**，
            # 有没有 `datasets` 键是那个形状的一部分。
            "manifest_version": "2" if dataset_snapshot is not None else "1",
            "kind": MANIFEST_KIND,
            "frozen_bars": int(bars),
            # 🔴 每个 symbol 记 snapshot_id —— manifest 必须能被**反向走通**回
            #    raw 层（探针 P5），不是一段只用于展示的自由文本。
            "symbols": entries,
            # v2 的两个新键（knowledge_cutoff / datasets）在下面按需追加。
            # 旧键（kind / frozen_bars / symbols）逐字保留 —— 老读端不受影响，
            # 而**老卡本来就没有 `manifest_version`**，读端按 v1 处理（裁定见设计文档）。
        }
        dataset_links: dict[str, str] = {}
        if dataset_snapshot is not None:
            manifest["knowledge_cutoff"] = dataset_snapshot.knowledge_cutoff
            manifest["datasets"] = {
                "cn.index.daily_bars": {"snapshot_id": dataset_snapshot.snapshot_id},
            }
            dataset_links["cn.index.daily_bars"] = dataset_snapshot.snapshot_id

        save_evidence_set(
            evidence_set_id=evidence_set_id,
            decision_id=decision_id,
            manifest=manifest,
            run_id=run_id,
            # 血缘与主行同事务 —— 不允许「manifest 写着快照号而链接行没落库」
            dataset_snapshots=dataset_links,
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
        _verify_snapshot_hash(snap)
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
