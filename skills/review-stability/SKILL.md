---
name: review-stability
description: stability-reviewer 서브에이전트로 인증·RLS·시크릿·입출력 공격 표면과 DB 스키마·인덱스·트랜잭션·마이그레이션 정합을 리뷰한다. 인증·RLS·마이그레이션을 바꾼 뒤 쓴다.
disable-model-invocation: true
argument-hint: '[경로 | 비우면 현재 브랜치 diff 기준]'
---

`stability-reviewer`(세션 모델 상속, 적대적·실측) 서브에이전트로 보안·DB 안정성 리뷰. 공격 표면(Auth/RLS 정확성·시크릿·입출력)과 DB 성능·정합(스키마·인덱스·트랜잭션·마이그레이션)을 한 렌즈로, 대표 볼륨 `EXPLAIN` 으로 실측한다.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 머지 최종 게이트는 외부 네이티브 `/code-review`(RULES §11).
> **DB 전제**: 라이브 introspection·`EXPLAIN` 은 에이전트가 읽기 전용 `psql` 로 한다 — 프롬프트에 DB URL(로컬 또는 preview)을 준다. 에이전트에는 MCP 도구가 없으므로, Supabase advisors(`get_advisors`)가 필요하면 메인 세션이 먼저 돌려 결과를 프롬프트에 붙인다. DB 접근이 없으면 마이그레이션·코드 정적 리뷰로 폴백(실측 부재를 리포트에 명시). **데이터 규모**를 에이전트에 알려준다(과설계 방지) — 무결성 결함은 규모 무관, 성능 결함은 실측·규모 근거 필수.

## 1. 대상 확정
경로는 전부 **현재 프로젝트(작업 디렉터리) 기준**이다. `${CLAUDE_PLUGIN_ROOT}` 는 하네스 설치 경로라 리뷰 대상이 아니다.
- `$ARGUMENTS` 있으면 그 경로
- 없으면 `git diff "$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main)"...HEAD --name-only` (특히 `src/features/auth/**`, `src/lib/supabase.*`, `src/lib/storage.*`, `supabase/migrations/`, `.env*`, OAuth/세션 처리 경로 포함)
- 전부 비면 되묻고 종료

## 2. 에이전트 호출
- `subagent_type: "react-supabase-harness:stability-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `stability-reviewer`. Read the plugin's `${CLAUDE_PLUGIN_ROOT}/agents/stability-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly. Save your full report to `<scratchpad>/stability-reviewer-ledger.md` section by section as you go, then reply with the path and the summary table.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
