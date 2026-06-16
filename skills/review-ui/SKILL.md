---
name: review-ui
description: UI/UX·접근성 리뷰 — ux-reviewer 서브에이전트(상태 망라·a11y·피드백·반응형·폼 5축). 디자인 취향이 아니라 기능적 UX·접근성 결함만. 수동 호출 전용(/review-ui [경로]).
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`ux-reviewer`(opus, 적대적) 서브에이전트로 UI/UX·접근성 리뷰.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 그리고 이 스킬은 *결함 적발*만 한다 — 새 UI **생성**·재디자인은 리뷰가 아니라 메인 세션 + `frontend-design` 스킬 몫(리뷰 → fix 분리).

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로
- 없으면 `git diff main...HEAD --name-only` (컴포넌트·pages·폼·모달·라우트)
- 전부 비면 되묻고 종료

## 2. 에이전트 호출
- `subagent_type: "ux-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `ux-reviewer`. Read the plugin's `agents/ux-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지.

## 4. 컴패니언 (리뷰가 아니라 보조/후속)
- `vercel:react-best-practices` — React 특화 a11y/perf/구조 체크리스트(보조 참조, Next 편향 감안).
- `frontend-design` — 리뷰에서 나온 결함을 **고치는 fix 단계**에서 새 UI 생성용. 리뷰 중에는 쓰지 않는다.
