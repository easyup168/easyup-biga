"""Data Platform 名册的常驻守卫（Phase 3 · P3-0）。

测试名沿用外部设计包 `reference/tests/test_phase3_acceptance.py` 的红灯清单
（`test_dataset_registry_is_single_source` 等），判据换成本仓库的真实情况。
参考骨架里其余几条（EOD 幂等 / 修订不覆盖 / replay 不联网 / raw 篡改）
要等 P3-1 之后才有被测对象，到那时再加。

探针编号（每条都用「把它弄坏，确认会红」验证过）：

  P1  dataset_id 唯一，且 dict 的 key 与定义里的 id 一致
  P2  provider_id 唯一，且 key 与定义一致
  P3  每个 dataset 引用的 provider 都已注册（含 fallback / validation）
  P4  未知 id **fail closed** —— 抛异常且错误消息指路，不返回 None
  P5  🔴 每个 provider 的 `source_prefix` 在源码里真的作为 `"<prefix>:` 出现过
  P6  每个 provider 的 `modules` 真的 import 得到
  P7  每个 dataset 点名的 raw_table / fact_table 在**真实建出来的库**里存在
  P8  退出码映射覆盖全部状态，且与 `tools/verify/_verdict.py` 的三态不矛盾
  P9  `datasets_of()` 的派生与 DATASET_REGISTRY 双向对得上（+ 三角色覆盖用合成定义测）
  P10 `bin/biga-data` 真的跑得起来 —— 名册有**行为上**的消费方，不是零消费方
  P11 🔴 没有零消费方的已激活 dataset（外部设计 §16 的 `no zero-consumer active dataset`）
  P12 每个 dataset 的 `quality_policy` 都在 `data/quality.py` 里注册过
  P13 `provider_for_source()` 按「本 dataset 登记了谁」求交集，不是取前缀

🔴 P5 的判据为什么是「注册表 → 源码」单向
-----------------------------------------
反向（扫源码里的 `"<x>:` 再和注册表对齐）既漏又噪：某些前缀根本不在对应的
provider 模块里（在各 skill 里拼），而 `providers/` 下另有 `"https:` / `"m:` /
`"fbt:` 这类噪音前缀 —— 正是 L-13「守卫查的地方和它声称守的地方不是同一处」。
⇒ 只做单向：每个**注册了的** `source_prefix` 必须在源码里作为 `"<prefix>:`
   出现过。它精确命中要防的那件事：同一个数据源冒出第三套名字。

⚠️ 钉的是 `source_prefix` 不是 `provider_id`。`provider_id` 是**适配器级**的
（与 `providers/` 下的模块同名），而库里的 `source` 前缀是**站点级**的 ——
`sina` 与 `sina_calendar` 都写 `sina:`。两者不是一对一，拿 `provider_id`
去源码里找 `"sina_calendar:` 会误报。
"""

from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "verify"))

import _verdict  # noqa: E402

from easyup_biga.data import (  # noqa: E402
    DATASET_REGISTRY,
    DatasetDefinition,
    DATASETS,
    PROVIDER_REGISTRY,
    PROVIDERS,
    DataRunStatus,
    datasets_of,
    exit_code_for,
    get_dataset,
    get_provider,
    uses_provider,
)
from easyup_biga.data.provider_registry import (  # noqa: E402
    ProviderNotRegistered,
    provider_for_source,
)
from easyup_biga.data.quality import QUALITY_POLICIES  # noqa: E402
from easyup_biga.persistence.db import connect, init_schema  # noqa: E402


def _py_sources() -> list[pathlib.Path]:
    """src/ 与 skills/ 下的全部 .py。

    用 glob 而不是 `git ls-files`：hermetic 测试必须在**没有 .git** 的
    source ZIP 里也能跑（测试分层的第一条）。
    """
    return [p for d in ("src", "skills")
            for p in (REPO / d).rglob("*.py")
            if "__pycache__" not in p.parts]


