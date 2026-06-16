---
name: review-db
description: DB·백엔드 리뷰 — db-reviewer 서브에이전트(스키마·인덱싱·트랜잭션/정합·RLS 성능·마이그레이션 5축). Supabase/PostgreSQL 성능·데이터 정합성 전담.
disable-model-invocation: true
argument-hint: [경로·스코프 | 비우면 migrations 전체 + api 워크로드]
---

`db-reviewer`(opus, 적대적·실측) 서브에이전트로 Supabase/PostgreSQL **성능·데이터 정합성** 리뷰. 마이그레이션 SQL·라이브 스키마(MCP introspection)·실제 쿼리 워크로드를 직접 읽고 대표 볼륨 `EXPLAIN` 으로 실측한다.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 이 스킬은 *결함 적발*만 한다 — 스키마/쿼리 **수정**은 리뷰가 아니라 메인 세션 몫(리뷰 → fix 분리).
> **양보**: RLS *접근제어 정확성*(인가)은 `/review-security`. 본 스킬은 같은 정책의 *성능·정합*만.

## 1. 대상·맥락 확정
- `$ARGUMENTS` 있으면 그 스코프(예: "핫패스" = tasks 목록/검색/facet 카운트 + 재고 무결성 + 완료/재작업 RPC + 그 RLS·트리거).
- 없으면 `supabase/migrations/**` 전체 + `src/features/**/api/**` 쿼리 워크로드.
- **MCP 전제**: 라이브 introspection·`EXPLAIN`·`get_advisors` 를 위해 `supabase`(또는 프로젝트의 supabase MCP) 활성 필요. 없으면 마이그레이션·코드 정적 리뷰로 폴백(실측 부재를 리포트에 명시).
- **데이터 규모**를 에이전트에 알려준다(과설계 방지) — 영세/중/대. 미상이면 에이전트가 대표 볼륨을 트랜잭션 안에서 합성해 측정.

## 2. 에이전트 호출
- `subagent_type: "db-reviewer"`. 스코프·규모·도메인 불변식(예: 재고 = Σstock_logs, 완료 1회성)을 프롬프트에 명시.
- 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `db-reviewer`. Read the plugin's `agents/db-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly. Use the supabase MCP for live introspection and transaction-wrapped EXPLAIN; never COMMIT.
- 스코프가 크면(전체 스키마) **적대적 복수 렌즈로 병렬** 위임 권장 — 성능(인덱싱·플랜) / 무결성·동시성 / RLS·마이그레이션. 각 에이전트 자기 디스커버리.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지. 복수 렌즈면 누그러뜨리지 말고 종합.

## 4. 컴패니언
- `/review-security` — RLS 인가·시크릿·PII (접근제어 정확성).
- `/review-architect` — 모듈 구조·경계.
- fix 단계는 리뷰가 아니라 메인 세션 + 마이그레이션 작성(`/db-migration`).
