#!/usr/bin/env bash
set -euo pipefail
mkdir -p supabase/migrations
mkdir -p src docs/plans
# 이 스킬은 /phase 뒤에 쓰인다 — plan 이 없으면 plan-first 게이트가 마이그레이션 쓰기를 막는다(규칙대로)
printf '# Phase 1 — 글\n\n## Story 1: 글 쓰기·읽기\nacceptance: 작성자만 자기 글을 고치고, 로그인한 사용자는 모두 읽는다\n' > docs/plans/phase-1-posts.md
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
echo "export const a = 1" > src/main.ts
