# 第 49 章 · 给证据一个身份（裁定 16 · 批 1）

> 📄 **过程** · 写完即冻结
> **覆盖**：`Evidence` 的内容寻址身份、`kind` 三分类、`input_evidence_ids` 字段，
> 以及「只加字段、不强制」这个批次边界本身为什么值得单独走一批 ｜
> **不覆盖**：各 skill 改成显式声明 `kind` 与输入（批 2）、risk 跨 verdict 的引用
> 接线（批 2）—— 粒度规格已在 `TODO.md`「批 1 / 批 2 输入」定死，本章不重复

## 目标 / 产出

| 产出 | 内容 |
|---|---|
| `Evidence.evidence_id` | 内容寻址的身份，`init=False` —— **只由内容算出** |
| `Evidence.kind` | `observed` / `derived` / `parameter` / `None`（未声明）|
| `Evidence.input_evidence_ids` | 派生值的输入；批 1 只接住不强制 |
| `SHA256_RE` | 哈希形状的**唯一定义**，`verdict_ref` 改为引用它 |
| 探针 | `tests/test_evidence_identity.py` 37 条 + `fixtures/evidence-id-vectors.json` 两条冻结向量，7 处 sabotage |

## 为什么这么做

### 1. 先有身份，才谈得上引用

裁定 16 要求派生值声明 `input_evidence_ids`。动手前先问一句：**引用什么？**

```python
@dataclass(frozen=True)
class Evidence:
    field: str
    source: str
    value: Any
    ...          # 没有 id
```

`Evidence` 没有 id。这不是「顺手补一个字段」的事 —— 它决定了整条链能不能走通，
所以单独一批，且**不夹带任何强制**。

> 通用原则：当一个需求的前置条件**不存在**时，先把前置条件单独落地。
> 把「加身份」和「用身份强制」塞进同一批，失败时分不清是身份设计错了
> 还是强制范围定宽了。

### 2. 为什么是内容寻址，不是分配器

三个候选：

| 方案 | 问题 |
|---|---|
| 自增 id / UUID | 要分配器；同一条证据每次运行拿到不同 id ⇒ 回放对不上 |
| verdict 内序号 | risk 要引用**别的 verdict** 里的证据，序号得先限定属于哪份 verdict |
| **内容哈希** | 全局可比、无需分配、回放天然稳定 |

决定性的是第二条：risk 那 444 条派生值的输入在**上游的 verdict** 里。
序号类 id 必须配一个「哪份 verdict」才有意义，而内容哈希本身就够。

口径也与仓库既有的 `content_sha256` / `VerdictRef` 一致，不引入第二套哈希习惯。

### 3. `init=False` —— 不给「传一个不符的 id」留门

```python
evidence_id: str = dc_field(init=False, default="", compare=False, repr=False)
```

允许构造方传 id，就等于允许传一个**与内容不符**的 id。而「引用对不上」正是这整套
东西要防的事 —— 在入口处放一个能制造该问题的参数，等于自带一个反例。

连带的好处：`from_dict` 读存量行时**不取**里面那个 `evidence_id`，一律重算。
于是「库里存着一个与内容不符的 id」这种行根本读不出来，因为它读出来就变对了。

> 通用原则：能由内容算出来的东西，就别让人传。
> 参数的存在本身就是一种承诺：「你可以给一个不一样的值」。

### 4. `kind` 是为了把 L-13 关掉，不只是为了分类

批 2 的契约要判断「这条该不该有 `input_evidence_ids`」。今天唯一能判的方式是：

```python
source.startswith("derived:")     # ← 按字符串形状分类
```

这正是 L-13。而且它**落在一条新不变式的关键路径上** —— 判错了，整条守卫就守错了对象。

所以 `kind` 不是锦上添花的元数据，它是让批 2 那条规则**可以被正确表达**的前提。

⚠️ `None` 是「未声明」，不是第四类。批 1 之后全仓都是 `None` —— 这是**预期状态**，
不是漏做。批 2 把各 skill 改成显式声明后这一档才消失。

### 5. 唯一一处「现在就强制」的地方

批 1 整体是 capture 不 enforce，但有一条例外：

```python
if self.kind == "parameter" and self.input_evidence_ids:
    raise ValueError(...)
```

理由：这不是对**存量数据**的要求，而是对**显式声明**的自洽检查。
一条证据同时说「我是我们自己的设定」和「我从这几条数据算出来」，两句话互相矛盾 ——
它要么其实是 `derived`，要么那串 id 是凑的。两种都该当场说出来。

> 通用原则：分清「要求数据补齐」和「拒绝自相矛盾的声明」。
> 前者要照顾历史，后者不用 —— 没有任何历史行会**主动声明**一个矛盾。

## 执行

