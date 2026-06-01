# Review instructions — nara-stack-harness (Claude Code 플러그인 하네스)

이 레포는 앱 소스가 아니라 **Claude Code 플러그인**이다: `skills/<name>/SKILL.md`(마크다운+frontmatter), `agents/*.md`, `hooks/*.py` + `hooks/hooks.json`, `docs/*.md`, `.claude-plugin/{plugin,marketplace}.json`. 리뷰는 "플러그인이 의도대로·안전하게 동작하는가"에 맞춘다.

## Important (실제로 깨지는 것만)
- **훅 출력/입력 계약 위반**: PreToolUse가 차단을 못 하거나(현행 스펙 `hookSpecificOutput.permissionDecision:"deny"` 또는 exit code 2), PostToolUse/SessionStart의 `additionalContext`가 현행 래퍼를 안 따라 무시되는 경우.
- **안전 우회 결함**: `block-destructive-git.py`의 정규식이 파괴적 git/rm을 놓치거나, `CLAUDE_COMMIT_APPROVED=1 git commit` 외 경로로 우회 가능한 경우. false negative(차단 누락)는 Important, 과차단은 Nit.
- **스킬 frontmatter 오류**: 존재하지 않는/오타 필드, 잘못된 `allowed-tools` 문법, `disable-model-invocation` 누락으로 의도치 않은 자동 호출.
- **에이전트 정의 결함**: 플러그인 에이전트 금지필드(`permissionMode`/`hooks`/`mcpServers`) 사용, 잘못된 `model` ID, read-only 에이전트가 쓰기 도구 보유.
- **매니페스트 오류**: `plugin.json`/`marketplace.json` 필수 필드 누락·스키마 위반·버전 불일치.
- **`${CLAUDE_PLUGIN_ROOT}` 등 경로/변수 오용**, 존재하지 않는 파일 참조.
- **스킬 간 책임 중복/모순**(예: 두 스킬이 같은 커밋 경로를 다르게 규정).

## Nit (최대 5개까지만)
- 마크다운 스타일·문법·줄바꿈, 주석 부재, 한국어 표현 다듬기.
- 동작에 영향 없는 JSON/YAML 포맷.

## Do not report
- 마크다운 line length/wrapping.
- CI(`pnpm check`)가 이미 잡는 포맷.
- 생성물·예시 코드의 사소한 스타일.
- "이론적으로 가능한" 추상 시나리오(실제 재현 경로 없는 것).

## 참고 (현행 Claude Code 스펙 기준, 2026-06)
- PreToolUse 차단: `hookSpecificOutput.permissionDecision: "deny"` + `permissionDecisionReason`, 또는 exit code 2(stderr=메시지). 레거시 top-level `{"decision":"block"}`은 비권장.
- 컨텍스트 주입: `hookSpecificOutput.additionalContext`(+ `hookEventName`).
- 플러그인 에이전트 허용필드: name·description·model·tools·disallowedTools·skills·memory·background·isolation(worktree)·effort·maxTurns.
