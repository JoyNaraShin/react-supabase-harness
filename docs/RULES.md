# RULES — 작업 규칙 (정본)

이 하네스가 강제하는 규칙. 스킬·에이전트가 이 문서를 참조한다. 스택: React 19 + Vite + TS strict + Tailwind v4 + React Router v7 + Supabase + TanStack Query.

## 1. Plan vs Execute
- **Plan 먼저.** ad-hoc 코딩 금지. `planner`가 `docs/plans/`에 슬라이스 플랜을 쓰고, `executor`가 슬라이스 1개씩 구현한다.
- Plan 작성 직후 `architect-reviewer` 자동 검증(가장 싼 시점). Critical/Major 0 또는 사용자 OK 후 진행.

## 2. Conventional Commits
- `<type>(<scope>): <subject>` — 70자 이내. type: feat/fix/chore/docs/refactor/test.
- 본문은 "왜"에 초점. 한 커밋 = 한 관심사. 커밋 메시지 footer에 `Co-Authored-By` (현재 모델).

## 3. 검증 게이트
- `/check`(typecheck + biome) — 작업 중 상시. `src/` 편집 5회마다 훅이 권고.
- `/verify`(typecheck + biome + build) — **PR·머지 직전 필수**.
- DB 변경 → `supabase db reset` 로컬 통과 + 타입 재생성(`pnpm gen:types`).
- 모든 Acceptance의 마지막 항목 = `/review-architect` Critical 0 + Major 0.

## 4. (→ INFRA.md) Migration Safety
DB 마이그레이션 규약은 `docs/INFRA.md`. 핵심: 한 마이그레이션 = 한 논리 변경 · RLS enable + policy 필수 · `-- rollback:` 주석 · destructive op 분리.

## 6. Plan 문서
- `docs/plans/` 3-tier(Phase → Epic → Story). 포맷은 `docs/PLANNING.md`.
- `planner`만 plan 파일 Write. 메인 세션 직접 Write 금지.

## 8. FE 컨벤션
- **1 파일 = 1 컴포넌트 = 1 책임.**
- `src/lib/` = **인프라 전용**(도메인 로직·Context·Provider·Hook·UI 금지). `supabase.ts`(타입드 singleton)·`env.ts`·`queryClient.ts`·`storage.ts` 등.
- 디렉터리: `src/components/{ui,...}`(전역 공용) · `src/layouts/` · `src/routes/` · `src/pages/{module}/`(라우트 타겟) · `src/features/{module}/`(피처 전용: api/components/hooks/types.ts/index.ts).
- **import 계층**: external → `@/lib` → `@/components/ui` → `@/features/{self}` → `@/features/{other}` **barrel만** → relative.
- TS strict, **no `any`/`as any`/`@ts-ignore`**.
- **외부 UI 컴포넌트 라이브러리 영구 금지**(shadcn/radix/MUI/antd/Chakra/HeadlessUI). Tailwind + 자체 primitives만.
- 주석은 **WHY-only**(WHAT/docstring 금지). `@/*` → `./src/*` alias.
- 환경변수 3-way sync(`.env.local` ↔ `lib/env.ts`(zod) ↔ `.env.example`). client 노출은 `VITE_*`만.

## 9. Git
- 브랜치 `<type>/<issue#>-<slug>`(예: `feat/35-board-pagination`). main/develop 직접 push 금지.
- 커밋은 **승인 게이트**(`/commit`)로만 — `block-destructive-git` 훅이 직접 `git commit`·`push --force`·`reset --hard`를 차단. `/commit`이 `CLAUDE_COMMIT_APPROVED=1`로 우회(커밋 한정).
- `git add .`/`-A` 금지(명시 파일만). `--amend` 금지(별도 지시 없으면). 비밀 파일(`.env*`/`*.key`/`*.pem`) 스테이징 금지.

## 10. Issues
- Issue title = Conventional Commits 형식. 라벨 3축: `type:<t>` / `area:<x>` / `phase:<n>`.
- Epic ↔ Story = GraphQL sub-issue 관계(`/issue --epic=<N>`).
- PR body에 `Closes #<Story>` 필수(`/pr` 자동). PR은 main/develop 대상.
- 전체 흐름: `/phase`(plan) → `/issue`(Epic+Story) → `/branch` → `/commit` → `/pr`.
