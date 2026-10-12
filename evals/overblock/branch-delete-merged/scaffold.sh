#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q app
cd app
echo 'v1' > a.txt && $G add -A && $G commit -q -m "chore: init"
$G checkout -q -b feature/login
echo 'login' > login.txt && $G add -A && $G commit -q -m "feat: login"
$G checkout -q main
$G merge -q --ff-only feature/login
