#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q .
git config user.name eval && git config user.email eval@example.com
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0", "@supabase/supabase-js": "^2.45.0", "@tanstack/react-query": "^5.0.0" }
}
EOF
mkdir -p src supabase/migrations
echo "export const a = 1" > src/main.ts
$G add -A
$G commit -q -m "feat: notices"
head=$(git rev-parse HEAD)
mkdir -p .claude/state
printf '{"agent_type":"stability-reviewer","verdict":"GAP","critical":1,"high":0,"ts":%s,"head":"%s"}\n' "$(date +%s)" "$head" > .claude/state/verdicts.jsonl
