# BOOTSTRAP — 신규 프로젝트 day-1 (정본)

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
   → 프로젝트의 VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY 를 .env.local 에 붙여넣기
   → (호스티드면) ./scripts/setup.sh "<Project Name>" <slug> --link=<project-ref>

3. claude
   /plugin marketplace add JoyNaraShin/react-supabase-harness     # 글로벌 1회면 생략
   /plugin install react-supabase-harness

4. /new-project <slug> --private --phases=3
   → repo 생성(degit 경로) · 라벨 · 마일스톤 · squash-only · 승인게이트 초기커밋 · push

5. supabase start            # 로컬 개발  (호스티드면 link 확인)
   pnpm gen:types            # database.types.ts 생성

6. /phase 0 <slug>           # 도메인·IA 산출물(스토리맵·워크플로우·화면IA md 1장)
   → structure-fitness-reviewer 검증(FIT) 후에만 다음. "맞는 걸 만드는가"를 스키마 전에.
   /phase 1 <slug>           # planner plan + architect 자동검증 (스키마·화면)

7. /issue (Epic + Story)  →  /branch  →  구현  →  /check

8. /commit  →  /verify  →  /pr        # Closes #<Story> 자동, squash merge
```

`/plugin install`이 글로벌로 끝나 있으면 step 3은 사라져 **정상상태 day-1 = 6 step**.

## 🔴 탑승 검사 — 이 시퀀스는 사슬이다 (v0.18.0 신설)

**한 고리를 놓치면 나머지가 자동으로 다 빠진다.** step 1~2를 건너뛰고 템플릿 파일만 손으로 복사해도 프로젝트는 멀쩡히 돌아간다 — 타입체크 통과하고 화면이 뜬다. **미탑승 상태와 탑승 상태가 겉으로 구분되지 않는 것**이 이 시퀀스의 구조적 약점이었다.

> 2026-08-07 실측: 사용자가 "하네스 얹어서 실제 서비스처럼 진행"을 명시했는데, 세션이 8단계 중 1개(`supabase start`)만 밟고 스키마와 화면 9개를 만들었다. 대가는 **`/phase 0` 건너뛴 자리에서 화면 IA 재작업 2회**로 돌아왔다.

### `.harness.json` — 탑승 마커

`setup.sh`가 생성한다. CLAUDE.md 산문의 "하네스로 운영한다"는 세션이 안 읽으면 없는 것과 같아서, **기계 판독 가능한 기록**을 레포에 남긴다.

| `role` | 의미 | 탑승 검사 |
|---|---|---|
| `project` (기본) | 하네스 관할 | 검사함 |
| `prototype` | 단발 데모 — 이슈·PR 플로우 대상 아님 | 침묵 |
| `template` | 템플릿 원본 (`__PROJECT_NAME__` 생존이 정상) | 침묵 |

기존 레포를 뒤늦게 탑승시킬 땐 이 파일을 직접 만들고 `/phase 0`부터 밟는다.

### `harness-boarding-guard` (SessionStart)

세션 시작에 미탑승 지문을 적출한다. 확정 지문 = `__PROJECT_NAME__` 생존(`setup.sh` 미실행) · git 저장소 없음. 약한 지문 = 마커 없음 · 실제 plan 0개.

**판단 여지가 없어 매 세션 반복한다** — `workflow-entry-guard`가 1회만 주입하는 것과 다르다(그쪽은 "한 문장 diff" 예외가 살아 있어야 하지만, 탑승은 이분법이다). 탑승하는 순간 영원히 침묵한다.

관할 범위는 좁게 잡는다. 오탐이 훅을 죽이기 때문이다 — "react 쓰면 전부"로 잡으면 포폴 프로토타입 16개가 매 세션 울리고, 그러면 아무도 읽지 않는다. `docs/plans/`·`docs/RULES.md`·`supabase/` 중 하나라도 있는 프로젝트만 본다.

### `plugin-drift-check.sh` — 플러그인 **밖**의 감시자

**플러그인 안의 훅은 자기 훅이 미설치라는 걸 감지할 수 없다.** 감지하려면 자기가 돌아야 하는데 안 도는 게 문제니까. 그래서 이 하나만 사용자 레벨에 산다. 정본은 `hooks/_external/plugin-drift-check.sh`.

```bash
cp hooks/_external/plugin-drift-check.sh ~/.claude/hooks/
# ~/.claude/settings.json 의 SessionStart 에 등록:
#   bash ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/plugin-drift-check.sh
```

구판은 버전만 비교해 실제 사고를 통째로 놓쳤다. 설치본에 `hooks/`가 **아예 없어** 강제 장치가 0개 가동 중이었는데 메시지는 "드리프트 v0.16.0 != v0.17.0" 한 줄이었고, 세션이 읽고 지나갔다. 게다가 처방이 무효였다 — **강제 장치를 만든 커밋이 미푸시**라 `claude plugin update`를 해도 그대로였다. 신판은 셋을 각각 본다: 설치본의 훅 실존 · 미푸시 커밋 · 버전 드리프트.

> ⚠️ **저자 함정.** 하네스를 로컬에서 개발하는 동안 하네스가 저자를 못 지킨다. 훅을 고쳤으면 **push 해야 자기한테도 적용된다.**

## 전제 / 주의

- **인프라는 운영 주체 계정으로**(Supabase/Vercel/Cloudflare/Resend) — 개발자는 키를 받아 셋업·배포만. 소유권·보안 책임 분리.
- 다른 머신에 플러그인을 얹을 땐 `gh`·`git`·`supabase`·`python3`(훅) 필요. biome 없는 레포에선 `format-on-edit` 훅이 no-op.
- **무료 private repo**는 classic branch protection 불가 → `/new-project`는 best-effort(403 경고 후 계속). main 보호는 `block-destructive-git` 훅 + `/branch`·`/pr` 규약으로 대체.
- sub-issue(`/issue --epic`)는 토큰 `repo` scope 필요 → `gh auth refresh -s repo`.
- 버전 핀: 템플릿은 harness **≥ 0.2.0**(`/new-project` 제공) 요구. 운영 프로젝트는 marketplace head 대신 태그 핀 권장.

## 관련
- 이슈 플로우: `docs/WORKFLOW.md` · plan 계층: `docs/PLANNING.md` · 규칙: `docs/RULES.md` · 인프라: `docs/INFRA.md`
- 템플릿: `react-supabase-stack`(별도 레포, template = canonical, 개선은 그쪽으로 백포트)
