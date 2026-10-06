# Review instructions — react-supabase-harness (Claude Code 플러그인 하네스)

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

## 종합 규약 집행 (복수 리뷰어 종합 시)

복수 적대 렌즈를 병렬로 돌린 뒤 결과를 종합할 때, 종합 규약(원장 join·보존 검사·severity 운반·재대조 큐)의 **집행 주체는 메인 루프 산문이 아니라 `scripts/synthesize.py`** 다. 산문 원칙(`docs/REVIEW-PROTOCOL.md` §종합 규약)은 그대로지만, 커버리지·충실도 누수는 회계로 막고 루프의 압축 편향을 집행에서 배제한다.

- **rule 1 (모든 reviewer 원장 emit)**: 이제 `agents/*.md` 리뷰어 전원이 리포트 끝에 machine-readable 결함 원장을 emit 한다 — `plan-consistency-reviewer` 포함(각 agent 스펙이 강제). 문구가 사실이 됐다.
- **모드 A (종합 시작)**: `python3 scripts/synthesize.py <report1.md> <report2.md> ...` — agent별 행 수 집계 + 전 행 union 의 join 표 스켈레톤(목적지·처리 빈칸 강제, 메인 루프가 채움) 출력. 원장을 못 찾은 리포트는 크게 경고하고 exit≠0(침묵 스킵 금지).
- **모드 B (종합 후 검증)**: `python3 scripts/synthesize.py --check <joined.md> <report1.md> ...` — 종합 문서에 모든 `agent:id` 가 보존됐는지(누락=침묵 드롭) + 원본 severity 문자열이 각 행에 보존됐는지(불일치=강등 의심) 대조, 있으면 exit 1. `처리=merged/rejected` 행은 "적대 재대조 큐"로 별도 출력(rule 5 부분집합 — 판단 개입 행만 독립 재확인).
- 표준 라이브러리만 사용 · 파싱 관대(펜스/표·컬럼 변형 허용), 실패는 시끄럽게.

## 수정 검증 (리뷰가 낸 fix 는 자기인증 금지)
리뷰를 독립 위임해도, 그 리뷰가 찾은 결함을 메인 루프가 고치고 다시 메인 루프가 "맞다"고 선언하면 독립성이 fix 단계에서 무효화된다(실측: RLS Critical 수정을 검증 없이 "완료·클린"으로 선언했고 사람이 다시 물어서야 드러났다). `docs/REVIEW-PROTOCOL.md` §수정 검증 규약을 따른다:
- **"수정 적용됨" ≠ "완료".** 검증 통과 전엔 "완료/클린" 보고 금지.
- **미묘한 수정(보안·RLS·동시성·SQL 시맨틱·프로토콜 계약·널리 복제되는 골격)은 새 독립 에이전트가 재판정** — 실측 우선(실행/pgTAP), 불가 시 공식 문서 근거 + "실측 부재" 명시. 스킬에 박아 배포하는 SQL 골격(`auth-scaffold` 등)이 특히 대상(전 프로젝트 전파 표면).
- 자명한 수정(문구·참조·typecheck/lint 가 곧 판정)은 그 기계 게이트로 충분.
