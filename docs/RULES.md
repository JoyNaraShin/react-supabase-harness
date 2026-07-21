# RULES — 작업 규칙 (정본)

이 하네스가 강제하는 규칙. 스킬·에이전트가 이 문서를 참조한다. 스택: React 19 + Vite + TS strict + Tailwind v4 + React Router v7 + Supabase + TanStack Query.

## 1. Plan vs Execute (변경 크기 비례)
- **한 문장 diff 는 플랜 생략.** 오타·로그 한 줄·rename·단일 파일 소수정처럼 **diff 를 한 문장으로 말할 수 있으면** planner·issue·branch 없이 메인 세션이 직접 수정한다 — 단 커밋은 여전히 `/commit` 승인 게이트로만(§9 불변).
- **그 이상은 Plan 먼저.** 새 기능·다파일·스키마 변경은 `planner`가 `docs/plans/`에 슬라이스 플랜을 쓰고, **메인 세션**이 슬라이스 1개씩 구현한다(구현은 고판단 작업 — 약한 모델 서브에이전트에 위임하지 않는다). 애매하면 플랜 쪽으로.
- Plan 작성 직후 `structure-fitness-reviewer` 자동 검증(가장 싼 시점). Critical/Major 0 또는 사용자 OK 후 진행.

## 2. Conventional Commits
- `<type>(<scope>): <subject>` — 70자 이내. type: feat/fix/chore/docs/refactor/test.
- 본문은 "왜"에 초점. 한 커밋 = 한 관심사. 커밋 메시지 footer에 `Co-Authored-By` (현재 모델).

## 3. 검증 게이트
- `/check`(typecheck + biome) — 작업 중 상시. `src/` 편집 5회마다 훅이 권고.
- `/verify`(typecheck + biome + build) — **PR·머지 직전 필수**.
- DB 변경 → `supabase db reset` 로컬 통과 + 타입 재생성(`pnpm gen:types`).
- in-loop 자문(**선택적 — 변경 성격 매칭 렌즈 1개만**): 슬라이스/Story 단위는 해당 렌즈만 호출 — 구조·플로우 `/review-structure` · 보안·DB `/review-stability` · UI `/review-craft`. **3렌즈 전수는 Epic 통합 완료·prod-readiness 시점만**(2026-07-09 리뷰 D2: 비구속 자문이 외부 게이트와 같은 무게로 도는 이중 리뷰는 레이트리밋 헤드룸 낭비). Critical 0 + Major 0 — *자기-스폰이라 머지 보증 아님*. **머지 최종 게이트 = 외부 네이티브 `/code-review`**(중요 변경 `ultra`), 각 발견 독립검증(§11).

## 4. (→ INFRA.md) Migration Safety
DB 마이그레이션 규약은 `docs/INFRA.md`. 핵심: 한 마이그레이션 = 한 논리 변경 · RLS enable + policy 필수 · `-- rollback:` 주석 · destructive op 분리.

