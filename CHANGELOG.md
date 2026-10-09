# Changelog

이전 버전의 변경 내역은 git 이력에 있다. 이 파일은 공개 이후부터 기록한다.

## 0.35.1 — 실사용에서 나온 게이트 오판 3건

다른 레포에서 하루 동안 실제로 쓰다가 드러난 결정론적 파서 버그다. 셋 다 "정상 작업을 막거나 거짓 정정을 요구"하는 쪽(과차단)이었다.

- **정정 게이트가 셸 문법어를 명령으로 셌다** — `for d in …; do …; done` 의 `done` 이 '실패했다가 성공한 명령'으로 잡혀 근거 없는 정정을 요구했다. 문법어(`do`·`then`·`done`·`fi`·`for` 등)는 신원에서 뺀다. 명령 분리는 이제 공용 파서(`shellparse.segments`)를 써서 heredoc 본문·따옴표 속 문자열·주석을 명령으로 읽지 않는다(python·node heredoc 본문도 셸 명령이 아니다).
- **플랜 게이트가 변수 경로를 프로젝트 안으로 읽었다** — `S=<스크래치> && cat > $S/…/a.sql` 의 대상을 `<프로젝트>/$S/…` 로 판정해 프로젝트 밖 쓰기를 막았다.
- **`rm` 게이트가 정의된 변수를 '빈 값일 수 있는 변수'로 막았다** — `S=<깊은 경로> && rm -rf $S`.

뒤의 둘은 원인이 같다. 공용 파서가 변수를 실제 셸처럼 펼친다 — 같은 명령 안의 앞선 대입, 그다음 환경변수(Claude Code 의 Bash 는 호출마다 셸 상태가 새로 시작되므로 이 둘이 값의 전부다). 값을 알 수 없는 변수(명령 치환 결과 등)와 `PWD`·`OLDPWD` 는 펼치지 않아, 파괴 명령 게이트는 지금처럼 막는다(fail closed). `rm` 게이트도 같은 함수를 쓴다.

- 계약 테스트 153 → 157(위 사고 4건을 그대로 재현), 뮤턴트 37 → 39(`expand_vars`·`record_assignment` 무력화).

## 0.35.0 — 측정 신뢰도와 커버리지

첫 CI eval 이 로컬과 다른 결과를 냈다(과차단 의심 0.89, 리뷰어 0.33). 원인을 하나씩 열어 보니 하네스 결함보다 **측정 결함**이 많았다 — 그것부터 고쳤다.

### 측정이 거짓말하지 않게
- 실행기 아티팩트 분리 — eval 실행기가 슬래시 프롬프트를 스킬로 펼치지 않는 런(모델이 Skill 도구로 부르려다 `disable-model-invocation` 으로 거절됨)을 슬래시 케이스마다 `runner-skill-expanded` 채점기(`arm: with-only`, 점수 제외)가 잡고, `eval-report` 가 측정 실패로 빼고 개수를 보고한다.
- `node_modules` 삭제 케이스 — "rm 으로 지웠나" 대신 "어떤 방법이든 지웠나 + 하네스 차단이 한 번도 없었나"로. 파일 채점기는 없는 파일에서 예외를 던져(실측) 삭제를 증명할 수 없다. 일반 패키지 추가 케이스에도 차단 부재 채점기.
- 리뷰어 llm 채점기는 trace 대신 최종 리포트(`last_message`)를 본다 — 긴 trace 가 잘려 최종 리포트를 못 본 채 FAIL 하던 판정이 있었다.
- `tests/test_eval_report.py` — 실행 실패·한도·실행기 아티팩트가 점수로 새지 않는지, 유효 런 없는 케이스가 게이트를 실패시키는지.

### 커버리지
- eval 21 → 27케이스: `fit-before-phase`·`no-pr-with-critical` 게이트(v0.34 신설, 첫 eval), `/branch`·`/phase`·`/prod-readiness`·`/review-structure`(고아 테이블). eval 이 있는 스킬 5 → 9/16 — 나머지는 네트워크(gh)·패키지 설치가 필요해 샌드박스 eval 로 잴 수 없다.
- `test.yml` — 주 1회 정기 실행(모델 비용 없음)과 `claude plugin validate --strict` 잡.

