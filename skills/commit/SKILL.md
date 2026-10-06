---
name: commit
description: 커밋 생성 — git status/diff 분석 → 메시지 제안 → 사용자 명시 승인 → 실행. 파괴적-git 차단 훅을 합법 우회하는 유일 경로.
disable-model-invocation: true
allowed-tools: Bash(git *)
---

`${CLAUDE_PLUGIN_ROOT}/docs/RULES.md`(커밋 승인 절차)를 표준화한 스킬. PreToolUse 차단 훅(`block-destructive-git`)을 우회하는 **유일한 합법 경로**이며, 서브커맨드 접두사 `CLAUDE_COMMIT_APPROVED=1` 로 훅을 bypass(커밋 한정).

## 1. 상태 수집 (병렬)
- `git status` · `git diff --stat` · `git diff --cached --stat` · `git log --oneline -5`(스타일 참조)

## 2. 변경 분석
- 변경 없으면 "커밋할 변경 없음" 후 종료
- 여러 관심사 섞이면 **경계 쪼개기 제안**(한 커밋 = 한 관심사)
- 비밀 파일(`.env*`, `credentials*`, `*.key`, `id_rsa*`, `*.pem`) 섞이면 **경고 + 제외 확인**
- 40+ 파일이면 경계 재검토 제안

## 3. 메시지 초안
시드 우선순위: ① 변경된 plan slice(`docs/plans/phase-*.md` 또는 `docs/plans/<slug>.md`)의 `**커밋 경계**:` 블록 → ② `git log -5` 스타일(fallback).

포맷:
- `<type>(<scope>): <subject>` — 70자 이내 (Conventional Commits)
- 본문 1~2문단 — "왜"에 초점
- `Refs: <plan § / issue #>` (관련 있으면)
- `Co-Authored-By: Claude <현재 세션 모델명> <noreply@anthropic.com>` footer — 모델명을 리터럴로 고정하지 않는다(RULES §11)

## 4. 사용자 승인
출력: 스테이징 대상 파일 목록(정확한 파일명 — `git add .`/`-A` 금지) + 커밋 메시지 초안.
응답: `approve`/`ok`/`커밋`/`go` → 실행 / `edit`/`수정` → 재제시 / `reject`/`취소` → 종료 / 그 외 → 대기.

## 5. 실행 (승인 직후)
1. `git add <정확한 파일 목록>`
2. `CLAUDE_COMMIT_APPROVED=1 git commit -m "$(cat <<'EOF'`
   `<메시지 본문>`
   ``
   `Co-Authored-By: Claude <현재 세션 모델명> <noreply@anthropic.com>`
   `EOF`
   `)"`
3. `git status` 로 성공 확인 → 마지막 커밋 sha + 제목 출력

**`CLAUDE_COMMIT_APPROVED=1` 접두사는 이 스킬에서만.** 다른 맥락(직접 실행·다른 스킬·에이전트)에서 사용 시 우회 의도로 간주 — 즉시 경고.

## 6. 실패 대응
- pre-commit 훅(lint/test) 실패 → 에러 리포트 + 수정 → **수정·재스테이징·새 커밋**(amend 금지)
- 차단 훅 발동 → 명령 구성 점검

## 7. 금지
- `--no-verify`/`--no-gpg-sign` 등 훅 우회(사용자 명시 요청 시만) · `--amend`(별도 지시 없으면) · `git push` 자동 · `git add .`/`-A` · 비밀 파일 스테이징 · 여러 관심사 한 커밋 · `CLAUDE_COMMIT_APPROVED=1` 타 맥락 재사용