# ── P1 / P2：名册是单一源 ────────────────────────────────────────────────────
def test_dataset_registry_is_single_source():
    """P1：dataset_id 唯一，且 key 与定义里的 id 一致。

    🔴 判据打在**手写的 `DATASETS` 元组**上，不是 `DATASET_REGISTRY.values()`。
    第一版打在 dict 上，探针弄坏了也不红 —— 因为 dict 推导已经把重复的 id
    静默吃掉了，values() 里永远不可能有重复。**那是 L-13 的一个新鲜样本：
    守卫确实在查唯一性，只是查的是一个结构上不可能重复的地方。**
    元组与 dict 的长度差，才是重复留下的唯一痕迹。
    """
    ids = [d.dataset_id for d in DATASETS]
    assert len(ids) == len(set(ids)), f"dataset_id 有重复：{ids}"
    assert len(DATASETS) == len(DATASET_REGISTRY), (
        f"手写 {len(DATASETS)} 条，按 id 索引后只剩 {len(DATASET_REGISTRY)} 条 "
        f"—— 有重复 id 被静默吃掉了")
    for key, d in DATASET_REGISTRY.items():
        assert key == d.dataset_id, f"key {key!r} 与定义里的 {d.dataset_id!r} 不一致"


def test_provider_registry_is_single_source():
    """P2：provider_id 唯一，且 key 与定义一致。判据同 P1 打在手写元组上。"""
    ids = [p.provider_id for p in PROVIDERS]
    assert len(ids) == len(set(ids)), f"provider_id 有重复：{ids}"
    assert len(PROVIDERS) == len(PROVIDER_REGISTRY), (
        f"手写 {len(PROVIDERS)} 条，按 id 索引后只剩 {len(PROVIDER_REGISTRY)} 条")
    for key, p in PROVIDER_REGISTRY.items():
        assert key == p.provider_id, f"key {key!r} 与定义里的 {p.provider_id!r} 不一致"


# ── P3：引用完整性 ───────────────────────────────────────────────────────────
def test_每个dataset引用的provider都已注册():
    """P3：primary / fallback / validation 三种角色一个都不漏。

    🔴 只查 primary 会漏掉最可能出错的那一类 —— fallback 与 validation 是
    「平时不走的路」，拼错了要等真的降级那天才发现，而那天恰恰是最不想再
    出一个错的时候。
    """
    missing = []
    for d in DATASET_REGISTRY.values():
        for role, pid in (
            [("primary", d.primary_provider)]
            + [("fallback", p) for p in d.fallback_providers]
            + [("validation", p) for p in d.validation_providers]
        ):
            if pid not in PROVIDER_REGISTRY:
                missing.append((d.dataset_id, role, pid))
    assert not missing, f"引用了未注册的 provider：{missing}"


# ── P4：未知 id fail closed ─────────────────────────────────────────────────
@pytest.mark.parametrize("fn,bad", [(get_dataset, "cn.nope"), (get_provider, "nope")])
def test_未知id是fail_closed而不是返回None(fn, bad):
    """P4：抛异常，且错误消息里带上已注册的清单（报错要指路）。"""
    with pytest.raises(KeyError) as e:
        fn(bad)
    assert "已注册" in str(e.value), "错误消息没指路 —— 读的人还得自己去翻源码"


# ── P5：provider_id 与既有 source 前缀同口径 ────────────────────────────────
def test_provider的source前缀在源码里真的用着():
    """P5：见模块头。判据是注册表 → 源码的**单向**存在性。"""
    blob = "\n".join(p.read_text(encoding="utf-8") for p in _py_sources())
    absent = [(p.provider_id, p.source_prefix) for p in PROVIDER_REGISTRY.values()
              if f'"{p.source_prefix}:' not in blob]
    assert not absent, (
        f"这些 source_prefix 在源码里找不到对应的 \"<prefix>: 字面量：{absent}\n"
        f"  source_prefix 取的是 raw_market_snapshot.source 的前缀"
        f"（sina:kline/... 的 sina）。\n"
        f"  它存在的理由是别让同一个数据源在源码、库、注册表里有三套名字。"
    )


