# AGENTS.md — 이 레포의 지도

이 레포를 고치는 사람과 에이전트가 처음 읽는 문서다. 짧은 지도만 두고, 자세한 내용은 링크한 문서가 정본이다.

## 이 레포는 무엇인가

Claude Code 플러그인이다. 플러그인을 설치한 **다른 프로젝트**의 세션에 훅·에이전트·스킬을 붙인다.
이 레포 안에서 작업한다고 이 레포가 하네스의 관할이 되지는 않는다. 고치는 대상은 플러그인 자체다.

## 디렉터리

| 경로 | 무엇 | 종류 |
|---|---|---|
| `bin/` | 실행 시점 가드(`bin/rm`·`bin/git`) — `install-rm-guard` 훅이 세션 PATH 앞에 둔다. bash 가 해석을 끝낸 실제 인자를 본다. **파괴 조작의 보장은 이 층이 진다**(정적 가드는 실행 전 안내층) | 센서 · 계산형 |
| `hooks/` | 훅 17개(Python·bash). `hooks.json` 이 이벤트와 매처를 등록한다. 공용 모듈: `shellparse.py`(셸 쓰기 대상 파서), `gitsnap.py`(작업 트리 스냅샷), `scope.py`(관할 — 훅마다 T0 전역·T1 하네스 관할만), `rmpolicy.py`(rm -r 이 지키는 대상 — 정적 가드와 `bin/rm` 공용), `gitpolicy.py`(git 파괴 조작 규칙 — 정적 가드와 `bin/git` 공용). `git/pre-push` 는 사용자가 설치하는 git 훅 | 센서 · 계산형 |
| `hooks/gate-engine.py` + `gates/rules.jsonc` | 룰은 데이터, 집행자는 엔진 하나. 새 룰은 jsonc 덩어리 하나 | 센서 · 계산형 |
| `agents/` | 리뷰어 4 · planner · verifier | 센서 · 추론형(verifier 만 계산형) |
| `skills/` | 사용자 호출 전용 스킬 16개(`disable-model-invocation: true`) | 가이드 |
| `docs/` | 규칙 정본. 훅·게이트 메시지가 여기 조항을 가리킨다 | 가이드 |
| `scripts/synthesize.py` | 복수 리뷰 원장 무손실 종합 검사 | 센서 · 계산형 |
| `scripts/doctor.py` | 참조·버전·스킬 규격·eval 구조·공개본(실재·버전·로컬 경로) 점검 | 센서 · 계산형 |
| `scripts/eval.sh`, `evals/` | 행동 eval — 플러그인 유무에 따른 점수 차(Δ) | 측정 |
| `scripts/eval-report.py` | eval 결과 축별 집계(efficacy·organic·skill·overblock), `--gate` 축별 게이트, `--publish` 단일 SHA 공개본 | 측정 |
| `evals/published/` | 공개 원자료 — 단일 커밋·clean 트리 eval 결과와 출처 meta, 보고서 | 측정 |
| `scripts/purge-snapshots.py` | 하네스 스냅샷 ref·표식 정리(기본 dry-run, 실행은 사용자) | 유지보수 |
| `scripts/ablate.py` | 훅별 ablation — 훅 하나를 뺀 사본으로 관련 케이스 재실행 | 측정 |
| `scripts/bench-snapshot.py` | 스냅샷 판정 비율·훅 지연을 두 트리(기본 HEAD vs 작업 트리)로 비교 | 측정 |
| `tests/` | 훅 계약 테스트 · 종합 스크립트 테스트 · eval 집계 테스트 · 뮤테이션 테스트 | 측정 |

가이드 = 행동 전에 방향을 준다(피드포워드). 센서 = 행동 뒤에 잡는다(피드백). 계산형 = 결정론, 추론형 = 모델 판단.

## 문서 진입점

- [`docs/RULES.md`](docs/RULES.md) — 작업 규칙 정본. 조항별 집행자는 README 「규칙과 집행자」 표
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — 이슈 플로우(/phase → /issue → /branch → /commit → /pr)
- [`docs/PLANNING.md`](docs/PLANNING.md) — plan 포맷 유일한 정본(planner 는 여기를 따른다)
- [`docs/INFRA.md`](docs/INFRA.md) — 마이그레이션·Supabase 운영 규약
- [`docs/REVIEW-PROTOCOL.md`](docs/REVIEW-PROTOCOL.md) — 리뷰 위임·무손실 종합·수정 검증
- [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md) — 신규 프로젝트 day-1
- [`CHANGELOG.md`](CHANGELOG.md) — 시점에 묶인 사실(날짜·실측)은 본문 대신 여기에 둔다

## 바꿀 때 지키는 것