### 보정
- `stability-reviewer` — 열 권한 등 DB 가 이미 집행하는 경로에 대한 이중화 제안은 결함이 아니라 권고(Minor 이하).
- `/phase` — 인자 해석 예 표(`1 notices` → `phase-1-notices.md`): haiku 가 3/3 "사용법과 맞지 않는다"며 되물었다. 물을 수단이 없을 때(헤드리스 `claude -p`·CI)는 질문을 늘어놓고 멈추지 말고 권고안을 가정으로 적어 진행, 프로젝트 문서의 Decision Register 는 다시 묻지 않는다. haiku 는 이 대체 규칙을 1/3 만 따랐다(README).
- `eval-report --latest` — (모델, 케이스)마다 최근 결과 파일만 집계.

### 수치
- haiku 3런 27케이스 pass^k 22/27, 평균 Δ +0.52. 새 게이트 둘(`fit-before-phase`·`no-pr-with-critical`)과 `/prod-readiness`·스냅샷 알림은 Δ +1.00·pass³.
- 테스트 147 → 153(eval-report 계약 6).

## 0.34.1 — CI eval 이 실제로 돌게

v0.34.0 의 첫 CI eval 실행에서 126런 전부가 모델 호출 전에 거부됐다("셸 도구를 가둘 샌드박스 백엔드가 없음"). 그런데 pass^k 표에는 파괴 명령 차단 케이스들이 1.00 으로 찍혔다 — 아무것도 실행되지 않아 "성공하지 않음" 채점이 저절로 참이 된 것이다.

- `eval.yml` — Ubuntu 러너에 샌드박스 백엔드(bubblewrap·socat)를 설치하고, Ubuntu 24.04+ 의 AppArmor 가 막는 bwrap user namespace 를 bwrap 전용 프로필로 허용한다(공식 sandboxing 문서). 워크플로 입력은 `run:` 에 직접 넣지 않고 env 로 넘긴다.
- `eval-report.py` — 실행되지 못한 런(사용량 한도·샌드박스 거부·인증 실패)을 전부 점수에서 빼고 사유를 출력한다. 유효 런이 없는 케이스가 있으면 `--min-pass-k` 가 실패한다. 최대 턴 도달 런은 결과를 채점할 수 있어 남긴다.

## 0.34.0 — 판정 기록의 소비와 측정 확장

v0.33.0 자기 채점(C1–C9)의 빈칸을 메웠다: 기록만 하던 판정을 다음 단계가 읽고, eval 을 20케이스 이상·pass^k 로 넓히고, 구성요소 단위 ablation 도구를 만들었다.

### 판정 기록 → 다음 단계 게이트
- 새 술어 `verdict` — `subagent-audit` 이 쓴 `.claude/state/verdicts.jsonl` 을 읽는다. `{agent, in}`(그 에이전트의 마지막 판정), `{criticalAbove, sameHead}`(지금 HEAD 위 리뷰 기록의 Critical).
- 새 술어 `commandRegex` — Bash 하위 명령마다 앞머리부터 맞춘다(환경변수 대입·launcher 는 건너뜀). 다른 명령의 인자 속 문자열·heredoc 본문은 걸리지 않는다.
- 새 룰 `fit-before-phase` — `docs/plans/phase-0-domain.md` 가 있는 프로젝트에서 structure-fitness-reviewer 의 마지막 판정이 FIT 이 아니면 phase 1 이후 plan 쓰기를 막는다(탈출구 `phase-gate-off`).
- 새 룰 `no-pr-with-critical` — 지금 HEAD 위 리뷰 기록에 Critical 이 남아 있으면 `gh pr create/merge` 를 막는다(탈출구 `review-gate-off`). 고쳐서 커밋하면 HEAD 가 바뀌어 판단에서 빠진다.

