"""批 N/O：在线卡的血缘 —— 测试共用的「造一次真实执行尝试」助手。

为什么需要它
------------
`save_card` 的在线分支要求卡带齐三样（外部评审 §7.2）：

    run_id              这次执行尝试
    evidence_set_id     这次用的冻结切片
    input_verdict_refs  这次用到的判定原件（批 O 起也必填）

而这三样都不是随手编一个字符串就行的：

  · `tests/test_store.py::TestRunIdNamespace` 要求**任何** `run_id` 列的值都得追得到
    `decision_runs.run_id` —— 所以库里必须真的有那一行。
  · `verify_verdict_refs()` 会拿每条 ref 去核 `agent_verdicts` 里那一行的
    agent / content_sha256 / task_id / run_id —— 所以必须真的落过 fact 行。
  · 批 O 起 fact 的唯一约束是 `(run_id, agent)`，所以**每个决策号要有自己的 run_id**，
    不能所有测试共用一个（共用会在第二个决策号上撞唯一约束）。

⇒ 一个入口 `provenance_for()` 把这三样一次性备齐。各测试文件各写一份的话，
   上面这三条约束每变一次就要改 N 处（L-3）。
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
from datetime import timedelta

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "skills"))

from _contract import (  # noqa: E402
    CONTRACT_VERSION,
    Evidence,
    FactBundle,
    VerdictRef,
    new_run_context,
    now_cn,
)
from _store import (  # noqa: E402
    connect,
    load_verdict_meta,
    open_run,
    save_fact_bundle,
)

__all__ = ["TEST_RUN_ID", "TEST_EVIDENCE_SET_ID", "open_test_run",
           "run_id_for", "provenance_for"]

#: 默认的执行尝试 id。固定值（不是随机）—— 断言里要能直接写出它。
TEST_RUN_ID = "t" * 32
#: 默认的冻结切片 id。契约只要求非空字符串，不校验它在 evidence_sets 里存在
#: （那是 `ux_evidence_set_per_run` 的事，与卡无关）。
TEST_EVIDENCE_SET_ID = "es-test-0000"


def open_test_run(path, *, decision_id: str | None = None,
                  run_id: str = TEST_RUN_ID) -> str:
    """在测试库里真的开一个 run，返回它的 `run_id`。

    幂等：同一个 `run_id` 重复开会撞主键，所以内部先查一次。
    """
    with connect(path, readonly=True) as conn:
        if conn.execute("SELECT 1 FROM decision_runs WHERE run_id=?",
                        (run_id,)).fetchone():
            return run_id
    open_run(new_run_context(origin="cli", non_interactive=True,
                             decision_id=decision_id, run_id=run_id), path=path)
    return run_id


def run_id_for(decision_id: str) -> str:
    """从决策号派生一个**确定的** run_id。

    🔴 每个决策号要有自己的 run_id：批 O 起 fact 的唯一约束是 `(run_id, agent)`，
    所有测试共用一个 run_id 的话，第二个决策号落 fact 时就会撞唯一约束 ——
    而那是测试夹具的问题，不是被测代码的问题（最难查的那种红）。
    确定性（不是随机）是为了让同一个决策号在一次测试里反复调也拿到同一个 run。
    """
    return hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:32]


def provenance_for(path, decision_id: str, agents) -> tuple[str, str, list[VerdictRef]]:
    """给一次决策备齐 `(run_id, evidence_set_id, refs)`，refs 指向**真落库**的 fact 行。

    幂等：同一个 `(decision_id, agent)` 重复调复用已有那一行（不重复落库，
    否则第二次就撞 `ux_fact_per_run_agent`）。
    """
    rid = run_id_for(decision_id)
    open_test_run(path, decision_id=decision_id, run_id=rid)
    t = now_cn()
    refs: list[VerdictRef] = []
    for agent in agents:
        with connect(path, readonly=True) as conn:
            row = conn.execute(
                "SELECT verdict_id FROM agent_verdicts "
                "WHERE run_id=? AND agent=? AND kind='fact'", (rid, agent)).fetchone()
        if row is None:
            fb = FactBundle(
                task_id=decision_id, agent=agent, status="completed", verdict="PASS",
                result={f"{agent}_x": 1.0}, data_completeness=1.0,
                evidence=[Evidence(field=f"{agent}_x", source=f"derived:{agent}",
                                   value=1.0, as_of=t - timedelta(seconds=60),
                                   retrieved_at=t)], missing=[])
            vid = save_fact_bundle(fb, run_id=rid, path=path)
        else:
            vid = int(row["verdict_id"])
        meta = load_verdict_meta(vid, path=path)
        refs.append(VerdictRef(agent=agent, verdict_id=vid,
                               content_sha256=meta["content_sha256"],
                               contract_version=CONTRACT_VERSION, run_id=rid))
    return rid, TEST_EVIDENCE_SET_ID, refs
