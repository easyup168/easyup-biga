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

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

__all__ = [
    "DataJobRun",
    "DataRunStatus",
    "DatasetLink",
    "DatasetPartition",
    "DatasetProviderBinding",
    "DatasetSnapshot",
    "DatasetStatus",
    "ProviderAttempt",
    "ProviderAttemptStatus",
    "ProviderRole",
    "QualityReport",
    "RawArtifact",
    "canonical_partition_key",
    "new_data_run_id",
    "new_dataset_snapshot_id",
    "new_partition_id",
    "new_quality_report_id",
    "new_raw_artifact_id",
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
    instrument_id: str | None = None
    field: str | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """落库用的扁平形式。**由 issue 自己负责** —— 写入层不该知道它有哪些字段。"""
        return {
            "code": self.code, "severity": self.severity, "detail": self.detail,
            "retryable": self.retryable, "instrument_id": self.instrument_id,
            "field": self.field, "source_ref": self.source_ref,
        }


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
        partition_keys: 这个数据集按什么切片。**必须非空且不重复。**

            🔴 第一版允许空元组，理由是「不给看起来该有的键硬塞一个值」。
            那条原则没错，用错了地方：真正的问题不是「要不要填」，是
            **没有任何东西核对填的对不对** —— 两版实现里它都只是装饰品。
            现在 `persistence.data` 会拿它核对每一次写入的实际分区键
            （`set(partition_key) == set(partition_keys)`），它因此变成承重的，
            而承重的字段不能为空。
        storage_policy: 今天它落在哪一类存储。这是**事实不是计划**：
            P3-0 时全部还在 SQLite，EOD 那条链在 P3-4 才换 Parquet。
        raw_table: 今天 raw 落哪张表 —— 由测试断言这张表真的存在。
        fact_table: 今天有没有归一化后的 fact 表。只有日历有。
        schema_version: 这个数据集的 schema 第几版。**P3-1 才加进来** ——
            在 `persistence.data.save_dataset_partition` / `save_dataset_snapshot`
            里被用来拒绝「分区声称的版本与注册表不符」。P3-0 时它没有消费方，
            按裁定 9 没装；现在有了才装，这正是那条纪律想要的节奏。
        quality_policy: 这个数据集用哪条质量策略。**P3-2 才加进来** ——
            `data.snapshots.DatasetSnapshotService` 把它写进 `QualityReport.policy_id`。
            🔴 它必须在 `data/quality.py::QUALITY_POLICIES` 里注册（由测试钉住）：
            外部实现包里这个字段指向一组**不存在**的策略 id，而
            「点名一个不存在的东西」正是本仓库立过守卫的失败模式。
        consumers: 🔴 **今天谁在读它。** 写成 ``"模块路径:符号名"``，
            由测试 import 那个模块并断言符号真的在（行为判据，不是字符串扫描）。

            这一列实现外部设计 §16 的那条守卫 **`no zero-consumer active dataset`**：
            注册一个没人读的数据集就是 L-1 死配置。空元组当场红 ——
            **想注册就得先答出谁读它**，答不出来说明这条还不该进册。
    """

    dataset_id: str
    title: str
    schema_version: int
    primary_provider: str
    fallback_providers: tuple[str, ...]
    validation_providers: tuple[str, ...]
    partition_keys: tuple[str, ...]
    storage_policy: str
    quality_policy: str
    raw_table: str
    consumers: tuple[str, ...]
    fact_table: str | None = None

    def __post_init__(self) -> None:
        _text("dataset_id", self.dataset_id)
        if self.schema_version < 1:
            raise ValueError(f"{self.dataset_id}: schema_version 必须 >= 1")
        if not self.partition_keys or len(set(self.partition_keys)) != len(self.partition_keys):
            raise ValueError(
                f"{self.dataset_id}: partition_keys 必须非空且不重复 —— "
                f"它被 persistence.data 用来核对每一次写入的实际分区键。")
        _text("quality_policy", self.quality_policy)
        if not self.consumers:
            raise ValueError(
                f"{self.dataset_id}: consumers 不能为空 —— "
                f"答不出谁读它，这条就还不该进册（零消费方 = L-1 死配置）。")


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


# ─────────────────────────────────────────────── P3-1：快照 / 执行记录（外部实现合入）
#
# 下面这一段取自外部 P3-0/P3-1 实现包，**逐个审过**而不是整份照搬。
# 取它的理由：这些结构在 P3-1 立刻有消费方（`persistence/data.py`），
# 且它的构造时校验、分区键归一、id 工厂三件事都做得比我原来的骨架完整。
#
# 改掉的地方各自在注释里写了为什么。


class DatasetStatus(StrEnum):
    """一份**快照/数据集**处在什么状态 —— 与 `DataRunStatus`（一次执行的终态）是两层。

    `COMPLETE` 不带 D，正是与 `DataRunStatus.COMPLETED` 拉开距离的那个字母。
    """

    COLLECTING = "COLLECTING"
    VALIDATING = "VALIDATING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class ProviderRole(StrEnum):
    """一个 provider 在**某个 dataset 上**扮演的角色。

    🔴 角色属于「dataset × provider」这条边，不属于 provider 本身 ——
    「不要把某个 Provider 永久等同于 Primary」。今天这条边内联在
    `DatasetDefinition` 的三个字段里（只有两个 dataset，独立结构是空开销）；
    等出现需要挂**边上元数据**的场景（重试策略、限流配额），再抽 `DatasetProviderBinding`。
    """

    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    VALIDATOR = "VALIDATOR"


class ProviderAttemptStatus(StrEnum):
    """一次 provider 取数尝试的结果。

    ⚠️ 可重试与不可重试**分成两个值**，不是一个 `failed` 加一个布尔 ——
    落库之后只剩字符串，布尔那一位最容易在序列化边界被丢掉。
    """

    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    SKIPPED = "SKIPPED"


def _text(name: str, value: str) -> None:
    """非空、且两端无空白 —— 这类 id 会进 UNIQUE 约束与 JSON 键。

    🔴 卡 `value != value.strip()` 而不只是 `strip()` 非空：`"sina "` 与 `"sina"`
    在 SQLite 里是两行，而人眼看不出区别。
    """
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} 必须是非空且两端无空白的字符串，收到 {value!r}")


def canonical_partition_key(value: Mapping[str, str]) -> dict[str, str]:
    """把分区键归一成**确定性**形式（按键排序）。

    🔴 它是 `UNIQUE(dataset_id, partition_key_json, data_version)` 能成立的前提：
    同一个分区写两次，若 JSON 键序不同就会变成两行，而唯一约束**不会报错** ——
    它是「同一份数据悄悄存了两份」的经典入口。
    """
    if not isinstance(value, Mapping) or not value:
        raise ValueError("partition_key 必须是非空映射")
    out: dict[str, str] = {}
    for key, item in sorted(value.items()):
        _text("分区键", key)
        _text(f"分区键 {key} 的值", item)
        out[key] = item
    return out


@dataclass(frozen=True, slots=True)
class DatasetProviderBinding:
    """dataset × provider × role 这条边。**今天没有消费方** —— 见 `ProviderRole`。

    留着类型定义是因为 `ProviderAttempt.role` 要用 `ProviderRole`；
    这个 dataclass 本身在抽边上元数据之前不会被实例化。
    """

    dataset_id: str
    provider_id: str
    role: ProviderRole


@dataclass(frozen=True, slots=True)
class DataJobRun:
    """一次数据任务执行的身份。"""

    data_run_id: str
    job_id: str
    dataset_id: str
    partition_key: Mapping[str, str]
    requested_data_version: int
    trigger_id: str
    created_at: str

    def __post_init__(self) -> None:
        _text("data_run_id", self.data_run_id)
        _text("job_id", self.job_id)
        if self.requested_data_version < 1:
            raise ValueError("requested_data_version 必须 >= 1")
        object.__setattr__(self, "partition_key", canonical_partition_key(self.partition_key))


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """一份 provider 原始响应的**元数据**（正文在文件里，不在库里）。"""

    artifact_id: str
    dataset_id: str
    provider_id: str
    request_fingerprint: str
    body_uri: str
    body_sha256: str
    size_bytes: int
    retrieved_at: str
    content_type: str | None = None
    compression: str | None = None
    as_of: str | None = None
    available_at: str | None = None

    def __post_init__(self) -> None:
        _text("artifact_id", self.artifact_id)
        _text("body_sha256", self.body_sha256)
        if self.size_bytes < 0:
            raise ValueError("size_bytes 不能为负")


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """一次取数尝试 —— fallback 链上的每一跳都要留痕，包括失败的那些。"""

    data_run_id: str
    provider_id: str
    role: ProviderRole
    attempt_no: int
    status: ProviderAttemptStatus
    started_at: str
    finished_at: str | None = None
    elapsed_ms: int | None = None
    artifact_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetPartition:
    """一个已发布的分区。`data_version` 递增，**旧版本永不覆盖**。"""

    partition_id: str
    dataset_id: str
    partition_key: Mapping[str, str]
    schema_version: int
    data_version: int
    storage_format: str
    storage_uri: str
    content_sha256: str
    row_count: int
    provider_id: str
    raw_artifact_id: str | None = None
    supersedes_partition_id: str | None = None

    def __post_init__(self) -> None:
        if self.data_version < 1:
            raise ValueError("data_version 必须 >= 1")
        if self.row_count < 0:
            raise ValueError("row_count 不能为负")
        object.__setattr__(self, "partition_key", canonical_partition_key(self.partition_key))


@dataclass(frozen=True, slots=True)
class QualityReport:
    """一次质量裁定的结果与依据。"""

    quality_report_id: str
    data_run_id: str
    dataset_id: str
    partition_id: str | None
    status: DatasetStatus
    policy_id: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    issues: tuple[DataIssue, ...] = ()
    checked_at: str = ""


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    """下游**唯一可引用**的发布单位。"""

    snapshot_id: str
    dataset_id: str
    partition_key: Mapping[str, str]
    as_of: str
    knowledge_cutoff: str
    status: DatasetStatus
    schema_version: int
    data_version: int
    partition_ids: tuple[str, ...]
    quality_report_id: str
    content_sha256: str
    supersedes_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "partition_key", canonical_partition_key(self.partition_key))
        if not self.partition_ids:
            raise ValueError("partition_ids 不能为空 —— 一个不指向任何分区的快照没有意义")
        if self.status in {DatasetStatus.COLLECTING, DatasetStatus.VALIDATING}:
            raise ValueError(
                f"已发布的快照不能是中途状态 {self.status} —— "
                f"发布意味着质量已裁定（COMPLETE / PARTIAL / QUARANTINED / FAILED）"
            )


# contract-exempt: 这不是第二份 Evidence 契约。它是 `evidence_set_datasets` 的一行
#   （EvidenceSet ↔ DatasetSnapshot 的链接），名字里的 EvidenceSet 指的是**被引用方**
#   而不是它自己是什么。改名绕开词根只会让读者更难看出它在连什么。
@dataclass(frozen=True, slots=True)
class DatasetLink:
    """把一次决策的冻结证据集，与它用到的某个 DatasetSnapshot 连起来。"""

    evidence_set_id: str
    dataset_id: str
    snapshot_id: str


def _new(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def new_data_run_id() -> str:
    return _new("dr")


def new_raw_artifact_id() -> str:
    return _new("raw")


def new_partition_id() -> str:
    return _new("part")


def new_quality_report_id() -> str:
    return _new("qr")


def new_dataset_snapshot_id() -> str:
    return _new("dss")
