# 69 · 让一项从没跑过的演练真的跑起来，以及两处「自己证明自己」

> 📄 **过程** · 写完即冻结
> **覆盖**：`SOURCE_ZIP_REPLAY` 第一次真跑；源码包导出/恢复工具；派生数据集的 raw 血缘
> **不覆盖**：另外三项演练（各自卡在不同的硬条件上，见 `TODO.md` 的 E-1a/E-1b/E-1c）

---

## 目标 / 产出

这一章的三件事有同一个形状：**一个东西声称能证明某件事，而它其实在证明自己。**

| | 声称证明 | 实际 |
|---|---|---|
| `SOURCE_ZIP_REPLAY` 演练 | 「这次决策能离线重放」 | 三个月**一次都没跑过** |
| 恢复树上用 `bin/biga-data` 跑回放 | 「恢复出来的树是完整的」 | 读的是**原树**的字节 |
| 派生数据集的 `content_sha256` | 「数据没被改过」 | 对**自己的输出**算的哈希 |

产出：v0.9.1 + v0.9.2。六项演练从 2/6 到 **3/6**。

---

## 为什么这么做

### 一、一项演练三个月没跑，不是因为难

`drills.py` 里 `source_zip_replay_drill()` 早就写好了。判据是完整的：
走一遍 EvidenceSet → DatasetSnapshot → DatasetPartition → RawArtifact，
每个能重算哈希的地方都重算。

它从没跑过，是因为**它前面那几步没有工具**：

> 把一次决策引用到的字节打成包 / 摊回一棵干净的树 / 站在对的工作目录上。

这几步的描述一直只存在于口头。每次想跑，都要重新想一遍「URI 到底是相对谁的」。
而这个细节答错一次，回放会报「文件不存在」—— 读起来像是数据坏了。

> 🔴 **「判据写好了」和「这项检查跑过」之间，隔着一整套操作。**
> 而那套操作没有工具时，它的成本不是「麻烦」，是「每次都要重新推导一遍」。

⇒ `tools/verify/source_bundle.py`：`export` / `restore`。

#### 三条硬规矩，每条对应一种会**通过**的错法

| 规矩 | 不这么做会怎样 |
|---|---|
| 只带这次决策引用到的字节，**不扫整棵 `data/`** | 多带的文件让回放在一棵本不该跑通的树上跑通 —— 把「我恢复对了」和「我什么都带上了」混为一谈 |
| 库用 `VACUUM INTO` 取一致副本，**不是 `cp`** | WAL 模式下 `cp` 会漏掉未 checkpoint 的事务，而**漏掉不报错**：库能打开、能查，只是少了最近几条 |
| `storage_uri` 是绝对路径就**拒绝导出** | 绝对路径搬不动。恢复的库仍指着导出那台机器上的目录，而那个目录多半还在 ⇒ 读到**原树**的字节并 PASS |

第三条值得展开：`storage_uri` 原样回显发布时传的 `data_root` ——
传相对路径就存相对，传绝对路径就存绝对。生产路径传的是 `"data"`，所以是相对的。
但测试里随手写 `data_root=str(tmp_path / "data")` 就会存成绝对路径。

> **「文件不存在」反而是好结局：至少它红了。**
> 真正危险的是那个目录还在 —— 那时回放会 PASS，而这个 PASS 与恢复无关。

### 二、恢复树上不能用 `bin/biga-data` —— 实测出来的假绿

`bin/biga-data` 这个壳启动时第一件事是 `cd "$ROOT"`（为了让默认的 `data/`
相对路径成立）。而分区 URI 是按**进程工作目录**解析的
（`FileStore.verify_file_hash` 用 `Path(uri)`，与 `--data-root` 无关 ——
`FileStore.root` 只用于**写**）。

实测：把恢复树里一个 Parquet **删掉**，

```
① 用 bin/biga-data（会 cd 回仓库根）： passed = True
② 正确调用（cwd = 恢复树）：          passed = False | file is unreadable: …
```

⇒ 演练结果里因此记下 `cwd` 与 `db_path`：**这两个值才决定读的是哪棵树。**

⚠️ **不自动判定两者算不算「同一棵树」** —— 目录布局不止一种，猜错会把合法调用
判红。记下来，让账本能回答「当初读的是哪棵树」。

### 三、顺手撞出的 CLI 缺陷：`--db` 从来就没法用

要把回放指向恢复树，唯一的办法是 `--db`。而它一直是坏的：

