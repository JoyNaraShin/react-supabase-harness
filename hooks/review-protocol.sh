#!/usr/bin/env bash
# UserPromptSubmit hook: 리뷰/감사 요청 감지 시 Review Protocol을 컨텍스트에 주입.
# 프로토콜 문서(docs/REVIEW-PROTOCOL.md)는 상주 컨텍스트가 아니다(컨텍스트 과적 방지) — 이 훅이 유일한 주입 경로.
# stdout 출력은 UserPromptSubmit에서 그대로 컨텍스트에 추가된다(exit 0).
#
# 판정 (결정론적 — LLM 판정 없음):
#   발화 = (리뷰 동사 + 같은 절 안의 요청 표현) 또는 강한 복합어 단독.
#   침묵 = "리뷰"가 기능·엔티티 이름으로 쓰인 경우(리뷰 테이블·리뷰 작성 기능),
#          리뷰 *이후*의 반영·수정 요청(리뷰 코멘트 반영해줘), 인사(감사합니다).
#   리뷰 요청 감지의 실측 코퍼스(미탐 8·오탐 5)를 tests/test_hooks.py 가 고정한다.

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# 훅 입력(stdin)을 먼저 받아 둔다 — 아래 heredoc 이 python 의 stdin 을 스크립트로 쓰기 때문.
HOOK_INPUT="$(cat)" python3 - "$ROOT" <<'PY' 2>/dev/null
import json, os, re, sys

root = sys.argv[1]
try:
    prompt = json.loads(os.environ.get("HOOK_INPUT") or "{}").get("prompt", "") or ""
except Exception:
    sys.exit(0)

p = prompt.strip()

STRONG = re.compile(
    r"(비판|적대)적으로 ?(리뷰|검토)|(코드|보안|아키텍처|설계|하네스) ?(리뷰|감사|점검)|/code-review|/review-"
    r"|\b(code|security|architecture) review\b|\bcritically review\b|\badversarial(ly)? review",
    re.I)
VERB_KO = r"(리뷰|검토|점검|감사|비판|평가|진단|문제점 ?(을 )?찾)"
CUE_KO = (r"(해 ?줘|[아어] ?줘|해 ?주세요|해 ?주라|해 ?줄래|해 ?봐|해 ?보자|하자|부탁|돌려|진행해|받고 ?싶|받아 ?보|"
          r"좀 ?(해|봐)|해 ?달라)")
ASK_KO2 = re.compile(VERB_KO + r".{0,8}?" + CUE_KO, re.I)
VERB_EN = r"(?<![-\w])(review|audit|critique|assess|evaluate)\b"
# 요청형(can you … / please …) 또는 명령형(문두 동사)일 때만. "the review table …" 같은 서술은 제외.
ASK_EN = re.compile(r"\b(can|could|would|will) you\b.{0,20}?" + VERB_EN + r"|\bplease\b.{0,20}?" + VERB_EN
                    + r"|^\W*" + VERB_EN, re.I)

# "리뷰"가 기능·엔티티 이름인 경우 — 커머스·서비스 도메인의 1급 엔티티다.
ENTITY = re.compile(
    r"(상품|고객|사용자|구매|별점|평점)? ?리뷰 ?(테이블|목록|리스트|작성|기능|컬럼|화면|페이지|api|데이터|"
    r"폼|카드|컴포넌트|모달|섹션|위젯|스키마|엔드포인트|개수|수정 기능|삭제|등록)", re.I)
# 리뷰가 이미 끝났고 그 결과를 반영하라는 요청.
AFTER = re.compile(r"(리뷰|검토|감사|/review-\S*) ?(코멘트|결과|피드백|지적|의견)\S* ?.{0,12}"
                   r"(반영|수정|고쳐|적용|처리)", re.I)
THANKS = re.compile(r"감사(합니다|해요|드립니다|드려요)")

if AFTER.search(p):
    sys.exit(0)
hit = bool(STRONG.search(p))
if not hit:
    q = ENTITY.sub(" ", THANKS.sub(" ", p))
    hit = bool(ASK_KO2.search(q) or ASK_EN.search(q.strip()))
if not hit:
    sys.exit(0)

print(f"""[Review Protocol — 적용]
이 요청은 비판 리뷰/감사로 감지됐다. 메인 루프가 직접 평가하지 말고, Agent 도구로
"이건 네가 만든 게 아니다, 이해관계 0, 무자비하게 비판하라" 외부 감사관 서브에이전트에게
위임한다. 사안이 크면 복수의 적대적 렌즈로 병렬 위임하고, 결과는 누그러뜨리지 말고 종합한다.
각 에이전트는 자기 디스커버리부터 독립 수행한다.
{root}/docs/REVIEW-PROTOCOL.md 전문을 Read 하라(상주 아님) — 특히 복수 리뷰어를
종합하기 전에는 §종합 규약(원장 join · {root}/scripts/synthesize.py 모드 A/B)을 따른다.
(감지가 틀렸다면 — 리뷰 요청이 아니면 — 이 안내는 무시한다.)""")
PY
exit 0
