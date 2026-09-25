#!/usr/bin/env python3
"""探活 `cn.security_master` 的上游端点 —— **手工跑，不进 pytest**。

为什么它存在
------------
外部 P3-3 实现包的 `TEST_RESULTS.md` 写明
"Live Eastmoney network fetching was not executed" ——
也就是说那个 provider **从没打过真接口**。而本仓库开发流程第一条是
「设计先探活」：先用真请求打一次，再决定怎么写。

本仓库补做探活时（2026-09-26）：三个 host 全失败，而**同一分钟内**兄弟端点
`ulist.np` / `push2ex` 都返回了 `rc:0` 真数据；随后因请求过密被整体限流，
未能复验。⇒ 状态是「**未验证**」，不是「不可用」。

⚠️ 它不进 `pytest` —— 默认测试必须全离线（`tests/conftest.py` 的禁网围栏）。
   需要真实数据的验证一律放这里手工跑。

退出码（与 `tools/verify/_verdict.py` 同一套）
---------------------------------------------
    0  取到了，且条数/总数自洽          —— 端点可用
    1  取到了但内容不对（rc 非 0 / 条数对不上）—— 去看代码与过滤串
    2  没取成（网络 / 限流 / 502）      —— 去看数据与时机，**别急着改代码**

🔴 退 2 不代表端点坏了。东财会整体限流：被限流时连已知可用的端点也 502。
   隔一段时间再跑一次，别在这个状态下改过滤串 —— 那会把两个变量搅在一起。
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402

from easyup_biga.providers.eastmoney_security_master import (  # noqa: E402
    _HOSTS,
    _SECURITY_FS,
    fetch_security_master,
)
from easyup_biga.providers.http import SourceError  # noqa: E402


def main() -> int:
    print(f"主域顺序 : {' → '.join(_HOSTS)}")
    print(f"过滤串   : {_SECURITY_FS}")
    print("抓 1 页试水（串行 + 限流，不会连打）…\n")
    try:
        # page_size 小一点：探活只需要证明「能取到、rc=0、total 合理」，
        # 不需要把 5000 多只全拉一遍 —— 那正是上次被限流的原因。
        result = fetch_security_master(page_size=20, max_pages=1)
    except SourceError as e:
        print(f"🔶 没取成：{e}\n")
        print("   这**不**等于端点坏了。东财会整体限流 —— 被限流时连已知可用的")
        print("   端点也返回 502。隔一段时间再跑，别在这个状态下改过滤串。")
        return _v.UNKNOWN
    except Exception as e:                                   # noqa: BLE001
        print(f"🔴 抓取层以外的异常：{e!r}")
        return _v.FAIL

    n = len(result.rows)
    print(f"✅ 取到 {n} 行，provider 自报 total={result.total}")
    print(f"   source = {result.source}")
    if n < 1 or result.total < 1000:
        print(f"🔴 内容不对：total={result.total} 明显小于全市场规模 —— 过滤串可能漏了段")
        return _v.FAIL
    sample = result.rows[0]
    print(f"   首行样本：{ {k: sample.get(k) for k in ('f12', 'f14', 'f13', 'f26')} }")
    print("\n⇒ 端点可用。把这个结论写回 provider 的模块头，并把")
    print("   `data/quality.py` 里 `cn-security-master-v1` 那条「尚未探活」的说明删掉。")
    return _v.PASS


if __name__ == "__main__":
    raise SystemExit(main())
