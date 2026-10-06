---
name: review-structure
description: structure-fitness-reviewer 서브에이전트로 모듈 구조·의존·경계와 엔티티-화면-가치 종단(고아 테이블·절단·이중 입력·중복 모델)을 리뷰한다. plan 작성 직후와 Epic 통합 시점에 쓴다.
disable-model-invocation: true
argument-hint: '[경로 | 비우면 현재 브랜치 diff 기준]'
---

`structure-fitness-reviewer`(세션 모델 상속, 적대적) 서브에이전트로 구조·적합성 리뷰.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 머지 최종 게이트는 외부 네이티브 `/code-review`(RULES §11).

## 1. 대상 확정
경로는 전부 **현재 프로젝트(작업 디렉터리) 기준**이다. `${CLAUDE_PLUGIN_ROOT}` 는 하네스 설치 경로라 리뷰 대상이 아니다.
- `$ARGUMENTS` 있으면 그 경로 / 없으면 `git diff "$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main)"...HEAD --name-only` / 전부 비면 프로젝트 전체 종단 감사인지 되묻고 진행

## 2. 에이전트 호출
- `subagent_type: "react-supabase-harness:structure-fitness-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `structure-fitness-reviewer`. Read the plugin's `${CLAUDE_PLUGIN_ROOT}/agents/structure-fitness-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly. Save your full report to `<scratchpad>/structure-fitness-reviewer-ledger.md` section by section as you go, then reply with the path and the summary table.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