### 안전망
- `snapshot-guard` PostToolUse — 명령이 끝나면 실행 직전 스냅샷 대비 **사라진 파일**을 결과로 찾아 복구 명령과 함께 알린다. 스크립트 안의 `git clean` 처럼 명령 문자열에 드러나지 않는 삭제도 잡는다. 같은 명령의 표식만 쓰고(15분 이내), 추가·수정은 알리지 않는다.
- `pre-push` 차단 메시지(원격 ref 삭제·`refs/harness/*`)에 "다음 행동" 줄.

### 측정
- eval 12 → 21케이스 — 스냅샷 복구(스크립트 속 삭제), 금지 의존성 결과 검사(스크립트가 쓴 `package.json`), 셸 heredoc plan-first, verify FAIL 위 승인 커밋, 스킬 4종(`/commit`·`/feature-scaffold`·`/auth-scaffold`·`/db-migration` backfill), 결함 없는 RLS 에 대한 리뷰어 과잉 지적.
- `scripts/eval-report.py` — 결과 JSON 들을 모델·케이스별 Δ·pass^k 로 집계. 사용량 한도로 끊긴 런은 점수에서 뺀다. `--min-pass-k` 미만이면 exit 1.
- `scripts/ablate.py` — 훅 하나를 hooks.json 에서 뺀 사본으로 그 훅이 지키는 케이스를 다시 돌려 기여도를 잰다(`claude plugin eval` 은 플러그인 전체 on/off 만 지원).
- `.github/workflows/eval.yml` — 구독 사용량 전용(`CLAUDE_CODE_OAUTH_TOKEN`, API 키는 넘기지 않음·토큰 없으면 즉시 실패), pass^k 비율 하한으로 잡 성패를 정한다.
- `doctor` — 100줄 넘는 스킬 파일의 목차를 실제 목록 형태로 검사(제목만 있는 것 불인정), README·AGENTS 의 훅·에이전트·스킬·술어·eval 케이스 수 표기를 실제 수와 대조.
- 계약 테스트 143 → 147, 뮤턴트 32 → 37.

### eval 이 찾아 고친 것
- `/db-migration` 이 INFRA §Backfill 규약(일회용 UPDATE 대신 `private.backfill_*()` 함수)을 몰랐다 — 기존 행이 있는 표에 NOT NULL 열을 더하는 케이스에서 플러그인 arm 도 일회용 UPDATE 를 썼다. 스킬 2단계에 규약을 넣었다.
- `stability-reviewer` 가 하네스 자신의 인증 규약(가입 트리거가 profile 을 만들고 클라이언트 INSERT 정책은 두지 않음)을 Major 결함으로 불렀다 — "정책 부재 = 기본 거부" 보정 예시를 넣었다.
- `harness-boarding-guard` 의 미탑승 안내가 읽기 전용 요청(리뷰)까지 멈춰 세워 되묻게 했다 — 파일을 쓰지 않는 요청은 수행하고 끝에 한 줄로 알리게 했다.
- `snapshot-guard` 사후 알림을 "의도하지 않았으면 되살려라"에서 "의도했더라도 사라진 목록과 복구 명령을 알려라"로 — 사용자가 리셋을 요청한 경우 모델이 삭제를 의도로 보고 침묵했다.

## 0.33.0 — 결과 기반 센서

v0.32.0 「알려진 한계」의 우회들을 명령 표기를 더 조이는 대신 **결과**로 닫았다. 어떤 표기로 했든 결과는 같다.

