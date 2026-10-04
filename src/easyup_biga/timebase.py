"""Canonical A-share business-time helpers.

Phase 4 persists and displays business timestamps in ``Asia/Shanghai``.  Comparison never
uses ISO text ordering: inputs are parsed as timezone-aware datetimes and compared as absolute
instants.  This preserves Beijing-time semantics while remaining correct when legacy rows use
another explicit UTC offset.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from easyup_biga.domain.evidence import CN_TZ

__all__ = [
    "parse_aware",
    "to_cn",
    "cn_iso",
    "at_or_before",
    "strictly_before",
    "datetime_rank",
]


def parse_aware(value: str | datetime, *, name: str = "timestamp") -> datetime:
    """Parse one timezone-aware instant without changing its represented moment."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include timezone")
    return parsed


def to_cn(value: str | datetime, *, name: str = "timestamp") -> datetime:
    """Return the same instant represented in the A-share business timezone."""
    return parse_aware(value, name=name).astimezone(CN_TZ)


def cn_iso(value: str | datetime, *, name: str = "timestamp") -> str:
    """Canonical persisted representation: aware ISO-8601 in Asia/Shanghai (+08:00)."""
    return to_cn(value, name=name).isoformat()


def at_or_before(candidate: str | datetime, cutoff: str | datetime) -> bool:
    return parse_aware(candidate, name="candidate") <= parse_aware(cutoff, name="cutoff")


def strictly_before(left: str | datetime, right: str | datetime) -> bool:
    return parse_aware(left, name="left") < parse_aware(right, name="right")


def datetime_rank(value: Any, *, name: str = "timestamp") -> datetime:
    """Convenience alias used by max/sort selectors."""
    return parse_aware(str(value), name=name)