```bash
src/easyup_biga/domain/evidence.py
    SHA256_RE                      # 唯一定义（verdict_ref 改为引用）
    EVIDENCE_KINDS                 # observed / derived / parameter
    Evidence.kind                  # 新字段
    Evidence.input_evidence_ids    # 新字段
    Evidence.evidence_id           # init=False，__post_init__ 末尾算
    Evidence._compute_evidence_id  # 除自身外全部内容的 canonical JSON 的 sha256
    to_dict / from_dict            # 带上新字段；from_dict 不取存量 id

src/easyup_biga/domain/verdict_ref.py
    删掉自己那份 _SHA256_RE，改引用 evidence.SHA256_RE
```

**生产代码零改动通过** —— 新字段全有默认值，既有的构造点一处不用动。
（新写的**测试文件**倒是被守卫拦了两次，见「坑」。）

## 坑

### 坑 1 · 我写了一条空测试，sabotage 才发现

原先有这么一条，名字叫「递归冻结不影响 id」：

```python
assert _ev(value={"a": [1, 2]}).evidence_id == _ev(value={"a": (1, 2)}).evidence_id
```

看起来在守「批 R 的递归冻结不能改变身份」。**它恒真** —— 两个入参经 `deep_freeze`
本就归一成同一个对象，任何实现都能通过。

发现方式是 sabotage：破坏了 id 的计算之后，这条**没红**。

换成真正有意义的性质：**身份必须是公开序列化形式的函数** ——
拿到 `verdict_json` 的人不依赖我们的代码也能自己算一遍验证。

> 通用原则：一条测试「通过」不说明它在守东西。
> 判据是**破坏被守的东西之后它会不会红** ——
> 而这需要 sabotage 逐条对照，不是跑一遍全绿就完事。

这是本项目 sabotage 抓到的第一条**空测试**（此前几次抓的都是 sabotage 自己写坏了）。

### 坑 2 · 铁律 4 的守卫抓了我自己的测试文件

```
发现字典形式的第二套契约（铁律 4）：
  tests/test_evidence_identity.py:37 疑似手搓 Evidence，
  命中键 ['as_of', 'field', 'retrieved_at', 'source', 'value']
```

我的 `_ev` 帮助函数写成了「先搭一个 dict，再 `Evidence(**base)`」，
另一处直接手写了一个「历史形状」的字典。守卫**判得对**：
一个带 `field/source/value/as_of/retrieved_at` 的字典就是第二套 Evidence 的形状，
哪怕它只活在测试里。

两处的改法都让测试变得更好：

* 帮助函数改成逐参数给默认值 —— 不再有那个字典
* 「历史字典」改成**从真的序列化结果里删掉批 1 新增的三个键**，
  而不是手写一个「我以为历史长这样」的字典

> 通用原则：守卫拦住你自己的时候，先假设它是对的。
> 这两处改完之后，第二条测试从「我以为的历史形状」变成了「真实的历史形状」——
> 守卫逼出来的版本比原版更能说明问题。

### 坑 3 · 我又用 `tail` 截掉了失败清单

全量跑完我看了 `tail -4`，数出**两条**失败，于是去查第二条。
实际是**三条** —— 最上面那条（铁律 4）被截掉了。

这正是我在第 47 章「坑 2」里写下的同一个错误，**隔了一章又犯**。
那条要点当时写的是「sabotage 的产出必须含跑了多少条与红了哪几条」，
范围写窄了 —— 它对**任何**测试输出都成立。

> 通用原则：看测试结果要看**完整的失败清单**（`grep '^FAILED'`），不是末尾几行。
> `tail` 截掉的永远是最先失败的那条，而那条往往是根因。

### 坑 4 · 探针用错加载器，把 42 条正常数据看成读不出来

拿生产库全表做「旧卡可读」验证时，42 条报 `KeyError('status')`。
第一反应是「新字段破坏了历史数据读取」。

实际是：那 42 条是 `kind='assessment'` 的行，形状本来就与 `AgentVerdict` 不同，
该走 `_store.load_verdict`（它认两种形状）。**是探针写错了，不是回归。**

> 通用原则：在断定「我改坏了」之前，先确认对照组本来是什么样。

### 坑 5 · 公开仓库审查把冻结向量当成了 Gateway token

```
⚠️  Gateway token —— 2 处
══ 有命中，不要 push ══
```

命中的是我写在源码里的两条冻结向量 —— 裸的 64 位十六进制字面量。
审查**判得对**：它分不出「内容哈希」和「网关令牌」，豁免只认
`sha256:` / `raw_hash:` 这种键名，而我写的是 `evidence_id == "<hex>"`。

仓库里已经有同类先例：`payload_sha256` 的历史向量放在
`tests/fixtures/payload-sha256-vectors.json`，键名就叫 `sha256`。
照着搬即可 —— 而且这样更好：向量是**数据**不是代码，加一条不该改测试逻辑。

> 通用原则：遇到守卫拦路，先找仓库里**同类东西是怎么过的**，
> 再考虑改守卫。这次「照先例搬」顺带让测试变干净了；
> 若当时选择去给审查加一条 `evidence_id` 豁免，就是在放宽一道安全检查
> 来迁就一处写法。

