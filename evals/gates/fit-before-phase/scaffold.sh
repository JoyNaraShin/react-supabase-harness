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
mkdir -p docs/plans
cat > docs/plans/phase-0-domain.md <<'EOF'
# Phase 0 — 도메인·IA (사내 공지 게시판)

## 스토리맵
- 직원은 공지를 읽는다 · 관리자는 공지를 쓰고 고정한다 · 직원은 공지에 댓글을 단다

## 핵심 워크플로우
1. 관리자 공지 작성 → 게시 → 목록 상단 고정
2. 직원 목록 → 상세 → 댓글

## 화면 IA
- /notices (목록) · /notices/:id (상세 + 댓글) · /admin/notices/new (작성)
- 무드: 차분한 사내 도구

## 가정
| 가정 | 확인 방법 |
|---|---|
| 직원 수 200명 이하 | 오너 확인 |
EOF
$G add -A
$G commit -q -m "docs: phase 0"
