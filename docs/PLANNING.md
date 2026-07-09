# PLANNING — Plan 3단 계층 (정본)

`planner` 에이전트와 `/phase` 스킬이 따르는 plan 포맷. 모든 plan은 `docs/plans/`.

## 계층
- **Phase 0 (도메인·IA)** `phase-0-domain.md` — **코드·스키마 전** 적합성(fitness) 산출물. 아래 3블록을 md 1장으로. `structure-fitness-reviewer` 가 검증(FIT) 후 Phase 1 진입. 빈칸 = 템플릿 `docs/plans/*.template.md`.
- **Phase** `phase-<n>-<slug>.md` — 큰 그림·모듈 구조·화면 흐름. 슬라이스 3~7개.
- **Epic** `phase-<n>-epic-<seq>-<slug>.md` — 큰 흐름·결정·Story 분할. **~80줄 cap**. 세부는 Story 위임.
- **Story** `phase-<n>-epic-<seq>-story-<story-seq>-<slug>.md` — **1 PR 단위** 세부 acceptance + 검증 + 롤백. Single-Story Epic도 분리.
- **ad-hoc** `<slug>.md` — Phase plan 축소판.

## Phase 0 산출물 (도메인·IA — md 1장, 3블록)
**왜**: "스키마 first·디자인 first" 룰이 있어도 *국소(테이블/화면)* first 라 전체가 안 잡혀 재설계가 반복된다. Phase 0 은 *흐름·무드* first 를 코드 전에 강제한다.
1. **도메인 스토리맵** — 가로축 = 업무 흐름(예 신청→승인→처리→정산), 세로축 = 각 단계의 **엔티티 · 화면 · "답하는 질문"**. 모든 핵심 엔티티가 "만드는 화면 + 보는 화면"을 갖는지, 데이터가 가치까지 흐르는지 한눈에(고아 테이블·절단 사전 차단).
2. **핵심 워크플로우 3~5개** — 사용자가 실제 일하는 순서(진입점 → 단계 → 결과 화면). "사용자의 하루"가 어디서 시작하나(대시보드 = 할 일 큐).
3. **화면 IA** — 화면 목록 + **무드 1줄**(예 "B2B ERP = 쿨 뉴트럴, 절제·밀집"). 이게 `craft-reviewer` 디자인 렌즈의 *사전* 바가 되어 "화면 따로 놈"을 전면 정비 전에 차단.
- **상한**: md 1장. 이벤트스토밍 풀세션·DDD 컨텍스트맵 = 소규모 프로젝트엔 과설계(대상 규모에 따라 조정).
- **게이트**: `structure-fitness-reviewer` 적합성 렌즈(종단·폐곡선·이중입력·가치·중복) FIT 판정 후 Phase 1.

## Decision Register (휘발성 결정 — Slices **위에** 강제)
**왜**: 가장 뒤집힐 가능성이 높은 결정(데이터 모델 · 타입 인터페이스 · UX 분기)이 acceptance 뒤에 숨으면 조기 확정이 플랜에 박제되고, 오너는 구현 후에야 리뷰로 발견한다. 결정을 **코드 전에 반응 가능한 형태로 전면화**한다.
```markdown
## Decision Register
| # | 결정 (휘발성 높은 순) | 선택 | 검토한 대안 | 선택 사유 | 뒤집힐 조건 |
|---|---|---|---|---|---|
| D1 | <데이터 모델: 예 STI vs 분리 테이블> | | | | |
| D2 | <타입 인터페이스 경계> | | | | |
| D3 | <UX 분기: 예 인라인 편집 vs 모달> | | | | |
```
- **필수 행**: 데이터 모델 · 타입 인터페이스 · UX 분기(해당 없으면 `해당 없음` 명기 — 침묵 생략 금지).
- planner 인터뷰 게이트에서 받은 답과 `미확정 — 가정: <...>` 항목이 여기 기록된다. 사용자가 이 표에 반응한 뒤 Slices 로 내려간다.
- Phase·ad-hoc plan 에 강제. Epic 은 §3(Epic-level 결정)에 대안·뒤집힐 조건 컬럼으로 흡수.

## 슬라이스 포맷 (4블록 강제)
```markdown
## Slice S<n>: <제목>
**Acceptance**:
- [ ] <테스트 가능한 조건>
- [ ] `pnpm typecheck` PASS
- [ ] `pnpm check` PASS (biome)
- [ ] (선택) `pnpm build` PASS
- [ ] **변경 성격 매칭 렌즈 `/review-*` Critical 0 + Major 0** (1개 — RULES §3)
**파일 예상**:
- src/path/to/file.tsx
**커밋 경계**: `<type>(<scope>): <한 줄>`
**의존**: S<n-1> | none
```
- 단일 관심사 + 리뷰 가능 크기(diff ~500줄). 의존 그래프 선형·얕은 트리(깊이 ≤2).

## Phase plan 섹션
목표 / 사전 조건 / **Decision Register** / 파일 변경 요약(표) / Slices(4블록) / 검증(phase 전체 — 모든 S + 통합 + **3렌즈 전수**(/review-structure·stability·craft) Critical 0 + Major 0) / 비고.

## Epic plan 섹션 (7, ~80줄)
1 상위 Phase plan 링크 / 2 큰 흐름(3~6) / 3 Epic-level 결정(표: 항목·결정·검토한 대안·뒤집힐 조건 — 휘발성 우선) / 4 Story 분할(표: 제목·Plan·Issue) / 5 검증(Epic-level) / 6 머지 후 follow-up / 7 롤백(요약).

## Story plan 섹션 (5)
1 범위+의존(1 PR·커밋 경계·Issue#→Epic# sub-issue) / 2 Acceptance(파일별: DB / 데이터 레이어 / 컴포넌트) / 3 검증 게이트(pnpm typecheck/check/build · (DB) supabase db reset · 수동 회귀 시나리오[데이터+step+예상] · /review-structure Critical 0 + Major 0 · (선택) /review-stability) / 4 1주 self-check(예상 파일·커밋·LOC·블로커·리스크·소요) / 5 롤백(코드 git revert · DB rollback SQL · 후속 의존).

## 데이터 접근 컨벤션
- **TanStack Query + query key factory**(feature별): `{ all, lists, list(filter), detail(id) }`. mutation → `invalidateQueries`.
- 타입드 클라이언트: `createClient<Database>`(`lib/database.types.ts`, `pnpm gen:types`).
- 게시판 페이지네이션: `range()` + `count:'exact', head:true`(소규모). 대용량은 cursor.
