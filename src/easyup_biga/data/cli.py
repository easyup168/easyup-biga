"""`bin/biga-data` 的实现。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：数据平台的全部命令行入口 —— 看注册表、看快照、跑 EOD、查历史、
  回放、审计、演练、记验收、判发布闸门
- **不覆盖**：决策链路的命令（`bin/biga-card`）、OpenClaw 本体（`bin/biga`）

🔴 为什么 CLI 的实现在 `src/` 而不是 `bin/` 的 heredoc 里
--------------------------------------------------------
子命令从 2 个长到 18 个之后，heredoc 里的那份既不能被 import、也不能被
单元测试直接调。⇒ 实现搬进包里，`bin/biga-data` 退化成五行壳。
壳的行为（用法错误的措辞、退出码）保持不变，`tests/test_data_registry.py`
的 P10 / P10b 两条判据原样继续钉着它。

退出码
------
0 正常 / 1 用法错误或命令自身失败。
⚠️ 数据任务的三态（裁定 12）体现在 `run-eod*` 的 `status` 字段里，不在进程
退出码上 —— 那两个是不同的层，混在一起会让「今天休市」和「脚本崩了」同码。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from easyup_biga.domain import now_cn
from easyup_biga.persistence import connect, load_dataset_snapshot

from .provider_registry import PROVIDER_REGISTRY, datasets_of
from .registry import DATASET_REGISTRY

USAGE = """用法：
  bin/biga-data list                      列出已注册的数据集
  bin/biga-data providers                 列出数据源，以及各自被哪些数据集用到
  bin/biga-data show-snapshot <id>        看一份 DatasetSnapshot
  bin/biga-data snapshots <dataset_id>    列出某个数据集的全部快照
  bin/biga-data resolve <dataset_id> <cutoff>
                                          按 point-in-time 解析快照
  bin/biga-data status <data_run_id>      看一次数据任务的状态与事件流
  bin/biga-data run-eod [--trade-date]    跑当日 EOD 日线
  bin/biga-data run-eod-bundle [--trade-date]
                                          跑 EOD 整包（日线 + 可交易性）
  bin/biga-data run-adjustment-factors --csv <path> [--trade-date]
                                          导入独立复权因子数据集
  bin/biga-data query-eod <start> <end>   跨日查询（取盘上最新修订）
  bin/biga-data query-eod-asof <cutoff> <start> <end>
                                          跨日查询（只用 cutoff 当时可见的修订）
  bin/biga-data replay <evidence_set_id>  离线回放清单
  bin/biga-data audit-evidence <evidence_set_id>
                                          point-in-time 审计
  bin/biga-data audit-revision <dataset_id> <partition_key_json>
                                          修订链审计
  bin/biga-data drill-raw-tamper          篡改检测演练
  bin/biga-data drill-duckdb              DuckDB 运行时演练
  bin/biga-data acceptance-record <事件类型>
                                          往验收账本记一条
  bin/biga-data acceptance-status         看验收闸门状态
  bin/biga-data finalize                  判 Phase 3 发布闸门
  加 --json 输出机器可读格式（list / providers）"""


def build_parser() -> tuple[argparse.ArgumentParser, frozenset[str]]:
    """返回 parser **与它认识的子命令名**。

    两个一起返回，是为了让 `main()` 不用去翻 argparse 的私有属性拿 choices，
    也不用另维护一份子命令清单 —— 那份清单漏更新时，新子命令会被自己的
    「未知子命令」分支挡掉，而 parser 明明认识它。
    """
    parser = argparse.ArgumentParser(prog="biga-data", add_help=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--json", action="store_true", help="list / providers 输出 JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)
    names: list[str] = []

    def add(name: str) -> argparse.ArgumentParser:
        names.append(name)
        return sub.add_parser(name)

    add("drill-duckdb")
    for name in ("list", "providers"):
        # 🔴 `--json` 在顶层和子命令上**各挂一次**，因为历史用法是
        #    `biga-data list --json`（标志在子命令之后），而 argparse 不把
        #    顶层标志认到子命令后面去。子命令这份用 SUPPRESS 做默认值 ——
        #    没给就不写这个属性，于是顶层的 `biga-data --json list` 也不会
        #    被子命令的 False 盖掉。两种写法都成立，不是二选一。
        add(name).add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    item = add("show-snapshot")
    item.add_argument("snapshot_id")
    item = add("snapshots")
    item.add_argument("dataset_id")
    item = add("resolve")
    item.add_argument("dataset_id")
    item.add_argument("knowledge_cutoff")
    item = add("status")
    item.add_argument("data_run_id")
    for name in ("run-eod", "run-eod-bundle"):
        item = add(name)
        item.add_argument("--trade-date")
        item.add_argument("--new-revision", action="store_true")
    item = add("run-adjustment-factors")
    item.add_argument("--csv", required=True)
    item.add_argument("--trade-date")
    item.add_argument("--new-revision", action="store_true")
    item = add("query-eod")
    item.add_argument("start_date")
    item.add_argument("end_date")
    item.add_argument("--instrument", action="append", default=[])
    item = add("query-eod-asof")
    item.add_argument("knowledge_cutoff")
    item.add_argument("start_date")
    item.add_argument("end_date")
    item.add_argument("--instrument", action="append", default=[])
    item = add("replay")
    item.add_argument("evidence_set_id")
    item = add("audit-evidence")
    item.add_argument("evidence_set_id")
    item = add("audit-revision")
    item.add_argument("dataset_id")
    item.add_argument("partition_key_json")
    item = add("drill-raw-tamper")
    item.add_argument("--work-root", default="data/acceptance-drill")
    item = add("acceptance-record")
    item.add_argument("event_type")
    item.add_argument("--ledger", default="data/phase3_acceptance.jsonl")
    item.add_argument("--status", choices=("PASS", "FAIL"), default="PASS")
    item.add_argument("--trade-date")
    item.add_argument("--detail-json", default="{}")
    item = add("acceptance-status")
    item.add_argument("--ledger", default="data/phase3_acceptance.jsonl")
    item = add("finalize")
    item.add_argument("--ledger", default="data/phase3_acceptance.jsonl")
    item.add_argument("--repo", default=".")
    item.add_argument("--write-marker")
    return parser, frozenset(names)


# ── 注册表两条：保持 heredoc 时代的输出格式 ────────────────────────────────
def _dataset_rows() -> list[dict[str, Any]]:
    return [
        {
            "dataset_id": d.dataset_id,
            "title": d.title,
            "primary_provider": d.primary_provider,
            "fallback_providers": list(d.fallback_providers),
            "validation_providers": list(d.validation_providers),
            "partition_keys": list(d.partition_keys),
            "storage_policy": d.storage_policy,
            "raw_table": d.raw_table,
            "fact_table": d.fact_table,
            "consumers": list(d.consumers),
        }
        for d in DATASET_REGISTRY.values()
    ]


def _provider_rows() -> list[dict[str, Any]]:
    return [
        {
            "provider_id": p.provider_id,
            "title": p.title,
            "source_prefix": p.source_prefix,
            "modules": list(p.modules),
            # 🔴 派生而非手写 —— 见 data/provider_registry.py 的模块头
            "datasets": list(datasets_of(p.provider_id)),
        }
        for p in PROVIDER_REGISTRY.values()
    ]


def _print_datasets(rows: list[dict[str, Any]]) -> None:
    print(f"已注册 {len(rows)} 个数据集：\n")
    for r in rows:
        extra = ""
        if r["fallback_providers"]:
            extra += f" · fallback {'/'.join(r['fallback_providers'])}"
        if r["validation_providers"]:
            extra += f" · 校验 {'/'.join(r['validation_providers'])}"
        print(f"  {r['dataset_id']:25} {r['primary_provider']:8}{extra}")
        print(f"  {'':25} {r['title']}")
        tables = r["raw_table"] + (f" + {r['fact_table']}" if r["fact_table"] else "")
        print(f"  {'':25} → {r['storage_policy']}（{tables}）")
        print(f"  {'':25} 读它的：{'、'.join(r['consumers'])}\n")


def _print_providers(rows: list[dict[str, Any]]) -> None:
    print(f"已注册 {len(rows)} 个数据源：\n")
    for r in rows:
        print(f"  {r['provider_id']:15} {r['title']}")
        print(f"  {'':15} source 前缀 {r['source_prefix']}: · "
              f"它挂了会影响：{'、'.join(r['datasets']) or '（无）'}\n")


def _snapshots(dataset_id: str, db) -> list[dict[str, Any]]:
    with connect(db, readonly=True) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT snapshot_id,partition_key_json,status,data_version,knowledge_cutoff,"
                "created_at FROM dataset_snapshots WHERE dataset_id=? ORDER BY created_at DESC",
                (dataset_id,),
            ).fetchall()
        ]


def _status(data_run_id: str, db) -> dict[str, Any]:
    with connect(db, readonly=True) as conn:
        run = conn.execute(
            "SELECT * FROM data_job_runs WHERE data_run_id=?", (data_run_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT seq,from_state,to_state,at,detail_json FROM data_run_events "
            "WHERE data_run_id=? ORDER BY seq",
            (data_run_id,),
        ).fetchall()
    return {"run": dict(run) if run else None, "events": [dict(x) for x in events]}


def _publish_result(result) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "data_run_id": result.data_run_id,
        "snapshot_id": result.snapshot_id,
        "partition_id": result.partition_id,
        "reused": result.reused,
    }


def _dispatch(args: argparse.Namespace) -> Any:
    cmd = args.cmd
    if cmd == "list":
        return _dataset_rows()
    if cmd == "providers":
        return _provider_rows()
    if cmd == "show-snapshot":
        return load_dataset_snapshot(args.snapshot_id, path=args.db)
    if cmd == "snapshots":
        return _snapshots(args.dataset_id, args.db)
    if cmd == "status":
        return _status(args.data_run_id, args.db)
    if cmd == "resolve":
        from .snapshot_resolver import resolve_snapshot
        item = resolve_snapshot(args.dataset_id, args.knowledge_cutoff, path=args.db)
        return None if item is None else {
            "dataset_id": item.dataset_id,
            "snapshot_id": item.snapshot_id,
            "status": item.status,
            "knowledge_cutoff": item.knowledge_cutoff,
            "partition_key": dict(item.partition_key),
        }
    if cmd == "run-eod":
        from .datasets.eod_daily_bars import run
        trade_date = args.trade_date or now_cn().strftime("%Y%m%d")
        return _publish_result(run(
            trade_date, db_path=args.db, data_root=args.data_root,
            new_revision=args.new_revision))
    if cmd == "run-eod-bundle":
        from .eod_pipeline import run_eod_bundle
        trade_date = args.trade_date or now_cn().strftime("%Y%m%d")
        result = run_eod_bundle(
            trade_date, db_path=args.db, data_root=args.data_root,
            new_revision=args.new_revision)
        return {
            "status": result.status.value,
            "trade_date": result.trade_date,
            "daily_bars": _publish_result(result.daily_bars),
            "universe_count": result.universe_count,
            "normalized_bar_count": result.normalized_bar_count,
            # 未发布成数据集，只是覆盖率的分母 —— 见 eod_pipeline 的模块头
            "tradability_counts": dict(result.tradability_counts),
        }
    if cmd == "run-adjustment-factors":
        from .datasets.adjustment_factors import CsvAdjustmentFactorProvider, run
        trade_date = args.trade_date or now_cn().strftime("%Y%m%d")
        return _publish_result(run(
            CsvAdjustmentFactorProvider(args.csv), trade_date,
            db_path=args.db, data_root=args.data_root,
            new_revision=args.new_revision))
    if cmd == "query-eod":
        from .analytics import query_eod_between
        return query_eod_between(
            data_root=args.data_root, start_date=args.start_date, end_date=args.end_date,
            instrument_ids=args.instrument or None)
    if cmd == "query-eod-asof":
        from .analytics import query_eod_as_of
        return query_eod_as_of(
            db_path=args.db, knowledge_cutoff=args.knowledge_cutoff,
            start_date=args.start_date, end_date=args.end_date,
            instrument_ids=args.instrument or None)
    if cmd == "replay":
        from .replay import offline_replay_bundle
        bundle = offline_replay_bundle(
            args.evidence_set_id, path=args.db, data_root=args.data_root)
        return {
            "evidence_set_id": bundle.evidence_set_id,
            "manifest_version": bundle.manifest_version,
            "knowledge_cutoff": bundle.knowledge_cutoff,
            "datasets": {
                k: {
                    "snapshot_id": v.snapshot_id,
                    "partition_ids": list(v.partition_ids),
                    "data_version": v.data_version,
                    "knowledge_cutoff": v.knowledge_cutoff,
                }
                for k, v in sorted(bundle.datasets.items())
            },
            "legacy_raw_snapshot_ids": list(bundle.legacy_raw_snapshot_ids),
        }
    if cmd == "audit-evidence":
        from .lineage_audit import audit_evidence_set_point_in_time
        audit = audit_evidence_set_point_in_time(
            args.evidence_set_id, path=args.db, data_root=args.data_root)
        return {"ok": audit.ok, "checks": list(audit.checks), "errors": list(audit.errors)}
    if cmd == "audit-revision":
        from .lineage_audit import audit_revision_chain
        audit = audit_revision_chain(
            args.dataset_id, json.loads(args.partition_key_json), path=args.db)
        return {"ok": audit.ok, "checks": list(audit.checks), "errors": list(audit.errors)}
    if cmd in {"drill-raw-tamper", "drill-duckdb"}:
        from .drills import duckdb_runtime_drill, raw_tamper_drill
        result = (raw_tamper_drill(work_root=args.work_root) if cmd == "drill-raw-tamper"
                  else duckdb_runtime_drill())
        return {"passed": result.passed, "name": result.name, "detail": result.detail}
    if cmd == "acceptance-record":
        from .acceptance import record_acceptance_event
        event = record_acceptance_event(
            args.ledger, args.event_type, status=args.status,
            trade_date=args.trade_date, detail=json.loads(args.detail_json))
        return {
            "event_type": event.event_type,
            "status": event.status,
            "trade_date": event.trade_date,
            "event_hash": event.event_hash,
        }
    if cmd == "acceptance-status":
        from .acceptance import evaluate_acceptance, load_acceptance_events, trading_days_from_db
        status = evaluate_acceptance(
            load_acceptance_events(args.ledger),
            trading_days_between=trading_days_from_db(args.db))
        return {
            "passed": status.passed,
            "eod_days": list(status.eod_days),
            "drills": status.drills,
            "errors": list(status.errors),
        }
    if cmd == "finalize":
        from .finalizer import RELEASE_TAG, evaluate_phase3_release, write_release_marker
        status = evaluate_phase3_release(
            repo=args.repo, acceptance_ledger=args.ledger, db_path=args.db)
        if args.write_marker:
            write_release_marker(status, args.write_marker)
        return {
            "code_ready": status.code_ready,
            "live_ready": status.live_ready,
            "release_ready": status.release_ready,
            "checks": list(status.checks),
            "code_errors": list(status.code_errors),
            "live_errors": list(status.live_errors),
            "tag": RELEASE_TAG if status.release_ready else None,
        }
    raise AssertionError(cmd)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser, known = build_parser()
    # 🔴 未知子命令自己拦，不交给 argparse：P10b 钉的是「未知子命令」这个措辞
    #    与非零退出码。argparse 的默认文案是英文的 invalid choice，
    #    换成它等于悄悄改掉一条被测过的对外行为。
    positional = [a for a in argv if not a.startswith("-")]
    if positional and positional[0] not in known:
        print(f"未知子命令 {positional[0]!r}\n\n{USAGE}", file=sys.stderr)
        return 1
    if not positional:
        print(USAGE, file=sys.stderr)
        return 1

    args = parser.parse_args(argv)
    out = _dispatch(args)
    if args.cmd in {"list", "providers"} and not args.json:
        (_print_datasets if args.cmd == "list" else _print_providers)(out)
        return 0
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
