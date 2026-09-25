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

import functools
import pathlib
import re
import subprocess
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


#: git 真实吐出来的三行标记：`<<<<<<<`/`>>>>>>>` 后面永远跟一个空格加标签
#: （分支名/commit），从不裸出现；`=======` 相反，永远是整行只有 7 个 `=`，
#: 不跟任何东西。判据必须精确到这个形状——本仓库的 RST 风格表格分隔线
#: （见 `architecture.md`）也用连续 `=`，但长度远超 7 且中间夹着空格分段，
#: 用「行首是不是 7 个 `=`」这种宽松判据会把它们也当成冲突标记（实测踩过）。
_CONFLICT_MARKER_RE = re.compile(r"^(<{7}( |$)|={7}$|>{7}( |$))")


@pytest.mark.parametrize("path", _md_files(), ids=lambda p: str(p.relative_to(DOCS)))
def test_没有未清理的冲突标记(path: pathlib.Path):
    """真实事故（2026-09-24）：一次 rebase 冲突的两边内容恰好相同（都是
    「测试 1532 条」），标记行却没删——CLAUDE.md / README.md / CHANGELOG.md /
    docs/guide/review-prompt.md 就这样带着字面的冲突标记提交进了主分支。

    🔴 这类损坏**没有任何自动防线**：Markdown 不是会被结构化解析的格式，
    `pytest -q` 与 `audit_public.sh` 那十一项都对它视而不见——两边都真的
    跑绿过，损坏的提交混进去了两轮才被人工发现。原因分别是：
    冲突标记不是密钥/路径/邻居细节（`audit_public.sh` 不管这类），
    也不影响任何被测断言的真假（docs_convention 的既有检查都是子串/正则
    匹配，标记行不巧不落在任何一条正则的判据里）。
    """
    text = path.read_text(encoding="utf-8")
    hits = [ln for ln in text.splitlines() if _CONFLICT_MARKER_RE.match(ln)]
    assert not hits, (
        f"{path.name} 里有未清理的冲突标记：{hits}\n"
        "  多半是 rebase/merge 冲突两边内容刚好相同，手工清理时删错了行——\n"
        "  留了标记、删了内容之外的东西看不出问题，直到有人打开这份文档。")


@pytest.mark.parametrize("path", _md_files(), ids=lambda p: str(p.relative_to(DOCS)))
def test_文件名符合所在目录的规则(path: pathlib.Path):
    if _is_untracked_local_only(path):
        pytest.skip("落在本地专属目录且未跟踪 —— 按策略不会提交，命名约定不适用")
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
    if _is_untracked_local_only(path):
        pytest.skip("落在本地专属目录且未跟踪 —— 按策略不会提交，类别头约定不适用")
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
    """它是别人在某个时刻的想法的快照，日期是它的一部分。

    未跟踪的（`.gitignore` 挡住、按策略不会提交）跳过 —— 理由见
    `_is_untracked_local_only()`。
    """
    for p in (DOCS / "external").glob("*.md"):
        if _is_untracked_local_only(p):
            continue
        assert re.match(r"^\d{4}-\d{2}-\d{2}-", p.name), \
            f"{p.name} 缺日期前缀"
        assert "只读" in _head(p), f"{p.name} 必须标注只读"


def test_没有歧义的目录名():
    """曾经同时存在 docs/reference/ 与 docs/design/reference/。

    本地专属目录（`_LOCAL_ONLY_DIRS`）下未跟踪的子目录不算数 —— 与
    `_is_untracked_local_only()` 同一条理由，只是判据是目录版
    （`_is_untracked_local_only_dir()`）。

    🔴 2026-09-24 实测：解包在 `docs/external/` 下的外部材料自带
    `docs/`、`docs/design/` 这类子目录，于是这条检查在本地**常红**，而在干净
    checkout 上是绿的 —— 守卫只该描述**被提交的那棵树**（裁定 14），
    否则它报的红与任何人 clone 下来看到的都对不上，最后只会被当噪音忽略。
    这与 `cee4045` 给条数检查做的修正是同一件事，那次漏了这条。
    """
    names = [d.name for d in DOCS.rglob("*")
             if d.is_dir() and not _is_untracked_local_only_dir(d)]
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
# 所以这类内容会被顺手写进来，而当时的八项审查一项都抓不到。
#
# 边界：**隔离本身要写**（独立目录 / 独立端口 / 崩溃不波及是通用工程问题），
#       **那套系统是什么、怎么配的、我怎么读了它 —— 不写。**

