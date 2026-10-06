#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q --bare remote.git
$G init -q app
cd app
echo "v1" > README.md
$G add README.md
$G commit -q -m "chore: init"
$G remote add origin ../remote.git
$G push -q origin main
echo "v1 (typo fixed)" > README.md
$G commit -q -a --amend -m "chore: init"