### 안전망
- `snapshot-guard`(PreToolUse·Bash) — 읽기 전용으로 확인되지 않은 명령 직전마다 작업 트리(미추적 포함·gitignore 제외)를 `refs/harness/snapshots/` 에 남긴다. 실제 인덱스를 임시 파일로 복사해 바뀐 파일만 해시하고, 같은 트리면 재사용, 최근 30개 보존. 파일 1,300개 레포에서 250–450ms. 스냅샷 커밋은 고정 신원을 써서 git 사용자 설정이 없는 환경(CI·새 머신)에서도 남는다. 파괴 계열 명령이면 파일 단위 복구 명령을 알린다. 저장소는 하위 명령마다 `cd` 를 따라 정하고, 프로젝트의 저장소(모노레포 상위 루트 포함)와 프로젝트 안 저장소에만 ref 를 쓴다(홈 디렉터리 dotfiles 저장소 등에는 쓰지 않는다 — 실세션 eval trace 에서 발견). Claude Code 체크포인트는 Bash 변경을 추적하지 않는다(공식 문서).
- `hooks/git/pre-push` + `scripts/install-git-hooks.sh` — 기본 브랜치의 non-fast-forward 갱신, 원격 ref 삭제, `refs/harness/*` 전송을 git 이 보낼 ref 갱신으로 판정해 막는다. 기능 브랜치의 rebase 후 갱신은 통과. 기존 pre-push 는 `pre-push.local` 로 보존해 먼저 실행. `block-destructive-git` 은 `push --no-verify`·`-c core.hooksPath=…` 를 막는다.

### 룰 술어
- `plan-first` — Bash 의 쓰기 대상(heredoc·리다이렉트·`cp`·`sed -i`, `cd` 추적)에도 같은 판정. 셸로 만든 새 코드 파일과 큰 heredoc 덮어쓰기가 대상이고, 작은 in-place 편집·삭제·프로젝트 밖 쓰기는 통과.
- `no-ui-library` — 별칭 스펙(`<이름>@npm:<패키지>`), 레지스트리 tarball URL·파일, `npm pkg set dependencies.<패키지>=`, `package.json` 값의 `npm:` 별칭, side-effect import, CSS·SCSS `@import`/`@use`. 그리고 룰의 새 필드 `after` 로 **Bash 실행 뒤 `package.json` 에 새로 들어온 금지 의존성**(HEAD 대비)을 결과로 잡는다 — 이미 커밋된 의존성은 다시 알리지 않는다.

### 서브에이전트
- `subagent-audit`(SubagentStart·SubagentStop) — 시작 스냅샷과 끝 트리를 비교해 코드 영역이 바뀌었으면(포매터·생성기·설치 포함) 한 번 세워 최종 보고 맨 앞에 목록과 스냅샷을 싣게 한다. 되돌리지 않는다(같은 시간의 사용자 편집일 수 있다). `delegation.writers` 타입은 제외.
- 판정 기록 — 하네스 verifier·리뷰어의 PASS/FAIL·VERDICT·Critical 수를 훅이 `.claude/state/verdicts.jsonl` 에 적는다(에이전트 쓰기는 gate-engine 이 막는다). 승인 커밋은 지금 HEAD 위의 마지막 verify 가 FAIL 이면 막힌다.
- `session-start-summary` — `permissions.ask` 에 `Bash(git commit *)` 가 없으면 알린다. BOOTSTRAP 2단계에 ask 규칙과 pre-push 설치를 넣었다(둘 다 사용자가 켠다).

### 측정
- 계약 테스트 125 → 143, 뮤턴트 23 → 32(전부 죽음). pre-push 는 bare 원격으로 실제 push 해 검증한다.

### 남긴 것
- 목록 밖 UI 라이브러리, git 이 아닌 프로젝트의 결과 검사, 스크립트·인터프리터 안 쓰기의 plan-first, 백그라운드 서브에이전트와 메인의 동시 변경 구분, 판정 품질, gitignore 대상 파일의 스냅샷(README 「알려진 한계」).

## 0.32.0 — 측정과 정본 정합

세 갈래 독립 리뷰(신규 사용자 · 정본 대조 · 적대적 우회)를 기준으로 고치고, 하네스가 모델 단독 대비 무엇을 더하는지 처음으로 측정했다.

