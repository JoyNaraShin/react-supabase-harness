---
name: check
description: 상시 검증 — verifier 서브에이전트에 위임 (typecheck + check 기본). 작업 중 자주 사용.
disable-model-invocation: true
argument-hint: [typecheck-only | check-only | build-only]
---

`verifier` 서브에이전트에 검증 위임. 작업 중간 상시용 (`/verify` 는 최종 게이트).

## 1. 모드 확정
- 인자 없음 → `typecheck + check`
- `typecheck-only` / `check-only` / `build-only`(보통 `/verify` 권장) — 그 외 인자는 허용 목록 안내 후 종료

## 2. 에이전트 호출
- `subagent_type: "react-supabase-harness:verifier"` 로 Agent 1회 호출. 프롬프트: `모드: <확정>. 대상: 전체 프로젝트. ${CLAUDE_PLUGIN_ROOT}/agents/verifier.md 스펙 준수.`
- `Agent type 'verifier' not found` 시 `general-purpose` 재호출, 앞머리에:
  > You are running as `verifier`. Read the plugin's `${CLAUDE_PLUGIN_ROOT}/agents/verifier.md` in full and treat it as your system prompt. Mode: `<확정>`.

## 3. 출력
- 에이전트 리포트(`## Verify Report ... ## Verdict`)를 **그대로** 노출. 재가공·서문·맺음말 금지. `## Verdict` 본문은 `PASS`/`FAIL` 단일 토큰.
