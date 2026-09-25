"""各 dataset 的适配器：Provider 结果 → 本仓库的标准 dataset schema。"""

from .index_daily import IndexDailyDatasetBridge, IndexDailyPublishResult
from .security_master import (
    Exchange,
    SecurityBoard,
    SecurityMasterQualityPolicy,
    SecurityMasterRecord,
    SecurityMasterService,
    SecurityMasterSyncResult,
    SecurityStatus,
    SecurityType,
    normalize_security_master_rows,
)

__all__ = [
    "Exchange",
    "IndexDailyDatasetBridge",
    "IndexDailyPublishResult",
    "SecurityBoard",
    "SecurityMasterQualityPolicy",
    "SecurityMasterRecord",
    "SecurityMasterService",
    "SecurityMasterSyncResult",
    "SecurityStatus",
    "SecurityType",
    "normalize_security_master_rows",
]
