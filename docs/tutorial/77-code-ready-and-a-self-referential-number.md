# 第 77 章 · Code Ready，与一个写不进自己那棵树的数字（Phase 4 收口）

> 📄 **过程 · 写完即冻结**（2026-09-27）
> **覆盖**：Phase 4 Code Ready 的最后一步、收口顺序为什么是强制的、以及补上的那处 marker 绑定 ｜ **不覆盖**：Evidence Ready（它卡在采样，见 [`../design/phase-4-review-measurement.md`](../design/phase-4-review-measurement.md) §7.2）

---

## 目标 / 产出

六批外部交付合完、生产库迁到 v31、第一次 catch-up 也跑过了之后，
`tools/verify/phase4_acceptance.py` 仍然是 **exit 1**。

差的不是代码，是**一条证据**：闸门要求账本里有一次真跑过的全量回归。

产出：`data/phase4_acceptance.jsonl` 里的第一条 `FULL_REGRESSION / PASS`、
`data/phase4_code_ready.json`、以及 tag `v1-review-measurement-foundation`。

---

## 为什么这么做

### 闸门为什么不肯自己信自己

`finalizer.py` 把 Code Ready 拆成两个独立条件：

```python
static_code_ready  = not static_errors          # 静态检查
full_regression_ready = any(                    # 账本里有一条对得上的 PASS
    e.event_type == FULL_REGRESSION and e.status == "PASS"
    and all(e.detail.get(k) == current[k] for k in BINDING_KEYS)
    for e in events
)
```

第二条才是这一章的主角。它防的是一句听起来很像证据的话：

> 「我本地跑过了，全绿。」

这句话缺的不是诚实，是**对象**。跑的是哪棵树？哪个 Python？装的哪些依赖？
选的哪一组测试？七个 `BINDING_KEYS` 就是把这四个问题钉死：

```
source_tree_sha256 · project_version · schema_version · python_version
dependency_fingerprint · test_selection_sha256 · git_commit
```

改一个字节，`source_tree_sha256` 变；换个 Python，`python_version` 变；
只跑一个子目录，`test_selection_sha256` 变。任何一项对不上，
`full_regression_ready` 就是 `False` —— **上一次的绿不会顺延给这一次。**

### 🔴 于是数字变成自指的：它写不进自己那棵树

`git_commit` 在绑定里。这意味着：

> **任何一次新 commit，都会让已有的回归证据失配。**

把它推到底，会得到一条反直觉的结论 —— 本章标题那句：

> 这份文档、CHANGELOG、README 徽章里，**都不能写下那个 commit hash**。
> 写进去就得 commit；一 commit，那个 hash 指的就是**不包含这句话的上一棵树**。

这不是工具的缺陷，是「证据绑定当前树」这个要求的**必然推论**。
一个能写进自己所描述的那棵树里的哈希，只说明它没真的描述那棵树。

⇒ 证据不进 git，只落 `data/`（`.gitignore` 里那条 `/data/*` 早就挡着）。
文档负责说**证据在哪、怎么复现**，不负责抄一份**必然过期**的副本。

> 通用原则：**自指的数字应当留在产物里，不留在源码树里。**
> 源码树能放的是「怎么把它算出来」。

### 这条纪律仓库里已经有了一份

`CLAUDE.md` 里 `sync_test_count.sh` 那一节，讲的是同一件事的另一个面：

> 同步必须跑在「这棵树就是要发布的那棵树」的时刻。

那次踩的坑有两个方向 —— 脏工作区**多算**16 条、未跟踪的新章节**少算** 5 条 ——
最后归结成一句「先 commit，再同步，再 amend」。

Code Ready 是**完全相同的形状**，只是代价更高：测试条数错了是个难看的数字，
回归证据错了是**一个没被验证过的提交上挂着一个 Code Ready tag**。

### 收口顺序因此是强制的，不是偏好

```text
写文档 → commit → 在那个 commit 上跑全量回归 → 写 marker → 打 tag
```

反过来做（先跑回归、再写文档、再提交），tag 指向的那棵树上：

- 文档写着「Code Ready 已达成」
- 同一棵树上的闸门报 `code_ready: False`

**这两句话同时印在一个发布里**，比没有 tag 更糟。

---

## 坑：一个不指认对象的 CODE_READY

跑之前顺手读了一遍 `write_code_ready_marker()`，它写出来的是：

```json
{ "tag": "v1-review-measurement-foundation", "status": "CODE_READY",
  "checks": [...], "evidence_ready": false, "evidence_errors": [...] }
```

**没有 `git_commit`，没有 `source_tree_sha256`。**

而 `code_ready` 这个结论的定义**就是**「这些字段与账本里某条 PASS 逐项相等」——
marker 记下了结论，却把结论赖以成立的那个对象全部丢掉了。

危害是**延时**的，这也是它容易被放过的原因：写出来的那一刻它是对的。
下一个 commit 之后，闸门会诚实地翻成 `code_ready: false`，
而硬盘上那份 marker 仍然写着 `CODE_READY`，**且没有任何字段能说明它当初说的是哪一版**。

