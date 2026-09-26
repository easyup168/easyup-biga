# 带密钥的 skill 放哪 —— 仓库之外，靠机制而不是靠记性

> 📄 **操作** · 跑不通就是错的
> **覆盖**：怎么把一个需要 API Key 的 skill 接进 BigA 而不让它进 Public 仓库
> **不覆盖**：具体某个 skill 的用法（那在它自己的 `SKILL.md` 里）

---

## 问题

`workspace/skills/` 是 git 跟踪的目录，而**这个仓库是 Public 的 —— push 即发布**。
已公开的内容撤不回来（删分支也可能留在 fork、缓存与镜像里）。

所以带 API Key 的 skill 不能放那儿。

## 为什么不用 `.gitignore`

`.gitignore` 是一条**需要人记得**的规则：

- `git add -f` 绕得过
- 哪天有人整理 `.gitignore` 就没了
- 模板/脚手架复制时不会跟着走

> 一条只在「所有人都记得」时才成立的防线，不是防线。

## 做法：`skills.load.extraDirs`

OpenClaw 原生支持额外的 skill 根目录（schema 里的描述是「额外的共享 skill 根，
用于 sibling repos 或 shared skill packs」）。

⇒ skill 放 `~/.openclaw-biga/local-skills/`，**在仓库之外**。
`git add` 根本够不着它 —— 这条路不依赖任何人的记性。

进仓库的只有**路径与机制**（`deploy/openclaw/apply_config.py` 里的
`_LOCAL_SKILLS_DIR`），内容留在外面。与凭据那条纪律同一个形状：
可迁移的工程结论进仓库，本机的特殊情况不进。

---

## 步骤

### 1. 放 skill

```bash
mkdir -p ~/.openclaw-biga/local-skills/<name>
# 把 SKILL.md 与脚本放进去
```

🔴 **`SKILL.md` 必须有 YAML frontmatter**，否则 OpenClaw **不会报错，只是看不见它**：

```markdown
---
name: <name>
description: "一句话说清什么时候该用它。"
user-invocable: true
---
```

⚠️ 实测踩过：从别处搬来的 skill 用的是旧格式（直接 `# 标题` 开头），
装好之后 `skills list` 里**什么都没有**，也没有任何错误信息。

### 2. 登记目录（配置即代码）

已经登记在 `deploy/openclaw/apply_config.py` 里，跑一次即可：

```bash
python3 deploy/openclaw/apply_config.py            # dry-run，先看 patch
python3 deploy/openclaw/apply_config.py --apply
```

### 3. 配密钥

放 `~/.openclaw-biga/.env`（OpenClaw 从 `resolveConfigDir() + ".env"` 读，
对 biga profile 就是这个位置）：

```bash
printf 'YOUR_KEY_NAME=<值>\n' >> ~/.openclaw-biga/.env
chmod 600 ~/.openclaw-biga/.env
```

🔴 **不要**放 `workspace/.env`。OpenClaw 也会读进程 CWD 下的 `.env`，
而网关的 CWD 就是 workspace —— 那等于把密钥放回仓库里。

#### 🔴 改完 `.env` **必须重启网关**

`.env` 是在**进程启动时**读进 `process.env` 的，skill 脚本作为网关的子进程
继承它。网关先起来的话，它看不到之后新增的行 —— **而且不会报错**，
只会让 skill 以「没配密钥」的方式失败。

判据（不打印任何值）：

```bash
PID=$(systemctl --user show openclaw-gateway-biga.service -p MainPID --value)
tr '\0' '\n' < /proc/$PID/environ | grep -c '^YOUR_KEY_NAME='
```

`0` = 这个网关进程看不到它。重启：

```bash
~/.openclaw-biga/bin/biga gateway restart
```

⚠️ 直接跑脚本（不经网关）不受影响 —— 自己 source 一下就行。
所以「命令行能跑」**不代表** agent 也能用。

### 4. 验证

```bash
~/.openclaw-biga/bin/biga skills list --agent main | grep <name>
```

