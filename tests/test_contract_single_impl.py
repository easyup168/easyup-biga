"""铁律 4 的常驻守卫：契约只有一份实现。

AST 全仓扫描，拦三种「第二套实现」的形状：

  A. 重名类      —— 仓库里除 `src/easyup_biga/domain/`（批 H-I 前是 `skills/_contract/`）
                     外再定义 `Evidence` / `AgentVerdict` / `DecisionCard`。
  B. 近名类      —— 定义 `EmotionVerdict` / `MarketEvidence` / `MiniDecisionCard`
                     之类「看起来是自己那一版」的类。这是契约漂移最常见的起点。
  C. 字典版契约  —— 不定义类，直接手搓一个 key 长得和契约一样的 dict。
                     这是最隐蔽的一种：它不引入任何新符号，grep 也搜不到。

为什么这条值得写一个专门的测试：
同一判据在多处各写一遍时，错法全是**静默**的 —— 每一份都返回一个看似合理的值，
没有任何一处会报错。等到发现口径不一致，往往已经过了几个月。

需要合法地写一个「长得像契约」的东西时（例如测试里的假对象），
在该行或上一行加注释豁免::

    class FakeEvidence:  # contract-exempt: 测试用的鸭子类型，用于断言契约拒绝它
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
# 🔴 批 H-I：契约/存储的**真实实现**已迁至 src/easyup_biga/。守卫认的是真实类
#    定义在哪，不是薄壳在哪——旧路径 skills/_contract、skills/_store 迁移后只剩
#    re-export 薄壳（无类定义）。这两个常量若仍指旧路径，「唯一实现」会在新目录上
#    **静默失效**：在 src/easyup_biga/domain 里另定义一个 Evidence 不会被抓到。
STORE_DIR = REPO / "src" / "easyup_biga" / "persistence"
CONTRACT_DIR = REPO / "src" / "easyup_biga" / "domain"

from _scan import is_external_reference, repo_files  # noqa: E402

EXEMPT_MARKER = "contract-exempt:"

#: A —— 契约类名，全仓只许 `_contract/` 定义
#: 🔴 A-II 补上 `MissingItem`（早就是契约对象，之前漏登记）与 `VerdictRef`
#:    （A6 新增）；批 B 补上 `RunContext`（运行身份）—— 都在这里，不新开一份判据。
CONTRACT_NAMES = {"Evidence", "AgentVerdict", "DecisionCard", "MissingItem",
                  "VerdictRef", "RunContext"}

#: B —— 近名类：名字里带这些词根的类定义，都算另起炉灶
NAME_ROOTS = ("Evidence", "Verdict", "DecisionCard")

#: pytest 的测试容器类按约定以 Test 开头，它们是分组而非数据结构，不参与 B 的判定。
#: 这是规则的精确化，不是豁免 —— 豁免要写在被扫的代码里，规则该准的地方要自己准。
TEST_CLASS_PREFIX = "Test"

#: C —— 字典版契约：键集合 + 其中的「特征键」
DICT_SHAPES: dict[str, tuple[set[str], set[str]]] = {
    "AgentVerdict": (
        {"task_id", "agent", "status", "verdict", "result",
         "data_completeness", "evidence", "warnings", "missing", "elapsed_ms"},
        {"task_id", "verdict", "elapsed_ms", "missing"},
    ),
    "Evidence": (
        {"field", "source", "value", "as_of", "retrieved_at", "calc_version", "label"},
        {"as_of", "retrieved_at", "calc_version"},
    ),
    "DecisionCard": (
        {"decision_id", "status", "headline", "verdicts",
         "missing", "synthesis", "model_ref"},
        {"decision_id", "headline", "verdicts", "model_ref"},
    ),
}

MIN_DICT_KEYS = 4


def _py_files() -> list[pathlib.Path]:
    # 🔴 走 git 的口径，不走文件系统 —— 见 `tests/_scan.py` 的「worktree 副本」一节
    return repo_files(".py")


def _is_exempt(lines: list[str], lineno: int) -> bool:
    """该行或上一行带豁免注释。"""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines) and EXEMPT_MARKER in lines[idx]:
            return True
    return False


def _in_contract(p: pathlib.Path) -> bool:
    """`p` 不受「契约只有一份实现」约束——本体所在目录，或只读参考材料。

    后者会真的出现：`docs/external/` 底下的外部评审/上游文档常带示意代码
    （比如一份示范 `Evidence`/`DecisionCard` 该长什么样的 `domain_models.py`），
    名字撞上契约类是因为描述的是同一个域，不是本仓库长出了第二份实现。
    """
    return CONTRACT_DIR in p.parents or is_external_reference(p)


ALL_FILES = _py_files()


def test_docs_external下的示意代码不算第二份实现():
    """2026-09-23 实测撞到：`test_scan_fallback.py` 模拟"没有 git"退化成纯
    文件系统遍历时，会扫到 `docs/external/` 下外部评审自带的示意代码
    （一份示范 `Evidence`/`DecisionCard` 该长什么样的 `domain_models.py`，
    平时被 gitignore、正常 git 路径天然看不到），误判成契约的第二份实现。"""
    fake = REPO / "docs" / "external" / "some-review" / "reference" / "domain_models.py"
    assert _in_contract(fake), "docs/external/ 下的文件应该被当作只读参考材料排除"


def test_扫描范围非空():
    """守卫的守卫：扫描本身失效时必须报红，而不是安静地全绿。

    一个扫不到任何文件的扫描器永远通过 —— 这正是「零消费方」失败模式在测试里的化身。
    """
    assert len(ALL_FILES) >= 3, f"只扫到 {len(ALL_FILES)} 个 .py，扫描范围可能坏了"
    assert any(_in_contract(p) for p in ALL_FILES), "没扫到 _contract/ 本身"


def test_A_契约类名只在_contract_下定义():
    offenders: list[str] = []
    for p in ALL_FILES:
        if _in_contract(p):
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(p))):
            if isinstance(node, ast.ClassDef) and node.name in CONTRACT_NAMES:
                if _is_exempt(lines, node.lineno):
                    continue
                offenders.append(f"{p.relative_to(REPO)}:{node.lineno} class {node.name}")
    assert not offenders, (
        "发现契约类的第二份实现（铁律 4）：\n  " + "\n  ".join(offenders)
        + "\n一切契约必须 `from _contract import ...`。"
    )


def test_B_不许定义近名的自建契约类():
    offenders: list[str] = []
    for p in ALL_FILES:
        if _in_contract(p):
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(p))):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in CONTRACT_NAMES:
                continue  # 由 A 负责
            if node.name.startswith(TEST_CLASS_PREFIX):
                continue  # pytest 分组容器
            if any(root in node.name for root in NAME_ROOTS):
                if _is_exempt(lines, node.lineno):
                    continue
                offenders.append(f"{p.relative_to(REPO)}:{node.lineno} class {node.name}")
    assert not offenders, (
        "发现自建的近名契约类（铁律 4）：\n  " + "\n  ".join(offenders)
        + "\n需要扩展契约就改 src/easyup_biga/domain/，不要在局部另起一个。"
    )


def test_C_不许手搓字典版契约():
    offenders: list[str] = []
    for p in ALL_FILES:
        if _in_contract(p):
            continue
        src = p.read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = ast.parse(src, filename=str(p))
        for node in ast.walk(tree):
            keys: set[str] = set()
            if isinstance(node, ast.Dict):
                keys = {
                    k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "dict":
                keys = {kw.arg for kw in node.keywords if kw.arg}
            if not keys:
                continue
            for shape, (full, distinctive) in DICT_SHAPES.items():
                hit = keys & full
                if len(hit) >= MIN_DICT_KEYS and keys & distinctive:
                    if _is_exempt(lines, node.lineno):
                        continue
                    offenders.append(
                        f"{p.relative_to(REPO)}:{node.lineno} "
                        f"疑似手搓 {shape}，命中键 {sorted(hit)}"
                    )
                    break
    assert not offenders, (
        "发现字典形式的第二套契约（铁律 4）：\n  " + "\n  ".join(offenders)
        + "\n请构造 _contract 的 dataclass 再调 .to_dict()；"
        "确有必要时加注释 `# contract-exempt: <理由>`。"
    )


def test_contract_目录里每个契约恰好定义一次():
    """_contract/ 内部也不许重复定义 —— 一份实现的前提是它自己不分裂。"""
    seen: dict[str, list[str]] = {n: [] for n in CONTRACT_NAMES}
    for p in sorted(CONTRACT_DIR.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in CONTRACT_NAMES:
                seen[node.name].append(f"{p.relative_to(REPO)}:{node.lineno}")
    for name, locs in seen.items():
        assert len(locs) == 1, f"{name} 应恰好定义 1 次，实际 {len(locs)} 次：{locs}"


@pytest.mark.parametrize("name", sorted(CONTRACT_NAMES))
def test_契约可从包根导入(name):
    import _contract

    assert hasattr(_contract, name), f"_contract 未导出 {name}"


# ───────────────────────────── 消费方必须走 from_dict（外部评审 F11）
#
# F11 的关键补充实验（评审做的）：把一条违反铁律的 verdict_json
# 用绕过 `db.py` 的裸连接直接 INSERT 进 `agent_verdicts` —— 写入不报错；
# 但用正规的 `load_verdict()` 读回来时**立刻**抛出引用具体铁律编号的
# ValueError。`from_dict()` 在每次反序列化时都重跑一遍 `__post_init__`，
# 构成运行时的第二道防线。
#
# ⇒ 所以静态扫描的盲区（逐键赋值、`type()` 动态建类）**不等于**
#   坏数据会被正常读取路径无声接受。
#
# 🔴 **前提是所有消费方都老实走 `from_dict`。**
#    评审检查过 `synthesize.py` 与 `card_ops.py`，两个都是干净的。
#    但上这条守卫时又扫出一个它没查到的：`missing_ledger.py` 当时
#    直接 `json.loads(card_json)`，绕开了那道防线。
#
# 「目前检查过的路径都是」这种前提，**得有机器守着才站得住**。

_JSON_COLUMNS = ("card_json", "verdict_json")


def test_契约对象只从_store取_不自己解JSON列():
    bad = []
    for path in repo_files(".py"):
        if STORE_DIR in path.parents or path.parent.name == "tests":
            continue
        src = path.read_text(encoding="utf-8")
        if not any(col in src for col in _JSON_COLUMNS):
            continue
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src, filename=str(path))):
            # json.loads(row["card_json"]) 这种形状
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") in ("loads", "load")):
                continue
            seg = ast.get_source_segment(src, node) or ""
            if any(col in seg for col in _JSON_COLUMNS) and \
                    "contract-exempt" not in lines[node.lineno - 1]:
                bad.append(f"{path.relative_to(REPO)}:{node.lineno}  {seg[:60]}")
    assert not bad, (
        "这些地方绕开了 `from_dict()` 的读取时校验：\n  " + "\n  ".join(bad) + "\n"
        "  `from_dict()` 每次反序列化都重跑一遍铁律校验 ——\n"
        "  那是静态扫描被绕过时唯一的后备防线，绕开它就什么都不剩了。\n"
        "  改用 `_store` 的 `load_online_card()` / `load_card_by_record_id()` / "
        "`load_verdict()`。"
    )