### 坑 6 · 1246 组「id 重复」，查完发现是对的

生产库 3152 条证据算出 1905 个不同 id —— 1247 组重复。

逐层查下去：不是跨决策（0 组），不是同 verdict 内（0 条），
而是**同一 `(task_id, agent)` 的不同 verdict 行** —— 来自**修订**
（`amends` 非空，第二行重述了同样的证据）。

内容相同 ⇒ id 相同，这正是内容寻址的定义，不是缺陷。

但它带出一条批 2 必须知道的事：**`evidence_id` 标识的是「哪份内容」，
不是「哪一行」。** 跨 verdict 引用要定位到行，得再带上 verdict。
已写成一条带解释的测试，免得批 2 的人重新查一遍。

## 验证

```bash
cd ~/.openclaw-biga/workspace

python3 -m pytest tests/test_evidence_identity.py -q     # 期望 37 passed

# 身份的四条核心性质
python3 - <<'PY'
import sys; sys.path.insert(0, "skills")
from _contract import Evidence, now_cn
from datetime import timedelta
t = now_cn() - timedelta(seconds=60)
mk = lambda v: Evidence(field="f", source="probe:x", value=v, as_of=t, retrieved_at=t)
a, b, c = mk(1), mk(1), mk(2)
assert a.evidence_id == b.evidence_id, "同内容必须同 id"
assert a.evidence_id != c.evidence_id, "异内容必须异 id"
assert Evidence.from_dict(a.to_dict()).evidence_id == a.evidence_id, "往返必须稳定"
assert "evidence_id" not in Evidence.__init__.__code__.co_varnames, "不许构造方传 id"
print("✅ 四条性质都成立")
PY

# 旧行可读（只读生产库）
python3 - <<'PY'
import sys, sqlite3, json; sys.path.insert(0, "skills")
from _store import load_verdict
from _contract import Evidence
c = sqlite3.connect("file:data/biga.db?mode=ro", uri=True)
n = sum(1 for (vj,) in c.execute("SELECT verdict_json FROM agent_verdicts")
        for e in json.loads(vj).get("evidence", [])
        if Evidence.from_dict(e).kind is None)
print(f"✅ {n} 条历史证据读回正常，kind 均为 None（未声明 —— 批 1 的预期状态）")
PY
```

## 本章要点

| # | 要点 |
|---|---|
| 1 | 需求的前置条件不存在时，先把前置条件单独落地 —— 别和「用它来强制」塞进同一批 |
| 2 | 内容寻址胜出的决定性理由：risk 要引用**别的 verdict** 里的证据，序号类 id 得先限定 verdict |
| 3 | 能由内容算出来的东西就别让人传 —— 参数的存在本身就是「你可以给个不一样的值」的承诺 |
| 4 | `from_dict` 不取存量 id 一律重算 ⇒ 「id 与内容不符」的行读出来就变对了 |
| 5 | `kind` 的真正用途是**关掉 L-13**：批 2 的规则否则只能靠 `source.startswith("derived:")` 判 |
| 6 | `None` 是「未声明」不是第四类；批 1 之后全仓都是 `None`，这是预期状态不是漏做 |
| 7 | 分清「要求数据补齐」（要照顾历史）与「拒绝自相矛盾的声明」（不用）—— 后者现在就能强制 |
| 8 | 🔴 **sabotage 抓到一条空测试**：两个入参经 `deep_freeze` 归一成同一对象，断言恒真 |
| 9 | 测试「通过」不说明它在守东西；判据是**破坏被守的东西之后它会不会红** |
| 10 | 守卫拦住你自己时先假设它是对的 —— 被逼出来的那版测试比原版更能说明问题 |
| 10b | 🔴 **看完整失败清单**（`grep '^FAILED'`），不是 `tail` 末尾几行 —— 截掉的永远是最先失败、也最可能是根因的那条（第 47 章刚写过，隔一章又犯）|
| 10c | 断定「我改坏了」之前先确认对照组本来什么样 —— 42 条「读不出来」是探针用错了加载器 |
| 11 | 1246 组 id 重复来自**修订行重述同样的证据**，内容相同则 id 相同是定义，不是缺陷 |
| 12 | ⚠️ 批 2 必读：`evidence_id` 标识「哪份内容」不是「哪一行」，定位到行要再带 verdict |
| 13 | 冻结向量钉住哈希算法 —— 改了算法，存量 `input_evidence_ids` 的引用会**静默失效** |
| 14 | 冻结向量放 `fixtures/*.json`（键名 `sha256`）而不是源码字面量 —— 裸 64 位十六进制会被公开仓库审查判成疑似令牌 |
| 15 | 遇到守卫拦路先找**同类东西怎么过的**，再考虑改守卫 —— 给审查加豁免是放宽安全检查来迁就写法 |
