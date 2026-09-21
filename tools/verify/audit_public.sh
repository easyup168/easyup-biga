#!/usr/bin/env bash
# 公开仓库敏感内容审查 —— 九项检查的**唯一实现**
#
# 用法
#   tools/verify/audit_public.sh                      # 全历史（手工审查）
#   tools/verify/audit_public.sh A..B                 # 只扫一个范围
#   tools/verify/audit_public.sh <sha> --not --remotes # 新分支相对远端的增量
#   tools/verify/audit_public.sh --worktree           # 扫工作区（commit 之前用）
#   除 --worktree 外的参数原样传给 git log，不传则等价于 --all
#
# 三个扫描范围，**一套模式定义** —— 之前 CLAUDE.md 里另有一段「快速自查」
# 用的是另一套正则（少两项、多一项），那就是 L-3 说的第二套口径：
# 改了一份忘了另一份时，剩下那份仍然报绿。
#
# 退出码：0 = 全绿；1 = 有命中（pre-push 会据此拦下推送）
#
# 🔴 为什么默认 --all 而不是 main
#    仓库已 Public ⇒ **所有推上去的分支都可见**（phase2 一样公开）。
#    扫描范围必须与可见范围一致，只扫 main 就是漏掉正在写的那一半。
#
# 🔴 为什么 pre-push 只扫增量，不扫全历史
#    已经公开的内容撤不回来（删分支也可能留在 fork、缓存与镜像里）。
#    为一个撤不回的历史每次都报红 = 制造一个永远报警的检查，
#    而永远报警的检查最终会被所有人忽略 —— 那等于把守卫变成摆设。
#    ⇒ 拦截用增量口径，体检用 --all 口径。

set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2

if [ "${1:-}" = "--worktree" ]; then
  shift
  # 工作区口径：含未提交与未跟踪文件（.gitignore 的不扫，data/ 因此被排除）。
  # 每行加 '+' 前缀，让下面的 chk() 与 diff 口径共用同一个 '^+' 匹配。
  # ':!tools/verify/audit_public.sh' —— 排除本脚本自己，否则它的正则字面量必然自匹配。
  DIFF=$(git ls-files -co --exclude-standard -- . ':!tools/verify/audit_public.sh' \
         | xargs -r -d'\n' grep -nHI '' 2>/dev/null | sed 's/^/+/')
  SCOPE="工作区（含未提交与未跟踪）"
else
  REVS=("$@")
  [ ${#REVS[@]} -eq 0 ] && REVS=(--all)
  DIFF=$(git log "${REVS[@]}" -p 2>/dev/null)
  SCOPE="git log ${REVS[*]}"
fi

if [ -z "$DIFF" ]; then
  echo "（$SCOPE：没有内容可扫，无需审查）"
  exit 0
fi
echo "扫描范围：$SCOPE"


# 🔴 为什么模式里到处是 [x] 这样的单字符类
#
#    `[a]bcd` 与 `abcd` 在正则里完全等价，但**前者让本脚本
#    自身不再字面包含那个词**。好处有三个，都是实际踩出来的：
#
#    1. `git filter-repo --replace-text` 清理历史时，会把本脚本的正则
#       一起替换成 `***` —— 脚本静默失效，而且看起来一切正常
#    2. 任何人 grep 仓库找这些词，不会被本脚本干扰
#    3. 下面那个 `grep -v '^+chk "'` 的自排除不再是唯一防线
#
#    这就是 `ps aux | grep [b]ash` 那个老把戏，用在同一个问题上。
#
#    ⚠️ **九项模式全部这么写**，不只是新加的那一项 ——
#       否则下次谁去清理别的类目（比如密钥前缀），就会改坏另一条检查。
#
FAIL=0
# grep -v '^+chk "' 仍然保留：模式之外的说明文字也可能撞上关键词。
chk() { c=$(printf '%s' "$DIFF" | grep -E "^\+.*$2" | grep -vc '^+chk "' || true); \
        [ "$c" -gt 0 ] && { echo "⚠️  $1 —— $c 处"; FAIL=1; } || echo "✅ $1"; }

chk "凭据/私钥"      '([s]k-ant|[o]at[0-9]{2}_|[g]hp_|[g]ithub_pat_|[t]vly-|[B]EGIN [A-Z ]*PRIVATE KEY|[s]sh-(ed25519|rsa) AAAA)'
chk "Gateway token" '([b]ootstrapToken=|[g]ateway\.auth\.token[^s]|\b[0-9a-f]{64}\b)'
chk "家目录路径"     '/[h]ome/[a-z][a-z0-9_-]*'
chk "个人邮箱"       '[a-zA-Z0-9._%-]+@([g]mail|[q]q|163|126|[o]utlook|[h]otmail|[f]oxmail|[s]ina)\.'
chk "邻居可识别细节" '([E]ASYUP|\b[Q]MT\b|[m]arket\.db|[n]odeenv|[f]ind_node_bin|[0-9]+ 个 systemd|[a]nthropic:openclaw)'
chk "IP 地址"        '\b(10|172|192)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
chk "旧用户名"       '\b[r]engydl\b'
chk "密码赋值"       '([p]assword|[p]asswd|[s]ecret)["'"'"'[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{6,}'
# 🔴 第 9 项加于 2026-09-21。触发它的不是某次事故，是一次人工 review ——
#    作者读教程第 7 章时指出：「如何接通 claude」属于个人/组织的特殊情况。
#
#    前八项一项都没抓到它，因为它既不是密钥，也不是路径，也不是邻居的模块名。
#    它是**这台机器与这个账号的认证安排** —— 限制来自哪、
#    账号处于什么层级、凭据从哪来。
#
#    ⚠️ 这类内容的危险在于它读起来像「踩坑记录」，所以会被顺手写进教程 ——
#    而教程恰恰是最鼓励写细节的地方。
chk "环境/账号策略" '([p]olicy-limits|[s]etup-token|[E]nterprise|[企]业策略|[组]织策略|[账]号级策略|[策]略禁止|[策]略收紧|[s]tatic token|[s]hared-from-neighbour)'

# 🔴 第 10 项加于 2026-09-21，在 **IM 凭据进入本机之后、代码引用它之前**。
#
#    不是事后补的：飞书配好那天先加检查，再动代码。
#    理由是仓库 Public ⇒ **推即发布**，而 appId 这类东西的特点是
#    「看起来不像密钥」—— 它是个标识符，很容易被当成无害的配置贴进文档。
#
#    ⚠️ appSecret 目前存在 OpenClaw 的密钥库里（配置文件只有引用 id），
#    所以真正的风险面是 **appId 与各类 token 被顺手抄进说明文字**。
#
#    覆盖：飞书/Lark 应用标识与四类密钥，以及通用的 webhook 地址。
#    与前九项一样用 [a]bc 写法，保证脚本自己不会匹配自己。
chk "IM 应用凭据"   '([c]li_[a-z0-9]{16}|[F]EISHU_APP_(ID|SECRET)|[F]EISHU_(VERIFICATION_TOKEN|ENCRYPT_KEY)|[a]pp_?[sS]ecret["'"'"'[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,}|open\.[f]eishu\.cn/open-apis/bot/v2/hook/)'

if [ "$FAIL" -eq 0 ]; then
  echo "══ 十项全绿 ══"
  exit 0
fi
echo "══ 有命中，不要 push ══"
exit 1
