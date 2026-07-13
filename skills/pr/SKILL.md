---
name: pr
description: 현재 브랜치 PR open/업데이트. 브랜치명에서 Story 번호 추출 → Closes 자동. plan 경로 Refs 자동 삽입.
disable-model-invocation: true
allowed-tools: Bash(git *), Bash(gh *)
argument-hint: [--draft] [--no-close] [--title=<t>] [--body-file=<path>]
---

`docs/RULES.md`(PR 규칙) 구현. Story PR 표준화.

## 1. 상태 수집 (병렬)
- `git rev-parse --abbrev-ref HEAD` → 현재 브랜치
- 브랜치명 `(feat|fix|chore|docs|refactor)/(?<issue>\d+)?-?(?<slug>.*)` → Story 번호 추출(없으면 null)
- `git log origin/main..HEAD --oneline` · `git diff origin/main..HEAD --stat`
- `gh pr list --head <branch> --json number,url` → 기존 PR 여부

## 2. 사전 체크
- main 이면 에러: `"main 에서 PR 불가. /branch 로 분기"`
- 커밋 0건이면 경고: `"커밋 없음. /commit 먼저"`
- upstream 없으면 `git push -u origin <branch>` 자동
- **verify 게이트(ready PR 선행조건):** 이 세션에서 `/verify`(또는 `verifier` 에이전트) PASS 를 확인하지 못했으면 **ready PR 를 열지 않는다** — `--draft` 로만 열거나, 먼저 `verifier` 를 호출해 PASS 를 받는다. RULES §3 "verify = 머지 직전 필수" 를 PR 시점에 기계적으로 강제(무인 실행 시 미검증 머지 방지). 사용자가 명시적으로 강행하면 경고 1줄 후 진행.

## 3. 메시지 조립
**title**: `--title` 또는 최신 커밋 subject(single) / 브랜치 slug 기반.
**body**:
```markdown
## Summary
<대표 커밋 2~3개 subject 요약>

Closes #<N>   <!-- Story 번호 있고 --no-close 없을 때 자동 -->

Refs:
- Plan: docs/plans/<해당 plan 파일>

## Changes
<git diff --stat 요약>

## Test plan
- [ ] ...   <!-- 체크박스 3~5개 -->

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
`--body-file` 있으면 파일 사용(단 Closes/Refs 자동 주입은 유지).

## 4. 실행
- PR 없음: `gh pr create --title "<t>" --body "<b>"`(+`--draft`)
- PR 존재: `gh pr edit <N> --body "<b>"`(title 은 `--title` 명시 시만)

## 5. 출력
`✓ PR #<N>: <title>` + url + `Closes #<Story>`(있으면) + `Status: draft|ready`

## 6. 금지
- main 에서 실행 · title/body 에 비밀 파일 경로 · `git push --force` 로 upstream 재작성 · PR body 에 `CLAUDE_COMMIT_APPROVED=1` 노출
