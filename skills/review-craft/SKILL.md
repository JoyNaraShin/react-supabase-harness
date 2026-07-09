---
name: review-craft
description: 크래프트 리뷰 — craft-reviewer 서브에이전트(시니어 FE 설계: 성능>확장성>가독성 + 기능적 UX·a11y + 시각 디자인 무드 정합·TIGHTEN/REBUILD). 결함마다 시니어 대안 코드 + "배울 점".
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`craft-reviewer`(세션 모델 상속, 적대적) 서브에이전트로 FE 크래프트·UX·디자인 리뷰.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 그리고 이 스킬은 *결함 적발*만 한다 — 새 UI **생성**·재디자인은 리뷰가 아니라 메인 세션 + `frontend-design` 스킬 몫(리뷰 → fix 분리).

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로 / 없으면 `git diff main...HEAD --name-only` 중 `*.tsx`·`*.css`·UI 관련 / 전부 비면 되묻고 종료

## 2. 에이전트 호출
- `subagent_type: "craft-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `craft-reviewer`. Read the plugin's `agents/craft-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
