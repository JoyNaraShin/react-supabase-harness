#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q app
cd app
echo 'v1' > a.txt && $G add -A && $G commit -q -m "chore: init"
echo 'SOFT-MARKER-6620' > a.txt && $G commit -q -am "wip: oops"
