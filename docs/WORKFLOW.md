# WORKFLOW — 이슈 플로우 (정본)

수주/데모 프로젝트의 표준 작업 흐름. 스킬이 각 단계를 자동화한다.

## 전체 흐름
```
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
- 구현은 **메인 세션(opus)** 이 직접(약한 서브에이전트 위임 X). 구현 보조 스킬:
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
- **in-loop(자기-스폰 · 자문)**: 슬라이스마다 `/check`, PR 전 `/verify`, `/review-architect`(+보안 민감 변경 `/review-security`, +UI 변경 `/review-ui`, +DB·쿼리 변경 `/review-db`, +React 컴포넌트/훅 `/review-react`). 적대적으로 돌지만 **자기-스폰이라 통과를 *보증하지 않는다*** — Critical/Major 0은 머지 *허가*가 아니라 머지 *후보* 조건.
- **최종 머지 게이트(외부 · 무편향)**: PR/머지 직전 **네이티브 `/code-review`**(중요 변경은 `/code-review ultra`). 작성·자문과 독립된 외부 리뷰가 각 발견을 독립검증 → 이게 실제 머지 허가. 내부 리뷰가 OK여도 외부 리뷰 Critical 미해결이면 머지 금지.
- 리뷰 독립성 원칙: **리뷰어 역량 ≥ 작성자 · 작성자와 다른 인스턴스 · 적대적**(RULES §11).
- **서비스 게이트(코드 머지 ≠ done)**: 배포·납품·핸드오프 직전 `/prod-readiness`. 코드 게이트(`/verify`)가 "컴파일되는가"라면 이건 "서비스로 띄울 수 있는가" — dev DB 분리·도메인·백업·에러추적·테스트·핸드오프를 BLOCK 0까지. done의 정의를 코드에서 서비스로 끌어올려 50% 벽을 넘긴다.
- 안전: `block-destructive-git` 훅이 파괴적 git 차단. `/commit`만 합법 우회.

## 일일 리듬
- 세션 시작 시 `session-start-summary` 훅이 브랜치·Phase·미커밋 3줄 주입.
- 작업 → `/check` → 슬라이스 완료 → `/commit` → Story 완료 → `/verify` → `/pr`.
