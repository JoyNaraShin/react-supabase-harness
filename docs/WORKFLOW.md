# WORKFLOW — 이슈 플로우 (정본)

수주/데모 프로젝트의 표준 작업 흐름. 스킬이 각 단계를 자동화한다.

## 전체 흐름
```
/phase   →  plan 작성 (Phase → Epic → Story)         [planner + architect 자동검증]
/issue   →  Epic Issue + 모든 Story Issue 일괄 생성   [type/area/phase 라벨, sub-issue]
/branch  →  Story 브랜치 분기  <type>/<issue#>-<slug>
(executor) 슬라이스 구현 → /check 상시
/commit  →  승인 게이트 커밋 (Conventional Commits)
/verify  →  PR 직전 full 검증 (typecheck + biome + build)
/pr      →  PR open (Closes #<Story> 자동) → 리뷰 → squash merge
```

## 브랜치 전략
- `<type>/<issue#>-<slug>` (feat/fix/chore/docs/refactor). main/develop 직접 push 금지.
- 1 Story = 1 브랜치 = 1 PR. squash merge → 단일 커밋(롤백은 `git revert <sha>` 1회).

## 라벨 3축
- `type:` feat/fix/chore/docs/epic/task/bug
- `area:` 모듈/도메인 (예: area:board, area:admin)
- `phase:` phase 번호

## 품질 게이트
- 코드: 슬라이스마다 `/check`, PR 전 `/verify`, 그리고 `/review-architect`(+ 필요 시 `/review-security`) Critical 0 + Major 0.
- 안전: `block-destructive-git` 훅이 파괴적 git 차단. `/commit`만 합법 우회.

## 일일 리듬
- 세션 시작 시 `session-start-summary` 훅이 브랜치·Phase·미커밋 3줄 주입.
- 작업 → `/check` → 슬라이스 완료 → `/commit` → Story 완료 → `/verify` → `/pr`.
