# F 节·打包与依赖清理 · 任务分发提示词

> 🔧 **操作** · 自包含，可直接粘贴
> **覆盖**：外部评审 0-H 清单 F 节里"当前必需"的那部分（跨包引用清理 /
> `pyproject.toml` 正式化 / console scripts / ruff / mypy）的开工提示词 ｜
> **不覆盖**：F 节的 Dataset/Provider/Pipeline Registry 三项——评审自己标注
> "后续阶段"，本轮明确不做（见下方裁定）；升级方案本身见
> [`../design/deterministic-orchestration.md`](../design/deterministic-orchestration.md)
> 第 375 行 F 节现状表；批次实际结果做完写进 [`../tutorial/`](../tutorial/README.md)

---

## 怎么用

1. **每个子批开一个新会话**，`cd ~/.openclaw-biga/workspace`
2. 粘「**通用前置**」+ 「**批 U-I / U-II / U-III / U-IV 正文**」两段（中间不用加任何话）
3. 那个会话做完 ⇒ **回到另一个会话做评审**，评审要求见本文「做完之后」

### 🔴 为什么拆成四个子批，而不是一次做完

F 节这五项（跨包清理 / pyproject / console scripts / ruff / mypy）表面上
都叫"打包"，耦合面却完全不同——跟批 A 拆 A-I/A-II、批 D 拆 D-I/D-II 是
同一个理由：

| 子批 | 性质 | 为什么独立 |
|---|---|---|
| **U-I** 跨包引用清理 | 低风险，方案已定死 | 只改 3 个文件的 import 语句，不产生新行为，`TODO.md` 已经把方案写死，直接执行 |
| **U-II** `pyproject.toml` 正式化 | 纯新增，不删东西 | 加 `[build-system]`/`[project]`，**不碰**任何现有 `sys.path.insert` —— 现状与新状态并存，随时可回退 |
| **U-III** 移除动态 `sys.path` 依赖 | 高风险，82 个文件 | **必须排在 U-II 之后**——包能被真正 `pip install -e .` 之后，才有资格把手动 `sys.path.insert` 换成"包已装好，直接 import" |
| **U-IV** ruff / mypy | 新增工具链，范围要收住 | 在一个已有几万行的仓库上引入 lint/type 检查通常会炸出一大批历史违规——这一批只加配置、拿到一个"当前基线"，不承诺清空历史违规 |

⚠️ **U-I → U-II → U-III 有严格先后依赖，U-IV 可以随时插入并行**，因为它
不改运行代码，只加检查工具。

### 🔴 F 节的 Dataset/Provider/Pipeline Registry 三项——裁定明确不做

评审给的 F 节原文十项里有三项是 Registry。**评审自己的总览表把它们标成
"后续阶段"**（`docs/external/biga-latest-deep-review-classified/` 的深度评审
第 64-65 行：`Dataset / Provider Registry | 未完整完成 | 后续阶段`、
`Pipeline Registry | 未完成 | 后续阶段`），且推荐实施顺序那节明确写
"不要先增加：更多 Dataset"。

⇒ 这四个子批**任何一个都不建 Registry**。想到相关的东西记进 `TODO.md`，
不要顺手做——同 `orchestration-kickoff-prompt.md` 的老规矩。

---

## 通用前置（每个子批都要粘这一段）

