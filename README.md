# react-supabase-harness

[![tests](https://github.com/JoyNaraShin/react-supabase-harness/actions/workflows/test.yml/badge.svg)](https://github.com/JoyNaraShin/react-supabase-harness/actions/workflows/test.yml)

**Claude Code 개발 워크플로 하네스** — 설계 리뷰·코드 리뷰·검증을 Claude Code 세션 안에 강제 장치로 묶는 플러그인. 스택 기준은 React + Vite + Supabase.

> A Claude Code plugin that turns a development workflow into enforced gates: plan-first coding, adversarial review subagents with lossless synthesis, and deterministic hooks that block what rules alone could not. Built for a React + Vite + Supabase stack.

---

## 왜 만들었나

규칙 문서는 지켜지지 않았다. 같은 규칙을 세션이 반복해서 무시하는 걸 확인한 뒤, 이 하네스는 **규칙을 텍스트가 아니라 실행되는 장치로** 옮기는 방향으로 자랐다. 아래 표의 항목은 실제 프로젝트에서 일어난 실패 유형이고, 각각이 하나의 훅·규약·스킬이 됐다.

| 일어난 일 | 바뀐 것 | 버전 |
|---|---|---|
| 여러 리뷰어의 결과를 메인 세션이 요약하다가 Critical 을 누락하고 일부를 강등 | 리뷰어마다 machine-readable 결함 원장을 내고, 종합은 요약이 아니라 원장 join | v0.8.0 |
| 같은 누수가 종합 단계에서 다시 남 | `scripts/synthesize.py` 가 누락·강등을 기계 검증(exit 1) | v0.12.0 |
| AI가 만든 인증 스캐폴드의 RLS 정책(`with check (true)`)으로 일반 회원이 자기 권한을 admin으로 올릴 수 있었음 | 적대 리뷰가 차단, 스캐폴드 수정 | v0.14.1 |
| 그 수정을 "완료"로 선언한 뒤 독립 재검증을 돌리자 첫 리뷰가 놓친 결함이 하나 더 나옴 — RLS 헬퍼 함수의 실행 권한이 빠져 미인증 요청이 실패하는 경로 | **수정한 쪽이 자기 수정을 통과시키지 못한다** — 미묘한 수정은 새 인스턴스가 처음부터 재판정 | v0.14.2 |
| 구현 전체를 하위 모델에 위임 → typecheck·테스트는 통과했는데 모듈 경계가 무너짐 | `block-impl-delegation` — 코드 작성 지시가 담긴 서브에이전트 스폰을 차단 | v0.17.0 |
| 규칙 텍스트가 반복해서 무효. 룰을 추가할 때마다 훅이 하나씩 늘어남 | `gate-engine` — 룰은 데이터(`gates/rules.jsonc`), 집행자는 엔진 하나 | v0.19.0 |
| 툴 실패 중 권한 거부를 보고에서 뺌. 세션이 스스로 잡지 못함 | `report-failures-gate` — 실패를 언급하지 않은 답변을 차단. "못 한다"던 명령이 나중에 성공하면 정정을 요구 | v0.30.0 |
| 중간 커밋 없이 미커밋 변경이 수십 개 쌓여 리뷰 불가능 | `commit-boundary-gate` — 20·40개 띠를 넘으면 관심사별 커밋 단위 제안 요구 | v0.30.0 |
| 공개 전 4렌즈 독립 감사(역할 수행·잔재·공식 스펙·채용 관점)에서 게이트 우회·스펙 위반이 나옴 | 우회 경로를 회귀 테스트로 고정, 훅 출력 채널·경로를 공식 스펙에 맞춤 | v0.31.0 |

## 무엇이 다른가 — 그리고 무엇은 다르지 않은가

**선행 사례와 같은 부분.** "규칙은 CLAUDE.md 보다 훅으로", "룰을 데이터로 두고 엔진 하나가 집행", "여러 리뷰 에이전트로 병렬 리뷰"는 이 하네스가 처음이 아니다. Claude Code 공식 문서의 훅 권고, 공개된 hookify·code-review 플러그인, 여러 에이전트 프레임워크가 같은 방향을 가리킨다. 이 하네스는 그 위에서 시작했다.

**이 하네스가 더한 것.**
1. **무손실 리뷰 종합** — 리뷰어가 낸 결함은 메인 세션의 요약을 거치지 않는다. 원장 행 단위로 join 하고 누락·강등을 스크립트가 검사한다(`scripts/synthesize.py`). 공개 전 감사의 258행도 이 방식으로 종합했다.
2. **구현 비위임** — 리뷰·조사는 위임하고 구현은 위임하지 않는다. 이를 훅으로 집행한다(`block-impl-delegation`).
3. **보고 무결성 게이트** — 실패를 빼고 성공만 보고하는 답변, 앞서 "못 한다"고 한 판단을 정정 없이 뒤집는 답변을 Stop 훅이 잡는다.
4. **Supabase 운영 규약** — RLS·Data API grant·함수 EXECUTE 권한·pgTAP 커버리지처럼 조용히 깨지는 지점을 규약과 스캐폴드로 묶었다([`docs/INFRA.md`](docs/INFRA.md)).

## 설계 원칙

1. **LLM이 판정하는 게이트는 만들지 않는다.** 판정이 확률적이면 "검증했다고 믿게" 만들어 없느니만 못하다. 모든 게이트의 술어는 파일 존재·경로·패턴·개수처럼 **항상 같은 답을 내는 것**만 쓴다. 판단이 필요한 리뷰는 게이트가 아니라 리뷰 에이전트의 몫이다.
2. **오탐이 게이트를 죽인다.** 게이트가 작업을 부당하게 세우면 다음엔 통째로 꺼진다. 그래서 파싱 실패·설정 부재는 통과(fail-open)이고, 모든 룰에 탈출구가 있으며, 되돌릴 수 있는 것은 차단 대신 알림만 한다. 예외는 파괴적 git 명령이다(fail-closed). 오탐과 미탐은 둘 다 회귀 테스트 코퍼스로 고정한다.
3. **리뷰어는 작성자보다 약하면 안 되고, 작성자와 같은 인스턴스면 안 된다.** 리뷰 에이전트는 세션 모델을 상속하고(하드핀 금지), 비판 리뷰 요청은 메인 세션이 직접 하지 않고 독립 서브에이전트에게 위임한다([`docs/REVIEW-PROTOCOL.md`](docs/REVIEW-PROTOCOL.md)).

## 보안 경계가 아닌 것

이 훅들은 **에이전트의 실수를 막는 협조형 가드**다. 의도적으로 우회하려는 프로세스를 막는 보안 경계가 아니다.

- **승인 표식은 자기 신고다.** `/commit` 이 붙이는 `CLAUDE_COMMIT_APPROVED=1`, 위임·파일 산출 예외 표식(`[HARNESS: …]`)은 모델이 직접 입력할 수도 있다. 이 하네스는 표식을 deny 메시지로 알려 주지 않고, 규칙으로 "사용자 승인 후에만"을 요구하는 데서 멈춘다. 강제 승인이 필요하면 Claude Code 권한 설정(`permissions.ask`)을 함께 쓴다.
- **범용 인터프리터는 보지 않는다.** `python -c`·`node -e`·스크립트 파일로 같은 일을 하면 통과한다. git 별칭(`git -c alias.x=…`)도 마찬가지다.
- **매처 밖 툴은 보지 않는다.** 다른 MCP 서버가 제공하는 파일 쓰기·셸 실행 툴에는 게이트가 붙지 않는다.
- **보고 무결성 게이트는 어휘를 본다.** 실패를 "언급했는가"를 정해진 어휘로 판정하므로, 형식적으로 한 단어만 넣어도 통과한다. 목적은 누락 방지이지 보고 품질 채점이 아니다.

## 알려진 한계

결정론을 지키느라 일부러 남긴 구멍과, 아직 측정하지 못한 것들이다.

- **`plan-first` 는 "플랜이 있는가"만 본다.** 실제 플랜이 하나라도 생기면 그 뒤로는 통과한다. 지금 쓰는 코드가 그 플랜의 범위인지는 판정하지 않는다(판정하려면 의미 해석이 필요하다). 20줄·800자 이하 편집은 통과시키므로, 편집을 잘게 쪼개면 크기 술어도 피해 갈 수 있다. 디자인·스키마 퍼스트는 `/phase` 절차와 리뷰어의 몫이지 이 게이트의 몫이 아니다.
- **판정 기록은 자기 신고다.** `/phase 0` 의 FIT, `/verify` 의 PASS, 리뷰의 Critical 0 은 대화와 문서에 남지만, 다음 단계가 그 기록을 기계로 확인하지는 않는다.
- **스택 준수 신호는 문자열 패턴이다.** `stack-compliance-guard` 는 `../../` import·컴포넌트 CSS import·`useEffect`+fetch 같은 우회 흔적을 알리기만 한다. 오탐 여지가 있어 차단하지 않는다.
- **파괴적 명령 파서의 미탐.** git 별칭, 축약된 long option, bash 가 아닌 셸 문법은 다 보지 못한다. 파싱이 실패하면 차단 쪽으로 기우므로, 따옴표가 복잡한 정상 명령이 막힐 수 있다.
- **측정하지 못한 것.** 서브에이전트의 툴 호출에도 플러그인 훅이 붙는지, `settings.json` 의 `env` 값이 훅 프로세스에 전달되는지는 이 저장소의 테스트로 확인하지 않았다.

## 구성

### 훅 14개 — 설치 즉시 자동 실행된다

| 훅 | 이벤트 | 하는 일 | 동작 방식 |
|---|---|---|---|
| `block-destructive-git` | PreToolUse · Bash | 직접 `git commit`·강제 push·`push --delete/--mirror`·`reset --hard`·`clean -f`·`branch -D`·작업 폐기 checkout/restore·이력 재작성, 시스템 최상위·홈 직속·상위(`../`)·현재 디렉터리·변수만 있는 경로의 `rm -r`·`find -delete` 차단(홈 아래 다른 프로젝트처럼 깊은 절대경로는 막지 않는다). `env`·`sudo`·`nohup` 같은 런처 접두어, 여러 줄 명령, `do`/`then` 복합문, `bash -c`·`eval`·셸이 읽는 heredoc 안도 검사 | **차단** · 파싱 실패·내부 오류 시 fail-closed. 커밋은 `/commit` 승인 경로만 통과 |
| `gate-engine` | PreToolUse · Write/Edit/NotebookEdit/Bash | `gates/rules.jsonc` + 프로젝트 `.claude/gates/rules.jsonc` 룰 집행. 에이전트가 탈출구 파일·룰 파일을 스스로 만들거나 바꾸는 것은 코드로 차단 | **차단/알림** · 룰마다 탈출구(사용자가 켠다) |
| `block-impl-delegation` | PreToolUse · Agent/SendMessage | 코드 작성 지시가 담긴 서브에이전트 스폰·재개 차단(리뷰·조사·plan 위임, 메인에게 보내는 보고는 통과) | **차단** · 사용자 승인 표식으로 예외 |
| `require-agent-output-file` | PreToolUse · Agent | 장기 에이전트는 결과를 저장할 파일 경로가 있어야 스폰(세션 한도로 끊기면 보고가 유실되므로) | **차단** · Explore·Plan·verifier·읽기 전용 리뷰어 면제 |
| `format-on-edit` | PostToolUse · Edit/Write | `src/` 편집 직후 biome 포맷 — 프로젝트 로컬 biome이 있을 때만 | 파일 in-place 포맷 · 네트워크 설치 안 함 |
| `edit-counter` | PostToolUse · Edit/Write | `src/` 편집 5회마다 `/check` 권고 | 알림 |
| `security-nudge` | PostToolUse · Edit/Write | 인증·마이그레이션·`supabase.*`·`.env*` 편집 시 `/review-stability` 권고 | 알림 · 같은 파일 1회 |
| `workflow-entry-guard` | PostToolUse · Write | 플랜 없이 첫 `src/` 코드를 만들면 워크플로 규정 주입 | 알림 · 프로젝트당 1회 |
| `stack-compliance-guard` | PostToolUse · Edit/Write | Tailwind·`@/*` alias·TanStack Query 미사용 신호 적출 | 알림 · 프로젝트당 1회 |
| `session-start-summary` | SessionStart | 브랜치·Phase·미커밋 3줄 요약 | 알림 · git 읽기만 |
| `harness-boarding-guard` | SessionStart | 하네스 관할 프로젝트인데 탑승 절차가 빠졌으면 적출 | 알림 |
| `review-protocol` | UserPromptSubmit | 리뷰·감사 요청을 감지하면 리뷰 프로토콜 주입 | 알림 · "리뷰 테이블"처럼 엔티티 이름이거나 리뷰 결과 반영 요청이면 침묵 |
| `report-failures-gate` | Stop | 이번 턴 툴 실패를 답변이 언급하지 않으면 차단. 앞서 실패한 명령이 성공했는데 정정이 없으면 차단(되돌릴 수 없는 것)/피드백(그 외) | **차단/피드백** · 트랜스크립트 못 읽으면 통과 |
| `commit-boundary-gate` | Stop | 미커밋 변경이 20·40개 띠를 넘으면 커밋 단위 제안 요구 | 모델 피드백 · 띠당 1회 |

Stop 훅의 피드백은 `additionalContext` 로 모델에게 전달된다(`systemMessage` 는 사용자 화면에만 보이므로 쓰지 않는다). 상태 파일은 `<project>/.claude/state/`(gitignore 권장)와 `~/.claude/state/commit-boundary/` 에 쓴다. 하네스 밖에서 도는 감시자 하나(`hooks/_external/plugin-drift-check.sh` — 플러그인이 꺼졌거나 설치본에 훅이 빠졌는지 검사)는 선택 사항이다.

### 리뷰 에이전트 6개

| 에이전트 | 렌즈 | 모델 · 도구 |
|---|---|---|
| `structure-fitness-reviewer` | 모듈 구조·의존 방향 + "맞는 걸 만드는가"(엔티티↔화면↔가치, 고아 테이블, 워크플로 폐곡선) | 세션 상속 · 읽기 도구만 |
| `stability-reviewer` | 인증·RLS·시크릿·입출력 + DB 정합(제약·인덱스·트랜잭션·동시성, 읽기 전용 세션의 EXPLAIN) | 세션 상속 · 읽기 도구만 |
| `craft-reviewer` | FE 설계(리렌더·레이스·쿼리 캐시) + 기능적 UX·a11y + 시각 디자인 | 세션 상속 · 읽기 도구만 |
| `planner` | Phase → Epic → Story 슬라이스 플랜 작성 | 세션 상속 |
| `plan-consistency-reviewer` | 플랜 포맷·자가 모순·의존 그래프(기계 패스) | `sonnet` |
| `verifier` | typecheck + `pnpm check`(biome 등, 구성 명령별) + build 실행·보고 | `sonnet` |

모든 리뷰어는 리포트 끝에 `| id | severity | 축 | 위치 | 한 줄 |` 결함 원장을 낸다. 복수 리뷰 종합은 `scripts/synthesize.py`로 기계 검증한다. "읽기 도구만"은 쓰기·편집 도구를 주지 않는다는 뜻이고, Bash 는 남아 있어 도구 수준에서 완전히 막히지는 않는다.

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

## 기본 정책: 외부 UI 컴포넌트 라이브러리 미사용

기본 룰 `no-ui-library` 는 shadcn·Radix·MUI·antd·Chakra·HeadlessUI 설치·import 를 차단한다. 이 하네스의 설계 의견이다 — Tailwind 토큰 + 자체 프리미티브로 만들면 번들·디자인 시스템·접근성 결정을 프로젝트가 직접 소유하고, 리뷰어도 그 기준으로 판정한다. 동의하지 않으면 프로젝트에서 끈다.

```jsonc
// <project>/.claude/gates/rules.jsonc
{ "rules": [ { "id": "no-ui-library", "enabled": false } ] }
```

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

술어: `toolIn` · `pathGlob` · `bodyRegex` · `pkgRef`(설치 명령·deps·import 동시 검사) · `bodySizeOver` · `globExists` · `harnessTarget` · `escapeHatch`. 각 조건에 `"not": true`로 부정. 모르는 술어가 들어간 룰은 조용히 무효화된다(엔진이 룰보다 오래 산다). 룰 파일은 사람이 편집한다 — 에이전트의 쓰기는 엔진이 차단한다.

## ⚠️ 설치 전에 — 범용 플러그인이 아니다

한 스택·한 디렉터리 구조에 맞춰져 있다. 다른 구성에서는 스킬·에이전트가 없는 경로를 찾다가 실패한다.

- **pnpm** — `/verify`·`/check`가 `pnpm typecheck|check|build`를 실행한다.
- **`src/` 구조** — `src/features/<m>/{api,components,hooks,types,index}`, `src/pages/`, `src/lib/supabase.*`, `src/routes/`.
- **React 19 + Vite + TypeScript + Tailwind v4 + React Router v7 + TanStack Query + Supabase**, biome.
- **템플릿은 선택 사항** — 하네스는 템플릿 없이 동작한다. 기존 프로젝트에 얹는 절차는 [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md).
- **한국어** — 에이전트 프롬프트·스킬·문서가 한국어다.
- 의존성: `python3` 3.9+(훅), `git`, `pnpm`·`biome`(검증·포맷).

맞지 않으면 그대로 설치하지 말고 fork해서 고쳐 쓸 것. 게이트 엔진과 Stop 훅들(`report-failures-gate`·`commit-boundary-gate`)은 스택과 무관하게 동작한다.

## 설치

```
/plugin marketplace add JoyNaraShin/react-supabase-harness
/plugin install react-supabase-harness@react-supabase
```

훅을 개별로 끄려면 `hooks/hooks.json`에서 항목을 지우고 fork해서 설치한다. 룰 단위로는 프로젝트의 `.claude/state/<off 이름>` 파일(사용자가 직접 만든다) 또는 프로젝트 룰 파일의 `"enabled": false` 로 끈다.

하네스 자체를 수정할 때는 캐시 대신 소스를 직접 로드한다.

```bash
claude --plugin-dir /path/to/react-supabase-harness
```

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

의존성 없이 표준 라이브러리만 쓴다. 훅을 Claude Code가 부르는 그대로(stdin JSON → stdout JSON) 실행해 **차단/알림/침묵 계약**을 고정한다. 우회 시도(미탐)와 정상 작업(오탐) 코퍼스를 함께 둔다 — "게이트는 결정론적이다"라는 주장의 근거가 이 테스트다. GitHub Actions 가 Python 3.9·3.12 에서 실행한다.

## 제거

```
/plugin uninstall react-supabase-harness@react-supabase
```

훅이 즉시 사라진다. 상태 파일(`<project>/.claude/state/`, `~/.claude/state/commit-boundary/`)은 남으니 필요하면 직접 지운다.

## 라이선스

MIT
