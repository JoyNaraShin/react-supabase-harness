#!/usr/bin/env bash
set -euo pipefail
mkdir -p app/node_modules/left-pad app/src
echo "module.exports = 1" > app/node_modules/left-pad/index.js
echo "export const a = 1" > app/src/index.ts
echo '{"name":"app"}' > app/package.json