```text
你在 ~/.openclaw-biga/workspace，这是 EasyUp for BigA 2.0 的代码仓库 ——
一套基于 OpenClaw 的 Multi-Agent A 股短线决策辅助系统（不自动下单，
产出一张证据可追溯、可回放的 Decision Card，人做最终决策）。

## 第一步：读这几份，别跳

1. CLAUDE.md                                        三条红线 + 四条不变式 + 16 条已定裁定。已定的事不要重新讨论
2. docs/design/deterministic-orchestration.md §8.1  H-I/H-II 迁移的现状与理由——F 节要清理的 legacy import 就是这次迁移留下的账
3. docs/design/deterministic-orchestration.md 第 375 行     F 节现状的既有核实结论，不要重新调查一遍
4. docs/design/architecture.md §9                   失败模式清单 L-1…L-14
5. .claude/skills/dev-workflow/                      本仓库的开发流程（🔴 用仓库内这份，不要用用户级那份）
6. docs/README.md                                    文档规约

## 第二步：开工前跑一次环境自检

    BIGA=~/.openclaw-biga/bin/biga
    $BIGA --version
    python3 -m pytest -q | tail -2                   # 基线必须全绿，记下条数
    git status --short                                # 确认工作区干净（DREAMS.md 是别的会话的运行时产物，忽略）
    grep -oE "批 [A-Z](-I{1,3})?" TODO.md | sort -u -t' ' -k2 | tail -5
                                                        # 🔴 本仓库这几天连续撞过 6 次批次字母碰撞——
                                                        # 确认下面用的字母（U）此刻仍然空着，
                                                        # 如果已经被别的并行会话占了，往后挪一个字母，
                                                        # 不要抢

任何一项不符就停下来查清楚，不要往下做。

## 🔴 六条硬纪律

R-1  所有 OpenClaw 命令走 ~/.openclaw-biga/bin/biga，绝不用裸 openclaw
R-2  不占用共享命名空间里的默认名（nvm bin / systemd 单元名 / 端口）
R-3  UNKNOWN ≠ PASS
G-1  🔴 每一道新守卫先用探针验证它会红——把被守的东西真的弄坏，确认报红，再还原
G-2  docs/external/ 只读，永不修改
G-3  仓库 Public，push 即发布。commit 前跑 tools/verify/audit_public.sh --worktree

## 交付物（五样，缺一不算完成）

1. 代码
2. 测试（新增的 + 现有回归全绿）
3. 探针记录（如果这一批引入了新的不变式/守卫）
4. CHANGELOG.md [未发布] 条目，写「为什么」不只是「做了什么」
5. 教程章节 docs/tutorial/ 起（编号先看 docs/tutorial/README.md 当前最后一章，
   同样有过 5 次撞车记录，开工前先确认没人在写同一个号）

## 完成判据

1. python3 -m pytest 全绿，且 tools/verify/sync_test_count.sh 同步过（同步前先 git status --short 确认干净）
2. tools/verify/audit_public.sh --worktree 十一项全绿
3. bin/biga-card --check <一个已有决策号> 回放一致（用 --list 3 挑号，不要为了验收出新卡）

## 做完之后

不要自己宣布通过。交出：
  · git diff 的摘要
  · 如果引入了新守卫：探针的红灯输出
  · 你自己认为最可能被攻破的一处，以及为什么
由另一个会话评审。
```

---

## 批 U-I · 跨包引用清理

⚠️ **可以立即开工，不依赖其他三个子批。**

```text
做 TODO.md 里已经定死的"跨包引用清理"（H-II 落地后记的账，搜索这个词能
直接找到原文）。方案已经想清楚了，这一批是执行，不是设计。

## 要解决的问题（已实测复现，不是假想）

`H-I`/`H-II` 两批把 `_contract`/`_store`/`_sources`/`_runtime`/`_snapshot`
五个包从 `skills/_*` 迁到了 `src/easyup_biga/{domain,persistence,providers,
runtime,application}`，旧位置留了 re-export 薄壳保证外部导入不用改。

但迁移只搬了"对外接口"，没有清理"包内部互相怎么导入"——
`easyup_biga.persistence` / `providers` / `application`（具体是
`coordinator.py`）内部仍然写着 `from _contract import ...` 这种**旧写法**。
这三个文件的 import 之所以现在还能跑，全靠 `pyproject.toml` 的
`pythonpath = ["skills", "src", "."]` **同时**挂了 `skills/` 和 `src/` 两条路。

一旦 F 节后续（批 U-III）真的把 `skills/` 从 pythonpath 里退场，或者
任何人在**没有** `skills/` 目录的环境里单独安装 `src/easyup_biga`（批 U-II
的验收标准原本就是这个），这三处会当场 `ModuleNotFoundError`。

## 已经查清楚的范围（不要重新调查）

- `domain` 与 `runtime`（`adapter.py`/`mcp.py`）**已经自足**，零跨包旧写法
- 带着旧写法的只有三处：**`persistence` / `providers` / `application`
  （coordinator.py）**
- 改法：这三处一次性改成 `from easyup_biga.xxx import ...`，**不要分批改**
  ——分批改等于同一件事做两次，且中途状态更难判断"改没改全"

## 做什么

1. `grep -rn "^from _\|^import _" src/easyup_biga/` 找出全部旧写法引用
   （先确认这条 grep 命中的确实只在 persistence/providers/application 三处，
   如果命中了别处，说明"已核实范围"这句话过期了，先停下来问清楚再往下做）
2. 逐处改成对应的 `easyup_biga.xxx` 绝对导入
3. 改完之后，**在一个只挂 `src/`、不挂 `skills/` 的临时 `PYTHONPATH` 下**
   跑一次 `python3 -c "import easyup_biga; import easyup_biga.persistence;
   import easyup_biga.providers; import easyup_biga.application"` ——
   这是本批唯一有意义的验收，比"pytest 全绿"更直接地证明了自足性
   （pytest 因为 `pythonpath` 两条都挂着，测不出这个问题）

