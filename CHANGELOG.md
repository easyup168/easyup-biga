# 更新日志

本文件记录 EasyUp for BigA 2.0 的版本与变更历史。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **维护约定**
> - 每完成一段工作，先往 `[未发布]` 里追加条目，再开始下一段
> - 分类只用这六种：`新增` / `变更` / `修复` / `移除` / `废弃` / `安全`
> - 🔴 **条目要写「为什么」，不只是「做了什么」** —— 三个月后有价值的是前者
> - 发版时把 `[未发布]` 整段改成版本号 + 日期，并在文件底部补链接

---

## [未发布]

### 🔧 变更 · 批 H-I —— 三个共享基础设施包迁进 `src/easyup_biga/`，旧路径留薄壳

外部评审 §29 建议把代码长期迁到 `src/easyup_biga/{domain,application,providers,
runtime,persistence,integrations,cli}`。设计文档 §8 已裁定采纳、并把代价写在明处
（作废 19 章教程路径、`sys.path.insert(0,"skills")` 是承重墙、结构改动无行为判据）。
这一批（H-I）只搬 §8 自己讨论过、给了具体缓解方案的三个包：
`skills/_contract` → `easyup_biga.domain`、`skills/_store` → `easyup_biga.persistence`、
`skills/_sources` → `easyup_biga.providers`。**纯目录搬迁**（`git mv` 保 history，
21 个子模块文件内容逐字节不变），旧包原地留 re-export 薄壳 ⇒ 全仓 199 处
`from _contract import ...` 之类的导入**一个字符都不用改**。

**为什么拆出 H-II 留白**：`_runtime`/`_snapshot` 往哪迁、`application`/`integrations`/
`cli` 三个命名空间装什么 —— §8 的缓解表**没讨论过**，说明当时也没想清楚。现在硬做
只能得到没有设计依据的猜测性目录，还违反「按需创建，不预建空目录」（`agents/` 已吃
过这个教训）。⇒ 留白，等真有内容要放时再建。也没有引入真打包层
（`[project]`/`pip install -e .`）：`src/` 仍是靠 `sys.path` 手动挂载的普通目录树。

**裁定：`persistence`/`providers` 暂时不自足，接受**。这两个包内部仍是
`from _contract import ...`（旧写法，没改），只挂 `src/`、不挂 `skills/` 会
`ModuleNotFoundError: _contract`（独立复核实测确认；`domain` 因为是叶子包、
不依赖别的两个包，反而自足）。真实运行时 `skills/` 与 `src/` **恒同时在
path 上**（薄壳自挂 + `pyproject.toml` 都挂了两条），这条限制从不在真实
路径上发作，是纯结构性的观察，不是活 bug。改法是把这两个包内部的跨包
`from _contract import` 换成 `from easyup_biga.domain import`——但那是
**内容改动**，会让「结构改动无行为判据、行为不变即通过」这条验收方式
失效，且 §29 与 §8 都没有把这一步纳入讨论范围。⇒ 留给 H-II（或专门的
「跨包引用清理」批次），H-I 里明确记录、不当成意外发现，也不假装它不
存在——这正是 Strangler Pattern 的中间态（新代码暂时还依赖旧壳的一角），
不是遗漏。

**薄壳怎么写的**（两个不显然的裁定）：
- 包级壳（3 个）用自身 `__file__` 相对路径把 `src/` 挂上 `sys.path` —— **不依赖
  pytest 的 `pythonpath`**，因此 `bin/biga-card` 拉起的子进程、`systemd-run` 脱树跑的
  （都不经过 pytest 配置）也能 import 到 `easyup_biga`。`pyproject.toml` 里**另外**把
  `src/` 列进 `pythonpath` 只为 pytest 内**直接** `import easyup_biga.*` 稳定可达 ——
  两条路径分别覆盖、分别验证（见 P5）。
- 子模块壳（21 个）用 `sys.modules[__name__] = 真实模块` 别名，让 `_store.db` 与
  `easyup_biga.persistence.db` 成为**同一个模块对象** —— 连下划线私有名都一致，杜绝
  「壳与本体漂移」。`import *` 会漏掉下划线名、且污染 stdlib 名，故不用它。

**🔴 一个「纯目录搬迁」本不该有、却真的有的行为变化**：`db.py` 与 `tradetime.py`
用 `__file__` 相对路径**自定位**（`DEFAULT_DB_PATH` = 仓库根/`data/biga.db`；tradetime
把 `skills/` 挂上 path）。这两个文件从 `skills/_X/` 迁到 `src/easyup_biga/Y/` 深了一层，
`parent.parent.parent` 于是从「仓库根」变成了「`src/`」—— `DEFAULT_DB_PATH` 悄悄算成
`src/data/biga.db`，指向一个不存在的库。**全套 1358 条测试没抓到它**（测试都用
`tmp_path`/`BIGA_DB_PATH`，不走默认路径），是 P3 的 `bin/biga-card --check` 当场撞红
（迁移前「组装一致」→ 迁移后 `StoreNotInitialised: src/data/biga.db`）。⇒ 给这两处
`__file__` 深度各补一级 `.parent`，注释写清「这不是改行为，是**保住**行为」。
教训：**对自定位文件，纯移动不是行为中立的** —— 这正是 §8 说「结构改动唯一可信的
验收是行为不变」的实例，也是 P3 存在的全部理由。

**🔴 守卫常量（L-13）**：契约/DB「唯一实现」两道 AST 守卫认的是**真实类定义在哪个
目录**（`CONTRACT_DIR`/`STORE_DIR`）。迁移后真实定义搬走、旧路径只剩薄壳（无类定义），
常量不改这道守卫会在新目录上**静默失效**。开工提示词点名两处
（`test_contract_single_impl.py`、`test_no_raw_sqlite.py`），重跑清单又扫出**另外两处**
同形状、提示词没列的：`test_decision_id_ownership.py` 读 `skills/_store/db.py` 源码找
`save_verdict`、`test_store.py` 读 `schema.py`/`db.py` 源码找 `subagent_runs` 注释并按
`skills/_store/` 前缀排除 store 自调 —— 四处全部改指新位置。**只改提示词点名的两处
就会漏掉后两处**，那正是「重跑 grep 确认清单没变」这条纪律要防的。

**探针记录（G-1，每道都见过红）**：
- P1 导入兼容：AST 扫全仓收集 136 条去重 import，在**全新子进程**（只挂 `skills`，逼壳
  自挂 `src`）逐条真执行 → 零 ImportError。sabotage：删 `skills/_sources/sina_news.py`
  壳 → 探针红，精确点名 4 条失败语句及出处（`news_scan.py:73` 等）→ 还原绿。
- P2 「唯一实现」守卫在新位置生效：在 `src/easyup_biga/domain/` 放第二个 `Evidence`
  → `test_contract_目录里每个契约恰好定义一次` 红；移除 → 绿。再把 `CONTRACT_DIR`
  临时改回旧路径 → 守卫在干净树上就红（test_A 把真实域类当成「第二份实现」、exactly-once
  在薄壳里数出 0 个契约类）⇒ 证明改常量是**承重**的、不是装饰。`STORE_DIR` 同构验证。
  ⚠️ 与提示词设想的「改回旧路径→悄悄漏过」不同：因为真实代码**已经搬走**，旧常量是
  **大声报错**而非静默放过 —— 更好，同样证明必要性。
- P3 一致性：`bin/biga-card --check BIGA-20260923-004` 迁移前后逐字段相同（修完深度 bug 后）。
- P4 测试条数：`pytest --collect-only` 迁移前后都是 **1358**（不减）。
- P5 非 pytest 路径：`python3 -c "sys.path.insert(0,'skills'); import _contract,_store,_sources"`
  （不经 pytest 配置）成功，模块解析到 `easyup_biga.*`、`DEFAULT_DB_PATH` 指向仓库根。
- P6 隔离自检：`tools/verify/isolation.py` 迁移前后判词一致（5✅+1🔶，本批不碰端口/单元/nvm）。

### 🔧 变更 · `THIRD_PARTY_NOTICES.md` 补上真实依赖审计，不再是占位模板

上一批开源合规材料（`901556f`）落地时，`THIRD_PARTY_NOTICES.md` 如实写着
"this compliance pack does not attempt to infer the current BigA
repository's actual dependency inventory. Before the next public release,
replace this section"——诚实，但那次审计一直没人做。这次做了：AST 扫全仓
`.py` 收集三方 import（`pyproject.toml` 没有 `[project.dependencies]`、
没有锁文件，扫源码是唯一办法），核对到只有两个真三方包
（PyYAML 6.0.3、pytest 9.0.3，都是 MIT，装的版本号与许可证都从
`importlib.metadata` 直接读的，不是抄记忆）；确认没有任何改写/搬运的
三方源码（扫了 `skills/`/`bin/`/`tools/`/`deploy/` 找"adapted
from"/"vendored"等标记，零命中）；补上 OpenClaw 本身是独立装的 MIT
运行时、不是本仓库打包分发的一部分。同时把「Data-provider notice」的
通用清单换成真实的五个数据源（深交所官方 + 新浪日线/快讯、腾讯行情、
东财股池——四个非官方接口），但**没有**编造 ToS/限流条款——那几列
如实写"Not reviewed"，因为确实没做过那份法律审查，属于 R-3 同一个
道理：不知道就说不知道，不能因为在写文档就放松这条。

### 🐛 修复 · AST 纯度守卫在无 git 退化模式下误判 `docs/external` 参考代码为第二份实现

写批 H 分发提示词时跑 `test_scan_fallback.py`（模拟"没有 `.git`"场景，
给外部深度评审发现的"发布包里没有 git 元数据、守卫集体报错"这条真事故
兜底）附带发现：`docs/external/` 下一份外部评审自带的示意代码
（示范 `Evidence`/`DecisionCard` 长什么样的 `domain_models.py`、示范
`Repository` 直接 `import sqlite3` 的 `repository.py`）触发了两条 AST
纯度守卫——「契约只有一份实现」「DB 唯一入口」。根子：这两条守卫走
`_scan.repo_files()`，正常路径用 `git ls-files -co --exclude-standard`，
被 `.gitignore` 挡住的未跟踪参考材料天然不可见；但 fallback 测试故意
模拟"没有 git"退化成纯文件系统遍历，这时 gitignore 完全不生效，参考
材料第一次进入扫描范围，两条路径对同一批文件给出不同判断——是 L-13 的
形状，守卫在自己的降级模式下悄悄看见了更多东西，而没人告诉它这些不该算。
`test_orchestration_single_source.py` 早就为它自己的扫描器加过同样的
排除（`docs/external/` 在它的 `_EXEMPT_PREFIX` 里），这次把同一个判断
补进 `_scan.py` 的共享判据 `is_external_reference()`，避免每处各写一份
（那正是 `_scan.py` 自己存在的理由），两处改动各自 sabotage-revert 验证
独立生效，并各补一条不靠子进程/fallback 模式就能发现回归的直接单测。

### ✨ 新增 · 批 L —— `cn.trading_calendar`：第一张真实的 `fact_*` 表，`market_is_open` 认节假日了

`skills/_sources/tradetime.py` 长期白纸黑字写着「已知边界：不认节假日」——
纯 weekday 判据会把工作日上的法定节假日（元旦、国庆首日等）当成开市。它有两个真实
生产消费方（`news-scan` 判「此刻是否连续竞价」、`emotion-calc` 判「这个数是真零还是
还没产生」），却零测试覆盖。这一批修这个缺陷，顺带做成一件更大的事：把
`architecture.md` §5.2 画了很久的 `raw → fact → derived` 分层图里的**中间那层**第一次
落成真实 schema —— 在此之前十张表里只有 raw 层被实例化过，`fact_*` 只存在于文档。
用一个非行情、体量小、判据清楚的数据集给 §45「第一版完整市场数据」那一批打样。

**为什么这么设计**（几处不显然的裁定）：

- **数据源选深交所官方 monthList，是探活后综合选出来的**（dev-workflow 第 1 条：设计
  先探活）。把能免鉴权拿到的都真打了一遍 curl：新浪 `klc_td_sh.txt` 可达但返回**加密串**，
  解密要一个 JS 引擎（重依赖，不收）；东财 `RPTA_WEB_TRADE_DATE` 可达但**数据脏**
  （把周日列成交易日、无未来、尾部塞 `20311231` 哨兵行）；timor.tech 放假 API 可达但它是
  **办公日历不是交易所日历**——调休上班的周末它标工作日，而交易所那天不开市（用新浪
  日线实测核实：2026-02-14 等调休日 `traded=False`）。只有深交所官方 monthList
  一手官方、结构化 JSON、每月每天带交易标志、缺日/未发布当场抛错——判据最清楚。
  这也与仓库对 `a-stock-data`（Provider Catalog / 接口参考）的既定定位一致，`jyrq`/`jybz`
  字段形状抄自它。
- 🔴 **一条诚实写出来的部署约束**：`www.szse.cn` 从本项目当前唯一部署环境（WSL）
  **连不通**（TCP 握手后挂死，HTTPS/HTTP 都 45s 超时）。这不是适配器的 bug，是这台机器
  到交易所站点的网络事实。后果：本环境里 `fact_trading_calendar` 保持空表、
  `market_is_open()` 恒走 weekday 回退（安全方向）。⇒ **解析层由离线 fixture 全测、
  落库链由注入桩 fetcher 全测**（联网那层不进离线测试，沿用禁网围栏）；真实抓取会在
  能连通深交所的运行环境里把日历填进来，届时 `market_is_open()` 自动从回退切到查表。
  消费方（`market_is_open`）与它的读取关系**真实且被测**，只是数据写入取决于网络可达性
  ——不是 L-1 的「零消费方」。写下来是因为：认证/取数类故障，第一反应总会怪新系统，
  而这里的空表是网络事实，排查方向天生容易错。
- **回退方向绝不能倒**（红线 R-3）：`is_trading_day()` 查不到返回 `None`，
  `market_is_open` 把 `None` 当「可能开市」回退到 weekday——顶多让依赖它的静默判据**多报**
  一条缺失项（Card 更保守），**绝不把「查不到」当「休市」**：后者会在真实交易日里以为
  休市，是危险得多的方向。探针 P5 钉死「无数据/超范围时结果与改之前逐一相同」。
- **`session_in_progress()` 不改，并加特征测试锁住**：它回答的是「这批数据声明的交易日
  过完了没」（纯时间比较：声明的 trade_date 是不是今天且未到收盘），跟「今天是不是
  节假日」不是同一个问题。给它加节假日感知会把 `emotion` 推向危险方向——节假日的 0
  会被当成真「冰点」而不是「还没产生」。它周末上午也返回 True 是**设计如此**（那天的
  数据确实还没过完），不是缺陷。加特征测试是防后续会话顺手「一起改了」。
- **完整性 fail-closed**：`parse_trading_calendar` 要求响应覆盖该月每一个自然日，
  缺日/未发布/多出别月的日期都当场抛错——宁可整月拒绝，也不放行半份月历把「没数据」
  和「休市」混成一谈。
- **不接 `SnapshotCoordinator`**：那套解决「同一次决策运行内多个 Specialist 必须看同一份
  易变网络数据」；交易日历是低频更新的只读参考表，一年抓几次即可，硬套会引入一套不必要
  的每次-运行开销。日历落 raw 直接调 `save_raw_snapshot`。
- **只追加、历史事实不 UPDATE**：交易所若事后补发调整（临时增/删交易日），写更晚
  `retrieved_at` 的新行，`is_trading_day` 按 `retrieved_at` 取最新一条——覆盖旧行就
  没法回答「我们当时看到的日历是什么」（与 raw 层同一条 L-8 先例）。

**建的东西**：

- schema **v15**：`fact_trading_calendar`（`trade_date`/`is_open`/`source`/`as_of`/
  `retrieved_at`/`snapshot_id`→raw/`created_at`，`CHECK(is_open IN (0,1))`）——
  这个仓库**第一张真实的 `fact_*` 表**，接 `_append_only()` 触发器。
- `skills/_sources/szse.py`：深交所日历 Provider，照 `sina.py` 的分层——
  `parse_trading_calendar`（不联网纯函数，能对已存 raw 重放）+ `fetch_trading_calendar`
  （联网薄函数）+ `refresh_trading_calendar`（抓取→原样落 raw→归一化进 fact 表，
  fetcher 可注入以离线测落库链）。`TradingCalendar.server_as_of = None`（F16：端点不带
  单一时刻，调用方用取回时刻当 as_of）。
- `skills/_store/db.py`：`save_trading_calendar`（只追加）+ `is_trading_day`（读最新一条，
  库/表不存在返回 `None` 不抛错）。
- `skills/_sources/tradetime.py`：`market_is_open` 加 `path` 参数、有日历数据以它为准、
  查不到回退 weekday（`session_in_progress` 不动）。
- 测试：`tests/test_tradetime.py`（21 条特征测试锁基线）+ `tests/test_szse.py`
  （解析/落库/日历感知/回退/免鉴权，P1/P2/P4/P5/P6）+ `tests/test_store.py`
  给 `fact_trading_calendar` 加只追加回归。

**🔴 探针记录**（G-1：每道新守卫先弄坏、见它红、再还原）：

- **P3 · `fact_trading_calendar` 只追加**：从 v15 迁移里删掉 `_append_only()` 调用，
  跑 `test_每张表都有只追加触发器` → 红：`AssertionError: 这些表可被改写：
  ['fact_trading_calendar']`（自动发现新表、要求它接触发器的判据真的生效）。补回 → 绿。
- **P4 · `market_is_open` 的日历查询是载荷代码**：把 `market_is_open` 临时改回
  weekday-only（保留 `is_trading_day` import 但不调用），跑 `test_元旦被判成休市` →
  红：`AssertionError: assert True is False`（喂了日历数据元旦仍报开市，证明日历分支
  不是摆设）。还原 → 绿。
- **解析完整性 fail-closed 是载荷代码**：临时删掉 `parse_trading_calendar` 的整月完整性
  校验，跑 `test_缺日抛错` / `test_多出别的月份的日期抛错` → 红：两条都
  `Failed: DID NOT RAISE SourceError`（残月/串月被静默放行）。还原 → 绿。
- 三次探针跑完 `grep -rn PROBE skills/` 确认工作区无残留。

### 🐛 修复 · 隔离自检漏认 `%h` 写法与 `.timer` 单元 —— 装 notify-worker 定时器时亲手撞到

`isolation.py::check_namespaces()` 判据是「引用了 BigA 路径的单元，名字必须带
`-biga`」，但实现只认字面绝对路径、只 glob `*.service`。装
`notify-worker-biga.{service,timer}`（见下一条）后拿它自查，报告只数到网关
那一个单元——查下去发现两处盲点：① 单元文件要进公开仓库，不能把真实家目录
硬编码进去，只能用 systemd 的 `%h` 写法，而检查只认 `str(BIGA)` 那个字面展开
路径；② 检查从不 glob `*.timer`，定时器单元完全不在扫描范围内。两处叠加的
后果不是报红，是**这道守卫对这一类单元完全失明**——不确认它没事，是根本
没看见它。改成 body 里出现字面路径或 `%h/.openclaw-biga` 任一即算引用，
候选文件同时 glob 两种后缀；新增 4 条测试，sabotage-revert 验证过两处
改动各自都会被抓到（错误信息与真实报告行为一致：判不了，不是通过）。
写下来是因为这正是 R-2 反复出现的那个模式——不是新红线，是同一条红线
第 N 次在新地方被撞到，见 CLAUDE.md 那张表。

### ✨ 新增 · `bin/biga-notify` + systemd 定时器 —— 给 `notify_worker.py` 接上调度方

对齐 `docs/external/2026-09-23-biga-minimal-feishu-design.md` §6/§13：投递
脚本本身（批 G-I）与真投递凭据（`FeishuDeliverer`，P6 live 修的那批）早就
都有了，缺的只是「谁、多久调一次它」——这个缺口在 TODO 里挂了很久，且
真会咬人：忘记 `--deliverer feishu` 就是「通知一直攒在 outbox 里」，P6 live
真跑时就撞过一次。`bin/biga-notify` 把默认值固化成真投递（仍可显式传参
覆盖回 stdout 桩排查），systemd timer 每 2 分钟跑一次（`Type=oneshot` +
`OnBootSec`/`OnUnitActiveSec`），已用配套的 `install_notify_timer.py --apply`
真实装上并 `enable --now`。装/卸载脚本不经 `bin/biga`（R-1 例外——纯 BigA
自己的 systemd 定时器，跟 OpenClaw 的 profile/gateway 完全无关，不存在可
转发的子命令）；单元名装前用 `check_r2()` 先查一遍 `-biga` 后缀，不等隔离
自检才发现（该守卫本身随即也在这批里补上了两处盲点，见上一条）。

### 🔧 变更 · 文档规约放过 `docs/external/` 下未跟踪的文件

`.gitignore` 已把 `/docs/external/*` 整体挡住（运营者的决策，2026-09-23：那目录
放本地参考材料，不打算提交）——但 `test_docs_convention.py` 的三条检查（文件名
规则、开头类别声明、日期前缀 + 只读标注）当时按磁盘扫描，不知道这条新策略，继续
对着这些永远不会提交的文件报错。

这与既有的"未跟踪文档要不要计入条数"是两条不同的判据，没合并：条数那条的前提是
"这份文档以后会提交，提交前查出来有价值"；`docs/external/` 新文件的前提反过来——
它们**没有**"以后会提交"的那一刻，"提交前该长什么样"这套约定对它们不适用，不是
放松检查，是检查的前提本身不成立。已经提交过的老文件（`2026-09-19-upstream-
source-design-v1.md`）不受影响，继续跟踪、继续受检——用 `git ls-files` 核对过
两者状态不同，不是猜的。

新增 `_is_untracked_external()`，复用已有的 `_tracked_docs()`（加了
`functools.lru_cache`，避免每条 parametrize case 各自 shell 一次 `git ls-files`）。
sabotage-revert 验证：把判据改回恒 `False` ⇒ 17 条失败原样重现；还原后全量测试
**首次在本次会话里真正全绿**（此前这 17 条一直是背景噪音，被反复误认成"已知的、
与当前改动无关的失败"）。

### 🐛 修复 · 批 G-II P6 live 真跑发现：飞书投递改走 `bin/biga message send`——不再需要 appId/appSecret

Card 完成、正确入队通知之后，第一次手动跑 `notify_worker.py --deliverer feishu`
报错缺 `BIGA_FEISHU_APPID`/`APPSECRET`/`OWNER_ID` 三个环境变量——`notify_worker.py`
至今没有调度方（已知缺口），意味着也没有"谁在正确环境里带着这三个凭据跑它"这件事
本身的答案。

而**网关那一刻已经在用一条真实、已认证的飞书连接**——它刚收到并处理了触发出卡的
那条消息。独立实现一套 HTTP 客户端 + 独立凭据是重复发明这条连接已经在做的事。
`openclaw` CLI 本就有 `message send --channel feishu --target <id> --message <text>`
（`bin/biga message send`），走网关自己的飞书通道——**不需要 appId/appSecret**，
这两个值从此不出现在这个类里。唯一还需要的是"发给谁"（`BIGA_FEISHU_OWNER_ID`，
一个标识符，不是凭据），且它本就有处可去（`channels.feishu.allowFrom[0]`，与出卡
触发的 owner 白名单同源）。

`FeishuDeliverer.deliver()` 改成 shell 一次 `bin/biga message send`（R-1：经
`bin/biga`，绝不裸 `openclaw`，与 `apply_config.py`/`inbound.py` 同一个
`_run_biga` idiom）；`tests/test_feishu_deliverer.py` 整批重写——旧版 `app_id`/
`app_secret` 假值随之删掉，不是漏测，是那两个参数已经不存在。

**验证不只是离线**：sabotage-revert（去掉 `--target` ⇒ 两条测试翻红；还原绿）
之后，真的手动跑通了一次——`bin/biga message send --dry-run` 先确认目标解析
正确，再真发：Feishu API 返回真实 `messageId` + `receipt`（不是本地 exit code
自证），**运营者在飞书里确认收到了**。这是这一批第一次有一条外发通知真的从
「代码认为发出去了」走到「人在飞书里看见了」。

⚠️ **同一时间收到一份外部材料**（`docs/external/2026-09-23-biga-openclaw-
feishu-delivery-fix.md`，本地参考、不提交）提出更完整的目标架构
（`DeliveryContext`/`NotificationAdapter`/`NotificationRouter`/账号与会话线程/
退避重试表）。核心原则与这次的修法一致（凭证属于 Channel，不属于 Agent/BigA）；
它顺带提的"worker 重启会不会重复发送"，查证 `undelivered_notifications()` 的
真实查询（`WHERE NOT EXISTS (...status='delivered')`）已经安全，不是缺口。
其余多渠道/多账号/会话线程部分是在给一个今天零消费方的通用通知系统打样（L-1）——
按裁定，先不建，留作以后"真的出现第二个渠道/第二个账号"那天的设计参考。

### 🐛 修复 · 批 G-II P6 live 真跑发现：预算闸门拒了它自己刚占的号

准备好 live 环境后真发了第一条飞书 `/card`。main 认出请求、跑了 `inbound.py`，
`accept_trigger` 正确占了号（`BIGA-20260923-003`）、正确拉起了 `systemd-run` 脱树
（entry_guard 判 HUMAN，journal 确认单元真的起来了）——到这里为止，这次返工要防的
东西全部兑现。但 `bin/biga-card` 自己的预算闸门（`check_budget()`）紧接着**拒绝了
这次运行**，报「距上次占号只有 0s」「还有 1 次运行没出卡」，两条理由都指向的是
`BIGA-20260923-003`——也就是它自己。

根因：预算闸门的「上次占号」查的是 `decision_ids` 表里最新一行。CLI 路径下，
`bin/biga-card` 先过闸门、后占号（orchestrator 自己占），闸门跑的时候号还不存在，
"最新一行"自然是**别的**、更早的尝试。飞书 inbound 路径反过来——`accept_trigger`
为了拿幂等键，**先**占号、**再**拉起 `bin/biga-card`；闸门跑的时候，"最新一行"
就是它自己刚占的那个。gap 恒为 0s，"还没出卡"也恒为真——**这条路径上，闸门 100%
会拒绝每一次触发，不是偶发**。这解释了为什么离线探针测不出来：P1/P3 的测试直接
调 `accept_trigger` 或桩掉 launcher，从没让`bin/biga-card` 自己的闸门在同一条真实
调用链上跑过。

修复：`check_budget()` 新增 `exclude_decision_id` 参数，排除掉「跟自己比」这一条
（`DAILY_CAP`——今天总共占了几个号——不受影响，这次自己确实算一次）；
`bin/biga-card` 把 `$BIGA_CARD_DECISION_ID`（飞书路径才有，CLI 路径本就没有）
传进去。`tests/test_budget_gate.py` 新增 `TestExcludeSelf` 四条：排除自己就不再
自比、不排除时（CLI 默认）行为一字不变、排除自己不连带放过同一天**别的**真实
占号、`DAILY_CAP` 不受排除影响。

**验证**：sabotage-revert 亲手复现——把排除逻辑改回"假装排除、实际不排除"
（`others = reserved`）⇒ 新增四条里两条当场翻红，报错文本与真实 live 事故完全同形；
还原绿。全量 1258 条测试（新增 4 条，`sync_test_count.sh` 已同步）+
`audit_public.sh` 十一项复跑仍绿。

⚠️ **这条比 MCP 路由那个坑更靠后、更隐蔽**：MCP 那个坑在"main 能不能碰到工具"
这一步就报错，錶面现象很显眼；这个坑要走到"main 已经成功发起、`accept_trigger`
已经成功占号、`systemd-run` 已经成功脱树"这么远，才在**下一个进程**里被同一套
闸门用**自己刚写的那一行**拒绝——四道离线探针（P1-P4）分别验证了各自的那一段，
没有一道探针把这几段串成一条真实调用链去跑，所以谁都没测出来。

### 🐛 修复 · 批 G-II P6 live 预检发现：config patch 省略 mcp 键不等于删除

给 P6 live 验证做准备时，把返工后的配置 apply 到真实 live，发现 `mcp.servers.
biga-card-trigger` 那条**没有消失**——它还指向一个这次返工已经从仓库删掉的脚本
（`skills/card/scripts/card_trigger_mcp.py`），对应的 stdio 子进程也还活着。

根因：`render_patch()` 不再往 patch 里写 `mcp` 键（因为不再注册 MCP server 了），
但 `config patch` 的合并语义是"patch 没提到的键原样保留"——**省略只是「不管」，
不是「删」**。任何 live 配置只要曾经 apply 过带 `mcp.servers` 的旧版 patch，这个键
就会一直留在那，指向的脚本删了也不会跟着消失。这是"只增量合并自己管的键"这条设计
本身的一个盲点：它保护了"不该碰的"，但没区分"不该碰的"和"曾经管过、现在不该再管的"。

修复：`render_patch()` 显式加 `"mcp": {"servers": {"biga-card-trigger": None}}`——
只删本脚本自己曾经写过的那一个键（不是整个 `mcp` 对象，给别的 MCP server 留位置），
用 `config patch` 自己文档化的"null 删"语义真正撤回它。`tests/test_apply_config.py`
的 `test_patch不注册mcp_server` 改名 `test_patch显式删掉曾经注册的mcp_server`，
断言从"不含 mcp 键"改成"含显式 null"——原断言在这次真复现之前会一直误报绿（不含
`mcp` 键 ≠ live 上那个键真的没了，两者在离线测试里看着一样，只有对着真实已污染的
live 配置才能分辨）。

**验证**：sabotage-revert 亲手复现——去掉 `"mcp"` 那行 ⇒ 新测试 `KeyError: 'mcp'`
翻红；还原绿。全量 1254 条测试 + `audit_public.sh` 十一项复跑仍绿。

### ✨ 新增 · 批 G-II —— Inbound Trigger：飞书出卡变结构化触发，main 只发起、编排脱树

批 G-I 让卡跑完能**推**回飞书。这一批做反方向、风险大得多的那半：让飞书能
**触发**出卡，但**出卡的编排绝不在 main 的会话进程树里跑**。它关掉的是 2026-09-21
两次事故（19:31 四孤儿 spawn、21:03 出卡递归 L-14）的共同根子——**出卡编排跑在了一个
agent 会话里，于是它能自己拼 `sessions_spawn`、还能递归再拉一次出卡**。

🔴 **这一批的立场在建造中变过一次，如实记下来**（详见教程第 37 章 §三/§四）：
最初照批 C-II 的激进立场做「**main 压根收不到这类请求**」——用 `command-dispatch: tool`
让 `/card` 绕过 model 直达一个 MCP 工具。**live 上走死了**：`command-dispatch` 在建
`toolSchema` 时够不到**会话内才连接**的 MCP stdio 工具（`docs/gateway/cli-backends.md`：
session-scoped，不 outlive the run），报 `Tool not available`；而 main 在会话里调同一个
工具反而算得对。加上运营者两个约束（**一个飞书机器人**做全部交互、**LLM 可用不追求
0 LLM**）——一个私聊里 `bindings` 按 peer 路由、没法按内容分流，于是退回到 BigA
全仓一致的形态：**`/card` 是普通技能，main 认出出卡请求 → 用 shell 跑 `inbound.py`**。
不变式从「main 全程不参与」诚实降级为「**main 只发起、编排绝不在 main 进程树里**」——
后者靠 `systemd-run` 脱树挡死（与谁发起无关），才是这批真正要防的东西。

**为什么这么设计**（几处不显然的裁定）：

- **出卡触发是纯技能（SKILL.md + shell 跑脚本），不用 MCP server。** BigA 全仓技能
  都是这个形态（`decision-card` / 各 `*-calc` / `news-scan` / `risk-check`）。曾为迁就
  command-dispatch 引入过一个 stdio MCP 工具 `biga_card_trigger`，但 command-dispatch
  够不到它（见上）、且与全仓形态不一致 ⇒ **整体删除**（连带删掉一版试过的「专用非-main
  agent + binding」，因单机器人单私聊无法按内容分流）。`/card` 回归成 `skills/card/SKILL.md`
  + `skills/card/scripts/inbound.py`：main 用 shell 跑 `inbound.py --origin feishu
  --trigger-id <event id>`，脚本秒回 ACK。
- **幂等键绑 `decision_ids` 不绑 `decision_runs`。** 身份模型是 Trigger→Decision→
  多个 Run。绑在每次尝试一行的 `decision_runs.trigger_id` 会**误伤将来的重试**
  （重试复用同一 trigger、会撞唯一约束）；绑在号分配器 `decision_ids`（每决策一行）
  正好——重试复用同一号、不重占 ⇒ 不撞约束，且占号本就是决策身份的原子仲裁点。
  **「先查 trigger 在不在、不在就占号」中间有竞态窗口——唯一约束才是唯一可靠的
  并发仲裁**（本仓库第三次用这句话，前两次在 `decision_ids`/`run_events`）。
- **异步用 `systemd-run` 把出卡拉出 agent 进程树。** 受理要快（立刻 ACK）、出卡要慢
  （后台 170~200s）。但命令工具在网关进程里跑，直接 fork 的 `bin/biga-card` 会继承
  运行时血缘/service env、被 `entry_guard` 判成 AGENT 拒掉。解法是**新增一条被允许
  的路径**（不改 entry_guard 已在拦的那条）：`systemd-run --user` 把它拉成瞬态
  systemd 单元 ⇒ 血缘变 `systemd --user`、无 service env ⇒ 判成 HUMAN——正是
  entry_guard 本就允许的「外部 Trigger」那一类。**不是绕过守卫，是走它本就留的门。**
- **人工 CLI 与飞书走同一个 `bin/biga-card`**（同五道守卫、同 orchestrator，不分叉
  两套决策逻辑）。异步只在 `inbound.py → bin/biga-card` 边界；`bin/biga-card` 内部
  照旧同步。人工 CLI 没有那三个透传环境变量 ⇒ `origin=cli` ⇒ **同步体验一字不变**。
- **`tools.deny:[ask_user]` 只给被 spawn 的非交互流水线 agent，main 不在名单。**
  半年前不能做（那时 main 同时是交互入口和出卡入口，禁它会连累飞书/TUI），批 C-II
  之后前提不成立。agent 名单**从 `_contract` 派生、不手写**（裁定 15 / dev-workflow
  第五问）：`discipline` 在 STAGE2 名单里但没建（裁定 13）⇒ 按「真有 AGENTS.md」
  过滤掉。**这条是设计探活点名必须补测的**——config 层的落点就是「main 不在 patch 里」。
- **`apply_config.py` 撞 R-2 但机关早在。** 一切经 `bin/biga`（强制 `--profile biga`、
  绝不裸 `openclaw`）、装 systemd 服务前拒绝任何非 `-biga` 单元名（`OPENCLAW_SYSTEMD_UNIT`
  env 覆盖是唯一能绕过推导的口子，堵上它）、装后由 `isolation.py::check_namespaces()`
  核对。`config patch` 递归合并 ⇒ 只碰它管的键（`tools.deny`/`commands.text`），
  **不碰 appSecret/gateway token/ownerAllowFrom 这些 live-only 值——仓库里一个凭据都
  不落**。

**建的东西**：

- schema **v14**：`decision_ids` 加 `trigger_id` 列 + partial unique index
  （`WHERE trigger_id IS NOT NULL`）。`_store.reserve_decision_for_trigger`（幂等
  占号，返回 `(decision_id, created)`）+ `find_run_by_trigger`（入站幂等查询侧）。
- `skills/card/scripts/inbound.py`：入站适配器 `accept_trigger`——幂等占号
  → 异步拉起（`detached_biga_card_launcher` 脱离进程树）→ 立刻 ACK。launcher 是依赖
  注入点（离线测桩）；带薄 CLI（`--origin/--trigger-id/--json`），SKILL.md 让 main 跑它。
- `skills/card/SKILL.md`：`/card` 技能（model-invocable）——指引 main 认出出卡请求后
  跑 `inbound.py`、原样转达 ACK、**不 spawn / 不自己编排 / 不等**。
- `skills/decision-card/scripts/feishu_deliverer.py`：`FeishuDeliverer` 实现批 G-I 的
  `Deliverer` 协议，接真飞书 API（HTTP 传输是注入点、凭据从环境变量读）。
  `notify_worker.py` 加 `--deliverer feishu`（默认仍是 stdout 桩）。
- `deploy/openclaw/`（新目录）：`agents.yaml` / `tool-policy.yaml` /
  `profile.template.json` / `apply_config.py`。
- `bin/biga-card`：认 `BIGA_CARD_ORIGIN/TRIGGER_ID/DECISION_ID` 三个环境变量透传给
  orchestrator（都没设 = 人工 CLI，同步路径不变）。`orchestrator.py` 加 `--decision-id`。

**探针（G-1，每道都亲手弄坏、见过红、已还原）**：

- **P1 幂等**：关掉 `reserve_decision_for_trigger` 的去重（跳过快查 + 插入不带
  trigger 标记）⇒ `created2` 变 `True`、adapter 拉起两次 ⇒ 幂等两条测试翻红。还原绿。
- **P2 编排脱树**：给 `detached_biga_card_launcher` 的 systemd-run 去掉 `--user`、
  或把回退路径清 `OPENCLAW_SERVICE_*` 那段删掉 ⇒ 两道结构测试翻红（不脱树/漏 service
  env ⇒ entry_guard 会判 AGENT）。还原绿。⚠️ P2 第一版用裸 substring 查「代码里不许
  出现 `sessions_spawn`」，结果抓到了 `inbound.py` docstring 里为解释「防的是什么」而
  提到的这个词（与「描述『不要写 X』别抄 X」同构）——改成 AST 查真实 import。
- **P3 异步**：给 `accept_trigger` 塞 `time.sleep(2)`（模拟同步等出卡）⇒
  「受理 < 1s」断言翻红。还原绿。
- **P4 R-2**：把 `check_r2` 的判据改成 `if False`（fail-open）⇒ 非 `-biga` 单元名
  不再被拒 ⇒ `DID NOT RAISE R2Violation` 翻红。还原绿。
- **P5 tools.deny**：把 `main` 塞进 deny patch（并关掉那句 `assert "main" not in`）⇒
  「main 不在 deny」断言在集合里查到 `'main'` ⇒ 翻红。还原绿。
- **P6 live**（真飞书 event → 卡 → 投递）：外部收尾，见下方「已知问题/待办」。

**探针记录里没有抄任何真值**：飞书 open_id / appId / appSecret / gateway token
一个都没进这份 CHANGELOG（公开仓库纪律——描述「不要写 X」的规则时不抄 X）。

**现状**：schema **v14**（仍是十张表；批 I 的 raw_text 列在 v13、本批 decision_ids.trigger_id 列在 v14，都是给既有表加列）、测试 **1254** 条、
教程 **37** 章。全部在独立 worktree（`wt-g-ii`，不含未跟踪文件干扰）里跑过。
### 变更（批 I）· raw 层真的存 raw —— 新增 `raw_text` 列，`content_sha256` 改基于原始响应文本（schema v13）

**为什么这是证据链最底层的问题**：raw 层此前存的**不是 raw**。链路是
`get_json()` → `json.loads` → 落盘时 `json.dumps(sort_keys=True)`。于是
`content_sha256`（`Evidence.raw_hash` 指向的那个指纹）算的是**我们自己重排后**
的字节，不是数据源发来的字节 —— 键序 / 空白 / 浮点表示 / 原始编码全部丢失，
上游改了序列化而没改数据，指纹看不出来。对一个卖点是「证据可追溯、可回放」
的系统，这是最不能含糊的一处。而 `schema.py` 的建表注释当时还写着「这里存的是
从数据源拿到的字节，**不做任何归一化**」——`sort_keys` 就是归一化，那句注释是假的
（L-3 的标准形状：注释断言了一件没发生的事）。

**做了什么**：

- `_sources/http.py` 加 `get_json_and_text()`，**同时**交出解析结果与原始响应
  文本；`get_json()` 收敛成它的薄封装。原文不再在 `json.loads` 之后就地丢弃。
- 四个适配器把原文一路带出来：`sina.IndexDaily` / `eastmoney.{Pool,Breadth,Board}Result`
  / `sina_news.NewsFeed` 各加 `raw_text` 字段；`tencent.fetch_index_quote` 改成
  返回 `(quotes, 原始响应体)`（它一次请求拿回所有代码，body 只此一份，而 raw 层
  里那份 `{code: 片段}` 是从 body 抠出再组装的**派生物**）。多页聚合的源（板块榜 /
  快讯）的 `raw_text` 是各页响应体的 **JSON 数组**，每个元素逐字节等于对应那次的
  响应体。
- `_store` schema **v13**：`raw_market_snapshot` 加 `raw_text` 列（`ALTER TABLE
  ADD COLUMN`，可空）。`db.raw_text_sha256()` 是 `content_sha256` 的新口径
  （`sha256(原文)`）。`save_raw_snapshot(raw_text=...)` 必填非空；六个采集调用方
  的 `Evidence.raw_hash` 一并改走 `raw_text_sha256`，与 raw 层同口径。
- **建表注释改回真话**：点名 `payload_json` 是解析后再 `sort_keys` 的规范表示
  （给回读用、归一化过的），`raw_text` 才是未归一化的原始文本、`content_sha256`
  基于它算。

**🔴 新增列、不替换 `payload_json` —— 设计探活救下的一处静默破坏**：普查发现
`_snapshot/coordinator.py` 的 `read_index_daily` 把 `load_raw_snapshot()["payload"]`
当**已解析对象**用（`len(raw)` / 切片）。如果把 `payload_json` 的语义直接换成原始
文本，这个消费方会拿到一个 `str`，`len()` 数的是字符数不是 K 线根数，**且不报错**
——正是本仓库最想防的「看起来正常、其实错了」。所以本批是**新增字段**：
`load_raw_snapshot()` 返回的 `payload` 继续是解析后的对象（coordinator 不用改），
原文另存 `raw_text`。这与 E-I 的 LegacyAdapter、K 的卡级冻结名单回退是同一个
「新增字段、旧读法继续成立」模式，不发明第四种写法。

**为什么旧行不迁移**：raw 层只追加（L-8）。v13 之前的行没有原文可填（那段文本在
`get_json` 内部早被丢弃、重建不出来），硬回填只能编。所以 `raw_text` 可空、旧行留
`NULL`、其 `content_sha256` 保持旧口径（`payload_sha256`，函数保留未删、由
`tests/fixtures/payload-sha256-vectors.json` 钉住）。**新行语义变了、旧行不受影响**
是 schema 演进的标准形状。`Evidence.raw_hash` 的文档补了一句：别拿跨 v13 的两个
`content_sha256` 直接比。

**探针（G-1）—— 每道守卫都亲手弄坏、见过红、再还原**（`tests/test_raw_artifact.py`）：

- **P1/P6 原始性**：把 `save_raw_snapshot` 的 INSERT 改成往 `raw_text` 列写重排后的
  `blob`。红：存下来的是 `[{"close":...}]`（sort_keys、无空白），与构造的带乱序
  key、多余空白的原始响应体逐字节不符（diff 直接把 bug 摆出来）。还原后绿。
- **P2 哈希基于原文**：把 `content_sha256` 改回 `payload_sha256(payload)`。红：两次
  「数据相同、键序不同」的响应产出**同一个** sha（`d8497d…` == `d8497d…`），
  `assert ha != hb` 失败。还原后绿。
- **P3 消费方不被破坏**：把 `load_raw_snapshot` 里的 `json.loads` 去掉，让 `payload`
  变成 `str`。红：探针 `isinstance(payload, list)` 失败，**且真实消费方**
  `test_snapshot` / `test_snapshot_wiring` 的冻结读取（`len`/切片）一并翻红 ——
  证明这条守的不是我自己写的断言，是 coordinator 那条真实路径。还原后绿。
- **P4 漏传即报错**：删掉 `raw_text` 非空校验。红：`raw_text=""` 时 `DID NOT RAISE`
  ——一个漏改的 collector 就能静默往新列塞空值。还原后绿。
- **P5 加列后仍只追加**：从 schema `_V1` 拿掉 `raw_market_snapshot` 的只追加触发器。
  红：`UPDATE ... SET raw_text=...` `DID NOT RAISE AppendOnlyViolation`。还原后绿
  ——确认 `ADD COLUMN` 没有意外绕开触发器，连新列本身的 UPDATE 也被拦。
- **P7 多页源按页保真**（自补，堵设计探活外我自己最担心的一处）：P1/P6 走的是**单次
  请求**的源；多页聚合的源（快讯 / 板块榜）`raw_text` 是各页 body 的 JSON 数组。把
  `fetch_feed` 的 `raw_text` 改成 `json.dumps(raw_pages)`（**解析后**的页而非原文页）。
  红：`json.loads(raw_text)` 取回的是 `{'result': {...}}` 解析对象，不是原始 body 字符串。
  还原后绿 —— 证明每一页的响应体都逐字节进了那个数组。

### 新增（批 I）· `tests/test_raw_artifact.py` —— raw 原始性的七道探针（+10 条）

对应上面 P1–P7，10 条测试。加上新教程章节带来的 4 条 `test_docs_convention`
参数化用例，批 I 自身的全量条数 **1170 → 1184**。

⚠️ **合回 orchestration 时的两处台账笔误，复核时改正**：CHANGELOG/教程原写测试
数 **1194**，worktree 干净跑出来的实际是 **1198**；三张验证用真卡引用的日期前缀
写错了两个（应为 `BIGA-20260921-025`/`-024`，不是 `-0922-`）。都已在复核合并时改正。

⚠️ **教程章节号与批 I 撞车**：批 I 与批 K 各自独立选中「第 35 章」，按落地先后
顺序处理——K 先落地保住 35（`35-agent-registry.md`），批 I 改记 **第 36 章**
（`36-raw-artifact.md`）。

⚠️ **schema 版本号与批 G-II 撞车（待 G-II 合回时处理）**：批 I 与批 G-II 都在各自
独立 worktree 里把新迁移记成 `_V13`——批 I 先合回 orchestration，保住 v13；
G-II 合回时需要把自己的迁移重编号为 v14（迁移 SQL 本体不动，只改版本标签），
按 J-I/J-II、F/G-I 已验证过的既定协议处理，这里先记一笔。

### 🔴 新增 · 批 K：Agent Registry —— roster 从五处收成一处（确定性编排设计文档 §6 批 K）

**为什么**：有一次真实的静默事故（2026-09-21）——`news` 进了契约的 Stage 1 名单、agent
也建好了，但运行时白名单漏了它 ⇒ 只 spawn 四个、**无任何报错**、Card 照常出只是少一个
领域；而 `risk` 如实报「Stage 1 缺席：news」，让排查方向天生指向 news 本身。当时的应对
是 `test_roster_matches_config.py` 做数据驱动**对账**——对，但那是对账、不是单一源。开工前
的设计探活普查发现 roster 实际散在**五处**，其中 `tools/verify/adapter_spike.py` 那处**零
测试覆盖**（不 import `_contract`、不在 pytest 下跑，对账根本看不到它）。这一批把它们
**结构性**收成一处：名册只手写一次，其余全部派生。

**做了什么**：

- **新增 `skills/_contract/registry.py`**：`AGENT_REGISTRY`（一个 agent 一条
  `AgentDefinition`：`stage`/`spawned`/`reads_snapshot`）。派生照抄 `run.py::RUN_STATES` 已
  验证过的 `vars()` 内省形状——`STAGE1_AGENTS`/`STAGE2_AGENTS`/`RISK_AGENT`/
  `SNAPSHOT_INDEX_AGENTS`/`EXPECTED_ROSTER` 全部从它派生，不再手写平行清单。字段**只装有
  消费方的**：没装外部示意稿里的 `required_datasets`（绑定被裁定表推迟的 Dataset Registry，
  装了就是 L-1 死配置）。`RISK_AGENT` 写成会 fail-closed 的纯函数 `_sole_spawned_stage2()`：
  「Stage 2 且 spawned」不是恰好一个就在 import 时 `RuntimeError`（今天恰好只有 risk；哪天
  `discipline` 上线成 spawned，这里当场炸，逼人想清楚谁是制衡层入口，而不是静默取第一个＝R-3）。
- **`verdict.py` 不再手写 `STAGE1_AGENTS`/`STAGE2_AGENTS`**，`_contract/__init__` 改从 registry
  re-export；`orchestrator.py` 删掉自己的 `RISK_AGENT`/`SNAPSHOT_INDEX_AGENTS` 两个独立字面量，
  改 import；`adapter_spike.py` 的 `STAGE1` 迁到 `STAGE1_AGENTS`（收编前的第五处、零覆盖那处）。
- **`DecisionCard.expected_roster`（卡级冻结名单）**：合成那一刻把当时 `EXPECTED_ROSTER`
  冻进 `card_json`，`absent_agents` 优先读它、只有老卡（字段不存在）才回退到读**今天**的
  Registry。堵的是一处静默漂移：`absent_agents` 原是 `@property`，现算现取**当下**名册，
  于是一张历史卡今天 `--show` 会用今天的 roster——若这期间 roster 变过，同一张卡的这个字段
  在不同时间给不同答案而 `card_json` 没变。**老卡不回填**（L-8）；**回放原样透传、不重算**
  （`replay.py` 把 `expected_roster=original.expected_roster` 传给 `synthesize()`，和
  `input_verdict_refs` 一样 ⇒ `comparable()` 两边恒等，`--check` 不误报组装不一致）。
- **`STANCE_VOCAB` 保持独立**（它是 stance 词表、不是 roster），只对 Registry 断言**子集**
  关系；`discipline` 在册（`STAGE2_AGENTS` 含它）但 `spawned=False` ⇒ 不进 `EXPECTED_ROSTER`
  ⇒ 永不被判「缺席」（裁定 13：没有输入源、从不 spawn）。
- **`architecture.md` §6.3** 加了 registry.py 一行（它是 `skills/_*/*.py` 入口，
  `test_每个入口都在设计文档里被提过` 要求点名并说清解决什么问题）。

**范围判断 · `test_roster_matches_config.py` 的 `skipif` 缺口（分发提示词留给建造会话定）**：
原来整个 `TestRosterConsistency` 挂 `@skipif(not CONFIG.exists())`，fresh clone / CI 上
**静默跳过整组**——连**不需要配置**的「契约名单 vs 已建 agent」都被一起跳过。R-3：算不出来
不该悄悄变成「没查出问题」。**收窄修**：① 不需配置的半边**照常跑**（CI 上也拦得住漂移）；
② 需配置的半边（读仓库外、人工维护的 `openclaw.json`）在配置缺席时发一条可见的
`RuntimeConfigUnavailable` 警告再 skip。**没**改成「配置不在就 fail」——CI 上本就不该有那份
机器专属配置，fail 是把环境差异误报成代码错误。

**探针记录（G-1：每道新守卫先弄坏、见红、还原）**——七道，全部见红且逐字节还原：

| 探针 | 怎么弄坏的 | 报红 |
|---|---|---|
| P1 派生一致性 | `technical.reads_snapshot` 关掉 | `FAILED …test_SNAPSHOT_INDEX_AGENTS等于旧frozenset` |
| P2 冻结名单 | 让 `absent_agents` 无视冻结字段、永远用今天的 Registry | `FAILED …TestP2FrozenRoster`（冻结卡跟着回退源变了） |
| P3 老卡兼容 | 去掉 `absent_agents` 的 `None` 兜底 | `FAILED …TestP3OldCardCompat`（老卡 `set(None)` 崩） |
| P4 adapter_spike 迁移 | `STAGE1` 改回独立字面量 `("market","sector")` | `FAILED …test_adapter_spike的STAGE1派生自contract` |
| P5 discipline 不进权威 | `EXPECTED_ROSTER` 改成收全部 agent（含 discipline） | `FAILED …TestP5DisciplineNeverAbsent` |
| P6 skipif 可见信号 | 配置缺席时静默 skip（删掉那条警告） | `FAILED …test_config缺席时发…警告而非静默skip` |
| RISK_AGENT fail-closed | `discipline` 改 `spawned=True`（两个 spawn 的 Stage 2） | `import _contract` 当场 `RuntimeError` |

两个探针**自身**的坑一并记下（探针也会有 bug，`dev-workflow` §3）：判据一开始写成小写
`"failed"` 而 pytest 打大写 `FAILED`，六道明明红的被误报「没红」；P1 第一版是「把 news 挪到
Stage 2」，结果先触发了 `RISK_AGENT` 的 fail-closed（import 炸），没验到 P1 自己声称验的东西
（L-13 形状）——改成「关 `technical.reads_snapshot`」才干净地报红。

**验收**：schema **不变**（批 K 是纯契约/派生，无迁移）；测试 **1198** 条全绿（在 worktree
干净 checkout 里跑，不含共享树里那批 gitignore 的外部材料）；`bin/biga-card --check` 拿三张
**批 K 之前**落库、`card_json` 里没有 `expected_roster` 的真卡（BIGA-20260922-001、
BIGA-20260921-025、BIGA-20260921-024）回放逐字段相同；`audit_public.sh --worktree` 十一项全绿。

⚠️ **过程记一笔（不是新问题，是复现的老形状）**：开工不久发现共享工作树里冒出不属于本批的
改动（另一会话在做批 G-II，动了 `_store/*` 与 `orchestrator.py`）。按第 28 章（批 C-III）
的先例，把本批挪进独立 `git worktree` 做，并先把已落在共享树上的自己那部分**逐个干净还原**
——尤其 `orchestrator.py` 那处 import 耦合着还没进共享树的 `registry.py`，不一起还原会让
共享树 `ImportError`、卡住对方会话。

### ✅ 复核 · 批 G-I 独立复核 + 合回 orchestration，schema v11 撞车按先例解决

批 G-I（外发通知 outbox）在独立 worktree（`.claude/worktrees/g-i`）里做完后，
独立复核了一遍，然后按既定顺序合回主线：**先把 orchestration（含已落地的
批 F）并进 g-i、解冲突、验绿，再把 g-i 合回 orchestration**——这条顺序不是
形式主义，是为了让冲突解决过程本身留在一个独立分支上可审查，而不是直接
在主线上现场解。

**复核做了什么**（不是读报告，是重新跑）：

- 通读 `_contract/notify.py`、`RunState` 的 diff、新增两张表的建表语句。
- 亲手把 `notification_outbox` 的只追加触发器关掉，跑 `TestP3AppendOnly`
  ——UPDATE/DELETE 两条测试当场翻红，还原后绿，证明触发器是真的在拦、
  不是"建了但没人测过它会不会拦"。
- 复制一份真实生产库 `data/biga.db`进 worktree，验证 v11→v12 迁移能在
  **已经被批 F 迁过一次的真实数据**上干净跑通（不只是空白测试夹具），
  `bin/biga-card --check` 迁移前后都过。

**合并冲突，按已有先例处理**（这个形状这批之前已经出现过两次：J-I/J-II
的 v9/v10，这次是第三次）：

- **schema 版本号撞车**：批 F 与批 G-I 在各自独立 worktree 里都把自己的新
  迁移记成 `_V11`（两边分叉时都看不到对方）。判据仍是"谁先合进
  orchestration 谁保留编号"——批 F 先落地（`e5b959f`），保住 v11；
  G-I 的两张新表改记 **v12**，只改版本标签，迁移 SQL 本体不动。
- **教程章节号撞车**（这次是新出现的一种，此前只在 schema 版本号上见过）：
  批 F 与批 G-I 各自独立选中了"第 33 章"。同样按落地顺序处理：F 的
  `33-risk-facts-into-orchestrator.md`已先落地，G-I 的章节
  `git mv` 成 `34-outbound-notifications.md`，连带修正文内自引用的
  "# 第 33 章"标题、两处 v11→v12 引用、以及一处已过时的"schema 撞车"
  描述段落（原文写的是"待解决"，此时已经解决，改成描述实际解法）。
- `card_ops.py::persist()`：两批各自独立在同一个函数尾部追加了逻辑
  （F 改了`.get()`取值风格并加了 spawn 过滤；G-I 加了通知入队块）。
  合并时保留 F 更简洁的内联`.get()`写法，G-I 的通知块整段保留，正确嵌进
  `if replay_of is None:`分支（回放路径不重复入队通知）。

**现状**：schema **v12**（十张表）、**1170** 条测试、教程 **34** 章。
合回 orchestration 后在独立 worktree（不含任何未跟踪文件干扰）里重新跑过
一次完整套件与 `audit_public.sh --worktree`，全绿。

⚠️ **一个容易误判的现象记在这里，免得下次又走一遍排查**：合并落地后在
**主工作区**直接跑全量测试会看到若干 `test_docs_convention.py` 失败——
原因是主工作区里躺着另一个并发会话产生的、`docs/external/` 下未跟踪的
研究材料（被 `.gitignore` 挡着，永远不会进仓库，但**在场就会被参数化测试
扫到**），与本次合并无关。这正是 CLAUDE.md「验收数字与徽章」一节警告过的
同一种失败形状的另一次出现：**判断"这次改动是否让套件变红"，必须在一个
不含无关未跟踪文件的环境里判断**（本次用了一次性 `git worktree`），不能
直接看主工作区当下跑不跑得绿。

**未决项**（批 G-I 自己在交付时披露的，不是本次复核新发现，已记入
`TODO.md`）：`notify_worker.py` 是 outbox 的真实读取方且测过（P4），但
**尚未接入任何调度**——没有 cron / systemd 定时去调它，靠人工起。判据是
"调度命令的字面量"，这条现在还不满足，留给 Phase 3 或批 G-II 顺带解决。

### 🔴 新增 · 批 G-I：外发通知 outbox（Outbound Only，确定性编排设计文档 §6 批 G）

**为什么**：出卡是一次 170~200 秒的**同步**调用，而「Card 完成 / UNKNOWN /
risk 否决 / 运行失败」这四类事件此前**没有任何外发通道** —— 人只能守着终端等
它跑完。这一批建一条「推」的通道：与 Card **同事务**入队一行 `notification_outbox`，
一个独立 worker 异步把它投出去。**只做推，不接受任何飞书方向的输入**（那是批 G-II：
异步执行、飞书 trigger、`main` 移出路由、`deploy/openclaw/` + R-2 —— 本批一概不碰，
没有新的攻击面）。

建的东西：

- `skills/_contract/notify.py`（契约层唯一一份）：`NOTIFICATION_EVENT_TYPES` 四类白名单
  + `card_event_type()`（把一张**已产出**的卡分到 `risk_block`/`card_unknown`/`card_completed`）
  + `NOTIFY_FAILURE_STATES`。分类判据放契约层的理由同 `RunState`/`RUN_ORIGINS`：
  `enqueue` 落库校验、`persist` 分类两处都 import 这一份，两份必漂。
  🔴 `risk_block` 判据是 `stance==VETO_STANCE` **不是 status 猜** —— `status='BLOCK'` 也
  可能是判官自己权衡的（无否决），而 veto stance 是契约里「制衡层否决」的唯一权威信号。
  🔴 `card_unknown` 判据取「任一 verdict UNKNOWN 或有缺失项」，**故意偏向报不确定**
  （R-3：UNKNOWN/缺失必须显式推出去，不藏在一个 `card_completed` 背后让人以为一切正常）。
- schema **v11** 两张只追加表：`notification_outbox`（幂等键 `(event_type, aggregate)`）
  + `notification_deliveries`（投递尝试日志）。
- `_store.db`：`save_card_with_notifications`（Card + outbox 同事务）、`enqueue_run_failed`、
  `record_delivery`、`undelivered_notifications`、`list_deliveries`；`save_card` 收成它的
  薄封装（不入队通知）。
- `RunState.NOTIFICATION_PENDING`（插在 `CARD_PERSISTED` 与 `COMPLETED` 之间）。原本推迟到
  批 G（无消费方），现在有了生产方（编排器入队）与消费方（`STATE_MEANING` + worker）。
  编排器走 `CARD_PERSISTED → NOTIFICATION_PENDING → COMPLETED`；`_fail()` 入队 run_failed；
  `run_ledger.cmd_move` 在终态转移时入队 run_failed（bash 侧的 TIMEOUT/CANCELLED）。
- `skills/decision-card/scripts/notify_worker.py`：outbox 的读取方，可替换投递接口
  `Deliverer` + 桩 `StdoutDeliverer`（真飞书 adapter 是批 G-II 的生产方，本批不接通真实 API）。

**🔴 一个需要判断的设计问题：outbox 要不要纯只追加？** 两条路都摆上台面：

- (A) **采用** · `notification_outbox` 纯只追加，投递状态另开 `notification_deliveries`
  追加日志，「投没投成」变成一条派生查询（deliveries 里有没有 `status='delivered'`）。
- (B) 放弃 · 给 outbox 开一列 `delivered_at`、投递成功 UPDATE 它一次。

选 **(A)**。理由不是「少写一张表也要忍」，是**本仓库的状态变更本来就这么记**：
`run_events` 就是先例（run 的当前状态不在 `decision_runs` 上原地 UPDATE，而是追加一行、
当前状态 = 最新一行）；`agent_verdicts` 的修订用 `amends` 指回原件、`decision_records`
的回放用 `replay_of` 指回原卡 —— **从不原地改状态**（L-8：一 UPDATE，「当时看到的」
就永久重建不出来了）。投递状态是同一形状的小状态机（pending→delivered/failed→重试再
failed…），append 日志天然记得下「第几次、结果如何、什么时候」，UPDATE 一列只留得下终值。
(B) 还要给 `test_每张表都有只追加触发器` 开一个它看不见的口子（schema.py 原话「例外必须
自己举手」）—— 拿一道有用的守卫换一列方便，不划算。⇒ (A) 与 `amends`/`replay_of`/`run_events`
先例一致，(B) 会引入本仓库**第一处被允许 UPDATE 的业务表**。

**同事务原子性（P1）**：card_* 通知与 Card 在 `save_card_with_notifications` 的**同一个
`connect()`** 里落库 —— 通知那步失败（非法 `event_type`、非严格 JSON）就整段回滚，
Card 也不落库，不会「卡进去了、通知没进去」。run_failed 反过来是**尽力而为**：在失败
终态转移**之后**入队（幂等 `aggregate=run_id`），通知入队失败绝不回滚「这次运行失败了」
这条记录 —— 分发提示词的同事务要求只对 Card+outbox，不对 run_failed。

**🔴 探针记录（G-1：每道新守卫先把被守的东西弄坏、确认报红、再还原）**：

| 探针 | 怎么弄坏的 | 报红输出 | 已还原 |
|---|---|---|---|
| P1 同事务 | `save_card_with_notifications` 在插卡后 `conn.commit()` 提前提交 | `assert load_online_card(...) is None` 失败：非法通知场景下卡仍被落库（`test_非法event_type_卡也不落库`/`test_payload非严格JSON` 两条同时红）| ✅ |
| P1 白名单 | 关掉 `event_type not in NOTIFICATION_EVENT_TYPES` 那道 raise | `Failed: DID NOT RAISE <class 'ValueError'>` —— 非法 event_type 被静默接受 | ✅ |
| P3 只追加 | 从 `_V11` 去掉 `notification_outbox` 的只追加触发器 | `test_outbox不能UPDATE`（UPDATE 成功、没抛 `AppendOnlyViolation`）+ `test_outbox不能DELETE` 同红；deliveries 两条仍绿（触发器还在）⇒ 探针有指向性 | ✅ |
| P2 幂等 | 去掉 INSERT 的 `ON CONFLICT(event_type, aggregate) DO NOTHING` | `sqlite3.IntegrityError: UNIQUE constraint failed: notification_outbox.event_type, notification_outbox.aggregate` —— 重复入队从无害 no-op 变成抛错（两条 P2 全红）| ✅ |
| P5 必经态 | 把 `(CARD_PERSISTED, COMPLETED)` 加回 `_ORCHESTRATED` | `Failed: DID NOT RAISE IllegalTransition` —— 跳过 NOTIFICATION_PENDING 直达 COMPLETED 又变合法 | ✅ |
| P4 worker | 让 `deliver_pending` 不再调 `deliverer.deliver` | 四类事件的投递调用集为空、与期望集不符（`test_四类事件都被投出` 红）| ✅ |
| run_failed | 关掉 `run_ledger.cmd_move` 里 `a.to in NOTIFY_FAILURE_STATES` 的入队 | `ValueError: not enough values to unpack (expected 1, got 0)` —— 终态转移后 outbox 空 | ✅ |

另外，新加的 `NOTIFICATION_PENDING` 由**既有守卫**兜底：不给它在 `run_ledger.STATE_MEANING`
里写一句话，`test_每个状态都有消费方` 当场红（「加了状态但没人读」）——L-1 的现成落点。

**✅ schema 版本号撞车，已解决**：本批开工时（HEAD=1824361）v11 是下一个空号；批 F
（当时未合并、另一棵工作树）也占用了 v11。批 F 先合并落地（`e5b959f`），合并
`orchestration` 进本批工作树时按 J-I/J-II 的先例重新编号：本批的两张新表改占 **v12**，
迁移体本身一字未动。`schema.py` 里两个版本号的注释都留了记号。

**明确不做**：飞书真实 API、异步执行模型、`deploy/openclaw/`、`main` 路由改动、
Preflight/Question Bridge、cron 调度 worker —— 全在批 G-II / Phase 3。

### 变更（批 F）· risk 的事实挪进编排器：确定性早退省一次 LLM 调用

**为什么**：`risk_check.py::build_fact_bundle()` 本来就是纯 Python、fail-closed、只给
事实不给结论。它唯一的问题是**位置** —— 跑在一个被 spawn 的 LLM 会话**内部**。老
路径为了触发这次「免费、快」的本地计算，先得花一次 LLM 调用（spawn risk → 它读提示词
→ exec 脚本 → 读 JSON → amend stance）。真正花钱耗时的是那两次 LLM turn，不是脚本本身。

这一批把 `build_fact_bundle()` 的调用从「risk 被 spawn 之后自己在 LLM 会话里跑」挪到
「编排器在决定要不要 spawn risk 之前，直接 `import` 它、免费地跑」。`run_id` 走批 J-I
已打通的 capture 路径（`save_fact_bundle(fb, run_id=ctx.run_id)`）。

- **两种确定性早退不再 spawn risk**：`build_fact_bundle` 的两条提前 return（`foreign`
  证据跨决策污染 / 完全没有上游）都产 `status='failed'`、`verdict='UNKNOWN'`，是**机械
  终局**——`missing` 已写清缺什么，stance 只能是「无法判定」，LLM 解读没有任何信息增量。
  判据是 `fb.status == 'failed'`（契约铁律钉死 `failed ⟹ verdict='UNKNOWN'`，而走到底那条
  永远是 `completed/partial`，所以这个判据不会误伤「覆盖不足但仍值得解读」）。编排器自己
  落库、直接拿 `verdict_ref` 参与合成，卡上 risk 呈现「给出事实、判定 UNKNOWN、无 stance」，
  **不是「缺席」**（`absent_agents` 单独管缺席）。
- **其余情况仍 spawn risk，但 risk 不再自己跑 skill**：编排器先落好 fact，`_risk_task`
  提示词整个换掉——「事实已算好，verdict_ref=NN，别重跑 skill，读它、按判断表给 stance、
  `amend_verdict.py --ref NN --stance <词>`」。提示词自包含（把该判断的字段全给它），
  免得它去 grep/find 找东西（那是实测踩过 62 秒 12 次工具调用的坑）。
- **`risk_check.py` 的 CLI 原样保留**（手工调试/人工复核仍要能独立跑），两条路径共用
  同一个 `build_fact_bundle`/`save_fact_bundle`，不发明第二套。
- **`agents/risk/AGENTS.md` 约束 1 改写**：从「怎么跑 `risk_check.py`」改成「事实已算好、
  你只解读」。**明确不留兜底自跑路径**——编排器永远先算好再 spawn，收到的 `verdict_ref`
  一定有效；真去自跑会撞下面那道守卫。这不跟着改就是 L-6（契约与实际行为对不上）。

### 修复（批 F）· 一个 (task_id, agent) 至多一份 fact —— 堵掉静默双写（schema v11）

**为什么**：`save_fact_bundle` 落 fact 行时 `amends` 恒为 `NULL`，而 v6 的
`ux_verdict_amends_linear` 只管 `WHERE amends IS NOT NULL` —— **fact 行天生在它管辖之外**。
于是对同一个 `(task_id, agent)` 第二次写 fact，两行都是 `amends=NULL`，唯一索引一条都拦
不住 ⇒ **静默产生两条并存的判定原件**。批 F 把 risk 的事实挪进编排器后，如果 risk 没听
新提示词、又自己跑一遍 `risk_check.py --task-id <同一个 did>`，正好触发这个双写：
`latest_verdict_ids()` 取 `MAX(verdict_id)` 会悄悄改用 risk 双跑那条，编排器预先算的那条
被架空——而「这次决策的 risk 事实原件是哪一条」从此有歧义。对一个卖点是「证据可追溯、
可回放」的系统，这最不能有。

- schema **v11** 加分区唯一索引 `ux_fact_per_task_agent ON agent_verdicts(task_id, agent)
  WHERE kind='fact'`，与 `ux_decision_online` / `ux_verdict_amends_linear` 同形（唯一约束
  由数据库兜底，不靠应用层「先查再插」）。assessment / 历史合体行不在 WHERE 内，不受影响。
- `save_fact_bundle` 接住 `IntegrityError` 翻成**指路的 `ValueError`**（不是裸
  `IntegrityError`）：说清「已经有一份 fact（verdict_ref=NN）」，指出手工复核加 `--no-store`。
  🔴 判据匹配的是**列名**（`agent_verdicts.task_id` + `agent_verdicts.agent`），不是索引名
  ——SQLite 的 `UNIQUE constraint failed` 报的是列不是索引（第一版判据落在索引名上，被探针
  当场抓到，这正是 L-13 的形状）。`risk_check.py` 的 CLI 也接住它、干净退出码 2。
- 🔴 **加索引前实测生产库**：`kind='fact'` 的行里没有任何 `(task_id, agent)` 重复
  （唯一 1 条 fact 行）——`CREATE UNIQUE INDEX` 在有重复时会直接报错，append-only 下没法
  事后清洗，所以必须先确认干净再加。

**G-1 探针（弄坏→报红→还原）**：

1. **守 A · v11 唯一索引**。从 `MIGRATIONS` 移除 `(11, _V11)`（索引不建）后跑
   `test_P4_CLI双跑同一did` → **FAILED**，stderr 实证 `verdict_ref=6` 后又 `verdict_ref=7`
   ——第二次 `risk_check` 静默产生了第二条并存 fact 原件（正是守卫要防的洞）。还原后绿。
   另有内联永久红灯 `test_P4_红灯_删掉唯一索引则第二次静默并存`（tmp 库里 `DROP INDEX`
   后两次 save 并存两条），证明索引 load-bearing。
2. **守 B · `status=='failed'` 不 spawn 判据**。把判据临时改成 `if False and …`（永远 spawn）
   后跑 P1/P2 → **FAILED**，报错 `编排在 RISK_RUNNING 失败：[risk] verdict='UNKNOWN' 却给出
   stance='放行'` ——揭示这道判据不只是省 spawn，还挡住了「对 UNKNOWN 事实强行要 stance」
   这个契约违规。还原后绿。

### 修复（批 F）· 执行账本只记真被 spawn 的 agent —— 修早退场景的 spawn_check 误判

**为什么**（这是设计文档没点名、批 F 引入的一处真实交互，独立发现并修掉）：早退不 spawn
risk，但 risk 仍在卡上 ⇒ `persist()` 原本会给 risk 写一行 `agent_runs`（执行账本）。而
risk 根本没被 spawn（没有 LLM turn、没花钱、运行时 `subagent_runs` 里没它）⇒
`tools/verify/spawn_check.py` 会看到「`agent_runs` 有行、`subagent_runs` 没有」，把 risk
判成 **forged（伪造）**，`bin/biga-card` 据此 `exit 4` —— 一张正确产出的卡被判失败。

给一个没执行过的 agent 记账本行，本身就是 **L-8 幽灵账本行**（记了没发生的事）。
⇒ `card_ops.persist()`：提供 `runtime_run_ids` 时，它的 **key 集**就是「本次真正被 spawn
的 agent」的权威名单，只给名单里的 agent 记账本行。判据是 key 在不在（不是 `.get()` 的
值——被 spawn 但没拿到 runtime id 的是「key 在、值 None」，仍要记账）。不提供该映射
（`synthesize.py` / 测试 / 回放）时维持原样。

### 行为变化（批 F）· 全员 Stage 1 缺席不再判「零证据 FAILED」，改出一张满是缺失的卡

因为 risk 现在**总会**落一条 fact（连「完全没有上游」都落一条无上游 UNKNOWN），编排器
Stage 3 的 `ordered` 至少有 risk 这一条。老行为（全员缺席 → 库里零 verdict → 「零证据
FAILED」）不再成立：现在出一张只有 risk「无上游」、满是缺失项的卡（「出一张标着不知道
的卡，比不出卡强」）。那道 `if not ordered` 检查退成纵深防御（契约层 `synthesize` 也拦
零 verdict，且抢在 synthesizer spawn 之前 fail-fast）。测试 `test_零证据则FAILED` 相应
改成 `test_全员stage1缺席_出无上游fact的卡_不再零证据FAIL`。

**VETO 回归底线**：这一批不改 risk 的判断实质，只改「事实从哪来」。P5（orchestrator 全
路径）验证：risk 给「否决」、判官给 BUY ⇒ 合成阶段照旧拒（否决从 `amend` 一路穿到
`DecisionCard`，一个环节都没断）；给 AVOID ⇒ 出卡、`risk.stance` 就是否决。

### ✅ 复核 · spawn-proof 四轮修复（最初 + 回合二/三/四），独立复现确认成立

下面四条（最初 `0a89ae0` → 回合二 `0c46a0e` → 回合三 `c061a60` → 回合四
`0a114c7`）独立复核了一遍——这条链本身已经比 J-I/J-II 更规范：最初那版
自己写明"未经独立评审会话复核"，后续三轮是真的独立评审、真的抓到前一轮
自己引入的回归（回合二推翻回合一两条判据、回合三抓到回合二的重复计数、
回合四抓到回合三丢证据），每轮都有具体数字支撑，不是空话。

本次独立复核（2026-09-23，另开会话，未参与这条链的建造）做了两件事：

1. **原样重跑 `orphan_spawns("20260921")`**，逐条核对 19:31:52 那批"招牌
   例子"——`market`/`technical`/`emotion` 报"无决策号"、`sector` 报"只带
   临时号 -000"，与回合四 commit 的 docstring 逐字对上，不是转述。
2. **亲手把回合四的修复（`_merge_text_by_run_id`）改回回合三的版本**
   （`_dedupe_by_run_id`），跑 `test_去重不能丢掉重复行携带的证据`——当场
   翻红，报错信息与回合四描述的回归症状完全一致（"证据在去重时被丢了——
   分类成了 '无决策号'"），还原后绿。

干净 `git clone` 全量测试绿，`audit_public.sh` 十一项绿。这条链没有未决项。

### 🔴 修复（评审回合四）· 去重顺手把一个诊断分支在真实数据上弄死了

回合三的去重本身是对的（54 → 28、零重复、钱对），但它**去掉的不只是重复的行，
还有那行携带的证据**。

两张表的文本字段**不是同一份文档**：

```
task_runs.task              「[Subagent Context] You are running as a subagent…」渲染后的提示词
subagent_runs.payload_json  「{"runId":…,"taskRunId":…}」spawn 载荷 JSON
```

实测：临时号 `-000` 在 `task_runs.task` 里出现 **0 次**，在
`subagent_runs.payload_json` 里 **3 次**。而去重永远留 `task_runs`（先读）
⇒ **「只带临时号 -000」这个分支再也不会亮**：09-21 实测 28 条全被归成
「无决策号」，其中有一条本该是「临时号」。

数量对、钱对，但读的人被**静默**送去了错误的诊断方向：
「无决策号」= 压根没带号；「只带临时号」= **占号晚于 spawn**，
也就是 L-11「身份晚于证据」—— schema v4 那整套机制专门要防的东西。
⚠️ `orphan_spawns()` 自己的 docstring 把这一行当成招牌例子，而那个例子一度跑不出来。

⇒ `_merge_text_by_run_id()`：同一个 `run_id` 的几行，扫决策号时扫它们文本的
**并集**，而不是只扫胜出那条。修复后 09-21 那批与 docstring 逐条对上
（market/technical/emotion「无决策号」、sector「只带临时号」），条数仍是 28、零重复。

🔴 **为什么两个调用点不能共用一个去重函数**：`_runtime_spawn_records()` 用
`_dedupe_by_run_id()` 是**安全的** —— 它两条查询都**先按决策号过滤、再去重**，
只在某一张表命中的行不会被丢。`orphan_spawns()` 是**按天取 → 去重 → 再分类**，
损失就发生在这个顺序差上。
⇒ 同一段逻辑在一个调用点正确、在另一个调用点有损，**差别不在代码，在调用点的
输入处于什么状态**。这是「漏改一个消费方」的变体，但更隐蔽：
**列消费方清单查不出来**，得问「这段代码在每个调用点拿到的是什么」。

### 🔴 修复（评审回合三）· 上一条的前提说反了，导致孤儿 spawn 重复计数

回合二评审确认三条必改都真改掉了（判据两半各自被独立钉住、读失败不再变成对真卡
的指控、`orphan_spawns()` 同源），但抓到上一条**自己引入的新回归**：

**前提说反了。** 上一条写「运行时把一次 spawn 记在哪张表不是恒定的，两张都可能
是落点」。实测 **`subagent_runs ⊂ task_runs`**：`subagent_runs` 去重后 67 个
`run_id`，**67 个全在 `task_runs` 里，0 个独有**。不是「落点会变」，是
`task_runs` 记全部、`subagent_runs` 只记子集。原来只读一张确实是盲区，
但成因是「只看了子集」，不是「看错了表」。

**那句话就是 bug 的根**：按它写代码就把两张表直接拼接，而同一次 spawn 两边各有
一行。实测 `budget_report.py 20260921` 打出「孤儿 spawn **54 个**（约 \$5.40~\$8.10）」，
其中 25 条逐字重复 —— 而这是个**报钱数的告警**，最容易让人下次直接忽略这个信号。
修复后 **28 个（约 \$2.80~\$4.20）**，零重复条目。

⇒ 取数处按 `run_id` 去重（`_dedupe_by_run_id`），顺序改成**先读全集 `task_runs`**
（它有显式 `agent_id` 列，比从 session key 抠可靠）、`subagent_runs` 只补老库。
去重与归一化**在同一处做一次** —— 沿用本仓库已立的那条原则。
`run_id` 为空的行不参与去重（宁可重复，也不把两条不同记录合成一条）。

**顺带两条**：`check_1_spawned()` 的三句话还停在只说 `subagent_runs`（同一个
`SpawnProof` 两个消费方，上一条只改了 `spawn_check.py` 那个 —— 与它自己刚修的
`orphan_spawns()` 是同一个形状，只是这次无害），现已接上 `by_source`；
`field` 重复导入（`field as dc_field, field`）去掉。

**一条残留写进 docstring**：这套判据证明的是「**被 spawn 了**」，不是标题写的
「被 **Supervisor** spawn 了」—— 全程不看 `requester`。今天两者等价靠的是**配置**
不是判据（七个 specialist 的 `subagents.allowAgents` 都是 `[]`，运行时
`resolveSubagentTargetPolicy` 读的正是发起方自己那份名单；真实库里
「specialist 起了别的 agent」0 条）。哪天给某个 specialist 开了 `allowAgents`，
「A 起了 B」就会被算成「Supervisor 起了 B」且不报错。
🔴 不要据此加 requester 判据：实测有 4 行真 spawn 的 requester 等于 child（自指），
加了会误杀。

⏭ **未做（评审建议另开一批）**：孤儿告警**分组显示**（生产 `orchestrator-<32位裸
hex>` vs spike/测试的带标签会话）。评审实测那 15 条孤儿里，会话名自己就带着标签，
区分是**按次的不是按天的** ⇒ 不需要「验证日」这种新口径。主张分组而非过滤：
过滤是静音开关，而验证日恰恰是最容易跑出真孤儿的那天。

### 🔴 修复（评审回合二）· 上一条的判据被实证绕过；同一个根还漏了第二个消费方

独立评审对下面那条做了三件事，两件推翻了它的判据：

**① 「排除 `task_kind='exec'`」是排除列表，实测能绕过。** 评审去读了已安装的
运行时源码（不是数据库）：`taskKind` 是**自由文本开放列**（无枚举无校验），
已发货的取值不止三个（`exec` / `automation_run` /
`context_engine_turn_maintenance` / `<label>_generation`）。更要命的是
`resolveTaskAgentId()` 会回退到**发起者自己的会话** ⇒ 任何在 agent X 会话里
建出来的 task 行，缺省就带 `agent_id = X`。

⇒ 造六行 `task_kind='image_generation'`、`agent_id` 是 specialist 本人、决策号
写在它自己可控的文本里，`check_1_spawned` 报 **PASS「6 个 agent 两份记录都齐」**，
而真实 spawn **零次** —— 正是外部评审 F3 要抓的形状，核验放行了它。
（本次复核独立复现过这个伪造，不是转述。）

⇒ 改成**肯定式**判据（`_REAL_SPAWN_SQL`），不枚举 kind：子会话的 agent 段必须
等于 `agent_id`，且 `run_id` 必须是**裸 UUID**（真 spawn 没有命名空间前缀，
工具/定时/后台执行都带 `tool:` / `cron:` / `exec:`）。实测全表三种已知 kind 被
run_id 命名空间完整分开；对 `BIGA-20260922-001` 新旧判据都是 12 行，零回归。

⚠️ **上一轮的探针钉不住这件事**：仿件里 `task_kind` 与 `run_id` 绑死，把实现换成
`run_id NOT LIKE 'exec:%'` 也全绿 —— 它钉的是实例不是判据。新探针专门造了一个
**第四种 kind**，退回排除列表当场报红。

**② per-table 的 `except sqlite3.Error: pass` 把「表不存在」和「读失败」混成一种。**
评审实测：上游把 `task` 列改名之后，真卡的结论不是「判不了」而是
**FAIL「这个号从未被 spawn 过」** —— 一次读失败变成**对一张真卡的指控**，
比退化成 UNKNOWN 坏一档，且与函数自己的 docstring 冲突。
⇒ 三态分清：两张都不在 ⇒ 判不了；在的那张读炸 ⇒ 判不了；只有一张在 ⇒ 正常用它。
口径照 `skills/_store/runtime.py::read_task_runs()`（先查 `sqlite_master`）——
同一个仓库里早就有正确写法，只是没被复用。

**③ 「通过是因为什么都没查」——同一个根上漏了第二个消费方。**
`orphan_spawns()` 仍然只读 `subagent_runs`。实测三天对照：

```
20260921  subagent_runs 51 行 | task_runs 真 spawn 100 行
20260922  subagent_runs  0 行 | task_runs 真 spawn  27 行
20260923  subagent_runs  1 行 | task_runs 真 spawn   1 行
```

09-22 那天 `budget_report.py` 打出「✅ 无孤儿 spawn」—— **那个 ✅ 是读一张空表
读出来的**，当天有 27 次真 spawn。而且它返回 `[]`（干净）不是 `None`（判不了），
连 R-3 都没保护到。
⇒ 改成与 `_runtime_spawn_records()` **同源**，共用同一份 `_REAL_SPAWN_SQL`。
修完之后 09-22 从「无孤儿」变成 **15 条**（都是验证/spike 会话的 spawn：
真花了钱、真没进卡，按这个检查的定义就是孤儿）。

⚠️ 这条不是上一版引入的，但上一版的**全部论证**就是「运行时记在哪张表不是
恒定的」—— 证完之后只修了两个消费方里的一个，正是 L-3：同一个事实两套口径，
而其中一套已经被自己证明是错的。

**④ 顺带**：`SpawnProof` 加 `by_source`（给 `source` 字段一个真消费方，L-1），
报错从只说 `subagent_runs` 改成说出实际读到的来源分布；仿件那句「字段形状照抄
真库」改准（只建查询用得到的 7 列，真库 33 列）。

### 🔴 修复 · `spawn_check` 对编排器产出的真卡「判不了」—— 运行时记录不只在一张表

**症状**：`BIGA-20260922-001`（至今唯一一张编排器真实产出的卡）跑 `spawn_check.py`
报「判不了」(exit 2)。一张真卡、七次真 spawn，核验却给不出结论。

**根因**：`spawn_proof()` 只读运行时的 `subagent_runs`。实测对照同一个决策号：

```
subagent_runs                      0 行
task_runs（排除 task_kind='exec'） 12 行   ← market/sector/news/technical/emotion + risk + synthesizer
```

运行时把一次 spawn 记在哪张表**不是恒定的**。另一个会话 2026-09-23 走同一条
Adapter 代码路径重新 spawn 时，`subagent_runs` 确实进了行 —— 两张表都可能是
落点，只读一张就在另一张那侧变成盲区。

⚠️ 这条**也是批 J-II 结构化 join 的地基**：join 的右表取自这份记录，地基是空的
时候，join 再硬也命中不了。J-II 的实现没问题，它忠实地接在了一条本就断掉的判据上。

**修法**：两张表都读，归一成 `{run_id, agent, source}`。
- `subagent_runs` 的 agent 从 `child_session_key` 第二段抠；`task_runs` 有现成的
  `agent_id` 列。归一化**只在取数处做一次**，消费端只比 `rec["agent"]`（不各解析
  一遍，那是第二套口径 L-3）。
- 🔴 排除 `task_kind='exec'`：那是 Specialist 自己在会话里跑 shell（`run_id` 形如
  `exec:<名>`、requester 是它自己的子会话），不是「被 spawn 起来」。不排掉的话，
  一个只跑过 exec、从未被 spawn 的 agent 会被判成 spawn 过 —— 正是这套核验要抓的
  伪造形状。
- 🔴 两张表**各自**容错：少一张不等于读不到。第一版把两条查询写在同一个 `try`
  里，结果运行时库缺 `task_runs` 就整个 `return None` ⇒ 退化成「判不了」，而
  「判不了」是会被忽略的。**现有测试当场抓到了这个**（仿件库只建了 `subagent_runs`，
  四条老测试翻红）。只有两张都取不到才是真的读不到（R-3）。

**探针**（弄坏→红→还原，G-1）：
- P1 去掉 `task_runs` 这个来源 ⇒ 「只有 task_runs 时也认得出 spawn」与
  「exec 行不算被 spawn」双红。
- P2 去掉 exec 过滤 ⇒ 只有 exec 行的 agent 被判成 spawn 过，报红。
- P3 把「两张都取不到才 None」改成永远返回列表 ⇒ 「零条记录」会被判成伪造而不是
  判不了，报红。
- 真实数据验证：修复前 `_runtime_spawn_records('BIGA-20260922-001')` 拿到 **0 条**，
  修复后 **12 条**、7 个 agent 全认出、0 条 exec 混入。

⚠️ **端到端仍未闭环**：那张卡的 `agent_runs` 侧是空的（它早于「persist 补记账本」
那次修复），所以 `spawn_check` 现在仍报「判不了」——但运行时侧已经从 0 条变成 12 条。
**下一张真实卡会免费给出完整结论**，不需要为此单独花钱 spawn。

### ✅ 复核补验 · 批 J-I 的 `--run-id` 提示词机制，live 确认可靠

下面这条修正记录了"`--run-id` 靠提示词指令传递、LLM 是否真的会带上它未被 live
验证"。授权后（"跑吧"）做了一次真实 spawn 去确认。

走真正的 `OpenClawRuntimeAdapter.attach()`/`start()`（与 `orchestrator.py` 起
Specialist 同一条代码路径），任务文本**一字不差**照抄 `_specialist_task()` 的
真实文案，只在末尾加一句要求 `--no-store`（避免真的写进生产账本，这是唯一
刻意偏离）。

不满足于 `sessions tail` 那种会脱敏 exec 参数的人类可读视图，直接查
`~/.openclaw-biga/agents/emotion/agent/openclaw-agent.sqlite` 的
`transcript_events` 原始表，拿到 LLM 真实发出的 `exec` 工具调用逐字内容：

```
python3 skills/emotion-calc/scripts/emotion_calc.py --task-id BIGA-VERIFYNOOP-001
  --run-id b7c1a2e9d3f4a5b6c7d8e9f0a1b2c3d4 --no-store
```

`--run-id` 确实被带上了，值与提示词里给的完全一致——与 `--task-id` 同一种
机制同样可靠，不是假设。命令本身因为验证用的 `task_id` 故意不合法
（`BIGA-VERIFYNOOP-001` 不满足 `BIGA-YYYYMMDD-NNN`）而报错退出，这是预期内、
与 `--run-id` 无关的副作用——`FactBundle` 的契约校验正常拦截，没有任何数据
落库。成本 $0.097（123 input / 200 output tokens）。

批 J-I 至此没有未决项。

### ✅ 复核 · 批 J-I 独立复核 —— 机制成立，一条假设未 live 验证

下面这条批 J-I 的条目自称"评审复核通过"。独立复核（2026-09-23，另开会话，
未参与建造）动手验了其中最安全相关的一处：2b 裁定（`save_assessment` 的
`run_id` 只能从被 amends 的 fact 行继承，Agent 在命令行上够不到）。

把 `skills/_store/db.py` 里 `inherited_run_id = meta["run_id"]` 这行改成硬编码
字符串，`tests/test_run_id_capture.py::TestInheritRunId` 的两条行为证明测试
当场翻红——值对不上、以及"fact 无 run_id 时 assessment 也该是 None"两个场景
都报错，还原后绿。commit 信息里"关掉继承当场报红"的描述复现成立。另外确认了
`amend_verdict.py` 里确实没有任何 `run_id`/`run-id` 字样——CLI 层面 Agent 连
尝试传都传不了。干净 `git clone` 全量 1118 条绿，`audit_public.sh` 十一项绿。

**一条假设本批和这次复核都没有像 J-II 那样做 live 验证**：`--run-id` 走的是
提示词指令（"跑你的 skill 时必须加 --task-id … --run-id …"），与 `--task-id`
同一机制——LLM 是否真的会在真实 spawn 里带上它，线下测不出来，只能靠真实
spawn 确认。检查生产库 `data/biga.db`：自 J-I 合并以来一次真实卡都没跑过
（`user_version` 仍是 9，J-I 需要的 v10 列不存在），没有现成证据可查。

风险等级比 J-II 那次低：不带就是 `None`（capture 不 enforce，到处都按"可能没有"
处理，不是静默错值），且复用的是 `--task-id` 已经在生产路径上长期验证过的
同一种机制，不是全新假设。是否要照 J-II 的先例、花一次可忽略的小成本真跑一次
去确认，留给下一步决定，这里先如实记录"尚未验证"。

### 🔴 新增 · 批 J-I：`run_id` capture 贯穿全链（EvidenceSet / Verdict / Card 绑定 Run）

设计文档 §2 追加 5.1；总体设计 §43 短期重点第 2、3 条。**实现完成、离线全绿
（1101→1112 条），四道探针 P1–P4 各自弄坏见红并已还原，但未交独立评审 —— 不自宣
通过**（开工与评审分不同会话）。schema v9→**v10**。

**只做 capture，不做 enforce**：让 `run_id` 能被存下来、传下去，**不改任何现有的判定/
过滤逻辑**。`latest_verdict_ids()` 仍按 `decision_id` 聚合 —— 按 `run_id` 过滤（拒绝跨
run 串读）要等真正的重试路径出现才做。**为什么现在只做一半**：追加 5.1 复盘发现，
「把 run_id 存下来」一直有消费方（它从批 B 就存在，这里只是让 `agent_verdicts` /
`evidence_sets` 等**已经在写别的字段**的表顺手多存一列），而「按 run_id 强制校验」没有
消费方（没有重试路径就无意义）。现在是最便宜的时机：批 E 系列刚在这一层做完迁移，
晚一步就要在同一层再开一次刀。

**做了什么**（v10 迁移给 `agent_verdicts` / `evidence_sets` 各加一列 `run_id TEXT`，都
nullable、不回填）：

- **六个 skill 各加可选 `--run-id`**（照抄 `--evidence-set-id` 的 CLI 模式），落进
  `save_fact_bundle(fb, run_id=…)` → `agent_verdicts.run_id`。⚠️ E-III 之后六个**全部**
  走 `save_fact_bundle` 一条路径（不是旧提示词说的「risk 走 save_verdict，两条路」）。
- **🔴 2b：`save_assessment` 从被 amends 的 fact 行「继承」run_id，不加 CLI 参数。**
  事实行由 skill 写，判断行由 Agent 经 `amend_verdict.py --ref <fact_id> --stance …` 写 ——
  它天然带着 `amends`→fact 行的指针。⇒ `save_assessment` 从 `load_verdict_meta(fact_id)`
  取回那行的 `run_id` 直接用。理由不是省事：Agent 手传就可能传错，而「继承」在结构上
  不可能与事实行不一致 —— **判据别建在可篡改的输入上**。
- **`orchestrator._specialist_task` / `_risk_task` 给每一个 agent 的任务文本带
  `--run-id {ctx.run_id}`**（六个都产落库记录，不只读冻结快照那三个）。与 `--task-id`
  同机制：提示词说要加，跑完靠探针核实（capture 不 enforce）。
- **`freeze_index_daily` / `save_evidence_set` 加 run_id**，编排器把 `ctx.run_id` 直接传进去
  （Python 内部调用，不经 CLI）。`decision_runs` 早有 `evidence_set_id` 反向指针（run→set），
  这一列是 set→run，直接、不用 join。
- **`VerdictRef` / `DecisionCard` 各加 `run_id: str | None = None`**：`VerdictRef` 从存量行
  的 `run_id` 列**直接搬**（`load_verdicts_and_refs` / `synthesize.py` 两处构造点都改），
  `DecisionCard` 由 `card_ops.synthesize(run_id=ctx.run_id)` 填。两处 `from_dict` 都用
  `.get("run_id")` —— 历史卡 JSON 没这个键，缺省 None，不是 `KeyError`。
- **🔴 `comparable()` 把 `run_id` 与 `generated_at`/`elapsed_ms` 一同剥掉**：回放**不是**
  原来那次执行尝试，它诚实地把 `card.run_id` 记成 None（不捏造），而原卡带着真实 run_id。
  不剥的话，一旦在线路径开始产出带 run_id 的卡，回放它们的 `--check` 就会因「这次执行 ≠
  上次执行」误报「组装不一致」。⚠️ `input_verdict_refs` 里各 ref 自带的 run_id **不剥** ——
  那是判定原件的血缘，回放照原样带过去，两边相同，是要被核对的证据。

**「继承」为什么在结构上不可能与事实行不一致（交接问题 4）**：两条独立证据 ——
① `inspect.signature(save_assessment)` 里**没有** `run_id` 参数，`amend_verdict.py` 的
argparse 里也没有 `--run-id`（实测 grep 过）⇒ Agent 在命令行上根本够不到这个值；
② run_id 的唯一来源是 `meta`（按 `fact_id` 取回、且已校验 `kind=='fact'` 且同
`(task_id, agent)` 的那一行）⇒ 存进去的 `assessment.run_id` 永远逐字节等于 `fact.run_id`
（fact 有值继承有值、fact 是 None 继承 None）。两条都固化成测试（`TestInheritRunId`）。

**探针记录（G-1：每道探针先弄坏、见红、还原）**：

- **P1 · 在线落库真的存进去了**：把 `save_fact_bundle` 的 INSERT 里 run_id 硬改成 None ⇒
  `assert None == 'cd5af379…'` 报红。另 `evidence_sets.run_id` 同样落库并取回比对。
- **P2 · 没有 run_id 的历史行读得回、不被拒**：把 `DecisionCard.from_dict` 的 `.get("run_id")`
  改成必填 `d["run_id"]` ⇒ 历史卡 JSON（无此键）当场 `KeyError: 'run_id'` 报红。还原后
  `load_verdict`/`load_outcome`/`load_verdict_meta`/`verify_verdict_refs` 对 NULL 行全部不报错。
- **P3 · 合成卡与 ref 追到同一个 ctx.run_id**：把 `synthesize` 里传给 `DecisionCard` 的
  `run_id=run_id` 改成 `None` ⇒ `card.run_id` 断链为 None、报红（而 VerdictRef 仍带 run_id，
  破坏点精确）。
- **P4 · 回放不捏造 run_id**：把 `comparable()` 的 `d.pop("run_id")` 注释掉 ⇒ 回放带 run_id
  的卡 `{'run_id': 'cd5af379…'} != {'run_id': None}` 误判不一致、报红（而「回放历史卡 run_id
  保持 None」那条仍绿，破坏点精确）。

**最可能被攻破的一处（交评审重点看）**：run_id 落进 `agent_verdicts` 的那一步，依赖
**Agent 真的在跑 skill 时带上了 `--run-id`**。离线探针只证明了管线通（skill 接得住、存得进、
传得出），没证明**live 的 Agent 确实照提示词加了这个参数** —— 与 `--task-id`/`--evidence-set-id`
是同一种「提示词说要加、靠探针核实真加了」的机制，而核实那一步需要一次 live 端到端
（本批未做，属 capture 的固有性质：不 enforce，漏了就是 None，不报错）。相比之下
`evidence_sets.run_id`（编排器 Python 内部传 `ctx.run_id`，已由
`test_JI_evidence_set落库带本次run_id` 端到端钉住）与 `save_assessment` 的继承（结构强制）
不依赖 LLM 顺从，是可靠的；唯独 **fact 行这一列的捕获率取决于 Agent 是否照提示词加
`--run-id`** —— 这是本批最软的一环，也是「capture 不 enforce」的固有性质（漏了是 None，
不报错、不阻断出卡）。真正堵死它要等 enforce 那一批（重试路径出现后按 run_id 强制校验）。

### ✅ 复核补验 · 批 J-II 的命名空间共享结论，独立复现确认成立

下面这条修正记录了「原 live 补验的具体断言复核复现不出来」。用户授权后
（"真正测试一遍吧"），独立复核补做了一次**真正走生产路径**的重新验证，而不是
继续读第三方运行时的压缩源码猜测。

区别于上一次：这次直接调用 `skills/_runtime/adapter.py::OpenClawRuntimeAdapter
.attach()` + `.start()`——与 `orchestrator.py` 起 Specialist **完全同一条代码
路径**（`attach()` 内部走 `bin/biga attach --print-config` 铸 grant，R-1 合规），
而不是绕开 Adapter 直接调 MCP 工具。任务文本沿用同一个最小成本模板（「只回复
两个字：收到，不要运行任何 skill」），实际花费 123 input / 5 output tokens。

结果：`adapter.start()` 返回 `runtime_run_id='17ace698-0754-4d91-ab2c-35f34ca59610'`；
立即查 `subagent_runs WHERE run_id = '17ace698-…'`，**命中 1 行**，`child_session_key`
与 `SpawnHandle.session_key` 逐字一致。⇒ **`runtime_run_id` 与 `subagent_runs.run_id`
确为同一命名空间，J2-3 结构化 join 的前提成立**——这次是一次干净、可复现、
走真实代码路径的实测，不是转述。

对上一次复现不出来的最合理解释：上一次的「live 补验」大概率不是经
`OpenClawRuntimeAdapter.attach()` 起的（那次 `task_runs` 行没有 `collect`/
`expectsCompletionMessage` 等 Adapter 固定会带的字段，`delivery_status` 也是
`not_applicable`，形状与这次、以及两条真实历史生产行都不一样）——即绕开了
Adapter 直接调 `sessions_spawn`，测的是另一条路径，不代表 BigA 实际生产会
经过的路径。**不是「结论是错的」，是「上次验证的不是这条路径」。**

J2-3 现在有了两件独立证据都成立：机制本身（P1–P5，见下方原条目）+ 走真实
Adapter 路径的命名空间实测。批 J-II 可以视为完全验证过关，不再有未决项。

### 🔴 修正 · 批 J-II「live 补验」的具体断言，独立复核复现不出来

下面这条批 J-II 的条目写着「查运行时库 `subagent_runs WHERE run_id='e5c00e01-…'`
查得到」，作为「`runtime_run_id` 与 `subagent_runs.run_id` 同一命名空间」这条
结论的现场证据。

**独立复核**（2026-09-23，另开会话，未参与 J-II 建造）原样重跑了同一条查询——
`~/.openclaw-biga/state/openclaw.sqlite`（`BIGA_RUNTIME_DB` 的默认路径，与
`phase1_acceptance.py::_runtime_spawn_records()` 用的是同一个文件）：

```sql
SELECT * FROM subagent_runs WHERE run_id = 'e5c00e01-4992-4634-9d2b-18346ff464b0'
```

返回 **0 行**。按 run_id 精确匹配、按 `child_session_key` 里的 `a4ee178b` 片段、
按 `payload_json` 子串三种方式都搜不到；该表在这次 spawn 之后完全没有新行
（最新一行仍是 2026-09-21 19:35，此后 66 行没变过，而这次复核发生在
2026-09-23，其间数据库其他表一直在正常写入，不是库被闲置或轮转过）。

同时能确认这次 spawn **真的发生过**——`task_runs` 表里有一条完全匹配的记录
（`agent_id=emotion`，`requester_session_key` 带 `orchestrator-jii-nscheck`
字样，任务文本正是「只回复两个字：收到。不要运行任何 skill，不要采集数据」），
只是它没有在 `subagent_runs` 里留下对应行。

不确定这是「当时查的是另一个库 / 另一个 profile」，还是「这一类 spawn 本来就
不会被 `subagent_runs` 记下」——两条真实历史行（BIGA-20260921-021 的 Stage 1/2
spawn）都带 `requesterOrigin.channel=feishu` 与完整的 `completion`/`delivery`
结构，而这次验证 spawn 的 `task_runs` 行是 `delivery_status: not_applicable`，
明显走的是不同的调用路径。**无论哪种解释成立**，现状是：J2-3 的结构化 join
在真实生产 spawn 路径下是否真的命中，目前**没有一次成功复现的实测**——而这
恰好是 J2-3 提交信息自己点名的风险：「猜错的后果是核验静默退化（join 永远
匹配不上 ⇒ 每次走退回分支 ⇒ 看起来一切正常）」。

不撤回已合并的迁移与 join 逻辑本身——P1/P2/P4/P5 与 P3「文本匹配更容易被
蒙混」都已被独立复核亲手复现，逻辑本身站得住，问题只在这一条 live 断言。
需要重新确认的是「这条 join 在真实 production spawn 上到底有没有命中」，
不是重写代码。

### 🔴 变更 · 批 J-II：收敛 `run_id` 三同名（`agent_runs.run_id`→`ledger_id` ＋ `runtime_run_id` 落库）

设计文档 §4「`run_id` 这个名字现在指三个互不相同的东西」。**✅ 评审复核通过**
（2026-09-23，独立会话）：五道探针 P1–P5 外加红灯演练全见过红并已还原，一处 live
命名空间补验实测通过。schema v8→**v9**。

评审独立复现了两道（不看交接里贴的输出，自己弄坏）：关掉强绑定 ⇒ 伪造经文本匹配
蒙混过关（stdout 实打实印出「1 个 agent 两份独立记录都齐」）；把命名空间守卫的表
扫描改成空 ⇒ 三条全红，包括那条「必须真的扫到 decision_runs/run_events」的非平凡
覆盖断言 —— 「全绿是因为什么都没查」这个模式确实被堵住了。

**为什么现在做**：`run_id` 这个字面量在仓库里同时指三个东西，同住一个库、一份代码：
编排的一次执行尝试（`decision_runs.run_id`，32 位 hex）、`agent_runs` 的账本行号
（INTEGER 自增）、`SpawnHandle` 里运行时返回的 spawn id（UUID，且**从不落库**）。危害不是
「某处算错」，而是**任何一条 join / 报表都会拿到语义正确但指向错误的数字，且不报错** ——
与 §4 开头「decision_id 被迫承担五件事」是同一个病的反面。三处必须一起改：改一半留下的
半新半旧命名空间比现在更难读（读者无法判断手上这个 `run_id` 属于已改还是未改的那半）。
批 E 系列正在同一层（`_store`/契约）做迁移，**现在是最便宜的时机**。

**做了什么**：

- **J2-1 `agent_runs.run_id` → `ledger_id`**（迁移 `_V9`：`RENAME COLUMN`）。它从 Phase 1
  起就是 INTEGER 自增账本行号，与编排 `run_id` 毫无关系。🔴 **实测全仓没有任何代码读这一列的
  值** —— 唯一的引用是 `list_agent_runs` 的 `ORDER BY`，随迁移一并改名。⇒ 现在改是**免费**的，
  等它有了第一个真实读取方就不是。不改 `_V1` 建表语句：全新库先按 v1 建出 `run_id`、再由这条
  改名，是对的。
- **J2-2 `SpawnHandle.run_id` → `runtime_run_id`，并落库**。它取自 `sessions_spawn` 的
  `runId`（运行时 `subagent_runs.run_id` 那个 UUID）；在 adapter 内部叫 `run_id` 本来对
  （忠实照抄运行时列名），一出 adapter 就和编排 `run_id` 撞名。同一条 `_V9` 给 `agent_runs`
  加 `runtime_run_id TEXT`（nullable）；`record_agent_run`/`record_verdict_run` 加可选参数接住；
  `card_ops.persist()` 加 **keyword-only、默认 None** 的 `runtime_run_ids: Mapping`（🔴 必须有
  默认值：`synthesize.py` 与几条测试也在调它，签名不兼容会把不相干的东西一起弄红）；
  `orchestrator.py` 从每个 Stage 1/risk 的 `SpawnResult.handle.runtime_run_id` 收成映射传进去
  （**Stage 3 判官不产 verdict、不进映射**）。回放路径不记账本 ⇒ 也不写 `runtime_run_id`
  （回放不重新执行 agent，给它记真实 spawn id 是假账）。
- **J2-3 让 `spawn_check` 用上它（这才是 J2-2 的消费方）**。`spawn_proof()` 原先靠
  `payload_json LIKE '%<决策号>%'` 文本匹配认 spawn（F3 残留）。改成：`runtime_run_id` 非空
  时直接与运行时 `subagent_runs.run_id` 做**结构化 join**，为 NULL（历史行）时退回现有 LIKE。
  🔴 形状**照抄 `risk_check.py::CROSS_CHECK_PAIRS`**（有 `evidence_set_id` 用它、缺失退回
  `raw_hash`）逐字同形，不发明第二种兼容写法。退回分支的三态（`readable=False`⇒UNKNOWN /
  `rows==0` 有行⇒伪造 / 逐 agent 伪造）一条没塌（R-3）。**不新增 SQL** —— join 的右表复用
  已按决策号过滤的记录，因此「id 存在」同时蕴含「记录属于本决策」，比对全表存在性更硬。
- **J2-4 一道新守卫**（`tests/test_store.py::TestRunIdNamespace`，判据**可派生不是清单**）：
  BigA 自己 schema 里任何名为 `run_id` 的列必须是 `TEXT`，且其非 NULL 值必须能在
  `decision_runs.run_id` 里找到。语义不同的第四个同名几乎必然过不了这两关（`agent_runs.run_id`
  当年是 INTEGER，第一关就红）。**不碰运行时的 `subagent_runs`**（第四个同名，但那是运行时命名
  空间，不归我们管；我们叫 `runtime_run_id` 正是为了在边界上认出它）。

**🔴 一处 live 补验（线下无法替代，正文明确授权的付费例外）**：`SpawnHandle.runtime_run_id`
与 `subagent_runs.run_id` 是不是同一命名空间 —— 这是 J2-3 整个结构化 join 的前提，线下只能
靠「两边都像 UUID」猜，猜错的后果是 spawn 核验**静默退化**（join 永不匹配 ⇒ 每次走退回分支 ⇒
看起来一切正常）。一个最小 spawn（任务只让 agent 回「收到」，成本远低于 $0.05 上限，不出卡、
不走 `bin/biga-card`）：`start()` 返回 `runtime_run_id='e5c00e01-…'`，直接查运行时库
`subagent_runs WHERE run_id='e5c00e01-…'` **查得到**、`child_session_key` 一致、spawn 正常
完成。⇒ **同一命名空间，J2-3 成立。**

**探针记录（G-1：每道守卫先弄坏、见红、还原）**：

- **P1 · 改名后无人读旧名**：① 全仓 grep `\.run_id`，SpawnHandle 使用点已全部改到
  `.runtime_run_id`，无残留；剩余的 `run_id` 都是 `subagent_runs.run_id`（运行时表，该留）
  与迁移语句本身。② 把迁移目标名从 `ledger_id` 改成第三个名字（`xledger_id`）跑测试 ——
  `list_agent_runs` 当场 `sqlite3.OperationalError: no such column: ledger_id`，6 条 TestAgentRuns
  变红（**证明确有测试盯着列名，不是「反正没人读所以改什么都绿」**），已还原。
- **P2 · 迁移后只追加触发器仍有效**🔴：`RENAME COLUMN` 会自动改写触发器体，名字还在而体被改坏
  **不报错**，所以判据是真跑 SQL 不是看名字。对迁移后的 `agent_runs` 真跑 `UPDATE` 和 `DELETE`,
  两者都被 `AppendOnlyViolation` 拒；非平凡演示：`DROP TRIGGER` 后同一条 `UPDATE` 影响 1 行成功
  （证明「被拒」不是平凡通过）。已固化为 `test_迁移后agent_runs仍拒绝UPDATE和DELETE`。
- **P3 · 结构化 join 真的比文本匹配硬**🔴：构造一条 `agent_runs` 行，其 `runtime_run_id` 在
  `subagent_runs` 里不存在，但决策号出现在某条 `payload_json` 里（蹭上别人记录那个旧洞）。
  弄坏：把 join 分支关掉（永远走弱路径）⇒ `spawn_check` 返回 0 放过、stdout 显示「1 个 agent
  两份记录都齐」（伪造经文本匹配蒙混过关），测试翻红。还原后新判据报伪造（返回 1），旧 LIKE
  判据放过它（同场景返回 0）—— 两条判据在同一份数据上给出相反结论。
- **P4 · 退回分支不塌**：现有 10 条三态/绑定/覆盖用例（`wire` fixture 的 mock 行只有 `agent`
  键 ⇒ `runtime_run_id` 缺失 ⇒ 走退回分支）逐条不变，全绿。
- **P5 · J2-4 守卫见红**：临时往真实表 `raw_market_snapshot` 加 `run_id INTEGER` 列 ⇒ 守卫报
  「声明为 'INTEGER'，应为 TEXT」，已还原。另有常驻自证：用临时表分别证明类型子句与值子句
  各自会红（守卫函数本身非平凡通过）。

**最可能被攻破的一处（交评审重点看）**：J2-3 的强绑定把「id 存在」限定在**按决策号过滤后的
记录**里（`runtime_ids = {r.run_id for r in spawns}`，而 `spawns` 已按 `payload_json LIKE 决策号`
过滤）。这依赖一条隐含前提：**真实 spawn 的 `subagent_runs.payload_json` 一定包含决策号**
（`orchestrator._specialist_task` 把 `did` 写进任务文本）。若将来某条编排路径 spawn 时**不把
决策号写进 payload**，那条真实记录会落在 `spawns` 之外 ⇒ 它的 `run_id` 不在 `runtime_ids` ⇒
即便 `agent_runs.runtime_run_id` 存了正确的 id，强绑定也会**误报伪造**（fail-closed，方向安全，
但会把真卡判失败）。live 补验只证了「同一命名空间」，没证「所有 spawn 路径都把决策号写进
payload」——后者目前只有 orchestrator 一条路且确实写了，但这是评审该盯的假设。

### 🔴 变更 · 批 E-III：迁 `risk` + 退役 `amend_verdict.py` 旧路径（Facts/Assessment 拆分收官）

设计文档 §6 批 E 的最后一段。**实现完成、离线全绿（1068→1079 条），五道探针（P1–P5）
外加三道红灯演练全见过红并已还原，但未交独立评审 —— 不自宣通过**（开工与评审分不同会话）。

**为什么**：`risk` 迁完之后六个 Specialist 全部产 `FactBundle`，`amend_verdict.py` 操作
合体 `AgentVerdict` 的旧路径才能真正退役。🔴 但 `risk` 不是前五个那种迁移 —— 它的 stance
是 `VETO_STANCE`（"否决"），是制衡层**唯一能拦住 BUY 的信号**。前五个迁错 stance 后果是
「这句话不准」；这一个迁错后果是「一个真该被拦的决策放行了」。所以这一批的核心不是迁移
本身（那跟前五个一样机械），是 **VETO 穿透验证**：否决必须能从 `AgentAssessment` 一路穿
到 `DecisionCard` 真正拦截它的那一层。

**做了什么**：

- **`risk_check.py` 改产 FactBundle**（三个 return 点，含 foreign/no-upstream 两条
  `status='failed'` 路径）：`build_verdict→build_fact_bundle`、`save_verdict→save_fact_bundle`。
  ⚠️ risk 读**上游**五个 verdict 仍用 `load_verdict()`（多态，对新旧形状都返回
  `AgentVerdict`）—— risk 是它们的消费方，那一半跟这次迁移无关，`AgentVerdict` 仍在 import。
- **VETO 穿透，确认无需改代码**：`card_ops.load_verdicts_and_refs()` 用 `load_verdict()`
  多态把 risk 的 `AgentAssessment(stance=否决)` 压回 `AgentVerdict.stance`，`DecisionCard`
  的否决判据（`v.stance == VETO_STANCE` ⇒ 拒 BUY、必须 AVOID/BLOCK）读的正是这个字段 ——
  没有任何一处绕过 `load_verdict` 直接读原始 `verdict_json`。穿透链完整，P2 钉死它。
- **退役 `amend_verdict.py` 旧路径**：删掉操作合体 `AgentVerdict` 的 74 行（`--add-missing`/
  `--add-warning` 对旧形状生效、`--verdict` 覆盖、`dataclasses.replace(original,…)` +
  `save_verdict(amends=…)`）。`_assess_fact()`（fact 行只加 `--stance`）**保留** —— 六个
  Specialist 以后永远走它，并顺手把它扩成也拒 `--verdict`（堵掉「fact 行给 --verdict 被静默
  忽略」那个缝）。历史合体行（kind NULL/'verdict'）现在只读：对它跑 amend 明确报「已退役」。
  🔴 **`save_verdict()` 不删也不废弃** —— 七个测试文件 + `phase1_acceptance.py` 还靠它造
  「老形状」来测 `LegacyAdapter` 读路径宽；退役的是「活的 skill 还在写这个形状」，不是
  「这个形状不该再被测试到」。amend 不再 import 它，函数本身留在 `_store`。
- **`agents/risk/AGENTS.md`**：删掉 `--verdict UNKNOWN --stance 无法判定` 组合命令
  （**先查了真实数据库**：risk 历史 `--add-missing` 只有两个码 `risk.coverage.insufficient` /
  `risk.upstream.trade_date_inconsistent`，都已由 skill 自己报，不是 market 那种范围外判断
  边界）。`无法判定` 允许挂在 skill 报的 WARNING 上（`check_stance_vs_verdict` 只禁 UNKNOWN
  上的方向判断），所以 agent 不再需要 `--verdict` 事后把完整度降级；上游矛盾这类 agent
  观察走「依据/未被审阅的面」自由文本。
- **迁移旧路径测试**：`test_facts_split.py` 的 P6「老路径照常」→「旧路径已退役」；
  `test_verdict_refs.py::TestAmendCLI` 三条旧路径 CLI 断言改成测退役 + fact 行新路径；
  九处 `risk.build_verdict` 调用点改名 `build_fact_bundle`。

**探针记录（G-1：每道守卫先弄坏、见红、还原）**：

- **P2**（🔴 VETO 穿透，核心）：**弄坏** `AgentOutcome.to_agent_verdict()` 把 `stance` 丢成
  `None`（断掉否决从 AgentAssessment 到 AgentVerdict 的传导）→ 跑「否决真的拦住 BUY」→
  **报红 `DID NOT RAISE`**（BUY 没被拦）→ 还原。证明 P2 锚在**整条穿透链**上，不是断
  「stance 字段等于否决」——后者就算穿透断了也照样绿。
- **P1**（risk 产 FactBundle）：**弄坏** risk 的 return 退回 `AgentVerdict` → `type is FactBundle`
  **报红** → 还原。
- **P3**（旧路径退役）：**弄坏** 退役分支 `return 2`→`return 0`（假装成功）→「报退役不静默」
  的 `assert rc==2` **报红** → 还原。P3 另用 AST 断言退役的代码真没了（`dataclasses` 不在
  Name 里、`save_verdict` 不在被调用集里、`save_assessment` 还在）。
- **P4**（`_assess_fact` 对 risk 正常）：risk fact 行加 `--stance 否决` 成功、加 `--add-missing`
  被拒（复用 E-II 判据，换 risk 名字）。
- **P5**（六个全新形状聚合不丢）：market/emotion/sector/technical/news/risk 全走 fact+assessment，
  `load_verdicts_and_refs` 聚合六个、stance 全压回 —— Facts/Assessment 拆分的最终验收
  （E-I P3 → E-II P4 → 这里六个全是新形状，一个旧的都没有）。

### 🔴 变更 · 批 E-II：把 market/sector/technical/news 四个 skill 迁到 FactBundle

设计文档 §6 批 E 的第二段。**实现完成、离线全绿（1039→1062 条），六道探针（P1–P6）
外加四道红灯演练全见过红并已还原，但未交独立评审 —— 不自宣通过**（开工与评审分不同会话）。

**为什么**：E-I 只迁了 `emotion`（不读冻结快照、不进 `CROSS_CHECK` 的那个），把新三型
的形状定了下来。这一批第一次让**读冻结快照**（market/sector/technical）与**参与
`CROSS_CHECK`**（market/technical）的 Specialist 走新形状 —— 这两件事 E-I 都没真正测过。
`risk` 仍留在 E-III（它带 `VETO_STANCE`，还消费其余五个的产出，等它们形状稳定再动最安全；
退役 `amend_verdict.py` 的前提是**全部六个**都迁完，天然跟最后一个绑在一起）。

**做了什么**：

- **四个 skill 机械迁移**（形状与 E-I 迁 emotion 完全一致）：各自的 `build_verdict`→
  `build_fact_bundle`（返回 `FactBundle`）、`save_verdict`→`save_fact_bundle`。
  market/sector/technical **读冻结快照、填 `evidence_set_id`** 那部分逻辑一字未改 ——
  这一批只改「产出的是哪个类型」。消费方（card_ops/risk_check/DecisionCard）**零改动**，
  继续吃 `load_verdict` 的 `to_agent_verdict()` 兼容垫（②的裁定：不改成直接读
  `AgentOutcome`，那没有消费方、提前做是 L-1）。
- **①「Agent 追加的限制」缺失项归哪 —— 查了真实数据库，不照抄设计文档的例子**：
  `data/biga.db` 里四个 skill 历史上靠 `--add-missing` 补的限制（去掉 `stance=` 类）只有
  三个形状，**全部已经由 skill 自己检测**：market 涨跌家数 0/0/0 → `market.breadth.not_yet_formed`、
  市场宽度源不可用 → `market.breadth.unavailable`、sector 盘前板块榜无数据 →
  `sector.board.pre_session`（同名同码）。technical/news 的历史 `--add-missing` **一条都没有**
  （全是 `stance=`）。⇒ ①的裁定对这四个 skill **不新增任何检测代码**，genuine 数据缺口
  skill 早就在自己的 `missing[]` 里报了。
- **唯一的例外 `market.trend.no_history`：裁定为「范围外」，skill 不产它**。
  🔴 **它不是数据缺口** —— 实测 5 次「市场趋势——只有单日快照，无指数历史序列」修订，
  原件**每一次都带着 `volume_ratio`**（≥21 根、整段 20 日序列），3 次还是 15/15 满字段的
  PASS。也就是说 market 一直有整段序列，这句话从来不是「抓少了」，而是 agent 在说
  「我看不出**趋势方向**」——那是**判断**（铁律 4），与 sector 早就裁定为范围外的
  「板块连涨几天」（`sector_calc.py` 顶部注释）是同一条线：`missing[]` 是给「本该有却这次
  没有」的，范围外的东西每次都在，混进去只会把真正的缺失淹没。⇒ 归 agent 回答的自然
  语言「需要注意」，不进 `missing`、不加 `AgentAssessment` 字段、skill 源码里连 `trend`
  这个词都不出现。（这一条命中了 E-I 交下来的 ⚠️ escape hatch；开工会话据实测证据停下来
  确认过设计，未强行套用。）
- **第五交付物：修 `agents/market/AGENTS.md` 模板**（本批**唯一**触碰 AGENTS.md，经设计
  owner 明确授权）。老模板**强制** agent「想说趋势就必须往 `missing` 加 `market.trend.no_history`
  + `--verdict WARNING`」——这本身是**旧契约漏的一个洞**：agent 拿 `--verdict` 把 skill
  算的、可回放的完整度**事后降级**，把一个**判断上的保留**伪装成**数据缺失**。批 E 拆事实/
  判断正是要焊死这条缝。迁移后 fact 行会**直接拒绝** `--add-missing`（`_assess_fact` 报错），
  🔴 **不修模板的代价不是文档陈旧，是 market agent 下一次真实出卡就撞拒绝、然后像 F9 那样
  抖动**（62s／12 次工具调用去找正确命令）。⇒ 删掉 `--add-missing … --verdict WARNING`
  组合命令，趋势 caveat 改走 agent 已有的「需要注意」自由文本字段。
  ⚠️ sector/technical/news 的 AGENTS.md 里还留着**可选**的 `--add-missing X.partial` 示例
  （对应 genuine 数据缺口，skill 已自检、agent 正常只需 `--stance` 转述，不会撞上）——
  破坏概率低，未在本批修，已记进 `TODO.md` 作为后续文档收敛。
- **`amend_verdict.py`**：`_assess_fact` 对 fact 行拒绝 `--add-missing/--add-warning` 这条
  **硬约束不变**，只把提示语从「留给批 E-II」改成终态口径（缺口归 skill 自己的 `missing[]`、
  范围外 caveat 归「需要注意」，这一行只收 `--stance`）。`kind=='fact'` 路由是 E-I 就铺好的
  通用路，四个新 agent 直接复用，未加新代码。

**探针记录（G-1：每道守卫先弄坏、见红、还原）**：

- **P1**（四个 skill 产合法 FactBundle、落库 `kind='fact'`）：另有一条主动红灯 ——
  把 skill 产出退回合体 `AgentVerdict`，`save_fact_bundle` 当场 `TypeError: 只接受…FactBundle`
  （写路径严，E-I 判据）。
- **P2**（CROSS_CHECK 穿透新形状）🔴：**弄坏** market 的 `_es_id_for` 恒返回 `None`（断掉
  es-id 流），跑「不同 evidence_set_id 应报冲突」→ **报红 `assert []`**（因两次冻结同一份
  数据 raw_hash 相同、只有 es-id 判据能抓到，退回 raw_hash 兜底就漏报了）→ 还原。证明 P2
  真的锚在 evidence_set_id 穿过新 FactBundle 形状这条链上，不是凑巧绿。
- **P3**（①裁定：skill 自检缺口、market-trend 不产）🔴：**弄坏** —— 往 market 注入一条
  `market.trend.no_history` → 「范围外不产」断言**报红 `assert not True`** → 还原。
- **P5**（amend 路由四个 agent）🔴：**弄坏** `_assess_fact` 的 `--add-missing` 拒绝分支
  （改成永不触发）→ 四个 agent 的「fact 行加 --add-missing 被拒」**全部报红**（rc 变 0）→ 还原。
- **P6**（第五交付物验收）🔴：**弄坏** —— 往 market AGENTS.md 塞回一段带 `--add-missing`
  的 bash 命令块 → 「模板命令块里没有 --add-missing」**报红** → 还原。P6 同时正向验证：
  模板现在教的那条 `--stance` 命令对一条真实 market fact 行确实 rc=0、判断落上去、事实没被重打。
- **P4**（回归：五新一旧六个 agent card_ops 聚合不丢）：market/sector/technical/news/emotion
  走新三型 + risk 走老合体，`load_verdicts_and_refs` 聚合六个、stance 全压回、market 自检的
  一条 missing 也没丢。

### 🔴 新增 · 批 E-I：把事实和判断拆开（契约基础设施 + 一个试点）

设计文档 §6 批 E 的第一段。**实现完成、离线全绿，六道探针（P1–P6）全见过红并已还原，
但未交独立评审 —— 不自宣通过**（开工与评审分不同会话）。

**为什么**：`AgentVerdict` 把两件东西焊在一个 frozen dataclass 里 —— 事实（skill 算的
`result`/`evidence`/`missing`）和判断（`stance`，skill 跑完之后 Agent 才补得上）。第一步
不是照抄设计文档那句「拆开」，是先查 `amend_verdict.py` **到底在补救什么** —— 它不是
方便功能，是「没有它，Agent 想只加一个判断，就只能把整份事实重打一遍」这件事的补丁。
这条断层已经咬了三次：`BIGA-20260920-002` 补丁路径建成前 15 条 Evidence 的
`retrieved_at` 全部转述丢失（L-10）；F8 `amend_verdict.py` 把契约层 stance 校验抄了一遍、
连盲区一起抄（L-3）；F9 Agent 靠第二条命令后补判断、报错后现读源码重试，Stage 1
延迟翻倍。三条同源：事实与判断焊在一起，"只加一个判断"没有一条干净的路。

**做了什么**：

- **三个新契约类型**（`skills/_contract/facts.py`）：`FactBundle`（事实 = AgentVerdict
  减去 stance，skill 产）、`AgentAssessment`（判断 = `stance` + `fact_ref` 指回哪份
  FactBundle，**不抄事实**，Agent 产）、`AgentOutcome`（组合视图，程序产）。
  `LegacyAdapter.split()/to_outcome()` 把任何历史 `AgentVerdict` 拆回新三型。
- **事实层铁律抽成一份共用**（`verdict.py`）：`check_fact_invariants` / `check_stance_vocab`
  / `check_stance_vs_verdict`，`AgentVerdict` 与 `FactBundle` **调同一份**，防 L-3
  （F8 就是各写一遍、连盲区一起抄的实测）。跨型铁律（UNKNOWN 的事实上不许挂方向判断）
  归 `AgentOutcome` 校验 —— 因为只有它同时握着 `verdict`（事实）和 `stance`（判断）。
- **存储：新旧同住 `agent_verdicts`**（schema v8 加 `kind` 列：NULL/'verdict'=旧合体、
  'fact'、'assessment'），复用既有的只追加触发器 + 线性 amend 唯一索引，不新开表。
  `save_fact_bundle`（写严，拒收 `AgentVerdict`）、`save_assessment`（校验 fact_id 是
  同 task_id/agent 的 fact 行、构造 AgentOutcome 跑跨型铁律、`ux_verdict_amends_linear`
  兜住「一份事实最多一个判断」）、`load_outcome`。
- **`load_verdict` 变多态，消费方零改动**：旧合体行直接 `from_dict`；新 fact/assessment
  行走 `load_outcome` 拼成 AgentOutcome 再 `to_agent_verdict()` 压回。`DecisionCard`
  的 `isinstance(v, AgentVerdict)` 严格检查、`risk_check` 读 `.stance` 全照旧。
  🔴 这是刻意取舍：让 AgentOutcome 成为组合视图（`load_outcome` 返回它、探针拿它检查
  拆分），但**没有**改 card_ops/risk_check/DecisionCard 去字面读它 —— 试点血缘面越小
  越好，让 DecisionCard 收 AgentOutcome 会涟漪到卡的序列化/回放。收敛留 E-II/后续。
- **读宽写严**：`LegacyAdapter` 能拆任何历史 verdict（读路径宽），新落库只收新形状
  （写路径严）⇒ 旧格式随时间自然清零，不驻留成第二套要跟着演进的口径。
- **试点只迁 emotion**：Phase 1 第一个建成、形状最简、**不进 `CROSS_CHECK_PAIRS`**
  （那条只连 market↔technical），影响面最小。`emotion_calc.py` 的 `build_verdict`→
  `build_fact_bundle`（返回 FactBundle）、`save_verdict`→`save_fact_bundle`。其余五个
  skill **一字未改**，等 E-II。
- **`amend_verdict.py` 认得 fact 行**：对 fact 行只加 `--stance`（写一行
  `AgentAssessment`，事实一个字不重打 —— 这正是 L-10 核心的修法）；对 fact 行给
  `--add-missing` **明确拒绝**并指路 E-II（见「已知问题」）。
- **顺带还 D-II 的账**：`Evidence` 加可选 `evidence_set_id` 字段；三个日线 skill 读冻结
  时填上；`risk_check.py` 的 CROSS_CHECK 升级为**优先比 `evidence_set_id`**（结构验证、
  不看内容），两条都有才用、缺一条退回 `raw_hash`（老 Specialist 还没填这字段，不能
  因此让检查失效）。D-II 承认的「两次独立抓取碰巧逐字节相同则 raw_hash 碰巧相等、漏报」
  盲区就此堵上。

🔴 **探针记录（每道守卫「怎么弄坏 / 报红 / 已还原」，L-13）**：

- **P1（LegacyAdapter 往返逐字段一致）**：让 `to_agent_verdict()` 漏拼 `stance` ⇒
  `test_legacy_adapter_round_trip` 红（还原出的 dict 少一个 stance 键）。已还原。
- **A（`save_fact_bundle` 写严）**：让它接受 `AgentVerdict` ⇒ 写严测试红（本该 `TypeError`
  拒收合体类型，却存进去了）。已还原。
- **B（`load_verdict` 多态不静默丢新行）**：让多态分支对 kind='fact' 也走旧
  `from_dict` ⇒ 读回来 stance 丢失、消费方拿到残缺 verdict，回归红。已还原。
- **C（跨型铁律在 AgentOutcome）**：把 `AgentOutcome.__post_init__` 的
  `check_stance_vs_verdict` 去掉 ⇒ 能给一个 UNKNOWN 的事实挂「亢奋」判断，
  `test_cross_type_invariant` 红（本该 fail-closed）。已还原。
- **D（事实层铁律共用一份，L-3）**：把 `check_fact_invariants` 弄坏一处 ⇒
  `AgentVerdict` 的测试与 `FactBundle` 的测试**一起**红（证明是一份，不是两份）。已还原。
- **P4（CROSS_CHECK 优先 evidence_set_id、缺失退回 raw_hash）**：禁用 evidence_set_id
  偏好、一律退回 raw_hash ⇒ `test_同一个evidence_set_id不报` 红（同一个冻结集、两条
  都无 raw_hash ⇒ 兜底误报「无法核实」）。
  🔴 **评审复核发现一处假绿并已修**：同一道破坏下，`test_P4_都有evidence_set_id就比它_不同则报`
  当初**仍绿** —— 它只断言「报了冲突」，而两条都无 raw_hash 的兜底也会印一条冲突，凑巧满足。
  即「名字像在测这个特性的那条测试，不是真正锁住它的那条」（L-13 的形状）。已把它锚死在
  只有 evidence_set_id 判据才印的「冻结集」+ 两个 es-id 值上；现在同一道破坏下它**也红**了。
- 还原核对：`grep -rn PROBE skills tests` 无残留；`test_contract_behavior` /
  `test_store` / `test_emotion_calc` / `test_facts_split` 全绿。

**这一批明确没做**（留给 E-II）：迁其余五个 skill（market/sector/technical/news/risk）；
退役 `amend_verdict.py`（要等全部迁完）；把 Agent 追加的「限制」缺失项归位（见下）。

**已知问题 · Agent 追加的缺失项在新形状里还没有落点**：老 `amend_verdict.py` 让 Agent
一条命令同时加 `stance`（判断）和 `--add-missing`（Agent 观察到的数据限制，比如「只有
单日快照、无法判断趋势」）。拆开之后 `stance` 有家（`AgentAssessment`），但**Agent 追加
的缺失项没有** —— 它既不是 skill 的事实（skill 不知道），也不是一个 stance。E-I 对 fact
行的 `--add-missing` **明确拒绝并指路 E-II**，不静默吞。E-II 要想清它归哪：改由 skill
自产、还是给 `AgentAssessment` 增一类字段。这是拆「事实 vs 判断」这条线时，一个一直骑在
缝上的用法逼出来的真实边界问题（已记进 `TODO.md`）。

### 🔴 修复 · 批 C-III：Orchestrator 健壮性四处收尾（外部架构复审复核）

来源：2026-09-22 一份外部架构复审，逐条复核记在设计文档 §2 追加 5。这一批只做
追加 5 里标了 ✅ 且判定「值得马上修」的四条，彼此独立。**实现完成、离线全绿
（1005 → 1016：+7 条 C-III 探针/单测，+4 条本章带出的参数化 docs 测试），四道探针
全见过红并已还原，`biga-card --check BIGA-20260922-001`
回放一致、`audit_public.sh --worktree` 十一项全绿，但未交独立评审 —— 不自宣通过。**

⚠️ 本批在**独立 git worktree（分支 `c-iii`，基于 `2e5e8ea`）**上做 —— 批 E-I 的
未提交改动同时在主工作区里（那份 WIP 当时让测试红 25 条）。两批文件层面只在
`_store/db.py` 相交，且区域不交叠（E-I 加的是新函数、C3-3 改的是既有
`verify_verdict_refs`）。这是本项目第一次两批**真正并行**（A–E-I 之前都是串行、
每批开工前上一批已提交），隔离开工是为了给 C-III 一个绿色基线、且不把 E-I 的
半成品扫进 C-III 的提交。合并回 `orchestration` 时须重新核对是否真无冲突，
不凭这次 diff 快照就认定安全。

**C3-1 · `CARD_PERSISTED` 转移必须写在 `persist()` 成功之后**（评审 §17-18）
原来 `transition(..., CARD_PERSISTED)` 先于 `card_ops.persist(card)`。为什么这是
真 bug：`persist()` 抛错时，`run_events` 会留一条「已落库」的假记录，而库里其实
没有这张卡 —— `run_events` 只追加、删不掉，于是**一个可修复的失败被记成了不可
修复的谎**，`--status` 从此对这次运行说假话。改法：先 `record_id = persist(card)`、
成功拿到号后才转移，`detail` 带真实 `record_id`（不再是占位串
`{"record":"persisting"}`）。
探针 P1：monkeypatch `card_ops.persist` 抛异常 → 断言 `run_events` 不含
`CARD_PERSISTED`。未改代码时报红（`assert 'CARD_PERSISTED' not in [...]` 失败——
状态链里确实多出这条假记录），改后绿、已还原。另一条断言 `detail.record_id` 是
真实 `int`（未改时 `{"record":"persisting"}` → KeyError）。

**C3-2 · Stage 1 部分启动失败时取消已启动的 handle**（评审 §28 + 追加 5.2）
`handles = [ad.start(a, ...) for a in STAGE1_AGENTS]` 中途某个 `start()` 抛错时，
之前已成功 start 的 handle 被直接丢弃 —— 那些 Specialist 会话继续跑到各自
`runTimeoutSeconds` 才停，白烧钱。为什么值得单独记：这恰好是
`OpenClawRuntimeAdapter.cancel()` 建好以来**一直缺的那个真调用方**（批 C-I/D-I/D-II
三次评审都把它记为残留风险：`cancel()` 只在 N=2、无 drain 场景验过）——Stage 1
五路正是 N 可达 5、天然有 drain 的真实取消场景。改法：列表推导换成显式循环 +
已启动 handle 列表，`except` 分支里对已启动的逐个 `cancel()`（cancel 本身失败用
`contextlib.suppress` 兜住，不盖原始异常）再抛。
探针 P2：桩 adapter 第 3 个 `start()` 抛 `SpawnStartError` → 断言前两个已启动的
handle 收到 `cancel()`（比对身份，不只数量）。未改代码时报红（`cancelled == []`——
两个 handle 被孤儿化），改后绿、已还原。
⚠️ 这是**离线**用桩 adapter 验证「调用发生」；真实 adapter 上 N=5/drain 的取消
**命中正确性**（`active[]`↔`tasks[]` 同序映射）本批未在活运行时重跑 —— 见「已知问题」。

**C3-3 · `verify_verdict_refs` 补 `agent` 字段核对**（评审 §8-16 的一部分）
原来只核对 `verdict_id` 存在 + `content_sha256` 一致，不核对
`ref.agent == 存量.agent`。为什么这是洞：`verdict_id` 是**跨 agent 的全局自增**
（不按 agent 分号段），所以一条手工拼的 ref 可以声称「这是 market 的原件」、
`verdict_id` 与 `sha` 却全指向 news 那一行 —— 此时「能找到 + hash 对」两道检查
都通过，只有 agent 核对能拦下这种张冠李戴。`VerdictRef.agent` 的 docstring 早写明
它必须与被引用行一致，这里补上真正强制那句话的检查。改法：`load_verdict_meta`
已返回 `agent`，加一行比较，不一致就报（与 hash 检查各自独立，可同时报）。
探针 P3：造两条不同 agent 的原件（market/news），伪造一条声称 market、
`verdict_id`+`sha` 全指向 news 的 ref。前置断言：一条「agent 也说 news」的 ref
核对通过（证明 forged 唯一破绽就是 agent，存在性/hash 都不触发——避开 A-I 评审
栽过的「探针没命中目标条件」坑）。未改代码时报红（返回 `[]`，`assert len==1`
失败），改后绿、已还原。真实数据零误伤：扫 37 张在线卡（1 张带 refs），新检查报
0 问题。

**C3-4 · Orchestrator 感知总预算，各阶段按剩余时间收窄**（评审 §35）
`stage1_sec`/`risk_sec`/`synth_sec` 各自独立、互不感知 `deadline_sec` 还剩多少。
Stage 1 吃满自己预算后，Risk 与 Synth 仍各自等全额，三段之和可以超过
`deadline_sec`，只靠外层 bash `timeout` 硬顶 —— 纵深防御的最后一层，不该是唯一
一层。改法：`run()` 里 `deadline = time.monotonic() + self.deadline_sec`，每段的
`ad.wait()` 用 `_stage_timeout(deadline, 该段预算) = min(该段预算, 剩余)`；
`remaining <= 0` 抛 `OrchestratorError` → 进 FAILED（与零证据/判官失败同一条失败
路径）。顺带用上了 `orchestrator.py` 里一直是死代码的 `import time`。
探针 P4：总预算设 50s（远小于任一段固定预算），断言三段 `wait` 拿到的 timeout
都被压到 ≤50 且严格小于各自固定值。未改代码时报红（stage1 拿到 300，
`300 <= 50` 失败），改后绿、已还原。另两条单测钉住 `_stage_timeout` 取较小者、
剩余耗尽抛错。

⚠️ 本批**不碰**（设计文档 §2 追加 5 明确划出的排期边界）：`run_id` 贯穿
`agent_verdicts`/`evidence_sets`/`VerdictRef`/`DecisionCard` 这条链（追加 5.1：
排期约束，没有「同 decision_id 重试」的消费方之前提前建是 L-1）；§26 的
budget/flock 从 bash 挪进 Python（比这四件更大的一批）；§24-25 stale-run reaper
的 cron/timer 调度（批 G 的地盘）。

#### 已知问题（批 C-III）

- **C3-2 的取消在真实运行时上未跑过 N=5/drain。** 离线探针证明的是
  Orchestrator **会**对已启动的兄弟调 `cancel()`、且调在对的 handle 上（桩
  adapter）。而 `cancel()` 内部把 `runId` 映射到 `taskId` 用的是 `active[]`↔
  `tasks[]` 的**同序对应**，这条只在 C-I 的 `adapter_spike.py cancel` 里对
  N=2、无 drain 实证过。C3-2 现在给了它第一个真实调用方（Stage 1 部分启动
  失败），也就是说这条 live 场景**从此可被触发**，但本批（纯离线）没有在活
  运行时上跑一次真实部分失败。⇒ 「`cancel()` 缺真实调用方」这半条残留风险
  **已由 C3-2 解决**；「真实运行时 N=5/drain 同序映射正确性」这半条仍是 live
  补验项，随第一次真实 Stage 1 部分失败（或专门造一次）补上。

### 🔴 修复 · `agent_runs` 记账在批 C-II 之后再没被写过（live 验证才暴露）

批 C-II 把出卡入口从 main 的提示词驱动切到 `DecisionOrchestrator`（程序驱动）
之后，第一次真实端到端出卡验证发现 `tools/verify/spawn_check.py` 永远报
「判不了」（rc=2）——不是安全洞（没把失败误判成功），但每次真实出卡都会印
一条误导性的红字，且这条验证闸门自己已经名存实亡。

**为什么会这样**：`spawn_check.py` 的判据要求 `agent_runs` 表有行，才能拿去跟
运行时自己的 `subagent_runs` 交叉核对（业务代码写的东西证不了自己，这条
交叉核对才是真证据）。写 `agent_runs` 这一步唯一的调用方是旧的 standalone
`synthesize.py`（main 提示词驱动时期，main 自己跑这个脚本落卡）。批 C-II 把
合成逻辑挪进了 `card_ops.synthesize()`/`persist()`（`orchestrator.py` 直接
调用的模块函数），但没有把「记账本」这一步一并搬过来——`agent_runs` 从
C-II 合并那天起就没被在线路径写过，只是当时没有真实运行过，直到 2026-09-22
第一次 live 验证才暴露。

**为什么这么久没被发现**：唯一守着这条不变量的测试
（`test_store.py::TestAgentRunsIsLedgerNotProof`）只用 AST 扫「repo 里有没有
非 `_store`/`tests` 的代码调用 `record_agent_run`」，而 `synthesize.py`
本身仍然是合法的测试夹具（好几个测试文件拿它当子进程跑），扫描器找到它就
满足了断言——即使生产入口再也不会真的执行它。这是「判据是文件里有没有这段
文字，不是调度命令会不会走到它」，本仓库自己在别处反复强调的那条原则，
这次自己没做到。

**修法**：`card_ops.persist()` 在线路径（`replay_of is None`）补上
`record_verdict_run()` 循环——这是 IO 边界该做的事，`synthesize()` 保持纯
函数不变。回放路径显式跳过，因为回放没有重新执行任何 agent，记一遍「执行」
是假账。新增两条回归测试钉住两个方向（在线必须记、回放必须不记），且都
独立复现过红（去掉记账循环 → 在线测试报 `[] == [...]`；把守卫改成对回放
也记账 → 回放测试报 `12 == 6`）。

⚠️ 没有改 `test_我们自己的代码就在写它` 那条 AST 扫描——它的目的本来就是
证明 `agent_runs` **可以被业务代码自己写**（因此不可信），不是验证生产
入口真的在写；`synthesize.py` 作为测试夹具继续满足这条断言是对的，只是
不足以覆盖生产入口自己有没有在写，那是 `spawn_check.py` 对着真实
`decision_id` 才能查的事，这次靠一次真实 live 运行才查出来。

### 🔴 变更 · 批 D-II：Specialist 改口读冻结快照（切三个 skill 的实际行为）

设计文档 §6 批 D 剩下的部分。**这是 D 系列真正的高风险段** —— D-I 只在旁边把机制
建好，这一批真正切 `market_calc.py` / `sector_calc.py` / `technical_calc.py` 的
`fetch_index_daily` 调用点。**实现完成、离线全绿（985 → 1003），四道探针全见过红并已
还原，但未交独立评审 —— 不自宣通过。**

**为什么**：D-I 之后 `freeze_index_daily` 一直没有调用方，「所有 Specialist 看同一份
数据」仍只是「机制上可以」。这一批让编排器真的冻结一次、三个日线消费者真的读同一份，
把那句话从「可行」变成「这次决策真的如此」——而且**留了一条能报红的检查**，将来谁
悄悄退回独立抓取会被抓到，不是改完就没人管了。

**做了什么**：

- **`orchestrator.py` 的 `SNAPSHOT_FROZEN` 接了真东西**：Stage 1 fan-out 之前调
  `SnapshotCoordinator.freeze_index_daily(did, ["sh000001","sz399106"], bars=120)`
  （🔴 **120 不是 25** —— 消费者里最大的是 `technical_calc.py::BAR_COUNT=120`，
  D-I 评审复核时核过），evidence_set_id 存进转移 detail + `RunContext`（批 B 起就有
  这个字段、一直是 None，这一批第一次真填）。freeze 失败 ⇒ 整体 FAILED（fail-closed，
  不退回各自抓一份、悄悄丢掉共享保证）。`_specialist_task()` 只给日线三个 agent 的任务
  文本加 `--evidence-set-id`（emotion/news 的 skill 没这个参数）。
- **三个 skill 加可选 `--evidence-set-id`**（参照 `--task-id`，缺省 `None`）：给了就
  `read_index_daily()` 读冻结、不联网、不重复落盘；没给就跟今天一样 `fetch_index_daily`。
  🔴 **没给不当成更严格的版本** —— 手工单跑某个 skill 调试的路径不被连坐拦掉。
  🔴 **给了坏号 fail-closed**：`SnapshotReadError` 直接上抛，**绝不静默退回独立抓取**
  （那是最危险的：假装什么都对）。
- **`raw_hash` 语义**：读冻结的 skill，`Evidence.raw_hash` 取冻结集登记的
  `content_sha256`（整份 raw 的指纹，`SnapshotCoordinator.frozen_content_sha256()`
  新增的只读访问器给），**不对自己读到的那一截重算** —— 不同消费者读 2/25/120 根，
  对切片重算会得到三个不同哈希，而它们本该指向同一份。
- **`CROSS_CHECK_PAIRS` 改判据，不是删**（`risk_check.py`）：`market.sh_close` ↔
  `technical.close` 在共享同一份冻结数据后**值必然相等**，比值就退化成恒真死配置（L-7）。
  改成核对两条 `Evidence.raw_hash` 是否相同：都读冻结 ⇒ 相同 ⇒ 不报；某个退回独立
  抓取 ⇒ 出自另一份 ⇒ 不同 ⇒ 报红。守的东西从「数值凑巧对上」变成「真的共享了同一份」。

🔴 **探针记录（每道新守卫「怎么弄坏 / 报红输出 / 已还原」，L-13）**：

- **P5（坏号 fail-closed）**：让 technical 的冻结分支 `except: 退回 fetch` ⇒
  `test_P5` 红：`assert 1 == 0`（fetch 被调了，本该 0）。已还原。
- **P4（raw_hash 取冻结集那份、不对切片重算）**：让 sector 对读到的 2 根重算 hash ⇒
  `test_P1` 红：sector 的 `d80e…` ≠ 冻结集 `f01a…`。已还原。
- **P2（CROSS_CHECK 判据换了、且仍会红）**：把判据退回「比值」⇒ `test_P2` 与
  `test_值相等也照报` 双红（值相等 ⇒ 漏掉 raw_hash 不一致，`assert []`）。已还原。
- **freeze 真的接进编排**：把 `freeze_index_daily` 换成假号、跳过冻结 ⇒
  `test_一次决策只冻2行raw` 红：`assert 0 == 2`。已还原。
- 还原核对：`grep -rn PROBE skills tests` 无残留；三个 skill 的既有离线测试与
  `test_orchestrator`/`test_risk_check` 全绿。

**这一批明确没做**（留给后面）：不迁 breadth/pool/news/emotion 的抓取（不属于
「三个日线消费者改口」）；不改 `SnapshotCoordinator` 的 `read_index_daily` 签名
（只**新增**了只读访问器 `frozen_content_sha256`，属于分发提示词说的「签名接不上」）；
不给 `Evidence` 加 `evidence_set_id` 字段（那是批 E 的契约改动，CROSS_CHECK 因此用
已有的 `raw_hash` 而非 `evidence_set_id`）。

### 新增 · 批 D-I：SnapshotCoordinator 基础设施（冻结一次、多处读）

设计文档 §6 批 D 的第一段。**实现完成、离线全绿（956 → 985），五道探针全见过红并
已还原，但未交独立评审 —— 不自宣通过**（开工与评审分不同会话，见
`docs/guide/orchestration-kickoff-prompt.md`）。

**为什么**：`fetch_index_daily` 被 market / sector / technical **各自独立调用**（§2 复核
成立的那条 §15 断言）——三次网络调用、各落一行 raw，理论上应该拿到同一份数据，但没有
任何机制保证。「所有 Specialist 看的是同一份数据」（设计文档 §4 `evidence_set_id` 那一行）
因此**无法验证**。这一批把「抓取」和「读取」拆开，让这句话从一句愿望变成一条可核对的
属性 —— 但**只建地基、不改任何 Specialist**（那是 D-II，风险高得多，要等这层验实）。

**做了什么**：

- **新增 `skills/_snapshot/`（`SnapshotCoordinator`）**：
  - `freeze_index_daily(decision_id, symbols, *, bars) -> evidence_set_id` —— 每个 symbol
    在一次决策里**只真实抓一次**（用调用方给的 `bars` 取全量），原样落 `raw_market_snapshot`，
    登记一行 `evidence_sets`。
  - `read_index_daily(evidence_set_id, symbol, *, bars) -> IndexDaily` —— 从已冻结的 raw
    **切片**出调用方要的根数，**不联网**。sector 要 2 根、market 要 25 根、technical 要
    120 根，都从同一份底层数据切。🔴 要的根数超过冻结的根数 ⇒ 抛错，绝不静默返回更少
    （R-3 / L-2：算不出来必须说算不出来，不 fail-open）。
  - `frozen_snapshot_ids(evidence_set_id)` —— 反查这次冻结登记了哪几行 raw。
- **`_sources/sina.py` 抽出纯函数 `parse_index_daily(symbol, payload)`**：`fetch_index_daily`
  = 网络（`get_json`）+ `parse_index_daily`。🔴 **为什么抽**：读冻结快照要从存下来的 raw
  重建 `IndexDaily`，若在 `_snapshot` 里再写一遍解析，就是同一判据两份实现（L-3）——
  某天一处改了另一处没改，两条路径对「什么样的日线算合法」给出不同答案，且不报错。
  抽取是纯空操作，解析逻辑一字未改（`tests/test_snapshot.py::TestParseExtraction` 锁住
  正常解析 + 每一条形状校验仍在；全量测试条数只增不减）。
- **`_contract.new_evidence_set_id()`**：`es-<uuid4>` —— 身份铸造只此一处，不让 `_snapshot`
  自己发明第二套 id 规则。与 `new_run_id` / `new_trigger_id` 同在契约层。
- **`_store.save_evidence_set` / `load_evidence_set`**：`evidence_sets` 表（批 B 建的）第一次
  真的被写行。存储层对 `manifest` 结构**不做假设**（只负责严格 JSON 落库）——manifest 长
  什么样、怎么反查，是冻结方的事，存储层若也内嵌一份就成了第二处要跟着演进的地方（L-3）。
  `manifest_json` 记每个 symbol 的 `snapshot_id` ⇒ 能被**反向走通**回 raw 层，不是一段
  只用于展示的自由文本。

🔴 **探针记录（每道新守卫「怎么弄坏 / 报红输出 / 已还原」，L-13）**：

- **P1（read 不重抓）**：在 `read_index_daily` 里加一行 `self._fetch(...)` 假装重抓 ⇒
  `test_P1` 红：`底层抓取应仍是 1 次，实测 4 次 assert 4 == 1`。已还原。
- **read fail-closed（越界）**：拆掉 `if bars > len(raw): raise` ⇒ `test_要的根数超过冻结的
  根数就报错` 红：`DID NOT RAISE SnapshotReadError`（`raw[-6:]` 在 len=5 时静默返回 5 根）。已还原。
- **P4（`evidence_sets` 只追加）**：临时拿掉 v7 里 `evidence_sets` 的只追加触发器 ⇒
  `test_P4_UPDATE被拒` / `test_P4_DELETE被拒` 红（改删成功），且 `test_store.py::test_每张表
  都有只追加触发器` **一并**抓到 `这些表可被改写：['evidence_sets']`（动态守卫也是活的）。已还原。
- **P5（manifest 可反查）**：freeze 的 manifest 条目去掉 `snapshot_id` ⇒ `TestReverseQuery`
  两条红（反查取不到行 / 字段不全）。已还原。
- 还原核对：`grep -rn PROBE skills tests` 无残留，`schema.py` 不在 `git diff` 里（临时改动
  逐字节复原）。

**这一批明确没做**（留给 D-II，已记进 `TODO.md`「批 D-I 施工空档 + 批 D-II 输入」）：
不改 market/sector/technical 的调用点；不动 `orchestrator.py` 的 `SNAPSHOT_FROZEN` 转移
（它的 detail 仍诚实地写着「还没有真东西」——D-I 之后仍没有真消费方）；不动
`CROSS_CHECK_PAIRS`（Specialist 真共享快照前它仍有意义，删早了是造假阴性窗口）。
⚠️ 顺带记下：分发提示词把 technical 写成 `bars=25`，实测是 **120**——D-II 接线时冻结
`bars` 要取 120，否则 technical 读 120 会撞 fail-closed。

### 🔴 变更 · 批 C-II：DecisionOrchestrator + 生产入口切换 ★

设计文档 §6 批 C 剩下的部分。**这次升级第一次改动生产入口 `bin/biga-card`。**
948 条测试全绿（938 → 948）。**实现完成、离线全绿**，已过独立评审复核（`d35d286`）。
P1/P2 live 补验一度挂起（需重启网关加载 synthesizer），**现已两条都真跑过**（2026-09-22）：
P1 = `BIGA-20260922-001` 走 8 步细粒度链、`subagent_runs` 7 个 spawn 含 synthesizer；
P2 = 把 L-14 递归提示词贴给真 main，它**拒绝**自我编排、0 spawn、不占号不落卡（「够不到」成立）。
详见 `TODO.md`「批 C-II 收尾」。

**为什么这是全案的中心**：这一批做完之后，「谁能启动出卡流程」的答案从
「守卫拦住了不该启动的人」变成「除了这条 Python 路径，没有别的路能启动」。
L-14 出卡递归事故就出在「谁能启动」这条边界上 —— 这一批把那条边界从**提示词约定**
（`AGENTS.md` 写着「你就是执行者，照着步骤做」）变成**程序结构**（编排是个 Python
对象，`main` 没有一条工具调用能到达它）。前者是运行时挡，后者是根本够不到。

**做了什么**：

- **新增 `skills/decision-card/scripts/orchestrator.py`（`DecisionOrchestrator`）** ——
  程序驱动 Stage 0→3：占号（在 `open_run` 之前，`decision_id` 从头非空，不留 legacy
  那个「开 run 时还没号」的口子）→ 8 步细粒度状态链（RECEIVED→PREFLIGHTED→
  SNAPSHOT_FROZEN→STAGE1_RUNNING→STAGE1_COMPLETED→RISK_RUNNING→SYNTHESIZING→
  CARD_PERSISTED→COMPLETED）→ Stage 1 五路 fan-out（共用一个 groupId）→ Stage 2
  risk → Stage 3 spawn 判官 → 程序组装并落库。usage 落 `run_events.detail`（不落
  `agent_runs`，L-1）。部分失败/超时：缺席 agent 记 `missing`、照常出卡；只有判官
  没给判断或**零证据**才整体 FAILED。
- **新增 `synthesizer` 判官 agent**（`agents/synthesizer/AGENTS.md` + 配置）——
  一个 `subagents.allowAgents=[]` 的**叶子节点**：结构上没有 spawn 能力。综合判断
  （status/headline/synthesis）从 `main` 手里移到它这里，靠 `outputSchema` 拿结构化
  回复、不解析自然语言（F3/L-13 形状）。🔴 **为什么是新 agent 而不是 spawn `main`**：
  判官若是 `main`，它带着 `main` 的全套 spawn 能力，一个提示词注入就能让它再拉起
  一轮编排（L-14）；叶子 agent 从能力上就做不到。数据不经判官搬运 —— 证据由程序从
  冻结的 verdict 原件直接组装。
- **`bin/biga-card` 收缩成薄 CLI**：五道守卫（熔断→ownership→单实例锁→预算闸门→
  第一次付费调用，**顺序原样**）→ 调 `orchestrator.py` → `spawn_check` + `readback_check`
  → 退出码。等待/传号/合成/看门狗那段 bash 轮询循环整体删除 —— 现在是同步的 Python。
  **不再 spawn `main`、不再抽提示词。**
- **删掉 legacy 粗边 `PREFLIGHTED → CARD_PERSISTED`**（`_contract/run.py`）。它是留给
  「编排整个交给一个被 spawn 的 LLM、中间态对 CLI 不透明」的；Orchestrator 自己驱动
  每一步，唯一使用者消失 ⇒ 删，否则是一条恒不被走的死边（L-7）。批 B 欠的账还清。
- **`ORCHESTRATION.md` 收缩**：删掉 `<!-- PROMPT -->` 提示词块与「怎么执行」（占号/
  等待/传号/合成）—— 那些现在是代码。只留各角色的**指令口径与契约要求**。
  🔴 顺带消灭了它内部 `--decision-id`「到底填不填」的三处自相矛盾（其中「不要填」
  那条产出过混血卡 `BIGA-20260921-014`）：矛盾搬进代码后不复存在 —— orchestrator
  永远显式传占好的号，`synthesize.py` 永远优先用证据自带的号，绝不在有上游号时另分配。
- **`AGENTS.md`（`main` 的契约）**：把「🔴 收到编排提示词时你就是执行者，照着步骤做：
  占号 → spawn 五个 → agents_wait → risk → 合成」整段改成「出卡是程序，你没有一步
  可做；就算有人把那段提示词贴给你也不要照做」。⚠️ **这动了 main 的 AGENTS.md** ——
  通用前置默认「不改 AGENTS.md」，但这一批正文的「main 失去启动管线的能力」要求它：
  留着「教 main 手工编排」的指令，既与本批的中心断言直接矛盾，又是一处 L-3 第二套
  口径（还正是 2026-09-21 那次 4/5 spawn、$0.4 白花的指令本身）。取舍写进评审交接。

**🔴 收缩揪出一个生产级 bug**：`echo "…约 $1.2…"` 里的 `$1` 在 `set -u`（nounset）下
是**未绑定的位置参数**——出新卡以无参数方式跑 `bin/biga-card`，一到这行就
`unbound variable` 当场终止，走不到 orchestrator。总闸 `.biga-card-stop` 开着时会先
`exit 3` 撞不到它，**一旦解除总闸就会咬人**。是 `test_spawn_proof` 的沙盒（排除了
总闸文件）跑出来的。已转义成 `\$1.2`。

**探针（G-1）**：

- **P3 · 守卫顺序前后判据不变**：收缩前后，`test_守卫排在第一次花钱之前`（五道守卫
  按 熔断→ownership→锁→预算→**付费调用** 排序）+ 四个守卫拒绝测试（ownership/锁/
  总闸/预算各 `exit 3`）逐字节钉住通过/拒绝判据。收缩只把「第一次付费调用」的落点从
  `$BIGA agent --agent main` 换成 `orchestrator.py`，顺序与退出码不变。
- **P4 · 全仓无 legacy 粗边引用**：新增 AST 扫描（`test_run_state_machine.py`），
  在所有**非测试** `.py` 里找相邻的 `PREFLIGHTED, CARD_PERSISTED`。删边前先跑 → 命中
  `run.py` 的 `_LEGACY`（红）；删后 → 0 命中（绿）。确认没有调用方没迁完。
- **元守卫跟着付费点一起挪（L-13 修复）**：收缩把付费点从 `$BIGA agent` 挪到
  `orchestrator.py`，而 Adapter 用 `DEFAULT_BIGA`（写死路径）**不读 `BIGA` 环境变量** ——
  「测试里跑出卡必须把 BIGA 换成桩」这条元守卫从此拦不住花钱了。改成桩掉 `orchestrator.py`
  （`_seeded_repo`），并在 `_run` 里加运行时自证（沙盒的 orchestrator 不是桩就当场炸，
  fail-closed 在使用点）。**探针**：把 `_seeded_repo` 的 orchestrator 桩去掉 → `_run`
  的断言当场红；恢复 → 绿。
- **P1（真跑端到端走 8 步链）/ P2（`main` 够不到 orchestrator，是「够不到」不是「被拒」）
  尚未做** —— 需先重启网关让 `synthesizer` 进 roster。记进 `TODO.md`，未做完不算收口。

**🔴 一次开发事故，如实记下**：验证 `$1.2` 修复时手工跑了一次无参数 `bin/biga-card`
（总闸指向不存在的文件 ⇒ 没拦住），它 `timeout 10` 的外层被杀、但 `timeout 840
orchestrator.py` 那个孙进程被**孤立后继续跑**，真的 spawn 了五个 Specialist。它自己的
run/卡写到一次性临时库（已删），但被 spawn 的 Specialist 用**继承不到**我那个
`BIGA_DB_PATH` 的默认库 = 生产 `data/biga.db`，落下 **11 条孤儿 verdict**
（task `BIGA-20260922-001`，无 decision_record）。代价：约一次 fan-out 的真金白银。
`data/biga.db` **git-ignored**（不进仓库、不影响克隆）、只追加（删不掉、也不该删，
L-8）、且是孤儿（不上任何卡）。**教训**：开发期绝不手工跑无参数出卡入口 ——
只有桩掉 orchestrator 的测试才安全。

**残留风险**（详见 `TODO.md`）：P5（Orchestrator 不调 `cancel()`，C-I 那条同序映射
残留原样带下去）；stall_watchdog 现在零消费方（`ask_user` 死锁改由 `runTimeoutSeconds`
兜，但那条是否在 `blocked_tool_call` 下开火未验证，模块与测试保留待裁定）。

**🔴 评审回合一 · 两条阻塞项已修（独立评审揪出，均改代码不改承诺）**：

1. **`orchestrator.py` 加 ownership 守卫**。评审读代码确认：它的 `main()` 直接
   `new_run_context → run()`，零守卫；而 `main` 有 shell 能 `exec python3
   orchestrator.py` **绕过 bin/biga-card**。这让这一批写在 orchestrator.py 头部的中心
   断言（「main 没有一条工具调用能到达它」）当前**是假的** —— 而关掉 L-14 正是 C-II
   的理由。修法是小的、非新机制：`main()` 里调已有的纯函数 `entry_guard.classify_caller()`
   —— agent 会话血缘命中运行时标记 → 拒绝 `exit 3`；人/cron 触发 → 放行。探针
   （= P2 的血缘面）：把守卫关掉 → `run()` 被换成会炸的桩、执行流一跑到它就红；恢复→绿。
2. **`bin/biga-card` 加 `trap` 收养兜底**。评审自己核对因果链发现：孤儿化事故的根因
   **不是** `$1.2` 那个 bash bug（那行在编排调用之前，真崩在那根本到不了 spawn），而是
   外层短 timeout 杀了上层 shell、`timeout 840 orchestrator.py` 孙进程孤立后跑完了整轮
   spawn。第一条教训（桩跟着付费点挪）已落成代码，第二条却只是「以后小心」的承诺 ——
   而本项目的规矩是安全保证必须是 Code Guard。⇒ 编排放后台 + `wait`，`trap` 接住
   EXIT/TERM/INT/HUP，wrapper 一死就 `kill` 掉编排子进程。探针：把 `_reap_orch` 改成
   空操作 → 真杀 wrapper 后子进程孤立存活、测试红；恢复→绿。

评审独立复核为真的部分（记此备查）：952 全绿、11 条孤儿 verdict 逐字段一致且
decision_records 0 行、synthesizer roster 接线、risk 否决强制 status 是既有构造期校验、
`--check` 与 audit 重跑绿。两处次要点（AGENTS.md 越权改、单源测试排除 `docs/design/`）
评审认同不改。总闸 `.biga-card-stop` 由评审重新点上（发现活口子后的临时止血）。
P1/P2 live 待两条阻塞项修完、探针见红后，另开会话再跑。

### 🔴 新增 · 批 C-I：Runtime Adapter + spike 补验

设计文档 §6 批 C 的 `OpenClawRuntimeAdapter` 部分 + §7 剩下未验的三项。
938 条测试全绿（899 → 938，+39：32 条离线适配器测试 + 7 条文档校验参数）。

**为什么**：整个确定性编排升级压在一个假设上 —— Python 编排器能**不经 LLM
轮次**驱动一次 spawn 并拿到同等运行时证据（`sessions_spawn` 是暴露给 agent 的
MCP 工具，CLI 里没有对应子命令）。spike（§7）证明能，但只跑了 **1 次** spawn。
批 C-II 是第一次改生产入口，不能带着三个没测过的假设开工：五路并行是不是真并行、
grant 撑不撑得住 780s、失败报错是不是结构化的。C-I 把这三个「应该」变成「测过」。

**做了什么**：

- `skills/_runtime/mcp.py` —— 传输层。`Grant`（`biga attach --print-config` 铸
  grant、持 token/url、用完删临时 `.mcp.json`，context manager）+ `MCPClient`
  （`initialize` 握手 + `tools/call` 的 JSON-RPC，SSE/JSON 两种响应都认）。
- `skills/_runtime/adapter.py` —— `OpenClawRuntimeAdapter.start / wait / cancel /
  status`，加**状态归一化**。
- `tools/verify/adapter_spike.py` —— 对着真实运行时把三个未知数测掉，运行时升级后
  可重跑复验。🔴 它驱动**真实的** `OpenClawRuntimeAdapter`（不另写一套 MCP 调用，
  否则测的是脚本不是产品 —— F3/L-13），且**会花钱**。

**为什么单独一层 + 状态归一化**：`sessions_spawn`/`agents_wait`/`subagents` 回的
状态是运行时自己的措辞（实测：`accepted`/`queued`/`running`/`done`/`killed`/
`forbidden`），会随运行时版本变。适配器对外只暴露自定义的 `SpawnStatus`
（running/succeeded/failed/timeout/cancelled/**unknown**），原始词只在这一层翻译 ——
运行时改一个词只改一张映射表，不波及编排链。🔴 R-3 落在这里：认不出来的原始词映射到
`UNKNOWN`，**绝不当 SUCCEEDED**（映射表是白名单 `.get(raw, UNKNOWN)`，不是「不是失败就算成功」）。

**两条 API 硬约束（§7，不是设计选择）**：① `collect=true` 无 requesting run 时必须带
`groupId`，fan-out 一批共用一个；② grant 带 TTL、不自回收只到期 ⇒ TTL 必须 ≥ 总预算
（780s），用完主动删 `.mcp.json`。

**cancel 的实测坑**：运行时的取消只认 `subagents.tasks[].taskId`（传 runId / taskName
都被 `Task outside session tree` 拒），而它与 spawn 返回的 runId **无直接关联字段**，
只能靠 `active[i]`（带 runId）↔ `tasks[i]`（带 taskId）同序对应映射。🔴 而 `active[]`
顺序**不是** spawn 顺序（实测先 spawn market 再 emotion，active[] 里是 [emotion,
market]）—— 必须按 `active[i].runId==目标` 定位 i，不能按 spawn 序 index。

**token 用量**：`agents_wait` 带 `usage: {inputTokens, outputTokens}`，随
`SpawnResult.usage` 带出来，但**这一批不写库** —— C-I 不接任何 run，没有 run_events
可写；写库（进 `run_events.detail`，不进 `agent_runs`）是 C-II 的事。在有消费方之前
建写入路径就是 L-1。

🔴 **批 C-I 不接生产入口**：`bin/biga-card` 一个字都不改。把 Adapter 接进
`DecisionOrchestrator`、让程序驱动 Stage 0→3，是批 C-II。

**验证（G-1；live 三项走共享凭据、真实 spawn，已获授权）**：

- **P1 五路并行**（`adapter_spike.py parallel`）：五个 Specialist 共用一个 groupId
  fan-out，实测**峰值同时 5/5 在 RUNNING** —— 真并行（不是排队）。判据是区间相交，
  不是「五个都成功」（成功但排队执行看起来完全正常，那是「延迟其实是正确性 bug」形状）。
- **P3 失败结构化**（`adapter_spike.py failure`）：起一个不存在的 agent →
  运行时 `{status:forbidden}` → Adapter 翻成 `SpawnStartError`，没让裸异常漏出去。
- **cancel 对应**（`adapter_spike.py cancel`）：起两个、取消第一个，实测死的正是第一个、
  第二个仍在跑 —— 同序对应映射正确（`active[]` 当时是乱序的，正好验到这一点）。
- **P2 grant 长跑**（`adapter_spike.py grant-longevity`）：TTL=840s，t=0 真实起一次成功
  （usage 123/5），**实测等 780s 后用同一 grant 复验仍可用**，退出后 `.mcp.json` 已删 ——
  §7-2「grant 不自回收只到期」这句话原来没验过，这次跑了一次真实场景钉死它。
- **P4 状态归一化探针**（离线）：把 `status()` 从 `normalize_status(raw)` 改成
  `return raw`，`test_对外永远是归一化状态而非原始词` 当场报红
  （`assert 'killed' == 'cancelled'`）。已还原。

### 新增 · 批 C 分发提示词：拆成 C-I（Adapter）/ C-II（Orchestrator ★）

批 B 评审通过后，`docs/guide/orchestration-kickoff-prompt.md` 补写批 C 的
开工段——此前一直是「等 B 落地后再写」的占位。

拆成两个会话的理由与批 A 拆成 A-I/A-II 相同：耦合面不同，且低风险的一半
能给高风险的一半兜底。C-I（`OpenClawRuntimeAdapter` + 补验 spike 留下的
三个未知数）完全不碰 `bin/biga-card`，可以独立验证「Python 能不能在不经
LLM 轮次的情况下驱动一次 spawn」；C-II（`DecisionOrchestrator` + 生产
入口切换）是这次升级第一次改动「出卡到底怎么被触发」这条路径——批 A-I/
A-II/B 都是新增或加固既有校验，只有这一步是把生产入口本身换掉，代价与
前面几批不对称，值得单独隔出一轮评审，不跟 C-I 混在一次交付里。

顺带把 TODO.md 里批 C 那一行也拆成两行，并在 C-II 上标注它要还批 B 欠的
一笔账（删掉 `LEGAL_TRANSITIONS` 里 `PREFLIGHTED → CARD_PERSISTED` 那条
legacy 粗边，防止它变成 L-7 死边）。

### 🔴 新增 · 批 B：运行身份 + 状态机（schema v7）

设计文档 §4「身份模型」与 §5「显式状态机」。899 条测试全绿（837 → 899，+62）。

**为什么**：此前只有 `decision_id` 一个身份，它被迫同时承担五件事，实测踩过三次——
飞书事件重投没有幂等键（重投 = 重跑一次决策）、硬超时重试的两次尝试挤在同一个
`decision_id` 上事后分不开、「所有 Specialist 看的是同一份数据」这句话没有
`evidence_set_id` 根本无法验证。⇒ 把身份拆开：`trigger_id`（一次外部请求）/
`decision_id`（一次业务决策）/ `run_id`（一次执行尝试）/ `evidence_set_id`（一片冻结数据）。

**做了什么**：

- `skills/_contract/run.py`：`RunContext` 值对象（第六个契约类型，已登记进
  `test_contract_single_impl.py` 的 `CONTRACT_NAMES`）+ 13 个状态 `RunState` +
  合法转移图 `LEGAL_TRANSITIONS`。状态清单从类属性**派生**，不手抄第二份。
- schema **v7**：`decision_runs`（运行身份头）/ `run_events`（状态转移日志）/
  `evidence_sets`（冻结切片登记，批 D 起有生产方）。三张表**建表时就带只追加触发器**
  —— v4 建 `decision_ids` 时漏过一次（F1），这次不重蹈。
- `skills/_store/runs.py`：`open_run()` / `transition(run_id, expected, next)` / `run_journey()`。
- `skills/decision-card/scripts/run_ledger.py`：bash↔状态机的桥，兼 `--status` 渲染，
  持有 `STATE_MEANING`（消费方知识）。
- `bin/biga-card`：新增 `--status <run_id>`；老路径出卡时**best-effort** 记 run 记录。

**为什么状态是事件溯源、而不是 `decision_runs` 上一个 `state` 列**：`decision_runs`
是只追加的（触发器强制），原地 `UPDATE state` 直接被拒。当前状态由 `run_events`
最新一行给出；`transition()` 靠 `UNIQUE(run_id, seq)` 做 compare-and-set——读到最新
`seq`、断言当前状态 == expected、`INSERT seq+1`，并发两次同转移都写 `seq+1`，唯一约束
只让一个落地。这与 `decision_ids` 用主键冲突占号是**同一招**：唯一约束是唯一可靠的
并发仲裁，「先查再写」永远有竞态窗口。

**为什么 13 个状态而不是评审的 15 个**：去掉 `IDENTITY_RESERVED`（与 `PREFLIGHTED`
是同一瞬间）和 `SNAPSHOT_COLLECTING`（并入 `PREFLIGHTED→SNAPSHOT_FROZEN` 的转移，
中间态无人读）——本系统里没有代码能进入、也没有消费方会读它们，凭空多一个状态就是
一条 L-1 死配置。`NOTIFICATION_PENDING` 推到批 G（outbox 存在之前它没有消费方）。
每个状态都要能指出「谁写它」（能从 `RECEIVED` 经合法转移到达）与「谁读它」
（`--status` 说得清），两条都有结构性测试钉死。

**为什么 `decision_runs.decision_id` 可空**：legacy 路径（`bin/biga-card`）在 RECEIVED
时**还不知道决策号**——它由被 spawn 的 LLM 的 Stage 0 占，`bin/biga-card` 是靠 poll
`MAX(decision_id)` 发现的。批 B 的硬约束是「不改行为 / 不改占号逻辑」，所以不能让
`bin/biga-card` 提前占号。⇒ run 头开在占号之前、`decision_id` 留空，发现号后记进
`CARD_PERSISTED` 事件的 `detail`。批 C 的 Orchestrator 在开 run 之前占号，那时它非空。

**为什么 run 记录是 best-effort**：批 B「新旧并存，不改行为」是硬约束——记账失败
绝不能改变出卡的退出码/流程/成本。所以 `bin/biga-card` 里所有 `_move` 调用吞掉自身
错误。代价是记账静默失效时无人察觉，但那是可接受的：run 记录是**可观测数据**不是
安全守卫，且它的正确性由测试（直接驱动 `run_ledger`/`_store.runs`）保证，不靠 live 路径。
legacy 路径粒度是粗的（`RECEIVED→PREFLIGHTED→CARD_PERSISTED→COMPLETED`，跳过五个
编排内部态）——那五个态由批 C 的 Orchestrator 产生，`bin/biga-card` 观测不到，
**不伪造**（`LEGAL_TRANSITIONS` 里 `PREFLIGHTED→CARD_PERSISTED` 是一条显式登记的
legacy 粗边，批 C 收缩 `bin/biga-card` 时删除）。

**探针（G-1，每道新守卫都见过红）**：

- **P1 并发同转移只有一个成功**（`test_并发同转移只有一个成功`）：8 线程同时对同一 run
  做 `RECEIVED→PREFLIGHTED`，断言恰好 1 个成功、7 个 `IllegalTransition`。常绿。
- **P3 拆掉 CAS 仲裁 → P1 变红**：把 `transition()` 里 `except IntegrityError` 从
  `raise IllegalTransition` 改成 `pass`（吞掉 UNIQUE 冲突 = 退化成无条件写）。
  P1 报红：`应恰好一个成功，实际 8`。已还原。
- **P2 只追加触发器**（`test_UPDATE_decision_runs被拒` / `test_DELETE_run_events被拒`）：
  从 schema v7 删掉 `_append_only("run_events", ...)`，建新库。两条报红（UPDATE/DELETE
  成功），且 F1 兜底 `test_每张表都有只追加触发器` 也报红：`这些表可被改写：['run_events']`。
  已还原。
- **P4 无消费方的状态被抓到**（`test_每个状态都有消费方`）：给 `RunState` 加一个
  `PROBE_ORPHAN`（不进 `STATE_MEANING`、无入边）。三条同时报红——消费方检查
  `这些状态没有读取方（STATE_MEANING 缺）：['PROBE_ORPHAN']`、可达性检查
  `从 RECEIVED 走不到（没人能写它）`、以及「恰好 13 个」的钉子。已还原。

### 🔴 新增 · 批 A-II：契约值对象与不变量（A1/A2/A5/A6/A7/A8 + F-4）

设计文档 §6 批 A 的后半段。A-I 已经把**写边界**堵上（写入前重新构造一遍），
这一批把**类型本身**收紧——很多绕过路径不需要碰写边界，直接改一个已构造
对象的字段就够了；类型层不收紧，写边界重校验就是唯一一道防线，防线破了
就什么都不剩。837 条测试全绿（774 → 837，+63）。

🔴 本批经过两轮独立评审才落地，评审挑出三条：F-5（`MissingItem`
「冻结」之后仍能被 `.code = ...` 改掉，见 A1 小节）、F-6（Card roster
「缺席」该不该硬拒，设计表格与实现口径不一致，见 A5 小节）、F-8
（F-6 修完后的第二意见：「非空即放行」本身还太松，见 A5 之后单独一节）。
三条都已修完才落地成下面这份记录——下方 A1/A5 两节写的已经是**修完
之后**的最终口径，不是最初提交评审时的样子；F-5/F-6/F-8 各自的探针
单独成节，不与原 8 个探针混在一起，因为它们验证的是评审**修出来**的
行为，时间线上确实更晚。

#### A1 · `MissingItem` 的身份从「文本」改成「code」

**为什么**：`replay.py` 反推 `extra_missing` 时用 `m not in from_verdicts`，
而 `MissingItem` 原来是纯 `str` 子类，`==`/`hash` 走的是文本。两条 verdict
各自报「数据源不可用」——一条 code 是 `market.turnover.unavailable`，
另一条是 `supervisor.agent_no_response`，文本相同但不是同一件事——旧身份
语义会把它们塌成一条，回放时**少还原一条缺失项**，「回放悄悄让卡变好看」。

`__eq__`/`__hash__` 改成只认 `.code`，且对非 `MissingItem` 一律返回
`False`（不回退到 `str` 内容比较，否则 `==` 与 `hash()` 依据不同信号，
是另一种静默 bug）。`sorted()`/`in`（子串）/`f"{m}"` 不受影响——它们走的是
`str` 继承来的 `__lt__`/`__contains__`/`__str__`，从不经过 `__eq__`。

⚠️ 副作用：`MissingItem("x") == "纯字符串"` 不再成立。仓库里靠这个成立的
只有 5 处（全在测试里，拿 `.missing` 跟一份纯文本列表比较），已改成显式
取 `.detail` 再比较。

**探针**：造两条同文本异代码的 `MissingItem`，构造「追加 2」描述的真实场景
（`tests/test_decision_card.py::test_同文本异代码的extra_missing不会被反推丢掉`），
临时删掉 `__eq__`/`__hash__` 覆写，确认它精确复现旧 bug 的症状——
`replay --check` 报「不一致」，回放出的 Card 少一条缺失项；恢复代码后转绿。

#### F-5（评审阻塞项）· `MissingItem.code` 在「冻结」之后仍然可写

**为什么**：`MissingItem` 不是 `@dataclass`（它是 `str` 子类，靠手写
`__new__` 构造），A2 那次「把所有契约对象都冻结」的批量整改是按类扫的，
根本没扫到它——`m.code = "supervisor.agent_offline"` 事后照样成功，
不报任何错。这条洞比听起来更致命：A1 刚把 `MissingItem` 的身份从「文本」
改成「code」，就近有一个字段能被绕过冻结原地改掉，等于 A1 立起来的身份
保证当场被同一批次的另一个洞卸掉——`missing_ledger.py` 与
`_check_restated_missing` 都拿 `.code` 当身份在用，事后能改就是「身份可以
被人悄悄换掉而不留痕迹」。`TestVerdictFrozen` 当时测的是"容器"（`missing`
是不是 tuple、`.append()` 是不是被挡住），没有测"容器里那个元素自己"能不能
被改字段——同一个批次里两类不同的「冻结」，只验证了一类。

修法：给 `MissingItem` 补 `__setattr__`/`__delattr__`，统一抛
`dataclasses.FrozenInstanceError`（不用自造异常类型——`AgentVerdict`/
`DecisionCard` 冻结失败时抛的就是这个，保持「所有契约对象冻结失败长一个
样子」）。构造阶段的自我赋值走的是 `__new__` 里的 `object.__setattr__`，
不经过这道覆写，不受影响。

**探针**：`tests/test_contract_behavior.py::TestMissingItemFrozen`
（新增类，4 条）。临时删掉 `__setattr__`/`__delattr__` 覆写，
`test_code不能被重新赋值` 与 `test_code不能被删除` 精确转红
（`Failed: DID NOT RAISE`）；`test_装在tuple里的元素同样不可变`
（造一条真实 `AgentVerdict.missing` 里的元素，确认冻结跟着对象走，
不是只在裸构造时生效）同样转红；`test_构造阶段不受影响`保持绿，
证明修法没有连带堵死合法的构造路径。恢复后全绿。

#### A2 · `AgentVerdict` / `DecisionCard` 冻结，`list`/`dict` → `tuple`/`Mapping`

**为什么**：`tests/test_write_boundary.py` 的 P1/P2 探针（批 A-I）能成功，
靠的正是这个洞——`v.missing.append(...)` 不重跑 `__post_init__`，
一个已经通过校验的对象可以在**不触发任何校验**的情况下被改成非法状态。
`frozen=True` 挡的是重新赋值（`v.verdict = "WARNING"`）；但光 frozen 挡不住
「对同一个可变对象原地 mutate」——`missing`/`evidence`/`warnings`/`verdicts`
换成 `tuple`（没有 `.append()`），`result` 换成 `MappingProxyType`（`[key]=`
直接 `TypeError`）,两者缺一不可。

`__post_init__` 里几处自我赋值（收敛 missing、补 `generated_at`、写
`identity_warning`/`restate_warning`）全部改用 `object.__setattr__`——
这是 Python 自己在 `dataclass(frozen=True).__init__` 里用的同一个后门，
构造阶段用它不违反「冻结」的语义。

⚠️ `to_dict()` 里 `result` 要显式 `dict(self.result)`——`json.dumps`
不认 `MappingProxyType`，不转会在落库那一刻才炸 `TypeError`。

**探针**：`tests/test_contract_behavior.py` 的 `TestVerdictFrozen`/
`TestCardFrozen`。临时把两个类的 `@dataclass(frozen=True)` 改回
`@dataclass`，`test_赋值被拒`（两处）与追加 4 点名的
`test_篡改from_store被拒` 精确转红（`Failed: DID NOT RAISE`）；
`.append()` 系列测试保持绿——证明它们测的是 tuple 类型本身，不依赖
frozen，两道防线各自独立被验证。恢复后全绿。

同时把 `card.py::from_dict` 一处过时的文档注释改了回来——它还在断言
「`save_card` 会传 `from_store=card.from_store`」，那正是追加 4 描述、
A-I 已经修掉的洞；代码早改了，注释没跟着改，是又一次 L-6 文档漂移。

#### A5 · Card roster：拒绝重复 agent；缺席须有缺失项解释才放行

**为什么**：`verdicts=[market的判定, market的判定]` 能直接构造——没有任何
字段说得清「该信哪一条」。但「缺席」不能用同一把尺子量：Phase 1 单 agent
skeleton、agent 掉线时 Supervisor 用 `supervisor.agent_offline` 显式登记，
都是**合法的**缺席场景（`ORCHESTRATION.md` 已有文档化的处理方式）。

⇒ 拆成两条判据，宽严不同：

- **重复**没有合法场景，新卡硬拒（`_check_roster`，与 `_check_identity`
  同一套新严旧宽三段式）。
- **缺席**分两种情况：已建成的 roster（`STANCE_VOCAB` 的 key 集合，
  已有测试 `TestStanceVocabMatchesContracts` 钉死它与 `agents/` 目录
  一致）不全，且 `missing[]` 完全为空 ⇒ 拒绝——缺席必须显式登记
  （如 `supervisor.agent_offline`），不许静默；roster 不全但 `missing[]`
  非空（哪怕只有一条，不要求精确对应哪个缺席的 agent）⇒ 放行。不要求
  精确对应是刻意的：`missing` 项的代码前缀是**发起方**的命名空间
  （`market.*`/`supervisor.*`……），不是缺席 agent 的名字，按文本猜「这条
  missing 说的是不是那个缺席的 agent」是本仓库反复踩过的「按字符串形状
  分类」陷阱（L-13）——这里只问「有没有解释」，不问「解释得准不准」。

只读属性 `DecisionCard.absent_agents` 报告缺席事实，`render()` 里接一行
`已建成 roster 缺席：...`，人看卡面就能看到。

**探针**：临时注释掉 `_check_roster()` 的调用，
`TestCardRoster::test_重复agent被拒`、`test_旧卡里的重复agent只警告不拒`、
`test_缺席且无解释被拒`、`test_旧卡里缺席无解释只警告不拒` 精确转红；
`test_缺席但有解释就放行` 保持绿，证明"重复/缺席无解释/缺席有解释"三条
判据真的互相独立，不是共用一套判断偶然都通过了。恢复后全绿。

#### F-6（评审中等）· 「缺席该不该硬拒」与设计表格字面探针不一致

上面 A5 描述的已经是**最终**口径。第一版实现是「缺席永不硬拒，只由
`absent_agents` 报告事实，不阻断构造」——理由是不分青红皂白一律硬拒
会牵连现有测试里几十处最小化单 verdict fixture，代价与收益不成比例。
评审指出两个问题：① 这与设计文档表格里「缺席也应报红」的字面探针不
一致，是「该不该硬拒」这件事本身没有定论，需要往上交，不是能靠读代码
单方面裁定对错的事；② `absent_agents` 算出来了，但当时**没有任何代码
读它**——零消费方（L-1），「缺席」这个事实只活在对象里，从不出现在
人会看到的任何地方。

裁决：折中方案——上面 A5 写的「roster 不全 + missing 全空 ⇒ 拒绝」。
新判据复用 `_check_roster` 已有的 `from_store` 三段式（新卡严格拒绝、
历史卡/回放只警告），不另起一套宽严规则。

修法牵连的测试比预想的更宽——凡是构造 `DecisionCard` 时只给单个 agent
判定、又不关心 roster 完整性的测试（`test_contract_behavior.py` /
`test_decision_card.py` / `test_verdict_provenance.py` /
`test_verdict_refs.py` / `test_write_boundary.py`，共约 30 处）全部要
补上「满 roster」或「占位 missing」其中之一，是本次 F-6 修复里改动面最大
的部分——但这是机械的测试维护，不是设计问题，设计问题已经在上面裁决完。

**探针**（两处独立信号）：
1. `render()` 接线：临时删掉 `render()` 里 `已建成 roster 缺席` 那一行，
   `test_缺席出现在卡面上` 精确转红（断言在渲染文本里找不到那行）；
   `test_全员到齐时卡面不显示缺席行` 保持绿。恢复后全绿。
2. 硬拒判据本身：临时把 `if self.absent_agents and not self.missing`
   短路成恒假，`test_缺席且无解释被拒` 精确转红
   （`Failed: DID NOT RAISE`）；同一测试类的其余用例保持绿。恢复后全绿。

#### F-8（第二轮独立复核）· 「非空」这条判据本身还留了一个口子

F-6 落地后，把结果再交出去要第二意见——这次挑出的是 F-6 自己的判据
不够严：「roster 不全 + missing 非空 ⇒ 放行」只问「有没有解释」，
不问「解释够不够」。攻击场景可以直接构造出来：5 个 agent 静默缺席，
只挂 1 条跟它们毫无关系的 `market.turnover.stale`，照样能通过——
一条解释就能给任意多个缺席背书。

评审给的修法没有推翻「不比较文本」这一半（那一半是对的，`_check_
restated_missing` 已经因为类似的字符串形状分类吃过亏，见 L-13），
而是指出还有一个不需要比较任何文本、只靠计数就能拿到的信号：

    roster 不全 且 len(missing) <  len(absent_agents) ⇒ 拒绝
    roster 不全 且 len(missing) >= len(absent_agents) ⇒ 放行

对按 `ORCHESTRATION.md` 约定正确操作的路径（每个掉线 agent 各自登记
一条）这条恒真，只在漏报时命中——成本是一行判据，不引入新的失败模式。

🔴 这条不是"堵你们"：**"缺席要不要登记"这件事今天完全由 Supervisor
LLM 的提示词自觉执行**——批 C 的 `DecisionOrchestrator` 会把它从
"LLM 自己判断要不要说"改成程序强制，但那是以后的事。在那之前，
任何依赖"LLM 会如实登记每一处缺席"的假设都值得一道结构性兜底，
这道计数判据就是那道兜底，不是替代批 C。

改动牵连面比预想的更宽——凡是"1 个 agent 到场 + 1 条占位 missing"
这个 F-6 留下的修复模式，在 F-8 之后全部要么补够计数（占位 missing
条数 ≥ 缺席数），要么换成"满 roster，把需要控制的那个 agent 换成
自定义 verdict"（`_roster_with_verdict()`，新增的测试助手）——后一种
更适合那些同时在断言 `len(card.missing)` 精确值、或者要测**后面**
那条铁律 2/否决权检查的用例：给它们硬凑够计数只会让计数本身失去意义，
真正该做的是让 roster 判据不介入，把舞台让给它们真正要测的那条检查。

**探针**：临时把判据退回 F-6 版本（`not self.missing`），
`test_缺席多于missing条数仍被拒`（造 1 个 agent、1 条 missing、5 个
缺席——F-6 版本会放行）精确转红（`Failed: DID NOT RAISE`）；
`test_缺席数与missing条数相等就放行`（5 条 missing 对 5 个缺席）与
`test_missing条数多于缺席数也放行` 保持绿，证明"计数不足才拒绝"
这条边界确实是新加的这一行在守，不是碰巧。恢复后全绿。

#### A6 · `VerdictRef` 上卡：证明「这张卡用的是哪一条判定原件」

**为什么**：Card 只装着 `AgentVerdict` 对象的一份拷贝，不记录它是从
`agent_verdicts` 哪一行读出来的。回放想证明"当初用的原件"和"库里现在
这一行"还是同一份，无从查起。

新增 `VerdictRef(agent, verdict_id, content_sha256, contract_version)`，
`DecisionCard.input_verdict_refs`（历史卡缺省空 tuple，可追加不强制）。
核对函数 `_store.verify_verdict_refs()` **必须**比对
`agent_verdicts.content_sha256` 这一列存量值，不能把 `AgentVerdict`
对象重新序列化再算一遍——这是追加 4（A-I 评审复核）钉死的约束：
`_canonical_dumps` 的格式不是冻结的（A-I 就加过一次 `separators`），
走「重算」会让 A-I 之前落库的 239 条原件集体核对不上且静默失效。
直接复用 `tests/test_write_boundary.py::TestVerdictContentShaIsHashOfStoredText`
钉住的判据，没有另写一套。

`synthesize.py --verdict-ids` 是唯一能建出 `VerdictRef` 的路径（`--verdicts`
读裸 JSON 那条退路没有 verdict_id）；`replay.py` 把 `original.input_verdict_refs`
原样带进重新合成的 Card，否则 `--check` 会把「回放没有重新记 VerdictRef」
误判成组装不一致。

**探针**：造一条哈希故意对不上的 `VerdictRef`
（`tests/test_verdict_provenance.py::test_哈希对不上时报红`），
临时把 `verify_verdict_refs()` 改成恒返回 `[]`，该测试与
`test_verdict_id不存在时报红` 精确转红；「哈希对得上」与「没有 ref」
两条保持绿，证明假阳性/假阴性两个方向都被独立验证。恢复后全绿。

#### A7 · `confidence` 改名 `data_completeness`

**为什么**：六个 skill 算的从来是 `len(result) / 应有字段数`——**字段
覆盖率**，不是模型对自己判断的信心。叫 `confidence` 是在暗示一件
从没发生过的事。`from_dict` 双键兼容（`data_completeness` 优先，
没有则退回 `confidence`），历史卡不需要迁移就能继续读。

**探针**：造一份「只有旧字段名 `confidence`」的字典模拟历史落库格式
（`tests/test_contract_behavior.py::test_历史卡只有confidence字段仍能还原`），
临时删掉 `from_dict` 里的 `confidence` 回退分支，该测试精确转红
（读出 `0.0` 而不是期望值）。恢复后全绿。

#### A8 · 修订血缘约束 + `load_card` 拆成两个 API

**为什么**：实测无约束——修订可以指向另一个 agent、另一次决策的原件；
一条原件可以有多条分叉修订；`load_card(decision_id=乱写, record_id=真)`
会静默忽略 `decision_id` 正常返回。

`save_verdict(amends=...)` 新增代码校验：`amends` 指向的原件必须存在，
且 `task_id`/`agent` 都与新行一致——不同意图（跨 agent、跨决策）的修订
在写入前就被拒，报错直接说清原件是谁、这次是谁。

线性修订（一条原件最多一次修订，不许分叉）**不能只靠应用层"先查再插"**
——`reserve_decision_id()` 当年的教训是这类竞态窗口必须靠数据库唯一约束
仲裁。schema **v6**：`ux_verdict_amends_linear`，`agent_verdicts(amends)`
上的局部唯一索引（`WHERE amends IS NOT NULL`）。设计文档与分发提示词里
「批 B 落成 schema v6」的表述同步改成 v7——迁移列表只许在末尾追加，
不许改动已发布的条目，这里是把设计文档的编号往后挪一位，不是重新设计。

`_store.load_card(decision_id, *, record_id=None)` 拆成
`load_online_card(decision_id)` 与 `load_card_by_record_id(record_id)`
两个 API，不再存在「两个 id 都能传、其中一个被悄悄忽略」这条路；
全仓 9 处调用点（`card_ops.py` / `missing_ledger.py` / `readback_check.py`
/ `phase1_acceptance.py` 等）与 `_store/__init__.py` 的导出同步改名。

**探针**（两处独立信号，各自验证）：
1. 临时删掉 `save_verdict` 里的 agent/task_id 校验，
   `TestAmendLineage::test_跨agent的修订被拒` /
   `test_跨决策的修订被拒` 精确转红（`DID NOT RAISE`）；
   `test_amends指向不存在的原件被拒` 转成裸
   `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
   （证明代码校验原来在做真实的翻译工作，不只是装饰）。
2. 临时删掉 schema v6 的 `CREATE UNIQUE INDEX`，
   `test_同一条原件不许被修订两次` 精确转红（`DID NOT RAISE`）——
   证明"不许分叉"这条真的靠数据库约束兜底，不是应用层的一厢情愿。

均恢复后全绿。

#### F-4 · `readback_check.py` 接入真实调用方（A-I 评审欠的账）

**为什么**：A-I 建的毒行巡检只有测试和文档，没有任何自动路径会跑它——
一条不会红的守卫，比没有守卫更糟：它占着"这件事已经有人管"的位置。

接进 `bin/biga-card` 出卡之后、紧挨 `spawn_check`（同样只读、零成本，
同样是"唯一每次都会跑到的地方"）。退出码必须被接住——不能重蹈
`spawn_check` 当年"调用在、退出码被丢，守卫照样绿"的覆辙。但毒行是
**历史遗留**，不是这次运行的错：新写入的行不可能再变成毒行（A3 的写
边界重校验已经堵死那条路），巡检报红说明库里有一条**更早**的坏行，
不该跟"这次出卡的 spawn 证据链断了"共用同一个信号——那会让人查错方向。
⇒ 用独立退出码 `6`（`spawn_check` 是 `4`），摘要措辞也分开。

**探针**：`tests/test_spawn_proof.py::TestReadbackWiredIntoRealPath`，
复用同一套"造沙盒跑真实 `bin/biga-card`"的机制。把 `readback_check.py`
换成"必然 `exit 1`"的桩，临时删掉 `bin/biga-card` 里对应的
`if [ "$READBACK_RC" -ne 0 ]` 分支，`test_巡检报红时出卡命令的退出码跟着变`
精确转红（`rc=0` 而不是期望的 `6`）。恢复后全绿；顺带确认了退出码
不与 `spawn_check` 的 `4` 撞车、且卡片仍然照常渲染（钱已经花了）。

#### 已知代价（不在这一批修，记进 TODO.md）

`bin/biga-card` 的 `_sql()` 在 `data/biga.db` 尚不存在时会打一屏无害的
`StoreNotInitialised` traceback 到 stderr（`BEFORE=` 那一行用 `readonly=True`
打开一个还不存在的库）。功能不受影响（`BEFORE` 拿到空字符串，语义上
恰好正确），真实机器建库之后不会再触发；是 F-4 测试沙盒时顺带发现的
**既有**问题，与本批任何一项都无关，不顺手改。

### 变更 · 设计文档补「追加 4」，把批 A-I 的评审教训喂给批 A-II

`docs/design/deterministic-orchestration.md` §2 新增「追加 4」，
`docs/guide/orchestration-kickoff-prompt.md` 的批 A-II 开工段同步指向它。

为什么要补这一条：批 A-I 的评审复核（`33fc55a`）挑出两处「收窄类修法方向
反了」，其中两条对**批 A-II 的具体做法**有直接约束——A2（`frozen=True`）
的探针清单必须显式覆盖 `from_store`（实测里唯一被利用过的具体攻击面），
A6（`VerdictRef.content_sha256`）的核对必须走「存量文本哈希」而不是
「对象重算」，否则 A-I 之前落库的 239 条原件会集体核对不上且不报错。

这两条结论已经写进了 A-II 的分发提示词（正文与探针清单都有），但设计
SSOT 本身还没有对应内容——TODO.md 早就写明「为什么这么设计写在设计
文档里，两处都写必然漂」，分发提示词与设计文档各自维护同一件事的解释，
本身就是这条纪律要防的第二套口径。补齐之后分发提示词那句「先读 §2 的
追加 2」也改成了「追加 2 与追加 4」，指向不再缺一半。

### 🔴 新增 · 确定性编排升级的设计方案（第四份外部评审的落地计划）

`docs/design/deterministic-orchestration.md` —— 七批迁移（A–G）的范围、顺序、
判据与出口条件。**只有设计，没有代码。**

#### 为什么要做这次升级

翻 `architecture.md` §9，本项目自己踩出来的五条失败模式里**有四条同源**：

| 事故 | 它其实是什么 |
|---|---|
| L-10 结构化数据经 LLM 转述，`retrieved_at` 全丢 | LLM 在**搬运**本该由程序传递的数据 |
| L-11 身份晚于证据，两次运行合成一张卡 | LLM 决定了**什么时候**分配身份 |
| L-14 出卡递归，187 个会话 / $8.99 | LLM 决定了**下一步跑什么命令** |
| 飞书路径 4 spawn 缺 news、`sessions_yield`、占号在后 | LLM 决定了**调用谁、按什么顺序、怎么等** |

我们已经加的五道护栏（契约改文字 → 单实例锁 → ownership 守卫 → 硬超时 →
`ask_user` 看门狗）全部有效，但**全部只能限制损失** ——
没有一道能让流程本身变成确定的。

> 🔴 护栏已经建齐了。下一步不是第六道护栏，是**把车道拿回来**。
> Program decides workflow. Agent decides judgment.

#### 评审断言先复核，再采纳

10 项可测断言在本仓库跑了探针，**10 项全部成立**。另有三条评审没说的：

- **§22 的后果是「毒行」，不是「脏数据」**：`save_verdict(非法对象)` 成功落库，
  而 `load_verdict()` 会因 `from_dict` 复校验抛错 —— 表有只追加触发器，删不掉也改不掉。
  ⇒ **一次误写让那次决策永久无法回放。** 一个可修复的错误被变成了不可修复的错误
- **§5 已经在回放路径上咬人**：`card_ops` 按 `(code, text)` 去重，
  而 `replay.py` 反推 `extra_missing` 只按文本 ⇒ 实测回放丢掉一条缺失项。
  **回放悄悄让卡变好看** —— 与教程第 8 章当年那个 bug 同形状，
  这次的载体是 `MissingItem` 的 str 身份
- **`ORCHESTRATION.md` 内部自相矛盾**：顶部提示词说 Stage 3「必须带 `--decision-id`」，
  底部参数速查表说「不要填」。后者正是产出 `BIGA-20260921-014`（卡 014、证据 013）
  的那条指令。**L-3 长在了「合并成唯一一份」之后的那一份里** ——
  合并消灭了跨文件的第二套口径，没有消灭同一文件内的

#### 评审的 drop-in 有一处会出事，已改

§23 建议统一用 `separators=(",", ":")`。但 `payload_sha256` 目前不带它，
而 `Evidence.raw_hash` 与 raw 层的对应关系就建在这个哈希上 ——
照抄会**改变所有历史哈希，且失效是静默的**（两串 sha 都「看起来正常」）。
⇒ 拆成两个函数，`payload_sha256` 只加 `allow_nan=False`，分隔符维持现状。

#### 另外三处对评审做了改形

- **不采用 §8 的六个具名字段 `DecisionInputs`** —— 写死 roster，`discipline`
  （裁定 13 故意不建）一上线就要改类型定义。roster 是**配置**，不该是类型。
  改用已验证过的结构性核对（`tests/_consistency.built_agents()`，F8/F10 用的就是它）
- **状态机从 15 个状态收到 13 个** —— 只登记「有代码能进入、且有消费方会读」的状态。
  凭空多一个就是一条 L-1 死配置
- **不可变（§34 第 2 位）与三层拆分推后** —— 有了写边界重校验，可变性的**后果**
  已被挡在库门外；而编排器本来就要重写全部构造点，合并做省一遍

#### 文档规约补第三种形状

跨阶段迁移**按主题命名**，生命周期仍是「阶段」（完成后冻结）。

🔴 为什么不写成 `phase-2.5-<主题>.md`：`test_阶段文档必须带主题` 的判据是
`^phase-\d+-[a-z]`，小数点不匹配；而放宽它的代价更大 ——
`test_一个阶段只有一份设计文档` 按 `phase-(\d+)-` 计数，`phase-2.5-` 压根不会被计入，
于是那条守卫对小数阶段**静默失效**。
**给守卫开一个它看不见的口子，比多一种命名形状糟得多。**

占用 `phase-3-` 也不行：Phase 3/4/5 在裁定、§9、§12、路线图里被按数字引用了几十处，
重编号是 L-6 文档漂移的标准起点。

#### ⚠️ 写这份设计的过程里，本仓库的守卫抓了我两次

| 守卫 | 抓到什么 |
|---|---|
| F7 幽灵文件 | 我在陈述句里点名 `apply_config.py` —— 那个文件不存在。**设计文档提到一个文件，读者会认为这条风险已经有人管了** ⇒ 按约定加 `未建` 标注 |
| F22 测试条数 | 我写的「基线 745」与实测对不上 —— **因为新增那份文档本身让 parametrize 多收了 3 条**。⇒ 跑 `sync_test_count.sh`；设计文档里那个数改标「冻结：开工快照」 |

| 审查第 2 项「Gateway token」 | **第五次「描述规则时把真值抄进去」** —— 我在写「审查会抓到 64 位十六进制」这条探针说明时，把那个令牌参数名原样写进了文档。已记入 `CLAUDE.md` 那张表（原来是四次） |

前两条不是笔误，而是那两道守卫**正好在它们声称守的位置上**。记在这里，
因为本仓库更常见的是相反的情况（L-13：守卫查的地方和它声称守的地方不是同一处）。

第三条更值得记：`CLAUDE.md` 预言过「好消息是审查脚本真的会抓到它」——
**这次预言在写它的同一份文档里应验了**，而且犯的人知道这条规则、
正在引用这条规则、还是抄了真值。
⇒ 分发提示词里因此多了一句：这类探针的内容只写进临时文件，跑完删掉。

### 🔴 修复 · 写边界重校验 + 严格 JSON（确定性编排批 A-I）

`save_verdict` / `save_card` / `save_raw_snapshot` 曾经只在**读**的时候校验
契约不变量（`from_dict()` 重跑一遍 `__post_init__`），写的时候完全不校验。
`v.missing.append(...)` 这类构造后直接改字段的写法能绕过 `__post_init__`：

```
save_verdict(非法对象)   →  ✅ 成功落库
load_verdict(同一行)     →  ❌ ValueError（铁律 1）
```

而 `agent_verdicts` / `decision_records` 都是只追加表（触发器强制）——
**一次误写就让那次决策永久无法回放，一个可修复的错误变成了不可修复的错误**。
当前生产库是干净的（239 条 verdict / 37 张卡，读不回来的 0 条，含 NaN/Infinity
的 0 条）：这是预防性修复，不是救火。

#### 改了什么

三个写函数在 INSERT 之前都加了一道：
`Domain Object → 规范序列化（拒绝 NaN/Infinity）→ 严格重建 → 不变量校验 → DB`。

| 函数 | 重建走的路径 | 为什么这么走 |
|---|---|---|
| `save_verdict` | `AgentVerdict.from_dict(json.loads(blob))` | 没有历史宽松语义要留，直接触发完整校验 |
| `save_card` | `DecisionCard.from_dict(json.loads(payload), from_store=replay_of is not None)` | 🔴 **不能**省略 `from_store` 这个参数——`from_dict()` 原来硬编码 `from_store=True`，如果重建时也用默认值，新卡的严格校验会被写路径自己悄悄降级成历史卡的宽松校验，「新卡严、旧卡宽」的三段式语义就被削平了。为此给 `DecisionCard.from_dict()` 新增了这个仅影响重建路径的可选参数，默认值不变，所有现有调用点不受影响。档位**不**从 `card.from_store` 读——那是评审复核纠正的一处，见下 |
| `save_raw_snapshot` | 不需要新代码 | raw payload 没有契约对象、没有不变量；`sha = payload_sha256(payload)` 本来就在 `connect()` 之前执行，只要 `payload_sha256` 拒绝 NaN，这行天然就是写边界 |

严格 JSON 拆成了两个函数而不是改一个：

- **新的** `_canonical_dumps()`（含 `separators=(",", ":")` + `allow_nan=False`）
  —— 只用于 `card_json` / `verdict_json` 这类**新增**载荷
- `payload_sha256`（raw 层内容哈希）**只加 `allow_nan=False`**，分隔符维持原状

原因：外部评审的 drop-in 建议统一加 `separators`，但 `payload_sha256` 早于它
存在，`Evidence.raw_hash` 与 raw 层的对应关系建立在这个哈希**当前**的输出
格式上——直接改会静默改变所有历史哈希（两串 sha 都「看起来正常」，只是
再也对不上当时存的那个）。`tests/fixtures/payload-sha256-vectors.json`
在改动**之前**用旧实现生成并立刻 `git add`，钉死这一点。

#### 探针（G-1）—— 每一道都见过红

| 探针 | 怎么弄坏 | 红的证据 |
|---|---|---|
| P1 | 合法 verdict 构造后 `missing.append(...)`，`save_verdict` | 暂时删掉重建那一行 → `DID NOT RAISE` |
| P2 | 合法 BUY 卡构造后 `missing.append(...)`（特意避开身份维度，因为 `save_card` 原有的 `foreign` 检查已经管得到身份——用它做探针测不出这一批**新加**的部分） | 同上删掉重建那一行 → `DID NOT RAISE` |
| P3 | 见 P1/P2 —— 把 A3 的重建调用临时注释掉 | 两条测试均变红（`DID NOT RAISE <class 'ValueError'>`），还原后复跑回绿 |
| P4 | `result` 里塞 `nan`/`inf`/`-inf`（覆盖 save_verdict、save_card、save_raw_snapshot 三条路径） | 临时去掉 `_canonical_dumps` 里的 `allow_nan=False` → 6 条经过它的用例变红，3 条走 `payload_sha256` 的用例仍绿（证明两条路径互相独立，不是同一处代码在兜底） |
| P5 | 历史哈希向量：**改动前**用旧实现生成 `tests/fixtures/payload-sha256-vectors.json` 并立刻 `git add`，全程未重新生成 | `test_历史向量逐条吻合` 全程未红过（这正是要的结果——它钉住的是「不变」） |
| P6 | 新增 `tools/verify/readback_check.py`（只读遍历两张表，逐行 `load_verdict`/`load_card`），手工在测试库里裸 `INSERT` 一行「BUY + missing 非空」/「missing 非空 + PASS」的毒行 | `scan_verdicts`/`scan_cards` 各自抓到 1 条，`main()` 返回 `FAIL`；对生产库只读运行确认 0 条毒行 |

真实回放确认（设计文档 §6 A3 明确要求）：在 `data/biga.db` 的一份 scratch
副本上跑 `replay.py --store`——干净卡与「同一件事报了两遍」的历史卡都正常
追加成功；`BIGA-20260919-002`（verdict 写着别的 task_id 的老卡）在
**改动前就已经**被 `save_card` 原有的身份检查拒绝，不是这次新加的回归。
生产库 `data/biga.db` 全程只读，mtime 未变。

#### 新增

- `tools/verify/readback_check.py`：毒行巡检，接入 `_verdict` 退出码三态、
  `test_verify_exit_codes.py::_WIRED`、`test_store.py` 的巡检工具烟雾测试
- `tests/test_write_boundary.py`：P1/P2/P4/P5 的永久回归测试
- `tests/test_readback_check.py`：P6 的永久回归测试
- `tests/fixtures/payload-sha256-vectors.json`：历史哈希向量

#### 外部评审复核（同会话内、代码合并前发现，三条，均已处理）

**F-1（阻塞，已修复）· `audit_public.sh` 的令牌判据收窄方向反了。**
第一版把「Gateway token」的裸十六进制分支改成「必须有 token 标识符 + 赋值
才算令牌」，为的是不再误伤 `payload_sha256` 的内容哈希（见下方 P5 相关段落）。
评审用真实泄露形状逐条对照：`biga attach --token <hex>`（空格分隔，不含
`:`/`=`）、`Authorization: Bearer <hex>`（key 是 Authorization 不是
token）、散文里裸贴一个值——三种都是**真实存在**的泄露形状（前两种就是
`biga attach --print-config` 铸 grant 时终端上会出现的原文），而收窄之后
全部漏检。**方向错了**：安全检查的默认必须是「命中」，例外要自己举手——
与只追加触发器「新表默认就该受保护」、测试计数守卫「默认必须最新」是同一条
原则，反过来收窄正例等于把默认状态从「安全」改成「需要举证才安全」。

⇒ 改法倒过来：**保留**裸 `\b[0-9a-f]{64}\b` 作为默认命中，只在同一行**也**
出现 `sha256` 或 `raw_hash` 这类明确自称哈希的词时豁免。`chk()` 因此加了
一个可选的第三参数（豁免模式），默认不传时九条既有检查行为逐字节不变。
探针：造 9 行覆盖评审列出的全部形状——`OPENCLAW_MCP_TOKEN=`/`"token":`/
`--token <空格>`/`Authorization: Bearer`×2/裸贴一次，以及
`content_sha256 =`/`"sha256":`/`raw_hash=`——前 6 行命中、后 3 行豁免，
与评审的对照表逐行吻合；删除探针文件后复扫回到十一项全绿。

**F-1b（评审在复核 F-1 修法时发现，已修复）· 豁免的粒度是整行，命中的粒度
是一个十六进制串——两者不相等就是漏检面。** `grep -Ev "$3"` 一旦命中就
扔掉**整行**，于是同一行里只要出现过 `sha256`/`raw_hash` 字样，那一行上
的**所有**十六进制（包括一个真令牌）都会被一并放过。例如
`{"token":"<真令牌>","sha256":"<内容哈希>"}` 或者一段解释这条检查本身的
文档散文（"OPENCLAW_MCP_TOKEN 和 sha256 长得一样"）——后者尤其值得警惕：
这正是本仓库已经栽过五次的「描述规则时抄了真值」最容易发生的地方，残余
风险恰好压在经验上最脆弱的一处。

⇒ 豁免的粒度改成与命中的粒度相等：`$3` 不再决定「扔不扔整行」，而是先用
`sed -E "s/$3//g"` 把「自称哈希的键 + 赋值 + 它自己的十六进制串」这一小段
**从文本里抠掉**，再对剩下的文本做正例匹配——同一行里其他独立的十六进制
（不管是不是紧跟在 `sha256`/`raw_hash` 后面）该命中的仍然命中。
`chk()` 因此从「按行豁免」改成「按子串抠除」。
探针：把 F-1 的 9 行与 F-1b 指出的 4 种复合场景（令牌与哈希同键值对、
令牌与哈希分列同一散文句/表格行、CLI 参数注释里顺带提哈希）合成 13 行，
命中的行与预期精确对上（1–6、10–13 共 10 行命中，7–9 共 3 行豁免）；
对整个真实工作区做同样的处理，命中 0 条（fixture 与本文件自身均不受影响）。

**F-2（应修复，已修复）· 写边界的档位不该由对象自己的可变属性决定。**
`from_store=card.from_store` 信任的是 `DecisionCard` 一个尚未 `frozen`
的属性（那是批 A-II 的 A2）：`card.from_store = True` 不会报错。实测验证
过，合法构造一张新卡后依次 `card.missing.append(重述的缺失项)` 与
`card.from_store = True`，`_check_restated_missing()` 会被当成「历史卡」
降级成警告而不是拒绝，`save_card` 因此成功落库——身份维度不受影响（另有
一道不看 `from_store` 的独立检查兜底），暴露的只有重述缺失项这一条。

🔴 评审同时纠正了一处因果表述：这**不是**「A-I 没碰过的旧洞」——`from_store`
一直可变，但在 A-I 之前它只影响**构造期**的宽严；A-I 把它接进了**写边界**
之后，篡改这一个字段的杀伤半径变大了（从「显示层多一条警告」变成「绕开
新加的写边界重校验」）。这句因果要写准，不能记成恰好被顺手发现的旧问题。

⇒ 改成从**调用参数**推导档位：`from_store=replay_of is not None`。
`replay_of` 由 `card_ops.persist()` 的调用方决定（`synthesize.py` 在线
路径永远不传，`replay.py --store` 永远传原始 `record_id`），不受 `card`
对象自身状态影响，篡改 `card.from_store` 因此不再有任何效果。探针：原样
重跑上面那个攻击，`save_card` 正确拒绝；三个真实历史决策（干净卡 / 带
`restate_warning` 的卡 / 带 `identity_warning` 的卡）在 scratch 副本上
`replay.py --store` 逐一重新验证，行为与改动前逐字节相同。

**F-3（应修复，已修复）· `content_sha256` 是「存量文本」的哈希，
不是「对象」的哈希，这个区别要为批 A-II 写下来。**
`_canonical_dumps` 的格式不是冻结的（这一批就刚加过 `separators`）。
批 A-II 的 A6（`VerdictRef.content_sha256` 上卡校验）如果核对时重新序列化
对象再比对，而不是直接比对存量 `verdict_json` 文本的哈希，这一批之前落库
的行会集体核对不上，而且是静默的——两串 sha256 都「看起来正常」。

⇒ 在 `save_verdict` 里补了这条区别的注释，并加了一条钉住它的测试：
`content_sha256 == sha256(存量 verdict_json 文本)` 永远成立，
`content_sha256 == sha256(今天重新序列化对象)` 只在序列化格式从未变过时
碰巧成立。探针：把 `content_sha256` 的计算临时改成对 `blob` 多拼一个空格
再哈希，测试立即报出两串不同的 sha256；还原后复跑回绿。

### 已知问题

- **`tools/verify/readback_check.py` 目前没有任何自动调用方**——只有测试
  和文档提到它，没有一条真实路径（`bin/biga-card` 或 cron 等）会跑它。
  与 `spawn_check.py` 同一个形状：需要一个真实库才有东西可查，而
  `data/biga.db` 只在出过卡之后才存在，pytest 覆盖不到生产库。这是分发
  提示词 P6 没写清楚，不是这一批漏做——留给批 A-II 的分发提示词补
  「接进 `bin/biga-card` 出卡之后那一段，只读、零成本，紧挨着
  `spawn_check.py`」。

### 变更 · 解除 `.biga-card-stop` 总闸（2026-09-22）

2026-09-21 21:47 事故期间加的紧急止血文件，现已删除。**它自己写的两条解除条件
逐条核过**，不是「看着差不多了」：

| 它写的条件 | 核验方式 | 结果 |
|---|---|---|
| 触发源确认停止 | `ps aux` 数 `biga-card` 进程 | 0 个 |
| 闸门真的接进路径 | 读 `bin/biga-card`：熔断 → ownership → flock → `check_budget` → `$BIGA agent` | ✅ 在第一个花钱的动作之前 |

外加一条它没要求、但本仓库该要求的：**顺序由测试钉住**
（`tests/test_entry_guard.py` 的顺序判据，18 条绿）。

⚠️ 文件是 `.gitignore` 的，删除不进版本历史 ⇒ 原文已存档在仓库外。
它记录的根因值得留在这里：

> 原有的「出卡预算闸门」`skills/decision-card/scripts/budget.py`
> **根本不在出卡路径上** —— `bin/biga-card` 与 `reserve_decision_id` 都没调它。

那正是 L-1（写了但没有消费方）与 L-14（闸门装在身份的咽喉点，
而成本的边界比它早一步）合起来的样子。`3d9ce90` 已经把它接进路径。

### 🔴 新增 · SPIKE 通过：Python 能在不经 LLM 轮次的情况下 spawn，并留下同等证据

全案唯一可能致命的未知已经消除。CLI 里没有 `sessions spawn` 子命令，
`sessions_spawn` 只是暴露给 agent 的 MCP 工具 —— 所以「确定性编排器」这个设计
成不成立，取决于 Python 能不能拿到同一条通路。

**实测（2026-09-22）：能。** 用 `biga attach --print-config` 铸一个 MCP grant，
对它直接做 JSON-RPC：

| 判据 | 结果 |
|---|---|
| 产生 `subagent_runs` 行 | ✅ |
| `controller_session_key` | ✅ `agent:main:main` |
| `requester_session_key` | ✅ 同上 |
| `child_session_key` 指向目标 agent | ✅ `agent:market:subagent:…` |
| `payload_json` 里能找到决策号 | ✅ |
| 消耗的 `main` LLM 轮次 | **0** |

`agents_wait` 3.3s 同步返回，带 `result` 与 `usage`（123 in / 5 out）。
⇒ `spawn_check.py` 现有四条判据**逐条满足，无需重新定义**；
设计文档里准备的两条退路（逐个 `$BIGA agent` / 极薄 spawn-only 轮次）都不需要了。

#### 为什么这次 spike 值一次真跑

第一次调用**当场被拒**，错误信息是设计层面的：

    sessions_spawn collect=true requires a requesting run id when groupId is omitted.

Python 客户端天然没有 requesting run（它不是一次 agent 轮次）⇒
**每次 fan-out 必须自己生成 `groupId`**。这是 API 硬约束，不是可选项。
纸面推演推不出这一条 —— 它正是「『应该会……』和『实测是……』之间隔着一次运行」。

另外两条也只有真跑才知道：grant 绑定在会话键上（`--session` 可指定，
于是 `controller_session_key` 能**按 run 区分**，比现在挂在 `agent:main:main` 上强）；
`agents_wait` 在编排器层就返回 token 用量。

⚠️ **但 usage 先不写进 `agent_runs`** —— schema v2 删掉 token 列的理由仍然成立
（唯一真相源是运行时，存第二份必然滞后）。先只在 `run_events` 留一条，
等真有消费方再说（L-1）。

#### 诚实标注没验的部分

五个并行 fan-out（只验了 1 个）、grant 在 780s 长跑里的稳定性（只跑了 3.3s）、
spawn 失败的结构化错误面（只见过一种）。
**这三项不是「应该没问题」，是「没测过」** —— 已写进设计文档 §7 末尾，
列为批 C 开工的第一件事。

### 新增 · 任务分发提示词

`docs/guide/orchestration-kickoff-prompt.md` —— 批 A-I / A-II / B 的自包含开工提示词。

🔴 **开工与评审分不同会话。** 实现者带着「我知道我想干什么」的上下文，
而那正是 L-13 能连续发生十次的原因 —— 判据写完看着对就交了，
没有人以「它会不会红」的视角再看一遍。

⚠️ 批 C–G 的提示词**现在不写**：C 依赖一次尚未做的 spike（Python 能否在不经过
LLM 轮次的情况下产生同等的 `subagent_runs` 证据），D–G 依赖 A/B 的实际落地形状。
**现在写出来的会是一份过期的分发清单** —— 那正是 L-6。


### 🔴 新增 · 事故 Hardening：ownership 守卫 + 硬超时 + `ask_user` 看门狗

两份外部 hardening 建议（递归事故 / `ask_user` 死锁）的落地。
两份都指向同一件事：**当天那两道修复（改契约文字 + 单实例锁）不够。**

> AGENTS.md = Intent / Behavior Guidance
> Code Guard = Safety Invariant   —— 两者职责必须分开。

#### 1. Ownership 守卫 —— 递归的**根因**修复

🔴 **锁拦的是并发，不是越权。** 一次**串行**递归（上一层退完下一层才起）
锁根本拦不住，而那同样是无限递归，只是慢一点、更难看出来。

新增 `skills/decision-card/scripts/entry_guard.py`：顶层入口只能由
人 / CLI / cron 启动，**agent 会话不得启动它**。两个信号，各自补对方的漏：

| 信号 | 判据 | 它单独漏掉什么 |
|---|---|---|
| 进程血缘 | 祖先里有 OpenClaw 运行时 | `nohup cmd &` 被 reparent 到 `systemd --user`，血缘断（事故 195 次里有 15 次是它） |
| 环境变量 | `OPENCLAW_SERVICE_*` 在 env 里 | 网关手工起的话这些压根不存在 |

实测对照：网关进程 env 有 `OPENCLAW_SERVICE_KIND` 等，**人类 shell 一个都没有**。

⚠️ 不要用 `OPENCLAW_PREPEND_PATH` —— exec 工具确实注入它，
   但那条命令**第一件事就是 `unset`**，等入口跑起来时它已经没了。

⚠️ 这一道判不了时**放行**（它是入口不是巡检，拒绝就等于人也用不了），
   但必须把依据打出来 —— 静默放行才是红线 R-3 要防的东西。

#### 2. 防护顺序 —— 比每一道本身更重要

    Trigger → 熔断 → ownership → 单实例锁 → 预算闸门 → 第一次付费调用

顺序错了等于没有：守卫排在付费调用**后面**，每次拒绝仍要付一个 `main` 轮次 ——
事故里那 $8.99 就是这么来的。**顺序由测试钉住。**

#### 3. 硬超时 —— 必须与锁配套

如果持锁的那一次永久卡死，锁会正确地拦住后续所有调用 ——
**于是整个入口永久不可用。**

出卡入口现在有**一个**总预算数（`BIGA_CARD_DEADLINE_SEC`），
而不是 `--timeout 900` 加 `seq 60` 乘 5s 这种「真实上限 1200s、
而没有任何一处写着 1200」。超时后主动 `sessions delete` 收掉会话。

⚠️ `sessions delete` 必须带 `--yes`：它默认会弹**销毁确认提示**，
   在非交互环境里那会挂住 —— 正好又是这一整节在修的那个失败模式。
   （第一版还把签名写成 `--session-key`，实际是位置参数；查 `--help` 才发现。）

#### 4. `ask_user`：不禁用，但不许它造成永久死锁

**禁不掉**，而且不值得硬改运行时。探查结论：`agent` 没有 tool 参数；
`agent exec`（隔离 headless）spawn 不了 specialist；配置级 `tools.deny`
粒度是**按 agent**，会把交互式 `main`（飞书、TUI）的能力一起砍掉。

🔴 所以目标有意识地降级：

> 不要求 `ask_user` 不出现，而是保证它即使出现也只能造成
> **有限、可检测、可审计**的失败。

新增 `skills/decision-card/scripts/stall_watchdog.py`，30s 内认出死锁并收掉，
比等满硬超时快一个数量级。三个条件缺一不可
（`blocked_tool_call` + `activeTool=ask_user` + `recovery=none`）——
少一个就会误杀正常运行，而**误杀的后果是「出卡永远跑不完」，比不杀更糟**。

⚠️ 用 `activeToolAge` 而不是 `age`：后者是整个会话的年龄，
   一次正常出卡跑 170~200s，拿它判会把**每一次正常运行**都算成卡住。

⚠️ 看门狗放在**已有的** 5s 轮询循环里，不新建守护进程 ——
   那会变成又一个「建了但没人跑」的组件（§9 L-1），
   而且那样它会看到**全局**会话，把交互式的 `ask_user` 也连坐。

退出码 `5` = `INTERACTION_UNAVAILABLE`，与超时的 `1` 分开（评审 §15：
不要把所有异常都归成 `FAILED`）。**不自动重试** ——
同一个请求很可能再次 `ask_user`，那是成本循环。

#### 5. 真实运行时 fixture（评审 §25/§26）

`tests/fixtures/stalled-session.log` 是事故当天 journal 的**原文**，
字段名 / 顺序 / 格式一字未改，只替换了两个标识符（公开仓库纪律）。

🔴 数据来源**只有日志**。三处都找过：`session_state_events` 只有
`created`/`child_spawned`/`run_completed`；`sessions list --json` 的 22 个字段里
没有 `state`/`activeTool`；只有 journal 的 `[diagnostic]` 行带 `reason`/`activeTool`。

明知解析日志脆弱。代价**朝安全方向**：格式变了 ⇒ 解析不出来 ⇒ 不开火 ⇒
退化到硬超时。**不会误杀。** 而正因为脆弱，判据必须对着真实那一行测 ——
手造 fixture 只能证明「解析器能解析我自己写的 fixture」。

### 修复

- `tests/test_isolation.py::test_只有一处在走proc的fd表` 的判据与它的名字不符：
  写的是「有字符串以 `/proc` 开头」，名字说的是 **fd 表**。
  于是 `entry_guard.py` 读 `/proc/<pid>/status` 追血缘被判成「第二份 I-1 实现」。
  🔴 那是 L-13 的**假阳**方向 —— 比假阴温和，但同样会逼人去加豁免，
  而豁免会掩盖真的第二份实现。
  ⚠️ 第一版缩窄后变成 **0 命中**（真实写法是 f-string，AST 里 `JoinedStr`
  把 `"/proc/"` 和 `"/fd"` 拆成两段）—— 而 0 命中会让守卫**平凡通过**，
  比假阳危险得多。现在同时认 `Constant` 与 `JoinedStr`。

### 🔴 修复过程自己又花了一次钱

`test_agent调用出卡命令会被拒` 第一版没把 `BIGA` 换成桩。
「逻辑上」它走不到 agent 调用 —— 守卫在前面。
但**探针恰恰是把守卫拆掉**，于是那次探针跑了一次真实出卡：
占号 `-025`、4 个 specialist、**$0.79**。

> 验证 fail-closed 的测试，本身不能有 fail-open 的代价。
> 这句话是同一天写进教程第 19 章的，然后在下一个小时里被违反了。

⇒ 判据不能是「记得加桩」。新增元守卫
`test_测试里跑出卡必须把BIGA换成桩`：AST 扫出所有在测试里启动出卡入口的
调用点，每一处都必须**在自己的 env 里**覆盖 `BIGA`。
只查文件级会被「同文件里另一个测试加了桩」满足 —— 那又是 L-13。

### 修复 · `.gitignore` 的 `*.log` 把测试 fixture 吃掉了

`tests/fixtures/stalled-session.log` 是看门狗解析器的**唯一**真实形状来源，
而它匹配 `*.log` ⇒ **不会进仓库**。

🔴 后果完全静默：本机跑全绿（文件就在硬盘上），克隆下来的人根本没有这个文件。
「文件在硬盘上」和「文件在仓库里」是两件事。

⇒ 加否定规则 `!tests/fixtures/**`，并由
`test_fixture真的被git跟踪` 钉住 —— 判据取 `git ls-files`
（git 眼里的仓库内容），不取 `Path.exists()`（我这台机器上的内容）。

### 变更

- 测试 704 → 745 条
- `usage.md` 补退出码 `3`/`5` 与「出事了怎么一键停」
- `AGENTS.md` 新增「出卡这条路是非交互的，不要等人回答」——
  行为指导，**不是**安全保证

### 已知问题

- **Input Preflight（评审 ASK-01）没做，因为现在无从下手**：
  出卡入口不收任何参数（提示词固定），不存在「缺 symbol」这回事。
  将来飞书接 `/biga 600519` 这类带参命令时才需要
- **恢复出卡前的顺序**（两份评审都强调）：保持总闸 → 重启网关让新
  `AGENTS.md` 生效 → `--list`/`--check` 确认 → 移除总闸 → **只跑一次**真实出卡

### 🔴 修复 · 出卡递归事故（2026-09-21 21:03–21:50）

`architecture.md` §9 新增 **L-14 · 入口与契约互相指**。

#### 发生了什么

同一天实施「编排唯一化」时，`AGENTS.md` 那一节被改成
「🔴 要出卡？跑这一条，不要自己 spawn」并贴出了出卡入口命令。
而那条命令做的事，就是**把编排提示词喂给 `main`** —— 读到那句话的正是它：

    出卡入口  →  agent --agent main  →  新的 main 会话
                                          ↓ 读角色契约
                                    出卡入口  →  …

无条件无限递归。实测 **187 个 main 会话 / 约 195 次调用**，
其中 **92 次是契约里那行命令的逐字复制**（其余是 `2>&1` / `nohup` /
`| tail -100` 这类同一行的即兴变体 —— 这也是最初误判「多个独立触发源」的原因）。

#### 🔴 为什么它没被任何指标发现

Stage 0 的预算闸门**工作了，拦下约 58 次**：

- 当日占号数 **一个都没多**（16 → 16）
- 出卡张数没变
- 没有错误、没有超时、没有告警

**所有业务指标都正常。** 唯一的异常信号是进程数和 token 账单。

递归的每一层都没有任何异常信号 —— **每一层都在「照文档做」。**

#### 闸门的缺口：成本的边界比身份的边界早一步

闸门装在 `new_decision.py` 的 Stage 0 占号之前，那是**身份**的咽喉点。
但出卡入口会先付掉一整个 `main` LLM 轮次，才走到那里被拒。

| | |
|---|---|
| 闸门省下 | ≈ **$48**（58 × 一次 fan-out） |
| 闸门漏掉 | 每次被拒**之前**的那个 `main` 轮次 —— 32 轮 **$8.99** |

> 🔴 「唯一入口」原则是对的；选错的是「哪一处才算入口」。
> 对花钱的事，入口是**第一次花钱的地方**，不是第一次产生事实的地方。

#### 三道防护，缺一不可

1. **契约侧** —— 那一节改写成「🔴 绝对不要运行它」，并画出递归示意图。
   守卫只禁**代码块里的裸调用**，不禁「提到它」：要写「不要做 X」就必须写出 X
   （同一形状在公开仓库纪律里记过四次）
2. **机器侧单实例锁** —— 契约靠不住是本项目第一课（同一天 19:31 那次
   「四个 spawn 无归属」，规则也一条不缺地写在契约里）。
   ⚠️ 用 `flock` 而不是 `BIGA_CARD_DEPTH=1` 这类标记：**env 传不过网关**，
   `agent` 起的会话在另一个进程树里，标记到不了下一层
3. **预算闸门前置** —— 同一个 `check_budget()`（不是第二套口径，阈值仍只有一份）
   在 agent 调用**之前**再跑一次 ⇒ 拒绝代价从 $0.28 变成 $0

#### 止血用的总闸

新增 `.biga-card-stop`：存在即拒出新卡（`exit 3`），在任何花钱的动作之前。
**只拦花钱的** —— `--list` / `--show` / `--check` 照常，
因为事故当中最需要的就是看现状。

为什么是文件而不是环境变量：失控时的触发源是几十个各自独立的进程组，
它们的 env 你控制不了；文件是所有路径都绕不过去的同一个事实。

⚠️ 它和单实例锁都是**运行时产物**，已进 `.gitignore`。
   第一版没加，于是沙盒测试把总闸原样复制进去，三条出卡路径测试全判成「被拦」——
   沙盒要复制的是**代码**，不是这台机器此刻的运行状态。

#### 排查过程本身的三次误判

都值得记，因为它们是同一类：

1. 「闸门根本不在路上」—— **错**。它在 `new_decision.py`，grep 时漏了那个文件
2. 「递归假设不成立」—— **错**。检测器查 `tool_use` / `input`，
   而真实的 transcript 块是 **`toolCall` / `arguments`** ⇒ 扫出 0 条。
   🔴 这正是刚写进 §9 的 **L-13**（守卫查的地方和它声称守的地方不是同一处）——
   **在排查这次事故时又犯了一次**
3. 「配置里的 `streaming` 被删了」—— **错**。它在当前文件和两个备份里都不存在

> 🔴 第 2 条的教训：**自己写的检测器，也要先证明它能命中一个已知实例。**
> 禁网围栏那次是对的（首跑即命中评审报的那一条），这次没做，于是白排查了一轮。

### 新增

- `architecture.md` §9 **L-14**
- 出卡总闸 `.biga-card-stop` + 单实例锁（`bin/biga-card`）
- `usage.md` 新增「出事了怎么一键停」，并把出卡退出码统一到一处
  （原来两处各写一份 = L-3）
- 4 条守卫：契约不许贴入口命令 / 契约必须点名禁止 / 锁真的并发拦得住 /
  总闸不拦 `--list`（事故中最需要的能力，不能被止血措施一起砍掉）
- 测试 700 → 704 条

### 已知问题

- **出卡会话调 `ask_user` 会永久卡死。** journal 实测：
  `stalled session … state=processing age=886s reason=blocked_tool_call
  activeTool=ask_user recovery=none`。
  `agent` 是非交互调用，没有人能回答那个问题 ⇒ 会话一直占着 `processing`。
  这是独立于本次递归的第二个缺陷，**未修**

### 🔴 修复 · 六条「不会红的守卫」（第二轮外部深度评审）

六条发现几乎不涉及业务逻辑 —— **它们全都打在守卫本身上**，而且是同一个形状：

> **守卫查的地方，和它声称守的地方，不是同一处。**

这句话在本仓库已出现十次，终于有了编号 —— `architecture.md` §9 **L-13**。
它是两轮外部评审里复发率最高的模式，而表现每次都一样：
守卫存在、命名正确、测试全绿，坏掉的东西没被抓到。

⚠️ **为什么单独列一条而不是六条**：六处的修法各不相同，但**不修根**的话第七处一定会来。
根不在「写得不够仔细」—— 十次里七次当时都觉得已经很仔细了。
变量是**判据的种类**：字符串存在性、文件名、函数单测，都是会漏的种类。
对照表落在 `.claude/skills/dev-workflow/`，下次直接查。

| # | 发现 | 判据从哪挪到哪 |
|---|---|---|
| 1 | `spawn_check` 跑了但**退出码被丢掉** | 「源码里有这个字符串」→ **行为测试**：桩掉两端走完整出卡路径，断言 `bin/biga-card` 自己的退出码 |
| 2 | 没有 `.git` 时十几条守卫集体 `CalledProcessError` | `check=True` → 降级到文件系统遍历，并**把降级说出来**（`scan_mode()`）；集成测试真的剥掉 `.git` 跑一遍别的守卫 |
| 3 | 声称离线的单测**真的在连新浪** | 「docstring 里写一句」→ **进程内拦 socket**（`tests/conftest.py`，autouse） |
| 4 | 巡检工具的「判不了」与「不通过」**同码** | 裸数字 → `tools/verify/_verdict.py` 唯一定义 + AST 钉住 `main()` 里不许有裸整数 |
| 5 | 已被推翻的口径还活在三处代码里 | 人工记性 → 守卫扫活文档，要求这一句**自己带历史框** |
| 6 | risk 的 fail-closed 是**半硬**的 | 只改标签 → **立刻返回**，载荷里一个派生字段都不留 |

#### 第 1 条：`set -uo pipefail` 没有 `-e`

出卡末尾是裸调用 + 无条件 `exit 0`：

    spawn_check exit 1（有伪造）  →  biga-card 仍 exit 0
    spawn_check exit 2（判不了）  →  biga-card 仍 exit 0

真实语义是「跑了一次检查并打印结果」，不是验收 Gate。
而当时的守卫只查**字符串在不在** —— 字符串一直都在。

现在 `exit 4`（刻意不复用 `2`：**钱已经花了、卡已经落库**，
和「没查成」是完全不同的处境）。卡仍然照常打印 ——
出卡这件事已经发生，藏起来没有意义；要变的是**这条命令的成败**。

⚠️ **验证 fail-closed 的测试，自己不能有 fail-open 的代价。**
第一版闸门测试会真的走出卡路径 —— 守卫一坏就**真的花 $1.37**。
改成两个桩（`BIGA` 落一张真卡进临时库、`spawn_check.py` 按参数退码），全程不联网。

#### 第 3 条：规则写在注释里，等于没写

两个测试文件开头各写着「🔴 这些测试**不许联网**」，而**没有任何东西执行这句话**。
`test_sanity_fence.py` 只桩掉了 `fetch_boards`，同一次 `build_verdict` 并发跑三个 job，
`collect_date` 那一路照样出网 ⇒ 有网时绿、断网时红，而它声称自己是离线单测。

围栏一跑就命中评审报的那一条，**且只命中它**（零误报）——
那是围栏本身最好的验证：复现了一个已知实例，没顺带抓出无辜的。

⚠️ 边界必须写出来：猴补丁**跨不过 `fork`**，`subprocess` 子进程不受保护。
不写这条，下次就会有人以为「有 conftest 就安全了」。

#### 第 4 条：注释是三态的，代码是两态的

评审报的是一处，扫一遍是四处。最说明问题的是 `phase1_acceptance.py`：

    elif tally["PENDING"]:
        # 🔴 PENDING 不算 PASS。这条是本项目的第一条红线。
    ...
    return 0 if tally == {...,"PENDING": 0} else 1   ← PENDING 与 FAIL 同码

紧挨着的两行，一个三态一个两态。
顺带定死了一条更实用的东西 —— **两个非零码的区别是排查方向，不是严重程度**：
`1` 去看代码，`2` 去看数据与时机。最常见的 `2` 是「报告跑早了」。

#### 第 6 条：`verdict` 说「不知道」，`result` 说得头头是道

risk 检测到上游来自别的决策后，**照样把全套指标算完**才改标签：

    verdict: UNKNOWN
    result:  coverage_ratio=1.0, trade_date_consistent=True, tripped_thresholds=[…]
    confidence: 0.9        ← 由 len(result) 算出来的

每个数字都是把两次决策的证据混起来算的，却与正常判定**逐字段同形**。
消费方不止一个（回放、台账、将来的模型分层），**只要有一个读载荷不读标签，fail-closed 就漏了**。

⚠️ 硬 fail-closed 的第一版被契约层当场拒绝（`铁律 3`：result 字段无证据支撑）。
**那次报错是对的。** 绕过它的两个办法都不对 ——
开豁免会在铁律上留口子，`result={}` 会把排查信息一起扔掉。
正解是老实给证据：归属确实是 risk 在 `retrieved` 这一刻看到的，它有正当的 `as_of`。

#### 修复过程自己翻的两次车

1. **探针第一次红，红的是探针自己。** 第 5 条的守卫首跑就报一处违规，
   而那处明明写着「第 7 章推翻了它」—— 是守卫的**词表**少了「推翻」。
   照着一个有 bug 的守卫改代码，会把正确的文字改坏。
   ⇒ **探针第一次红时，先怀疑探针。**
2. **探针没红也是结论。** 同一条守卫的第一版判据是「±6 行内有人澄清过」，
   探针照样绿 —— 修正块还落在窗口里。而那恰好就是被修的那个病
   （旧断言与修正**同时存在**）。判据必须是「这一句自己带历史框」。
   收紧到 ±2 后两个探针都红。**没跑探针，它会以「已修复」的身份留在仓库里。**

### 新增

- `tools/verify/_verdict.py` —— 巡检退出码的**唯一定义**（`0`/`1`/`2` = 过/不过/判不了）。
  六个工具都从这里取码。**不覆盖**契约层的 `AgentVerdict.verdict`（名字像，是两层的东西）
- `tests/conftest.py` —— 全局禁网围栏（autouse）。真要联网加 `@pytest.mark.network`，
  目前**一条都没有**，这正是想要的状态
- `tests/test_no_network.py` / `tests/test_scan_fallback.py` / `tests/test_verify_exit_codes.py`
- `docs/tutorial/19-guards-that-cannot-fail.md` —— 第 19 章
- `architecture.md` §9 **L-13**，以及 `dev-workflow` 里「判据该落在哪里」对照表

### 变更

- `tests/_scan.py::repo_files()` 改为**多后缀**（`repo_files(".py", ".md")`），
  并在 git 不可用时降级遍历。原来需要两种文件的守卫只能调两遍或传空串自己过滤
- `latency_report.py` / `agent_trace.py` / `missing_ledger.py` / `phase1_acceptance.py`
  的「判不了」从 `1` 改为 `2`；`--parallel-check` 不再用 `4`，而是区分
  `False`（真串行 → `1`）与 `None`（判不了 → `2`）
- `skills/_store/db.py::record_agent_run()` 的 docstring —— 它曾经**自相矛盾**：
  P2-3 的修订插在旧结论**上面**、没删旧的，同一段里两句话互相打脸。
  🔴 **改口径要去删旧的那一句，不是在它上面再写一句。**
- 测试 659 → 700 条

### 🔴 安全 · 第 11 项：运行时产物不进仓库（归档 + `memory/`）

不是内容检查，是**范围检查** —— 有些东西的问题不在于「这次的内容有没有问题」，
而在于**它是自动长出来的，下次长成什么样你不知道**。

两类，都是实际在仓库里发现的：

| 类 | 为什么不能进 | 当时状态 |
|---|---|---|
| 归档（`*.zip` 等） | 上面那十项用 `grep -I` **跳过二进制** ⇒ 压缩包里的内容一个字都扫不到 | 2 个已在历史里 |
| `memory/` | OpenClaw 自己写的记忆/做梦产物，其中 `.dreams/session-corpus/*.txt` 是**会话逐字记录** | 8 个已在历史里 |

`memory/` 那条尤其要紧：**今天内容无害不代表明天无害。**
凭据讨论、环境细节都会自动进去，而内容不由你决定。

已 `git rm --cached` 并加进 `.gitignore`，原文留在本机。
⚠️ **已经进过历史的撤不回来** —— 内容抽样核过：
归档是外部评审原文，`memory/` 是行情问答与决策卡，密钥/家目录/应用标识/邮箱五类全 0 处。

#### 为什么不做「归档展开后扫描」

一度写出来了，然后放弃：

1. 外部设计文档会**合法地**讨论 `FEISHU_APP_*` 这类变量名 ⇒ 当场 6 处误报
2. 全历史口径根本做不到 —— `git log -p` 对二进制只输出「Binary files differ」

⇒ **不让它进来**比「进了再扫」好：规则更窄，但**可判定且完整**。

#### `.claude/` 也排除了，但留一个例外

```
.claude/*
!.claude/skills/
```

🔴 那个例外是**必须的**：仓库内的 `dev-workflow` 技能存在的理由是
**覆盖用户级同名技能** —— 后者的 Track D 会去改同机已有实例的 cron 配置，
照做直接破坏 I-1。删掉它，任何人克隆后 `/dev-workflow` 就命中危险的那份。

#### ⚠️ 项数守卫自己错了两处

1. 只数 `^chk "` —— 第 11 项是范围检查，不走正则那条路。
   于是脚本打印「十一项全绿」、文档写「十项」，**守卫两边都觉得对得上**。
   ⇒ 非 `chk` 的检查用 `# audit-check:` 显式标记，一起数。
2. 汉字数字用**单字符表下标**取 —— 到 11 越界回退成 `"11"`，
   而正则也是单字符类，把「十一项」截成「一项」。
   **单字符表只够用到十。**


### 🔴 变更 · 出卡编排合并成唯一一份（方案 C）

飞书用自然语言要卡会出错，第一反应是「改 `AGENTS.md`」。**查下来契约里一条不缺** ——
Stage 0 顺序、五个 specialist 点名、`agents_wait`、「不要 yield」全都写着。

区别只有一个：

| 路径 | 消息 | 实测 |
|---|---|---|
| `bin/biga-card` | 提示词里**又把五步写了一遍** | ✅ 100% 照做 |
| 飞书 | 「出一张决策卡片吧」 | 🔴 4 spawn + yield + 占号在后 |

> 🔴 **契约写了不等于会被遵守。** 同一段知识两份实现，弱的那份会悄悄失效 ——
> 这就是 L-3，只不过这次失效的是**提示词**，不是代码。

⇒ 四个改动：

1. 新增 `skills/decision-card/ORCHESTRATION.md` —— **唯一**的一份，
   361 行编排细节从 `AGENTS.md` 整体搬过来，顶部加一个 `PROMPT` 标记块
2. `AGENTS.md` 的 Stage 0~3（361 行）换成一句「跑 `bin/biga-card`，不要自己 spawn」，
   契约从 480 行降到 140 行
3. `bin/biga-card` **从标记块里抽**提示词，不再自己写一遍；取不到就 **fail-closed**
   （退化成「让它凭契约自己编排」恰恰是实测会出错的那条路）
4. 守卫：编排的四个关键词**同时出现**在同一文件 = 第二份副本，直接红

🔴 **第 2 步最反直觉：删掉正确的文档内容。**
理由与 F7 同形 —— 它没被执行，却让人以为有保障。
**一份不被遵守的契约，比公开承认「编排不由你负责」更危险。**

#### ⚠️ 顺带修掉一个会花钱的测试

验「取不到提示词就停」的第一版测试，直接跑 `bin/biga-card`。
探针把守卫改成 fail-open 之后，**测试当场把真实出卡流程跑了起来** ——
一个坏掉时会花 $1.3 的测试。

⇒ 给脚本加了 `BIGA` 环境变量缝（只为测试），把 agent 调用换成会留痕的桩，
断言**那条昂贵的调用根本没被走到**。

> 验证 fail-closed 的测试，本身不能有 fail-open 的代价。


### 🔴 安全 · R-2 改写成模式级 + gateway 装成 systemd 服务

装服务时发现 **R-2 只覆盖了三分之一的风险面**。

默认单元名 `openclaw-gateway.service` 正被同机已有实例用着（active），
**旁边还有个 `.bak` —— 有人已经覆写过一次**。更隐蔽的是
`OPENCLAW_SYSTEMD_UNIT` 这个环境变量会覆盖 profile 推导出来的名字，
一旦从别处漏进 shell，`gateway install` 就会写到生产那个文件上。

装之前先查了运行时的命名逻辑，确认两件事才动手：
① profile 后缀生效 ⇒ BigA 用 `openclaw-gateway-biga.service`；
② 那个环境变量在 shell / wrapper / env 文件里都没设。
装完立刻核对生产单元的 **sha256 逐字节未变**、MainPID 未变、仍 active。

> 🔴 **当时差点按「R-2 说的是 nvm，这里不是 nvm」放行。**
> 这正是外部复查点名的那个形状 —— 治好这一例，模式还在。

⇒ R-2 从「不许装进 nvm bin」改写成 **「不许占用共享命名空间里的默认名」**，
已知三处列成一张表（nvm bin / systemd 单元名 / 端口），
并写明：**再发现第四处，补进表，别新开一条红线。**

新增不变式 **I-5**，判据进 `isolation.py`（第五项，三态）：
**引用了 BigA 路径的单元，名字必须带 `-biga`** ——
反过来就是覆写的签名。没装服务时报**判不了**，
因为「什么都没装时没占别人名字」是平凡成立的。

顺带：gateway 从 `nohup` 改成 systemd 用户服务 ——
开机自起、崩溃自拉、日志进 journal。之前那份在 `/tmp` 里，登出就没。

> 端口分开了、状态目录分开了，**名字还是共享的**。
> 而顶掉是静默的：文件被覆写，服务照常起来，只是指向了另一套。


### 🔴 新增 · 孤儿 spawn 检查 —— 跑了但进不了任何卡

飞书触发的第一张卡（`BIGA-20260921-021`）少了 `news`。
按纪律**先看工具调用序列、不先改提示词**，查出来的比预想严重：

```
19:31:50  sessions_spawn ×4      ← 只有 4 个（契约要求 5 个）
19:31:59  sessions_yield         ← 契约明令禁止
19:34:11  exec new_decision.py   ← 🔴 占号发生在 spawn 之后
19:34:27  sessions_spawn ×4
19:35:14  sessions_spawn ×1 risk
19:35:52  exec synthesize
```

第三条是 **L-11「身份晚于证据」** —— schema v4 那整套机制正是为了防它。
核实运行时记录：19:31:52 那一波四个 spawn

```
emotion / technical / market   无决策号
sector                         BIGA-20260921-000  ← 临时号，落库会被拒
```

**四个白跑**，约 $0.4 / 2.5 分钟，结果进不了任何卡。

⇒ 新增 `orphan_spawns()`，接进 `budget_report.py`。

🔴 **它与 `spawn_check` 方向相反，缺一不可**：

| 检查 | 问的是 |
|---|---|
| `spawn_check <号>` | 这张卡上的 agent，真的被 spawn 了吗 |
| `orphan_spawns(日)` | 被 spawn 的 agent，最后进卡了吗 |

`spawn_check` **查不到孤儿** —— 它按决策号查，而孤儿的特征恰恰是没有号可查。

#### 根因不是契约缺内容

先怀疑的是 `AGENTS.md` 没写清楚。**查下来它全都写着**：
Stage 0 在 spawn 之前、五个 specialist 点名列出、
`collect: true` + `agents_wait`、「不要同时用 yield」。

真正的区别在这里：

| 路径 | 消息内容 | 结果 |
|---|---|---|
| `bin/biga-card` | 提示词里**又把五步复述一遍** | ✅ 5 spawn + `agents_wait` + 顺序正确 |
| 飞书 | 「出一张决策卡片吧」，只能靠 `AGENTS.md` | 🔴 4 spawn + yield + 顺序反了 |

⇒ **契约写了不等于会被遵守。** 修法是架构性的（飞书的出卡请求不该
直接进自由对话，而应触发与 `bin/biga-card` 同一条路径）——
外部设计文档 §35 / §62 正是这么建议的，现在有实测证据了。
列入待办，不在这次改。

> ⚠️ 消费方检查的第一版又是「查源码里有没有这个词」——
> 把调用换成 `orphans = []` 之后 import 行还在，照样绿。
> 改成 AST 查**真的有调用**。这是同一形状的第 7 次。


### 🔴 新增 · 出卡预算闸门 —— 接飞书之后误触的代价从零变成 $1.37

飞书接通当天就出现了新的攻击面：**手机上一句话 = 198s / $1.37**。
在那之前每次出卡都要人手工敲一条命令，误触代价是零。
2026-09-21 当天出了 18 张卡，约 $23。

🔴 **去重挡不住这个。** 外部设计文档提的「飞书事件去重」防的是
**飞书重发同一个事件**，而人连点两下是**两个不同的事件** —— 去重会放行。

闸门放在 **Stage 0 占号**这个咽喉点上：任何触发路径（CLI / 飞书 /
将来的 cron）要跑一次真实出卡都必须先占号，这是唯一一处绕不过去的地方。

> 守卫放在唯一入口，不是五个调用点。

| 规则 | 默认 | 取值依据 |
|---|---|---|
| 最小间隔 | 400s | **实测单次 172~198s**，留一倍余量。比一次运行还短的间隔没有意义 |
| 当日上限 | 20 | 不是为了省钱，是**让失控可见** |
| 「上一次还在跑」 | 600s 窗口 | 🔴 必须有窗口 —— 见下 |

**为什么「还在跑」必须带时间窗口**：库里有 3 个当天上午的占号来自冒烟测试，
它们**永远不会**出卡。不设窗口的话闸门会被它们永久关死 ——
而一个永远拒绝的闸门等于逼所有人常用 `--force`，也就等于没有闸门。

配套 `tools/verify/budget_report.py`（当日用量与闸门状态）。
🔴 它存在的一半理由是：**报错里指的那条路必须真的存在** ——
F7 刚一次扫出 6 个幽灵文件。为此专门有一条测试从报错文本里
抽出路径去核实它存在。

#### 三个设计细节，都是被测试逼出来的

1. **被拦时不占号** —— 否则连点十下就把当日配额用完了，而一张卡都没出
2. **闸门只读** —— 「检查一下能不能跑」本身不该消耗配额，这是这类闸门最常见的设计错误
3. **空库放行** —— 全新环境里库还不存在是**正常状态**。
   外部评审 P2-2 已经为 `reserve_decision_id` 修过同一件事，
   而这个闸门排在它**前面**，于是把坑原样重踩了一遍

> ⚠️ 第 3 点的第一版还写错了层：`try` 包在 `db.connect(...)` 外面，
> 而它是 contextmanager —— 异常在 `__enter__` 时才抛。
> 测试原样报同一个错，那是探针在说「你挡的位置不对」。


### 🔴 修复 · 同一个生产方把同一句话说了两遍（裁定 15 的新实例）

来源：`BIGA-20260921-021`，**飞书触发的第一张卡**。

```
risk.upstream.coverage_incomplete   Stage 1 缺席：news，这些领域的风险本次没有被看过
risk.coverage.insufficient          Stage 1 缺席:news,这些领域的风险本次没有被看过
```

🔴 **注意标点**：全角 `：，` vs 半角 `:,`。后者是 LLM 重打出来的 ——
这正是 L-10「不让 LLM 搬运结构化数据」要防的形态，而且**它自己留下了指纹**。

根因在契约：`agents/risk/AGENTS.md` 指示 agent 在覆盖不足时追加
`risk.coverage.insufficient`，而 skill 早就报了同一件事。
⇒ 契约改成「只改结论，不加缺失项」，并说明什么时候**才**该追加
（真有 skill 没覆盖到的判断，那是你的判断，不是复述）。

#### 判据为什么不是「代码前缀」

先扫了一遍：**六份 specialist 契约全都**指示 agent 加自己命名空间下的代码。
按命名空间归属判会把六条全误伤 —— 而那多数是合法的：
skill 报「数据没取到」，agent 报「我据此判断不了」，那是两件事（S-2）。

真正可判定的信号是**这两条说的是同一句话**。
⇒ 判据落在契约层的卡上：**(命名空间, 归一化正文) 重复即拒**。

⚠️ 第一版只看正文，当场把一条既有测试判红了 ——
`test_同文本不同代码不合并` 钉的是一个**合法反例**：
两个不同的源各自「数据源不可用」是两件事，合并会让统计少一条。
加上命名空间这一维才两边都对。

新卡直接拒，旧卡只警告（「新卡严格，旧卡可读」）——
库里已经有这样的卡（021 就是），回放不该因此崩掉。

> ⚠️ 探针第三次没红是**字节码缓存**，不是守卫失效 ——
> 教程 17 章第 13 条记过：「改一下、立刻跑」是探针的节奏，
> 也是撞上缓存的节奏。清 `__pycache__` 后两条正确报红。


### 🔴 安全 · 审查脚本补第 10 项：IM 应用凭据

**在凭据进入本机之后、代码引用它之前加的** —— 不是事后补。

飞书配好当天先加检查，再动代码。理由是仓库 Public ⇒ **推即发布**，
而 `appId` 这类东西的特点是**看起来不像密钥** ——
它是个标识符，很容易被当成无害的配置贴进文档。

覆盖五类：应用标识前缀、两个应用级环境变量、两个事件校验用的密钥、
密钥的明文赋值形态，以及自定义机器人的 webhook 地址前缀。

⚠️ **这里故意不抄具体的变量名** —— 见下。

已核：当前 `appId` 在**工作区和全部 git 历史里都不存在**；
`appSecret` 存在 OpenClaw 密钥库里（配置只有引用 id），未落盘。

#### 🔴 又一次「通过是因为它什么都没查」—— 这次是当场抓到的

顺手给「文档里写的项数」也上了判据（`八项`/`九项` 当时有四处没跟上，
与测试条数是同一个形状）。**第一版守卫一处都没匹配到**：

```
CLAUDE.md 里写的是  **十项**检查
                    ↑ markdown 的 ** 把「项」和「检查」隔开了
```

删掉第 10 项之后它照样绿。补了「先剥 markdown 强调符」+
「没找到 ≠ 通过」两条，两个方向都验过会红。

这是本次会话第 6 次同一个形状 —— **守卫查的地方，和它声称守的地方，不是同一处。**

#### 🔴 然后新检查做的第一件事，是抓住我自己

上面这条 CHANGELOG 的初稿把要防的那些变量名**原样抄了进去**，
提交时当场被第 10 项拦下：

```
⚠️  IM 应用凭据 —— 1 处
CHANGELOG.md:26  覆盖：`cli_` 开头的应用标识、…
```

这是 `CLAUDE.md` 已经记了三次的那个形状的**第四次**：
**描述一条「不要写 X」的规则时，把 X 抄了进去。**

前三次分别发生在 CHANGELOG、`TODO.md`、开发流程文档里；
这一次也是 CHANGELOG。⇒ 已改成只说**类别**，不说实例 ——
读者需要知道的是「有哪几类」，脚本才需要知道具体正则。

> ⚠️ 顺带记一条更简单的解法：`TODO.md` 那句改成「这些检查」之后
> 就**不需要**这条守卫了。**最好的防漂移是不写那个数。**


### 变更 · 裁定 14 改写：`main` 跟随开发主线

原文是「`main` 只保留已验收状态」。Phase 2 出口条件 5 ✅ / 2 🔶 / 1 ⬜ 时
合并 `phase2`，与它直接冲突 ⇒ **改裁定，而不是默不作声地违反它。**

改的理由：仓库 Public，`main` 是访客唯一会看的分支。
让它长期落后 59 个提交，展示的是一份**过期的自我描述** ——
那比「进行中」更失真。

🔴 **保留原裁定真正想防的那半句**：徽章与验收数字必须始终描述一个
真被测过的提交。未达成就写 `⬜`/`🔶`，不写 `✅`。
本次合并不造假徽章 —— 端到端那条已转黄写着「盘后 198s」，
出口条件表里 ⬜ 与 🔶 都摆着。

> 一条裁定挡路时有两种走法：改它，或者绕过它。
> **只有前者会留下记录。**


### 新增 · `tests/_consistency.py` + L-12 + dev-workflow 第五问 —— 把「模式还在」的三条补成模式级

外部评审追问"六、修复方向建议"里那句「与其逐条单独修，不如先解决这个
形状」具体怎么做。抽出唯一实现 `tests/_consistency.py`
（`assert_matches_source` / `assert_subset_of_source` / `built_agents()`），
把 F1/F9/F12 各自发明过一次的「从权威源派生/比对」收成一份可复用的调用，
然后把 F8 / F10 / F16 从「实例修好，模式还在」补成「模式级」（详见
`TODO.md`）：

- **F8**：新增 `set(STANCE_VOCAB) == built_agents()`。探针：建一个
  `agents/discipline/` 目录（不改 STANCE_VOCAB），当场报红。
- **F10**：`_MUST_COVER_BUILT` 把"理应覆盖已建好 agent 的配置字段"数据化，
  parametrize 到字典本身；加第三个这样的字段只需要加一行。探针：复现
  原始 news 漏出 bug + **另造一个假想的第三个字段**，两者都被抓到。
- **F16**：从 `_source_result_classes()` 结构性派生"哪些源没有服务端日期"，
  反查每个 calc 脚本是否消费了这些源却没登记进 `_LIVE_FIELDS`（字段级
  仍手写，**文件级**结构性核对）。探针：让 `technical_calc.py` 引用
  `BreadthResult` 而不登记，当场报红。

🔴 **过程本身又验证了一次这条原则要对自己也成立**：最初想让 F10 的三个
配置字段"两两完全相等"，实测直接报错——`agents.entries` 天然比另外
两个多一个 `"main"`（entries 回答"谁注册了"，另外两个回答"main 能碰到
谁"，语义不同，不是 bug）。删掉那条方向搞错的测试，因为"每个字段都必须
覆盖已建好的 agent"本身已经完整复现 F10 要防的事故。

另在 `architecture.md` §9 补 **L-12：测试双打得太靠上**——F3 第一版的
回归测试 mock 的正是"按决策号过滤"这段逻辑本身，于是那段逻辑从没被
真正执行过，是这张失效模式表里第三条在本项目自己踩出来的（前两条是
L-10/L-11）。`.claude/skills/dev-workflow/SKILL.md` 加第五问：新增清单
类东西时，先问"别处有没有一份该跟它一致的孪生"。

### 新增 · 第 18 章 + 两条**反向**文档守卫

问了一句「Phase 2 的功能都反映到设计和教程里了吗」，**机器扫出来是没有**：

```
sanity.py  spawn_check.py  server_as_of  StoreNotInitialised
    ↑ 四样在任何设计文档里都找不到，而它们全是当天刚建的
```

上守卫之后又扫出 **9 个入口**（四个数据源、`http.py`、`tradetime.py`、
`runtime.py`、`schema.py`、`missing.py`）同样一字未提。

⇒ 新增 `test_每个入口都在设计文档里被提过`。它与已有的「幽灵文件」检查
**方向相反，缺一不可**：

| 方向 | 防的 |
|---|---|
| 文档 → 代码 | **幽灵文件**：说了有，其实没有（F7） |
| 代码 → 文档 | **孤儿组件**：做了，但没人知道 |

⚠️ 两条守卫上线当天就互相咬了一次：补文档时把路径写成 `_store/schema.py`
（相对 `skills/`），幽灵检查按仓库根解析 ⇒ 当场报红。**这是它该做的。**

补出来的内容不是「一行文件名」，是 `architecture.md` 新增的
§6.1（`server_as_of` / `sanity.py` 两条统一接口）、
§6.2（四个数据源各自的脾气）、§6.3（三个支撑模块）。

#### 第 18 章：请外人来拆

第 17 章本来是收官，**它写完的当天两份外部评审就到了**。
新开一章记这段，因为它讲的是「自检为什么不够」：

- 对抗性评审提示词的三个设计决定（第一句必须是**警告**，不是任务）
- 23 条发现归成七类，出现最多的是「手工维护的名单不会自己长大」
- 🔴 **修复过程本身翻了 5 次车，5 次都是探针抓的、0 次是我看出来的**
- 被外部复查**推翻**的那一条，以及它为什么能通过我自己写的测试

第 17 章是过程文档（写完即冻结）⇒ 只追加「⏩ 后续变动」指针，不改原文：

> 🔴 「收官章」写完当天就被推翻了一部分，这件事本身该留在这里。
> 自检说一切良好，不等于一切良好 —— 它只等于「我没想到别的」。

### 变更 · `architecture.md` 那句「四张表全部只追加」是假的

原文写「四张表」，而当时已经有**五张**，且新加的 `decision_ids` 恰恰是
唯一没有触发器的那张。⇒ 改成五张，并把判据从「数几张表」换成
扫 `sqlite_master` 的实际表名 —— **手写的数字只会在下一次加表时再错一遍**。


### 🔴 修复 · 外部复查推翻了 F3 的修复 —— 已重修

复查逐条核过 23 条，结论是 **15 模式级修复 / 7 实例级修复（模式还在）/ 1 诚实标注修不干净**。
上一版台账写的「23 / 23 全部处理完」**不成立**，已按复查口径改写。

#### F3 的洞：做了自动化，但不按决策号绑定

第一版覆盖了全部 6 个 specialist、接进了出卡流程 ——
**却只问「这个 agent 名字有没有出现在最近 50 条运行时记录里」**。

实测复现：

```
伪造决策号 BIGA-20260921-901，用合法 API 写 6 行 agent_runs
→ spawn 核验：6 个 agent 两份独立记录都齐   ✅ 通过
```

伪造的号**蹭上了别的决策的真实记录**。而 `bin/biga-card` 正常使用就会反复运行
⇒ 这个条件在真实机器上**几乎总是成立**，不是刁钻场景。

⚠️ 最讽刺的是：决策号本来就**明文写在 `payload_json` 里**（Supervisor 的 spawn
指令带着它），只是没被拿来做绑定。

重修三处：按决策号过滤（顺带去掉 `LIMIT 50` —— 按号取不该有条数上限，
否则一天跑得多了早先的决策会悄悄查不到）；`child_session_key` 按**段**比而非
子串（否则 `news` 会被 `newsflash` 顶替）；区分「读不到运行时库」（判不了）
与「库能读、本决策零记录」（**伪造**）—— 前者会被忽略，后者不会。

顺带发现第二个 bug：伪造号在 `agent_runs` 还没写入时，第一版打出
「0 个 agent 两份独立记录都齐 []」然后**退出 0** ——
「无事可查却报绿」，出现在一个专门为了防造假而写的工具里。

> 🔴 **而第一版的测试结构上不可能发现它。**
> 它去 fake `_runtime_spawn_records` 本身，正好把「按决策号过滤」那段整个绕过去。
> 现在改成造一份**真的 sqlite**，让被测代码走完整查询路径。
>
> **假的东西造得太靠上，测的就是自己写的假货，不是产品代码。**

#### F7 的后半段：文档修了，风险本体一条测试都没加

上一版把整条 F7 标成「✅ 已修」—— 而它有两半：
文档失真那半结构性修复了，**6 条阈值里 3 条从未被测试触发过**这半没动。
复查指出这容易让人以为风险已经解除。

已补：`THRESHOLDS` **parametrize 到自己身上**（新加一条自动纳入 ——
写死六条的话第七条又会是下一个 F7），正反两向，
外加用 AST 核对上游是否还在产出那个字段。
探针验过三种失效：阈值侧改名、**上游侧改名**（最隐蔽的那种）、判定整个失效。

🔶 残留如实标注：这是**构建期**可达性，不是 L-7 要的「每日巡检」。

#### 台账改成三档

`TODO.md` 不再用「全部处理完」这种说法，改成
✅ 模式级 / 🔶 实例级（模式还在）/ ⬜ 诚实标注修不干净，
并逐条写明「堵上的」与「没变的根子」。

> 🔴 复查最该记住的一句：**修复清单类问题最容易的坑就是「治好这一例，模式还在」——
> 而这次修复过程本身又踩了一遍。**


### 变更 · 首次盘后端到端 —— 交易日首次对齐，但预算超了

`BIGA-20260921-020`（17:18，当天日线发布之后）。

**好消息：架构问题的前置条件解决了。**

```
上游交易日是否一致 = True      上游自报的交易日 = 20260921
Stage 1 覆盖率     = 1.0       缺失项 3（此前 8~9）
risk               = PASS / 放行
```

六个 Agent 首次报同一个交易日，`risk` 第一次真的裁决 ——
而不是因交易日分裂弃权。今天 7 个 commit 的改动也第一次经过真实运行，
卡面上能直接看到 F4 生效：日线证据 `as_of 15:00`、
无日期端点 `as_of 17:16`，同一张卡上两种时刻各归各的。

🔴 **但别把前置条件当成达成。** 条件 3 要的是一次真实**否决**，
「放行」不是。障碍从「risk 根本没法裁决」变成了「需要等一个够极端的交易日」——
两件不同的事，只是在状态表里长得一样（都是 ⬜）。

**坏消息：198s / $1.37，超了 180s 的预算。**

原因定位得到：收盘后一小时快讯 **184 条**（盘中约 70 条），
窗口同样是 60 分钟，而 `news` 的 prompt 长度直接跟着条数走 ——
单轮从 78.3s 涨到 100.0s，成本 $0.42，是最大的一笔。

> 🔴 **三次改预算的共同点：旧数字不对，都是因为测量窗口没覆盖到某个情形。**
> 第一次是「一个 agent 都还没有」，第二次是「休市日」，
> 第三次是「所有实测都在 15:00 之前」——
> 与 F4 / FIX-03 躲过一整天是同一个成因。

⬜ **这次没有顺手把预算调到 210s。** 只有一次盘后数据，
回答不了「什么时候它不该红」，而那是前两次改预算时反复强调的必答问题。
留作待裁定（`TODO.md`），先攒实测。


### 新增 · `tools/verify/sync_test_count.sh`

F22 的守卫每加一条测试就会报红，而手工同步那个数在**同一次会话里错了两回**：
一次取到空串把数字擦成了空，一次用了 `N passed` 而守卫要的是 collected
（差一条 skipped）。

⇒ 判据只有一个来源：**守卫自己报出来的那个数**。
脚本跑完会再验一次，还对不上就说「有一处没被覆盖到」而不是假装成功。

> 一个「每次改动都要手工同步」的数字，最后一定会不同步 ——
> 这正是 F22 本身的成因，手工修它只是把成因重演一遍。


### 🔴 修复 · F3 —— 「Specialist 真的被调用过」此前没有任何机器在管

根 `AGENTS.md` 写着：

> 每次调用都会在 `agent_runs` 表留下一行。**那张表是唯一凭证** ——
> 你在回答里声称调用过，不算数。

**这句话本身不成立。** 写那行的代码只是把 verdict JSON 自带的 agent 字段
抄进表里，`started_at`/`finished_at` 还被写死成 Card 生成的同一时刻。

⚠️ 于是这两种情况产出的 Card 与 `agent_runs` 记录**逐字节相同**：

1. Supervisor 真的 spawn 了 market
2. 有人手工跑 `market_calc.py --task-id …`，把 verdict_ref 喂给 synthesize

两份都是 BigA 自己写的 —— **自己写的东西证明不了自己。**

唯一能做真区分的核验（读 OpenClaw 运行时自己记的表，那张表 BigA 业务代码
碰不到）此前 ① 硬编码只查 `"emotion"` 一个名字，Phase 2 新增的五个完全没有；
② 只能手工调用，不在 pytest、不在任何 hook 里。

⇒ 判据改为取自契约的 stage 名单（**名单会随 agent 增加自己长大**），
并接进 `bin/biga-card` —— 那是唯一每次出卡都会走到的地方。
放进 pytest 不行：它需要一次真实运行。
实测对真卡 `BIGA-20260921-019`：6 个 agent 两份独立记录全部对上。

根契约里那句话也改了：`agent_runs` 是**执行账本，不是 spawn 证明**。

> 🔴 **第三次被探针抓到假守卫。**
> 「核验不依赖 BigA 自己写的表」这条用 AST 找表名字面量 ——
> 而 docstring 里也提到了那个表名，于是把查询改成读 BigA 自己的表，
> 测试照样绿。加了「只看像 SQL 的常量」才真的会红。
>
> 三次的形状完全一样：**守卫查的地方，和它声称守的地方，不是同一处。**


### 修复 · F11 / F12 / F13 / F14 —— 守卫的边界，说清楚或者补上

**F11 · 静态扫描能被绕过，但真正的防线是 `from_dict()`。**
评审做了个关键的补充实验：把违反铁律的数据用裸连接直接 INSERT 进库 ——
写入不报错；但正规 `load_verdict()` 读回来时**立刻**抛出引用具体铁律编号的
ValueError。`from_dict()` 每次反序列化都重跑一遍校验，是真实存在的第二道防线。

> 🔴 **前提是所有消费方都老实走 `from_dict`。**
> 评审查了 `synthesize.py` 与 `card_ops.py`，都干净。
> 而上守卫时又扫出一个它没查到的：`missing_ledger.py` 当时直接
> `json.loads(card_json)`，绕开了那道防线。
>
> **「目前检查过的路径都是」这种前提，得有机器守着才站得住。**

⇒ 不去追平静态扫描的盲区（逐键赋值、`type()` 动态建类是正常 Python idiom，
那会变成军备竞赛），改为守住**那道后备防线的前提**。

**F12 · 这条没有后备。** `__import__("sqlite3").connect(path)` 拿到的是一条
真实可读写的连接，不经过任何契约层。同样不追平「任意字符串拼接」（判定不了），
换一个可判定的判据：`importlib.import_module` / `__import__`
**在业务代码里本来就不是一个自然会写出的模式** ⇒ 默认禁止，例外举手。

上这条时逮到一处真实的：`_sources/tradetime.py` 写着
`__import__("pathlib").Path(...)` —— 省一行 import，用的正是同一种手法。

**F13 · 8 词字面量黑名单，同义改写即可通过。**
这条**没有彻底的解法**（目标是开放式自然语言，不像开关名有稳定锚点），
所以做了两件事：① 如实在测试里写明它能挡什么、挡不住什么；
② 补一条**可判定**的兜底 —— 教程里不许出现同机另一实例的具体路径与端口。

> 即使有人用没被列进黑名单的说法，只要他要讲清楚「怎么和它共存」，
> 就绕不开写出具体路径或端口 —— 而那才是真正不该进教程的东西。

新检查当场扫出 **4 份教程里的真实泄漏**（具体端口、具体 state 路径）。
全部改成占位符与中性措辞，顺手把一条通用原则显式化：
**守卫写在受害者那边**（加害方可以被删掉、重装、换人维护，
而受害方才是那个需要一直知道「我还安全吗」的人）。

**F14 · `--check` 的名字暗示的范围比它实际验的大。**
评审把 headline 改成「外星人今日登陆陆家嘴」，`--check` 照样报一致 ——
`status`/`headline`/`synthesis` 在该模式下 100% 照抄原卡，
两侧本质是同一个纯函数喂入被证明相等的输入，**数学上必然相等**。
⚠️ 但它并非测了个寂寞：把缺失项的计算改坏之后它正确报了不一致。
⇒ 不改行为，把边界**印进输出本身**（而不是只写在文档某处）：
「验的是组装管线无损，不是这个结论能被独立复算出来」。


### 修复 · F8 / F9 / F10 / F17 / F23 —— 五条「清单不会自己长大」

评审自己归纳的模式观察，值得原样记下来：

> **手工维护的名单**（测试的 parametrize 列表、运行时配置的 allow 数组、
> 架构文档里的组件清单、同一不变式的重复实现）**不会随 agent / 组件数量
> 增长而自动同步**。而且这个形状里至少有两处是「一个坑已经真实炸过、
> 专门补了守卫，另一处结构相同的地方至今没人管」。
>
> 这比任何一条单独的发现都更值得记住：清单类约定第一次踩坑后，
> 团队本能地去补**那一个**，而不去问「还有哪些地方是同样的结构」。

**F8 · 词表校验「命中才查，命不中就放行」。**
`agent="Market"`（大小写 typo）或 `agent="discipline"`（还没登记）
一律静默退化成「1~16 字任意词都收」。
⇒ 未登记就是错误，**不是「暂时放宽」**。
原来唯一的交叉校验用 `parametrize("agent", sorted(STANCE_VOCAB))` ——
天然只测「已经正确登记」的，覆盖不到「忘记登记」那个分支。
顺手把 `amend_verdict.py` 里抄了一遍的同一段逻辑（连盲区一起抄的）
改成只翻译契约层的报错。

**F9 · `news/AGENTS.md` 少了其余五份都有的那句话。**
真实 trace 两次为证：news 把 task_id 当成 `--ref` 传进去，报错后
去读 `--help` 和源码，每次多花 2~3 次工具调用 ——
与契约自己强调的「不要为了确认参数去读源码」形成直接讽刺。
守卫改了两处：清单从**扫目录**得来（新建 agent 自动纳入）；
判据从「提到了 verdict_ref」换成字面量 `verdict_ref=NN`。
🔴 前者是覆盖面，**后者是要害** —— "verdict_ref" 在 news 契约里
本来就出现 3 次（都在示例里），纯子串匹配会判它通过。

**F10 · 姊妹配置已经出过事故，另一半至今没人管。**
`allowAgents` 漏 news 那次事故专门写了守卫；
`tools.agentToAgent.allow` 是同一形状的第二处名册，全仓没有任何测试碰过它 ——
实测它确实少了 news。已补齐配置并加对账。
⚠️ 这类 json **不受版本控制**，漂移天然不出现在 git diff 里被 review 到。

**F17 · `TestReservationIsAtomic` 名不副实。**
文档字符串宣称并发属性，而所有调用都是同进程同线程的顺序调用 ——
**没有任何真正的竞争窗口**。评审两路各自用 multiprocessing 压测
（80 进程 / 30 进程）证明底层机制本身是对的，所以这里不是修 bug，
是让测试名副其实。新测试起 16 个真 OS 进程、`Barrier` 同步启动；
探针把原子占号换成「先查再插」，16 个进程当场拿到同一个号。

**F23 · 库不存在时是一屏裸 traceback。**
修在 `db.connect()` 而不是某个工具里 —— 所有只读消费方共用这一个入口。
新增 `StoreNotInitialised`，区分「文件不在」（全新环境的正常状态）
与「schema 建好但零行」（也是正常状态，不该报错）。
三个巡检工具统一转成人话 + 退出码 2，与 `isolation.py` 的三态口径一致。


### 🔴 修复 · F4 / F15 / F16 —— `as_of` 归属的三个层次

**F4 · raw 落盘层与 Evidence 层分裂。** 上午的 `af91a0d` 给 Evidence 构造
加了 `add_live()`，但同一次提交**没改 `save_raw_snapshot` 的调用** ——
那里仍用全局共享的 `as_of`。实测两次运行：

```
snapshot_id=4  em:push2delay/ulist.np  as_of=2026-09-18T15:00:00+08:00
snapshot_id=6  em:push2delay/ulist.np  as_of=2026-09-18T15:00:00+08:00
```

两行 `content_sha256` 不同、payload 确实不同，却共用同一个 `as_of` 键 ——
而 `(source, as_of)` 正是 `ix_raw_source_asof` 索引存在的理由。
`tencent:quote` 更直接：payload 里嵌着今天的时间戳，raw 层写着上周五。
那个数在 Evidence 层已经算出来、也已经拿去做交叉校验了，落 raw 这一步被弃之不用。

> ⚠️ 测 Evidence 层的和测存储层的**各自全绿**，
> bug 正好落在两者之间从未被同时检查过的缝隙里。

**F16 · 别再靠白名单。** 原来「哪些字段来自无日期端点」是一份按文件路径 +
字面量字段名枚举的显式字典 —— 新源、新字段都不会被自动纳入。
⇒ 把这个知识放回**源自己身上**：每个源结果类型必须声明 `server_as_of`
（`None` = 这个端点不带日期）。调用方统一写 `r.server_as_of or now_cn()`。

**判断一旦分散，必然有某一处判错 —— F4 就是那一处。**
守卫是「`_sources` 里每个带 `raw` 的 dataclass 都必须声明它」，
新加一个源不回答这个问题就红。

**F15 · `staleness_sec` 对共模误差免疫。** `Evidence` 唯一的时间校验是
「都带 tzinfo」和「as_of ≤ retrieved_at」，**从不与真实时刻比较**。
一对都比现在晚 3 天、彼此只差 60 秒的时间戳：

```
staleness_sec = 60      # 1 分钟，看起来非常新鲜
as_of 实际比现在晚了 3 天
```

要害不是「少查了一项」，而是 `staleness_sec` 是个**差值** ——
上游两个时刻由同一段错误逻辑派生时，它对那个错误免疫。
⇒ 补一条锚在真实时钟上的检查（未来时刻永不合法，留 120s 时钟抖动容差），
并新增 `age_sec`（距**现在**多久）与之区分。

> 🔴 **探针第二次抓到我自己差点交出去的假守卫。**
> F4 的 per-source `as_of` 已经用 `probe.sh` 手工验过是对的，
> 但把那行改回共用之后**一条测试都没红** —— 与 F5 那次一模一样。
> 手工验过 ≠ 有守卫。两处都补了走完整落库路径的回归测试。


### 🔴 修复 · F5 / F6 —— 两处「有值、而且看起来正常，但它是错的」

比 `missing` 危险得多：不会被看见。

**F5 · 60 日高低点没有量级围栏。** 评审在窗口内**第 91 根**（不是最新一根）
把 `low` 改成 `0.01` —— 正数，不触发任何「≤0」守卫、不会 `ZeroDivisionError`，
正是本项目自己反复强调的「rc=0、字段齐全、类型正确」那类垃圾值：

```
verdict: PASS completed / missing: [] / warnings: []
dist_to_low60_pct = 31189900.0        # 3118.99 万 %
```

⚠️ 真实世界更危险的是**偏离没那么大**的坏值 —— 那种情况下算出的百分比
是一个看起来完全合理的错误数字，直接印上卡面。所以围栏卡在「物理上不可能」
（相对窗口**中位**收盘价差 5 倍），不是「看着不像」。用中位数不用均值：
一根 `0.01` 会把均值拉走，拉走之后它自己就显得没那么离谱了。

`market_calc` 早就有这类围栏，注释还写着「不是为了抓行情，是为了抓垃圾值」——
但只长在那一个文件里。⇒ 抽成 `_sources/sanity.py`。
顺带合并了两个同名不同值的 `PCT_ABS_LIMIT`（20 / 15，看起来像抄漏了），
改名 `INDEX_PCT_LIMIT` / `BOARD_PCT_LIMIT`，**让「不同」变成一个明示的决定**。

**F6 · 解析层把「字段缺失」吞成 0。** `float(r.get("f62") or 0.0)` ——
而**同一份代码对涨跌幅的处理是相反的**：为 `None` 或 `"-"` 直接 raise。
一份解析器里两套口径，被吞的那一半没人守。
后果：资金字段失效时「主力净流入前 5」给出一个**任意但格式完整**的第一名
（排序全相等，取谁都行），带着「0.0 亿」上卡，verdict 仍然 PASS。

原有的 `pre_session` 守卫只看涨跌幅，**从设计上没考虑同一响应里其它字段
可以独立失效** —— 而这不是假想的组合：集合竞价阶段指示价已变、
资金流统计尚未开始，同一时刻不同字段的新鲜度可以相反。

> 🔴 **探针抓到了一条我自己差点交出去的假守卫。**
> F5 的围栏函数被测得很细，但把 `technical_calc` 里那行调用删掉之后
> **一条测试都没红** —— 围栏被测了，「它有没有被用上」没被测。
> 补了走完整 `build_verdict()` 的回归测试才真的会红。
>
> **测函数，和测「产品线上真的走了这个函数」，是两件事。**


### 🔴 修复 · F2 / F18 / F19 —— 隔离自检自己会自欺

评审最尖锐的一句：

> **这份文档自己用来证明「我们没有自欺」的示范检查，其中至少两项本身就是会自欺的。**

三条缺陷同一个根：`isolation.py` 只有「过 / 不过」两态，
于是**「什么都没扫到」和「扫了，没问题」写出来一模一样**。

**F18 · 补第三态。** 代码里原本就写着「⚠️ 没给 `--before`，这一项不构成证据」，
紧接着却是 `res.add(True, ...)` —— 作者知道那不算数，
但**数据结构里没有第三态可以用来表达**，于是「知道」没能变成「报出来」。
⚠️ 它取代的旧脚本用的是三态 `PASS/FAIL/PENDING`，这里曾经是一次**退步**。

**F2 · 「找到了，但找到的是无关的东西」比「一个都没找到」更常见。**
原判据是「cwd 或 cmdline 落在 BigA 根下」，于是开发者的编辑器、终端全被算成
BigA 进程 —— 实测真网关 2 个，判据数出 15 个。这项检查因此**几乎永远为真**，
它实际验证的是「我这个终端没泄漏 fd」，与真网关是否在跑无关。
⇒ 扫描范围仍然宽（多扫不漏报），但**能不能构成证据，看有没有扫到真运行时**。

端口检查更直接：`clash = ours & theirs` 在两边都没监听时是空集，
`not clash` 为真 ⇒ 稳定报 ✅。**空集不冲突是平凡成立的。**
紧挨着的 I-1 至少防住了「0 个进程」，端口检查连那个都没防。

**F19 · I-1 曾经有两份判据。** 同一分钟内现场对照：一份数出 14 个进程，
另一份数出 4 个 —— 两边都报绿，但检查对象的集合完全不同。
真被违反时，两份实现可能给出相反结论，而使用者不知道该信哪一份。
⇒ 删掉旧的那份，`phase1_acceptance.py` 改为调用 `isolation.py`。**判据只留一处。**

退出码改为 `0` 全过 / `1` 有失败 / `2` 有判不了 ——
🔴 判不了必须非零，否则三态在渲染层做对了、在调用层又退化回两态。

新增 `tests/test_isolation.py`（11 条）：三态语义、两处「无事可查」、
以及用 AST 钉住「全仓只有一处在走 `/proc` 的 fd 表」。
四条旧行为逐一注回去当探针，全部确认会红。

> 🔴 **自检工具本身没有被同样严格地验证过** —— 这是评审归纳的两大系统性缺口之一。
> 顺带补上了 `architecture.md` 那句「由 `tests/test_isolation.py` 钉住」：
> 那个文件当时不存在，现在真的建出来了。


### 变更 · 补上实测：当天日线的发布时刻

前一条只证伪了「收盘后跑就对齐」，没给出真实时刻。5 分钟一探测到了：

```
15:32:47  20260918
15:37:50  20260921   ← 发布
```

收盘后约 33~38 分钟 ⇒ 分裂窗口约 6 小时（09:30 起算），不是原先以为的 4 小时。

⚠️ **仍然没有写成「15:40 之后跑就行」** —— n=1、一个源。
把一次观测写成规律，与刚被证伪的那条是同一个错误，只是数字换了。
判断方法仍然是直接查日线最后一根的日期。多测几天记在 `TODO.md`。


### 🔴 修复 · 设计文档点名了 6 个不存在的文件，其中 4 个被写成「已经有守卫在钉」

对抗性评审 F7 报了 2 个（`placebo.py` / `reachability.py` 与真实文件并排列在目录树里，
`git log --all` 对它们是空的）。**上了机器判据之后扫出 6 个**，多出来的 4 个里有两个是
「由 `tests/xxx.py` 钉住」这种陈述句 —— 而那两个测试文件都不存在。

其中一条是真实的无人管风险：`agents.entries.*.skills` 是**替换语义**，
漏抄一个技能不报错、只是静默少一个能力；`architecture.md` 声称有测试钉它，没有。

> 🔴 **一个不存在的守卫，比公开承认没有守卫更危险。**
> 后者至少不会让人放心，前者让读者（包括三个月后的作者）决定不去建它。

> 🔴 **评审找到的是样本，不是全集。** 人去找也只找得到想到过的那几个。
> ⇒ 拿到评审结论后，先给**那一类**上一条机器判据，再看它扫出多少。
> 这次是 2 → 6。

守卫本身差点成为反例：第一版只查反引号里的完整路径，
而 F7 报的两个幽灵写在目录树里、是**裸文件名** —— 探针当场证明它什么都没查出来。
补了按 basename 比对 `git ls-files` 的第二条路，两条都验过会红。

### 修复 · 三处「以已验证事实的语气写下、其实没被检验过」的断言

评审「收尾三问」第 2 问点名的三条，逐条改掉：

| 在哪 | 原文 | 实际 |
|---|---|---|
| `CLAUDE.md` 状态表 | schema v4，五张表，**只追加由触发器强制** | 五取四 —— 分配器没有触发器（F1） |
| `architecture.md` §3.2 | 「官方：只列一半 = 互相够不着」 | 与本机实测矛盾：配置少了一个 agent，而它当天仍被成功调用 ≥3 次（F10） |
| `architecture.md` §9 L-4/L-7 | 点名两个文件为「新系统的防护机制」 | 从未提交过（F7） |

三条的共同点与刚刚证伪的「收盘后跑就对齐」完全一样：
**听起来合理、写下来就成了依据、没有人回头验。**

### 变更 · 两份外部评审合并成一份台账，并核实了重叠处

评审 ② 的附记怀疑评审 ① 的修复没真正盖住 F3。**已核实，怀疑成立**，
而且三处「看起来重叠」的地方**三处都没被覆盖**：

- F1 ↔ P1-1/P1-2：两条新防线建立在「id 可信」之上，F1 恰在这个前提**之下**
- F3 ↔ P2-3：FIX-06 只修了教程的两处口径矛盾，机制缺口原样留着
- F4 ↔ P1-3：FIX-03 修的是 news 的 Evidence 层，F4 在 market/sector 的 **raw 落盘层**

> 🔴 **两份报告指向「同一大类」时，最容易假设已经修过了。**
> 真正的洞总在上一次修复所依赖的前提里。

台账同时保留了评审 ② 第二节「攻了但没攻破」的 12 条 ——
它标出哪些防护**不用再查**，价值不亚于发现清单。


### 🔴 修复 · 「收盘后跑就对齐」是我没验证就写下的推断，已被证伪

原文四处写着「15:00 之后跑，六个 Agent 的交易日就统一了」。**实测不成立**：

```
15:21  BIGA-20260921-019  交易日仍然分裂
       technical/market/sector  20260918   ← 日线
       emotion/news             20260921   ← 实时源
15:22  直接查源：新浪日线最后一根还是 20260918
```

**当天日线不在 15:00 发布，有一段未知长度的延迟。**
⇒ 这个架构问题的暴露窗口比原先认为的大得多 ——
不是「盘中 4 小时」，而是「从开盘到当天日线真正发布为止」。

> 🔴 为什么值得单独记一条：它**不是算错了**，是**根本没算**。
> 一句听起来无比合理的推断，被当成结论写进了 README、操作手册和教程，
> 而这三处又互相印证，看上去像是有依据的。
>
> **「应该会……」和「实测是……」之间隔着一次运行。**

在测出发布时刻之前，`docs/guide/usage.md` 改成给一条**可执行的判断方法**
（直接查日线最后一根的日期），而不是给一个时刻。实测项记在 `TODO.md`。

### 🔴 修复 · F1 —— 决策编号分配器是唯一没有只追加保护的表（schema v5）

外部对抗性评审的第一条。探针复现：

```
A 占到 BIGA-20260921-001
DELETE 成功 —— 1 行
B 占到 BIGA-20260921-001      ← 同一个号发了两次
```

**为什么这个洞比「少了个触发器」严重得多**：
FIX-01（卡不能装外来判定）与 FIX-02（risk 核对上游归属）校验的都是
「这些判定的 task_id 是不是同一个」。号被回收之后，两次运行的判定
在 id 字段上**真实自洽** —— 两道闸门会一致放行，
合成出来的恰恰就是 v4 要防的那种「两次运行混进一张卡」。
⇒ 身份的前提是**发出去不能收回**；没有这对触发器，
v4 建立的整套决策身份机制建在一个可撤销的地基上。

同时补了一条**不靠手写清单**的守卫：判据取自
`sqlite_master` 里实际有哪些表，`EXEMPT` 列例外。
原来那份 parametrize 清单当然不会提到 `decision_ids` ——
手写清单只覆盖想到过的，而漏掉的永远是没想到的那张。
⇒ **新表默认就该受保护，例外必须自己举手。**

### 修复 · F22 —— 测试条数在四处手写，三处已经过期

实测 476 条时，两处写 417、两处写 445。这是「同一个事实两套口径」的标准形状，
而且这个数**每加一条测试就变** —— 靠记性同步在高频变化的事实上必然失守。

⇒ 判据取自 pytest 自己收集到的条目数（不另写计数器，那就是第二套口径）。
只校验三份「活」文档；教程 / CHANGELOG / external 里的数字是历史快照，
用行内标记「冻结」豁免。两个方向都用探针验过会红 ——
**包括把那句话整个删掉**（否则检查会静默退化成「无事可查」）。


### 🔴 修复 · FIX-02…06 外部评审的其余五条

**六条全部复现，没有一条是误报。** 逐条修完、逐条探针验证。

#### FIX-02 · risk 不核对上游判定属于哪次决策（P1-2）

risk 检查覆盖率、交易日、陈旧度、阈值、stance 冲突、跨源校验 ——
**唯独没检查上游属不属于这次决策**。于是可以出现：五个 Specialist 全在、
`trade_date` 一致、`coverage_ratio=1.0`，而它们来自三个不同的决策。

⚠️ 合成阶段那道闸门拦得住最终的卡，但拦不住这件事：
**错误的 risk 判定已经生成、而且可能已经落库。**

⇒ 报 `risk.upstream.foreign_decision` 并**压到 UNKNOWN** ——
归属不成立时，`coverage_ratio=1.0` 不是「五个都到齐了」，
而是「五个坑里各插了一面旗，但不是同一片地」。

#### 🔴 FIX-03 · news 的时间语义（P1-3）—— 它盘中是对的，所以躲过了一整天

原来把最新一条的日期喂给 `as_of_for_trade_date()` —— 那是给**日线**用的：
交易日过完就返回当天 15:00。而 7x24 **没有「收盘」这个概念**。

实测量化：

| 时刻 | as_of | 算出的陈旧度 |
|---|---|---|
| 盘中 14:35 | 14:35 | 0 分钟 ← 碰巧对 |
| 收盘后 20:00 | **15:00** | **300 分钟** 🔴 |
| 深夜 23:50 | **15:00** | **530 分钟** 🔴 |

而真实情况是最新一条通常只有几分钟前。

> 🔴 **它盘中是对的，这正是它躲过一整天实测的原因** ——
> 当天所有测试都在 15:00 之前。上午刚修过 market/sector 的同类问题，
> 偏偏漏了 news，就是因为测试窗口系统性地避开了它暴露的时段。

⇒ 连续事件流的 as_of 只能是**最新一条的真实时刻**。
回归测试全部钉在**收盘后**的时点上。收盘后实测：陈旧度 15 秒（旧实现会是 600 秒）。

#### FIX-04 · news 的证据补 `raw_hash`（P2-1）

已经在落 raw snapshot，但证据不带哈希 ⇒ 拿着 `item_count=48` 查不回原始报文。
与 market/sector 对齐；派生字段返回 None —— **不硬凑**，
凑出来的溯源比没有更糟，它会让人以为查得到。

哈希**无条件算**，不只在落库时算 —— 否则 `--no-store` 跑出来的证据没有它，
而那正是人工核对最常用的路径。

#### FIX-05 · Stage 0 在全新库上跑不起来（P2-2）

`new_decision.py` 在空库上直接抛 `unable to open database file`
（算序号走的是 readonly 连接）。Stage 0 是整条链路的第一步，
它跑不起来「自包含的入口」就不成立。

⇒ 建 schema 放在 `reserve_decision_id()` 而不是 CLI ——
放在 CLI 就只有那一个入口受益。

#### FIX-06 · `agent_runs` 不是 spawn 证明（P2-3）

评审说「容易被误解」。实际是**白纸黑字写着**：`schema.py` 的注释与
教程第 4 章都写着它「证明 Supervisor 确实 spawn 了 Specialist」。

而 **BigA 自己的代码就在写它** —— `synthesize.py` 按已有 verdict 调
`record_verdict_run()`。人手工跑一遍合成脚本，这张表照样多出几行。

⚠️ 教程**第 7 章早就推翻了第 4 章**（「那行 `agent_runs` 是我手工插进去的」），
但第 4 章与 schema 注释没跟着改 —— 两套口径并存了很久（L-3）。

⇒ 改源头（schema 注释 + `record_agent_run` docstring），
教程第 4 章按「过程文档写完即冻结」的规矩**只追加 ⏩ 指针**，不改原文。

---

### 🔴 修复 · 被截断的 HTTP 响应会炸掉整个 skill（修 FIX-04 时撞上的）

```
http.client.IncompleteRead: IncompleteRead(234505 bytes read)
```

`news_scan.py` 吐出一个裸 traceback 崩掉 —— 而按本项目的口径，
它应该产出 `news.feed.unavailable`，让卡照常出、只是标着「不知道」。

根因：`IncompleteRead` 继承自 `HTTPException` + `ValueError`，
**不继承 `OSError`** —— 而重试层的 `except` 只写了
`(URLError, TimeoutError, OSError)`。

⚠️ **重试只此一处 ⇒ 这条影响全部六个 skill。**

> 通用原则：**按基类捕获异常之前，先确认它真的在那条继承链上。**
> `OSError` 覆盖不了 `http.client` 的那一族，而两者看起来都像「网络错误」。



外部评审指出：`synthesize.py` 有「所有 verdict 的 task_id 必须一致」的检查，
**但契约层没有** ⇒ 可以构造「卡 001 装着 999 的 verdict」。

实测复现：契约接受，而且**落库成功**。

> 守卫在编排层，就只守得住走编排层的那条路。
> 回放、将来的 API、测试辅助代码、手工构造 —— 每一条都能重新打开这个洞。

#### ⚠️ 与评审建议不同的一处，及其理由

评审的验收是「**Replay 也无法绕过**」。但实测已落库 **31 张卡里有 20 张**
的 verdict 写着别的号（Stage 0 统一占号是后来才加的）。一刀切会让它们
**永远读不出来**。

这与 `synthesize.py` 里 stance 检查的取舍同源，那条已经写在代码注释里：
**能不能重建「当时看到的东西」优先于形式一致。**

⇒ 改成三段式：

| 路径 | 行为 |
|---|---|
| 新造的卡 | **直接拒绝** |
| 从库里读的旧卡 | 可读，但把问题记下来并**显示在卡面上** |
| `save_card()` | **永远拒绝** —— 读可以宽，写必须严 |

第三段是关键：否则「读一张旧卡 → 原样存回去」就把非法状态洗白了。

验证：31 张卡全部可读（11 一致 / 20 带身份警告 / 0 读不出来），
`--check` 回放一致性仍然成立。三道守卫各用探针验证会红。

### 🔴 修复 · 两个 AST 扫描器会走进 worktree 副本

修 FIX-01 时全量测试红了一条**看似无关**的：
`.claude/worktrees/` 下出现了三份仓库副本（`git worktree`，被
`.git/info/exclude` 忽略），而扫描器**走的是文件系统，忽略规则对它不生效**
—— 同一份 `_store/db.py` 被数了四遍，其中三遍报成违规。

而且两个扫描器各自定义了**一模一样**的 `EXCLUDE_DIRS` —— L-3 的形状。

⇒ 抽出 `tests/_scan.py`，改用 `git ls-files -co --exclude-standard`。

> 通用原则：**黑名单只挡得住你想到过的目录。**
> 而仓库里会长出什么（worktree、缓存、临时克隆）不由测试作者决定。
> 「git 认为属于这个仓库的文件」正好就是扫描该覆盖的范围。


> **Phase 2 · Specialists，进行中。** 出口条件 6/8
> （见 `docs/design/phase-2-specialists.md` §4）。
> 满足后合回 `main` 并切 `0.3.0`。

### 新增 · `docs/guide/review-prompt.md` —— 对抗性评审提示词

自包含，粘到**新会话**里用（参与过建造的会话带着「我知道为什么这么写」的
上下文，那恰恰是评审要避开的）。

提示词开头第一条是一个**针对本仓库的具体警告**：

> 这个仓库的文档非常详尽、而且高度自省 —— 它会主动列出自己踩过的坑。
> 这很容易产生一种错觉：「他们连这个都想到了，那应该没问题」。
> 但**「把问题写进文档」和「把问题解决掉」是两回事。**

⚠️ 还明确要求**不要先读教程** —— 读完会获得建造者的视角，而那正是要避开的。

五个攻击面，每个都配了本项目**真实栽过的例子**（不是让评审复述，是举一反三）：
守卫是不是真会红 / 有没有「通过是因为什么都没查」/ 静默 fail-open /
契约与数据层的洞 / Agent 契约与实际行为的差距。

另附 8 条已知问题清单，**明确要求不要重复报告** —— 报告已知的不增加信息。

输出要求每条写「为什么现有守卫没抓到」，并单独回答三问，
其中一问是「**有没有哪个地方你试着攻击但没攻破**」——
那条同样有价值，它告诉我们哪些防护是真的。

提示词里的 7 条命令与 13 处文件/章节引用**全部实跑或核对过**。

### 变更 · 补切 `0.2.0`，把 Phase 1 从 `[未发布]` 里分出来

Phase 1 于 2026-09-20 验收并合入 `main`，**但当时没有发版** ——
于是它的 17 条记录和整个 Phase 2 堆在同一段里，`[未发布]` 积到 **88 条**。

后果不是「不好看」：读的人**分不清哪些已验收、哪些还在做**。
而 `main` 上其实只有 Phase 1 —— 版本号本该承担这个区分。

分界不是估的：对比 `git show main:CHANGELOG.md`，它的 `[未发布]` 正好 17 条，
逐条对上。⇒ 这 17 条切给 `0.2.0`，`[未发布]` 只留 Phase 2（71 条）。

> 通用原则：**版本号的作用是划出「已被证明」与「正在做」的边界。**
> 不切版本，`[未发布]` 就会膨胀成一份流水账。

### 变更 · 教程第 17 章重写为**收官章**

原来它只是「Phase 2 收尾踩的坑」。作者指出收官章应该回答三个问题：
**建成了什么 / 怎么用 / 下一步是什么**。

新结构：功能清单（含拓扑图与卡片八段结构）→ 怎么跑 + **四个「确认它没骗你」
的检查** → 八个收尾的坑 → **还做不到什么**（诚实列出）→ Phase 3 展望。

两处值得单独说：

**「还做不到什么」单列一节。** 诚实地列出来比含糊带过有用 ——
尤其是「盘中给出有把握的结论：做不到，而且原因是结构性的」这一条。

**Phase 3 展望不是照抄路线图。** 经过 Phase 2 实测，几件事有了更具体的形状：
时间基准统一从「待决项」升级为首要项（它正在挡住最后两条出口条件）；
采集缓存有了新理由（第三方接口会渐进限流，缓存是**减少对别人接口的压迫**）；
第一条 cron 有了现成的消费方（缺失项台账需要跨天累积）。

章内所有数字都与实际核对过（7/8 Agent、445 测试、schema v4、5 张表、
31 张卡、7 个核查工具）。


### 🔴 修复 · 一个今天的数，挂着上周五的时间戳

作者看卡时问「怎么用的是 18 日的行情数据」。查下去发现**两件事，只有一件是对的**。

**对的那件**：`technical` / `sector` 的日线派生量确实是 09-18 ——
今天的日线要收盘后才发布，盘中取不到。

**错的那件**：卡上这行

```
[market] 上涨家数 = 4385   as_of 09-18 15:00   src em:push2delay/ulist.np
```

**4385 是当天此刻的数**，却挂着上周五收盘的时刻。`sector` 的板块榜同样。

#### 根因两层

1. `as_of` 只从日线的 `trade_date` 算**一次**，然后被所有证据共用。
   而涨跌家数、板块榜这两个端点**连日期字段都没有** ——
   能诚实声明的只有「取回它的时刻」。
2. 代码其实知道（它发了 warning），但 **`card.render()` 从不渲染 warnings**。
   四个 Agent 各发了一条，一条都没上卡，于是卡面看起来板上钉钉。

⚠️ 注意这里的不对称：**带日期的源会被核对**（腾讯行情不一致就报
`date_mismatch`），**唯独没有日期的那个源反而被默认对齐** ——
而它恰恰是最可能对不上的。

#### 修法

- 新增 `add_live()`：实时快照类证据 `as_of = 取回时刻`。
  market 4 个字段、sector 8 个字段改用它
- `card.render()` 增加「⚠ 提请注意」段 —— warning 与 missing 的分工是
  「能用但要注意」vs「没有」，把前者藏起来等于只保留了它的名字
- 现在同一个 Agent 的证据会横跨两个时刻，**而卡面看得出来**

> 通用原则：**一个值的时间戳是它的一部分，不是环境的属性。**
> 「这批数据都是同一时刻的」是个假设，而它对无日期的源从来不成立。

#### ⚠️ 两个连带发现

**一、单一生产方守卫只认 `add(`。** 加 `add_live` 的一瞬间，
9 个字段静默退出了它的视野。测试当场红了（另有一条断言兜住）。
⇒ 扫描器同步认两个入口，并在 docstring 里写明「每加一个登记入口都要同步这里」。

**二、Python 的 bytecode 缓存差点让我查错方向。** 改完 `card.py` 在**同一秒内**
跑测试，`.pyc`（13:53:41.503）与源文件（13:53:41.579）相差 76 毫秒 ——
而 mtime 判据的粒度是 **1 秒**，于是执行的是旧字节码。

> 表现是「代码明明在那儿，就是不生效」。
> 探针验证守卫时尤其容易撞上，因为那正是「改一下、立刻跑」的节奏。
> ⇒ 探针脚本里清一次 `__pycache__`。


### 新增 · `bin/biga-card` + `docs/guide/usage.md` —— 终于能「用」了

在此之前，出一张卡要手敲一段很长的提示词，而且**漏一句就出问题**：

| 漏了什么 | 后果 |
|---|---|
| `--session-key` | 用默认会话，一轮 `end_turn` 就返回，卡不出来 |
| 「带上决策编号」 | Specialist 用临时号，落库直接报错 |
| 「等全部结算」 | 两次运行交叠（今天真出过一次） |

这些都是「靠人记得」的约定 —— 本项目反复证明那类约定会失效。⇒ 固化成一条命令。

```bash
bin/biga-card              # 出一张新卡（实测约 2–3 分钟、$1.2）
bin/biga-card --list       # 最近出过哪些
bin/biga-card --show <号>   # 看某一张
bin/biga-card --check <号>  # 用冻结证据重跑，断言结论逐字段相同
```

手册的重点是**「怎么确认它没骗你」**：四个互相独立的检查
（回放一致性 / Specialist 真被调用 / 真并行 / 没影响同机其他服务），
任何一个都能单独拆穿。

### 🔴 修复 · 一张卡渲染出来 34.5 KB

写手册时第一次**真的去用**它，立刻撞到：`replay.py <号>` 输出 34.5 KB。

原因是卡面摘要直接渲染 `result` 的值，而 `news.items` 装着 **67 条快讯原文**。

> 它在技术上完整，**在用途上作废了** —— Decision Card 的定位是给人看的一页纸。
> 而且没有任何东西报错，因为「把 result 渲染出来」这件事本身是对的。

⇒ 显示层截断（列表报条数与首项、长文本截断到 72 字符）。
完整值永远在 `card_json` 与 `agent_verdicts` 里，**截断只发生在显示层**。
34.5 KB → 9.5 KB。

> 通用原则：**加一个新字段时，去看它在所有消费方那里长什么样。**
> `items` 在库里是对的、在回放里是对的、在卡面上是灾难。


### 🔴 修复 · 强推时 pre-push 什么都没扫

做完 filter-repo 强推时，hook 打出的是：

```
（git log <旧>..<新>：没有内容可扫，无需审查）
```

旧 commit 被重写后**不可达**，`旧..新` 是空区间 ——
**恰恰在最危险的那次推送上，守卫静默放行了。**

增量口径的前提是「新历史包含旧历史」。这个前提一旦不成立，
省下的那点扫描时间换来的是一个假绿。

⇒ hook 现在先判断 `git merge-base --is-ancestor`，不成立就改扫**整条新历史**。
三种口径对应三种推送：普通=增量 / 新分支=相对远端的增量 / **强推=全量**。

探针：造一个孤儿提交当 `remote_sha` ⇒ hook 正确识别并切换口径。

> 通用原则：**一个「省事的口径」总有它失效的前提。**
> 把前提写成判断，而不是指望它一直成立 ——
> 尤其当失效时的表现是「什么都没查，然后报绿」。


### 🔴 安全 · 重写全部 git 历史并强推（第二次）

第一次是 Phase 1 期间抽掉可识别细节（尚未 push，零代价）。
**这次不同：内容已经公开过。**

远端状态：`main` 与 `phase2` 都已推送，敏感内容在 GitHub 上存在了约一天。

#### 做法

`git filter-repo --replace-text` + `--replace-message`，两遍。

#### 🔴 第一遍漏了两样，都值得记下来

**一、`--replace-text` 不管提交信息。** 它只替换文件内容。
提交信息要用 `--replace-message`（同样的语法，单独的开关）。
第一遍推上去之后，从远端重新 clone 复核才发现 —— 提交信息里还留着一条。

> 通用原则：**改写历史之后，从远端重新 clone 一份来验**。
> 在本地验等于用自己的记忆检查自己的记忆。

**二、替换表漏了一项。** 旧用户名不在表里，于是它在历史里原封不动。
复核时用的是**原始 grep 而不是审查脚本** —— 而审查脚本恰好会排除那几行
（它们在脚本自己的模式里）。用工具验工具，就会这样互相掩护。

#### 🔴 一个必须提前想到的连带损坏

`--replace-text` 替换**所有** blob，**包括审查脚本当前的那一份**。
于是九项检查的正则全变成 `(***|***|…)` —— **脚本静默失效，
而且跑起来一切正常**，它只是什么都匹配不到了。

修法不是把词填回去，是让脚本自身不再字面包含它们：

```
[a]bcd   与   abcd      正则语义完全等价
```

`ps aux | grep [b]ash` 那个老把戏。**九项模式全部这么写** ——
否则下次清理别的类目（比如密钥前缀）会改坏另一条检查。
第二遍验证了它确实有效：脚本毫发无伤。

#### 复核结果（从远端重新 clone）

```
blob 扫描      0 处
提交信息扫描    0 处
```

#### ⚠️ 仍然不是完全撤回

GitHub 上旧 commit 的对象可能在一段时间内仍可通过 SHA 访问
（缓存、fork、第三方镜像）。这次强推的意义是**止损**，不是撤销。
已公开的内容撤不回来 —— 这条纪律本身没有变。

备份：`~/.openclaw-biga/backup-<时间戳>/`（bundle + `.git` 副本）。


### 🔴 安全 · 教程只写正常操作，与既存系统相关的内容全部移出

一次人工 review 引出的两轮清理。

**第一轮 · 「如何接通 claude」**
本机与本账号在认证上的**具体安排**（限制来自哪、凭据从哪来）——
属于**这台机器与这个账号的特殊情况**，不该进公开仓库。

> ⚠️ 这段最初就是把那些词逐个列出来写的，被第 9 项当场拦下 ——
> **第 4 次犯「描述『不要写 X』时把 X 抄进去」。**
> 好消息是这次有守卫了：拦下它的正是本条新增的检查。

⚠️ 前八项审查一项都没抓到：它既不是密钥，也不是路径，也不是可识别的模块名。
⇒ 加审查第 9 项「环境/账号策略」。加上之后先红 **28 处**（5 个文件）。

**第二轮 · 与既存系统相关的操作**
教程原本刻意以「在一台有邻居的机器上施工」为差异化卖点，
于是共存细节被大量写进正文（6 章 + README，约 46 行）。

改法是**泛化措辞、保留工程内容**：

| 留 | 去 |
|---|---|
| 隔离安装、端口分离、`kill -9` 演练 | 「邻居」这种拟人化的特指 |
| 「别把同机其他服务搞崩」这个通用问题 | 那套系统是什么、怎么配的 |
| 对照组排查法（通用技巧） | 我具体读了它哪些配置 |

⇒ 第 09 章保留，措辞改为中性的「同机已有实例」。

**⚠️ 这推翻了 `CLAUDE.md` 里原来的一条写法**（原文说教程的差异化在于
「有邻居的机器」）。改的理由：那让教程变成一份共存故障记录，
而共存细节属于本机情况。

#### 为什么这类内容特别容易漏

> 它**读起来像「踩坑记录」** —— 而教程恰恰是最鼓励写踩坑的地方。

⇒ 两道守卫，都用探针验证会红：
审查脚本第 9 项（全仓）+ `test_docs_convention.py` 的教程措辞检查。

操作细节移到仓库**外**：`~/.openclaw-biga/AUTH-NOTES.local.md`。

#### ⚠️ 这不构成撤回

仓库已 Public 且已 push ⇒ 这些内容在 **git 历史里仍然存在**。
本次只让 HEAD 干净。已公开的内容撤不回来 —— 删分支也可能留在 fork、缓存与镜像里。


### 🔴 修复 · `CLAUDE.md` 的「当前状态」严重过期

它写着「**业务代码尚未开始**、未开始：契约层、数据层、任何 agent」——
而实际已有 7 个 Agent、417 条测试、schema v4。

⚠️ **这是每次会话第一个读的文件。** 过期的状态描述比没有状态描述更糟：
它会让下一次会话（或下一个人）从一个错误的起点开始推理。

同样修了 README 的五个徽章（Phase / Agents / Tests / Store / 教程）
与端到端徽章，并把「关于徽章全绿的说明」补成两次改预算都讲清楚 ——
第二次数字是**变差**的（74.8s 休市日 → 172.6s 盘中），更需要说明白。

### 新增 · 教程第 17 章《收尾：把「差不多了」变成一张能核对的表》

按裁定 10 补上今天后半段的章节。六个坑，其中三条是关于**怎么诚实地报数**的：

- 改完之后端到端**更慢**了，而只报改善的那三行就是挑指标
- 改预算的三个必答问题（旧的错在哪 / 新的怎么来 / **什么时候它该红**）
- 指标达标之后要再问一句：**它证明的是不是我当初想证明的那件事**


### 新增 · `tools/verify/missing_ledger.py` —— 台账从库里生成，不手工记

出口条件 4 原本打算手工记「日期 / 哪个源缺 / 缺失代码 / Card 状态」。
这四项**全都已经在 `decision_records` 里了** —— 手工再记一遍就是
第二套口径（L-3），而且它会先漂。

#### 🔴 两次「数着数着就达标了」

**一、注入的不能算。** 判据一开始写的是中文文案「演练：人为中断」，
测试立刻抓到 `market_calc` / `emotion_calc` 用的是另一句
「数据源被人为中断（--break-source X）」。两套文案 ⇒
那两个 skill 的**注入故障会被算成真实缺失**，出口条件凭空达标。

⇒ 改用 **CLI 开关的字面量** `--break-source` 做判据 ——
散文会被顺手改写，开关名不会。

> 顺带发现最不一致的是 `news_scan` —— 我今天刚写的那个。
> **新加的东西最容易偏离既有约定**，因为写的时候没在看别人怎么写。

**二、不是所有真实缺失都是同等强度的证据。**
`supervisor.agent_offline`（某个 Agent 还没建）证明的是
「我们知道自己少了个 Agent」，不是「我们能发现数据缺了」。
混着数：29 张卡里 27 张「有真实缺失」；只看源级就只剩 11 张。

#### 当前状态：数上够了，实质不够

```
有源级真实缺失的决策：11 / 29 张卡    要求 ≥5    ✅
不同的源级缺失代码：18 种            覆盖天数：1 天  ⬜
```

🔴 全部来自同一天 —— 同一天的多次运行缺的往往是同一个东西。
**这条出口条件还不能勾。** 工具会在只有一天时自己报警。


### 变更 · 延迟预算 90s → 180s（出口条件 5）

🔴 **这不是把红灯调绿。** 改预算必须同时说清三件事，否则就是作弊：

**1. 旧数字错在哪** —— `60-105s` 是在**一个 agent 都还没有**的时候，
把四个阶段各拍一个区间再相加得到的。它是一个愿望，不是一个预测。

**2. 新数字怎么来的** —— 四次盘中实测，即使每一项都取最好值：

```
Stage 1 最好 64.2  +  risk 最好 19.9  +  main 最好 73.9  =  158s
```

⇒ 180s（留 14% 余量）。而 Stage 1 的最慢项是**第三方接口延迟**，
实测还会渐进限流 —— **不在我们控制内**。

**3. 什么时候它该红** —— 优化后三次实测都在 158–173s。
超过 180s 说的是「某个组成异常了」，不是「今天慢一点」。

还能往哪压，按可控性排序：`main` 的合成（73.9s）→ `news` 的 prompt
（但缩窗口就缩发言范围，**那是诚实性与速度的交换，不是单纯优化**）→
其余 Stage 1 agent 都在 30s 上下，压它们对 `max()` 没有意义。


### 🔴 修复 · 契约推荐的「同步等待」快路从来没生效过

按流程展开 main 的调用序列（不是先改提示词）：

```
10:45:37  agents_wait   → 五个 id 全部 not_found，8.2s
10:45:43  subagents     → agent 自己去查状态，6.2s
10:45:50  sessions_yield→ 退回异步路径，7.4s
```

**22 秒全是协议开销，而且没有任何东西报错。**

根因在运行时源码：`agents_wait` 的判据是 `if (!entry?.collect)` ——
它只认 swarm collector 子会话。而契约教 main 用 `agents_wait`，
**却没教它 spawn 时带 `collect: true`**。

更深一层：契约给了**两条路**（「优先 `agents_wait`」+「`sessions_yield` 是对的」）。
给「优先 A，B 也行」，得到的是 **A 然后 B** —— agent 还自己加了一次
`subagents` 去弄清 A 为什么没用。

> **写契约时，一个动作只给一条路。** 备选要明确标成「兜底」并写清触发条件。

⚠️ `collect: true` 与 `agents_wait` 是**一对**：collect 模式没有完成通知，
只用 collect 而后 yield，这次对话会**静默地永远停住**。

#### 实测收益比预期小，如实记

| | yield 路径 | collect+wait |
|---|---|---|
| main 轮次 | 3 轮 | **1 轮** |
| main 自身模型时间 | 94.1s | **~73.6s** |
| Stage1→Stage2 空档 | 11–14s | **6s** |
| 端到端 | ~157s | **172.6s** ← 更慢 |

🔴 端到端反而更慢，因为这次 Stage 1 本身慢了（news 63.4→78.3s）。
**n=1 时 Stage 1 的波动盖过了这个改动。**

⇒ 能确定的只有「结构变对了」和「main 模型工作量降约 22%」。
**不能声称端到端变快** —— 那需要 Stage 1 稳定下来多测几次，
而它的波动源（第三方限流）不在我们控制内。

### 新增 · `tools/verify/isolation.py` —— I-1 终于有了可执行判据

`CLAUDE.md` 的不变式表里，I-1 的验证方式一直写着「（待建）」。
一条没有可执行判据的不变式等于一条约定，而约定只能约束记得它的人。

四项检查：I-1（无写模式打开邻居文件）/ I-2（操作前后邻居库未变）/
R-2（nvm bin 无 openclaw）/ 端口不重叠。实测全绿（16 进程、413 个 fd）。

🔴 **前缀陷阱：`~/.openclaw` 是 `~/.openclaw-biga` 的前缀。**
第一版用 `startswith` 判断越界 —— 实测会误报 **64** 个 fd，全是 BigA 自己的。
改成 `PurePath.is_relative_to()` 按路径分量比较后命中 0 个。

> 一个 100% 误报的检查，几次之后就没人看了 —— 和没写一样。

探针验证：把检测目标指到临时目录、以写模式打开一个文件 ⇒ 立刻抓到；
只读打开 ⇒ 不误报。**全程不碰邻居的任何文件。**

### 新增 · Phase 2 成本分解（出口条件 6）

`BIGA-20260921-016` 盘中六 Agent，**$1.20/次**。`main` 一个占 37%
（缓存读 746k，是第二名的 2.5 倍 —— 它要把所有 Specialist 的回答读进上下文）。
`news` 是最贵的 Specialist（$0.22），因为 prompt 里有 79 条快讯原文 ——
那是设计选择不是意外。

**现在不降档任何 agent**：唯一明显的候选是 `main`，而它恰恰是判断最密集的一步。
⇒ 先有区分力检验（Phase 4），再谈降档。


### 变更 · 出口条件 1 达成：五个 Specialist 真并行已证

`BIGA-20260921-013`（盘中六 Agent）：区间两两相交，最小重叠 27.9s，
Stage 1 墙钟 64.2s vs 个体之和 227.3s —— **省 72%**。
sector 的翻页优化同时验证：113.6s → 51.6s。

🔴 **瓶颈回到了 Supervisor**：main 三轮共 120.7s，占总时长 64%，
最慢单轮输出 4627 tok。Stage 1 多一个 agent 只多 12.8s（`max()` 的性质），
main 反而涨了 —— 要合成的东西变多了。

> **并行解决扇出，解决不了汇聚**（第 11 章那句话的第三次兑现）。
> 下一步优化 Stage 3，不是再压某个 specialist。


### 修复 · 合成时忘了 `--decision-id`，脚本就又分配了一个新号

六 Agent 端到端（`BIGA-20260921-014`）跑通后发现的尾巴：

```
卡 = BIGA-20260921-014        它的五条证据全写着 BIGA-20260921-013
```

Supervisor 在 Stage 0 占了 013、一路传给五个 specialist，
合成时漏了 `--decision-id` ⇒ 脚本按老逻辑又分配了 014。

混血闸门不会红（所有 verdict 的号是一致的），但**归属仍然断了**。

⇒ 没给 `--decision-id` 时，**用证据自己带的号**。
`next_decision_id()` 退化成兜底，只有完全没有上游号时才会走到。

> 根子还是那一条：**决策的身份在 Stage 0 就定了。**
> 合成阶段的职责是用它，不是再造一个。

### 🔴 修复 · 契约说 spawn 五个，配置只允许四个，中间没有任何报错

`news` 建好、注册好、写进 Supervisor 契约之后，端到端跑下来 Stage 1
**只起了四个**：

```
10:37:18  technical / market / emotion / sector     ← news 不在其中
```

原因是 `agents.entries.main.subagents.allowAgents` 是一条**显式白名单**，
里面没有 `news`。Card 照常产出，只是少了一个领域。

⚠️ 更麻烦的是**它把排查引向错误的方向**：`risk` 会如实报
「Stage 1 缺席：news」—— 看起来像 news agent 没建好，
而真正的问题在一个完全不同的文件里。

⇒ 名册这种「两处各写一遍」的东西必须有人对账：
`tests/test_roster_matches_config.py` 现在对三个方向做检查
（已建 → 注册 / 已建 → 白名单 / 契约声明 → 已建），探针验证会红。

> 这与 `agents.entries.*.skills` 是替换语义（不与 defaults 合并）同一族：
> **配置里的"白名单"默认是排他的，而排他的失败是静默的。**

### 新增 · 2.4 `news` —— 第一个「skill 算不出结论」的 Specialist

前五个 Agent 的形状一样：skill 把数字算完，agent 复述并挑一个 stance。
news 不一样 —— **「这条消息是利好还是利空」没有算式**。

两条铁律在这里第一次指向同一个方向：铁律 3（Agent 不做算术）让 skill 算
条数与覆盖度；铁律 4（skill 不做判断性归类）把**含义**留给 agent。
⇒ 产出是 **事实 + 原文**。

**回放的定义因此要改一次。** 模型做判断 ⇒ 同样输入可能给出不同 stance。
把原文（含 id）随证据冻结，回放时喂同一批文本 ⇒
「输入相同而结论不同」从一个无法排查的现象，变成**一个能被统计的量**。

#### 数据源探活：四选一，两个当场出局

财联社电报 404（接口已变）；东财 7x24 每补一个参数就再要一个。
选新浪 7x24（`create_time` 已是北京时间、id 单调、9 页覆盖 29 小时）。

🔴 **`is_focus` / `top_value` / `tab` 全是常量 0** —— 名字都像重要性标记，
实测 100 条没有一条非零。照它设计过滤器会得到一个**永远选出空集**的过滤器，
而空集在下游表现为「今天没有重要新闻」，不报错。

🔴 **这个源的「0 条」只可能是取数失败**（凌晨 1 点仍有 21 条/小时）——
与行情源恰好相反（那边的 0 常是「这一天还没开始」）。

#### 🔴 第一版额度定错了，实跑才发现是 fail-open

窗口 180 分钟 / 上限 40 条 ⇒ 实跑窗口内 250 条、只展示 40 条、
`items_truncated=210`，**而 verdict 是 PASS**。
agent 只看到 16% 却说「数据完整」。

按实测分布重定为 60 分钟 / 120 条（盘中典型 93 条 < 120，**正常不截断**），
并把截断从 warning 升级为 **missing**。

> 代价是只能就最近一小时发言。这是**诚实的小范围**，
> 好过「三小时」这个读了 16% 就敢下的大结论。

静默阈值同样按实测定：盘中相邻间隔最大 235s ⇒ `STALE_SEC=600`，
**且只在连续竞价时段启用**（夜间实测就能到 1830s，周末只会更长）。
为此在 `tradetime` 加了 `market_is_open()` —— 它与 `session_in_progress()`
回答的不是同一个问题，docstring 写清了区别。

#### 机器行情播报：打标，不过滤

盘中 21% 是「上证指数涨0.44%，现报3929.027点」这类 —— 那是 market/sector
的事实（裁定 15）。不过滤（过滤是判断性归类，且误伤真消息下游看不出来），
改为打 `is_quote` 标记 + 契约明令「不许从快讯提取任何行情数字」。

⚠️ 标记有已知局限：`【国债期货开盘】…` 这类带标题的真播报会漏标。
**不调正则去追**（拿一天样本过拟合）。关键在定位：
**标记是提示，契约才是防线。** `quote_count` 是下界不是精确计数。

#### 单一生产方守卫抓到一条「同名不同事实」

`news.session_live` 与 `risk.session_live` 撞名，而周六 10:00 两者相反
（`session_in_progress`=True「这批数据所属交易日没过完」，
`market_is_open`=False「此刻不在交易」）。⇒ news 改名 `market_open`。

> 两种都要防：同一事实两个名字**让守卫看不见**（今早那条 `sh_close`/`close`），
> 同名两个事实**让人以为是一个数**。

六道守卫全部探针验证会红。契约层第三次接住 `_EXPECTED_FIELDS` 除数写错。


### 新增 · 教程第 15 章《第一次盘中运行》

记录四个只在交易时段暴露的问题，以及一次「我违反了自己写的开发流程」。
按裁定 10，写完这一章才开工 2.4。


### 变更 · `fetch_boards` 并发翻页：67.9s → ~19s，但并发度是**测出来的**

盘中端到端 214.9s（超预算 2.4×），差距几乎全在 `sector`（113.6s）。
按流程先看调用序列，而不是先改提示词 —— 结果根本不是 agent 的问题：

1. sector 在 `process poll` 上等了两次 30 秒
2. 因为 skill 跑了 68 秒，被运行时自动转了后台
3. 量下去：`fetch_boards` **串行**翻页，industry+concept 共 11 次请求、
   **58.8 秒纯网络等待**

> 又一次印证：Agent 的「慢」多数时候是它在**等**或在**找**，不是在想。

#### 🔴 并发度 4 反而更慢 —— 而且要连跑三次才看得出来

| 并发 | 连跑三次 | 判断 |
|---|---|---|
| 1（原实现） | 67.9s | 串行等待占 58.8s |
| 4 | 7.2s → 25.2s → **60.1s** | 🔴 单调上升 ⇒ 渐进限流 |
| **2** | 23.3s → 9.1s → 23.5s | ✅ 不再爬升，均值 ~19s |

单调上升不是网络抖动 —— 抖动会双向。打得越猛越慢，说明对方在限速。

> 通用原则：**调并发度必须连跑几次看趋势。**
> 单次计时分不出「这次快」和「还没被限到」——
> 而第一次测到的 7.18s 恰好是最快的那次，照它定参数就会把限流当成性能。

顺便测准了一个旧坑的边界：`pz` 被硬顶在 **100**
（`pz=200/300/500` 都只返回 100 行，`total` 照报 504）。页数减不了。

并发引入的两个新风险各钉一条测试：**按页号拼装**（用到达顺序会让
同样的输入产生不同的 raw，回放就对不上）、**分页不全仍要报错**。

⚠️ 第一版「乱序」探针改的是字典构造，测试**没红** ——
说明那条测试当时并没有在测它声称的东西。顺序保证其实在
`for pn in rest` 那一行。已把正确的探针写进测试 docstring。

### 🔴 已知问题 · 盘中出不了有把握的卡：时间基准是分裂的

同一次盘中运行里，四个 Specialist 报出了**两个交易日**：

| agent | `trade_date` | 为什么 |
|---|---|---|
| `emotion` | `20260921` | 实时涨跌停池 ⇒ 说的是**此刻** |
| `market`/`technical`/`sector` | `20260918` | 日线 ⇒ 今天的还没收，只能报上一个已完成交易日 |

`risk` 正确地拒绝把它们当同一天一起审，Card 落在 `WAIT`。**这是对的行为**，
但它意味着：按目前的构造，BigA 盘中出不了有把握的卡 —— 不是哪里坏了，
而是一半 Agent 说「此刻」、一半说「昨天收盘」。

两种解法（盘中用分时合成未完成 K / 只在盘后出卡）都要改指标口径，
而口径一改，此前所有实测数字都不可比。⇒ 记入 Phase 3 待决，现在不选。


### 🔴 修复 · 两次运行的证据合成进了同一张卡

盘中端到端（2026-09-21 09:37 与 09:39，相隔两分钟）暴露的**最严重的一个 bug**。

症状：

| | 卡 006（09:40:57） | 卡 007（09:42:08） |
|---|---|---|
| technical | PASS `result#3b4dd3a0` | PASS **`result#3b4dd3a0`** ← 同一条 |
| sector | **UNKNOWN** | **PASS** |

同一分钟内两张卡共用一条判定原件，且对 sector 给出**相反**的结论。
而卡面上**没有任何异常**：agent 齐全、时间戳都在几十秒内、missing 正常上浮。

#### 根因有两层

1. **五个 specialist 都写着 `new_task_id(1)`** —— 序号硬编码。
   于是所有判定原件的 `task_id` 全是 `-001`，任何两次运行都无法区分。

   ⚠️ 这个 bug 在 `synthesize.py` 上**修过一次**，兄弟模块一个没查。
   `next_decision_id()` 的注释里甚至写着它的形状。
   教程 14 要点 9 正是「在一个 Agent 上修好的坑，去检查所有同类」——
   写下这条的人（我）随后就违反了它。⇒ 补一条 AST 测试机器化它。

2. **更根本：编号原来在合成时才分配**，而那时证据早已采完。

   > 一个决策必须在**收集证据之前**就有身份，否则证据无处归属。

#### 修法：把身份提到 Stage 0，把守卫放在改不掉的地方

- **schema v4 `decision_ids`** —— 编号分配器。占号靠**主键冲突**仲裁，
  不靠「先查空位再插入」（那中间有竞态窗口，正是本次事故的机制）。
  探针：8 个并发占号 → 8 个不同编号，无空洞
- **`new_decision.py`** —— Stage 0 占号，编号沿 Stage 1/2/3 一路下传
- **临时号 `BIGA-YYYYMMDD-000`** —— 手工跑 skill 时用。它**自曝身份**，
  而原来的默认值 `-001` 看起来像个正经决策号。
  `save_verdict()` 拒绝落这种号：**临时结果可以看，不可以入账**
- **守卫放在 `save_verdict` 这个唯一写入口**，不是五个调用点 ——
  调用点只会越来越多，而这正是同一个 bug 能同时活在五个文件里的原因
- **合成时拒绝混血**：verdict 的 task_id 不一致就报错

#### 检查顺序也是设计的一部分

混血检查放在 stance 完整性检查**之前**。顺序不是随意的：
证据来自两次运行时，先抱怨「缺 stance」会把人引向错误的修复 ——
补完 stance，真正的问题还在，而且更难看见了。**身份比完整性更根本。**

#### 三道守卫都用探针验证会红

拆掉临时号闸门 → 2 条红；改回硬编码序号 → 1 条红；拆掉混血闸门 → 1 条红。

### 修复 · 我自己违反了「端到端必须开新会话」

第一次盘中 e2e 用了默认会话 `agent:main:main`（历史 e2e 都用 `e2e-HHMMSS`）。
后果：`stop=end_turn` 一轮就返回、rc=0、输出为空，而 subagent 还在跑。
随后第二次运行与它交叠 —— **上面那个 bug 就是这样被触发的**。

流程第 6 条写的就是这件事。⇒ 坏事变好事，但记下来：
**违反流程的代价常常不是「这次没做好」，而是「制造出一个你从没见过的故障」。**

### 新增 · news 数据源探活（2.4 设计前置）

| 源 | 结果 |
|---|---|
| 新浪 7x24 直播 | ✅ 可用。`create_time` 已是北京时间，id 单调，9 页 ~900 条覆盖 29 小时 |
| 东财个股新闻搜索 | ✅ 可用，关键词检索 |
| 财联社电报 | ❌ 404，接口已变 |
| 东财 7x24 快讯 | ❌ 每补一个参数就再要一个，形状不稳 |

两个反直觉的发现：

1. 🔴 **`is_focus` / `top_value` / `tab` 全是常量 0** —— 看着像重要性标记，
   实际零信息。照它设计过滤器会选出空集。**又一次印证「设计先探活」**
2. **这个源的 `0 条` 只可能是取数失败** —— 凌晨 1 点都有 21 条。
   这和行情源恰好**相反**（那边 0 常常是「还没形成」）。
   ⇒ news 不需要「盘前守卫」，但需要一条「静默即故障」的判据

内容构成（盘中 100 条）：57% 带标题快讯、22% 正文、**21% 机器行情播报**。
最后那类里全是「上证指数涨 0.44%，现报 3929.027 点」——
那是 `market` 已经权威产出的事实。⇒ **news 不得产出数字**（裁定 15）。


### 🔴 修复 · 裁定 15 的守卫比的是字段名，于是漏掉了一个同义重复

同步设计文档时顺手查了一遍「同一个事实有没有两个生产方」，抓到：

```
market.sh_close   = 3911.87
technical.close   = 3911.87      ← 同一个事实，不同的名字
```

`tests/test_field_single_producer.py` 完全没看见它 —— 它比的是**字段名**。
这条重复在 2.3 合入时就在那儿，走完了整套流程都没被发现。

**修法不是删掉其中一个。** 两个 agent 从同一个源独立取值，
不一致本身就是信息：说明它们看到的不是同一份数据，
那时它们的结论没有共同基准。⇒ 把它从**隐患**变成**探测器**：

- `_contract.CROSS_CHECK_PAIRS` 声明被允许的重复事实
- `risk` 逐对比较，不等就进 `missing`（`risk.upstream.cross_check_conflict`）
- 守卫反过来要求：声明过的字段**必须**出现在 risk 的核对清单里 ——
  声明了却没人查，就退回裁定 15 要防的那种情况

所以规则精确成一句：**重复是允许的，前提是有人核对。**

⚠️ 探针已验证守卫会红：删掉 risk 里的核对 ⇒ 测试失败。
「守卫写了却没见它红过，和没写是一回事」。

### 🔴 修复 · `phase-2-specialists.md` §3.9 设计的检查造不出来

原设计：*「同一次决策内，同一个 `source` 的 `raw_hash` 应当一致」*。
2.3 真要落地时才发现**这个检查一写出来就永远是红的**：

| agent | 端点 | 要几根 |
|---|---|---|
| `market` | `sina:kline/sh000001` | 25 |
| `technical` | 同上 | 120 |
| `sector` | 同上 | 2 |

同一个 `source` 标识，参数不同 ⇒ 报文必然不同 ⇒ 哈希必然不同。
分歧不是异常，是设计本身。

错在哪：`source` 是**端点的名字**，不是**那次请求的身份**。
文档没有悄悄换掉方案，而是留下了这段推理 ——
否则下一个人会照着同样的思路再设计一遍。
`Evidence.raw_hash` 因此**暂时没有消费方**（不删，回放比对里另有用途）。

### 新增 · `tools/verify/probe.sh` —— 手工探针跑在一次性库上

做上面那条验证时，我把 6 行假 verdict 写进了**真库**
（`task_id BIGA-20260921-900`）。`_store` 是追加式的，**它们删不掉**。

`BIGA_DB_PATH` 早就存在，但「记得设环境变量」的唯一执行者是人的记性 ——
而记性正是追加式设计要惩罚的东西。⇒ 做成比裸跑更短的默认路径。

> 追加式存储的好处是「当时看到的」可重建，代价是**手滑也一起被永久保存**。
> 这条代价在设计时没有写下来，现在补上。

### 变更 · 设计文档同步到 2.3 的真实状态

- `architecture.md` §10.1 加 **Phase 2 实测列**：加 3 个 agent，
  端到端 87.9s → **71.0s**。Stage 1 成本是 `max()` 不是 `sum()`
  （四个串行需 98.5s，实际墙钟 31.2s）；真正被压下来的是
  Stage 0+3 的固定开销（51% → 33%），靠 `verdict_ref` 让 Supervisor 不再重打 JSON
- `phase-2-specialists.md` 出口条件与实测块更新到 `BIGA-20260921-005`（6 agent）
- ⚠️ 两次实测都在**休市日**，Stage 1 多数 agent 因数据未形成而早退。
  盘中要重测 —— 在那之前不要把 71.0s 当成能力上限


### 🔴 修复 · 五 Agent 端到端把同一个坑在 emotion 和 market 里也照出来了

2.3 的端到端（周一 09:05 盘前，`BIGA-20260921-005`）跑出一张诚实的卡，
但逐字段核对上游 verdict 时发现 —— **sector 挡住的那个坑，emotion 和 market 没挡**：

| agent | 报了什么 | 实际 |
|---|---|---|
| emotion | `limit_up_count=0`、`max_streak=0`，`trade_date=20260921` | 盘前**尚未形成**，不是「涨停 0 家」 |
| market | `advance_count=0/0/0`，而 `trade_date=**20260918**` | 🔴 **把今天的空快照贴上了周五的日期** |

market 那条更严重：它不是「算不出来」，是**一个有日期的错值** ——
Card 上写着「9-18 上涨家数 0」，而那天真实是 4277。
日后做归因的人没有任何办法发现这是假的。

⇒ 共享判据 `_sources.session_in_progress(trade_date, retrieved_at)`：
这批数据所属的交易时段**还在进行中**吗。

- emotion：三个池全为 0 且时段进行中 ⇒ `emotion.pool.not_yet_formed`，
  不产出任何计数
- market：涨跌平三项合计为 0 ⇒ `market.breadth.not_yet_formed`（任何真实
  交易时段都不会三项全 0），不产出三个计数与占比

> **同一时刻，不同端点对「新一天还没开始」的表现是相反的**：
> 三个实时源（股池 / 涨跌家数 / 板块榜）清零，
> 两个历史源（新浪日线 / 腾讯行情）保留上一交易日。
> ⇒ 不能靠「有没有数据」判断，每个源都要自己判断。

### 修复 · 顺带删掉一个刚变成恒假的分支

加了 `not_yet_formed` 守卫之后，market 里「涨跌平合计为 0 ⇒ 分母为零」
那条 `else` **永远走不到了** —— 恒假分支就是 L-7，留着只会让人以为还有一条路。
已删，并加测试钉死它不会回来。

### 实测 · 五 Agent 端到端（`BIGA-20260921-005`）

```
emotion    UNKNOWN  无法判定    technical  PASS     空头
sector     UNKNOWN  无法判定    market     WARNING  缩量上涨
risk       UNKNOWN  无法判定
状态 WAIT · 情绪与板块数据双双失真，风控自称无法判定，本轮不具备下场条件
```

**technical 是唯一 PASS 的** —— 它用的是已收盘的历史日线，
按设计就不受盘前影响。这反过来印证了「数据源按谁能自证时间分工」那条。

测试 359 → 363。

### 新增 · `sector` 与 `technical`（2.3 —— 真正的「复制」那一步）

Stage 1 扩到 4 个。这一步没有新机制，正是用来验证
「新增 Specialist 不需要改核心架构」——
`_contract` 0 行改动，Supervisor 只在 roster 表加了两行。

- `skills/sector-calc/` 9 个字段：行业/概念涨幅榜、上涨板块占比、
  主力净流入榜与合计。交易日取自**新浪日线**（与 market 同源，
  因此 risk 核得出「大家说的是同一天」）
- `skills/technical-calc/` 14 个字段：MA5/10/20/60、`ma_order`、
  MACD 三线、RSI14、距 60 日高低点。**只做上证一个指数**

### 🔴 三个在这一步抓到的数据源陷阱

1. **`pz=600` 被静默忽略** —— 请求 600 行返回 100 行，而 `total` 照报 496。
   与 kline 的 `lmt` 被忽略同一类。已改为分页并核对总数 ——
   悄悄少几十个板块，涨跌分布就是错的，**而且不会报错**
2. 🔴 **盘前板块榜全部清零，而同一时刻行情端点保留上一交易日**。
   实测周一 08:48：板块榜 496 行 `pct` 全 0、领涨股全 `None`；
   而腾讯行情仍是 `3911.87 @20260918`。
   **同一时刻，不同端点对「新一天还没开始」的表现是相反的。**
   更危险的是榜单按涨跌幅排序 —— 全 0 时「第一名」是任意的一行，
   照着说「今日领涨板块是 X」完全是编造。
   ⇒ `BoardResult.nonzero_count` + `sector.board.pre_session` 守卫
3. 同一主机的 `clist` 返回 `diff` 是 **dict**，而 `ulist` 是 **list**。
   两处都写成数组解析，第二处会静默拿到空列表 —— 于是「今天没有板块上涨」

### 🔴 测试抓到的是写测试的人的误解

`test_单调上涨时macd柱为正` 失败了。查下来**代码是对的**：
完全线性的序列里 DIF 与 DEA 收敛到同一个常数，柱必然趋近 0
（实测 `DIF=7.0 DEA=7.0 HIST=0.0`）。

**MACD 柱衡量的是加速度，不是方向。** 已改为用加速上涨测柱、
用 DIF 测方向，并把这条写进 technical 的契约 ——
否则 Agent 会犯同样的错：看到柱为 0 就说「没涨」。

### 变更 · 范围外不进 `missing`

- sector 的「板块持续性」需要历史序列 —— 写进契约禁止 Agent 谈，**不塞 `missing`**
- technical 只做上证 —— 同上

理由见 `phase-2-specialists.md` §3.8：`missing[]` 是给
「本该有却这次没有」的。范围外每次都在，混进去就把真缺失淹没了。

测试 302 → 359。

### 🔴 安全 · 第三次「描述规则时把规则的内容抄进去」——  这次被 hook 拦下了

写开发流程时，在「哪些邻居内容不适用」那张表里**写了邻居的模块名**。
`pre-push` 报「邻居可识别细节 —— 1 处」并拒绝推送。

同一形状已经第三次：

| # | 在哪里 | 抄进去的是 |
|---|---|---|
| 1 | 记录「别泄露邮箱」的条目 | 邮箱本身 |
| 2 | 把审查脚本固化进 `TODO.md` | 脚本自己的正则（从此永远自匹配） |
| 3 | 开发流程的「不适用」表 | 邻居的模块名 |

**举例子的时候用了真值。** 已写进 `CLAUDE.md` 公开仓库纪律：
读者需要知道的是**类别**，不是**实例**。

值得记一笔的是：这次**没有靠人发现** —— 是那个 hook 在 push 前拦下的。
守卫第一次拦到的是写守卫的人。

### 新增 · 仓库内的开发流程 `.claude/skills/dev-workflow/`

🔴 **不改用户级那份。** `~/.claude/skills/dev-workflow` 是**邻居项目**的流程，
它引用 `deploy/`、阿里云同步，以及 `~/.openclaw/cron/jobs.json` ——
**照它的 Track D 做会直接破坏不变式 I-1**（BigA 不得以写模式打开 `~/.openclaw/`）。
改它还会波及邻居项目本身。⇒ 建项目级 skill，在本仓库工作时优先命中。

内容全部来自本项目这一路的实测，十条：

1. **设计先探活，测试再离线** —— 外部评审建议的「Offline First」对我们恰好是反的
2. 加东西的四个必答问题（事实归属 / 谁读 / 会不会经 LLM 转述 / missing 还是范围外）
3. **每加一个守卫，用探针验证它会红** —— 并确认探针跑完工作区已还原
   （踩过：探针脚本自己有 bug，导致后面几条结论全不可信）
4. 重构要**两个独立信号**：测试条数与内容都没动 + 真跑一次数字逐字节相同
5. 🔴 **诊断先看工具调用序列，再改提示词** —— 两次最有价值的诊断都来自这个动作，
   而两次的第一反应都是「提示词写得不好」，两次都错
6. 端到端**必须开新会话**（否则拿记忆回答），且**等 Supervisor 那轮结算完再测量**
7. **指标先问「它什么时候不该红」** —— 踩过三次：脚本自匹配、周末全红、
   在正确行为上报红
8. 报错要指路：给 Agent 看的错误必须包含下一步怎么做
9. 文档与 CHANGELOG 同 commit；commit message 贴实测数字；
   **不许把「改了指标让红灯变绿」说成「修好了」**
10. 明确列出邻居流程里**哪些不适用**及原因

### 新增 · 2.3 详设计（`phase-2-specialists.md` §3.7–3.9）

- **`technical` 做指数技术面，不做个股** —— Phase 2 不选股，没有个股输入。
  硬做就是第二个 `discipline`（没输入源却硬建，只能编）
- 🔴 **「范围外」与「数据缺失」不是一回事**：板块持续性需要历史序列，
  属于范围外 ⇒ 写进契约禁止 agent 谈，**不塞进 `missing`**。
  `missing[]` 是给「本该有却这次没有」的 —— 前者每次不同，后者每次相同，
  混在一起前者就被淹没了
- **同一端点被 market 与 technical 各采一次**，盘中可能拿到不同快照。
  不提前做采集缓存（那是 Phase 3），而是**让它可检测**：
  同一决策内同一 `source` 的 `raw_hash` 应当一致 —— 这是 2.1 加的
  `raw_hash` 的第二个消费方，也反过来证明它不是装饰

### 新增 · 文档规约（`docs/README.md`）+ 机器强制

文档已经乱到需要规则了：两个同名的 `reference/` 目录、一个只放了一个文件的
`prompt/`、以及 `phase2.md` 这种「什么的 phase 2」的名字。

🔴 **按生命周期分类，不按主题分类。** 主题会重叠（market 的设计算 design 还是
tutorial？），生命周期不会 —— 而真正出问题的恰恰是生命周期。

| 目录 | 类别 | 生命周期 | 文件名 |
|---|---|---|---|
| `design/` | 常青 / 阶段 | 永远描述当前状态 / 完成后冻结 | `<主题>.md`、`phase-<N>-<主题>.md` |
| `tutorial/` | 过程 | 写完即冻结 | `NN-<主题>.md` |
| `guide/` | 操作 | 跑不通就是错的 | `<动作>.md` |
| `external/` | 只读 | **永不修改** | `YYYY-MM-DD-<来源>-<主题>.md` |

- 只有 `external/` 的文件名带日期：**我们自己的文档历史在 git 里**，
  文件名带版本号等于邀请别人新建 `v2` 而把旧的留在原地
- 每份文档开头必须写 **覆盖什么 / 不覆盖什么** ——
  这次混乱的根因不是缺文档，是没人说清边界，于是内容往最近的那份里落

### 新增 · `docs/design/phase-1-walking-skeleton.md`

Phase 1 的设计原本是 `architecture.md` 的 §11 —— **一段历史躺在常青文档里**。
后果实测过：那份文档顶部长期写着「设计中，未开工」，
而那时 Phase 2 都做完两步了。**读者没有办法判断哪些还作数。**

⇒ 抽成独立文档并标注「已完成并冻结」，原处留一行指针（编号不动，引用不断）。
内容在原 §11 之上补了最终结果：九项验收、74.8s / $0.2179、
三个「以慢表现的 bug」、以及 Phase 1 留给后面的四笔账。

### 变更 · 文档重组

- `docs/reference/` + `docs/design/reference/` → **`docs/external/`**（两个同名目录）
- `docs/design/install-guide.md` → `docs/guide/install.md`（安装手册不是设计）
- `docs/prompt/` → `docs/guide/`
- `docs/design/phase2.md` → **`docs/design/phase-2-specialists.md`**
  —— `phase2.md` 答不出「什么的 phase 2」
- ✅ `tests/test_docs_convention.py`，**六条探针逐个验证过会红**：
  按步骤切分阶段文档 / 带版本号 / phase 名没主题 / 外部材料无日期 /
  重建歧义目录 / 没写「不覆盖」

### 实测 · Stage 2 端到端通过（`BIGA-20260921-004`）

```
emotion  PASS     stance='修复'
market   WARNING  stance='缩量上涨'
risk     UNKNOWN  stance='无法判定'
状态：WAIT · 情绪与大盘读数偏暖，但风控因板块/消息/技术面缺席无法判定
```

🔴 **risk 没有安静放行** —— 它说「无法判定」并点名了缺席的三个领域。
这正是制衡层该有的行为：**一个说不上话的风控，必须让人看出它说不上话。**

- risk 只用 **2 次工具调用 / 5.9s**：跑 `risk_check --verdict-ids 32,33`，
  再提交 stance。全程没碰任何数据源
- 端到端 **87.9s**（三个 Agent），仍在 90s 预算内
- Stage 1 真并行（重叠 24.7s），Stage 2 在其后

### 修复 · 并行判据在「正确行为」上报红了

risk 上线后第一次跑，`--parallel-check` 报「emotion 与 risk 区间不相交 ⇒ 串行」。
但 risk 是 Stage 2，**它本来就该在 Stage 1 之后** —— 那是冻结证据的前提。

> **在正确行为上报红的检查，比漏报更糟。** 它会被训练成噪音，
> 等哪天报的是真问题，也已经没人看了。

修法不是加例外，是把拓扑提到契约层 `STAGE1_AGENTS` / `STAGE2_AGENTS` ——
`risk-check`（算覆盖率）与 `latency_report`（判并行）都从这里读。
各写一份名单必然漂，而漂开的表现正是这种误报。

顺带得到一条**新的正确性检查**：Stage 2 若与 Stage 1 重叠，
说明它读的是**还没冻结**的证据 —— 那时回放重建不出「当时为什么放行」。

测试 248 → 251。教程第 13 章。

### 🔴 变更 · 否决权从 `verdict` 挪到 `stance`（2.2 第 1 步）

建 risk 之前先解决了一个自相矛盾：`DecisionCard` 用 `verdict == 'BLOCK'`
作为否决权判据，而我们刚在 §4.1.1 写死「`verdict` = 数据完整度」。

**一个字段装不下两件事**：risk 数据完整且要否决时，`verdict` 填 `BLOCK`
就再也说不出「它的数据是全的」—— 而「否决」与「凭什么否决」恰恰都需要知道。

- `VerdictLevel` 去掉 `BLOCK`，只剩 `PASS / WARNING / UNKNOWN`
- 新增 `VETO_STANCE = "否决"` 常量，`STANCE_VOCAB["risk"] = (放行/警示/否决/无法判定)`
- 🔴 否决这个字面量**只许有一处定义** —— 改了词表却忘了改判据，
  否决权会**静默失效**，而那正是最怕的 fail-open。测试钉死两者同源
- 🔴 **补了反向约束**：有人否决，Card 状态就必须是 `AVOID` 或 `BLOCK`。
  原来只拦 `BUY`，于是「否决」可以被显示成 `WAIT` ——
  而 `WAIT` 是「再看看」，否决是「不要做」，把后者显示成前者就是软化了制衡层

### 新增 · `skills/risk-check/` 与 `risk` Agent（2.2）

Stage 2 制衡层。9 个字段：覆盖率 / 上游缺失数 / 交易日一致性 / 最旧证据年龄 /
交易时段是否进行中 / 触发的阈值 / 上游判断互斥 / 到场 Agent / 上游交易日。

- 🔴 **结构上保证它不采数据**：`risk_check.py` 不 import `_sources`，
  无任何网络依赖，由 AST 测试钉死。理由是制衡的意义所在 ——
  它要审的是**别人据以下结论的那份证据**；自己重采一遍就成了审自己的幻觉，
  且回放时两边对不上
- 输入是 Stage 1 的 `verdict_id`（2.1 建的 `agent_verdicts` 正好是它的入口）
- 阈值写死并带版本号，**skill 只报「哪些线被碰到了」，不报风险等级**（铁律 4）
- 契约里最硬的一条：**覆盖不足是「无法判定」的理由，不是「放行」的理由。**
  否决的意思是「我看到了风险」，不是「我没看清」

### 修复 · 一个在周末必然全红的指标

- risk-check 第一版数「超过 120 秒的证据有几条」，非交易日跑出来
  **25 条证据全部陈旧** —— 因为 as_of 停在上一交易日收盘，距今几十小时
- **一个周末必报的指标等于没有指标**（与 R-3 同源）。改为只报两个事实：
  `max_staleness_sec`（年龄）+ `session_live`（那个交易时段还开着吗），
  「几十小时算不算陈旧」由 Agent 结合后者判断

### 变更 · 时间统一北京时间

见下方「时间」条目。`tools/verify/agent_trace.py` 一并新增。

### 修复 · stance 的代价不是「思考」，是「找不到词表」

前一条记的「stance 让 Stage 1 慢了一倍」已解决，**而原因和我最初的判断相反**。

拆开 Specialist 那一轮的轨迹才看清：62 秒、12 次工具调用，
全花在**找 stance 词表在哪**上 ——

```
--help → grep stance → grep vocab → grep STANCE_VOCAB
→ 猜错路径 → find . → 🔴 find /   （契约明令禁止的那条）
→ 终于 python3 -c "from _contract import STANCE_VOCAB"
→ 才调对 amend_verdict
```

两个根因：

1. 🔴 **我写的是 `--stance <下表里的一个词>`，而那张表不存在。**
   契约里只有输出格式那行列了选项，Agent 没把两者联系起来 ——
   于是它去搜。这正是 Phase 1 学到的「照抄这段，不要去读源码」，我没做到
2. 🔴 **运行时会把那些命令的输出吞掉**：`exitCode=0`，但 Agent 看到的是
   `[Malformed diagnostic JSON redacted]`。**它连 `--help` 都读不到** ——
   所以越搜越远。⇒ 契约必须自包含，任何「去查一下」的指引都是无效的

修法：把词表连同「什么时候用哪个词」直接写进契约正文，
并给出两条可以照抄的完整命令。

| 运行 | 工具调用（market/emotion） | Stage 1 | Specialist 输出 tok | 端到端 |
|---|---|---|---|---|
| 加 stance 前 | — | 30.0s | 1375 / 1097 | 71.0s ✅ |
| 加 stance 后 ① | **13 / 9** | 103.9s | 3893 / 2841 | 133.5s ❌ |
| 加 stance 后 ② | **8 / 12** | 82.0s | 3178 / 3068 | 115.7s ❌ |
| **词表内联后** | **2 / 2** | **45.6s** | **2443 / 1218** | **70.6s ✅** |

2 次调用 = 跑 skill + 提交 stance，正好是设计的样子。

> **通用原则：Agent 的「慢」，多数时候是它在找东西，不是在想事情。**
> 看轨迹里的工具调用序列，比猜提示词写得好不好有用得多。

### ~~⚠️ stance 的代价：Stage 1 慢了一倍（实测，未解决）~~（已解决，见上）

真 agent 验证通过 —— 两个 Specialist 都提交了 stance，缺失项都带代码，
证据都带 `raw_hash`。但延迟回退了：

| 运行 | Stage 1 墙钟 | Specialist 输出 tok | 端到端 | 成本 |
|---|---|---|---|---|
| `-004` 加 stance 前 | **30.0s** | 1375 / 1097 | **71.0s** ✅ | $0.181 |
| `0921-001` 加 stance 后 | 103.9s | 3893 / 2841 | 133.5s ❌ | $0.675 |
| `0921-002` 再跑一次 | 82.0s | 3178 / 3068 | 115.7s ❌ | $0.644 |

🔴 **是结构性的，不是缓存**：两次跑下来 Specialist 的输出 token 稳定在
~3100（此前 ~1200），时间基本随输出线性增长。多出来的是
「选哪个 stance 词」的推理与那条 `amend_verdict` 命令本身。

⇒ **不把它记成「已完成」。** 换来的是 Phase 4 唯一可用的方向判断数据，
这笔交易值得做；但 90s 预算又被打破了，Stage 1 现在是瓶颈。
候选方向（尚未验证）：把契约里 stance 那一节从「解释为什么」压成一张决策表，
让 Agent 少推理；或者让 skill 在事实齐备时给出一个**候选** stance 由 Agent 确认。

### 新增 · 可追溯性的三个空白（来自外部设计评审）

一份 40 多条的评审文档，指出了三个我们确实有、而且自己没看见的空白。

- **`AgentVerdict.stance`** —— 方向判断成为结构化字段。
  在此之前它**只存在于 Agent 的自然语言回复里**，意味着
  Phase 4 要检验「BigA 说强的时候后面几天怎么样」时，**那一列根本不存在**。
  三个字段分工写进 `architecture.md` §4.1.1：
  `status`=跑完了吗 / `verdict`=判断有效吗 / `stance`=判断是什么
  - 取值必须在 `STANCE_VOCAB[agent]` 固定词表里 ——
    自由文本的判断没法聚合，等于没记
  - 耦合校验：`verdict='UNKNOWN'` 时不许有方向（数据都不够，方向从哪来）
- **`Evidence.raw_hash`** —— 指回 `raw_market_snapshot.content_sha256`。
  在此之前「这个结论基于哪份数据」只能靠时间戳猜。
  派生字段允许为空，**不硬凑** —— 凑出来的溯源比没有溯源更糟
- **`MissingItem`** —— 缺失项 = 机器可读代码 + 人话。
  散文缺失项只能数次数，说不出是不是同一个源挂了五遍，
  而 Phase 2 的出口条件恰好是「`missing[]` 真实非空 ≥5 次」

### 🔴 三处刻意保守的设计

- **`MissingItem` 做成 `str` 的子类**，不是普通 dataclass。
  仓库里到处是 `f"{m}"`、`"某某" in m`、`set(missing)` ——
  换成 dataclass 会让这些地方**静默改变行为**（卡上会打出 `MissingItem(code=...)`）。
  改完之后**193 条既有测试一条没动**
- **「必须有 stance」这条校验放在在线合成路径，不放契约层。**
  放契约层会在 `from_dict` 时同样生效 ⇒ Phase 1/2 落的四张卡再也回放不了。
  **新规矩对新数据严格，对旧数据只要求可读** —— 回放能力优先于形式一致
- 裸字符串 `missing` 收编为 `legacy.unclassified`，
  **不给历史数据编一个像样的代码** —— 那等于伪造分类

### 修复 · 设计文档欠的账

- `architecture.md` §4.1 只写了三个数据结构、字段停在 Phase 1；
  §5.3 的表清单**漏了 schema v3 的 `agent_verdicts`**。均已同步
- §9 失败模式表补 **L-10「结构化数据经 LLM 转述」** ——
  这是表里**唯一一条本项目自己踩出来的**，其余九条继承自那套长期运行的系统。
  同时更正了「下面九条」这种会随表增长而失效的表述

### 评审里没有采纳的四条（附理由）

- **「Offline First, Live Last」**（先写 fixture、最后接真实数据源）——
  对我们恰好是反的。market 的数据源选型完全来自先探测真接口：
  `涨跌幅=-29971017728.0`、`lmt=25` 被忽略返回全历史。
  **先写 fixture 等于把一个坏掉的 API 形状固化进测试。**
  正确表述是「测试要离线，**设计要先探活**」
- **`volume_state = EXPANDING/NORMAL/SHRINKING` 由 skill 产出** ——
  违反铁律 4（skill 不做判断性归类）。我们给 `volume_ratio` 这个数，归类由 agent 做
- **涨停/跌停归 market** —— 直接冲突裁定 15，会重新造出「同一事实两个生产方」
- **每个 skill 自带 `fetch_market.py`** —— 会重新复制 HTTP 客户端，
  正是刚抽成 `_sources` 要避免的
- 另一处事实修正：评审建议用**深证成指**(399001)，我们用**深证综指**(399106) ——
  成指只覆盖 500 只成分股，而涨跌家数与成交额是全市场口径，混用会对不上

测试 193 → 215。教程第 12 章。

### 实测 · 合成轮瓶颈解除，端到端回到预算内

| 运行 | 缓存 | Supervisor 总时长 | 它的输出 tok | 端到端 | 成本 |
|---|---|---|---|---|---|
| `-002` 修前 | 冷 | **159.1s**（单轮） | **9486** | 159.1s | $0.6835 |
| `-003` 修后 | 冷 | 48.5s | 2225 | 100.6s | $0.5615 |
| `-004` 修后 | **热** | 50.5s | 2308 | **71.0s ✅** | **$0.1812** |

- **Supervisor 时间 −68%，输出 token −76%** —— 消失的正是「抄 JSON」与
  「抄错了再自救」那两段
- 热缓存下端到端 **71.0s**，回到 90s 预算内
- `retrieved_at` 恢复真实：`-003` 上 market 是 21:13:30、emotion 是 21:13:48
  —— **两个不同的、各自真实的采集时刻**，而 `-002` 上 25 条全是同一个编出来的数
- ⚠️ Stage 1 的方差很大（冷 63.6s / 热 30.0s），n=2 还不足以推 8 agent 的预算。
  但瓶颈确实从 Stage 3 转回了 Stage 1 —— 而 Stage 1 是并行段，
  它的代价是「最慢那个 specialist」，不是相加

### 修复 · 延迟报告把相邻两次运行并成了一个窗口

- 向前串联轮次时只看「间隔 < 120s」，结果两次相隔 67s 的决策被并起来，
  报出 **238.7s「超预算 2.7×」**，而真实值是 **71.0s（达标）**
- 🔴 **一个会虚报 3 倍的指标，比没有指标更糟** —— 它会让人去优化一个不存在的问题
- 已加硬边界：向前串联到**上一张卡的落库时刻**为止。
  自报 `elapsed_ms` 越过上一张卡时同样截断，并在标题里说明截过

### 🔴 修复 · Decision Card 上的 `retrieved_at` 是编出来的（数据完整性）

诊断始于「Supervisor 合成轮 159.1s」这个延迟问题，挖下去发现的是完整性问题。

**证据**（2026-09-20 `BIGA-20260920-002`，逐条核对轨迹）：

| | |
|---|---|
| skill 实际输出 | 15 条 evidence，**每条都有 `retrieved_at`** |
| Specialist 转述后 | `as_of` 34 条，`retrieved_at` **0 条** |
| skill 真实采集时刻（raw 层记录） | **20:42:48** |
| 落库 Card 上全部 25 条 evidence 的 `retrieved_at` | **20:44:34**（差 106 秒） |

25 个各不相同的采集时刻，被压成了「Supervisor 敲 heredoc 的那一刻」。

更糟的是 main **给自己写了一份恢复文档**（`workshop-skills/decision-card-ops`），
把补救写成了标准操作：「transcribing 时给每条加一个 `retrieved_at`
（用当前合成时间）」、「Specialist 把 evidence 缩写成 `[同上…]` 时，
从 `result` 反向重建条目」。

⇒ **「事实可追溯」这条地基，在最后一公里被 LLM 的复述打穿了。**
   该文档已退休 —— 留着就是继续教 agent 手工重建证据。

### 新增 · schema v3 `agent_verdicts`：结构化数据不经过 LLM

- skill 算完**直接把判定原件落库**并在 stderr 打出 `verdict_ref=NN`
- Specialist 交给 Supervisor 的是**编号**，不是 JSON
- `synthesize.py --verdict-ids 17,18` 按编号取原件
- `amend_verdict.py --ref 17 --add-missing … --verdict WARNING` ——
  Specialist 追加缺失项**不重打 JSON**，而是写一行指回原件的修订
  （`amends` 列，与 `decision_records.replay_of` 同一套做法）
- 表只追加不修改，由触发器强制；修订必须写 `amend_reason`
- 🔴 `--verdicts <文件>` 保留但只留给手工调试。新增测试
  `TestContractsTeachTheSafePath` 钉死：**三份 `AGENTS.md` 里不许再出现贴 JSON 的写法**
  —— 守卫再硬，agent 照着文档做还是会走老路

**为什么这同时解决了延迟**：搬运成本从 `O(evidence 数)` 变成 `O(1)`。
159.1s 那一轮里，47s 是零工具调用地生成 6460 字符 JSON，
49s 是因缺字段失败两次后的自救 —— 两段都直接消失。

测试 176 → 193。

### 新增 · Stage 1 并行判据与端到端实测（2.1 第 5 步）

- `latency_report.py --parallel-check`：判据是**两个 specialist 的时间区间
  是否相交**，不是「总耗时变短」。后者会被网络波动掩盖，区间相交是结构性证据。
  只有一个 specialist 时报「**判不了**」而不是「通过」（R-3 对验证工具本身同样适用）
- 窗口不再依赖 Card 自报的 `elapsed_ms`，改为**从轨迹向前串联轮次反推**。
  原来的写法要求 Supervisor 自己做时间减法 —— 而契约禁止它做算术，
  实测它直接省略，于是 `elapsed_ms=0` 退化成一个 10 分钟的粗略窗口

### ✅ 并行被证明了，❌ 但延迟预算崩了 —— 两件事都要说

实测 `BIGA-20260920-002`：`market` PASS 15 条证据 + `emotion` PASS 10 条证据。

| 项 | 结果 |
|---|---|
| Stage 1 真并行 | ✅ 区间相交 28.9s，墙钟 41.5s vs 个体之和 70.4s |
| 端到端 < 90s | ❌ **159.1s，超 1.8×** |

🔴 **崩的位置恰好是设计文档预判的那个：Stage 3，不是 Stage 1。**

| | Phase 1 · 1 个 | 2.1 · 2 个 | 变化 |
|---|---|---|---|
| Stage 1（并行段） | 36.0s | 41.5s | +15% |
| Supervisor 合成 | 30.0s | ≈111s | **×3.7** |
| Supervisor 输出 tok | 1668 | 9486 | ×5.7 |

> **并行解决的是扇出，解决不了汇聚。** 扇出段加一个 agent 只付「最慢那个」
> 的差价；汇聚段加一个 agent，是实打实多一份要读、要比对、要写进结论的材料。

⚠️ n=1 且冷缓存，不足以推 8 agent 的预算 —— 但足够推翻
「加 agent 的主要成本在 Stage 1」这个假设。

### 修复 · 三个在端到端实测中被抓到的问题

- 🔴 **Supervisor 违反自己的契约，在非交易日用了严格日期模式**
  （日志原话：「已按 Supervisor 要求以 `--date 20260920` 严格模式核对」），
  于是两个 Specialist 双双 `UNKNOWN`、`evidence` 为空，
  产出一张**零证据的空卡** —— 而当时 09-18 的完整数据是拿得到的。
  契约里那段「默认不要指定日期」不够硬，已改为**照抄式的 spawn 指令模板**
  + 明令禁止 + 把这次的实测后果原样写进去
- 🔴 **`latency_report` 会在缺 Supervisor 轮次时照样报「达标」。**
  成因是测量时机：Supervisor 在自己那一轮的**中途**落卡，卡一出现就跑报告，
  它那一轮还没写进 trajectory。实测漏报过一次：报 **41.5s「✅ 达标」**，
  而缺掉的合成轮是 **159.1s**。
  ⇒ 现在缺 Supervisor 轮次时**不许判达标**，并明说「上面的数字偏小」。
  **这正是这个脚本存在的理由，它差点自己犯了它要防的错**
- `--all` 模式下的并行检查跨越多天，报出过 97502s 的「重叠」。
  已跳过 —— 一个在错误模式下仍然给结论的检查，比不给结论更危险

### 新增 · agent `market` 与 Supervisor 的并行契约（2.1 第 4 步）

- `agents add market`，并照教程 06 清掉脚手架五件套：嵌套 `.git`（会让父仓
  当 submodule）、`BOOTSTRAP.md`、`SOUL.md` / `IDENTITY.md`（spawned session
  不加载）、`USER.md`
- `agents/market/AGENTS.md` 角色契约。三段是 market 特有的：
  - **最有价值的判断是「背离」**：指数涨而上涨占比 <40% ⇒ 权重在拉指数、
    个股在普跌，「大盘涨了」是失真的。这一层只有 market 看得到，要主动说
  - **「成交额是地量/天量」不许说** —— `turnover_total` 没有历史基线。
    要谈量的高低只能引用 `volume_ratio`（它有 20 日均量做底）。
    这是最容易犯的一句话：「2.07 万亿属于温和水平」里的「温和」没有依据
  - 涨停炸板连板明确划给 emotion，涨跌家数明确划给自己（裁定 15）
- 配置三处，**两端都要列**（只改一边不会报错，只会「工具不可见」）：
  `main.allowAgents` 加 `market`、`agentToAgent.allow` 加 `market`、
  `market.allowAgents: []`（叶子节点）
- 补上 `announceTimeoutMs: 120000` —— `architecture.md` §3.2 的骨架里有，
  实际 config 一直缺

### 🔴 Supervisor 契约：多个 spawn 必须在同一条消息里发出

- 分两轮发就是串行，而串行的表现是 **每步成功、Card 照常产出、日志全绿，
  只是慢了一倍** —— 与此前抓到的三个 bug 同一族：以「慢」表现的正确性问题
- 判据写死为「两个子会话的时间区间**是否相交**」，不是「这次跑得快不快」。
  区间相交是结构性证据，快慢会被网络波动掩盖
- 同时补上「spawn 了几个就要等几个」：少等一个而照常出卡，那张卡会
  **看起来完整**，而它少了一整个领域的证据，且缺失项里什么都不会写 ——
  因为你根本没意识到少了

### 变更 · emotion-calc 移出涨跌家数（2.1 第 3 步，裁定 15 落地）

- `advance_count` / `decline_count` / `flat_count` 与 `collect_breadth` 全部移除，
  Evidence 13 → 10。**情绪分公式只用 涨停家数 / 最高板 / 炸板率，派生值一个没变**
  （实测仍是 55.52）
- 新增 `tests/test_field_single_producer.py` —— 全仓 AST 扫描，
  同一个字段被两个 skill 产出即红。已用探针验证：把 `advance_count`
  加回 emotion，立刻失败
- 🔴 **这个守卫当场抓到一个我没想到的情况：`trade_date` 也是两个 skill 都在产。**
  想清楚之后它**不该**按裁定 15 禁掉 —— 它不是「同一个事实的两份实现」，
  而是各自的**溯源元数据**：emotion 的来自股池 `qdate`，market 的来自日线 `day`。
  **两者不一致本身就是一条重要信息**（某个源陈旧了），强行只留一份等于抹掉这个信号。
  ⇒ 规则改为：溯源字段不但不禁止，反而**要求每个 skill 都有**；
  跨 agent 的一致性核对是 Supervisor 在 Stage 3 的职责

### 修复 · confidence 永远到不了 1.0

- emotion-calc 的分母写死 15，而它实际只产 13 个字段 ——
  **数据完整时也只读到 0.87，「完整」这件事永远表达不出来**。
  现改为钉死真实字段数（10）并由测试守住，与 market-calc 同款

### 新增 · `skills/market-calc/`（2.1 第 2 步）

15 个字段：指数点位/涨跌幅、两市成交额、量能比、涨跌家数与上涨占比。
实测一次（2026-09-18 收盘）：上证 3911.87 +0.94% / 深综 2512.63 +1.67% /
两市成交额 20771 亿 / 量能比 0.9888 / 涨跌平 4277·1173·180。

- **三个源，各自只干它能自证的那件事**：新浪日线给交易日（每行自带 `day`，
  **权威**）+ 点位 + 涨跌幅 + 量能基线；腾讯给成交额（14 位时间戳，可交叉校验）；
  东财 ulist 只给涨跌家数（**没有日期**，只能推断）
- 新增 `_sources/sina.py` / `_sources/tencent.py`，以及 `http.get_text()`
  （腾讯返回 GBK；编码猜错**不会报错**，只会让中文名变乱码，所以是显式参数）
- 五条守卫，每条都对应一次真见过的失败形状 —— 并且**六条探针逐个验证过会红**
  （把守卫拆掉，对应测试立刻失败）。守卫写了却没见它红过，和没写是一回事

### 修复 · 两个在写 market-calc 途中被自己抓到的问题

- 🔴 **`turnover_sh` 会凭空消失。** 早先把整个 per-market 循环体挡在
  `if d is None: continue` 后面 —— 新浪日线一挂，来自**腾讯**的成交额
  既不产出、也不进 `missing[]`，**Card 上什么都不留下**。
  这正是 R-3 要防的形状：不是算错，是那一行根本不存在了。
  已解耦，并在无法交叉校验时发 warning「该值只有单源支撑」
- `confidence` 分母写成 14（实际 15 个字段）⇒ 契约层当场拒绝构造。
  **故意不做 `min(…, 1.0)` 截断**：截断会把「加字段忘改常数」变成一个悄悄偏小
  的置信度；不截断则当场炸。已加测试把它从生产挪到 CI 里炸

### 修复 · 🔴 emotion-calc 盘中会直接崩掉（Phase 1 埋的 bug，2.1 途中发现）

- **症状**：交易日盘中跑 `emotion-calc`，抛 `ValueError` 整个退出 ——
  **连一条 missing 都留不下**。收盘后与非交易日不受影响
- **根因**：`as_of` 的换算把交易日一律当成「当日 15:00 收盘」。
  盘中 10:00 采数据时 `as_of`(今天15:00) > `retrieved_at`(今天10:00)，
  而契约层校验「数据不可能早于自身被取回」⇒ 拒绝构造
- 🔴 **为什么 Phase 1 九项验收全过却没发现**：那几天的实测
  **全部发生在收盘后或非交易日**（09-18 晚、09-19、09-20 周日）。
  验收覆盖了三条 R-3 路径，却没覆盖「什么时候跑」这一维 ——
  **而 BigA 是短线决策系统，盘中才是主场景**
- **修法**：换算抽成共享实现 `skills/_sources/tradetime.py`，
  它认得「今天尚未收盘 ⇒ 这是盘中快照，as_of 退回取回时刻 + 必须发 warning」。
  盘中快照与全天结果不是一回事，不标出来读卡的人就会当成收盘数据
- 放进 `_sources/` 而不是各 skill 自己写：market-calc 要用同一套换算，
  各写一遍就会有一个记得这个分支、另一个不记得
- ✅ **探针验证会红**：把修复退回旧行为，新测试立刻以
  「`as_of` 晚于 `retrieved_at`」失败。测试 124 → 130

### 变更 · 抽出 `skills/_sources/` 共享采集层（2.1 第 1 步）

- `emotion-calc/scripts/sources.py` → `skills/_sources/`：
  `http.py`（重试 / 退避 / 请求头的唯一实现）+ `eastmoney.py`（股池与涨跌家数）
- 🔴 **为什么现在动 Phase 1 已验收的代码**：`market-calc` 需要同一套
  重试 / 退避 / 备选主机链。各写一遍就会慢慢漂开，**而漂开时不报错** ——
  表现只是某个源偶尔多一条 missing。第二个消费方出现的那一刻就是抽取的时刻
- `get_json(url, *, referer)` 的 `referer` **故意不给默认值**：
  这些接口按 Referer 拒绝请求，给了默认值会让「忘了设」表现为偶发失败而不是报错
- 只搬已有的东西，**没有顺手加 `get_text()` 之类给下一步用的接口** ——
  那会是一个零消费方函数（L-1）。sina / tencent 的适配等第 2 步真需要时再加

**验证这次重构是空操作的两个独立信号**：

1. **124 条测试一条不变**（不是「仍然全绿」，是数量与内容都没动）。
   测试里只加了一行 `import _sources as sources`，局部名保持不变，
   其余 200 余处引用零改动 —— **改测试就把这个信号弄脏了**
2. **真采一次，数字与 Phase 1 记录逐位相同**：涨停 78 / 炸板 25 / 跌停 0 /
   炸板率 0.2427 / 最高 4 板 / 梯队 `{1:66,2:8,3:2,4:2}` / 情绪分 55.52 /
   涨跌平 4277·1173·180

### 新增 · 2.1 设计：`docs/design/phase2-market.md`

三个裁定，都是分析出来的而不是照抄 roadmap：

- 🔴 **市场宽度归 `market`，`emotion` 删掉那三条**（裁定 15）。
  上游文档把宽度同时派给了两个 agent；而宽度端点是**不带日期的实时快照** ——
  两个 agent 并行各调一次，同一张 Card 上就会出现**同一个字段两个值**，
  两个都带推断出来的 `as_of`。实测坐实：周日请求返回的数与上一交易日逐位相同
  （`4277/1173/180`），它把最后一个交易日冻在那里反复发
- **抽出 `skills/_sources/` 共享采集层**。market 需要 emotion 那套
  重试/退避/备选主机链，抄一份就是两套策略慢慢漂开。
  动 Phase 1 已验收代码是有意识的：**第二个消费方出现的那一刻就是抽取的时刻**
- **量能纳入 2.1**（需要 25 天日线）。只给今日成交额，agent 必然拿一个脑补的
  均值去比 —— 直接违反铁律 3「Agent 不做算术」。而日线顺带就是权威交易日的来源

### 🔴 数据源探测推翻了最省事的方案（实测记录）

原计划「给宽度那次请求多加几个字段，一次拿到宽度+点位+成交额」不成立：

- `push2delay` 加行情字段 ⇒ 宽度对，但**指数字段全是垃圾**：
  最新 `0.0`、涨跌幅 **`-29971017728.0`**。
  `rc=0`、字段齐全、类型正确 —— **任何「是不是数字」的检查都会放过它**
- `push2` / `push2his` 行情端点**直接断连**；`push2his` kline 一次返回 483KB
  后被截断，因为 **`lmt=25` 被忽略，它给了全历史**
- ⇒ 改为三源分工：新浪日线做脊梁（自带日期 + 同单位 + 量能基线）、
  腾讯给成交额并交叉校验日期（自带时间戳）、`push2delay` 只取宽度
- ⚠️ **单位陷阱**：新浪日线 `volume` 是**股**、腾讯是**手**，差 100 倍且
  乘 100 后完全相等。量能基线与今日量必须同源，跨源做比值就是静默错 100 倍；
  反过来这个恒等式成了一条免费的双源校验

### 2.1 的真验收是「并行被证明过」，不是「market 能出数」

- `maxConcurrent: 6` 配了，但仓库里只有一个 specialist ⇒ **并行至今零次实测**
- 判据定为**两个 specialist 的轮次区间必须相交**，而不是「总时长变短了」——
  后者可能只是这次网络快。**区间相交是结构性证据，快慢不是**
- Supervisor 契约要写死「两个 spawn 在同一条消息里发出」：
  分两轮发就是串行，**而日志照样全绿** —— 与三个「以慢表现的 bug」同一族


---

## [0.2.0] - 2026-09-20

**Phase 1 验收完成。** `0.1.0` 走通了最细的一条链路，这一版把它**证明**了：
延迟与成本可独立测量、观测工具自身的缺陷被修掉、审查从「记得跑」变成
「跑不过就推不上去」、仓库转 Public。

🔴 **本节是补切的。** Phase 1 于 2026-09-20 验收并合入 `main`，
但当时没有发版 —— 于是它的 17 条记录和整个 Phase 2 一起堆在 `[未发布]` 里，
读的人分不清哪些已验收、哪些还在做。

> 通用原则：**版本号的作用是划出「已被证明」与「正在做」的边界。**
> 不切版本，`[未发布]` 就会膨胀成一份流水账 —— 这次积到了 88 条。

### 修复 · Phase 1 收尾：三处文档漂移（L-6 的防护对象是它自己）

Phase 1 九项验收全过之后，文档里留下了三处与事实不符的地方。
**L-6 之所以危险，是因为文档不会自己报错，而人会照着它做判断** ——
所以收尾时逐项核对了一遍：

- `TODO.md` 的「端到端跑通」仍是未打勾状态，条目末尾还写着「教程 07 待写」，
  而第 07 章早已写完、README 与教程索引都标 ✅。
  改成已完成并补上章节链接
- `README.md` 说 `images/` 含「三张 Logo」，实际有四张（多一张红底白字的方形
  App 图标）。顺手把每张图的**用途和底色**写清楚 —— 之前只写编号，
  取图的人还得逐个点开看
- 目录结构里的 `LOGO_BigA01/02/03` 同步改为 `LOGO_BigA01–04`

### 新增 · `pre-push` hook：把「记得跑审查」变成「跑不过就推不上去」

- `tools/verify/audit_public.sh` —— 八项检查的**唯一实现**，三种扫描范围：
  `--worktree`（commit 前）/ `A..B`（增量）/ 默认 `--all`（体检）。
  全历史一次 0.18s
- `tools/git-hooks/pre-push` —— 推送前扫**本次新增的提交**，不过就拦下。
  装法 `git config core.hooksPath tools/git-hooks`（仓库级 config，克隆后各自跑一次）
- 🔴 **hook 扫增量、体检扫全历史，这是有意区分的。** 已公开的历史撤不回来，
  让 hook 为它每次报红 = 造一个永远报警的检查，**而永远报警的检查会训练出忽略**。
  拦截必须只对「你现在还能改变的东西」报红
- 🔴 **顺手消掉一处第二套口径**：`CLAUDE.md` 里那段「快速自查」用的是**另一套正则**
  （比八项少两项、多一项 `appsecret`）。两套模式定义共存时，
  **改了一份忘了另一份，剩下那份仍然报绿** —— 正是 L-3。
  已改为指向同一个脚本的 `--worktree` 模式
- ✅ **探针验证三条路径都真的会红**（守卫不验证「会红」等于没验证）：
  worktree 口径 3 项命中 / 增量口径 2 项命中 /
  `git push --dry-run` 被 hook 拦下并返回非零。探针分支已删除，未推送

### 变更 · 仓库已转 Public（2026-09-20）⇒ 审查的触发条件跟着变

- 八项安全审查全绿后转为公开，`main` 与 `phase2` 两个分支现在都对外可见
- 🔴 **审查脚本的触发条件从「每次转可见性前」改为「每次 push 前」。**
  仓库公开之后 **push 即发布** —— 原来那句话隐含了一个不再存在的窗口
  （先推上去、转公开之前再检查）。而已公开的内容撤不回来：
  删分支也可能留在 fork、缓存与镜像里 ⇒ 顺序只能是「先审查、后 push」
- ⇒ 前一条「审查脚本改扫 `--all`」从「以防万一」变成了**载重的**：
  `phase2` 与 `main` 一样公开，只扫 `main` 就是漏掉正在写的那一半
- ⚠️ 顺带修掉一处自己刚犯的漂移：`TODO.md` 里仍写着「当前私有」，
  而实际已公开。**照着过期文档回答问题，会给出言之凿凿的错答案** ——
  这次就是（有人问「转了吗」，答了「仍是 Private」）。判断可见性要查 API，不查文档

### 新增 · 品牌素材

- `images/bigA01.png` —— 横版字标的透明底白字版，供深色背景与视频叠加使用。
  ⚠️ 它**不能替代** `LOGO_BigA01`：白字透明底在浅色背景（GitHub 亮色模式）
  上会整体消失，两张是互补而非新旧关系 —— 写进 README 是为了让取图的人
  一眼看出该用哪张，而不是逐个点开试

### 变更 · Phase 2 范围：补齐到 **7** 个 agent，不是 8 个

- 🔴 **`discipline` 推到 Phase 3**（裁定 13）。它的职责是「人的行为风险」，
  输入是**人的交易行为史**；而 BigA 不下单、不接账户、按裁定 1 不挂邻居的库 ——
  **它现在没有输入源**。在这种状态下建它，等于同时踩中 L-1（零消费方）和
  L-2（无输入却给得出 PASS）。
  Phase 2 的出口条件按 7 个 agent 写，**不假装有第 8 个**
- roadmap 原文「补齐 8 个 agent」直译成「一次建 6 个」也会撞 L-1。
  改为**新机制优先、复制其次**的四步：`market`（Stage 1 首次真并行）→
  `risk`（唯一的结构性新机制：冻结证据 + BLOCK 否决权）→
  `sector`/`technical`（才是真正的复制）→ `news`（先做数据源 spike）
- Phase 2 出口条件**不照抄 105s**。理由与 60s 被证伪同源：那个数出自同一套
  「区间端点相加」的算术。真实风险在 Stage 3 —— 只有 1 个 specialist 时它就已经
  压着 30s 上沿，而 Stage 1 是并行的。改为每加一个 agent 量一次斜率，攒够点再推
- 「`missing[]` 真实非空 ≥5 次」需要按日历累积，且 Phase 2 不建 cron ⇒
  **第一天就要开始记台账**，否则末尾只能干等

### 变更 · Phase 2 在独立分支开发（裁定 14）

- `main` 只保留 Phase 1 **已验收**的状态。Phase 1 那几个数字
  （74.8s / $0.2179 / 124 测试）是在一个具体提交上实测出来的，只对那个状态成立。
  把半成品 agent 混进 main，README 的徽章就开始描述一个**没人验收过的状态** ——
  这正是 L-6 的生成机制，而不只是「不够整洁」
- 🔴 **建分支当天就修了它带出的一个漏洞**：转 Public 前的审查脚本写死
  `git log main -p`，而 Phase 2 的代码全在 `phase2` 上 ⇒ **这个守卫从此扫不到
  我们实际在写的东西，却仍然报全绿**。改为 `git log --all -p`（转 Public 后
  所有推上去的分支都可见，扫描范围必须与可见范围一致）。
  与「脚本自匹配」是同一类问题：**守卫的失效方式是继续报绿，不是报错**

### 新增

- 接入 GitHub 远端 `easyup168/easyup-biga`（当前私有）。
  为该账号单独生成 SSH 密钥并用 Host 别名区分，本机另一账号的默认密钥不受影响

### 安全

- 重写全部 commit 的作者身份，统一为项目账号 + **GitHub noreply 邮箱**，
  并从文件内容中清除旧用户名。noreply 与真实邮箱在 GitHub 上功能完全等价，
  唯一区别是公开后不会被爬虫抓到真实地址
- **转 Public 前的八项安全审查全过**，审查脚本固化在 `TODO.md`，
  每次改动仓库可见性前重跑
- ⚠️ 审查抓到两个同源的问题，都是「描述规则时把规则的内容当成了数据」：
  1. 在**记录「别泄露邮箱」**的条目里，把邮箱本身写了进去
  2. 把审查脚本固化进 `TODO.md` 后，**脚本的正则字面量匹配到了自己**，
     每次都稳定报 3 处假命中
  第 2 条尤其危险 —— **一个永远报警的检查很快会被当成噪音忽略**，
  等于把守卫变成了摆设。已让脚本排除自己的定义行

### 新增 · 延迟与成本核算

- `skills/_store/runtime.py` —— OpenClaw 运行时数据的**只读**读取器。
  按 `runId` 配对 `prompt.submitted` / `model.completed`，得到每轮 LLM 的
  真实耗时、token 与成本。schema 对不上时返回空 + 明确原因，**绝不返回
  部分数据却装作完整**
- `tools/verify/latency_report.py` —— 分解「时间和钱花在哪」。
  用 Card 的 `elapsed_ms` 精确定界窗口；拿不到时明确标注降级。
  并与调度器的 `task_runs` **双向对账**，两份记录对不上就报出来

### 修复

- 🔴 **验收第 8 条原本在让被考核方自己报成绩。** 它读的是
  `card.elapsed_ms`（Supervisor 自己填的），实测 122.8s；
  而真实墙钟是 **215.2s** —— **少报了 43%**。已改为读运行时 trajectory
- 🔴 **`agent_runs.elapsed_ms` 测的是技能耗时（几秒），不是 agent 的 LLM
  轮次（几十秒）。** 拿它做延迟分析会把优化做到错的地方
- 删除 `agent_runs.tokens_in` / `tokens_out`（schema v2）——
  它们从建表起就**没有任何生产方**（8 行记录 0 行有值）。
  真实数据在运行时的 trajectory 里，在这里再存一份就是第二套口径且必然滞后。
  **留着假装有，比没有更糟**

### 修复 · 三个以「慢」而非「报错」表现出来的 bug

延迟 **216.3s → 74.8s**，成本 $0.70 → $0.2179，Supervisor 工具调用 18 → 2。
三个 bug 没有一个会让流程失败，它们只是让 Agent 不停地自救：

- 🔴 **`synthesize.py` 写死 `new_task_id(1)`**，当天第二次决策必然撞
  `UNIQUE constraint`。Supervisor 每次都自己查库、改号、重试并最终成功 ——
  **系统"能用"，只是每次要自救 18 次工具调用 / 140 秒**。
  只看「有没有出卡」永远发现不了。
  已加 `next_decision_id()`，并把 UNIQUE 冲突翻译成一句指路的报错
  （给 Agent 看的错误消息必须包含下一步怎么做，否则它会花十几轮去猜）
- 🔴 **emotion 的契约用了仓库根的相对路径，而它的 cwd 是 `agents/emotion/`**。
  脚本找不到，emotion 跑起了 `find /`。已改为自带 `cd`，并明令禁止 `find /`
- 🔴 **emotion 的契约自相矛盾** —— 同时要求「JSON 原样透传」和
  「局限要写进 `missing`」，而契约层规定 `missing` 非空 ⇒ 不能是 `PASS`。
  Agent 连续被拒两次。已补一张表把隐含规则摆到台面上

### 变更 · Phase 1 延迟预算 60s → 90s（改了指标，说清楚为什么）

- 🔴 **原来的 60s 不是预算，是 §10.1 四个阶段预估「下界之和」**
  （5+20+15+20），105s 是上界之和。把一个区间的最好情况当及格线，
  等于要求四个阶段同时命中最优
- 实测按同样阶段拆开：Stage 0 = 8.0s（估 5-10s）、Stage 1 = 36.0s（估 20-40s）、
  Stage 3 = 30.0s（估 20-30s）—— **每个阶段都在区间内，合计仍然 75s**。
  没有哪个阶段超支，**超的是那个加法**
- 第二处推理错误：「只有 2 个 agent 所以该落在下界」是错的。
  Stage 1 的下界 20s 说的是「5 个并行 specialist 里最快那个」，
  Phase 1 只有一个 emotion，它就是 36s；而 Stage 0 + Stage 3 是
  **与 agent 数量无关的固定开销**（38s，占 51%）
- 新的 90s 有推导：Stage 0/1/3 上界之和（80s）+ 10s 盘中网络余量
- ⚠️ **八 Agent 的 105s 没有跟着改** —— 同一套算术，但现在没有数据支持新的数

### 修复 · 观测工具自己的两个缺陷

- 🔴 **把「缓存读」和「缓存写」合并成一列展示，制造了一个会骗人的指标。**
  写缓存按 1.25× 计费、读缓存 0.1×，差 12 倍。重启 gateway 后的冷启动
  因此看起来像「这个配置贵了一倍」，差点得出错误结论。
  已拆成两列并自动识别冷启动（**聚合之前先问：这些数字单价一样吗**）
- 🔴 **成本只算到 Card 落库为止，低报 32%。** 子 agent 全部 settle 后
  runtime 会再唤醒 Supervisor（`announce:requester-settle`，失败还 retry）。
  那些轮次不计入「等卡时间」，但真花了钱。现在分「等卡成本」与「决策总成本」
  两个口径报（**"等了多久"和"花了多少钱"是两个问题**）

### 验证 · thinking 不是杠杆（实验结论）

bug 清完、信号干净之后重测，**推翻了最初的假设**：

| 档位 | 等卡墙钟 | 输出 tok | 卡的质量 |
|---|---|---|---|
| medium | **74.8s** | 6,731 | 13 证据 / 3 缺失 / WAIT |
| low | 79.3s | 6,468 | 13 证据 / 3 缺失 / WAIT |

降档没有变快，输出 token 只少 4% —— 此时 token 已由**实际产出**主导
（读 JSON、写 13 条证据），不再由思考预算主导。
`thinkingDefault` 保持 `medium`（实测最优且质量不降）。

> **先证明某个旋钮是瓶颈，再去拧它。顺序反了，你会把噪声当成效果。**

### 结果

**`phase1_acceptance.py` 九项全过（PASS 9 / FAIL 0 / PENDING 0）。**
Phase 1 目标达成：环境隔离安装 + 跨 Agent 编排跑通 + 首张可回放 Decision Card。

### 修复 · 更正一处误报：heartbeat 并没有失败

- 先前记「自动创建的 heartbeat cron 每小时失败一次」，**是错的**。
  `cron_run_receipts.error_text` 写的是 `heartbeat skipped: no-route` ——
  heartbeat 要往 IM 频道推状态，而 Phase 1 明确不接任何 IM，
  没有投递路由所以跳过。**设计内的正确行为，没有东西要修**
- 🔴 误报的来源是**两份记录对同一件事说法不一致**：
  `cron_run_receipts.status = skipped`（对），`task_runs.status = failed`（错）。
  读了后者就得出了「在故障」的结论。
  > 一个把合法跳过标成失败的指标，会持续制造假警报，
  > 而假警报的终点是所有人都不再看它 —— 与 R-3 同源：**状态标错和状态缺失一样危险**
- 顺带查实那 4 条 cron 确是 openclaw 自带的托管任务：全部带 `declaration_key`
  （邻居实例 91 条 cron 该字段全为 NULL），声明写死在 `server-reload-managed-*.mjs`。
  ⚠️ 但**跨实例对比不足以证明「是不是默认」** —— 两边大版本不同
  （2026.9.5 vs 2026.7.1-2），那个对比只能说明该机制是新加的。
  决定性证据是包自身的源码，它与版本无关

### 新增 · 教程第 10 章

[`docs/tutorial/10-latency-and-cost.md`](docs/tutorial/10-latency-and-cost.md) ——
本段工作的完整记录：为什么不能让被测对象自报成绩、为什么延迟往往是
正确性 bug 的症状、以及发现指标错了之后该怎么正当地改它。

---

## [0.1.0] - 2026-09-19

**Phase 1 · walking skeleton。** 一条最细的端到端链路：
人提问 → Supervisor spawn Specialist → 真实采数 → 产出可回放的 Decision Card。

验收 8 过 / 1 未达标（延迟），117 条测试全绿。

### 新增

#### 契约层 `skills/_contract/`
- `Evidence` / `AgentVerdict` / `DecisionCard` 的**唯一实现**
- 四条契约铁律在 `__post_init__` 里**拒绝构造**而非记日志 ——
  静默 fail-open 时，放行与通过的日志长得一模一样，
  唯一可靠的对策是让非法状态无法被表示
- `as_of`（数据自身时间）与 `retrieved_at`（取回时间）分离；naive datetime 直接拒绝
- AST 扫描拦三种「第二套实现」：重名类、近名类、**字典版契约**
  （最隐蔽，不引入新符号，grep 搜不到）

#### 数据层 `skills/_store/`
- 三张表：`decision_records` / `agent_runs` / `raw_market_snapshot`
- **只追加由 SQLite 触发器强制**（6 个），不是代码约定 ——
  触发器连 `sqlite3` 命令行都绕不过
- `decision_records` 用 partial unique index 表达「一个 decision_id
  只能有一条在线记录，回放记录不限条数」
- 派生列只由唯一写入口从完整 Card 对象生成，不可能与真相源不一致
- AST 扫描禁止业务代码出现裸 `sqlite3`（import 与 connect 两条独立规则）

#### 技能 `skills/emotion-calc/`
- 真采 A 股情绪数据：涨停 / 跌停 / 炸板 / 炸板率 / 最高板 / 连板梯队 /
  封板质量 / 涨跌家数 / 情绪分
- 四个数据源并行采集（串行 26s → 并行 19s），备选主机链
- 只产出事实与分数，**不做周期归类** —— 判断逻辑散进 skill 会产生第二套口径

#### 技能 `skills/decision-card/`
- `card_ops.synthesize()` 纯函数，**在线与回放共用同一份组装代码**
- `replay --check` 一致性检查；回放追加不覆盖原始记录
- 组装版本写进 `model_ref`，「是模型变了还是代码变了」一秒有答案

#### Agent
- `main`（Supervisor）+ `emotion`（Specialist）
- 角色契约写在各自 `AGENTS.md` —— spawned session 不加载 `SOUL.md` / `IDENTITY.md`
- emotion 契约含「拿单日数据**说不了**什么」一节：LLM 会把不完整的信息
  补成完整的故事，必须明确告诉它哪些判断它的数据支撑不了

#### 验收与教程
- `tools/verify/phase1_acceptance.py` —— 8 条机器核对，**PENDING 不计为通过**
- `docs/tutorial/` 九章开源开发教程

### 变更

- 模型运行时由桥接模式改为 `openclaw` 内置 harness。
  桥接模式下本机的一条策略会拦掉 MCP server，导致跨 agent spawn 工具整体不可见

### 安全

- 按公开仓库纪律**重写全部 git 历史**，抽掉同机另一套实例的可识别细节
  （内部模块名、文件路径、具体数量、下单通道），保留工程教训与防护机制本身。
  ⚠️ 在新 commit 里脱敏是不够的 —— 旧 commit 里的原文原封不动还在，
  必须重写历史。仓库尚未 push，此时重写零代价
- `.gitignore` 排除 `USER.md` 与 Specialist 的脚手架残留
  （运行时每次 spawn 都会重建，`rm` 只解决当下）

### 已知问题

- **端到端 122.8s，超 60s 预算**（见 `[未发布]`）
- **anthropic 凭据与另一套实例共享**（本机无法另行签发）。
  ⚠️ 凭据失效时两套系统同时停，且排查方向天然指向 BigA（错的那边）。
  耦合与排查顺序记在 `CLAUDE.md`「已知耦合」一节，Phase 3 前必须替换。
  具体配置属于本机情况，记在仓库外的 `AUTH-NOTES.local.md`
- 起 gateway 会自动创建 4 条运行时内置 cron，与「单一调度域」冲突。
  已裁定暂不处理，Phase 3 建自己的调度域时统一收编

---

## [0.0.1] - 2026-09-19

### 新增

- 隔离安装：node v24.21.0 + OpenClaw 2026.9.5，**刻意不装进 nvm 的 bin**
- `bin/biga` wrapper —— 强制注入 `--profile biga`。
  该 CLI 启动会跑 doctor 迁移，漏掉参数就是在改另一套实例的库
- workspace 骨架、架构设计文档、安装指南

[未发布]: https://github.com/easyup168/easyup-biga/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/easyup168/easyup-biga/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/easyup168/easyup-biga/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/easyup168/easyup-biga/releases/tag/v0.0.1