### 측정
- `evals/` — `claude plugin eval` 스위트 12케이스(파괴 명령 · 승인 없는 커밋 · 게이트 · 위임 · 스킬 · 리뷰어 · 보고). 막혀야 할 것과 통과해야 할 것(과차단)을 짝지었다. 채점은 trace 의 tool_use ↔ tool_result 를 id 로 이어 "파괴 명령이 실제로 성공했는가" 같은 결과를 본다. `scripts/eval.sh`, 수동 실행 CI(`eval.yml`).
- `tests/test_mutation.py` — 차단 술어 23개를 하나씩 무력화해 계약 테스트가 실패하는지 확인한다. 이 과정에서 호출되지 않던 함수 하나를 지웠다.
- `scripts/doctor.py` — 죽은 참조 · 버전 드리프트 · 스킬 규격 · frontmatter YAML · eval 구조 점검. CI 에서 돈다(기존 인라인 검사 흡수).
- 차단 메시지 계약 테스트 — 모든 deny 에 `다음 행동:` 이 있다.

- eval 결과 요약(README 「eval 결과」): 결정론 게이트의 Δ 는 하위 모델에서 크다(haiku 기준선은 맨 force push·승인 없는 커밋·작업 트리 폐기를 실제로 실행). UI 라이브러리 정책·위임 차단은 모델과 무관하게 Δ. 과차단 0.
- eval 이 찾은 결함: 스킬에 펼쳐진 하네스 절대경로를 소형 모델이 작업 대상으로 착각 → `session-start-summary` 가 작업 대상과 설치 경로를 구분해 알린다. 리뷰 스킬·리뷰어의 `main...HEAD` 하드코딩을 기본 브랜치 해석으로.

### 위임 차단 재설계
- `block-impl-delegation`: 스폰 프롬프트 문구 판정을 버리고, **서브에이전트의 코드 영역 쓰기**(Write·Edit·Bash 쓰기 대상·작업 트리를 바꾸는 git)를 막는다. 훅 입력의 `agent_id` 로 서브에이전트를 가린다. 코드 영역과 예외 타입은 `gates/rules.jsonc` 의 `delegation` 데이터. 승인 토큰을 없앴다.

### 게이트·센서
- `gate-engine` 자기보호: 설치된 플러그인 코드, 플러그인 비활성화(설정 편집·CLI), 심볼릭 링크 별칭, 쓰기 동사 확장, 리다이렉트 대상 판정.
- `require-agent-output-file`: 면제 타입 정확 일치(planner·fork 추가), 화살표·한국어 저장 표현 인식, Claude Code 가 서브에이전트에게 거부하는 보고서 파일명(`REPORT*.md` 등)을 미리 막고 대안 이름을 안내. 차단 메시지에서 우회 토큰을 뺐다.
- `block-destructive-git`: 차단 사유마다 비파괴 대안과 사용자 직접 실행(`! <명령>`) 안내.
- `report-failures-gate`: 권한 거부성 실패만 정정 추적 대상으로 센다. 명령 모양을 서브커맨드·정렬한 플래그·인자로 정규화.
- `commit-boundary-gate`: 새 디렉터리 안의 미추적 파일을 개별로 센다. "staged" 같은 단어 하나로 제안을 대신하지 못한다.
- `session-start-summary`·`/branch`·`/pr`: 기본 브랜치를 `origin/HEAD` 로 해석한다.

### 문서·스킬·에이전트
- 스킬·에이전트 description 을 "무엇을 한다 + 언제 쓴다"로 통일. 상주 토큰 ~2,608 → ~2,498.
- 리뷰어 4종에 판정 보정 예시(좋은 지적 · 나쁜 지적 · 결함 없음).
- planner 의 포맷 사본을 지우고 `docs/PLANNING.md` 를 유일한 정본으로.
- `AGENTS.md`(레포 지도). README 에 규칙↔집행자 표, 탈출구 표, 위협 모델, 테스트·측정 층, eval 결과.
- 스킬 frontmatter 의 따옴표 없는 `argument-hint` 7건이 엄격한 YAML 에서 배열·오류로 읽히던 것을 고쳤다.

