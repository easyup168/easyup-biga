"""质量策略名册 —— `DatasetDefinition.quality_policy` 指向的那些 id 的**唯一定义处**。

🔴 为什么需要这份名册，而不是让 `quality_policy` 只是个字符串
--------------------------------------------------------------
外部实现包里 `quality_policy="cn-index-daily-v1"` 这类值**指向不存在的东西** ——
既没有策略定义，也没有任何地方能回答「这条策略到底检查了什么」。
那正是本仓库立过守卫的失败模式：**点名一个不存在的东西，读者会认为它已经有人管了。**

⇒ 每个 `quality_policy` 必须在这里注册，由 `tests/test_data_registry.py` 钉住。
  注册一条策略 = 写清楚**它检查什么、不检查什么**。

⚠️ 这里只放「策略是什么」的声明。具体判据的实现随各自的 Job 进来
（P3-4 的 coverage / OHLC 合法性等）—— 今天只有 P3-2 那一条是真的在跑的。
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import DatasetStatus

__all__ = [
    "QUALITY_ISSUE_CODES",
    "QUALITY_POLICIES",
    "QualityPolicy",
    "get_quality_policy",
    "is_default_consumable",
]

#: 标准问题码。带点号分层，与仓库既有的 `missing[]` 代码同一个形状 ——
#: 统计「哪个源最常出问题」时不用靠人眼从自由文本里挑。
QUALITY_ISSUE_CODES = frozenset({
    "data.provider.timeout",
    "data.provider.rate_limited",
    "data.provider.schema_changed",
    "data.provider.empty_unexpected",
    "data.raw.hash_mismatch",
    "data.normalize.invalid_symbol",
    "data.quality.duplicate_key",
    "data.quality.coverage_below_threshold",
    "data.quality.cross_source_conflict",
    "data.snapshot.not_complete",
})


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """一条质量策略：它检查什么、**不**检查什么。

    Attributes:
        policy_id: 被 `DatasetDefinition.quality_policy` 引用的 id。
        checks: 这条策略今天**真的执行**的检查（自然语言，一条一句）。
        not_checked: 🔴 **明确不检查的**。不写这一栏，读者会默认它全查了 ——
            而「以为查过」比「知道没查」危险得多（R-3 的同一条道理）。
    """

    policy_id: str
    checks: tuple[str, ...]
    not_checked: tuple[str, ...]


QUALITY_POLICIES: dict[str, QualityPolicy] = {
    p.policy_id: p
    for p in (
        QualityPolicy(
            policy_id="cn-index-daily-bridge-v1",
            checks=(
                "每个 symbol 的冻结 raw 行真的存在（legacy snapshot 可加载）",
                "raw 行的 content_sha256 与 EvidenceSet manifest 里记的逐字相同",
                "整个 bundle 只来自一个 provider",
                "该 provider 在 Provider Registry 里有契约",
            ),
            not_checked=(
                "行情数值本身是否合理（量级围栏在 providers/sanity.py，不在这一层）",
                "交易日是否正确（各 skill 自报，Stage 3 交叉核对）",
                "覆盖率 —— 指数日线是按需冻结的，没有「应该有多少个」的期望集合",
            ),
        ),
        QualityPolicy(
            policy_id="cn-trading-calendar-v1",
            checks=(),
            not_checked=(
                "🔴 **今天什么都没检查。** 日历还没走 Data Job 链路 —— "
                "`bin/biga-calendar` 直接刷 fact 表，不经过 DatasetSnapshotService。"
                "这条策略登记在册只是为了让 registry 的引用不指向空气；"
                "它变成真的检查要等日历接进 Data Job（P3-6 之后）。",
            ),
        ),
    )
}


def get_quality_policy(policy_id: str) -> QualityPolicy:
    """按 id 取策略。未知 id **fail closed**。"""
    try:
        return QUALITY_POLICIES[policy_id]
    except KeyError:
        raise KeyError(
            f"未注册的质量策略 {policy_id!r}。\n"
            f"  已注册：{list(QUALITY_POLICIES)}\n"
            f"  新策略要先在 data/quality.py 里写清「检查什么 / 不检查什么」再引用。"
        ) from None


def is_default_consumable(status: DatasetStatus) -> bool:
    """下游默认能不能直接用这份快照。

    只有 `COMPLETE` 为真 —— `PARTIAL` 默认也**不能**：缺失项要先上浮到
    Card 的 `missing[]` 并让 risk 有机会 UNKNOWN / BLOCK，而不是被静默消费。
    """
    return status == DatasetStatus.COMPLETE
