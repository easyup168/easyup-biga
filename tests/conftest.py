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

import pathlib
import socket
import sqlite3  # store-exempt: 本文件是围栏本身 —— 它要补丁 sqlite3.connect 才能拦住
#                 任何绕过 _store 的打开方式；走 _store 反而拦不到裸调用

import pytest


def pytest_addoption(parser):
    """P1-3：默认 hermetic —— 需要真实服务/安装环境的测试必须显式开关才跑。"""
    parser.addoption("--run-live", action="store_true", default=False,
                     help="运行需要在线服务的测试（@pytest.mark.live）")
    parser.addoption("--run-installed", action="store_true", default=False,
                     help="运行需要本机已安装 OpenClaw profile 的测试（@pytest.mark.installed）")
    parser.addoption("--run-git", action="store_true", default=False,
                     help="运行需要 git 仓库的测试（@pytest.mark.git，source ZIP 里没有 .git）")


def pytest_collection_modifyitems(config, items):
    """默认跳过 installed / live / git 标记的测试；可以通过 --run-* 开关启用。"""
    switches = {
        "live": config.getoption("--run-live"),
        "installed": config.getoption("--run-installed"),
        "git": config.getoption("--run-git"),
    }
    for marker, enabled in switches.items():
        if enabled:
            continue
        skip = pytest.mark.skip(reason=f"need --run-{marker}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)

#: 允许的目的地。回环留着，是为了将来可能出现的本地假服务器；
#: 真实数据源一个都不在里面。
_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


class NetworkUsedInTest(AssertionError):
    """测试开了出站连接 —— 报错要指路，所以把目的地写进消息里。"""


def _addr(a) -> str:
    if isinstance(a, tuple) and a:
        return str(a[0])
    return str(a)


class ProductionDbUsedInTest(RuntimeError):
    """测试进程打开了生产库 `data/biga.db`。"""


#: 生产库的绝对路径。测试进程**永远**不该打开它 —— 读也不行（见下）。
_PROD_DB = (pathlib.Path(__file__).resolve().parent.parent / "data" / "biga.db").resolve()


def _sqlite_target(database) -> pathlib.Path | None:
    """把 `sqlite3.connect` 的第一个参数解析成绝对路径；内存库返回 None。

    两种形状都要认（`_store.db.connect` 两种都用）：
    普通路径，以及只读时的 `file:/abs/path?mode=ro` URI。
    """
    if isinstance(database, pathlib.Path):
        return database.resolve()
    if not isinstance(database, str) or database.startswith(":memory:"):
        return None
    raw = database
    if raw.startswith("file:"):
        raw = raw[len("file:"):].split("?", 1)[0]
        if not raw or raw.startswith(":"):
            return None
    try:
        return pathlib.Path(raw).resolve()
    except (OSError, ValueError):
        return None


@pytest.fixture(autouse=True)
def _tmp_db_by_default(tmp_path_factory, monkeypatch):
    """每条测试默认拿到**自己的** tmp 库路径。

    🔴 这是「测试读生产库」那一类的**源头修法**，与下面那条围栏是两层：
    这一条让默认行为就是安全的，围栏负责在有人显式绕开时报错。

    实测价值：加围栏时它当场抓出 17 条 `test_news_scan.py` 的测试 ——
    它们调 `is_trading_day()` 不传 `path`，于是一路走到 `DEFAULT_DB_PATH`，
    **读的是生产库的交易日历**。测试从此依赖这台机器上恰好有什么数据，
    而没有任何一行代码说过它想要这个。

    ⚠️ 自己 `monkeypatch.setenv("BIGA_DB_PATH", ...)` 的测试不受影响 ——
    后设的值覆盖这里，顺序由 pytest 保证（同为 function 级）。
    """
    monkeypatch.setenv("BIGA_DB_PATH",
                       str(tmp_path_factory.mktemp("biga-db") / "biga.db"))


@pytest.fixture(autouse=True)
def _no_production_db(monkeypatch):
    """测试进程不许打开 `data/biga.db`。

    为什么是守卫而不是约定
    ----------------------
    `tools/verify/probe.sh` 就是为了这件事写的 —— 它把手工探针跑在一次性库上，
    因为 `_store` 是追加式的、写错的行**删不掉**。它存在之后，同一件事
    **又发生了一次**：2026-09-23 两行 `task_id=BIGA-20260302-001` 的假证据
    （`field=x`、`source=derived:x`）进了真库，来自一次没设 `BIGA_DB_PATH` 的探针。

    > 「记得用 probe.sh」是靠记性的约定，而记性正是追加式存储惩罚的东西。
    > 同一形状第二次发生 ⇒ 把判据从文档搬进进程里。

    这与本文件开头那条禁网围栏是同一个动作：**声称的规矩必须有东西执行它**。

    覆盖什么 / 不覆盖什么
    ---------------------
    - 覆盖：本进程内任何 `sqlite3.connect`，**读写都拦**。
      只读也拦，因为那让测试依赖这台机器上恰好有什么数据 —— 与禁网同一个理由。
    - **不覆盖**：`subprocess` 起的子进程（同禁网围栏，猴补丁跨不过 fork）。
      那类测试要自己把 `BIGA_DB_PATH` 指到 tmp 目录。

    真的需要生产数据 ⇒ 搬去 `tools/verify/`，那里本来就是干这个的。
    """
    real_connect = sqlite3.connect

    def guard(database, *a, **kw):
        if _sqlite_target(database) == _PROD_DB:
            raise ProductionDbUsedInTest(
                f"这条测试打开了生产库 {_PROD_DB} —— 测试进程不许碰它。\n"
                "  理由：`_store` 是追加式的（触发器强制），写错的行**删不掉**。\n"
                "  实际发生过两次，第二次留下 2 行永久的假证据。\n"
                "  ⇒ 用 tmp 库：`monkeypatch.setenv(\"BIGA_DB_PATH\", str(tmp_path / \"biga.db\"))`\n"
                "    或把库路径显式传给 `_store` 的函数（`path=...`）。\n"
                "  真的要读生产数据 ⇒ 搬去 `tools/verify/`。")
        return real_connect(database, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", guard)


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
