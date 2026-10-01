# react-supabase-harness

**Claude Code 개발 워크플로 하네스** — 설계 리뷰·코드 리뷰·검증을 Claude Code 세션 안에 강제 장치로 묶는 플러그인. 스택 기준은 React + Vite + Supabase.

> A Claude Code plugin that turns a development workflow into enforced gates: plan-first coding, adversarial review subagents with lossless synthesis, and deterministic hooks that block what rules alone could not. Built for a React + Vite + Supabase stack.

---

## 왜 만들었나

규칙 문서는 지켜지지 않았다. 같은 규칙을 세션이 서너 번 반복해서 무시하는 걸 확인한 뒤, 이 하네스는 **규칙을 텍스트가 아니라 실행되는 장치로** 옮기는 방향으로 자랐다. 아래 표의 항목은 전부 실제로 일어난 일이고, 각각이 하나의 훅·규약·스킬이 됐다.

| 일어난 일 | 바뀐 것 | 버전 |
|---|---|---|
| 리뷰어 9명의 결과를 메인 세션이 요약하다가 Critical 1건을 통째로 누락하고 1건을 강등 | 리뷰어마다 machine-readable 결함 원장을 내고, 종합은 요약이 아니라 원장 join. `scripts/synthesize.py`가 누락·강등을 기계 검증(exit 1) | v0.8.0 |
| AI가 만든 인증 스캐폴드의 RLS 정책(`with check (true)`)으로 일반 회원이 자기 권한을 admin으로 올릴 수 있었음 | 적대 리뷰가 차단, 스캐폴드 수정 | v0.14.1 |
| 그 수정을 "완료"로 선언한 뒤 독립 재검증을 돌리자 **첫 리뷰가 놓친 Critical이 하나 더** 나옴(미인증 요청으로 DB 크래시) | **수정한 쪽이 자기 수정을 통과시키지 못한다** — 미묘한 수정은 새 인스턴스가 처음부터 재판정 | v0.14.2 |
| 구현 전체를 하위 모델에 위임 → typecheck·테스트 38개 전부 통과했는데 모듈 경계가 무너짐 | `block-impl-delegation` — 코드 작성 지시가 담긴 서브에이전트 스폰을 차단 | v0.17.0 |
| 규칙 텍스트가 3~5회 무효. 룰을 추가할 때마다 훅이 하나씩 늘어남 | `gate-engine` — 룰은 데이터(`gates/rules.jsonc`), 집행자는 엔진 하나 | v0.19.0 |
| 툴 실패 5건 중 1건(권한 거부)을 보고에서 뺌. 세션의 자가 검출률 0 | `report-failures-gate` — 실패를 언급하지 않은 답변을 차단. "못 한다"던 명령이 나중에 성공하면 정정을 요구 | v0.30.0 |
| 중간 커밋 없이 미커밋 변경 41개가 쌓여 리뷰 불가능 | `commit-boundary-gate` — 20·40개 띠를 넘으면 관심사별 커밋 단위 제안 요구 | v0.30.0 |

## 설계 원칙

1. **LLM이 판정하는 게이트는 만들지 않는다.** 다른 에이전트 프레임워크에서 QA 에이전트가 깨진 코드에 성공을 보고하고, TDD 강제가 에이전트에게 우회당하는 사례를 조사한 뒤 기각했다. 판정이 확률적이면 "검증했다고 믿게" 만들어 없느니만 못하다. 모든 게이트의 술어는 파일 존재·경로·패턴·개수처럼 **항상 같은 답을 내는 것**만 쓴다. 판단이 필요한 리뷰는 게이트가 아니라 리뷰 에이전트의 몫이다.
2. **오탐이 게이트를 죽인다.** 게이트가 작업을 부당하게 세우면 다음엔 통째로 꺼진다. 그래서 파싱 실패·설정 부재는 전부 통과(fail-open)이고, 모든 룰에 탈출구가 있으며, 되돌릴 수 있는 것은 차단 대신 알림만 한다. 예외는 파괴적 git 명령 하나뿐이다(fail-closed).
3. **리뷰어는 작성자보다 약하면 안 되고, 작성자와 같은 인스턴스면 안 된다.** 리뷰 에이전트는 세션 모델을 상속하고(하드핀 금지), 비판 리뷰 요청은 메인 세션이 직접 하지 않고 독립 서브에이전트에게 위임한다([`docs/REVIEW-PROTOCOL.md`](docs/REVIEW-PROTOCOL.md)).

## 구성

### 훅 14개 — 설치 즉시 자동 실행된다

