---
name: prod-readiness
description: 실서비스 출시 가능성 게이트 — 코드가 아니라 "서비스로 띄울 수 있는가"를 검사. dev DB 공유·도메인·백업·에러추적·테스트·리뷰 게이트·핸드오프를 PASS/BLOCK 체크리스트로 판정. 수동 호출 전용(/prod-readiness [commercial|demo]). 배포·핸드오프 직전 사용.
disable-model-invocation: true
argument-hint: "[commercial | demo] (기본 commercial)"
allowed-tools: Read, Glob, Grep, Bash(git *), Bash(grep *), Bash(cat *)
---

`/verify`(typecheck+biome+build)는 코드가 **컴파일**되는지만 본다. 이 스킬은 그 다음 질문 —
**"이걸 실제 서비스로 띄우거나 운영 주체에 넘길 수 있는가"** — 를 게이트한다. 흐름상 `/pr`(머지) **이후, 배포·핸드오프 직전**.

> 왜: 5개 리뷰 게이트를 통과한 코드가 *공유 dev DB 위, 무료 티어, 에러추적 0, 테스트 0* 상태로 "완료"될 수 있다. done의 정의가 코드 머지에서 끝나면 프로젝트는 50%에서 멈춘다. 이 게이트가 뒷단 50%를 강제한다.

## 모드
- `commercial`(기본): 실서비스 출시. 인프라·도메인·백업 전부 필수.
- `demo`: 데모·프로토타입(비상업). 유료 인프라·커스텀 도메인 항목은 **N/A**(무료 티어 전제). 나머지(에러바운더리·테스트·리뷰·시크릿)는 동일 적용.

## 절차
각 항목을 **자동 점검(repo)** + **사용자 확인(인프라 사실)** 으로 PASS / BLOCK / N/A 판정한다. 추측 금지 — 확인 불가하면 사용자에게 묻는다. 명령 출력이 비어 보이면 원 명령을 다시 돌리거나 Read 로 교차확인한다.

### A. 데이터/DB
- [ ] **prod Supabase가 dev와 분리** (공유 dev DB 금지) — `supabase/.temp/linked-project.json`·`config.toml` 확인 + 사용자 확인. *공유 dev DB면 BLOCK.*
- [ ] 마이그레이션이 **prod에 적용**됨(dev만 아님)
- [ ] **백업/PITR** 활성 (Supabase Pro) — `commercial` 필수
- [ ] RLS 전 테이블 enable + `get_advisors`(security/performance) 통과 — supabase MCP 있으면 실행 권장

### B. 환경/시크릿
- [ ] `service_role` 키가 클라 번들에 **미노출** — `grep -rn "service_role\|SERVICE_ROLE" src/` = 0, 클라는 `VITE_*` anon만. *노출 시 BLOCK.*
- [ ] prod env가 dev와 **분리**된 값, `.env*`가 git에 미커밋(`git ls-files | grep -E '^\.env'` = 빈값 기대, `.env.example` 제외)

### C. 인프라/호스팅 (`commercial` 전용 — `demo`는 N/A)
- [ ] 상업이므로 **유료 플랜** (Vercel Pro·Supabase Pro) — 무료 티어는 비상업 전용(`INFRA.md`/pricing). *상업인데 무료 티어면 BLOCK.*
- [ ] **커스텀 도메인 + DNS** (실서비스가 `*.vercel.app` 단독이면 BLOCK)
- [ ] 트랜잭션 메일(Resend 등)·결제(Toss 등) 키가 **prod**

### D. 신뢰성/관측
- [ ] **에러 바운더리** 존재 — `grep -rn "errorElement\|ErrorBoundary\|componentDidCatch" src/` 비면 BLOCK
- [ ] **에러 추적**(Sentry 등) 연결 — `package.json` 의존성 확인. `commercial` 없으면 BLOCK, `demo` 권장
- [ ] 사용자 노출 에러가 한국어 매핑(영문 원문·스택 노출 금지) — `src/lib/errors.ts` 류

### E. 테스트/검증
- [ ] `/verify` **PASS** (typecheck+biome+build) — 미실행이면 먼저 `/verify`
- [ ] **e2e/통합 테스트** 존재 — `find . -path ./node_modules -prune -o -name '*.test.*' -o -name '*.spec.*' -print` + Playwright/vitest 설정. `commercial` 0개면 최소 핵심 플로우 1개라도 요구
- [ ] **핵심 플로우 수동 검증**(인증·결제·데이터 쓰기·실데이터 마이그레이션) — 사용자 확인

### F. 리뷰 게이트 (honor-system 강제 흡수)
- [ ] **3렌즈 전수**(`/review-structure`·`/review-stability`·`/review-craft`) Critical/Major **0** (RULES §3 — 전수 시점)
- [ ] 보안 민감 변경(auth·migrations·supabase.*·.env) 있었으면 `/review-stability` 통과 — `security-nudge` 훅 이력 참고
- [ ] **외부 `/code-review`** Critical **0** (= 실제 머지/배포 허가, WORKFLOW §최종 게이트). 미실행이면 BLOCK

### G. 핸드오프/운영 (`commercial` 전용)
- [ ] **운영 런북** (`docs/operations/` 또는 README 운영 섹션) — 배포·복구·장애 대응 절차
- [ ] 운영 인계: 계정·권한 소유권, env 인계, 지원 범위 **명시**(빈 칸 금지)
- [ ] 출시 후 지원 범위(기간·결함 정의) 합의

## 출력
```
## Prod-Readiness Report (mode: <commercial|demo>)
| 영역 | 항목 | 판정 | 근거/누락 |
... (A~G)

## Verdict
SHIP-READY  — BLOCK 0
또는
HOLD — BLOCK <n>건: <목록>. 서비스 출시 보류.
```
- **BLOCK이 하나라도 있으면 HOLD.** "거의 됐다"로 통과시키지 않는다 — 이 게이트의 존재 이유가 그 "거의"다.
- 데모면 C·G의 상업 항목을 N/A로 명시(숨기지 말 것).

## 상류 파이프라인 페이오프 캡처 (BLOCK 아님 — 학습 게이트)
`docs/plans/pipeline-eval.md` 가 존재하면(= 이 프로젝트가 상류 파이프라인으로 시작), Verdict 와 별개로 **T1 페이오프**를 완료한다 — 빌드 후에야 알 수 있는 축5의 진짜 지표(구조적 갈아엎기 발생 여부·표면화 결정의 load-bearing 비율·놓친 unknown)를 기록. prod-readiness 는 출하 전 항상 도는 게이트라 이 캡처의 신뢰 트리거다.
- T1 완료 후: (a) 상류 파이프라인이 실증됐으므로 **하네스 총점 축5 재채점을 독립 에이전트에 위임할 수 있음을 사용자에게 제안**(docs/REVIEW-PROTOCOL.md — 자기채점 금지, 일회성), (b) T1 "놓친 것"은 `/domain-research`·`/phase 0`·`structure-fitness` 스펙에 역주입(북극성 루프). 이 캡처는 **HOLD 사유가 아니다**(출하를 막지 않음).