```console
$ bin/biga-data --db data/biga.db snapshots cn.news.flash
未知子命令 'data/biga.db'
```

根因：挑位置参数的判据是「不以 `-` 开头」，于是**带值全局标志的值**
成了第一个位置参数。

> 🔴 **它比「报错了」更糟：错误信息把人引向「子命令拼错了」，而子命令是对的。**

修法的关键不是写个跳过逻辑，而是让**带值的顶层标志只有一份表**：

```python
_GLOBAL_VALUE_OPTIONS = (("--db", None), ("--data-root", "data"))
for _flag, _default in _GLOBAL_VALUE_OPTIONS:
    parser.add_argument(_flag, default=_default)          # 注册用它
...
return parser, frozenset(names), frozenset(f for f, _ in _GLOBAL_VALUE_OPTIONS)  # 跳过也用它
```

两处分家的那天不会报错，只会把某个值当成子命令名。

### 四、派生数据集的 raw，一直在自己证明自己

这条是**核对一条「过期 TODO」**时翻出来的。条目写着：

```
- [ ] ⓪-b 可交易性进册 —— 等它真有读取方
      …那时它的 raw 必须指向同一份 provider 响应（与日线共用），
      不能再是把自己的输出重新序列化
```

它标着 `[ ]`，读起来像整件事都没开始。实际是**半完成**：
进册那一半在上一轮就做了（`analytics:query_tradability` 是真读取方），
血缘那一半没做。而条目整体还是未勾选 ⇒ **没人再看第二眼**。

> 🔴 **一个「半完成」的条目标成「未开始」，比标成「已完成」更难被发现。**
> 后者至少会被下一个读的人质疑。

#### 旧实现

```python
raw = json.dumps([item.to_dict() for item in records])
```

把**自己的输出**重新序列化当 raw。于是 `content_sha256` 是对输出算的 ——
回放去校验它，校验的是「我写下的等于我写下的」，与真正的来源
（行情源那次 EOD 响应）毫无关系。

而同一次取数里，日线走的是真响应：

> **同一次取数，两个数据集两套口径。**

`cn.market.emotion_close` 是同一个毛病，而且更明显：生产路径上喂给它的那个 dict
是 `decision_client` **自己从股池算出来的计数**（`up.total`、
`broken.total / denom`、`max(streaks)`）—— raw 存的是我们的中间结果，
不是任何人发给我们的字节。

#### 修法：引用，不复制

第一反应是「把上游的字节复制一份写到自己名下」。那样 raw 的 bytes 确实对了，
但 `provider_id` 会写成 `derived_biga` —— **读的人会以为这个 provider 返回过
这些东西**。

⇒ `SnapshotPublishRequest` 新增 `upstream_artifact_ids`，与 `raw_artifacts`
**二选一**。分区的 `raw_artifact_id` 外键直接指向上游那一条。

三条 fail-closed：

| 情况 | 行为 |
|---|---|
| 上游 id 在库里不存在 | 抛 —— 指向空气的血缘比没有血缘更糟，它会让人以为查得到 |
| 两种血缘同时给 | 抛 —— 分区上只有一条外键，必然有一个是摆设 |
| 派生发布漏传上游 | 各 dataset 自己抛，**不悄悄退回自造** |

⚠️ **复用既有快照的路径也要把血缘带出去**（取自既有分区的 `raw_artifact_id`）。
不带的话，「今天重跑」与「今天第一次跑」给下游的东西不一样 ——
而下游会静默退化成自造。

---

## 执行

```bash
# 1. 导出一次真实决策的全部字节
python3 tools/verify/source_bundle.py export es-<…> --out /tmp/replay-bundle.zip

# 2. 摊回一棵干净的树
python3 tools/verify/source_bundle.py restore /tmp/replay-bundle.zip --into /tmp/recovered

# 3. 🔴 在**那棵树**上跑回放（不能用 bin/biga-data）
env -C /tmp/recovered PYTHONPATH=$PWD/src python3 -m easyup_biga.data.cli \
    --db data/biga.db --data-root data drill-replay es-<…> \
    --ledger $PWD/data/phase3_acceptance.jsonl
```

真实输出：

```json
{ "passed": true, "name": "SOURCE_ZIP_REPLAY",
  "detail": { "manifest_version": "2", "legacy_raw_snapshot_ids": [407, 408] },
  "acceptance_event_hash": "c8826c3e…" }
```

