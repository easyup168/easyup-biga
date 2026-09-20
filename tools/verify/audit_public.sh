#!/usr/bin/env bash
# 公开仓库敏感内容审查 —— 八项检查的**唯一实现**
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


FAIL=0
# 🔴 grep -v '^+chk "' 是必需的：本脚本的正则字面量自己也在被扫描的内容里，
#    不排除就会永远自匹配 —— 与上面那条同源：永远报警 = 被当成噪音。
chk() { c=$(printf '%s' "$DIFF" | grep -E "^\+.*$2" | grep -vc '^+chk "' || true); \
        [ "$c" -gt 0 ] && { echo "⚠️  $1 —— $c 处"; FAIL=1; } || echo "✅ $1"; }

chk "凭据/私钥"      '(sk-ant|oat[0-9]{2}_|ghp_|github_pat_|tvly-|BEGIN [A-Z ]*PRIVATE KEY|ssh-(ed25519|rsa) AAAA)'
chk "Gateway token" '(bootstrapToken=|gateway\.auth\.token[^s]|\b[0-9a-f]{64}\b)'
chk "家目录路径"     '/home/[a-z][a-z0-9_-]*'
chk "个人邮箱"       '[a-zA-Z0-9._%-]+@(gmail|qq|163|126|outlook|hotmail|foxmail|sina)\.'
chk "邻居可识别细节" '(EASYUP|\bQMT\b|market\.db|nodeenv|find_node_bin|[0-9]+ 个 systemd)'
chk "IP 地址"        '\b(10|172|192)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
chk "旧用户名"       '\b***\b'
chk "密码赋值"       '(password|passwd|secret)["'"'"'[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{6,}'

if [ "$FAIL" -eq 0 ]; then
  echo "══ 八项全绿 ══"
  exit 0
fi
echo "══ 有命中，不要 push ══"
exit 1
