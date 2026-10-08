#!/usr/bin/env bash
set -euo pipefail
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
mkdir -p src supabase docs
echo "export const a = 1" > src/main.ts
