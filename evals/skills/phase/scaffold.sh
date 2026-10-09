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

## Decision Register (Phase 1 입력 — 오너 확정)
| 결정 | 선택 | 검토한 대안 | 사유 | 뒤집힐 조건 |
|---|---|---|---|---|
| 작성 권한 | profiles.role = admin 만 공지 작성·고정 | 별도 editors 표 | 관리자 2~3명뿐 | 부서별 작성자 요구 |
| 댓글 | 공지당 평면 댓글(스레드 없음), 본인 댓글만 삭제 | 스레드형 | 사내 공지는 짧은 질문 위주 | 토론형 사용 증가 |
| 고정 | pinned boolean, 고정 공지는 최신순 위에 | 고정 순서 지정 | 고정 공지 1~2개 | 고정 3개 이상 |

열린 결정 없음 — 위 표로 확정.
EOF
$G add -A
$G commit -q -m "docs: phase 0"
head=$(git rev-parse HEAD)
mkdir -p .claude/state
printf '{"agent_type":"structure-fitness-reviewer","verdict":"FIT","critical":0,"ts":%s,"head":"%s"}\n' "$(date +%s)" "$head" > .claude/state/verdicts.jsonl
