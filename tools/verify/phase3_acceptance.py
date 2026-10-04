#!/usr/bin/env python3
"""Phase 3 发布闸门 —— `v1-data-platform-foundation`。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：代码面齐不齐（`--code-only`）、以及上线证据够不够（默认模式）
- **不覆盖**：跑演练、记证据。那是 `bin/biga-data drill-* / acceptance-record`

🔴 三态退出码（与 `phase1_acceptance.py` 同口径，见 `_verdict.py`）
------------------------------------------------------------------
- `0` 两道闸门都过了
- `1` **代码面**有问题 ⇒ 去看代码（少了模块、注册表指向空气、依赖没声明）
- `2` 代码面没问题，但**证据不足** ⇒ 去跑演练、去等够五个交易日

⚠️ 区分 1 和 2 是这个工具存在的主要理由。外部实现把两者都退成 1 ——
于是「还没跑够五天」和「代码写坏了」在 CI 里长一个样，而这两件事的
下一步完全不同。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402

#: P3-11..P3-17 的收口模块。缺任何一个都说明这份 checkout 不完整。
CLOSEOUT_MODULES = (
    "src/easyup_biga/data/replay.py",
    "src/easyup_biga/data/lineage_audit.py",
    "src/easyup_biga/data/integrity.py",
    "src/easyup_biga/data/manifest_compat.py",
    "src/easyup_biga/data/failover.py",
    "src/easyup_biga/data/drills.py",
    "src/easyup_biga/data/acceptance.py",
    "src/easyup_biga/data/finalizer.py",
)


def _code_report(repo: pathlib.Path) -> tuple[list[str], list[str]]:
    from easyup_biga.data.finalizer import _code_checks

    checks, errors = _code_checks(repo)
    missing = [rel for rel in CLOSEOUT_MODULES if not (repo / rel).exists()]
    if missing:
        errors.append(f"P3-11..P3-17 收口模块缺失：{missing}")
    else:
        checks.append(f"P3-11..P3-17 收口模块齐了：{len(CLOSEOUT_MODULES)} 个")
    return checks, errors


def _emit(title: str, checks: list[str], errors: list[str]) -> None:
    print(title)
    for item in checks:
        print(f"  ✅ {item}")
    for item in errors:
        print(f"  ❌ {item}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--db", default="data/biga.db")
    parser.add_argument("--ledger", default="data/phase3_acceptance.jsonl")
    parser.add_argument("--code-only", action="store_true",
                        help="只判代码面，不碰库与验收账本")
    args = parser.parse_args(argv)
    repo = pathlib.Path(args.repo).resolve()

    if args.code_only:
        checks, errors = _code_report(repo)
        _emit("✅ Phase 3 代码面闸门" if not errors else "❌ Phase 3 代码面闸门",
              checks, errors)
        print("  🔶 上线验收：本次未判（--code-only）")
        return _v.PASS if not errors else _v.FAIL

    from easyup_biga.data.finalizer import evaluate_phase3_release

    db = pathlib.Path(args.db)
    ledger = pathlib.Path(args.ledger)
    db = db if db.is_absolute() else repo / db
    ledger = ledger if ledger.is_absolute() else repo / ledger
    status = evaluate_phase3_release(repo=repo, acceptance_ledger=ledger, db_path=db)
    _, module_errors = _code_report(repo)
    code_errors = list(dict.fromkeys(list(status.code_errors) + module_errors))

    _emit("✅ Phase 3 发布闸门" if status.release_ready and not code_errors
          else ("❌ 代码面未过" if code_errors else "🔶 代码面已过，证据不足"),
          list(status.checks), code_errors + list(status.live_errors))
    if code_errors:
        return _v.FAIL
    return _v.PASS if status.live_ready else _v.UNKNOWN


if __name__ == "__main__":
    raise SystemExit(main())
