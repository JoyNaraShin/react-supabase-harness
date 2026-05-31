---
name: review-architect
description: 아키텍처/설계 리뷰 — architect-reviewer 서브에이전트(모듈 경계·의존성·구조 5축).
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`architect-reviewer` 서브에이전트로 설계 리뷰.

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로 / 없으면 `git diff main...HEAD --name-only` / 전부 비면 되묻고 종료

## 2. 에이전트 호출
- `subagent_type: "architect-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `architect-reviewer`. Read the plugin's `agents/architect-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지.
