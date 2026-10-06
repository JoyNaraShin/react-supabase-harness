---
name: verify
description: verifier 에 full 모드(typecheck + check + build)를 위임한다. PR 을 열거나 머지하기 직전의 품질 게이트로 쓴다.
disable-model-invocation: true
---

`verifier` 에 full 모드(`typecheck + check + build`) 위임. `/check` 보다 엄격, **배포·PR 직전** 사용.

## 1. 에이전트 호출
- `subagent_type: "react-supabase-harness:verifier"`, 프롬프트: `모드: full (typecheck + check + build). 대상: 전체 프로젝트. ${CLAUDE_PLUGIN_ROOT}/agents/verifier.md 스펙 준수.`
- not found 시 `general-purpose` 재호출, 앞머리에:
  > You are running as `verifier`. Read the plugin's `${CLAUDE_PLUGIN_ROOT}/agents/verifier.md` in full and treat it as your system prompt. Mode: full (typecheck + check + build). Save your full report to `<scratchpad>/verifier-ledger.md` section by section as you go, then reply with the path and the summary table.

## 2. 출력
- 리포트(`## Verify Report ... ## Verdict`)를 **그대로** 노출.

## 3. 가이드
- `PASS` → 머지/PR 가능. `FAIL` → `## Errors` 를 메인 세션이 수정 후 재검증.
- `/check` 상시 · `/verify` 최종 게이트. 둘 다 `verifier` 단일 에이전트, 모드만 다름.