> 🔴 **一份会比它所描述的对象活得更久的结论，必须自带对象。**
> 这正是裁定 14 那句「徽章与验收数字必须始终描述一个真被测过的提交」——
> 只是这次它不在 README 上，在一个 `data/` 下的 JSON 里。

⇒ 把闸门里那个局部的 `binding_keys` 提成模块常量 `BINDING_KEYS`，
marker 写 `source_binding = {k: current[k] for k in BINDING_KEYS}`。

两点刻意为之：

| 选择 | 理由 |
|---|---|
| marker 的 key 从 `BINDING_KEYS` **派生** | 手写第二份就是 L-3；闸门和 marker 必须永远是同一组 |
| `repo_root` **不进** marker | 它是本机绝对路径，而 marker 会被贴进工单 / PR |

### 探针跑了两个方向

第一个方向是「字段在不在」，第二个是「值对不对」—— 后者容易被漏掉，
因为一条只断言 `"source_binding" in payload` 的测试，对着一份**写死常量**的
实现照样是绿的。

```console
① 把 marker payload 里那行绑定删掉        → FAILED（断言 binding 非空）
② 绑定保留、值全写成 "deadbeef"           → FAILED（断言 git_commit 相等）
恢复后                                     → 23 passed
```

再加一条反向用例：monkeypatch 把 `_git_commit` 换成一个不存在的 commit，
断言 `code_ready` 翻成 `False`、且 `write_code_ready_marker()` 直接 `RuntimeError` ——
**闸门不过时，marker 根本写不出来。**

---

## 执行

```console
$ ./bin/biga-review verify --run-full-regression
passed: True   returncode: 0   command: ('/usr/bin/python3', '-m', 'pytest', '-q')
static_code_ready: True
full_regression_ready: True
code_ready: True
evidence_ready: False
code_errors: ()
evidence_errors: ('no MeasurementRun',)
EXIT=2
```

闸门从 **exit 1 → exit 2**。这两个码的区别正是红线 R-3：

| 码 | 含义 |
|---|---|
| `1` | 代码面有问题（**坏了**） |
| `2` | 代码面过了，证据不足（**还没到时候**） |
| `0` | 两个都过 —— 要等 Evidence Ready |

⚠️ `exit 2` 是 Phase 4 此刻**正确的**收口状态，不是一个待修的失败。
把它「修」成 0 的唯一合法途径是攒够采样（§7.2 要 ≥30 个 anchor 日，
今天生产库是 **3** 个），不是改判据。

---

## 验证

```bash
cd ~/.openclaw-biga/workspace

# ① 闸门三态
python3 tools/verify/phase4_acceptance.py ; echo "exit=$?"      # 期望 2

# ② 账本里那条证据绑的是 HEAD
python3 -c "
import json, subprocess, pathlib
head = subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()
ev = [json.loads(l) for l in pathlib.Path('data/phase4_acceptance.jsonl').read_text().splitlines()]
ok = [e for e in ev if e['event_type']=='FULL_REGRESSION' and e['status']=='PASS'
      and e['detail']['git_commit']==head]
print('绑到 HEAD 的 PASS 条数:', len(ok))"      # 期望 >= 1

# ③ marker 自带对象
python3 -c "
import json; b=json.load(open('data/phase4_code_ready.json'))['source_binding']
print(b['git_commit'], b['source_tree_sha256'][:16])
assert 'repo_root' not in b"

# ④ 证据不进仓库
git check-ignore data/phase4_acceptance.jsonl data/phase4_code_ready.json

# ⑤ 两条探针对应的判据
python3 -m pytest -q "tests/test_p4_r11.py::test_code_ready_marker_必须带上被测那棵树的绑定"
```

---

## 本章要点

| # | 要点 |
|---|---|
| 1 | 「我本地跑过了，全绿」缺的不是诚实，是**对象** —— 七个 `BINDING_KEYS` 把对象钉死 |
| 2 | 上一次的绿**不顺延**给这一次：改一个字节、换一个 Python，证据就失配 |
| 3 | 🔴 `git_commit` 进绑定 ⇒ 那个哈希**写不进自己那棵树**，写进去指的就是上一棵 |
| 4 | 能写进自己所描述的那棵树的哈希，只说明它没真的描述那棵树 |
| 5 | **自指的数字留在产物里（`data/`），不留在源码树里**；文档说「在哪、怎么复现」 |
| 6 | 收口顺序是强制的：**写文档 → commit → 跑回归 → 写 marker → 打 tag** |
| 7 | 反过来做，tag 那棵树上文档说达成、闸门说没达成 —— 比没有 tag 更糟 |
| 8 | 🔴 **一份会比它所描述的对象活得更久的结论，必须自带对象** |
| 9 | marker 的危害是**延时**的：写出来那一刻它是对的，下一个 commit 之后才变成假话 |
| 10 | 绑定要从闸门那份**派生**，不要手写第二份（L-3）；本机绝对路径不进 marker |
| 11 | 探针要跑两个方向：**字段在不在**、**值对不对** —— 只断言前者时写死常量照样绿 |
| 12 | `exit 2` 是 Phase 4 正确的收口状态；把它改成 0 的唯一合法途径是攒够采样 |
| 13 | 这与 `sync_test_count.sh` 是同一条纪律，只是代价更高：一个没被验证的提交挂着发布 tag |
