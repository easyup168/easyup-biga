#!/usr/bin/env python3
"""P3-R2 runtime reconciliation gate.

Code-only checks prove every active Phase-3 dataset has a resolvable production
entrypoint whose fixed provider identity agrees with Dataset Registry.  ``--installed``
also probes the local DuckDB runtime; missing runtime dependencies are environment
UNKNOWN, not a false code PASS.
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args(argv)

    from easyup_biga.data.finalizer import REQUIRED_DATASETS
    from easyup_biga.data.producers import PRODUCER_REGISTRY, resolve_producer, validate_producer_binding

    errors: list[str] = []
    checks: list[str] = []
    missing = sorted(set(REQUIRED_DATASETS) - set(PRODUCER_REGISTRY))
    if missing:
        errors.append(f"ACTIVE dataset 没有 producer：{missing}")
    for dataset_id in REQUIRED_DATASETS:
        if dataset_id in missing:
            continue
        try:
            resolve_producer(dataset_id)
            validate_producer_binding(dataset_id)
        except Exception as exc:
            errors.append(f"{dataset_id}: {type(exc).__name__}: {exc}")
    if not errors:
        checks.append(f"{len(REQUIRED_DATASETS)} 个 ACTIVE dataset producer/provider 可解析")

    if args.installed:
        from easyup_biga.data.drills import duckdb_runtime_drill
        drill = duckdb_runtime_drill()
        if drill.passed:
            checks.append(f"DuckDB runtime OK: {drill.detail.get('version')}")
        else:
            print("🔶 Phase 3 Runtime Gate：代码面通过，但本机 runtime 未就绪")
            for item in checks:
                print(f"  ✅ {item}")
            print(f"  🔶 {drill.detail.get('error')}")
            return _v.UNKNOWN

    if errors:
        print("❌ Phase 3 Runtime Gate")
        for item in checks:
            print(f"  ✅ {item}")
        for item in errors:
            print(f"  ❌ {item}")
        return _v.FAIL
    print("✅ Phase 3 Runtime Gate")
    for item in checks:
        print(f"  ✅ {item}")
    return _v.PASS


if __name__ == "__main__":
    raise SystemExit(main())
