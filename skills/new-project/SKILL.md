---
name: new-project
description: 신규 프로젝트 day-1 부트스트랩 — gh repo create + 3축 라벨 + Phase 마일스톤 + squash-merge 강제 + 초기커밋(/commit 위임). 전부 멱등.
disable-model-invocation: true
allowed-tools: Bash(gh *), Bash(git *), Bash(jq *)
argument-hint: <repo-slug> [--owner=<login>] [--private] [--phases=N] [--no-repo]
---

하네스 스택 구조(README「설치 전에」)로 스캐폴드한 프로젝트의 **GitHub 측 셋업**을 표준화한다. 파일 치환·`pnpm install`·`.env.local`·`supabase link`는 스캐폴드 단계(템플릿을 쓰면 그 setup 스크립트) 담당이고 이 스킬은 안 한다. 이 스킬은 **gh 오케스트레이션 + `/commit` 승인 게이트 재사용**만. `allowed-tools`에 Write·`sed` 없음 → 템플릿 파일·secrets를 건드릴 수 없다.

## 1. 인자 파싱
- `$ARGUMENTS[0]` = **repo-slug**(필수, kebab-case)
- `--owner=<login>` = repo owner(미지정 시 `gh api user --jq .login`)
- `--private` = private repo로 생성(기본). 공개 의도면 명시적으로 빼고 확인.
- `--phases=N` = Phase 마일스톤 0..N 생성(기본 3)
- `--no-repo` = repo 생성 건너뜀(이미 원격 있음)

## 2. Preflight (read-only — 변경 없음)
1. `gh auth status` — 미인증이면 중단(`gh auth login` 안내).
2. **scope 확인**: 토큰에 `repo` 없으면 경고 — `gh auth refresh -s repo`(sub-issue GraphQL·private repo 위해 필요).
3. `git rev-parse --show-toplevel` — repo 루트 확인(아니면 중단).
4. `git remote get-url origin 2>/dev/null` · `gh repo view --json nameWithOwner 2>/dev/null` — repo/원격 **존재 감지**(있으면 생성 단계 skip).
5. owner 확정. `.github/labels.yml` 존재 확인(없으면 경고 — 템플릿 기반인지 확인).

## 3. 단계 (전부 멱등 — 재실행 안전)
1. **repo 생성**(origin 없고 `--no-repo` 아닐 때): `gh repo create <owner>/<slug> --private --source=. --remote=origin --push=false`. 이미 있으면 "exists, skip".
2. **라벨 upsert** — `.github/labels.yml` 순회:
   ```bash
   gh label create "<name>" --color "<color>" --force   # --force = create-or-update
   ```
3. **Phase 마일스톤** — create-if-absent:
   ```bash
   existing=$(gh api "repos/$OWNER/$REPO/milestones" --jq '.[].title')
   for i in $(seq 0 "$PHASES"); do
     grep -qx "Phase $i" <<<"$existing" || gh api "repos/$OWNER/$REPO/milestones" -f title="Phase $i" >/dev/null
   done
   ```
4. **squash-merge 강제**:
   ```bash
   gh repo edit "$OWNER/$REPO" --enable-squash-merge \
     --enable-merge-commit=false --enable-rebase-merge=false --delete-branch-on-merge
   ```
5. **브랜치 보호**(best-effort): `gh api -X PUT repos/$OWNER/$REPO/branches/main/protection ...` 시도. **403이면 경고만 하고 계속**(무료 private는 classic protection 불가 — `block-destructive-git` 훅 + `/branch`·`/pr` 규약이 대체).
6. **초기 커밋 — 사용자가 `/commit` 을 실행하도록 안내하고 여기서 멈춘다.** `/commit` 은
   `disable-model-invocation: true` 라 모델이 대신 부를 수 없고, 이 스킬은 `CLAUDE_COMMIT_APPROVED=1` 을
   **직접 emit하지 않는다**(prefix 단일-issuer = `/commit`).
   - 안내 문구: 스테이징 후보(`.env*` 제외)와 메시지 초안(`chore: bootstrap <slug>`)을 보여 주고 "`/commit` 을 실행해 주세요".
   - 사용자가 커밋을 마친 뒤 이어서 `git push -u origin main`.
   - 이미 커밋/푸시돼 있으면 skip.
7. **초기 plan은 별도** — 이 스킬은 GitHub 셋업 전용. 부트스트랩 후 `/phase 0 <slug>`(도메인·IA 게이트)부터 밟는다 — `/phase 1` 로 바로 가면 structure-fitness 의 FIT 판정이 통째로 빠진다(책임 분리 — 이 스킬은 파일·서브에이전트를 다루지 않음).

## 4. 출력
`✓ <owner>/<slug> — repo <created|exists> · N labels · M milestones · squash-only · push <done|skip>` + 다음 힌트 `→ supabase start && pnpm gen:types → /phase 0 <slug>`.

## 5. 금지
- `.env*`·secrets 스테이징(초기 커밋에서 명시 제외) · `git add .`/`-A` · `CLAUDE_COMMIT_APPROVED=1` 을 `/commit` 외 맥락에서 재사용 · 사용자 승인 없는 커밋·push · branch protection 실패 시 전체 중단(경고 후 계속) · 템플릿 파일 치환(=setup.sh 영역) · `--owner` 가 본인 외인데 권한 미확인 상태로 생성.

## 6. 후속
`scripts/setup.sh`(치환·env) → 이 스킬(`/new-project`) → `/phase` → `/issue` → `/branch` → `/commit` → `/verify` → `/pr`. 전체 시퀀스는 `${CLAUDE_PLUGIN_ROOT}/docs/BOOTSTRAP.md`.