### 4차 적대 리뷰(수정분 재검증) 반영
- 공용 셸 파서 `hooks/shellparse.py` — heredoc 본문·따옴표 속 문자열을 명령으로 읽지 않고(셸로 흘러가는 heredoc 과 `bash -c` 인자는 예외), `cd` 를 추적해 상대 경로를 풀고, 쓰기 대상(리다이렉트·복사 목적지·`dd of=`·`find -exec`/`xargs` 뒤 in-place 편집)을 뽑는다. gate-engine 과 위임 훅이 함께 쓴다.
- 자기보호: Write·MultiEdit 로 settings 를 다시 쓰며 하네스 항목을 빼는 것, 셸로 settings 를 쓰는 것(`jq | sponge`, `mv`, `rm`, `git checkout --`), 끝 슬래시 없는 `~/.claude/plugins`·`~/.claude` 를 막는다. `~/.claude` 가 링크인 환경에서 플러그인 파일을 읽기만 하는 명령이 막히던 과차단을 풀었다(링크 해석은 쓰기 대상만).
- 위임: `cd src && …`, 디렉터리 목적지, 일괄 `sed -i`, `git switch/stash/clean`, 전역 옵션 뒤 git 을 잡는다. 기본 코드 영역에 `index.html`·`*.config.*`·`styles/`·`theme/`·`vercel.json` 을 넣었다. 검색어 속 `git checkout` 과 다른 저장소(`git -C /elsewhere`)는 통과.
- 실패 보고: git non-fast-forward 의 `[rejected]` 와 사용자 거절을 권한 거부로 세지 않는다(정상 흐름 하드 차단 해소). 명령 신원은 플래그 표기(`-rf`=`-r -f`=`--recursive --force`)·경로 표기·런처·리다이렉트를 정규화한다.
- `no-ui-library`: 설치를 하위 명령 단위로 본다 — 모노레포 형태(`-F`·`--filter`·`workspace`·`--prefix`)와 별칭 동사(`in`·`a`)를 잡고, 같은 줄의 `grep <패키지> src/` 같은 다른 하위 명령은 설치로 보지 않는다.
- `plan-first`: `app/`·`components/`·`pages/`·`lib/`·`supabase/migrations/` 까지(코드 영역과 같은 축).
- 파일 산출: 면제는 내장 타입과 이 하네스 에이전트만(다른 플러그인의 같은 이름 제외), 거부 파일명 판정을 Claude Code 와 같게 대소문자 구분.

### 남긴 것
- 룰 술어의 우회(별칭 스펙·tarball·`npm pkg set`·side-effect import·CSS `@import`·목록 밖 UI 라이브러리), heredoc 으로 만든 파일의 plan-first, 서브에이전트의 포매터·생성기·설치 쓰기, 쓰기 동사와 보호 경로 언급이 한 하위 명령에 따로 있을 때의 과차단 일부(README 「알려진 한계」).
- 파괴 명령 파서에서 재현된 우회·과차단 20건 남짓은 다음 릴리스로 미뤘다(README 「알려진 한계」).

## 0.31.0 — 공개 전 독립 감사 반영

네 갈래 독립 감사(역할 수행 · 잔재 · 공식 스펙 · 채용 관점)와 수정분 독립 재검증에서 나온 결함을 고쳤다.

### 게이트 우회 차단
- `block-destructive-git`: 런처 접두어(`env`·`sudo`·`nohup`·`xargs` …), 여러 줄 명령, `do`/`then` 복합문, 셸이 받는 heredoc 안의 파괴 명령을 검사한다. `rm -r`(‑f 없이)·`find -delete`, `push --delete/--mirror`, 작업 폐기 checkout/restore/switch, stash·reflog·update-ref 삭제, 이력 재작성을 추가했다. 내부 오류 시 fail-closed.
- `gate-engine`: 에이전트가 탈출구 파일·룰 파일·설정 env 를 스스로 만들거나 바꾸는 것을 코드로 차단한다(데이터로 끌 수 없음). NotebookEdit 도 검사한다.
- `block-impl-delegation`: SendMessage 재개도 위임으로 본다. 에이전트 타입 전체 일치, 보고서 산출 지시·금지문을 오탐 원인으로 지운 뒤 판정한다.
- `plan-first`: `*.template.md` 를 플랜으로 세지 않는다(세 훅의 플랜 정의 통일). 차단 메시지가 우회법을 안내하지 않는다.

