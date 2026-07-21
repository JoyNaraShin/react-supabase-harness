# WORKFLOW — 이슈 플로우 (정본)

수주/데모 프로젝트의 표준 작업 흐름. 스킬이 각 단계를 자동화한다.

## 전체 흐름
```
/domain-research → 도메인 도시에 (기존 장부 흡수 + 성숙 SaaS teardown·엣지 규칙·용어)  [greenfield 기본값]
/phase 0 →  도메인·IA 산출물 (스토리맵·워크플로우·화면IA·가정표 md 1장)  [structure-fitness-reviewer 검증 + 인터뷰 필수]
/phase   →  plan 작성 (Phase → Epic → Story)         [planner + architect 자동검증]
/issue   →  Epic Issue + 모든 Story Issue 일괄 생성   [type/area/phase 라벨, sub-issue]
/branch  →  Story 브랜치 분기  <type>/<issue#>-<slug>
(메인 세션) 슬라이스 구현 → /check 상시
/commit  →  승인 게이트 커밋 (Conventional Commits)
/verify  →  PR 직전 full 검증 (typecheck + biome + build)
/pr      →  PR open (Closes #<Story> 자동) → 리뷰 → squash merge
─────────  (코드 완료) ─────────────────────────────────────
/prod-readiness  →  배포·납품·핸드오프 직전 서비스 가능성 게이트
                    (dev DB 분리·도메인·백업·에러추적·테스트·리뷰·핸드오프)
                    BLOCK 0 이어야 SHIP. [commercial|demo]
```
- **`/phase 0` = 적합성(fitness) 게이트 — craft 전에 "맞는 걸 만드는가".** 나머지 게이트·리뷰어는 전부 per-file/per-slice 로 *잘 만드는가(craft)* 만 본다 → 슬라이스마다 Critical 0으로 머지돼도 합쳐진 시스템이 *맞는가*는 누구 담당도 아니어서 재설계가 늦게·오너가 손으로 뒤집힌다. `/phase 0`은 그 전역 시야를 코드 전으로 당긴다: **도메인 스토리맵 + 핵심 워크플로우 3~5개 + 화면 IA(무드 1줄) + 가정 표** 를 md 1장으로 쓰고 `structure-fitness-reviewer`(+`plan-consistency`)가 검증한 뒤에만 `/phase 1`(스키마·화면) 진입. 산출물 빈칸은 템플릿 `docs/plans/*.template.md`(react-supabase-stack 출하). **중량 의식(이벤트스토밍·DDD) 금지 — md 1장이 상한**(영세 1인엔 과설계). 이 산출물이 이후 `structure-fitness-reviewer`·`craft-reviewer`(무드 바)의 채점 기준표가 된다.
- **한 문장 diff 는 이 파이프라인을 생략한다**(RULES §1) — 오타·로그 한 줄·rename 급은 planner·issue·branch 없이 직접 수정 → `/commit` 승인 게이트만. 그 이상부터 위 흐름.
- 구현은 **메인 세션(세션 모델)** 이 직접(약한 서브에이전트 위임 X). 구현 보조 스킬:
  - 인증(로그인·profiles·RLS·보호 라우트) → `/auth-scaffold` (회원제 첫 슬라이스 표준)
  - 새 도메인 모듈 → `/feature-scaffold <name>` (표준 `features/` + `pages/` 구조)
  - DB 스키마 변경 → `/db-migration <slug>` (RLS enable + rollback 주석, `INFRA.md`)

## 브랜치 전략
- `<type>/<issue#>-<slug>` (feat/fix/chore/docs/refactor). main/develop 직접 push 금지.
- 1 Story = 1 브랜치 = 1 PR. squash merge → 단일 커밋(롤백은 `git revert <sha>` 1회).

## 라벨 3축
- `type:` feat/fix/chore/docs/epic/task/bug
- `area:` 모듈/도메인 (예: area:board, area:admin)
- `phase:` phase 번호

## 품질 게이트
- **in-loop(자기-스폰 · 자문 · 선택적)**: 슬라이스마다 `/check`, PR 전 `/verify` + **변경 성격 매칭 렌즈 1개**(구조·플로우 `/review-structure` · 보안·DB `/review-stability` · UI `/review-craft`). **3렌즈 전수는 Epic 통합 완료·prod-readiness 시점만**(RULES §3). 적대적으로 돌지만 **자기-스폰이라 통과를 *보증하지 않는다*** — Critical/Major 0은 머지 *허가*가 아니라 머지 *후보* 조건.
- **최종 머지 게이트(외부 · 무편향)**: PR/머지 직전 **네이티브 `/code-review`**(중요 변경은 `/code-review ultra`). 작성·자문과 독립된 외부 리뷰가 각 발견을 독립검증 → 이게 실제 머지 허가. 내부 리뷰가 OK여도 외부 리뷰 Critical 미해결이면 머지 금지.
- 리뷰 독립성 원칙: **리뷰어 역량 ≥ 작성자 · 작성자와 다른 인스턴스 · 적대적**(RULES §11).
- **서비스 게이트(코드 머지 ≠ done)**: 배포·납품·핸드오프 직전 `/prod-readiness`. 코드 게이트(`/verify`)가 "컴파일되는가"라면 이건 "서비스로 띄울 수 있는가" — dev DB 분리·도메인·백업·에러추적·테스트·핸드오프를 BLOCK 0까지. done의 정의를 코드에서 서비스로 끌어올려 50% 벽을 넘긴다.
- **기계 검증 ≠ 설계 검증.** `/check`·`/verify`(typecheck·biome·build)는 "컴파일되는가"만 판정한다 — 모듈 경계·계층 분리·타입 배치는 통과시킨 채 지나간다(2026-07-20 실측). 슬라이스가 구조를 건드렸으면 렌즈 리뷰 1개가 필수이며, `edit-counter` 훅이 편집 5회마다 둘 다 안내한다.
- 안전: `block-destructive-git` 훅이 파괴적 git 차단(`/commit`만 합법 우회). `block-impl-delegation` 훅이 구현의 서브에이전트 위임 차단(RULES §모델분담 실효화). `workflow-entry-guard` 훅이 플랜 없는 `src/` 첫 코드 생성 시 이 문서를 1회 주입 — 하네스 미탑승 방지.

## 일일 리듬
- 세션 시작 시 `session-start-summary` 훅이 브랜치·Phase·미커밋 3줄 주입.
- 작업 → `/check` → 슬라이스 완료 → `/commit` → Story 완료 → `/verify` → `/pr`.
