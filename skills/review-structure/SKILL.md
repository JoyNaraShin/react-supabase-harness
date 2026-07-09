---
name: review-structure
description: 구조·도메인 적합성 리뷰 — structure-fitness-reviewer 서브에이전트(모듈 구조·의존·경계·진화 + 엔티티-화면-가치 종단·폐곡선·고아/절단·이중입력·중복 모델).
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`structure-fitness-reviewer`(세션 모델 상속, 적대적) 서브에이전트로 구조·적합성 리뷰.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 머지 최종 게이트는 외부 네이티브 `/code-review`(RULES §11).

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로 / 없으면 `git diff main...HEAD --name-only` / 전부 비면 프로젝트 전체 종단 감사인지 되묻고 진행

## 2. 에이전트 호출
- `subagent_type: "structure-fitness-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `structure-fitness-reviewer`. Read the plugin's `agents/structure-fitness-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
