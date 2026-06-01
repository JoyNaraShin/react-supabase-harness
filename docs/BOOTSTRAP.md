# BOOTSTRAP — 신규 클라이언트 프로젝트 day-1 (정본)

신규 계약을 받아 **0부터 셋업하지 않고** 보일러플레이트(`react-supabase-stack`) + 이 하네스로 곧장 이슈 플로우에 진입하는 표준 절차. `/new-project` 스킬이 GitHub 측을, 템플릿 `scripts/setup.sh`가 로컬 측을 담당한다.

## 역할 분담

| 관심사 | 담당 |
|---|---|
| 파일 치환(`__PROJECT_NAME__`)·식별자(name/project_id)·`pnpm install`·`.env.local`·`supabase link` | 템플릿 `scripts/setup.sh` (로컬·결정적, secrets 미트랜스크립트) |
| `gh repo create`·3축 라벨·Phase 마일스톤·squash-only·승인게이트 초기커밋·push·(옵션)plan seed | 하네스 `/new-project` 스킬 |

> secrets는 `setup.sh`만 만진다(`.env.local`, git-ignored). `/new-project`는 `allowed-tools`에 파일쓰기가 없어 키를 영속화할 수 없다.

## Day-1 시퀀스 (8 step)

```
1. gh repo create <slug> --template JoyNaraShin/react-supabase-stack --private --clone
   (또는)  npx degit JoyNaraShin/react-supabase-stack <slug> && cd <slug> && git init

2. cd <slug> && ./scripts/setup.sh "<Project Name>" <slug>
   → 치환 · pnpm install · .env.local 생성
   → 클라이언트가 준 VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY 를 .env.local 에 붙여넣기
   → (호스티드면) ./scripts/setup.sh "<Project Name>" <slug> --link=<project-ref>

3. claude
   /plugin marketplace add JoyNaraShin/nara-stack-harness     # 글로벌 1회면 생략
   /plugin install nara-stack-harness

4. /new-project <slug> --private --phases=3
   → repo 생성(degit 경로) · 라벨 · 마일스톤 · squash-only · 승인게이트 초기커밋 · push

5. supabase start            # 로컬 개발  (호스티드면 link 확인)
   pnpm gen:types            # database.types.ts 생성

6. /phase 1 <slug>           # planner plan + architect 자동검증

7. /issue (Epic + Story)  →  /branch  →  구현  →  /check

8. /commit  →  /verify  →  /pr        # Closes #<Story> 자동, squash merge
```

`/plugin install`이 글로벌로 끝나 있으면 step 3은 사라져 **정상상태 day-1 = 6 step**.

## 전제 / 주의

- **클라이언트 인프라 자가가입**(Supabase/Vercel/Cloudflare/Resend) — 본인은 키를 받아 셋업·배포만. 운영비 0, 보안 책임 분리.
- 클라이언트 머신에 플러그인을 얹을 땐 `gh`·`git`·`supabase`·`python3`(훅) 필요. biome 없는 레포에선 `format-on-edit` 훅이 no-op.
- **무료 private repo**는 classic branch protection 불가 → `/new-project`는 best-effort(403 경고 후 계속). main 보호는 `block-destructive-git` 훅 + `/branch`·`/pr` 규약으로 대체.
- sub-issue(`/issue --epic`)는 토큰 `repo` scope 필요 → `gh auth refresh -s repo`.
- 버전 핀: 템플릿은 harness **≥ 0.2.0**(`/new-project` 제공) 요구. 클라이언트는 marketplace head 대신 태그 핀 권장.

## 관련
- 이슈 플로우: `docs/WORKFLOW.md` · plan 계층: `docs/PLANNING.md` · 규칙: `docs/RULES.md` · 인프라: `docs/INFRA.md`
- 템플릿: `react-supabase-stack`(별도 레포, template = canonical, 개선은 그쪽으로 백포트)
