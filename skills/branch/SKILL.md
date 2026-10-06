---
name: branch
description: origin/main 기준 feature/chore/fix 브랜치 생성. Story Issue 번호 prefix 지원.
disable-model-invocation: true
allowed-tools: Bash(git *)
argument-hint: <slug> [--issue=<N>] [--type=feat|chore|fix|docs|refactor]
---

`${CLAUDE_PLUGIN_ROOT}/docs/RULES.md`(브랜치 전략) 규약대로 분기.

## 1. 인자 파싱
- `$ARGUMENTS[0]` = **slug**(필수, kebab-case — 예: `board-pagination`)
- `--issue=<N>` = Story Issue 번호(옵션). 있으면 브랜치명에 번호 prefix
- `--type=<t>` = `feat`(default)/`chore`/`fix`/`docs`/`refactor`

## 2. 브랜치명 조립
| 인자 | 결과 |
|---|---|
| `slug=board-pg --issue=26 --type=fix` | `fix/26-board-pg` |
| `slug=board-pg --issue=35` | `feat/35-board-pg` |
| `slug=fix-typo --type=docs` | `docs/fix-typo` |
| `slug=cleanup` | `feat/cleanup` |

## 3. 실행
1. 현재 브랜치가 main 아니면 경고: `"현재 <br>에서 분기됩니다. main 기준이면 먼저 'git switch main && git pull'"`
2. `git fetch origin main`
3. 브랜치명 중복 체크 — 존재 시 에러 종료
4. `git switch -c <branch>`(작업 트리 유지)
5. 요약: `✓ <branch> (from <parent>, <X> commits ahead of origin/main)` + Issue 링크(있으면)

## 4. 금지
- main/master 직접 전환 · 슬래시 2개+ slug(`feat/sub/slug`) · 인자 누락 시 자동 추측(되묻기) · 기존 브랜치 `-f` 덮어쓰기

## 5. 후속
`/commit` → `/pr`(자동 `Closes #<N>`)
