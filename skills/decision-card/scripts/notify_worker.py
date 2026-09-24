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

__all__ = ["Deliverer", "StdoutDeliverer", "MAX_ATTEMPTS", "deliver_pending", "main"]

#: 一条通知最多重试几次（2026-09-24，外部评审 §11）。达到这个数还没投成 ⇒ 放弃
#: （记 status='abandoned'，不再出现在 `undelivered_notifications()` 里）——不这样
#: 的话，一个非 retryable 也没有分类信息的错误会被无限期重投，且没有任何信号
#: 表明它已经无望（这正是"通知无限重试 + worker 假成功"这条评审指出的问题）。
MAX_ATTEMPTS = 5


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
    """把 outbox 里还没投成功的通知逐条投出去，返回
    `{pending, delivered, failed, abandoned}`。

    每条：调 `deliverer.deliver(...)`；成功 → 记一条 `delivered`。失败时按
    2026-09-24（外部评审 §11）的规则分流：

    * 异常带 `retryable=False`（配置类错误，重试不会变好——见 `feishu_deliverer.
      FeishuError`），或者这次已经是第 `MAX_ATTEMPTS` 次尝试 ⇒ 记 `abandoned`，
      不再出现在下次 `undelivered_notifications()` 里。
    * 否则记 `failed`，留在 outbox 里等下次重投。

    异常没有 `retryable` 属性（比如测试/`StdoutDeliverer` 抛的普通异常）时默认当
    `True`（R-3 方向：不确定该不该放弃时，宁可继续重试，也不要武断放弃一条本来
    还有机会的通知）。

    一条失败不连累其余（continue 到下一条）。attempt 号 = 这条 outbox 已有的
    尝试数 + 1（append 语义，不覆盖历史尝试）。

    `deliverer` 默认 `StdoutDeliverer`（桩）；测试注入 mock 验证调用与 payload（P4）。
    """
    deliverer = deliverer or StdoutDeliverer()
    channel = getattr(deliverer, "channel", "?")
    pending = undelivered_notifications(limit=limit, path=path)
    delivered = failed = abandoned = 0
    for row in pending:
        attempt = int(row["attempt_count"]) + 1
        try:
            deliverer.deliver(event_type=row["event_type"],
                              aggregate=row["aggregate"], payload=row["payload"])
        except Exception as e:  # noqa: BLE001
            # 🔴 投递失败只记账、不抛：不连累后面的通知，也不让它成为一个能把
            #    worker 进程整个带走的未捕获异常。
            retryable = getattr(e, "retryable", True)
            if not retryable or attempt >= MAX_ATTEMPTS:
                record_delivery(outbox_id=row["outbox_id"], attempt=attempt,
                                status="abandoned", channel=channel,
                                error=str(e), path=path)
                abandoned += 1
            else:
                record_delivery(outbox_id=row["outbox_id"], attempt=attempt,
                                status="failed", channel=channel,
                                error=str(e), path=path)
                failed += 1
            continue
        record_delivery(outbox_id=row["outbox_id"], attempt=attempt,
                        status="delivered", channel=channel, path=path)
        delivered += 1
    return {"pending": len(pending), "delivered": delivered,
            "failed": failed, "abandoned": abandoned}


def _make_deliverer(name: str) -> Deliverer:
    """按名字造投递器。`stdout` 是桩（默认，测试/离线）；`feishu` 是批 G-II 的真投递方。

    🔴 feishu 的凭据从环境变量读、仓库里不落（见 feishu_deliverer.py）。造它本身不
    出网（token/发消息都推迟到第一次 deliver），所以这里 import 失败以外不会有副作用。
    """
    if name == "stdout":
        return StdoutDeliverer()
    if name == "feishu":
        from feishu_deliverer import FeishuDeliverer  # 局部导入：桩路径不依赖它
        return FeishuDeliverer()
    raise SystemExit(f"未知投递器 {name!r}（可选 stdout / feishu）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="投递 notification_outbox 里待发的外发通知（批 G-I worker / 批 G-II 真飞书）")
    ap.add_argument("--limit", type=int, default=100,
                    help="单次最多投递多少条（默认 100）")
    # 🔴 批 G-II：默认仍是桩（离线/测试不出网）；--deliverer feishu 才接真实飞书 API。
    ap.add_argument("--deliverer", default="stdout", choices=["stdout", "feishu"],
                    help="投递渠道：stdout 桩（默认）/ feishu 真投递")
    a = ap.parse_args(argv)
    summary = deliver_pending(deliverer=_make_deliverer(a.deliverer), limit=a.limit)
    print(f"待投 {summary['pending']}  投出 {summary['delivered']}  "
          f"失败 {summary['failed']}  放弃 {summary['abandoned']}", file=sys.stderr)
    # 🔴 2026-09-24（外部评审 §11）：曾经无论失败多少条都 return 0，systemd 因此
    #    把失败批次误判为成功（Type=oneshot 只看退出码）。`failed`（还会重试的）
    #    非零就该让调用方知道这次不是全绿——`abandoned` 不算：那些已经处理完了
    #    （不会再自动重试），不是"这次调用出了问题"。
    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