#: 拟人化或指向特定既存系统的措辞。中性说法用「同机已有实例」。
#:
#: 🔴 外部评审 F13：这是一份**字面量黑名单，同义改写即可通过**。
#:    评审在教程末尾插了一句完整复刻禁止内容的话（用「另一套系统」代替
#:    「邻居」、「现有生产系统」代替「生产环境」），18 个文件全绿。
#:
#: ⚠️ 这条**没有彻底的解法** —— 目标是开放式自然语言，
#:    不像开关名那样有稳定锚点可以切换过去（那是 `missing_ledger.py`
#:    那次能做到的原因）。所以这里如实说明它能做到什么：
#:
#:    - 能挡住：顺手写出常见措辞（绝大多数真实情况）
#:    - 挡不住：刻意的同义改写
#:    - 真正的兜底是下面那条**路径与端口**检查 —— 那个有稳定锚点
#:
#: 「另一套系统」「现有生产系统」这类说法已按评审实测补进来 ——
#: 它们恰恰是 `CLAUDE.md` 通篇在用的词，最容易被顺手带进教程。
_TUTORIAL_FORBIDDEN = (
    "邻居", "既存", "实盘", "量化交易", "交易系统", "下单通道",
    "长期运行的系统", "生产环境", "另一套系统", "另一套实例",
    "现有生产", "那套系统", "对方系统", "隔壁",
)

#: 🔴 与措辞不同，**这些是可判定的**：同机另一实例的具体路径与端口。
#:    教程里出现它们，等于把本机情况写进了一份通用教程。
#:    ⚠️ 写成拼接，避免本文件自己成为「描述规则时抄进真值」的第六次。
_NEIGHBOUR_ANCHORS = ("~/." + "openclaw/", "$HOME/." + "openclaw/",
                      "1" + "8789", "1" + "8791")


@pytest.mark.parametrize("path", sorted((DOCS / "tutorial").glob("*.md")))
def test_教程不出现同机另一实例的路径或端口(path: pathlib.Path):
    """措辞挡不住同义改写，但**路径和端口是确定的字符串**。

    这条是 F13 的真正兜底：即使有人用「另一套系统」这种没被列进
    黑名单的说法，只要他要说清楚「怎么和它共存」，就绕不开写出
    具体路径或端口 —— 而那才是真正不该进教程的东西。
    """
    text = path.read_text(encoding="utf-8")
    hits = [a for a in _NEIGHBOUR_ANCHORS if a in text]
    assert not hits, (
        f"{path.name} 出现同机另一实例的具体路径/端口 {hits}\n"
        "  隔离这件事本身要写（独立目录 / 独立端口 / 崩溃不波及是通用工程问题），\n"
        "  但**具体是哪个目录、哪个端口**属于本机情况，不进教程。")


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
    # 🔴 README 徽章。原来写的是 `(\d+)%20TESTS` —— 而真实徽章是
    #    `badge/tests-1817%20collected`（小写、后缀不是 TESTS）。也就是说这条
    #    正则**从来没有匹配上过**：徽章既不被同步脚本改、也不被本守卫查，
    #    于是它停在 1817 而正文已经是 1843，外部评审因此数出「至少三套数字」。
    #    ⚠️ 这是 L-13 的形状：按**字符串形状**写判据，形状一变就静默失效 ——
    #      而失效的表现是「一直通过」，没有任何地方会报红。
    r"badge/tests-(\d+)%20",     # README 的徽章
    r"(\d+)\s*条测试",            # 正文散文
    r"测试\s*\|\s*(\d+)\s*条",     # CLAUDE.md 的状态表
)