1. **게이트 술어는 결정론만.** LLM 판정 게이트는 만들지 않는다. 판단은 리뷰 에이전트의 몫이다.
2. **차단 메시지는 3요소.** 무엇이 막혔나 / 왜(RULES 조항) / `다음 행동:`. 계약 테스트 `DenyMessageContractTest` 가 검사한다.
3. **오탐과 미탐은 둘 다 회귀 테스트로 고정.** 새 차단 경로에는 막혀야 할 입력과 통과해야 할 입력을 짝지어 넣는다.
4. **새 술어를 넣으면 뮤턴트도 넣는다.** `tests/test_mutation.py` 의 `MUTANTS` 에 한 줄 추가해 테스트가 그 술어를 지키는지 확인한다.
5. **복사본을 두지 않는다.** 포맷·규칙은 정본 문서 하나에만 쓰고 나머지는 링크한다(planner ↔ PLANNING.md 처럼 사본은 어긋난다).
6. **우회 토큰을 메시지에 노출하지 않는다.** 승인 표식은 자기 신고라 deny 메시지가 알려 주면 무력해진다.
7. **스킬 description 은 "무엇을 한다 + 언제 쓴다".** 따옴표 없는 `[`·`{`·백틱으로 시작하는 frontmatter 값은 doctor 가 잡는다.
8. **파괴 조작의 보장은 효과 지점에 둔다.** 정적 가드에서 표기 구멍을 찾으면 파서를 조이기 전에, 실행 시점 가드(`bin/rm`·`bin/git`)가
   그 표기를 받는지 차분 테스트로 확인한다. 받으면 정적 가드는 그대로 둔다 — 철자 목록을 늘리는 릴리스는 내지 않는다.
   규칙 자체가 틀렸으면(예: 막아야 할 조작이 정책에 없음) 정책 모듈(`rmpolicy`·`gitpolicy`)을 고쳐 두 층에 함께 반영한다.

## 실행

```bash
python3 -m unittest discover -s tests -v              # 훅 계약 + 종합 (빠름, PR 마다 CI)
HARNESS_MUTATION=1 python3 -m unittest tests.test_mutation   # 뮤테이션 (뮤턴트마다 test_hooks — 30분 안팎)
python3 scripts/doctor.py                             # 드리프트·죽은 참조 (CI)
claude plugin validate --strict .                     # 매니페스트
MODEL=haiku RUNS=3 scripts/eval.sh                    # 행동 eval (사용량 소모, 수동)
python3 scripts/eval-report.py evals/results/*.json   # 축별 집계 (--gate 게이트, --publish 공개본)
```

eval 은 모델 비용과 플랜 사용량을 쓴다. PR 마다 돌리지 않고 GitHub Actions `eval` 워크플로(수동 실행)나 로컬에서 돌린다.

## 릴리스 — 보안 리뷰는 push 전에

push 뒤 자동 보안 리뷰가 구멍을 찾은 릴리스가 다섯이다(0.35.1·0.35.2·0.35.3·0.36.0·0.40.0). 그중 0.40.0 의 셸 해석 지적 3건은
요약만 남아 끝내 재현하지 못했다. 그래서 순서를 바꿨다.

1. **셸 해석·게이트 술어·rm 판정을 바꾼 릴리스는 push 전에 독립 적대 검증을 한 번 돈다.** 읽기 전용 에이전트가
   기록 스텁 PATH 로 실제 bash 와 대조해 **재현한 것만** 원장 파일로 낸다. 커밋 diff 에는 `/security-review` 도 돌린다.
   재현된 지적이 남아 있으면 push 하지 않는다. 검증 에이전트가 Claude Code 안전 분류기에 막히면 표현을 바꿔 다시
   띄우지 않는다(우회) — 막혔다고 기록하고, 아래 결정론 검사와 `/security-review` 로 갈음한다.
2. **차분·예외 퍼즈는 새 시드로 돈다**(rm 차분·git 차분·예외 퍼즈 — `tests/test_shell_differential.py`). `HARNESS_FUZZ_SEED=<새 값> HARNESS_FUZZ_N=5000` 을 시드 다섯 개 이상. 이미 통과한
   시드만 다시 돌리면 아무것도 새로 검사하지 않는다.
3. **push 뒤 리뷰 알림은 "미확인"으로 닫지 않는다.** 요약만 와도 그 범주(파일·fail-open·파서 차이)로 재발견을
   시도한다 — 표적 퍼즈와 독립 검증. 재현되면 수정 릴리스를 낸다. 재현하지 못하면 README 「알려진 한계」에 범주와 날짜를 적는다.

## 하지 않는 것

- 서브에이전트는 이 레포에서도 커밋하지 않는다. 커밋은 사용자 승인을 받은 메인 세션이 한다.
- 이 레포의 `.claude/state/` 는 감사 원장 같은 로컬 작업물이다(gitignore). 공개 이력에 넣지 않는다.
- 탈출구 파일·룰 파일은 사용자가 켠다. 에이전트가 만들면 엔진이 막는다.
