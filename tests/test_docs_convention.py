"""文档规约 —— 由机器强制，不靠人记得。

规约本身在 `docs/README.md`。这里只负责让违反它的改动**红**。

为什么需要它：本项目已经因为文档命名吃过两次亏 ——

1. `phase2-market.md` 按**步骤**切分常青文档，2.1 做完它就过期了，
   而名字看起来像「Phase 2 的设计文档」
2. `architecture.md` 顶部长期写着「设计中，未开工」，
   那时 Phase 2 都做完两步了 —— 因为一段**历史**躺在**常青**文档里

两次的共同点：**没人说清每份文档的生命周期**，于是内容往最近的那份里落。
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

import pytest

from _scan import repo_files

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

#: 五个类别标记，见 docs/README.md
CATEGORIES = ("常青", "阶段", "过程", "操作", "只读")

PATTERNS = {
    "design": re.compile(r"^(phase-\d+-[a-z0-9]+(-[a-z0-9]+)*|[a-z0-9]+(-[a-z0-9]+)*)\.md$"),
    "tutorial": re.compile(r"^(\d{2}-[a-z0-9]+(-[a-z0-9]+)*|README)\.md$"),
    "guide": re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$"),
    "external": re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*\.md$"),
}


def _md_files() -> list[pathlib.Path]:
    return sorted(p for p in DOCS.rglob("*.md"))


def _head(p: pathlib.Path) -> str:
    """文档开头到第一条分隔线为止 —— 声明必须在这里面。"""
    return p.read_text(encoding="utf-8").split("\n---", 1)[0]


def test_扫到了文档():
    """防空转：扫描器坏掉时会「零违规」通过，那是最糟的绿。"""
    assert len(_md_files()) >= 15


@pytest.mark.parametrize("path", _md_files(), ids=lambda p: str(p.relative_to(DOCS)))
def test_文件名符合所在目录的规则(path: pathlib.Path):
    top = path.relative_to(DOCS).parts[0]
    if path.parent == DOCS:            # docs/README.md 本身
        assert path.name == "README.md"
        return
    pat = PATTERNS.get(top)
    assert pat is not None, f"docs/{top}/ 不在规约里 —— 先去 docs/README.md 定义它"
    assert pat.match(path.name), \
        f"docs/{top}/{path.name} 不符合命名规则 {pat.pattern}"


@pytest.mark.parametrize("path", _md_files(), ids=lambda p: str(p.relative_to(DOCS)))
def test_开头声明了类别与覆盖范围(path: pathlib.Path):
    head = _head(path)
    # 允许带后缀，例如 **阶段 · 已完成并冻结**
    marker = re.compile(r"\*\*(" + "|".join(CATEGORIES) + r")[^*]*\*\*")
    assert marker.search(head), \
        f"{path.name} 开头没有类别标记（{' / '.join(CATEGORIES)}）"
    assert "**覆盖**：" in head, f"{path.name} 没写覆盖什么"
    assert "**不覆盖**：" in head, \
        (f"{path.name} 没写**不覆盖**什么 —— 不写边界，内容就会往最近的那份文档里落")


def test_常青文档不带日期或版本号():
    """我们自己的文档，历史在 git 里。文件名带版本号等于邀请别人新建 v2。"""
    for p in (DOCS / "design").glob("*.md"):
        assert not re.search(r"-v\d|\d{4}-\d{2}-\d{2}", p.stem), \
            f"{p.name} 带了版本号或日期 —— design/ 只描述当前状态"


def test_阶段文档必须带主题():
    """`phase2.md` 答不出「什么的 phase 2」。"""
    for p in (DOCS / "design").glob("phase*.md"):
        assert re.match(r"^phase-\d+-[a-z]", p.stem), \
            f"{p.name} 应形如 phase-2-specialists.md —— 主题不能省"


def test_一个阶段只有一份设计文档():
    """🔴 按步骤切分常青文档，就会有 N 份各自过期的文档。"""
    nums = Counter(m.group(1) for p in (DOCS / "design").glob("phase-*.md")
                   if (m := re.match(r"^phase-(\d+)-", p.stem)))
    dup = [n for n, c in nums.items() if c > 1]
    assert not dup, \
        f"Phase {dup} 有多份设计文档 —— 合成一份。按步骤切必然各自过期"


def test_教程序号唯一且连续():
    nums = sorted(int(p.stem[:2]) for p in (DOCS / "tutorial").glob("[0-9][0-9]-*.md"))
    assert nums == list(range(1, len(nums) + 1)), f"教程序号不连续：{nums}"


def test_外部材料带日期前缀():
    """它是别人在某个时刻的想法的快照，日期是它的一部分。"""
    for p in (DOCS / "external").glob("*.md"):
        assert re.match(r"^\d{4}-\d{2}-\d{2}-", p.name), \
            f"{p.name} 缺日期前缀"
        assert "只读" in _head(p), f"{p.name} 必须标注只读"


def test_没有歧义的目录名():
    """曾经同时存在 docs/reference/ 与 docs/design/reference/。"""
    names = [d.name for d in DOCS.rglob("*") if d.is_dir()]
    assert len(names) == len(set(names)), f"存在同名目录：{names}"
    assert "reference" not in names, \
        "`reference` 说不出生命周期 —— 外部只读材料放 docs/external/"


# ── 教程只写正常操作 ────────────────────────────────────────────────
#
# 🔴 这条守卫加于 2026-09-21，起因是一次人工 review。
#
# 教程原本刻意以「在一台有邻居的机器上施工」为差异化卖点，于是共存细节
# 被大量写进正文：读它的配置、它的定时任务怎么扫 PATH、它跑在哪个端口。
#
# 问题是这些属于**本机的特殊情况**，而教程是公开的。
# 而且它们读起来像「踩坑记录」—— 教程恰恰是最鼓励写踩坑的地方，
# 所以这类内容会被顺手写进来，八项审查一项都抓不到。
#
# 边界：**隔离本身要写**（独立目录 / 独立端口 / 崩溃不波及是通用工程问题），
#       **那套系统是什么、怎么配的、我怎么读了它 —— 不写。**

#: 拟人化或指向特定既存系统的措辞。中性说法用「同机已有实例」。
_TUTORIAL_FORBIDDEN = (
    "邻居", "既存", "实盘", "量化交易", "交易系统", "下单通道",
    "长期运行的系统", "生产环境",
)


@pytest.mark.parametrize("path", sorted((DOCS / "tutorial").glob("*.md")))
def test_教程不写与既存系统相关的内容(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    hits = [w for w in _TUTORIAL_FORBIDDEN if w in text]
    assert not hits, (
        f"{path.name} 出现 {hits} —— 教程只写正常操作。\n"
        "  隔离本身（独立目录/端口/崩溃不波及）要写，那是通用工程问题；\n"
        "  「那套系统是什么、怎么配的、我怎么读了它」不写。\n"
        "  中性措辞用「同机已有实例」。"
    )


# ───────────────────────────────────────────────────────────── 测试条数不漂移
#
# 外部评审 F22：同一个事实（跑出来多少条测试）在四处手写，实测 475 时
# 三处写着 417、一处写着 445。这是 L-3 的标准形状 ——
# **改了一份忘了另一份，剩下那份仍然看起来很自信。**
#
# 🔴 为什么不是「记得同步」就能解决：
# 这个数**每加一条测试就变**，而加测试的人没有任何理由想到去翻 README 的徽章。
# 靠记性的约定在高频变化的事实上必然失守。
#
# 判据取自 `request.session.items` —— pytest 自己收集到的条目数，
# 不是另写一个计数器（那就是第二套口径了）。

#: 必须与实测一致的文档。**不含**三类：
#:   · `docs/tutorial/` —— 过程文档写完即冻结，里面的数字是当时的快照
#:   · `CHANGELOG.md`   —— 历史记录，改它等于篡改历史
#:   · `docs/external/` —— 只读
_LIVE_TEST_COUNT_DOCS = ("README.md", "CLAUDE.md", "docs/guide/review-prompt.md")

_TEST_COUNT_PATTERNS = (
    r"(\d+)%20TESTS",            # README 的徽章
    r"(\d+)\s*条测试",            # 正文散文
    r"测试\s*\|\s*(\d+)\s*条",     # CLAUDE.md 的状态表
)


#: 行内豁免标记。历史快照（某次验收当时是多少条）本来就不该跟着涨。
#: 🔴 默认必须是最新的，**例外要自己举手** —— 和只追加触发器那条同一个道理：
#:    手工维护「哪些要查」的名单，漏掉的永远是没想到的那个。
_FROZEN_MARK = "冻结"


def test_文档里的测试条数与实测一致(request):
    collected = len(request.session.items)
    # 只在**全量**跑时校验：跑子集时条数本来就对不上，那不是文档的错
    got = {item.path for item in request.session.items}
    if got != set((REPO / "tests").glob("test_*.py")):
        pytest.skip("跑的是子集，条数无从比较")

    bad = []
    for rel in _LIVE_TEST_COUNT_DOCS:
        seen = 0
        for line in (REPO / rel).read_text(encoding="utf-8").splitlines():
            nums = [int(m) for pat in _TEST_COUNT_PATTERNS
                    for m in re.findall(pat, line)]
            if not nums:
                continue
            seen += 1
            if _FROZEN_MARK in line:
                continue
            bad += [(rel, n) for n in nums if n != collected]
        # 🔴 没找到 ≠ 通过。文档把这句话删了，这条检查就变成「无事可查」——
        #    而那正是本项目反复栽的「通过是因为什么都没查」。
        assert seen, f"{rel} 里找不到测试条数 —— 是被删了，还是换了写法？"

    assert not bad, (
        f"实测 {collected} 条，文档里写着 {bad}。\n"
        f"  改掉这些数字；如果某处是历史快照（某次验收当时的数），\n"
        f"  在同一行加注释标记「{_FROZEN_MARK}」把它排除掉。\n"
        "  教程 / CHANGELOG / external 不在校验范围内。"
    )


# ───────────────────────────────────────────────── 设计文档不许点名不存在的文件
#
# 外部评审 F7：`architecture.md` §2.4 的目录树把 `placebo.py` / `reachability.py`
# 与真实文件并排列出，§9 的 L-4 / L-7 用**与真实机制相同的陈述句式**点名它们
# 为「新系统的防护机制」。而 `git log --all -- '**/placebo.py'` 是空的 ——
# 不是曾经有过又删了，是从来没有过一次提交。
#
# 🔴 为什么这不是「纯文档问题」：
# 读者（包括三个月后的作者）据此认为这条风险已经有人管了，于是不再去建。
# **一个不存在的守卫，比公开承认没有守卫更危险** —— 后者至少不会让人放心。
#
# 上这条检查时，除了评审报告点名的 2 个，又扫出 4 个，其中两个同样是
# 「由 `tests/xxx.py` 钉住」这种句式，而那两个测试文件都不存在。
# ⇒ 评审找到的是**样本**，不是全集。这正是「手工维护的名单」形状的另一面：
#    人去找的时候，也只找得到想到过的那几个。

#: 未建的东西照样可以在设计文档里讨论 —— 但必须在同一行标出来。
_UNBUILT_MARKS = ("未建", "设计中", "从未存在", "从未提交")

_PATH_IN_DOC = re.compile(
    r"`([a-zA-Z_][\w./-]*/[\w./-]+\.(?:py|sh|md|ya?ml|json|db|sqlite))`")

#: 🔴 光查反引号路径**等于没查** —— 探针当场证明了这一点：
#: F7 报的那两个幽灵就写在目录树里，是**裸文件名**，一个反引号都没有。
#: 差一点就上线一条「通过是因为它什么都没查」的守卫，正是本项目最常栽的形状。
#: ⇒ 第二条路按 basename 比对 `git ls-files`：树形图里重建完整路径不现实，
#:    而「仓库里根本没有叫这个名字的文件」已经足够定性。
_BARE_FILE_IN_DOC = re.compile(r"\b([\w-]+\.(?:py|sh))\b")


@pytest.mark.parametrize("path", sorted((DOCS / "design").glob("*.md")))
def test_设计文档点名的文件必须真实存在(path: pathlib.Path):
    known = {p.name for p in repo_files(suffix="")}
    ghosts = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if any(w in line for w in _UNBUILT_MARKS):
            continue
        ghosts += [(lineno, rel) for rel in _PATH_IN_DOC.findall(line)
                   if not (REPO / rel).exists()]
        ghosts += [(lineno, n) for n in sorted(set(_BARE_FILE_IN_DOC.findall(line)))
                   if n not in known]
    assert not ghosts, (
        f"{path.name} 点名了不存在的文件：{ghosts}\n"
        f"  要么建出来，要么在同一行标注 {_UNBUILT_MARKS[0]} —— \n"
        "  设计文档用陈述句提到一个文件，读者会认为这条风险已经有人管了。"
    )
