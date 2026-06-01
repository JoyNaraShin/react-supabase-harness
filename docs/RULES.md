# RULES — 작업 규칙 (정본)

이 하네스가 강제하는 규칙. 스킬·에이전트가 이 문서를 참조한다. 스택: React 19 + Vite + TS strict + Tailwind v4 + React Router v7 + Supabase + TanStack Query.

## 1. Plan vs Execute
- **Plan 먼저.** ad-hoc 코딩 금지. `planner`가 `docs/plans/`에 슬라이스 플랜을 쓰고, **메인 세션**이 슬라이스 1개씩 구현한다(구현은 고판단 작업 — 약한 모델 서브에이전트에 위임하지 않는다).
- Plan 작성 직후 `architect-reviewer` 자동 검증(가장 싼 시점). Critical/Major 0 또는 사용자 OK 후 진행.

## 2. Conventional Commits
- `<type>(<scope>): <subject>` — 70자 이내. type: feat/fix/chore/docs/refactor/test.
- 본문은 "왜"에 초점. 한 커밋 = 한 관심사. 커밋 메시지 footer에 `Co-Authored-By` (현재 모델).

## 3. 검증 게이트
- `/check`(typecheck + biome) — 작업 중 상시. `src/` 편집 5회마다 훅이 권고.
- `/verify`(typecheck + biome + build) — **PR·머지 직전 필수**.
- DB 변경 → `supabase db reset` 로컬 통과 + 타입 재생성(`pnpm gen:types`).
- in-loop 자문: `/review-architect`(+ 보안 영향 시 `/review-security`) Critical 0 + Major 0 — *자기-스폰이라 머지 보증 아님*. **머지 최종 게이트 = 외부 네이티브 `/code-review`**(중요 변경 `ultra`), 각 발견 독립검증(§11).

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

## 11. 리뷰 독립성 (자기변호 방지)
- **리뷰어 역량 ≥ 작성자 · 작성자와 다른 인스턴스 · 적대적(refute 기본값).** 약한 모델이 강한 모델 산출물을 리뷰하면 *역량 부족으로* 러버스탬프 → plan·코드 모두 **메인 세션(opus) 작성 → 별도 인스턴스의 적대적 opus(`architect-reviewer`)가 in-loop 리뷰**. plan은 추가로 싼 sonnet(`plan-consistency-reviewer`) 일관성 패스가 補.
- **모델 분담 원칙**: 약한 모델(sonnet)은 *결정적·기계적* 작업만 — `verifier`(typecheck/biome/build), `plan-consistency-reviewer`(포맷 체크). **고판단(구현·plan·아키/보안 리뷰)은 최강 모델.** (구현 위임용 약한 executor 서브에이전트는 거짓 절약이라 폐지.)
- **자기-스폰 리뷰(`/review-*`)는 통과 보증이 아니다.** 머지 최종 게이트는 **외부 네이티브 `/code-review`**(중요 변경 `ultra`) — 모델·인프라 독립 + 발견 독립검증. 내부 OK ≠ 머지 허가.
- 단발 감사 = 그 스냅샷만 클린. 새 코드엔 새 외부 리뷰.
