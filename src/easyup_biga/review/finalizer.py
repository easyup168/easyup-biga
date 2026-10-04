"""P4-9 release finalizer.

Code Ready and Evidence Ready are intentionally separate.  The Code Ready marker/tag may be
created once static checks and a recorded full regression pass both exist.  Evidence Ready is
reported independently and may legitimately be NOT_SUPPORTED or INCONCLUSIVE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import (
    FULL_REGRESSION,
    code_checks,
    current_source_evidence,
    evaluate_evidence_ready,
    load_acceptance_events,
)

__all__ = [
    "BINDING_KEYS", "CODE_READY_TAG", "Phase4ReleaseStatus",
    "evaluate_phase4_release", "write_code_ready_marker",
]

CODE_READY_TAG = "v1-review-measurement-foundation"

#: 回归证据要与**当前这棵树和这个环境**对得上，才算数（F03）。
#: 只证明事件不可改写（哈希链）不等于证明被测的源码就是现在这份。
#:
#: 🔴 这组 key 同时是 **marker 要写进去的那一组** —— 因为 `code_ready` 这个结论
#:    的定义**就是**「这些字段与账本里某条 PASS 逐项相等」。marker 不带它们，
#:    就是一个不指认自己在证明哪棵树的 Code Ready 声明（裁定 14 点名的形状），
#:    而它会留在 `data/` 里活得比那棵树久。
#: ⚠️ 刻意**不含** `repo_root` —— 那是本机绝对路径，marker 可能被贴进工单/PR。
BINDING_KEYS: tuple[str, ...] = (
    "source_tree_sha256", "project_version", "schema_version", "python_version",
    "dependency_fingerprint", "test_selection_sha256", "git_commit",
)


@dataclass(frozen=True, slots=True)
class Phase4ReleaseStatus:
    static_code_ready: bool
    full_regression_ready: bool
    code_ready: bool
    evidence_ready: bool
    evidence_conclusion: str | None
    checks: tuple[str, ...]
    code_errors: tuple[str, ...]
    evidence_errors: tuple[str, ...]
    #: 本次结论所绑定的那棵树与那个环境（`BINDING_KEYS` 逐项）。tuple 而不是
    #: dict，是为了让这个 frozen dataclass 仍然可哈希、且序列化顺序确定。
    source_binding: tuple[tuple[str, str], ...] = ()


def evaluate_phase4_release(
    *,
    repo: Path | str,
    db_path=None,
    acceptance_ledger: Path | str = "data/phase4_acceptance.jsonl",
) -> Phase4ReleaseStatus:
    root = Path(repo).resolve()
    ledger = Path(acceptance_ledger)
    if not ledger.is_absolute():
        ledger = root / ledger
    # 🔴 **两个 list，不是一个 list 再按文案捞回来。**
    #
    #    外部 R1 实现把回归证据的缺失也塞进 `code_errors`，再用
    #    `if e != "missing source-bound FULL_REGRESSION PASS ..."` 把它滤掉，
    #    以此算出 `static_code_ready`。那是 L-13 的形状：**判据挂在错误文案上。**
    #    而 R1 这次改动本身就是把那句文案改了（`missing verified ...` →
    #    `missing source-bound ...`）—— 两处得同时改对。探针实测：保留文案判据、
    #    只把消息里的 `for current` 改成 `for the current`，`static_code_ready`
    #    当场翻面，而**在拆成两个 list 之前没有任何测试会红**。
    #    而这次改动本身就是把那句文案改了 —— 两处得同时改对，改漏一处
    #    `static_code_ready` 就会悄悄翻面，**没有任何测试会红**。
    #
    #    本仓库 Phase 3 的 `data/finalizer.py` 模块头已经为同一件事写过一遍：
    #    「今天它碰巧对，因为文案恰好互不重叠」。这里从头就分开。
    checks, static_errors = code_checks(root)
    events = load_acceptance_events(ledger)
    current = current_source_evidence(root)
    full_regression_ready = any(
        event.event_type == FULL_REGRESSION
        and event.status == "PASS"
        and all(event.detail.get(key) == current[key] for key in BINDING_KEYS)
        for event in events
    )
    regression_errors: list[str] = []
    if full_regression_ready:
        checks.append("full Phase 1-4 regression proof recorded")
    else:
        regression_errors.append(
            "missing source-bound FULL_REGRESSION PASS for current tree/environment"
        )

    evidence = evaluate_evidence_ready(path=db_path, acceptance_ledger=ledger)
    checks.extend(evidence.checks)
    return Phase4ReleaseStatus(
        static_code_ready=not static_errors,
        full_regression_ready=full_regression_ready,
        code_ready=not static_errors and not regression_errors,
        evidence_ready=evidence.ready,
        evidence_conclusion=evidence.conclusion,
        checks=tuple(checks),
        # 给人看的时候合起来；**算结论时不要用它** —— 与 `data/finalizer.py` 同一条规矩。
        code_errors=tuple(dict.fromkeys([*static_errors, *regression_errors])),
        evidence_errors=evidence.errors,
        source_binding=tuple((key, str(current[key])) for key in BINDING_KEYS),
    )


def write_code_ready_marker(status: Phase4ReleaseStatus, path: Path | str) -> Path:
    if not status.code_ready:
        raise RuntimeError("Phase 4 Code Ready gate not passed: " + "; ".join(status.code_errors))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "tag": CODE_READY_TAG,
        "status": "CODE_READY",
        "evidence_ready": status.evidence_ready,
        "evidence_conclusion": status.evidence_conclusion,
        "checks": list(status.checks),
        "evidence_errors": list(status.evidence_errors),
        #: 🔴 没有这一行，marker 就只是一句「CODE_READY」，不说是哪棵树的。
        "source_binding": dict(status.source_binding),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
