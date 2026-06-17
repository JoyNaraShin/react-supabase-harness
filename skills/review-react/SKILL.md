---
name: review-react
description: React/FE 크래프트 리뷰 — react-reviewer 서브에이전트(관심사·View/Logic·리렌더·가독성·확장성 5축). 시니어 FE 기준 설계 결함 + 시니어 대안 코드 + 배울 점.
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`react-reviewer`(opus, 적대적·가르치는) 서브에이전트로 React 크래프트 리뷰. 동작/정확성이 아니라 **설계**(관심사 분리·View↔Logic·리렌더·추상화·확장성)를 본다.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 동작 결함은 네이티브 `/code-review`, a11y 는 `/review-ui`, 구조는 `/review-architect`.
> **시니어 기준 게이트**: Major+ 는 패치가 아니라 *재설계* → 통과까지 loop. 결함마다 시니어 대안 코드 + 배울 점이 나오므로 fix 가 곧 학습.

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로(컴포넌트·훅·feature 모듈).
- 없으면 `git diff main...HEAD --name-only` 의 `.tsx`/`.ts`.
- **reference 동봉**: 같은 코드베이스의 잘 짠 동형 모듈(기존 컨트롤러 훅·폼 모달)을 비교 기준으로 알려주면 정확도↑.

## 2. 에이전트 호출
- `subagent_type: "react-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `react-reviewer`. Read the plugin's `agents/react-reviewer.md` in full and treat it as your system prompt. Follow the 5-axis rubric exactly — every finding needs a senior-alternative code sketch + a "배울 점" line.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지.

## 4. 캘리브레이션 (이 리뷰어의 핵심)
- 루브릭(`agents/react-reviewer.md` §5축)은 **사용자 기준으로 누적**한다. 사용자가 reject 하는 이유 = 새 루브릭 규칙 → 에이전트 §5축에 추가(또는 feedback 메모리). 3~4 사이클이면 사용자 바를 넘김.
- **shift-left**: 구현 위임/생성 전에 이 루브릭을 읽혀 평범한 생성을 차단(리뷰가 잡을 게 줄도록).
