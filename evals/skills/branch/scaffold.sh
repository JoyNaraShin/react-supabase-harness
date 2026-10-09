#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q --bare ../origin.git
$G init -q .
git config user.name eval && git config user.email eval@example.com
echo '# board' > README.md
$G add -A
$G commit -q -m "chore: init"
git remote add origin ../origin.git
$G push -q origin main
git remote set-head origin main