| 훅 | 이벤트 | 하는 일 | 동작 방식 |
|---|---|---|---|
| `block-destructive-git` | PreToolUse · Bash | `git commit`(직접)·`push --force`·`reset --hard`·`clean -f`·`branch -D`, 절대·홈·상위 경로의 `rm -rf` 차단. `bash -c`·`eval` 안도 재귀 검사 | **차단** · 파싱 실패 시 fail-closed. 커밋은 `/commit` 승인 경로만 통과 |
| `gate-engine` | PreToolUse · Write/Edit/Bash | `gates/rules.jsonc` + 프로젝트 `.claude/gates/rules.jsonc` 룰 집행 | **차단/알림** · 룰마다 탈출구 |
| `block-impl-delegation` | PreToolUse · Agent | 코드 작성 지시가 담긴 서브에이전트 스폰 차단(리뷰·조사 위임은 통과) | **차단** · 사용자 승인 토큰으로 예외 |
| `require-agent-output-file` | PreToolUse · Agent | 장기 에이전트는 결과 파일 경로가 있어야 스폰(세션 한도로 끊기면 보고가 유실되므로) | **차단** · Explore·verifier 면제 |
| `format-on-edit` | PostToolUse · Edit/Write | `src/` 편집 직후 biome 포맷 — 프로젝트 로컬 biome이 있을 때만 | 파일 in-place 포맷 · 네트워크 설치 안 함 |
| `edit-counter` | PostToolUse · Edit/Write | `src/` 편집 5회마다 `/check` 권고 | 알림 |
| `security-nudge` | PostToolUse · Edit/Write | 인증·마이그레이션·`supabase.*`·`.env*` 편집 시 `/review-stability` 권고 | 알림 · 같은 파일 1회 |
| `workflow-entry-guard` | PostToolUse · Write | 플랜 없이 첫 `src/` 코드를 만들면 워크플로 규정 주입 | 알림 · 프로젝트당 1회 |
| `stack-compliance-guard` | PostToolUse · Edit/Write | Tailwind·`@/*` alias·TanStack Query 미사용 신호 적출 | 알림 · 프로젝트당 1회 |
| `session-start-summary` | SessionStart | 브랜치·Phase·미커밋 3줄 요약 | 알림 · git 읽기만 |
| `harness-boarding-guard` | SessionStart | 하네스 관할 프로젝트인데 탑승 절차가 빠졌으면 적출 | 알림 |
| `review-protocol` | UserPromptSubmit | 리뷰·감사 요청을 감지하면 리뷰 프로토콜 주입 | 알림 · 일반 언급("리뷰한거야?")엔 침묵 |
| `report-failures-gate` | Stop | 이번 턴 툴 실패를 답변이 언급하지 않으면 차단. 앞서 실패한 명령이 성공했는데 정정이 없으면 차단(되돌릴 수 없는 것)/알림(그 외) | **차단/알림** · 트랜스크립트 못 읽으면 통과 |
| `commit-boundary-gate` | Stop | 미커밋 변경이 20·40개 띠를 넘으면 커밋 단위 제안 요구 | 알림 · 띠당 1회 |

상태 파일은 `<project>/.claude/state/`에만 쓴다(gitignore 권장). 하네스 밖에서 도는 감시자 하나(`hooks/_external/plugin-drift-check.sh` — 설치본이 소스보다 뒤처졌는지 검사)는 하네스를 개발하는 사람만 쓴다.

### 리뷰 에이전트 6개

| 에이전트 | 렌즈 | 모델 |
|---|---|---|
| `structure-fitness-reviewer` | 모듈 구조·의존 방향 + "맞는 걸 만드는가"(엔티티↔화면↔가치, 고아 테이블, 워크플로 폐곡선) | 세션 상속 |
| `stability-reviewer` | 인증·RLS·시크릿·입출력 + DB 정합(제약·인덱스·트랜잭션·동시성, EXPLAIN 실측) | 세션 상속 |
| `craft-reviewer` | FE 설계(리렌더·레이스·쿼리 캐시) + 기능적 UX·a11y + 시각 디자인 | 세션 상속 |
| `planner` | Phase → Epic → Story 슬라이스 플랜 작성 | 세션 상속 |
| `plan-consistency-reviewer` | 플랜 포맷·자가 모순·의존 그래프(기계 패스) | `sonnet` |
| `verifier` | typecheck + biome + build 실행·보고 | `sonnet` |

모든 리뷰어는 리포트 끝에 `| id | severity | 축 | 위치 | 한 줄 |` 결함 원장을 낸다. 복수 리뷰 종합은 `scripts/synthesize.py`로 기계 검증한다.

### 스킬 16개