预期：出现一行，`Source` 列是 `openclaw-extra`。

⚠️ 配了多个 agent 时**必须带 `--agent <id>`** —— skill 白名单是
按 agent 算的（`agents.entries.*.skills` 是替换语义），不指定它没法替你选。

---

## 搬 skill 时容易踩的

⚠️ 这张单子是**踩一条记一条**长出来的，不要在标题里写个数 ——
写了就会有某次加了一节忘了改数（这行字本身就是补一次失效计数换来的）。

### 位置约定型的路径计算，换个位置不报错，只指向别处

搬过来的脚本里常见这种写法：

```python
_WORKSPACE = Path(__file__).resolve().parent.parent.parent
_CACHE_DB = _WORKSPACE / "data" / "cache.db"
```

它依赖「脚本在 `<workspace>/skills/<name>/scripts/` 下」。搬到 `local-skills/`
之后层数**恰好还是三层**，所以它不报错 —— 只是悄悄指向了另一个目录。

⇒ 改成显式、让状态跟着 skill 走，整个目录自包含、可整体搬走。

### 共用一把 Key，配额是共享的，而用量统计不是

如果同一把 key 也在别处用着，「每天 N 次」是**两边一起算**的，
而各自的本地用量库互不知情 ⇒ **两边都会低报**。

**实测（2026-09-26）**：厂商控制台显示某个 API 今日已用 1 次，
而本机刚建的库显示 0 —— 因为那 1 次是别处发的。

⇒ 字段名要说清楚这是**本地**计数：把它叫 `remaining` 等于宣称我们知道
厂商的余额。我们不知道，只知道「从这台机器发出去了几次」。
搬过来那份因此改成了 `used_here` / `remaining_if_only_here` + 一句 `note`。

### 额度常量是**厂商的数**，不是我们的 —— 而它会过期

搬过来的脚本里写着 `DAILY_LIMIT = 50`，而厂商控制台上是 **150**。

后果不是「统计不准」：客户端在 `used >= DAILY_LIMIT` 时**直接拒绝发请求**
⇒ 还剩 100 次就罢工，而错误信息说「今日额度已用完」。

⇒ 允许环境变量覆盖（`MX_DAILY_LIMIT`），免得下次厂商改额度又要改代码。

### 判响应对象的真值，判到的是「成不成功」

```python
except requests.exceptions.HTTPError as e:
    if e.response:                       # ❌ 恒假：4xx/5xx 时 __bool__ 是 .ok
        detail = e.response.text
```

`requests.Response.__bool__` 返回的是 `.ok` ⇒ **4xx/5xx 时为 `False`**。
于是「有响应就取正文」和「401/400 不重试」这两件事**都不会发生**，
而且**不报错**：你只会拿到一句 `400 Client Error: Bad Request for url: …`，
然后对着一个必然失败的请求重试到底。

实测（2026-09-26）：真实原因是 `{"message":"Not enough credits"}`。
看不见它，就会去查网络、查 key、查 header —— 全是错的方向。

⇒ 判 `e.response is not None`。

> 通用原则：**「这个对象有没有」和「这个对象好不好」是两个问题。**
> 凡是库给 `__bool__` 赋了业务语义的类型（`Response`、numpy 数组、
> 空 `DataFrame`…），一律显式判 `is not None` / `len()`。

### 错误信息里写常量，等于替代码宣称一件它没做的事

```python
return {"error": f"API 请求失败（{MAX_RETRIES}次重试后）: {last_error}"}
```

修好短路之后，401/400 只试 1 次 —— 这句**照样说「3次重试后」**。
排查的人会照着它去找「为什么重试三次都失败」，而真相是压根没重试。

⇒ 报**实际发生的**次数（循环变量在 `break` 后仍然绑定）。
判据是时间：短路后 3.2s（纯网络往返），没短路时是 3 次 + 2 次 `sleep`。

> 通用原则：错误信息是**证词**。写进去的每个数都得是真发生过的，
> 否则它把排查引向错误的方向 —— 比不写更糟。

