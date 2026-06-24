---
name: review-design
description: 시각 디자인 크래프트 리뷰 — design-reviewer 서브에이전트(타이포·색·간격·컴포넌트 일관성·시각 위계 5축). ux-reviewer가 금지당한 "취향 판단"을 정면으로. "전문적으로 디자인됐나, 무드가 따로노나, 아마추어/AI-슬롭인가"를 원칙 기반 + 렌더 실측으로. 수동 호출 전용(/review-design [경로]).
disable-model-invocation: true
argument-hint: [경로 | 비우면 현재 브랜치 diff 기준]
---

`design-reviewer`(opus, 아트디렉터) 서브에이전트로 **시각 디자인 크래프트** 리뷰. `ux-reviewer`(기능)·`frontend-design`(생성)이 못 메우는 **"이미 만든 화면이 전문적으로 디자인됐는가"의 취향 비판** 담당.

> **in-loop 자문** — 자기-스폰이라 머지 보증 아님. *결함 적발*만 — fix(새 UI 생성·재디자인)는 메인 + `frontend-design` 몫.
> **취향 리뷰가 노이즈가 안 되는 이유**: 원칙(타입스케일·시맨틱 색·위계·일관성) + **맥락별 바**(ERP=절제, 랜딩=대담)로 판정. "그냥 싫다" 금지.

## 1. 대상 확정 + 바
- `$ARGUMENTS` 있으면 그 경로. 없으면 `git diff main...HEAD --name-only`(컴포넌트·pages·layouts). 전부 비면 되묻고 종료.
- **타깃 사용자·도메인 확인** → 평가 바 설정(B2B/ERP/백오피스 = 절제·밀집·정합 / 마케팅·랜딩 = 대담·개성). 잘못된 바로 깎으면 리뷰가 틀림.

## 2. ★ 렌더 캡처 (이 리뷰의 생명 — 코드만 읽으면 실패)
디자인은 **픽셀로 판정**한다. 코드의 Tailwind 클래스만으로 판정하면 이 하네스가 반복한 실패(렌더 미검증 → 레이아웃 깨짐 출하)를 답습.
- 로컬 앱이 떠 있으면(dev 서버) **chrome MCP 로 대상 화면을 캡처**(`tabs_context_mcp`→기존 인증 탭 / `navigate` → `computer` screenshot `save_to_disk:true`). 핵심 화면 3~6장(목록·상세·폼·모달 등 대표).
- 인증 필요 화면은 **이미 로그인된 탭 재사용**(새 세션마다 새 탭 원칙이나, 디자인 캡처는 인증 상태 필요).
- 캡처 경로(또는 캡처 불가 사실)를 다음 단계에 명시 전달.

## 3. 에이전트 호출
- `subagent_type: "design-reviewer"`. 프롬프트에 **① 대상 범위 ② 렌더 스크린샷 경로(또는 "캡처 불가 — 코드-only") ③ 적용할 바(맥락)** 를 명시. 실패 시 `general-purpose` 재호출, 앞머리:
  > You are running as `design-reviewer`. Read the plugin's `agents/design-reviewer.md` in full and treat it as your system prompt. Follow the spec exactly.
- 캡처를 못 넘기면 에이전트가 chrome MCP 로 직접 캡처 시도하도록 허용(앱 떠 있을 때) — 단 신뢰도 명시.

## 4. 출력
- 에이전트 리포트(VERDICT + 5축 findings + 결함 원장)를 **그대로** 노출. 재가공 금지.

## 5. 컴패니언 (리뷰가 아니라 fix 단계)
- `frontend-design` — 리뷰에서 나온 결함을 **고치는 단계**에서 새 UI/토큰/프리미티브 생성용. 리뷰 중엔 쓰지 않는다(생성 ≠ 비판).
- 짝 리뷰: `review-ui`(기능적 UX·a11y — 취향과 직교). 디자인 정비는 보통 둘 다 돌린다(미적 = design-reviewer / 동작 = ux-reviewer).
