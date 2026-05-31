---
name: phase
description: Phase / Epic / Story plan 작성·갱신 — planner 위임 + architect-reviewer 자동 검증. 3-tier 계획 진입점.
disable-model-invocation: true
argument-hint: <n> [slug] | <n> epic <seq> <slug> | <n> epic <seq> story <story-seq> <slug>
---

`planner` 에 3-tier(Phase → Epic → Story) plan 작성·갱신 위임. `docs/PLANNING.md`(plan 포맷)·`docs/RULES.md`(계획 규칙) 기준. Plan → Epic Issue → Story Issue 일괄 → feature 브랜치 진입점.

## 1. 모드 / 경로
- **Phase**: `/phase <n> [slug]` → `docs/plans/phase-<n>-<slug>.md`. slug 미지정 시 기존 `phase-<n>-*.md` 탐색(정확히 1개=갱신, 0개=에러, 2+=slug 요구).
- **Epic**: `/phase <n> epic <seq> <slug>` → `docs/plans/phase-<n>-epic-<seq>-<slug>.md`. (~80줄 cap, 큰 흐름·Story 분할만). 상위 Phase plan 존재 전제.
- **Story**: `/phase <n> epic <seq> story <story-seq> <slug>` → `...-story-<story-seq>-<slug>.md`. (1 PR 단위 세부 Acceptance + 수동 회귀). 상위 Epic plan 전제. **Single-Story Epic 도 분리 필수.**
- 인자 비었으면 사용법 출력 후 종료.

## 2. 에이전트 호출
- `subagent_type: "planner"` 로 Agent 1회. 프롬프트: `대상 파일: <경로> / 모드: create|update / Tier: Phase|Epic|Story / Phase:<n>[,Epic:<seq>][,Story:<story-seq>] / slug:<slug>. PLANNING.md 의 해당 tier 필수 섹션 준수. agents/planner.md 스펙 전체 따름.`
- not found 시 `general-purpose` 재호출(앞머리에 planner.md 를 시스템 프롬프트로 읽으라 지시).
- plan 파일 Write 는 planner 가 수행. 메인 세션은 요약 1줄: `✓ <path> (<N> slices|stories) [created|updated]`.

## 3. Architect 자동 검증
planner Write 직후 메인 세션이 `architect-reviewer` 를 **자동 호출**(plan 단계 finding 은 1줄 수정으로 끝나는 가장 싼 시점). 대상=방금 작성된 plan 파일, 5축(Structure/Dependencies/Boundaries/Composition/Evolution). 리포트 그대로 노출. Critical/Major 있으면 사용자 결정 → plan patch. Critical 0 + Major 0 또는 사용자 OK 시 다음 단계 진입.

## 4. 금지
- 코드 편집 / git 커밋·브랜치 — 이 스킬은 plan 문서 전용
- planner 우회해 메인 세션이 직접 plan Write — 항상 planner 경유

## 5. 전체 흐름
`Phase plan → Epic plan → Story plan(각 1파일) → Epic Issue + 모든 Story Issue 일괄(/issue) → /branch → /commit → /pr`
