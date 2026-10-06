# Changelog

이전 버전의 변경 내역은 git 이력에 있다. 이 파일은 공개 이후부터 기록한다.

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
