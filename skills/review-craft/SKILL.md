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

## 2. 시각 캡처 (에이전트 스폰 **전** — 메인 세션이 수행)
공식 실패 패턴 "trust-then-verify gap" 방어 — 디자인 퍼스트 원칙(RULES §1)의 검증 짝. craft-reviewer 는 스크린샷이 있으면 픽셀로, 없으면 코드-only(신뢰도 낮음 명시)로 판정한다(에이전트 스펙).
- dev 서버 구동 확인(안 떠 있으면 기동 시도 — 실패하면 이 단계 생략).
- claude-in-chrome 으로 **변경 영향 핵심 화면 3~5개** 스크린샷 → 세션 스크래치에 저장(데스크톱 + 대표 1개는 모바일 폭).
- 스크린샷 경로 목록 + Phase 0 화면 IA 의 **무드 1줄**을 §3 프롬프트에 포함.
- 캡처 불가(서버·브라우저 부재) 시 생략 — 에이전트가 "코드-only·신뢰도 낮음"을 Summary 에 명시하는 것으로 폴백.

## 3. 에이전트 호출
- `subagent_type: "react-supabase-harness:craft-reviewer"`. 프롬프트에 (§2 수행 시) `렌더 스크린샷: <경로들> / 무드 바: <1줄>` 포함. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `craft-reviewer`. Read the plugin's `${CLAUDE_PLUGIN_ROOT}/agents/craft-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 4. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