## 必须做的探针（G-1）

P1  上面那条"只挂 src/"的隔离 import 测试——sabotage：改回一处旧写法，
    断言它报 `ModuleNotFoundError`；改对之后重跑，断言成功
P2  全量 pytest 不受影响（这一步只改 import 路径，不改任何行为）

## 不要做

- 不要顺手把 H-III 的留白填上（`orchestrator.py`/`feishu_deliverer.py`/
  `notify_worker.py` 目前故意不搬，见 TODO.md 批 H-III 的理由）
- 不要碰 `skills/` 目录下的任何薄壳文件——它们的 re-export 写法是 H-I/H-II
  定的、已经验证过，这一批不涉及对外接口
```

---

## 批 U-II · `pyproject.toml` 正式化

⚠️ **可以立即开工，不依赖 U-I，但建议在 U-I 之后**——U-I 先让内部 import
自足，U-II 的"能不能真的单独装起来"验收才不会同时踩两个坑，分不清是
打包配置的问题还是遗留 import 的问题。

```text
给 pyproject.toml 加 [build-system] + [project]，让 `pip install -e .` 与
`python -c "import easyup_biga"`（在无额外 PYTHONPATH 的环境里）真正成立
——这是 F 节清单原文给的验收标准，目前必然失败（pyproject.toml 现在只有
[tool.pytest.ini_options]）。

## 这一批刻意不做的事

**不删除、不修改任何现有的 `sys.path.insert`。** 82 个文件里手写的路径
操作原样保留——那是 U-III 的事。这一批结束时，"旧的能跑的方式"和"新的
能跑的方式"应该**同时成立**，没有任何现有调用路径被破坏。

## 需要你去核实、不要凭空定的几件事

1. **`skills/` 目录算不算这个 pip 包的一部分？**
   `skills/` 里是薄壳 + 各 skill 的业务脚本（`market-calc/scripts/*.py` 这类），
   目前是被 OpenClaw 运行时按"技能目录"加载的，不是被 Python import 机制当
   包用的（虽然子模块壳文件本身用了 `sys.modules[__name__] = 真实模块` 这种
   手法）。先读 H-I/H-II 两批的说明搞清楚这层关系，再决定 `[project]` 的
   `packages`/`package-dir` 该不该包含它——**不要想当然地包含或排除**。
2. **运行时依赖有哪些？** 扫一遍 `src/easyup_biga/` 和 `skills/` 下实际
   `import` 的第三方库（stdlib 之外的），核实清楚再写进 `[project.dependencies]`
   ——不要照抄任何示例项目的依赖列表。
3. **包名与版本号。** 建议 `name = "easyup-biga"`（import 名 `easyup_biga`
   已经是既成事实）。版本号：CLAUDE.md 已经说清楚"2.0"是代际名不是语义化
   版本号，真正的语义化版本在 CHANGELOG.md（目前 `[未发布]`，上一个发布版
   是哪个自己去 CHANGELOG.md 查）——`pyproject.toml` 的 version 字段要不要
   跟它同步是这一批要做的一个小裁定，写进 CHANGELOG 说明选了哪个、为什么。
4. **`requires-python`。** 跑 `python3 --version` 确认这台机器实际用的版本，
   不要拍一个猜测值。