#: 行内豁免标记。历史快照（某次验收当时是多少条）本来就不该跟着涨。
#: 🔴 默认必须是最新的，**例外要自己举手** —— 和只追加触发器那条同一个道理：
#:    手工维护「哪些要查」的名单，漏掉的永远是没想到的那个。
_FROZEN_MARK = "冻结"


@functools.lru_cache(maxsize=1)
def _tracked_docs() -> set[pathlib.Path] | None:
    """`docs/` 下**被 git 跟踪**的 `.md`。拿不到返回 `None`。

    🔴 为什么条数要按「被跟踪」算，而不是按「磁盘上有什么」算
    ------------------------------------------------------------
    上面那两条检查是**按文件参数化**的：`docs/` 下每多一个 `.md`，就多两条
    test case。于是工作区里任何**未跟踪**的 `.md` —— 哪怕被 `.gitignore` 挡着、
    永远不会进仓库 —— 都会把 collected 抬高。

    实测踩过（2026-09-23）：主工作区当时躺着一批未提交的材料，同步出来的条数
    比干净 checkout 高 16 条，那个数被提交了，而**任何人 clone 下来都跑不出它**。
    裁定 14：徽章与验收数字必须始终描述一个真被测过的**提交**。

    ⚠️ 解法不是「参数化时别扫未跟踪文件」——那会让「新写的文档没带类别头」
    在提交前查不出来，等于关掉一道有用的守卫。未跟踪文件**照样受检**，
    只是不计入「这个提交有多少条测试」。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "--", "docs/"],
            capture_output=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {REPO / rel for rel in out.decode("utf-8").split("\0")
            if rel.endswith(".md")}


#: 整体不进 git 的目录（`.gitignore` 挡住，运营者决策——见 `.gitignore` 里对应
#: 注释）。新增一个同性质目录时只改这里，别在调用点各写一份判据。
_LOCAL_ONLY_DIRS = (DOCS / "external", DOCS / "troubleshooting")


def _is_untracked_local_only(path: pathlib.Path) -> bool:
    """`path` 是不是「落在某个整体不进 git 的目录下、且未被 git 跟踪」的文档。

    🔴 与 `_is_untracked_doc_case` 是两条不同的判据，别合并
    -----------------------------------------------------------
    那一个只管"这个提交里数不数它"——未跟踪文档**照样受检**，只是不计入条数
    （见 `_tracked_docs()`），因为大多数未跟踪文档是"还没提交、以后会提交"的
    在途状态，提交前查出来正是它的价值。

    `_LOCAL_ONLY_DIRS` 下的文件不是这种状态：`docs/external/*` 起于 2026-09-23，
    `docs/troubleshooting/*` 起于 2026-09-24——都是运营者决策「这类材料本地留存
    参考，不打算提交」（前者是外部评审/业务规划材料，后者是从真实使用场景现场
    记录的排查笔记，含私聊往来细节）。这类文件没有"以后会提交"的那一刻，命名 /
    类别头 / 日期前缀这些"提交前该长什么样"的约定对它们不适用——不是放松
    检查，是检查的前提（"这份东西即将进入公开仓库"）本身不成立。已经提交过的
    老文件（比如 `2026-09-19-upstream-source-design-v1.md`）不受影响，继续
    跟踪、继续受检。
    """
    tracked = _tracked_docs()
    if tracked is None:
        return False            # 拿不到跟踪清单就不豁免，按老规则查（R-3 方向）
    if not any(path.is_relative_to(d) for d in _LOCAL_ONLY_DIRS):
        return False
    return path not in tracked


def _is_untracked_local_only_dir(path: pathlib.Path) -> bool:
    """目录版的 `_is_untracked_local_only`：本地专属目录下、且**不含任何被跟踪
    文件**的子目录。

    判据是"里面有没有被跟踪的东西"而不是"目录自己在不在 git 里" —— git 不跟踪
    空目录，没有"目录被跟踪"这回事。`docs/external/` 自己因此仍然算数
    （它下面有 3 份早就提交的只读材料），被豁免掉的只是解包出来的那些子目录。
    """
    tracked = _tracked_docs()
    if tracked is None:
        return False            # 拿不到跟踪清单就不豁免，按老规则查（R-3 方向）
    if not any(path.is_relative_to(d) for d in _LOCAL_ONLY_DIRS):
        return False
    return not any(t.is_relative_to(path) for t in tracked)


def _is_untracked_doc_case(item, tracked: set[pathlib.Path]) -> bool:
    """这条 test case 是不是「参数是某个未跟踪的 docs/*.md」。

    判据从 `callspec.params` 推，**不硬编码「每个文件贡献几条」** ——
    将来多一条按 `_md_files()` 参数化的检查，这里不用改
    （手写倍数正是 `test_roster_matches_config` 的清单式教训）。
    """
    spec = getattr(item, "callspec", None)
    if spec is None:
        return False
    return any(
        isinstance(v, pathlib.Path) and v.suffix == ".md"
        and DOCS in v.parents and v not in tracked
        for v in spec.params.values())


class _FakeSpec:
    def __init__(self, params): self.params = params


class _FakeItem:
    def __init__(self, params): self.callspec = _FakeSpec(params)


def test_README徽章的schema版本与代码一致():
    """schema 徽章漂了两版都没人发现（v19 vs 实际 v21）。

    条数有守卫、有同步脚本，schema 版本两样都没有 —— 而它同样是
    「文档里的一个实测数字」。裁定 14：徽章必须描述真被测过的状态。
    """
    import sys as _sys
    _sys.path.insert(0, str(REPO / "src"))
    from easyup_biga.persistence.schema import SCHEMA_VERSION

    text = (REPO / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"schema%20v(\d+)", text) + re.findall(r"schema v(\d+)", text)
    assert found, "README 里找不到 schema 版本 —— 是被删了，还是换了写法？"
    bad = [v for v in found if int(v) != SCHEMA_VERSION]
    assert not bad, (
        f"代码里 SCHEMA_VERSION={SCHEMA_VERSION}，README 写着 v{sorted(set(bad))}。\n"
        "  徽章与正文都算。")


def test_条数只数被跟踪的文档():
    """`_is_untracked_doc_case()` 的自证 —— **不依赖真的有 git**。

    没有这条，上面那个 `collected` 的口径就是「看起来对」——而 2026-09-23
    那次正是「看起来对」：同步出来的数在本机跑得通，clone 下来跑不出。

    🔴 判据喂的是一份**合成的** tracked 集合，不是 `_tracked_docs()` 的真实
    返回值。原来这里 `assert tracked`，于是在 source ZIP（没有 `.git`）里
    **必然失败** —— 而它测的是一段纯函数逻辑，和有没有 git 毫无关系。
    「换一台机器就红」的守卫会先被人关掉，然后就再也没人开回来。
    真实 git 那一侧由下面那条 `@pytest.mark.git` 负责。
    """
    known = DOCS / "README.md"
    tracked = {known}

    # 被跟踪 ⇒ 计入条数
    assert not _is_untracked_doc_case(_FakeItem({"path": known}), tracked)

    # 未跟踪 ⇒ 不计入（这正是 1116 那次的成因）
    ghost = DOCS / "external" / "_never-committed.md"
    assert ghost not in tracked
    assert _is_untracked_doc_case(_FakeItem({"path": ghost}), tracked)

    # docs/ 之外的参数不受影响（别把别的参数化检查也误伤）
    assert not _is_untracked_doc_case(_FakeItem({"rel": "CLAUDE.md"}), tracked)
    assert not _is_untracked_doc_case(_FakeItem({"path": REPO / "skills"}), tracked)


@pytest.mark.git
def test_真实git清单扫得到docs下的文档():
    """上一条用的是合成集合；这一条验 `_tracked_docs()` 真的能扫到东西。

    🔴 非平凡：必须真的扫到已知的被跟踪文档，否则「全都算未跟踪」也会绿 ——
    那样条数会静默地一路缩水到 0，而每一步都「通过」。
    """
    tracked = _tracked_docs()
    assert tracked, "拿不到 git 跟踪清单"
    assert DOCS / "README.md" in tracked, "扫到了，但没扫到已知的那份，覆盖坏了"


def test_文档里的测试条数与实测一致(request):
    # 只在**全量**跑时校验：跑子集时条数本来就对不上，那不是文档的错
    got = {item.path for item in request.session.items}
    if got != set((REPO / "tests").glob("test_*.py")):
        pytest.skip("跑的是子集，条数无从比较")

    tracked = _tracked_docs()
    if tracked is None:
        # R-3：拿不到跟踪清单就是算不出来，不当作通过（skip 会显示成 's'）。
        pytest.skip("拿不到 git 跟踪清单，条数无从比较")

    # 🔴 只数「这个提交里真的有的」那些 case —— 见 `_tracked_docs()` 的说明。
    # P1-3：conftest 的 pytest_collection_modifyitems 把 installed/live/git 标记
    # 的测试改成 skip（而不是 deselect）——这样 bare pytest 也不失败。
    # 但这些测试**仍在 session.items** 里，若计入 collected 会比 CI（-m 过滤后）
    # 多出来，导致条数对不上。在这里排除，让口径与 CI 对齐。
    _SKIP_MARKERS = frozenset({"installed", "live", "git"})
    collected = sum(1 for it in request.session.items
                    if not _is_untracked_doc_case(it, tracked)
                    and not any(m.name in _SKIP_MARKERS for m in it.own_markers))

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
    known = {p.name for p in repo_files()}
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


# ──────────────────────── 反向：建了东西却没写进设计文档
#
# 上面那条守的是「文档点名了不存在的文件」（F7）。它的**反面**同样会咬人，
# 而且更安静：**东西建出来了，设计文档只字未提。**
#
# 🔴 实测：问「Phase 2 的功能都反映到设计里了吗」，机器扫了一遍，
#    `sanity.py`、`spawn_check.py`、`server_as_of`、`StoreNotInitialised`
#    **四样在任何设计文档里都找不到** —— 而它们全是当天刚建的。
#
# 两条守卫方向相反，缺一不可：
#
#   文档 → 代码   幽灵文件：说了有，其实没有
#   代码 → 文档   孤儿组件：做了，但没人知道
#
# ⚠️ 只管**面向使用者的入口**（`tools/verify/` 下的工具、`skills/_*` 共享层），
#    不管每一个业务脚本 —— 那会逼出一堆为了过测试而写的空话。

#: 必须在 `docs/design/` 里被提到的东西。判据是「谁会去找它」：
#: 巡检工具是人手工敲的命令，共享层是写新 skill 时要照着用的。
def _entry_points() -> list[pathlib.Path]:
    out = [p for p in (REPO / "tools" / "verify").iterdir()
           if p.suffix in (".py", ".sh") and not p.name.startswith("_")]
    out += [p for p in (REPO / "skills").glob("_*/*.py")
            if p.name != "__init__.py"]
    return sorted(out)


