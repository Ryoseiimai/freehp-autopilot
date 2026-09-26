#!/usr/bin/env bash
# ジョブが書き換えた状態ファイルだけをコミットして push する。
# 使い方: scripts/commit_state.sh "コミットの一言" <パス>...
# 他のジョブと同時に push したときのために、rebase して最大4回やり直す。
set -euo pipefail
msg="$1"; shift
git config user.name "freehp-autopilot[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for path in "$@"; do
  if [ -e "$path" ]; then git add -A -- "$path"; fi
done
if git diff --cached --quiet; then
  echo "状態の変化なし（コミットしません）"
  exit 0
fi
git commit -q -m "$msg"
for attempt in 1 2 3 4; do
  if git pull -q --rebase origin main && git push -q origin HEAD:main; then
    echo "状態を保存しました: $msg"
    exit 0
  fi
  sleep $((attempt * 5))
done
echo "状態の push に失敗しました" >&2
exit 1