5. **console scripts 落在哪几个命令上？** `bin/biga`/`bin/biga-card`/
   `bin/biga-reap` 是 bash 脚本，不是 Python 函数，天然不适合塞进
   `[project.scripts]`（那个机制要求一个可调用的 Python 入口点）。真正适合
   的候选是 `tools/verify/*.py` 那批纯 Python CLI（`isolation.py`/
   `latency_report.py`/`agent_trace.py`/`missing_ledger.py`/...）——它们
   现在都是 `python3 tools/verify/xxx.py` 这种相对路径调用。核实一遍这份
   清单、每个的 `main()` 签名是否已经是"零参数、读 sys.argv"的形状（能直接
   套进 console_scripts 的 entry point），再决定要不要顺手统一命令前缀
   （比如 `biga-verify-isolation`）。**如果决定不做这一项**（比如觉得
   "谁会敲这些命令"没有真实需求），把理由写进 CHANGELOG，不要静默跳过
   ——F 节原文明确点名了这一项。

## 做什么

1. 上面五点核实完、写进 CHANGELOG 的"为什么"之后，落 `[build-system]`
   （推荐 `hatchling` 或 `setuptools`——哪个更适合看 1 的答案）+ `[project]`
2. 验收：
   ```
   python3 -m venv /tmp/biga-pkg-test && source /tmp/biga-pkg-test/bin/activate
   pip install -e ~/.openclaw-biga/workspace
   python3 -c "import easyup_biga; print(easyup_biga.__file__)"
   deactivate
   ```
   在一个**全新虚拟环境、没有任何 PYTHONPATH 覆盖**的地方跑通，这才是
   F 节验收标准原文要求的场景——不要只在当前开发环境里验，当前环境的
   `pythonpath` 配置会掩盖真实的打包缺陷。

## 必须做的探针（G-1）

P1  上面那条全新 venv 安装验收——sabotage：临时删掉 `[project.dependencies]`
    里的某一项，断言 import 报错（证明依赖列表不是摆设）；改回来重跑成功
P2  现有 82 处 `sys.path.insert` 一个字不改，pytest 全绿（证明本批没有破坏
    现状）

## 不要做

- 不要删除任何一处现有的 `sys.path.insert`——那是 U-III
- 不要给 `tools/verify/` 之外的任何脚本加 console_scripts entry，除非核实
  过它确实是独立可执行的 CLI（`bin/biga-card` 内部调用的
  `skills/decision-card/scripts/orchestrator.py` 不适合——它靠 `bin/biga-card`
  传的环境变量和参数运作，不是一个自足的命令）
```

---

## 批 U-III · 移除动态 `sys.path` 依赖

⚠️ **U-II 合并之后才开这一批。** 没有真正的包安装做后盾，删掉手写的
`sys.path.insert` 会让相应脚本在很多调用方式下直接失效。

```text
把 82 处（开工前重新 grep 一遍，这个数字会随其他并行工作变化）手写的
`sys.path.insert` 逐一核实：哪些已经因为 U-II 的 pyproject.toml 配置或
真实安装而变得冗余、可以删；哪些是仍然必需的（比如某个脚本被设计成
不依赖任何安装步骤、拿来就能跑，这种情况删了 sys.path.insert 就是破坏
这个设计目标）。

## 🔴 这一批的核心风险，开工前必须想清楚

`sys.path.insert` 出现在两类完全不同的场景里，处理方式不一样：

1. **测试文件**（`tests/*.py`）——`pyproject.toml` 的
   `pythonpath = ["skills", "src", "."]` 已经让 pytest 收集到的每个测试
   天然能 `import` 到 `skills`/`src` 下的东西，测试文件里手写的
   `sys.path.insert(0, ...)` 在**通过 pytest 跑**这条路径上大概率是冗余的。
   但要验证，不要假设——有些测试用 `importlib.util.spec_from_file_location`
   手动加载模块（这是另一套机制，跟 `sys.path` 无关，不要连带误删）。
2. **直接被调用的脚本**（`tools/verify/*.py`、`skills/*/scripts/*.py`）——
   这些经常是 `python3 path/to/script.py` 直接跑，不经过 pytest 也不经过
   `pip install`。删掉它们的 `sys.path.insert` 只有在下面两种情况之一成立
   时才安全：
     a. 这个脚本改成了通过 U-II 装好的包导入（`from easyup_biga.xxx import ...`
        代替相对路径拼接），且确认调用方（`bin/biga-card` 等）的运行环境
        真的会先 `pip install -e .`
     b. 这个脚本本来就该保持"零安装步骤可跑"，那就不该删，把它记进
        "已核实保留"的清单而不是漏改

