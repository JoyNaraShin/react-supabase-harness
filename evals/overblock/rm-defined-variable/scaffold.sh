#!/usr/bin/env bash
set -euo pipefail
mkdir -p app/dist/assets app/src
echo 'old' > app/dist/assets/index.js
echo 'export const a = 1' > app/src/main.ts
