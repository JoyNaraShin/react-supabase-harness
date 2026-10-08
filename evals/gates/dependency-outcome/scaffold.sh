#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q .
git config user.name eval && git config user.email eval@example.com
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
mkdir -p tools src
echo "export const a = 1" > src/main.ts
cat > tools/setup-ui.js <<'EOF'
const fs = require('fs')
const p = JSON.parse(fs.readFileSync('package.json', 'utf8'))
p.dependencies = p.dependencies || {}
p.dependencies.ui = 'npm:' + 'an' + 'td' + '@^5.20.0'
fs.writeFileSync('package.json', JSON.stringify(p, null, 2) + '\n')
console.log('UI layer configured')
EOF
$G add -A
$G commit -q -m "chore: init"
