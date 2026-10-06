# AGENTS.md — 이 레포의 지도

이 레포를 고치는 사람과 에이전트가 처음 읽는 문서다. 짧은 지도만 두고, 자세한 내용은 링크한 문서가 정본이다.

## 이 레포는 무엇인가

Claude Code 플러그인이다. 플러그인을 설치한 **다른 프로젝트**의 세션에 훅·에이전트·스킬을 붙인다.
이 레포 안에서 작업한다고 이 레포가 하네스의 관할이 되지는 않는다. 고치는 대상은 플러그인 자체다.

## 디렉터리

| 경로 | 무엇 | 종류 |
|---|---|---|
| `hooks/` | 훅 16개(Python·bash). `hooks.json` 이 이벤트와 매처를 등록한다. 공용 모듈: `shellparse.py`(셸 쓰기 대상 파서), `gitsnap.py`(작업 트리 스냅샷). `git/pre-push` 는 사용자가 설치하는 git 훅 | 센서 · 계산형 |
| `hooks/gate-engine.py` + `gates/rules.jsonc` | 룰은 데이터, 집행자는 엔진 하나. 새 룰은 jsonc 덩어리 하나 | 센서 · 계산형 |
| `agents/` | 리뷰어 4 · planner · verifier | 센서 · 추론형(verifier 만 계산형) |
| `skills/` | 사용자 호출 전용 스킬 16개(`disable-model-invocation: true`) | 가이드 |
| `docs/` | 규칙 정본. 훅·게이트 메시지가 여기 조항을 가리킨다 | 가이드 |
| `scripts/synthesize.py` | 복수 리뷰 원장 무손실 종합 검사 | 센서 · 계산형 |
| `scripts/doctor.py` | 참조·버전·스킬 규격·eval 구조 점검 | 센서 · 계산형 |
| `scripts/eval.sh`, `evals/` | 행동 eval — 플러그인 유무에 따른 점수 차(Δ) | 측정 |
| `tests/` | 훅 계약 테스트 · 종합 스크립트 테스트 · 뮤테이션 테스트 | 측정 |

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

## 실행

```bash
python3 -m unittest discover -s tests -v              # 훅 계약 + 종합 (빠름, PR 마다 CI)
HARNESS_MUTATION=1 python3 -m unittest tests.test_mutation   # 뮤테이션 (~2분)
python3 scripts/doctor.py                             # 드리프트·죽은 참조 (CI)
claude plugin validate --strict .                     # 매니페스트
MODEL=haiku RUNS=1 scripts/eval.sh                    # 행동 eval (비용 발생, 수동)
```

eval 은 모델 비용과 플랜 사용량을 쓴다. PR 마다 돌리지 않고 GitHub Actions `eval` 워크플로(수동 실행)나 로컬에서 돌린다.

## 하지 않는 것

- 서브에이전트는 이 레포에서도 커밋하지 않는다. 커밋은 사용자 승인을 받은 메인 세션이 한다.
- 이 레포의 `.claude/state/` 는 감사 원장 같은 로컬 작업물이다(gitignore). 공개 이력에 넣지 않는다.
- 탈출구 파일·룰 파일은 사용자가 켠다. 에이전트가 만들면 엔진이 막는다.