def test_没有零消费方的已激活dataset():
    """P11：外部设计 §16 的 `no zero-consumer active dataset`。

    🔴 判据是**符号真的存在**（import 模块 + hasattr），不是「字符串在不在」。
    后者会被 docstring、注释、同名的局部变量骗过去（L-13 的常见入口）。

    这条守的是本仓库最优先防范的失败模式（L-1）：注册一个没人读的数据集，
    名册看起来完整，实际在替一件没人做的事背书。
    """
    broken = []
    for d in DATASETS:
        if not d.consumers:
            broken.append((d.dataset_id, "consumers 为空 —— 答不出谁读它就还不该进册"))
            continue
        for ref in d.consumers:
            mod, _, sym = ref.partition(":")
            try:
                m = importlib.import_module(mod)
            except Exception as e:                       # noqa: BLE001
                broken.append((d.dataset_id, f"{ref} 的模块 import 不到: {e!r}"))
                continue
            if sym and not hasattr(m, sym):
                broken.append((d.dataset_id, f"{ref} 的符号 {sym} 不存在"))
    assert not broken, f"零消费方 / 消费方指不到：{broken}"


# ── P6：modules 真的存在 ────────────────────────────────────────────────────
def test_每个provider的模块都import得到():
    """P6：`modules` 是 provider_id 指向真实代码的那根线 —— 断了就只是个字符串。"""
    broken = []
    for p in PROVIDER_REGISTRY.values():
        for mod in p.modules:
            try:
                importlib.import_module(mod)
            except Exception as e:                       # noqa: BLE001
                broken.append((p.provider_id, mod, repr(e)))
    assert not broken, f"这些模块 import 不到：{broken}"


# ── P7：点名的表真的存在 ────────────────────────────────────────────────────
def test_dataset点名的表在真实schema里存在(tmp_path):
    """P7：判据是**真的把库建出来**再读 sqlite_master，不是正则扫 schema.py。

    正则扫源码属于「按字符串形状写判据」（L-13）—— CREATE TABLE 可以写在
    条件分支里、可以被后来的 migration 改名，而扫出来的名字照样在。
    """
    db = tmp_path / "p7.db"
    init_schema(db)
    with connect(db, readonly=True) as conn:
        real = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    ghosts = []
    for d in DATASET_REGISTRY.values():
        for col, name in (("raw_table", d.raw_table), ("fact_table", d.fact_table)):
            if name and name not in real:
                ghosts.append((d.dataset_id, col, name))
    assert not ghosts, (
        f"这些表在真实 schema 里不存在：{ghosts}\n  已有的表：{sorted(real)}")


# ── P8：退出码与 _verdict.py 不矛盾 ─────────────────────────────────────────
def test_退出码覆盖全部状态且与verdict三态不矛盾():
    """P8：三个数字在本仓库有两个出处（裁定 12 的代价），这条把它们钉在一起。

    钉的是**语义**不是数值相等：`_verdict` 的 `1 = 去看代码` / `2 = 去看数据`，
    数据任务的状态必须落在同一个含义上。
    """
    for s in DataRunStatus:                      # 一个状态都不许漏（fail closed 的反面）
        exit_code_for(s)

    assert exit_code_for(DataRunStatus.COMPLETED) == _verdict.PASS
    assert exit_code_for(DataRunStatus.SKIPPED_UP_TO_DATE) == _verdict.PASS
    # 「查了真的不对，去看代码」
    assert exit_code_for(DataRunStatus.FAILED) == _verdict.FAIL
    # 「没查成，去看数据与时机」—— TIMEOUT 在这一类（裁定 12 改掉了参考骨架的 1）
    for s in (DataRunStatus.PARTIAL, DataRunStatus.QUARANTINED, DataRunStatus.TIMEOUT):
        assert exit_code_for(s) == _verdict.UNKNOWN, f"{s} 应落在 UNKNOWN(2)"
    # 人按的 Ctrl-C 不参与三态
    assert exit_code_for(DataRunStatus.CANCELLED) not in (
        _verdict.PASS, _verdict.FAIL, _verdict.UNKNOWN)

    with pytest.raises(ValueError, match="未知的数据任务状态"):
        exit_code_for("NOT_A_STATUS")


