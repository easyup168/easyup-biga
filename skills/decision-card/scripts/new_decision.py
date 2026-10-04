#!/usr/bin/env python3
"""Stage 0 · 占一个决策编号，打印出来。

为什么要有这一步
----------------
编号原本在**合成时**才分配 —— 也就是证据都采完之后。
那意味着 Stage 1 跑的时候，这次决策还没有身份，
于是每个 specialist 只好自己编一个（实测：五个都编成 `-001`）。

后果在 2026-09-21 盘中被真实触发：两次端到端相隔两分钟，
它们的 verdict 全部写着同一个 task_id，证据混进了同一张卡，
**事后没有任何字段能把两次运行分开。**

⇒ 决策的身份必须先于它的证据存在。

用法
----
    $ python3 skills/decision-card/scripts/new_decision.py
    BIGA-20260921-007

把这个号用 `--task-id` 传给每一个 specialist，
最后用 `--decision-id` 传给 `synthesize.py`。

占号是**原子**的（主键冲突仲裁），两次同时起也不会拿到同一个号。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _store import db  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from budget import check_budget, explain  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="占一个决策编号（Stage 0）")
    ap.add_argument("--by", default="supervisor",
                    help="谁占的号。排查「这个号哪来的」时唯一有用的线索")
    ap.add_argument("--day", help="YYYYMMDD，缺省为今天（北京时间）")
    ap.add_argument("--force", action="store_true",
                    help="越过预算闸门。**要有理由** —— 它拦的是误触，不是你")
    a = ap.parse_args()

    # 🔴 闸门在**占号之前** —— 占号是整条链路的第一步，
    #    也是唯一一处所有触发路径（CLI / 飞书 / 将来的 cron）都绕不过去的地方。
    #    接飞书之后手机上一句话就能花掉 $1.37，而在那之前误触代价是零。
    if not a.force:
        reasons = check_budget(day=a.day)
        if reasons:
            print(explain(reasons), file=sys.stderr)
            return 3

    print(db.reserve_decision_id(by=a.by, day=a.day))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