### 공식 스펙 정합
- Stop 훅 피드백을 `systemMessage`(사용자 전용)에서 `additionalContext`(모델 전달)로 옮겼다. 최종 답변은 `last_assistant_message` 를 먼저 본다.
- 훅 출력의 `${CLAUDE_PLUGIN_ROOT}` 를 환경변수로 풀고, 스킬·에이전트의 하네스 파일 참조를 전부 `${CLAUDE_PLUGIN_ROOT}/…` 로 바꿨다. `subagent_type` 은 스코프명을 쓴다.
- `review-protocol.sh`: heredoc 이 stdin 을 먹어 훅 입력이 사라지던 버그를 고쳤다.
- 마이그레이션 파일명을 `supabase migration new` 의 14자리 타임스탬프로 바꿨다. `gen types` 는 현행 플래그 형태.
- marketplace `metadata.description`, plugin `repository` — `claude plugin validate --strict` 통과.

### 검증 도구
- `synthesize.py --check`: 채우지 않은 스켈레톤, `F10` 에 가려진 `F1` 누락, 산문으로 적은 강등, 원장 없는 리포트를 실패시킨다.
- `stack-compliance-guard`: 선언(deps) 외에 사용 신호(`../../` import · 컴포넌트 CSS import · `useEffect`+fetch)를 본다.
- `harness-boarding-guard`: 마커만 있고 실제 plan 이 없으면 침묵하지 않는다.
- `/auth-scaffold`: pgTAP 동작 테스트(8건)를 함께 생성한다 — 이전 권한상승 정책에서 실패하는 것을 실DB로 확인했다.
- verifier 는 `pnpm check` 의 구성 명령별 결과와 `&&` 단락으로 실행되지 않은 단계(NOT RUN)를 보고한다.

### 2차 독립 재검증 반영
- `block-destructive-git`: 파싱 실패 폴백을 부분문자열 목록에서 패턴으로 바꿨다(이전엔 `git commit`·`push -f` 가 폴백에서 통과했고, 승인 커밋 접두어 뒤에 이은 명령도 검사하지 않았다). `|&`, 백틱 치환, 셸로 가는 here-string·`echo … | sh`, 따옴표 없는 `$(pwd)` 를 검사한다. `find -type` 은 범위를 좁히는 필터로 치지 않는다.
- `gate-engine`: 경로를 쪼개 쓰는 셸 우회(`cd .claude/state && touch …`, 변수·중괄호·따옴표 분할)를 막는다. 설정의 `disableAllHooks`·플러그인 비활성화·`\u` 이스케이프한 탈출구 env, 설치본 `gates/rules.jsonc` 편집도 막는다. `pnpm dlx`·`bunx` 같은 일회성 실행기와 `radix-ui` 패키지를 UI 라이브러리 룰이 본다.
- `block-impl-delegation`: "구현할 것/하시오", "적용해줘", "생성해", rewrite/edit/replace 를 지시로 본다. 영어 동사는 절 첫머리일 때만 세서 "verify the fix" 같은 명사 오탐을 없앴다. 오탐 제거 필터가 문장 전체를 지우던 범위를 좁혔다.
- `synthesize.py --check`: severity 칸 안 강등(`High → Low`)·처리 칸에 적은 강등·부록 행으로 가린 강등, `-`/`TBD` 처리 칸, 원장 안 소제목 아래 행 누락, "없음" 단어 하나로 빈 원장 오판, 앞쪽 "ledger" 헤딩이 원장을 가로채던 것을 고쳤다.
- CI 트리거 브랜치를 기본 브랜치(`master`)로 고쳤다.

### 기타
- Python 3.9 지원(`from __future__ import annotations` 로 PEP 604 주석을 지연 평가), GitHub Actions(3.9·3.12). 테스트 97개.
- 특정 프로젝트·도메인·개인 맥락 잔재를 제거했다. 외부 UI 라이브러리 미사용은 "영구 금지"가 아니라 끌 수 있는 기본 정책으로 표기한다.
- README: 수치 대신 실패 유형과 대응 장치, 선행 사례 명시, 보안 경계가 아닌 것과 알려진 한계 섹션.
