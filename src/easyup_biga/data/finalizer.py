"""P3-17 · Phase 3 的发布闸门（`v1-data-platform-foundation`）。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：把「代码面齐了没有」与「上线证据够不够」分成**两个独立结论**，
  并且只在两者同时成立时才允许落发布标记
- **不覆盖**：跑那些演练（`drills.py`）、记那些证据（`acceptance.py`）

🔴 两个结论必须分开算，不能靠错误文案的前缀去分
-----------------------------------------------
外部实现把两类错误堆进同一个 list，再用 `e.startswith("five consecutive")`
之类的前缀把它们捞回来。那是 L-13 的形状：**判据挂在文案上**。
今天它碰巧对，因为文案恰好互不重叠；把某条错误改个措辞，`code_ready`
就会悄悄翻面，而没有任何测试会红。⇒ 这里从头就是两个 list。
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import (
    AcceptanceStatus,
    evaluate_acceptance,
    load_acceptance_events,
    trading_days_from_db,
)
from easyup_biga.domain import STAGE1_AGENTS, required_datasets_for_agents

from .integrity import audit_specialist_provider_boundary
from .provider_registry import PROVIDER_REGISTRY, all_bindings
from .registry import DATASET_REGISTRY

RELEASE_TAG = "v1-data-platform-foundation"

SPECIALIST_PATHS: tuple[str, ...] = (
    "skills/market-calc/scripts/market_calc.py",
    "skills/sector-calc/scripts/sector_calc.py",
    "skills/news-scan/scripts/news_scan.py",
    "skills/technical-calc/scripts/technical_calc.py",
    "skills/emotion-calc/scripts/emotion_calc.py",
)

#: Phase 3 收口时注册表里**应该**有的 dataset，以及各自归哪个里程碑。
#:
#: 🔴 带上里程碑是为了让红灯能自己解释。只报 `missing datasets: [...]` 的话，
#:    读者分不清「哪里写错了」和「那一段还没做」—— 而这两件事的下一步完全不同。
REQUIRED_DATASETS: dict[str, str] = {
    "cn.security_master": "P3-3",
    "cn.trading_calendar": "P3-1",
    "cn.index.daily_bars": "P3-2",
    "cn.equity.daily_bars": "P3-4",
    "cn.security.tradability": "P3-4",
    "cn.equity.adjustment_factors": "P3-5",
    "cn.market.emotion_close": "P3-5",
    "cn.index.realtime_quote": "P3-6",
    "cn.market.breadth": "P3-6",
    "cn.sector.board_snapshot": "P3-6",
    "cn.market.limit_pool": "P3-6",
    "cn.news.flash": "P3-6",
}


@dataclass(frozen=True, slots=True)
class Phase3ReleaseStatus:
    code_ready: bool
    live_ready: bool
    release_ready: bool
    checks: tuple[str, ...]
    code_errors: tuple[str, ...]
    live_errors: tuple[str, ...]
    live: AcceptanceStatus

    @property
    def errors(self) -> tuple[str, ...]:
        """两类合起来给人看。**算结论时不要用它** —— 见模块头。"""
        return self.code_errors + self.live_errors


def _declared_dependencies(pyproject: Path) -> list[str]:
    if not pyproject.exists():
        return []
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data.get("project", {}).get("dependencies", []))


def _called_names(path: Path) -> set[str]:
    """一个源文件里**真正被调用**的函数名（含 `x.foo()` 的 `foo`）。

    注释与 docstring 里出现的名字不算 —— 那正是字符串扫描会误判的地方。
    """
    import ast

    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", None))
            if name:
                out.add(name)
    return out


def _code_checks(repo: Path) -> tuple[list[str], list[str]]:
    """只看代码面。任何要查库/查账本的判据都不属于这里。"""
    checks: list[str] = []
    errors: list[str] = []

    missing = {ds: milestone for ds, milestone in REQUIRED_DATASETS.items()
               if ds not in DATASET_REGISTRY}
    if missing:
        by_milestone: dict[str, list[str]] = {}
        for ds, milestone in sorted(missing.items()):
            by_milestone.setdefault(milestone, []).append(ds)
        for milestone, items in sorted(by_milestone.items()):
            errors.append(f"{milestone} 尚未落地 ⇒ 注册表里没有：{items}")
    else:
        checks.append(f"Dataset Registry 齐了：{len(REQUIRED_DATASETS)} 个")

    bindings = all_bindings()
    bound = {b.dataset_id for b in bindings}
    unbound = sorted(set(DATASET_REGISTRY) - bound)
    if unbound:
        errors.append(f"已注册但没有任何 provider 绑定的 dataset：{unbound}")
    else:
        checks.append(f"每个 dataset 都有 provider 绑定：{len(bindings)} 条边")

    fallbacks = [b for b in bindings if b.role.value == "FALLBACK"]
    if not fallbacks:
        errors.append("整个注册表没有一条 FALLBACK 绑定 —— 降级路径无从演练")
    else:
        checks.append(f"FALLBACK 绑定：{len(fallbacks)} 条")

    # P3-6: 注册表齐不代表迁移完成。生产 Specialist 源码必须已经脱离
    # provider/_sources 边界，否则仍是「名册说归平台管，运行时自己抓」。
    boundary = audit_specialist_provider_boundary(repo, SPECIALIST_PATHS)
    if boundary.ok:
        checks.append(f"P3-6 Specialist Provider 边界已收口：{len(SPECIALIST_PATHS)} 个")
    else:
        errors.append("P3-6 Specialist 仍直连 Provider：" + "; ".join(boundary.errors))

    # P3-7: Agent Registry 是 required_datasets 的唯一源，且每一个需求都必须
    # 能在 Dataset Registry 中解析；Orchestrator 必须消费这份派生清单并冻结。
    required = required_datasets_for_agents(STAGE1_AGENTS)
    missing_required = sorted(set(required) - set(DATASET_REGISTRY))
    if missing_required:
        errors.append(f"P3-7 Agent required_datasets 未注册：{missing_required}")
    else:
        checks.append(f"P3-7 Agent required_datasets 可解析：{len(required)} 个")
    # 🔴 判据是 AST（**真的调用了**），不是字符串扫描。
    #    第一版写的是 `"required_datasets_for_agents" not in orch_text` ——
    #    那样一句注释、一段 docstring、甚至一条「TODO: 以后接上
    #    freeze_required()」都能让它变绿。守卫声称守的是「编排器按名册冻结」，
    #    实际守的是「源码里出现过这两个词」，两者不是同一处（L-13）。
    orch = repo / "skills/decision-card/scripts/orchestrator.py"
    called = _called_names(orch)
    if not {"required_datasets_for_agents", "freeze_required"} <= called:
        missing_calls = sorted(
            {"required_datasets_for_agents", "freeze_required"} - called)
        errors.append(
            f"P3-7 Orchestrator 没有真的调用：{missing_calls} —— "
            f"名册说归平台管，而编排器没按它冻结")
    elif not (repo / "src/easyup_biga/data/snapshot_resolver.py").exists():
        errors.append("P3-7 SnapshotResolver 缺失")
    else:
        checks.append("P3-7 AgentRegistry → freeze_required → SnapshotResolver 链路在位")

    # 🔴 provider → 模块路径从 `ProviderDefinition.modules` 派生，不另写一张表。
    #    外部实现里那张硬编码 map 只覆盖它碰巧知道的 7 个 provider，
    #    注册了但不在 map 里的**一个都不查** —— 守卫查的地方和它声称守的
    #    地方不是同一处（L-13）。
    missing_modules: list[str] = []
    for provider in PROVIDER_REGISTRY.values():
        for module in provider.modules:
            rel = Path("src") / (module.replace(".", "/") + ".py")
            if not (repo / rel).exists():
                missing_modules.append(f"{provider.provider_id} -> {rel}")
    if missing_modules:
        errors.append(f"注册的 provider 指向不存在的模块：{sorted(missing_modules)}")
    else:
        checks.append(f"{len(PROVIDER_REGISTRY)} 个 provider 的模块都在")

    deps = _declared_dependencies(repo / "pyproject.toml")
    if not any(dep.split("[")[0].split(">")[0].split("=")[0].strip() == "duckdb" for dep in deps):
        errors.append("pyproject 没有声明 duckdb 依赖 —— 历史数据面无从读写")
    else:
        checks.append("duckdb 运行时依赖已声明")

    replay = repo / "src/easyup_biga/data/replay.py"
    if not replay.exists() or "network_forbidden" not in replay.read_text(encoding="utf-8"):
        errors.append("离线回放围栏缺失")
    else:
        checks.append("离线回放代码在位")
    return checks, errors


def evaluate_phase3_release(
    *,
    repo: Path | str,
    acceptance_ledger: Path | str,
    db_path=None,
) -> Phase3ReleaseStatus:
    root = Path(repo)
    checks, code_errors = _code_checks(root)
    live_errors: list[str] = []
    events = load_acceptance_events(acceptance_ledger)
    try:
        live = evaluate_acceptance(events, trading_days_between=trading_days_from_db(db_path))
    except Exception as exc:
        # 🔴 读不到日历 ⇒ 连续性**判不出来**，按不过处理（R-3），
        #    并且归进 live 一侧 —— 它不是代码面的问题。
        live = evaluate_acceptance(events, trading_days_between=None)
        live_errors.append(f"交易日历读不到，连续性无法判定：{exc}")
    if live.passed:
        checks.append("上线验收证据齐全")
    live_errors.extend(live.errors)

    code_ready = not code_errors
    live_ready = live.passed and not live_errors
    return Phase3ReleaseStatus(
        code_ready=code_ready,
        live_ready=live_ready,
        release_ready=code_ready and live_ready,
        checks=tuple(checks),
        code_errors=tuple(dict.fromkeys(code_errors)),
        live_errors=tuple(dict.fromkeys(live_errors)),
        live=live,
    )


def write_release_marker(status: Phase3ReleaseStatus, path: Path | str) -> Path:
    """两道闸门都过了才落标记。没过就抛 —— 不写一个 `status: NOT_READY` 的文件。"""
    if not status.release_ready:
        raise RuntimeError(
            "Phase 3 发布闸门未通过，拒绝落发布标记：\n  "
            + "\n  ".join(status.errors))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "tag": RELEASE_TAG,
        "status": "READY",
        "checks": list(status.checks),
        "live_eod_days": list(status.live.eod_days),
        "drills": status.live.drills,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
