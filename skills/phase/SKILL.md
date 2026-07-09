---
name: phase
description: Phase / Epic / Story plan 작성·갱신 — planner 위임 + structure-fitness-reviewer 자동 검증. 3-tier 계획 진입점.
disable-model-invocation: true
argument-hint: <n> [slug] | <n> epic <seq> <slug> | <n> epic <seq> story <story-seq> <slug>
---

`planner` 에 3-tier(Phase → Epic → Story) plan 작성·갱신 위임. `docs/PLANNING.md`(plan 포맷)·`docs/RULES.md`(계획 규칙) 기준. Plan → Epic Issue → Story Issue 일괄 → feature 브랜치 진입점.

## 1. 모드 / 경로
- **Phase 0 (도메인·IA)**: `/phase 0 [slug]` → `docs/plans/phase-0-domain.md`. 코드·스키마 전 적합성 산출물(스토리맵·워크플로우·화면IA·가정표 — `PLANNING.md` Phase 0 4블록). **greenfield 면 `docs/plans/phase-0-domain-dossier.md` 존재 확인 — 없으면 `/domain-research` 먼저 권고**(사용자가 스킵을 승인하면 진행하되 가정 표 첫 행에 `도시에 부재` 명시). 작성 후 **`/review-structure` FIT 판정 권고** → Phase 1 진입.
- **Phase**: `/phase <n> [slug]` → `docs/plans/phase-<n>-<slug>.md`. slug 미지정 시 기존 `phase-<n>-*.md` 탐색(정확히 1개=갱신, 0개=에러, 2+=slug 요구).
- **Epic**: `/phase <n> epic <seq> <slug>` → `docs/plans/phase-<n>-epic-<seq>-<slug>.md`. (~80줄 cap, 큰 흐름·Story 분할만). 상위 Phase plan 존재 전제.
- **Story**: `/phase <n> epic <seq> story <story-seq> <slug>` → `...-story-<story-seq>-<slug>.md`. (1 PR 단위 세부 Acceptance + 수동 회귀). 상위 Epic plan 전제. **Single-Story Epic 도 분리 필수.**
- 인자 비었으면 사용법 출력 후 종료.

## 2. 인터뷰 게이트 (planner 스폰 **전** — 메인 세션이 수행)
planner 는 서브에이전트라 사용자와 대화형 루프가 불가능하다(출력이 단일 메시지로 귀환). 인터뷰는 이 스킬을 실행하는 **메인 세션**이 한다:
- 대상 범위에서 **열린 결정**(답에 따라 아키텍처·스키마·UX가 바뀌는 것 — 데이터 모델 형태·타입 인터페이스·UX 분기·권한 경계)을 식별. 없으면 생략하고 §3.
- AskUserQuestion 으로 **한 번에 한 질문**, 아키텍처가 바뀔 질문 우선, **최대 5문항**. 각 질문에 후보 2~3개 + 트레이드오프 1줄.
- 답을 **Decision Register 초안**(`| 결정 | 선택 | 검토한 대안 | 사유 | 뒤집힐 조건 |`)으로 정리해 §3 planner 프롬프트에 포함.
- update 모드는 보통 생략 가능. **Phase 0(greenfield)은 인터뷰 필수** — 최소 입력(배경·목표·디자인 방향)일수록 도메인 미지가 최대인 지점이 여기다. 도시에 §5(설계 갈림길)를 질문 재료로 쓰면 각 질문에 "레퍼런스 A는 X, B는 Y" 를 병기할 수 있어 오너가 도메인 비전문이어도 답할 수 있다. 미답 항목은 planner 가 가정 표에 기록.

## 3. 에이전트 호출
- `subagent_type: "planner"` 로 Agent 1회. 프롬프트: `대상 파일: <경로> / 모드: create|update / Tier: Phase|Epic|Story / Phase:<n>[,Epic:<seq>][,Story:<story-seq>] / slug:<slug>. PLANNING.md 의 해당 tier 필수 섹션 준수. agents/planner.md 스펙 전체 따름.` + (§2 수행 시) `인터뷰 결과 Decision Register 초안: <표>`
- not found 시 `general-purpose` 재호출(앞머리에 planner.md 를 시스템 프롬프트로 읽으라 지시).
- plan 파일 Write 는 planner 가 수행. 메인 세션은 요약 1줄: `✓ <path> (<N> slices|stories) [created|updated]`.

## 4. 자동 검증 (적대적 · 2종, plan 단계가 가장 싼 시점)
planner(세션 모델 상속) Write 직후 메인 세션이 **작성자와 다른 인스턴스**로 둘 다 자동 호출:
- **`plan-consistency-reviewer`(sonnet, 싼 기계 패스)** — PLANNING.md 4블록 준수·acceptance 테스트가능성·자가모순·미정의 참조·의존 그래프 sanity.
- **`structure-fitness-reviewer`(세션 모델 상속, 적대적 판단)** — 구조(모듈·의존·경계·진화) + 적합성(종단·폐곡선·고아/절단).

두 리포트 **그대로** 노출. Critical/Major 있으면 사용자 결정 → plan patch. 둘 다 Critical 0 + Major 0 또는 사용자 OK 시 진입.
> 리뷰어 ≥ 작성자 · 다른 인스턴스 · 적대적(RULES §11). 이 자동검증은 **in-loop 자문**이며, 코드 머지의 최종 게이트는 외부 네이티브 `/code-review`(WORKFLOW).

## 5. 금지
- 코드 편집 / git 커밋·브랜치 — 이 스킬은 plan 문서 전용
- planner 우회해 메인 세션이 직접 plan Write — 항상 planner 경유

## 6. 전체 흐름
`Phase plan → Epic plan → Story plan(각 1파일) → Epic Issue + 모든 Story Issue 일괄(/issue) → /branch → /commit → /pr`