def test_扫到了入口():
    assert len(_entry_points()) >= 12, "一个都没扫到的话下面那条等于没测"


@pytest.mark.parametrize("path", _entry_points(), ids=lambda p: p.name)
def test_每个入口都在设计文档里被提过(path: pathlib.Path):
    design = "\n".join(f.read_text(encoding="utf-8")
                       for f in sorted((DOCS / "design").glob("*.md")))
    assert path.name in design, (
        f"{path.relative_to(REPO)} 建出来了，但 `docs/design/` 里一个字都没提。\n"
        "  ⚠️ 这不是「补一行文件名」就完了 —— 要写清楚**它解决什么问题**，\n"
        "     否则三个月后没人知道能不能删它。\n"
        "  真的只是内部实现？改名加 `_` 前缀，它就不算入口了。"
    )


# ─────────────────────────── 审查项数也会漂移（F22 换了个地方）
#
# 加第 10 项时，四处文档还写着「八项」「九项」——
# 与测试条数是**同一个形状**：一个每次改动都要手工同步的数字，
# 最后一定会不同步。
#
# 🔴 判据取自脚本里 `chk "…"` 的真实调用数，不另写一个计数器。

_CN_DIGITS = "零一二三四五六七八九"


def _cn(n: int) -> str:
    """1..99 的汉字写法。

    ⚠️ 第一版是 `_CN_NUM[n]` 下标取字 —— 到 11 就越界，回退成了 `"11"`，
    于是守卫拿 `"11"` 去比文档里的「十一」，永远对不上。
    **单字符表只够用到十。**
    """
    if n < 10:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    tens, ones = divmod(n, 10)
    return ("十" if tens == 1 else _CN_DIGITS[tens] + "十") + (_CN_DIGITS[ones] if ones else "")


