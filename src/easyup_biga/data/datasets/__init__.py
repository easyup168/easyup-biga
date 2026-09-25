"""Dataset-specific adapters for the Phase 3 data platform."""

from .index_daily import IndexDailyDatasetBridge, IndexDailyPublishResult

__all__ = ["IndexDailyDatasetBridge", "IndexDailyPublishResult"]
