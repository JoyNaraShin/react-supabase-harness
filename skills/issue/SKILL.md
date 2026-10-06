---
name: issue
description: gh issue create 를 감싸 Conventional Commits title 에서 type 라벨을 붙이고 Epic sub-issue 관계(GraphQL)를 건다. plan 의 Epic·Story 를 Issue 로 옮길 때 쓴다.
disable-model-invocation: true
allowed-tools: Bash(gh *)
argument-hint: <title> [--type=feat|fix|chore|docs|epic|task|bug] [--labels=<csv>] [--epic=<N>] [--body-file=<path>] [--milestone=<name>]
---

이슈 플로우의 시작점. `${CLAUDE_PLUGIN_ROOT}/docs/RULES.md`(Issue 작성 규칙) 구현.

## 1. 인자 파싱
- `$ARGUMENTS[0]` = **title**(필수, `<type>(<scope>): <subject>` 권장)
- `--type=<t>` = Issue 타입(미지정 시 title prefix 추론)
- `--labels=<csv>` = 추가 라벨(예: `area:board,phase:2`). `type:<t>` 는 자동 부착
- `--epic=<N>` = 상위 Epic 번호 — 지정 시 **GraphQL addSubIssue** 로 관계 설정
- `--body-file=<path>` = body 파일. 미지정 시 `.github/ISSUE_TEMPLATE/<type>.md`
- `--milestone=<name>` = Milestone(옵션)

## 2. 타입 추론
title prefix → type: `feat(`/`feat:`→feat · `fix`→fix · `chore`→chore · `docs`→docs · `epic(`→epic · 매치 안 되면 `task`. `--type` 명시가 우선.

## 3. 실행
1. title 파싱 + type 확정
2. labels 조립: `type:<t>` + `--labels`
3. body: `--body-file` 또는 `.github/ISSUE_TEMPLATE/<type>.md`(부재 시 빈 body)
4. `gh issue create --title "<title>" --label "<labels>" --body "<body>" [--milestone "<m>"]`
5. 생성된 Issue 번호 추출
6. `--epic=<N>` 있으면 sub-issue 링크:
   - `gh issue view <N> --json id --jq .id`(Epic) / `gh issue view <new> --json id --jq .id`(Story)
   - `gh api graphql -H "GraphQL-Features: sub_issues" -f query='mutation { addSubIssue(input: { issueId: "<EPIC>", subIssueId: "<STORY>" }) { subIssue { number title } } }'`
7. 요약: `✓ #<N> <title> (<url>)` + `(Epic: #<E>)` if applicable

## 4. 금지
- title 에 `Closes #`/`Fixes #`(PR body 전용) · 존재하지 않는 라벨 · `--epic` 이 실제 `type:epic` 아닐 때 · 알 수 없는 `--type`

## 5. 후속
`/branch <slug> --issue=<N>` → `/commit` → `/pr`
