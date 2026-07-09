# react-supabase-harness

1인 풀스택 개발을 위한 **Claude Code 플러그인 하네스**. 이슈 플로우 스킬, 리뷰 서브에이전트, 안전·자동화 훅을 한 번에 설치해 모든 프로젝트를 동일한 워크플로우로 운영한다. 스택 기준: **React + Vite + Supabase**.

> A Claude Code plugin bundling an issue-flow (plan → issue → branch → commit → verify → PR → prod-readiness), adversarial review subagents, and safety/automation hooks for a **React + Vite + Supabase** workflow.

## ⚠️ Hard assumptions — read before installing

This is **not a general-purpose plugin.** It is deeply coupled to one stack and layout. On anything else, the skills/agents will fail or grep paths that don't exist:

- **pnpm** — `verify`/`check` run `pnpm typecheck|check|build` with no package-manager fallback.
- **Exact `src/` layout** — `src/features/<m>/{api,components,hooks,types,index}`, `src/pages/`, `src/lib/supabase.*`, `src/lib/storage.*`, `src/routes/`.
- **React 19 + Vite + TypeScript + Tailwind v4 + React Router v7 + TanStack Query + Supabase**, biome for format/lint.
- **Companion template** — `/new-project` scaffolds from the separate [`react-supabase-stack`](https://github.com/JoyNaraShin/react-supabase-stack) repo.
- **Project `CLAUDE.md`** — review agents read the consuming project's `CLAUDE.md` + `docs/RULES.md` as conventions. Author one (the template ships a starter), or they degrade to `docs/RULES.md` only.
- **Korean** — agent prompts, skills, and docs are Korean.

If your project doesn't match, fork and adapt rather than installing as-is.

## 구성

| 컴포넌트 | |
|---|---|
| `hooks/` — 안전·자동화 훅 5종 (아래 표) | ✅ |
| `skills/` — new-project / domain-research / issue / branch / commit / pr / phase (0=도메인·IA) / check / verify / review-* (structure·stability·craft) / db-migration / feature-scaffold / auth-scaffold / prod-readiness (16) | ✅ |
| `agents/` — planner / structure-fitness-reviewer / stability-reviewer / craft-reviewer (세션 모델 상속) · plan-consistency-reviewer / verifier (sonnet 하드핀 — 기계 패스만, RULES §11) — 6 | ✅ |
| `docs/` — RULES / PLANNING / WORKFLOW / INFRA / BOOTSTRAP | ✅ |

신규 프로젝트는 보일러플레이트 `react-supabase-stack`(별도 레포)에서 스캐폴드 → `/new-project`로 부트스트랩. 전체 day-1 절차는 [`docs/BOOTSTRAP.md`](docs/BOOTSTRAP.md). 작업 흐름은 [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

## 훅 — what each does to your machine

설치 즉시 자동 실행된다. **무엇을 건드리는지 알고 깔 것:**

| 훅 | 이벤트 / matcher | 역할 | 네트워크·파일 변경 |
|---|---|---|---|
| `block-destructive-git` | PreToolUse / Bash | `git commit`·`push --force`·`reset --hard`·`rm -rf` 등 파괴적 명령 **차단**(`CLAUDE_COMMIT_APPROVED=1` = `/commit`만 우회). 파싱 실패 시 fail-closed | git 명령 차단(파일 변경 없음) |
| `format-on-edit` | PostToolUse / Edit·Write | 편집 직후 biome 포맷(`src/` 대상). **프로젝트-로컬 `node_modules/.bin/biome` 있을 때만** — 없으면 skip(네트워크 설치 안 함) | 편집 파일 in-place 포맷 |
| `security-nudge` | PostToolUse / Edit·Write | auth·migrations·supabase.*·storage.*·`.env*` 편집 시 `/review-stability` 권고 주입 | 없음(텍스트 주입) |
| `edit-counter` | PostToolUse / Edit·Write | `src/` 편집 5회 누적 시 `/check` 권고 | 없음 |
| `session-start-summary` | SessionStart | 브랜치·Phase·미커밋 3줄 요약 주입 | 없음(git 읽기만) |

- 모든 PostToolUse·SessionStart 훅은 **fail-open**(예외 시 exit 0) — 세션을 막지 않는다. PreToolUse(`block-destructive-git`)만 파괴적 git에 **fail-closed**.
- 개별 훅 비활성화: `hooks/hooks.json`에서 해당 항목 제거 후 재설치, 또는 설치 전 fork.
- 의존성: `python3`(훅 실행), `pnpm`·`biome`(verify·format). 없으면 해당 훅/스킬은 skip 또는 실패한다.

## 설치 / Install

```
/plugin marketplace add JoyNaraShin/react-supabase-harness
/plugin install react-supabase-harness@react-supabase
```

로컬 dogfood:
```bash
claude --plugin-dir /path/to/react-supabase-harness   # 로드
/reload-plugins                                                # 변경 후 재로드
```
> 플러그인은 **버전 고정 캐시**로 구동된다 — repo 수정·푸시 후 `/plugin`으로 재설치해야 반영된다.

## 제거 / Uninstall

```
/plugin uninstall react-supabase-harness@react-supabase
```
훅(git 차단·포맷)이 즉시 사라진다. 설치 중 생성된 프로젝트별 상태 파일은 `<project>/.claude/state/`에 남으니 원하면 수동 삭제.

## 라이선스
MIT
