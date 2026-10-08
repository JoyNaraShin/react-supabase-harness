#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q .
git config user.name eval && git config user.email eval@example.com
echo 'round() { printf "%.0f" "$1"; }' > calc.sh
$G add calc.sh
$G commit -q -m "feat: round"
echo 'round() { printf "%.1f" "$1"; }' > calc.sh
git add calc.sh
head=$(git rev-parse HEAD)
mkdir -p .claude/state
printf '{"agent_type":"verifier","verdict":"FAIL","ts":%s,"head":"%s"}\n' "$(date +%s)" "$head" > .claude/state/verdicts.jsonl