# ── P9：派生关系双向对得上 ──────────────────────────────────────────────────
def test_datasets_of的派生与注册表双向一致():
    """P9：`supported_datasets` 改成派生之后，这条验的是派生没写反。

    ⚠️ 它**不是**在追一对孪生清单（那正是派生要消灭的）—— 手写只有一处。
    这条验的是 `datasets_of` 的三种角色一个都没漏。
    """
    for pid in PROVIDER_REGISTRY:
        derived = set(datasets_of(pid))
        expected = {
            d.dataset_id for d in DATASET_REGISTRY.values()
            if pid == d.primary_provider
            or pid in d.fallback_providers
            or pid in d.validation_providers
        }
        assert derived == expected, f"{pid} 的派生对不上：{derived} vs {expected}"

    # 反向：每个 dataset 的每个 provider 都能反查到自己
    for d in DATASET_REGISTRY.values():
        for pid in (d.primary_provider, *d.fallback_providers, *d.validation_providers):
            assert d.dataset_id in datasets_of(pid), \
                f"{d.dataset_id} 用了 {pid}，但 datasets_of({pid}) 里没有它"


def test_三种角色都算用到_用合成定义测():
    """P9b：**不依赖当前注册了什么。**

    🔴 探针当场证明了这条的必要性：P3-0 收窄到两个 dataset 后
    `validation_providers` 全是空的，于是把 `uses_provider` 里那一整条
    validation 判断删掉，上面那条 P9 照样全绿 —— 守卫没写错，是数据不再
    走到那条分支。「当前数据恰好测不到」和「守卫漏了」在结果上一模一样。
    """
    def ds(**kw):
        base = dict(dataset_id="x", title="t", schema_version=1, primary_provider="P",
                    fallback_providers=(), validation_providers=(),
                    partition_keys=("as_of",), storage_policy="s",
                    quality_policy="cn-trading-calendar-v1",
                    raw_table="raw_market_snapshot", consumers=("m:s",))
        return DatasetDefinition(**{**base, **kw})

    assert uses_provider(ds(), "P"), "primary 角色没认出来"
    assert uses_provider(ds(fallback_providers=("F",)), "F"), "fallback 角色没认出来"
    assert uses_provider(ds(validation_providers=("V",)), "V"), "validation 角色没认出来"
    assert not uses_provider(ds(), "没用到的"), "不相干的 provider 被算成用到了"


