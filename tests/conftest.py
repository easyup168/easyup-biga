"""测试期的全局围栏。

为什么这个文件存在
------------------
`test_market_calc.py` 与 `test_emotion_calc.py` 的开头各写着一句：

    🔴 这些测试**不许联网**。

而在这个文件出现之前，**没有任何东西执行这句话** —— 它是散文，不是守卫。

外部深度评审就是这么抓到的：`test_sanity_fence.py` 里那条
「资金侧失效时不给排名而是报缺失」只桩掉了 `fetch_boards`，
同一次 `build_verdict` 里的 `collect_date` 照样**真的去连新浪**。
测试在有网的机器上绿、断网就红，而它声称自己是离线单测。

> 同一形状第 9 次：**守卫查的地方，和它声称守的地方，不是同一处。**
> 这次极端一些 —— 声称的那一处**根本没有守卫**。

⇒ 判据从注释搬到进程里：谁真的开了一个出站连接，谁就红。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：本进程内任何 socket 出站连接（不管走的是哪个 HTTP 库）
- **不覆盖**：`subprocess` 起的子进程 —— 围栏是进程内的猴补丁，
  跨不过 `fork`。所以那类测试（`test_spawn_proof` 的沙盒）必须
  **自己**把会联网的东西换成桩，围栏帮不了它。
  ⚠️ 这条边界要写出来，否则下次会以为「有 conftest 就安全了」。

真要联网的测试
--------------
加 `@pytest.mark.network`。目前**一条都没有**，而这正是想要的状态：
需要真实数据的验证放在 `tools/verify/` 下手工跑，不混进 `pytest`。
"""

from __future__ import annotations

import socket

import pytest

#: 允许的目的地。回环留着，是为了将来可能出现的本地假服务器；
#: 真实数据源一个都不在里面。
_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


class NetworkUsedInTest(AssertionError):
    """测试开了出站连接 —— 报错要指路，所以把目的地写进消息里。"""


def _addr(a) -> str:
    if isinstance(a, tuple) and a:
        return str(a[0])
    return str(a)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """默认禁网。`@pytest.mark.network` 可豁免。"""
    if request.node.get_closest_marker("network"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def guard(addr):
        host = _addr(addr)
        if host in _ALLOWED_HOSTS:
            return
        raise NetworkUsedInTest(
            f"这条测试连了 {host} —— 单测不许联网。\n"
            "  理由不是速度，是可信度：会随机变红的测试，几次之后就没人看了；\n"
            "  更糟的是它在有网时绿、断网时红，于是「绿」不再说明代码是对的。\n"
            "  ⇒ 把这一路的取数函数换成桩。注意**一次调用可能取多个源**：\n"
            "    评审抓到的那条就只桩了其中一个，另一个照样出网。\n"
            "  真的需要真实数据 ⇒ 加 @pytest.mark.network，或搬去 tools/verify/。")

    def fake_connect(self, addr):
        guard(addr)
        return real_connect(self, addr)

    def fake_connect_ex(self, addr):
        guard(addr)
        return real_connect_ex(self, addr)

    def fake_create(addr, *a, **kw):
        guard(addr)
        return real_create(addr, *a, **kw)

    # 🔴 三个入口都要补。只补 `create_connection` 拦不住直接
    #    `socket()` + `connect()` 的写法，而围栏漏一个口就等于没有。
    monkeypatch.setattr(socket.socket, "connect", fake_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", fake_connect_ex)
    monkeypatch.setattr(socket, "create_connection", fake_create)