派生血缘改完之后，同一棵树上实测：

```
--- 分区的 raw 血缘 ---
  cn.equity.daily_bars      raw_artifact_id = raw-28b1b70a…
  cn.security.tradability   raw_artifact_id = raw-28b1b70a…   ← 同一条
--- raw_artifacts ---
  cn.security_master        eastmoney_security_master
  cn.equity.daily_bars      eastmoney_eod
                            （没有 derived_biga 的行了）
```

---

## 坑

### 坑 1 · 记进账本之前，先证明这项演练会红

```
① 原样：      True
② 改一个字节：False   file hash mismatch: …
③ 还原：      True
④ 删掉文件：  False   file is unreadable: …
⑤ 再还原：    True
```

两种破坏走**同一个异常类型** —— 这正是 `verify_file_hash` 那句 docstring
承诺过的（「最彻底的那种篡改不能绕过为篡改准备的那条分支」）。

### 坑 2 · 探针测的是辅助函数，不是行为

给 CLI 缺陷写完测试后，探针把 `main()` 改回旧实现 —— **仍然绿**。
因为测试直接调 `_positional_tokens()`，而破坏点在 `main()` 里。
辅助函数还在、还对，命令行照样坏。

⇒ 补一条**真起子进程**的测试，判据落在退出码上。

### 坑 3 · 仓库自己的三道守卫当场抓住新工具

写完 `source_bundle.py` 跑全量，红了三条：

| 守卫 | 抓到什么 |
|---|---|
| `test_每个入口都在设计文档里被提过` | 新工具建出来了，`docs/design/` 一个字都没提 |
| `test_tests下不许再出现冗余的sys_path_insert` | 测试文件里多余的 `sys.path.insert` |
| `test_名单没有漏掉任何用了这套码的工具` | 新工具用了三态退出码，却不在 `_WIRED` 名单里 |

第三条尤其值得记：那张名单曾经漏过两个工具，后来反过来被一条
「用了这套码就必须在名单里」的守卫钉住。这次它立刻生效了。

### 坑 4 · 全量绿，只是说明没人在验这件事

派生血缘改完后跑全量 —— **2377 passed, 0 failed**。
那不是「改对了」的证据，是「**没有测试在验这条血缘**」的证据。
真正的验证是直接查库（见上面「执行」那段输出），然后才补守卫。

---

## 验证

```bash
# 演练账本：SOURCE_ZIP_REPLAY 必须是 true
bin/biga-data acceptance-status | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['drills'])"
# 预期含 'SOURCE_ZIP_REPLAY': True
```

```bash
# 派生血缘：可交易性必须指向日线那一条
python3 -m pytest tests/test_derived_lineage.py -q
# 预期 8 passed
```

```bash
# 带值全局标志后面的子命令要能跑
bin/biga-data --db data/biga.db snapshots cn.news.flash >/dev/null; echo $?
# 预期 0
```

---

## 本章要点

| # | 一句话 |
|---|---|
| 1 | 「判据写好了」和「这项检查跑过」之间隔着一整套操作；没工具时成本不是麻烦，是每次重新推导 |
| 2 | 打包只带这次决策引用到的字节 —— 多带的会让回放在一棵本不该跑通的树上跑通 |
| 3 | WAL 下拷库要 `VACUUM INTO`：`cp` 漏掉的事务**不报错** |
| 4 | 绝对路径的包恢复不出来，而它的失败方式是**通过** —— 「文件不存在」反而是好结局 |
| 5 | 🔴 恢复树上用 `bin/biga-data` 跑回放会假绿（它 `cd` 回仓库根）⇒ 演练记下 `cwd` 与 `db_path` |
| 6 | 错误信息指错方向比报错本身更糟：`未知子命令 '<值>'` 让人去查子命令，而子命令是对的 |
| 7 | 一个「半完成」的条目标成「未开始」，比标成「已完成」更难被发现 |
| 8 | 派生数据集的 raw 不能是自己的输出 —— 那让 `content_sha256` 变成自己证明自己 |
| 9 | 也不能复制上游字节到自己名下：`provider_id` 会撒谎。**引用，不复制** |
| 10 | 复用路径也要把血缘带出去，否则下游会在「今天重跑」时静默退化成自造 |
| 11 | 探针测辅助函数证明不了行为 —— 破坏点在调用方时，判据要落在进程退出码上 |
| 12 | 改完跑全量全绿，可能只是说明**没人在验这件事** |
