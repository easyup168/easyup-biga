#!/usr/bin/env python3
"""notify_worker.py —— 外发通知投递 worker（批 G-I，Outbound Only）。

它是 `notification_outbox` 的 **L-1 读取方**：扫还没投成功的行，逐条经一个
**可替换的投递接口**投出去，并往 `notification_deliveries` 追加一条尝试记录。

设计文档 §6 批 G-I：

* 这一批只做**推**。worker 是在 Card 已经写完之后**另起一个进程**去补投递，
  与 `bin/biga-card` 的同步调用者体验**完全无关**（`bin/biga-card` 出卡照旧
  `wait` 到 Card 落库；通知投递是它之后的事）。
* 投递接口先接一个桩（`StdoutDeliverer`，打印到 stdout）。**真正的飞书 adapter
  是批 G-II 才有的生产方** —— 这一批不接通真实飞书 API（探针 P4 用 mock/桩验证）。
* 幂等：`undelivered_notifications` 只返回没有 `delivered` 记录的行，重复跑安全
  （已投成的不会再投）。失败的行下次再来、attempt+1（append 一条新尝试，不改旧的）。

🔴 为什么投递失败**只记一条 failed、不抛**：一次飞书 API 抽风不该让 worker 整个
   崩掉、连累后面待投的通知；也不该把 Run 卡在非终态（Run 早在 COMPLETED 了，
   投递与 Run 状态解耦——设计文档 §6 批 G-I / 探针 P5）。失败留在 outbox 里等下次。

调度：这一批 worker 是一条**可单独跑的命令**（`python3 …/notify_worker.py`），
由人 / 将来的 cron 调起。真正把它挂进调度域（cron）是 Phase 3 的事（`tools/cron`
现在是空的，见 architecture.md §9 的 stale-run reaper 同款处境）——这一批先把
「有一个真能读 outbox、真能投递、真会记账」的消费方做出来并测掉（P4）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Protocol

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _store import (  # noqa: E402
    record_delivery,
    undelivered_notifications,
)

__all__ = ["Deliverer", "StdoutDeliverer", "deliver_pending", "main"]


class Deliverer(Protocol):
    """投递接口 —— 把一条通知发到某个渠道。

    🔴 **可替换/可 mock 的抽象**：这一批只有桩实现（`StdoutDeliverer`）；批 G-II 会
    加一个真飞书 adapter，只要满足这个协议就能直接替换进 `deliver_pending`。探针 P4
    注入一个记录调用的假实现来断言「worker 真的投了、payload 里有决策号」。

    约定：投递成功返回 None；**失败抛异常**（worker 据此记一条 status='failed'）。
    """

    #: 投递日志里记名的渠道（'stdout' / 将来 'feishu'）。
    channel: str

    def deliver(self, *, event_type: str, aggregate: str,
                payload: dict[str, Any]) -> None:
        ...


class StdoutDeliverer:
    """桩投递：打印到 stdout。批 G-II 才有真飞书 adapter（这一批不接通真实 API）。"""

    channel = "stdout"

    def deliver(self, *, event_type: str, aggregate: str,
                payload: dict[str, Any]) -> None:
        print(f"[notify] {event_type} {aggregate} :: "
              f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def deliver_pending(
    deliverer: Deliverer | None = None,
    *,
    limit: int = 100,
    path: "pathlib.Path | str | None" = None,
) -> dict[str, int]:
    """把 outbox 里还没投成功的通知逐条投出去，返回 `{pending, delivered, failed}`。

    每条：调 `deliverer.deliver(...)`；成功 → 记一条 `delivered`，失败 → 记一条
    `failed`（带 error 文本）并继续下一条（一条失败不连累其余）。attempt 号 =
    这条 outbox 已有的尝试数 + 1（append 语义，不覆盖历史尝试）。

    `deliverer` 默认 `StdoutDeliverer`（桩）；测试注入 mock 验证调用与 payload（P4）。
    """
    deliverer = deliverer or StdoutDeliverer()
    channel = getattr(deliverer, "channel", "?")
    pending = undelivered_notifications(limit=limit, path=path)
    delivered = failed = 0
    for row in pending:
        attempt = int(row["attempt_count"]) + 1
        try:
            deliverer.deliver(event_type=row["event_type"],
                              aggregate=row["aggregate"], payload=row["payload"])
        except Exception as e:  # noqa: BLE001
            # 🔴 投递失败只记账、不抛：不连累后面的通知，也不让它成为一个能把
            #    worker 进程整个带走的未捕获异常。留在 outbox 里等下次重投。
            record_delivery(outbox_id=row["outbox_id"], attempt=attempt,
                            status="failed", channel=channel, error=str(e), path=path)
            failed += 1
            continue
        record_delivery(outbox_id=row["outbox_id"], attempt=attempt,
                        status="delivered", channel=channel, path=path)
        delivered += 1
    return {"pending": len(pending), "delivered": delivered, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="投递 notification_outbox 里待发的外发通知（批 G-I worker）")
    ap.add_argument("--limit", type=int, default=100,
                    help="单次最多投递多少条（默认 100）")
    a = ap.parse_args(argv)
    summary = deliver_pending(limit=a.limit)
    print(f"待投 {summary['pending']}  投出 {summary['delivered']}  "
          f"失败 {summary['failed']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
