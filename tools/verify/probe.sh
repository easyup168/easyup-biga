#!/usr/bin/env bash
# 把任意命令跑在**一次性的库**上 —— 手工探针专用。
#
# 为什么需要它
# ------------
# `_store` 是追加式的（触发器强制），写错的行**删不掉**。
# 2.3 收尾时，一次本该用 BIGA_DB_PATH 的手工探针直接写进了真库，
# 留下 6 行永久的假 verdict（task_id `BIGA-20260921-900`）。
#
# 「记得设环境变量」是靠记性的约定，而记性正是追加式存储惩罚的东西。
# 所以给它一个比裸跑更短的入口：
#
#     tools/verify/probe.sh python3 - <<'PY'   # 而不是 python3 - <<'PY'
#
# 库落在 mktemp 里，退出即删。
set -euo pipefail
[ $# -gt 0 ] || { echo "用法: tools/verify/probe.sh <命令...>" >&2; exit 2; }
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
export BIGA_DB_PATH="$d/probe.db"
echo "▸ 探针库 $BIGA_DB_PATH（退出即删）" >&2
# 建好 schema，否则探针第一行就是 "no such table"，人会以为是自己写错了
python3 -c "import sys;sys.path.insert(0,'$(cd "$(dirname "$0")/../.." && pwd)/skills');from _store import db;db.init_schema()"
"$@"
