"""批 N：在线卡的 run 血缘 —— 测试共用的「开一次真 run」助手。

为什么需要它
------------
`save_card` 的在线分支从批 N 起要求卡带 `run_id` / `evidence_set_id`（外部评审
§7.2），而 `tests/test_store.py::TestRunIdNamespace` 早就要求**任何** `run_id` 列
的值都得追得到 `decision_runs.run_id` —— 不是「非空就行」。批 N 给
`decision_records` 加了 `run_id` 列，于是这两条合起来意味着：测试库里必须真的有
那一行，随手编一个字符串会让命名空间守卫报红（这正是那道守卫存在的意义）。

⇒ 固定值 + 一次真 `open_run()`，各测试文件的卡构造助手共用一份。
各写各的就是第二套口径的起点（L-3）。
"""

from __future__ import annotations

import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "skills"))

from _contract import new_run_context  # noqa: E402
from _store import open_run  # noqa: E402

__all__ = ["TEST_RUN_ID", "TEST_EVIDENCE_SET_ID", "open_test_run"]

#: 测试用的执行尝试 id。固定值（不是随机）—— 断言里要能直接写出它。
TEST_RUN_ID = "t" * 32
#: 测试用的冻结切片 id。契约只要求非空字符串，不校验它在 evidence_sets 里存在
#: （那是 B-13 唯一索引的事，与卡无关）。
TEST_EVIDENCE_SET_ID = "es-test-0000"


def open_test_run(path, *, decision_id: str | None = None,
                  run_id: str = TEST_RUN_ID) -> str:
    """在测试库里真的开一个 run，返回它的 `run_id`。

    幂等：同一个 `run_id` 重复开会撞主键，所以只在 `db` fixture 里调一次。
    """
    open_run(new_run_context(origin="cli", non_interactive=True,
                             decision_id=decision_id, run_id=run_id), path=path)
    return run_id
