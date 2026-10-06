# Changelog

이전 버전의 변경 내역은 git 이력에 있다. 이 파일은 공개 이후부터 기록한다.

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