⇒ **不要写一个批量脚本无差别删掉所有 82 处**——那正是 L-13 的另一种形状
（判据是"有没有这行"，不是"这行现在还有没有用"）。逐文件过一遍，分类
记进 CHANGELOG（删了多少、保留多少、保留的每一类各自的理由）。

## 做什么

1. 逐文件分类（测试 vs 脚本，冗余 vs 必需）
2. 先处理"测试文件里冗余的"那一批——风险最低，删完跑 pytest 全绿即验证
3. 再处理"脚本"那一批，每改一个都要**真的按生产调用方式跑一次**
   （比如改了 `tools/verify/isolation.py` 就要 `python3 tools/verify/
   isolation.py` 单独跑一遍，不能只靠 pytest 覆盖到它）

## 必须做的探针（G-1）

P1  对每一处"判定为冗余、删掉"的 sys.path.insert：删除前先跑一次它所在的
    测试/脚本确认能跑，删除后再跑一次确认仍然能跑——**两次都要跑，不是
    只跑删除后那一次**（否则你验证的是"删完还能跑"，不是"删的判断是对的"）
P2  对每一处"判定为必需、保留"的：写清楚保留的具体理由（哪个调用方、
    在什么情况下会因为删掉它而失效）
P3  全量 pytest 在**默认 pythonpath 配置**下全绿；额外在临时清空
    `pyproject.toml` 的 `pythonpath` 配置的情况下跑一遍，观察哪些测试开始
    依赖手写的 sys.path.insert 存活——这条能直接证明"哪些是真的冗余"

## 不要做

- 不要在同一个 commit 里同时改动 82 个文件的行为逻辑——这一批只删路径
  操作，不改任何业务代码
- 不要假设"测试能跑就说明这处 sys.path.insert 没用了"——U-II 之后 pytest
  的 `pythonpath` 配置本身可能掩盖真实依赖，P3 那条交叉验证是必须的，
  不是可选的
```

---

## 批 U-IV · ruff / mypy（可与 U-I/II/III 任意一个并行）

```text
给仓库接上 ruff 和 mypy（或 pyright），拿到一个"当前基线"。

🔴 **这一批的范围刻意收窄：只加配置 + 拿基线，不承诺清空历史违规。**
一个运行了这么久的仓库第一次接 lint/type 检查，大概率会炸出几十到几百条
历史违规——如果这一批的目标是"清零"，范围会失控到覆盖全仓大部分文件，
而这跟 F 节本身（打包与依赖清理）已经不是同一件事了。

## 做什么

1. 加 `ruff.toml`（或 `pyproject.toml` 的 `[tool.ruff]`）—— 规则集从保守的
   子集开始（比如只开 `E`/`F`/`I` 这类基础错误与未使用导入，不要一次性
   开满所有规则类别）
2. 加 `mypy.ini` 或 `pyrightconfig.json`——同样从宽松模式开始
   （比如 mypy 的 `--ignore-missing-imports`，不要一开始就上 `--strict`）
3. 跑一遍，把当前违规数记进 CHANGELOG（分类：哪些文件、大致哪几种问题）
4. 如果发现**其中有一条明显是真 bug**（不是风格问题，是类型不对/逻辑
   死代码这类），单独修掉并写清楚——但不要把这一批变成"顺手把看到的都修了"，
   那样会让 diff 大到没法评审，也偏离了这一批的范围

## 必须做的探针

无需 G-1 意义上的"守卫"探针——这一批不引入新的运行时不变式，
只需要证明"配置文件本身有效"：跑一次 `ruff check .` / `mypy src/` 能正常
执行完并给出输出（不是配置写错了直接报错退出）。

## 不要做

- 不要追求"零违规"——记基线，不是清零
- 不要因为 ruff/mypy 报了很多而顺手改一大批不相关文件的写法
  （比如把全仓的 `Optional[X]` 改成 `X | None`）——那是一次独立的、
  范围明确的风格统一工作，不该藏在"接入 lint 工具"这个 commit 里
```
