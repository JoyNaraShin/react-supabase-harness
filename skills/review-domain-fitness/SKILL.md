---
name: review-domain-fitness
description: 도메인 적합성(fitness) 리뷰 — domain-fitness-reviewer 서브에이전트(엔티티-화면-가치 종단 / 워크플로우 폐곡선 / 이중입력·마찰 / 가치 정당화 / 모델 중복·충돌 5축). 나머지 리뷰어가 per-file/per-slice 로 "잘 만드는가"만 보는 빈칸을 메워 "맞는 걸 만드는가"를 cross-cutting 으로 검증. "스키마에 그렸는데 읽는 코드 0인 고아/절단", "같은 데이터 두 번 입력", "답 없는 화면", "중복 모델"을 코드 짜기 전·또는 통합 시점에 적출. 수동 호출 전용(/review-domain-fitness [도메인|워크플로우|비우면 전체]).
disable-model-invocation: true
argument-hint: [도메인/워크플로우/경로 | 비우면 프로젝트 전체 종단 감사]
---

`domain-fitness-reviewer`(opus) 서브에이전트로 **도메인 적합성** 리뷰. craft 리뷰어 9종(architect·react·db·security·ux·design·plan-consistency·verifier·planner)이 구조적으로 못 보는 **"이 시스템이 실제 업무를 닫는가, 그린 데이터가 가치까지 흐르는가"**를 종단으로 본다.

> **왜 필요한가**: 슬라이스마다 Critical 0 으로 머지되는데 *합쳐진 시스템이 맞는가*는 누구 담당도 아니다 → 모든 재설계가 늦게·오너가 손으로 뒤집힘. 이 리뷰가 그 전역 시야를 **코드 전·통합 시점**으로 당긴다.
> **언제 돌리나**: ① `/phase 0` 도메인·IA 산출물 검증(코드 전 — 가장 효과 큼) ② Epic plan 적대 리뷰의 한 렌즈 ③ 통합 시점("슬라이스 다 됐는데 시스템이 맞나").

## 1. 대상 + 바
- `$ARGUMENTS` 있으면 그 도메인/워크플로우/경로. 없으면 프로젝트 전체 종단 감사(스키마 + features + plans).
- **타깃 사용자·도메인·규모 확인** → 바 설정(소규모 = 절제·과설계 경계 / 대규모 = 망라). 규모 잘못 잡으면 소규모에 과설계 강요.

## 2. 에이전트 호출
- `subagent_type: "domain-fitness-reviewer"`. 프롬프트에 **① 대상 도메인/워크플로우 ② 적용 바(사용자·규모) ③ (있으면) `/phase 0` 산출물 경로**. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `domain-fitness-reviewer`. Read the plugin's `agents/domain-fitness-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.
- 에이전트는 **자기 디스커버리부터**(스키마 introspect + `grep <table> src/` 로 고아/절단 대조) — 요약해 떠먹이지 않는다(REVIEW.md 규약).

## 3. 출력
- 에이전트 리포트(VERDICT FIT/GAP/REDESIGN + 5축 findings + 결함 원장)를 **그대로** 노출. 재가공 금지.

## 4. 컴패니언
- 코드 전 단계: `/phase`(planner) 가 `/phase 0` 도메인·IA 산출물을 쓰고, 이 리뷰가 그걸 검증한 뒤 `/phase 1` 진입(WORKFLOW.md).
- 짝 리뷰: `review-architect`(구조)·`review-db`(스키마 정합) — fitness 와 직교(맞는가 vs 잘 지었는가). 큰 재설계 판단은 셋을 병렬로(REVIEW.md 복수 렌즈).