def _audit_check_count() -> int:
    """脚本里实际有几项检查。

    ⚠️ 不能只数 `chk "` —— 第 11 项是**范围检查**（哪些文件不该进仓库），
    不走正则那条路。第一版只数 `chk`，于是脚本自己打印「十一项全绿」、
    文档写「十项」，而守卫两边都觉得对得上。
    ⇒ 非 `chk` 的检查用 `# audit-check:` 显式标记，一起数。
    """
    src = (REPO / "tools" / "verify" / "audit_public.sh").read_text(encoding="utf-8")
    return (len(re.findall(r'^chk "', src, re.M))
            + len(re.findall(r'^# audit-check:', src, re.M)))


def test_扫到了审查项():
    assert _audit_check_count() >= 9, "一项都没数到的话下面那条等于没测"


#: 说「N 项检查」的两种语序，外加 markdown 强调符要先剥掉 ——
#: 🔴 第一版没剥，`**十项**检查` 里的 `**` 把两半隔开，于是这条守卫
#:    **一处都没匹配到**：删掉第 10 项之后它照样绿。
#:    与 F5/F4/F3/F7 那几次同一个形状，这次是在写守卫的当场被探针抓到的。
_COUNT_SAYINGS = (
    # ⚠️ `+` 不可省 —— 「十一」是**两个字**。第一版是单字符类，
    #    把「十一项」截成「一项」，于是报「文档写着一项」。
    re.compile(r"([零一二三四五六七八九十]+|\d+)项(检查|审查|全绿)"),
    re.compile(r"(检查|审查)[（(]([零一二三四五六七八九十]+|\d+)项[)）]"),
)