이슈 플로우 `/phase` → `/issue` → `/branch` → `/commit` → `/verify` → `/pr` → `/prod-readiness`, 상류 `/domain-research`·`/phase 0`(도메인·IA 적합성 게이트), 구현 보조 `/auth-scaffold`·`/feature-scaffold`·`/db-migration`, 리뷰 `/review-structure`·`/review-stability`·`/review-craft`, 기타 `/new-project`·`/check`. 흐름 전체는 [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

### 문서

| 문서 | 내용 |
|---|---|
| [`docs/RULES.md`](docs/RULES.md) | 작업 규칙 정본 — 훅·게이트 메시지가 가리키는 조항 |
| [`docs/WORKFLOW.md`](docs/WORKFLOW.md) | 이슈 플로우 |
| [`docs/PLANNING.md`](docs/PLANNING.md) | 플랜 문서 포맷 |
| [`docs/INFRA.md`](docs/INFRA.md) | 마이그레이션 안전·Supabase 운영 규약 |
| [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md) | 신규 프로젝트 day-1 절차 |
| [`docs/REVIEW-PROTOCOL.md`](docs/REVIEW-PROTOCOL.md) | 리뷰 위임·무손실 종합·수정 검증 규약 |

## 게이트 룰 추가하기

룰 하나는 데이터 한 덩어리다. 엔진 코드는 고치지 않는다.

```jsonc
// <project>/.claude/gates/rules.jsonc — 같은 id 면 하네스 기본 룰을 덮어쓴다
{
  "rules": [
    {
      "id": "no-console",
      "on": ["Write", "Edit"],
      "when": [
        { "pathGlob": "*/src/*.tsx" },
        { "bodyRegex": { "pattern": "console\\.log", "except": "eslint-disable" } }
      ],
      "action": "warn",                       // deny | warn
      "message": "console.log 발견: {match}"
    }
  ]
}
```

술어: `toolIn` · `pathGlob` · `bodyRegex` · `pkgRef`(설치 명령·deps·import 동시 검사) · `bodySizeOver` · `globExists` · `harnessTarget` · `escapeHatch`. 각 조건에 `"not": true`로 부정. 모르는 술어가 들어간 룰은 조용히 무효화된다(엔진이 룰보다 오래 산다). 기본 룰을 끄려면 같은 id로 `"enabled": false`.

## ⚠️ 설치 전에 — 범용 플러그인이 아니다

한 스택·한 디렉터리 구조에 맞춰져 있다. 다른 구성에서는 스킬·에이전트가 없는 경로를 찾다가 실패한다.

- **pnpm** — `/verify`·`/check`가 `pnpm typecheck|check|build`를 실행한다.
- **`src/` 구조** — `src/features/<m>/{api,components,hooks,types,index}`, `src/pages/`, `src/lib/supabase.*`, `src/routes/`.
- **React 19 + Vite + TypeScript + Tailwind v4 + React Router v7 + TanStack Query + Supabase**, biome.
- **짝 템플릿** — `/new-project`는 별도 레포 [`react-supabase-stack`](https://github.com/JoyNaraShin/react-supabase-stack)에서 스캐폴드한다.
- **한국어** — 에이전트 프롬프트·스킬·문서가 한국어다.
- 의존성: `python3` 3.10+(훅), `git`, `pnpm`·`biome`(검증·포맷).

맞지 않으면 그대로 설치하지 말고 fork해서 고쳐 쓸 것. 게이트 엔진과 Stop 훅들(`report-failures-gate`·`commit-boundary-gate`)은 스택과 무관하게 동작한다.

## 설치

```
/plugin marketplace add JoyNaraShin/react-supabase-harness
/plugin install react-supabase-harness@react-supabase
```

훅을 개별로 끄려면 `hooks/hooks.json`에서 항목을 지우고 fork해서 설치한다. 룰 단위로는 탈출구 파일(`touch .claude/state/<off 이름>`)로 프로젝트별로 끈다.

하네스 자체를 수정할 때는 캐시 대신 소스를 직접 로드한다.

```bash
claude --plugin-dir /path/to/react-supabase-harness
```

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

의존성 없이 표준 라이브러리만 쓴다. 훅을 Claude Code가 부르는 그대로(stdin JSON → stdout JSON) 실행해 **차단/알림/침묵 계약**을 고정한다 — "게이트는 결정론적이다"라는 주장의 근거가 이 테스트다.

## 제거

```
/plugin uninstall react-supabase-harness@react-supabase
```

훅이 즉시 사라진다. 프로젝트별 상태 파일(`<project>/.claude/state/`)은 남으니 필요하면 직접 지운다.

## 라이선스

MIT
