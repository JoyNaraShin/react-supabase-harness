# BOOTSTRAP — 신규 프로젝트 day-1 (정본)

새 프로젝트를 **0부터 셋업하지 않고** 이 하네스로 곧장 이슈 플로우에 진입하는 표준 절차. 하네스는 특정 템플릿 없이 동작한다 — 대상은 React + Vite + Supabase 프로젝트이고, 템플릿은 그 출발점을 빠르게 만들어 주는 선택 사항이다.

## 역할 분담

| 관심사 | 담당 |
|---|---|
| 프로젝트 골격(Vite + Supabase + biome + `docs/plans/`) | 직접 구성하거나 자신의 템플릿(선택) |
| 탑승 기록(`.harness.json`) | 직접 생성(아래 형식) 또는 템플릿의 셋업 스크립트 |
| `gh repo create`·3축 라벨·Phase 마일스톤·squash-only·push | 하네스 `/new-project` 스킬 |

> secrets 는 하네스가 만지지 않는다(`.env.local`, git-ignored). `/new-project` 는 `allowed-tools` 에 파일 쓰기가 없어 키를 영속화할 수 없다.

## Day-1 시퀀스

```
1. 프로젝트 골격 준비
   - 템플릿 사용: gh repo create <slug> --template <your-template> --private --clone
   - 기존·직접 구성: Vite + React + TS, supabase/ (supabase init), biome, docs/plans/

2. 탑승 기록 — 레포 루트에 .harness.json
   {"harness": "react-supabase-harness", "version": "<설치 버전>", "role": "project", "boarded": "<YYYY-MM-DD>"}

3. claude
   /plugin marketplace add JoyNaraShin/react-supabase-harness     # 글로벌 1회면 생략
   /plugin install react-supabase-harness@react-supabase

4. /new-project <slug> --private --phases=3
   → repo 생성 · 라벨 · 마일스톤 · squash-only · (사용자가 /commit 으로 초기 커밋) · push

5. supabase start            # 로컬 개발  (호스티드면 link 확인)
   supabase gen types --lang typescript --local > src/lib/database.types.ts

6. /phase 0 <slug>           # 도메인·IA 산출물(스토리맵·워크플로우·화면IA md 1장)
   → structure-fitness-reviewer 검증(FIT) 후에만 다음. "맞는 걸 만드는가"를 스키마 전에.
   /phase 1 <slug>           # planner plan + plan-consistency 검증 (스키마·화면)

7. /issue (Epic + Story)  →  /branch  →  구현  →  /check

8. /commit  →  /verify  →  /pr        # Closes #<Story> 자동, squash merge
```

`/plugin install` 이 글로벌로 끝나 있으면 step 3 은 사라진다.

## 🔴 탑승 검사 — 이 시퀀스는 사슬이다 (v0.18.0 신설)

**한 고리를 놓치면 나머지가 자동으로 다 빠진다.** step 1~2를 건너뛰고 템플릿 파일만 손으로 복사해도 프로젝트는 멀쩡히 돌아간다 — 타입체크 통과하고 화면이 뜬다. **미탑승 상태와 탑승 상태가 겉으로 구분되지 않는 것**이 이 시퀀스의 구조적 약점이었다.

> 실측: "하네스를 얹어서 진행"이 명시됐는데도 세션이 시퀀스의 한 단계(`supabase start`)만 밟고 스키마와 화면을 먼저 만들었다. 대가는 **`/phase 0` 을 건너뛴 자리에서 화면 IA 재작업**으로 돌아왔다.

### `.harness.json` — 탑승 마커

위 step 2 에서 만든다(템플릿을 쓰면 템플릿의 셋업 스크립트가 만들 수 있다). CLAUDE.md 산문의 "하네스로 운영한다"는 세션이 안 읽으면 없는 것과 같아서, **기계 판독 가능한 기록**을 레포에 남긴다.

| `role` | 의미 | 탑승 검사 |
|---|---|---|
| `project` (기본) | 하네스 관할 | 검사함 |
| `prototype` | 단발 데모 — 이슈·PR 플로우 대상 아님 | 침묵 |
| `template` | 템플릿 원본 (`__PROJECT_NAME__` 생존이 정상) | 침묵 |

기존 레포를 뒤늦게 탑승시킬 땐 이 파일을 직접 만들고 `/phase 0`부터 밟는다.

### `harness-boarding-guard` (SessionStart)

세션 시작에 미탑승 지문을 적출한다. 확정 지문 = 템플릿 자리표시자 `__PROJECT_NAME__` 생존(셋업 미실행) · git 저장소 없음. 약한 지문 = 마커 없음 · 실제 plan 0개.

**판단 여지가 없어 매 세션 반복한다** — `workflow-entry-guard`가 1회만 주입하는 것과 다르다(그쪽은 "한 문장 diff" 예외가 살아 있어야 하지만, 탑승은 이분법이다). 탑승하는 순간 영원히 침묵한다.

관할 범위는 좁게 잡는다. 오탐이 훅을 죽이기 때문이다 — "react 쓰면 전부"로 잡으면 단발 프로토타입 수십 개가 매 세션 울리고, 그러면 아무도 읽지 않는다. `docs/plans/`·`docs/RULES.md`·`supabase/` 중 하나라도 있는 프로젝트만 본다.

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

- **인프라 계정 소유권은 운영 주체에 둔다** — 개발자는 위임받은 키로 셋업·배포만. 소유권·보안 책임 분리.
- 다른 머신에 플러그인을 얹을 땐 `gh`·`git`·`supabase`·`python3`(훅) 필요. biome 없는 레포에선 `format-on-edit` 훅이 no-op.
- GitHub 무료 플랜의 **private repo** 는 classic branch protection 불가 → `/new-project`는 best-effort(403 경고 후 계속). main 보호는 `block-destructive-git` 훅 + `/branch`·`/pr` 규약으로 대체.
- sub-issue(`/issue --epic`)는 토큰 `repo` scope 필요 → `gh auth refresh -s repo`.
- 버전 핀: 운영 프로젝트는 marketplace head 대신 태그 핀 권장.

## 관련
- 이슈 플로우: `docs/WORKFLOW.md` · plan 계층: `docs/PLANNING.md` · 규칙: `docs/RULES.md` · 인프라: `docs/INFRA.md`
- 템플릿(선택): 저자의 `react-supabase-stack` 은 비공개다. 자신의 템플릿을 쓰면 `__PROJECT_NAME__` 자리표시자·`docs/plans/*.template.md` 규약을 맞추면 탑승 검사가 그대로 동작한다.
