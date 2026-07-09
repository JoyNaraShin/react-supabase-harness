---
name: review-stability
description: 안정성 리뷰 — stability-reviewer 서브에이전트(인증·RLS 정확성·시크릿·입출력 공격 표면 + DB 스키마·인덱스·트랜잭션·마이그레이션 성능·정합, EXPLAIN 실측).
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`stability-reviewer`(세션 모델 상속, 적대적) 서브에이전트로 보안·DB 안정성 리뷰.

> **in-loop 자문** — 자기-스폰이라 머지 보증이 아니다. 머지 최종 게이트는 외부 네이티브 `/code-review`(RULES §11).

## 1. 대상 확정
- `$ARGUMENTS` 있으면 그 경로
- 없으면 `git diff main...HEAD --name-only` (특히 `src/features/auth/**`, `src/lib/supabase.*`, `src/lib/storage.*`, `supabase/migrations/`, `.env*`, OAuth/세션 처리 경로 포함)
- 전부 비면 되묻고 종료

## 2. 에이전트 호출
- `subagent_type: "stability-reviewer"`. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `stability-reviewer`. Read the plugin's `agents/stability-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.

## 3. 출력
- 에이전트 리포트를 **그대로** 노출. 재가공 금지(결함 원장 포함).