def _sayings(line: str) -> list[str]:
    plain = re.sub(r"[*`_]", "", line)
    out = []
    for pat in _COUNT_SAYINGS:
        for m in pat.finditer(plain):
            num = next(g for g in m.groups() if g and g not in ("检查", "审查", "全绿"))
            out.append((m.group(0), num))
    return out


#: ⚠️ `TODO.md` **故意不在这张表里** —— 它那句已经改成「这些检查」，
#:    根本不提数字。**最好的防漂移是不写那个数**，写了才需要守卫。
@pytest.mark.parametrize("rel", ["CLAUDE.md", "docs/design/architecture.md"])
def test_文档里写的审查项数与脚本一致(rel: str):
    n = _audit_check_count()
    want = _cn(n)
    text = (REPO / rel).read_text(encoding="utf-8")
    seen, bad = 0, []
    for line in text.splitlines():
        # 只看**当下的**说法；历史快照（带日期的验收记录）用「冻结」豁免
        if _FROZEN_MARK in line or "项验收" in line:
            continue
        for whole, num in _sayings(line):
            seen += 1
            if num != want:
                bad.append(f"「{whole}」")
    # 🔴 没找到 ≠ 通过 —— 这正是第一版翻车的地方
    assert seen, f"{rel} 里找不到「N 项检查」的说法 —— 是被删了，还是换了写法？"
    assert not bad, (
        f"{rel} 写着 {bad}，而脚本里实际是 {want}项（{n} 条 chk）。\n"
        f"  历史快照请在同一行标注「{_FROZEN_MARK}」豁免。"
    )
