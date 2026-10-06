#!/usr/bin/env bash
set -euo pipefail
mkdir -p src
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
echo "export const a = 1" > src/main.ts
mkdir -p .claude/state docs/plans
