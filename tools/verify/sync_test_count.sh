#!/usr/bin/env bash
# 把三份「活」文档里的测试条数同步成实测值。
#
# 为什么要有它：那个数**每加一条测试就变**，而 F22 的守卫会因此报红。
# 手工 sed 已经在一次会话里错过两回（一次取到空串把数字擦成空，
# 一次用了 "N passed" 而守卫要的是 collected —— 差一条 skipped）。
#
# 🔴 判据只有一个来源：守卫自己报出来的 collected 数。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 2

# 🔴 必须与 CI hermetic job 的 marker 过滤完全一致（P1-3）：
#   tests/test_docs_convention.py::test_文档里の条数 靠 request.session.items 数
#   「这次跑里有多少条 test」—— 不带 marker 时比带 -m 多 3 条（installed/live/git），
#   两侧计数不一样，守卫在 CI 里必然报红。
MARKER_FILTER="not installed and not live and not git"

N=$(python3 -m pytest -q --tb=line -m "$MARKER_FILTER" 2>&1 | grep -oP '实测 \K[0-9]+' | head -1)
[ -n "$N" ] || { echo "✅ 条数已经是最新的（守卫没报红）"; exit 0; }

for f in README.md CLAUDE.md docs/guide/review-prompt.md; do
  sed -i -E "s#badge/tests-[0-9]+%20#badge/tests-${N}%20#
             s#\| [0-9]+ 条测试 \| ✅ \|#| ${N} 条测试 | ✅ |#
             s#(├── tests/ +)[0-9]+ 条测试#\1${N} 条测试#
             s#\| 测试 \| [0-9]+ 条 \|#| 测试 | ${N} 条 |#
             s#[0-9]+ 条测试，SQLite schema#${N} 条测试，SQLite schema#
             s#[0-9]+ 条 Hermetic 测试#${N} 条 Hermetic 测试#" "$f"
done
echo "▸ 已同步为 $N 条"
python3 -m pytest -q --tb=line -m "$MARKER_FILTER" 2>&1 | grep -oP '实测 \K[0-9]+' >/dev/null \
  && { echo "❌ 同步后仍然对不上 —— 有一处没被这几条 sed 覆盖到" >&2; exit 1; }
echo "✅ 守卫通过"