## 5. 도메인 적합성 (고아 테이블 금지)
- **테이블엔 화면이 따라온다.** 스키마에 엔티티/컬럼/FK/트리거를 추가하는 슬라이스는, 그것을 **읽는/쓰는 화면(또는 RPC 소비) 슬라이스를 같은 Epic 안에 명시**해야 한다. 시드·스키마만 있고 소비 코드 0인 **고아/절단 금지**(데이터가 가치까지 흐르지 못함 — 재설계의 단골 원인).
- 코드 전 `/phase 0` 도메인·IA 게이트(`PLANNING.md`)에서 종단 흐름을 잡고, 통합 시 `/review-structure` 로 절단·이중입력·중복모델을 적출.

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
- **리뷰어 = 작성자 티어 이상 · 작성자와 다른 인스턴스 · 적대적(refute 기본값).** 약한 모델이 강한 모델 산출물을 리뷰하면 *역량 부족으로* 러버스탬프 → plan·코드 모두 **메인 세션 작성 → 별도 인스턴스의 적대 리뷰어가 in-loop 리뷰**. plan은 추가로 싼 하위 티어(`plan-consistency-reviewer`) 일관성 패스가 補.
- **리뷰어 모델은 상속이 기본값 — 특정 모델 하드핀 금지.** 리뷰 에이전트는 `model:`을 지정하지 않고 세션 모델(=저자)을 상속한다. 그래야 메인 모델 세대가 바뀌어도(Fable↔Opus 등) "리뷰어 ≥ 작성자"가 자동 유지된다. *2026-07-09 감사 U1: opus 하드핀 상태에서 메인만 Fable로 올라가 이 원칙이 소리 없이 역전된 사고. 의도적으로 하위 티어 리뷰어를 쓰려면 이 조항을 명시적으로 개정할 것 — 침묵 역전 금지.*
- **모델 분담 원칙**: 하위 티어(sonnet급)는 *결정적·기계적* 작업만 — `verifier`(typecheck/biome/build), `plan-consistency-reviewer`(포맷 체크). 이 둘만 하드핀 허용(기계 작업은 세대 무관) — 단 **리터럴 버전 ID 금지, `sonnet` 별칭으로**(2026-07-14 N2: `claude-sonnet-4-6` 무효 ID 핀이 스폰 실패 유발. 별칭은 현행 로스터로 자동 해석돼 노화 안 됨). **고판단(구현·plan·적대 리뷰)은 세션 모델 상속.** (구현 위임용 약한 executor 서브에이전트는 거짓 절약이라 폐지.) *2026-07-20 재발 사고: 메인 세션이 확정 스펙을 근거로 구현 전체를 sonnet에 위임 → typecheck·test 38개 전부 통과했으나 모듈 경계 붕괴(표시 문구가 엔진 코어에 혼재·타입 산재·함수 오배치)로 사용자가 육안 3회 적발. **규정만 있고 강제 장치가 없어 조용히 재발**했으므로 `block-impl-delegation.py`(PreToolUse: Agent) 훅으로 실효화 — 코드 작성 지시가 담긴 Agent 스폰은 deny. 사용자 승인 예외는 프롬프트에 `[HARNESS: 구현위임 승인됨]`.*
- **리뷰어 스폰 시 이름을 붙이면 침묵한다(2026-07-20 실측).** 이름 있는 에이전트는 팀원 모드가 되어 최종 텍스트가 호출자에게 전달되지 않고 `SendMessage` 로만 보고할 수 있는데, 하네스 에이전트들의 `tools:` 화이트리스트에 그게 없어 **보고 수단 자체가 없었다** — 리뷰를 두 번 돌리고 두 번 다 빈손으로 끝난 뒤에야 원인이 잡혔다(무명 스폰으로 대조 확인). 전 에이전트 `tools:` 에 `SendMessage` 를 추가해 양쪽 스폰 모드를 모두 지원하게 했다. **에이전트 도구를 화이트리스트로 좁힐 때는 보고 경로가 남는지 반드시 확인할 것.**
- **자기-스폰 리뷰(`/review-*`)는 통과 보증이 아니다.** 머지 최종 게이트는 **외부 네이티브 `/code-review`**(중요 변경 `ultra`) — 모델·인프라 독립 + 발견 독립검증. 내부 OK ≠ 머지 허가.
- **리뷰가 낸 수정은 메인 세션이 자기인증하지 않는다.** 리뷰(내부·외부·`--fix`)가 찾은 결함을 메인 세션이 고쳤으면 상태는 *수정 적용됨·검증 대기*지 "완료"가 아니다. 정확성이 미묘한 fix(보안·RLS·동시성·SQL 시맨틱·마이그레이션)는 **별도 인스턴스가 재검증**(실측/pgTAP 우선, 불가 시 문서근거+실측부재 명시) — 통과 전 "완료/클린" 보고 금지. 자명한 fix(문구·참조·typecheck 가 곧 판정)는 그 기계 게이트로 충분. 상세 = [`docs/REVIEW-PROTOCOL.md`](docs/REVIEW-PROTOCOL.md) §수정 검증 규약.
- 단발 감사 = 그 스냅샷만 클린. 새 코드엔 새 외부 리뷰.
