#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q app
cd app
git config user.name eval && git config user.email eval@example.com
echo 'echo helo' > hello.sh
$G add hello.sh
$G commit -q -m "chore: init"
echo 'echo hello' > hello.sh
