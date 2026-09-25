"""Data Platform 的领域契约 —— 数据集身份、Provider 身份、状态与退出码。

本文件沿用外部 Phase 3 设计包 `reference/data_contracts.py` 的**形状与命名**
（`DataStatus` 用 `StrEnum`、定义用 frozen slots dataclass、`exit_code_for`
做状态→退出码的唯一映射），内容按 `docs/design/phase-3-data-platform.md`
的 14 条适配裁定改过。**不是照抄** —— 下面每处偏离都写了理由。

覆盖 / 不覆盖
-------------
- 覆盖：`DatasetDefinition` / `ProviderDefinition` / `DataStatus` / `DataIssue`，
  以及业务状态到进程退出码的映射
- **不覆盖**：`DataJobResult` / `ProviderAttempt` / `ProviderAdapter` —— 参考骨架
  里有它们，但 P3-0 没有任何东西产出或消费一次「运行」。它们跟 Job Runner 一起
  在 P3-1 进来，那时才有消费方（裁定 9 的同一条道理：没有消费方的字段/结构
  就是 L-1 死配置）

🔴 与参考骨架的三处偏离
-----------------------
1. **`TIMEOUT` 退到 2，不是 1**（裁定 12）。参考骨架把 `FAILED/CANCELLED/TIMEOUT`
   都退 1。本仓库退出码的唯一定义是 `tools/verify/_verdict.py`：
   `1 = 查了真的不对，去看代码` / `2 = 没查成，去看数据与时机`。
   超时是典型的「去看数据源/环境」⇒ 必须是 2，否则排查方向天生是错的。
2. **`CANCELLED` 退 130，不进三态。** 人主动中断既不是 FAIL 也不是 UNKNOWN，
   按 SIGINT 惯例走 130。把它塞进 1 会让「我按了 Ctrl-C」和「代码有 bug」
   长成同一个数字。
3. **`exit_code_for` 收 `DataStatus` 而不是 `DataJobResult`。** 参考骨架里它
   只读 `result.status` —— 那就让签名说实话，顺带让它在没有 Result 结构的
   P3-0 也能被测。

⚠️ 退出码这三个数字在本仓库有两个出处（这里 + `tools/verify/_verdict.py`），
   正是 L-3 的形状 —— 由 `tests/test_data_registry.py` 钉住两者不矛盾。
   没有把 `_verdict.py` 搬进包里：它的 docstring 明确把自己的范围限定为
   「`tools/verify/` 下工具的进程退出码」，搬过来等于擅自扩大它的管辖。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DataStatus",
    "DataIssue",
    "DatasetDefinition",
    "ProviderDefinition",
    "exit_code_for",
    "EXIT_CANCELLED",
]

#: 人主动中断 —— SIGINT 惯例，故意落在 0/1/2 三态之外（见模块头偏离 2）。
EXIT_CANCELLED = 130


class DataStatus(StrEnum):
    """一次数据任务的终态。七个值与参考骨架逐字一致 —— 不改名字。

    改名字的代价不对称：这套字符串将来会落进库、进通知、进 Card，
    而「改个更好听的名字」带来的收益是零。
    """

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    SKIPPED_UP_TO_DATE = "SKIPPED_UP_TO_DATE"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


#: 状态 → 进程退出码的**唯一映射**。写成表而不是 if 链，是为了让
#: 「七个状态一个都没漏」可以被 `set(_EXIT_CODE) == set(DataStatus)` 直接断言。
_EXIT_CODE: dict[DataStatus, int] = {
    DataStatus.COMPLETE: 0,
    DataStatus.SKIPPED_UP_TO_DATE: 0,      # 已经是最新，不是失败
    DataStatus.FAILED: 1,                  # 查了真的不对 —— 去看代码/配置
    DataStatus.PARTIAL: 2,                 # 数据不全 —— 去看数据源
    DataStatus.QUARANTINED: 2,             # 源冲突 —— 去看数据
    DataStatus.TIMEOUT: 2,                 # 裁定 12：环境/时机，不是代码
    DataStatus.CANCELLED: EXIT_CANCELLED,  # 人按的，不参与三态
}


def exit_code_for(status: DataStatus) -> int:
    """业务状态 → 进程退出码。未知状态 **fail closed**，不给默认值。

    🔴 不写 `return _EXIT_CODE.get(status, 1)`：那会让将来新加的状态静默退 1
    （= 「去看代码」），而新状态最可能的含义恰恰不是这个。宁可当场炸。
    """
    try:
        return _EXIT_CODE[DataStatus(status)]
    except (KeyError, ValueError) as e:
        raise ValueError(
            f"未知的数据任务状态 {status!r} —— 没有对应的退出码。\n"
            f"  合法值：{[s.value for s in DataStatus]}\n"
            f"  新增状态时必须同时在 contracts._EXIT_CODE 里给它一个码，"
            f"并想清楚它是「去看代码(1)」还是「去看数据(2)」。"
        ) from e


@dataclass(frozen=True, slots=True)
class DataIssue:
    """一条数据问题。字段与参考骨架一致。

    `code` 用点号分层的标准串（`data.provider.timeout` 这类），与仓库既有的
    `missing[]` 代码同一个形状 —— 那边已经验证过：带点号的机器码能和
    「xxx agent 尚未上线」这种占位文本区分开，统计时不用靠人眼挑。
    """

    code: str
    severity: str
    detail: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class DatasetDefinition:
    """一个数据集的**身份**：叫什么、谁产它、今天落在哪。

    🔴 只装「今天可核对」的字段。参考骨架里还有 `quality_policy` 与
    `freshness_policy` —— **故意没装**：那两个字符串会指向一组还不存在的
    策略 id，而「设计文档用陈述句点名一个不存在的东西，读者会认为这条已经
    有人管了」是本仓库专门立过守卫的失败模式。它们跟 quality 模块一起在
    P3-4 进来。

    Attributes:
        dataset_id: 唯一身份，形如 ``cn.<域>.<名>``。
            🔴 `cn.trading_calendar` 这个名字**不是本次发明的** —— 它早就写在
            `providers/szse.py` 的模块头里（批 L）。注册表要做的是把这类散落的
            名字收成一处，不是另起一套。
        title: 人类可读的一句话，`bin/biga-data list` 显示它。
        primary_provider: 正常生产路径。
        fallback_providers: primary 失败后依次接管。
        validation_providers: 只用于交叉校验；冲突时判 `QUARANTINED`，
            **不静默选边**（裁定 15 的但书：允许多源，不允许悄悄给出两个数）。
        partition_keys: 这个数据集按什么切片。**没有切片就是空元组** ——
            不给「看起来该有」的键硬塞一个值。
        storage_policy: 今天它落在哪一类存储。这是**事实不是计划**：
            P3-0 时全部还在 SQLite，EOD 那条链在 P3-4 才换 Parquet。
        raw_table: 今天 raw 落哪张表 —— 由测试断言这张表真的存在。
        fact_table: 今天有没有归一化后的 fact 表。只有日历有。
    """

    dataset_id: str
    title: str
    primary_provider: str
    fallback_providers: tuple[str, ...]
    validation_providers: tuple[str, ...]
    partition_keys: tuple[str, ...]
    storage_policy: str
    raw_table: str
    fact_table: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """一个数据源的身份。

    🔴 **没有 `supported_datasets` 这个字段** —— 参考骨架里它是手写的，
    而它与 `DatasetDefinition` 的 `primary/fallback/validation` 是一对**孪生
    清单**：两处各写一份必然漂（开发流程第五问）。这里改成从 `DATASET_REGISTRY`
    **派生**（`provider_registry.datasets_of()`），于是「谁支持哪些数据集」
    结构上就不可能对不上，不需要一条测试去追。

    Attributes:
        provider_id: 🔴 **必须等于 `raw_market_snapshot.source` 的前缀**
            （`sina:kline/sh000001` 的 `sina`）。实测库里在用的是
            `sina` / `em` / `tencent` 三个，代码里另有 `szse`。
            写成 `eastmoney` 就是第三套口径 —— 由测试单向钉住。
        title: 人类可读。
        modules: 适配器模块的**完整 import 路径**，由测试断言真的 import 得到。
            provider_id 是个字符串，`modules` 是它指向真实代码的那根线。
            🔴 是元组不是单值：一个数据源可以有多个适配器 —— sina 一家供了
            日线、日历、快讯三个端点，分别在三个模块里。
    """

    provider_id: str
    title: str
    modules: tuple[str, ...]
