"""禁网围栏自己的守卫 —— 外部深度评审「sector 单测真调新浪」。

为什么围栏也要被测
------------------
`tests/conftest.py` 是一个 **autouse fixture**。它失效的方式很安静：
改个名字、挪个位置、或者哪天有人把 `autouse=True` 去掉 ——
测试全都照样绿，只是**又开始联网了**。

> 一个悄悄失效就没人知道的守卫，和没有这个守卫是一回事。
> 本仓库这条教训已经出现第 9 次了（详见 `docs/tutorial/18-external-review.md`）。

⇒ 用围栏**自己**的报错来证明它活着：下面这条测试如果绿，
  说明此刻确实有一道拦得住出站连接的墙。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：围栏在本进程内生效、三个 socket 入口都补上、豁免标记可用
- **不覆盖**：`subprocess` 子进程（猴补丁跨不过 `fork`）——
  这条边界写在 `conftest.py` 的模块 docstring 里
"""

from __future__ import annotations

import socket

import pytest

from conftest import NetworkUsedInTest

#: 一个**保证不存在**的域名。`.invalid` 由 RFC 2606 保留，
#: 所以即使围栏没生效，这里也只会 DNS 失败而不会真打到谁身上。
#: ⚠️ 不要拿真实域名做这件事 —— 「验证禁网的测试」自己联网就荒谬了。
_SINK = ("no-such-host.invalid", 80)


def test_围栏拦得住create_connection():
    with pytest.raises(NetworkUsedInTest) as e:
        socket.create_connection(_SINK, timeout=0.1)
    assert "不许联网" in str(e.value)


def test_围栏拦得住裸socket的connect():
    """🔴 只补 `create_connection` 是不够的。

    `urllib` 走的就是它，所以第一版只补那一个也能过；
    但换一个 HTTP 库（或者直接 `socket()`）就绕过去了。
    **围栏漏一个口，等于没有围栏。**
    """
    s = socket.socket()
    try:
        with pytest.raises(NetworkUsedInTest):
            s.connect(_SINK)
        with pytest.raises(NetworkUsedInTest):
            s.connect_ex(_SINK)
    finally:
        s.close()


def test_回环没被拦():
    """回环要放行 —— 将来可能有本地假服务器。

    这里只验证**判据**，不真的去连（没人在听，连上也没意义）。
    """
    import conftest
    assert "127.0.0.1" in conftest._ALLOWED_HOSTS


@pytest.mark.network
def test_豁免标记真的能豁免():
    """标记生效的判据：同一个调用不再抛围栏的异常。

    ⚠️ 断言的是「**不是** `NetworkUsedInTest`」，而不是「连上了」——
    CI 上没网时这条也必须绿，否则它自己就变成一条会随机变红的测试，
    而那正是禁网要解决的问题。
    """
    with pytest.raises(OSError) as e:
        socket.create_connection(_SINK, timeout=0.1)
    assert not isinstance(e.value, NetworkUsedInTest), "标记没被认出来"
