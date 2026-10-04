#!/usr/bin/env python3
"""从权威日历导出 `tradetime._HOLIDAYS` 的内容 —— **别手抄**。

用法::

    python3 tools/verify/dump_holiday_fallback.py              # 联网取最新
    python3 tools/verify/dump_holiday_fallback.py --fixture    # 用离线 fixture

🔴 它存在的理由是一次真实的手抄事故：照着另一份同类系统的假期表抄 2026 年，
把国庆后的复市日 `20261008` 抄成了休市日。那张表写得很认真 —— 分组注释、
维护规则都有 —— 照样是错的。
⇒ 判据不能是「抄的时候仔细一点」，只能是「从数据导出」。

输出直接贴进 `providers/tradetime.py` 的 `_HOLIDAYS`，并把 `_COVER_THROUGH`
改成输出里报的那个覆盖止。
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "src"))

from easyup_biga.providers.sina_calendar import (  # noqa: E402
    fetch_trading_days, parse_datelist)

_FIXTURE = _REPO / "tests" / "fixtures" / "sina-klc-td-sh.txt"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", action="store_true", help="用离线 fixture，不联网")
    ap.add_argument("--from", dest="start", default=None, help="起始日 YYYY-MM-DD，缺省取今天")
    args = ap.parse_args(argv)

    if args.fixture:
        days = parse_datelist(_FIXTURE.read_text(encoding="utf-8"))
    else:
        days, _ = fetch_trading_days()

    known = set(days)
    start = (datetime.date.fromisoformat(args.start) if args.start
             else datetime.date.today())
    end = max(days)

    holidays, cur = [], start
    while cur <= end:
        if cur.weekday() < 5 and cur not in known:
            holidays.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)

    print(f"# 覆盖 {start} … {end}（共 {len(known)} 个交易日的名单导出）")
    print(f"_COVER_THROUGH = datetime({end.year}, {end.month}, {end.day}, tzinfo=CN_TZ).date()")
    print("_HOLIDAYS: frozenset[str] = frozenset({")
    for h in holidays:
        print(f'    "{h}",')
    print("})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
