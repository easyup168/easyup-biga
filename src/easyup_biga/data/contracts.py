"""Data Platform 的领域契约 —— 数据集身份、Provider 身份、状态与退出码。

设计依据是 **`BigA Data Architecture v1`（2026-09-25）** —— 它取代了此前的
Phase 3 Data Platform Foundation 草案，并且是照 Phase 2 收口源码重扫写成的
（旧草案假定 schema 从 v21 起排、日历主源是官方端点，两条都与现状不符）。

覆盖 / 不覆盖
-------------
- 覆盖：`DatasetDefinition` / `ProviderDefinition` / `DataRunStatus` /
  `DataIssue`，以及 Data Run 终态到进程退出码的映射
- **不覆盖**：
  * `DatasetStatus`（快照状态，P3-1 有快照可标时才有消费方）
  * `DataJobResult` / `ProviderAttempt` / `ProviderAdapter`（P3-1 随 Job Runner）
  * `quality_policy` / `freshness_policy` / `point_in_time` 三个字段 ——
    外部骨架有，但它们今天会指向一组还不存在的策略。按 ADR-002
    「有真实生产者或消费者才激活」，随各自的 Milestone 进来

🔴 退出码：`TIMEOUT` 退 2，不是 1
---------------------------------
本仓库退出码的唯一定义是 `tools/verify/_verdict.py`：
`1 = 查了真的不对，去看代码` / `2 = 没查成，去看数据与时机`。
超时是典型的「去看数据源/环境」⇒ 必须是 2，否则排查方向天生是错的。
`CANCELLED` 退 130（SIGINT 惯例），不塞进三态 —— 「我按了 Ctrl-C」
和「代码有 bug」不该长成同一个数字。

⚠️ 外部设计 v1 没有规定退出码映射，所以这条是本仓库自己的裁定，不是偏离。
⚠️ 这三个数字因此在仓库里有两个出处，正是 L-3 的形状 ——
   由 `tests/test_data_registry.py` 钉住两者不矛盾。
   没有把 `_verdict.py` 搬进包里：它的 docstring 明确把自己的范围限定为
   「`tools/verify/` 下工具的进程退出码」，搬过来等于擅自扩大它的管辖。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DataRunStatus",
    "DataIssue",
    "DatasetDefinition",
    "ProviderDefinition",
    "exit_code_for",
    "EXIT_CANCELLED",
]

#: 人主动中断 —— SIGINT 惯例，故意落在 0/1/2 三态之外（见模块头偏离 2）。
EXIT_CANCELLED = 130


class DataRunStatus(StrEnum):
    """一次 **Data Run** 的终态。

    🔴 **名字里的 `Run` 不是装饰。** 外部设计 v1 另有一个 `DatasetStatus`
    （`COLLECTING`/`VALIDATING`/`COMPLETE`/`PARTIAL`/`QUARANTINED`/`FAILED`/
    `SUPERSEDED`），描述的是**快照/数据集**处在什么状态 —— 与「这次执行结束在
    哪一格」是两层东西，只是有几个值同名。第一版把本枚举叫 `DataStatus`，
    那个名字会在 P3-1 引入 `DatasetStatus` 时变成一个必须靠上下文区分的歧义。

    ⚠️ 是 `COMPLETED` 不是 `COMPLETE` —— 与外部设计 §10 的 Data Run 终态列表
    对齐，也正是与 `DatasetStatus.COMPLETE` 拉开距离的那个字母。

    `DatasetStatus` 本身**不在这里** —— 它要到 P3-1 有快照可标状态时才有消费方。
    """

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    SKIPPED_UP_TO_DATE = "SKIPPED_UP_TO_DATE"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


#: 状态 → 进程退出码的**唯一映射**。写成表而不是 if 链，是为了让
#: 「七个状态一个都没漏」可以被 `set(_EXIT_CODE) == set(DataRunStatus)` 直接断言。
_EXIT_CODE: dict[DataRunStatus, int] = {
    DataRunStatus.COMPLETED: 0,
    DataRunStatus.SKIPPED_UP_TO_DATE: 0,      # 已经是最新，不是失败
    DataRunStatus.FAILED: 1,                  # 查了真的不对 —— 去看代码/配置
    DataRunStatus.PARTIAL: 2,                 # 数据不全 —— 去看数据源
    DataRunStatus.QUARANTINED: 2,             # 源冲突 —— 去看数据
    DataRunStatus.TIMEOUT: 2,                 # 裁定 12：环境/时机，不是代码
    DataRunStatus.CANCELLED: EXIT_CANCELLED,  # 人按的，不参与三态
}


def exit_code_for(status: DataRunStatus) -> int:
    """业务状态 → 进程退出码。未知状态 **fail closed**，不给默认值。

    🔴 不写 `return _EXIT_CODE.get(status, 1)`：那会让将来新加的状态静默退 1
    （= 「去看代码」），而新状态最可能的含义恰恰不是这个。宁可当场炸。
    """
    try:
        return _EXIT_CODE[DataRunStatus(status)]
    except (KeyError, ValueError) as e:
        raise ValueError(
            f"未知的数据任务状态 {status!r} —— 没有对应的退出码。\n"
            f"  合法值：{[s.value for s in DataRunStatus]}\n"
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
        consumers: 🔴 **今天谁在读它。** 写成 ``"模块路径:符号名"``，
            由测试 import 那个模块并断言符号真的在（行为判据，不是字符串扫描）。

            这一列实现外部设计 §16 的那条守卫 **`no zero-consumer active dataset`**：
            注册一个没人读的数据集就是 L-1 死配置。空元组当场红 ——
            **想注册就得先答出谁读它**，答不出来说明这条还不该进册。
    """

    dataset_id: str
    title: str
    primary_provider: str
    fallback_providers: tuple[str, ...]
    validation_providers: tuple[str, ...]
    partition_keys: tuple[str, ...]
    storage_policy: str
    raw_table: str
    consumers: tuple[str, ...]
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
        provider_id: **适配器级**的身份，与 `providers/` 下的模块同名。

            🔴 第一版取的是 `raw_market_snapshot.source` 的前缀（`sina` / `em` /
            `tencent`），于是新浪的日线、日历、快讯合成了一个 `sina`。
            **那个模型答错了它自己要答的问题**：`datasets_of("sina")` 会说
            「sina 挂了影响 3 个数据集」，而那是三个**可以各自独立挂**的端点 ——
            它系统性地高估影响面。外部设计 v1 的 Provider Inventory 按模块列，
            是对的。

        source_prefix: 这个适配器写进 `raw_market_snapshot.source` 的前缀。
            🔴 它与 `provider_id` **不是一对一**：`sina` 与 `sina_calendar`
            都写 `sina:`。这一列存在的全部理由，是保住第一版真正想守的那件事
            —— **别让同一个数据源在源码、库、注册表里有三套名字**。
            由测试单向钉住：每个注册的 `source_prefix` 必须在源码里作为
            `"<prefix>:` 出现过。

        title: 人类可读。
        modules: 适配器模块的**完整 import 路径**，由测试断言真的 import 得到。
            `provider_id` 是个字符串，`modules` 是它指向真实代码的那根线。
            仍是元组：一个适配器身份将来可能拆成多个文件。
    """

    provider_id: str
    title: str
    source_prefix: str
    modules: tuple[str, ...]