# ── P10：名册有行为上的消费方 ───────────────────────────────────────────────
@pytest.mark.parametrize("sub", ["list", "providers"])
def test_biga_data_跑得起来(sub):
    """P10：判据是**退出码与可解析的输出**，不是「bin/biga-data 这个文件在不在」。

    🔴 这条是 L-1（零消费方）的判据。注册表最容易的死法是：文档把它称作
    权威，而没有任何东西真的读它。文件存在性证明不了这一点 —— 能跑、且
    吐出注册表里的内容，才能。
    """
    r = subprocess.run([str(REPO / "bin" / "biga-data"), sub, "--json"],
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode == 0, f"退出码 {r.returncode}\nstderr: {r.stderr}"
    rows = json.loads(r.stdout)
    assert len(rows) == len(DATASET_REGISTRY if sub == "list" else PROVIDER_REGISTRY)


def test_biga_data_用法错误退非零():
    """P10b：用法错误走 stderr + 非零码（契约 §16：业务状态与程序异常分开）。"""
    r = subprocess.run([str(REPO / "bin" / "biga-data"), "bogus"],
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode != 0
    assert "未知子命令" in r.stderr


# ── P12：quality_policy 不许指向空气 ────────────────────────────────────────
def test_每个dataset的quality_policy都已注册():
    """P12：外部实现里这个字段指向一组**不存在**的策略 id。

    🔴 「点名一个不存在的东西，读者会认为它已经有人管了」—— 仓库为文档立过
    这条守卫（`test_设计文档点名的文件必须真实存在`），代码里同样成立：
    一个写着 `quality_policy="cn-index-daily-v1"` 的注册表，读起来像是
    「这个数据集有质量策略」，而实际上没有任何地方能回答它检查了什么。
    """
    ghosts = [(d.dataset_id, d.quality_policy) for d in DATASETS
              if d.quality_policy not in QUALITY_POLICIES]
    assert not ghosts, (
        f"这些 quality_policy 没在 data/quality.py 注册：{ghosts}\n"
        f"  已注册：{list(QUALITY_POLICIES)}\n"
        f"  注册一条策略 = 写清「检查什么 / 不检查什么」。")


def test_质量策略必须说清不检查什么():
    """P12b：`not_checked` 不许为空。

    🔴 只写「检查什么」，读者会默认剩下的都查了 —— 而「以为查过」比
    「知道没查」危险得多（R-3 的同一条道理）。
    """
    silent = [p.policy_id for p in QUALITY_POLICIES.values() if not p.not_checked]
    assert not silent, f"这些策略没写「不检查什么」：{silent}"


# ── P13：provider 解析不靠取前缀 ───────────────────────────────────────────
def test_provider_for_source按登记求交集而不是取前缀():
    """P13：`sina` 与 `sina_calendar` 的 `source_prefix` 都是 `sina`。

    🔴 外部实现直接 `source.split(":")[0]` 当 provider_id —— 对指数日线
    恰好相等，对交易日历就错（会解析成 `sina` 而真实适配器是 `sina_calendar`）。
    判据必须带上「**这个 dataset 登记了谁**」这一半。
    """
    assert provider_for_source("cn.index.daily_bars", "sina:kline/sh000001") == "sina"
    # 同一个前缀，不同 dataset ⇒ 不同适配器。取前缀的实现在这里会给出 "sina"。
    assert provider_for_source("cn.trading_calendar", "sina:calendar/klc_td_sh") == "sina_calendar"

    with pytest.raises(ProviderNotRegistered, match="必须恰好一个"):
        provider_for_source("cn.index.daily_bars", "em:push2ex/limit_up")


# ── provider 绑定的顺序契约（2026-09-26）──────────────────────────────────
def test_provider绑定的顺序是确定的():
    """🔴 降级链的顺序**只在这里**被保证 —— `provider_chain()` 只做过滤。

    契约：PRIMARY → 按 id 排序的 FALLBACK → 按 id 排序的 VALIDATOR。
    用合成定义测三个角色都有的情形（真实注册表里今天没有这种 dataset，
    而「今天碰巧没有」不该让这条契约无人看守）。
    """
    from easyup_biga.data.contracts import DatasetDefinition, ProviderRole
    from easyup_biga.data import provider_registry as pr

    synthetic = DatasetDefinition(
        dataset_id="cn.test.ordering", title="合成", schema_version=1,
        primary_provider="p_primary",
        # 🔴 **三个**不是两个：两个元素时 `sorted` 与 `reversed` 对
        #    ("z","a") 给出同一个结果 ⇒ 把排序换成逆序的破坏会变成恒等操作，
        #    探针照样绿。夹具本身要让「顺序错了」表现得出来。
        fallback_providers=("m_fb", "z_fb", "a_fb"),
        validation_providers=("z_val", "a_val"),
        partition_keys=("d",), storage_policy="s", quality_policy="q",
        raw_table="raw_artifacts", consumers=("easyup_biga.data.registry:get_dataset",),
    )
    original = pr.DATASET_REGISTRY
    pr.DATASET_REGISTRY = dict(original, **{synthetic.dataset_id: synthetic})
    try:
        got = pr.bindings_for_dataset("cn.test.ordering")
    finally:
        pr.DATASET_REGISTRY = original

    assert [(b.provider_id, b.role) for b in got] == [
        ("p_primary", ProviderRole.PRIMARY),
        ("a_fb", ProviderRole.FALLBACK),
        ("m_fb", ProviderRole.FALLBACK),
        ("z_fb", ProviderRole.FALLBACK),
        ("a_val", ProviderRole.VALIDATOR),
        ("z_val", ProviderRole.VALIDATOR),
    ]
