#!/usr/bin/env bash
# UserPromptSubmit hook: 리뷰/감사 요청 감지 시 Review Protocol을 컨텍스트에 주입.
# 프로토콜 문서(docs/REVIEW-PROTOCOL.md)는 상주 컨텍스트가 아니다(컨텍스트 과적 방지) — 이 훅이 유일한 주입 경로.
# stdout 출력은 UserPromptSubmit에서 그대로 컨텍스트에 추가된다(exit 0).

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

prompt=$(python3 -c "import sys,json
try: print(json.load(sys.stdin).get('prompt',''))
except Exception: pass" 2>/dev/null)

# False-positive 차단: 단순 언급("리뷰한거야?")·인사("감사합니다")·스킬명("proposal-review")엔
# 발동하지 않는다. 실제 리뷰 요청 = 강한 복합어(strong) 단독, 또는 (리뷰 noun + 요청 cue).
# macOS BSD grep 호환 — \s·\b 미사용.
strong='비판적으로 ?리뷰|적대적으로 ?리뷰|코드 ?리뷰|보안 ?리뷰|아키텍처 ?리뷰|보안 ?감사|코드 ?감사|하네스 ?감사|/code-review|/review-|code review|security review|critically review|adversarial.{0,3}review|review (this|the|my|it)|audit (this|the|my|it)'
noun='리뷰|비판|critique'
cue='해줘|해주|해 ?줘|돌려|부탁|진행|봐줘|봐 ?줘|좀 ?봐|해봐|해 ?봐|받고|받아|받을|싶|하자|원해|해줄'

if printf '%s' "$prompt" | grep -qiE "$strong" \
   || { printf '%s' "$prompt" | grep -qiE "$noun" && printf '%s' "$prompt" | grep -qiE "$cue"; }; then
  cat <<EOF
[Review Protocol — 강제 적용]
이 요청은 비판 리뷰/감사로 감지됨. 메인 루프에서 직접 평가하지 말 것.
Agent 도구로 "이건 네가 만든 게 아니다, 이해관계 0, 무자비하게 비판하라" 외부 감사관
서브에이전트에게 위임하고, 사안이 크면 복수의 적대적 렌즈로 병렬 위임한다.
각 에이전트는 자기 디스커버리부터 독립 수행. 결과는 누그러뜨리지 말고 종합.
지금 ${ROOT}/docs/REVIEW-PROTOCOL.md 전문을 Read 하라(상주 아님) — 특히 복수 리뷰어
종합 전엔 §종합 규약(원장 join·${ROOT}/scripts/synthesize.py 모드 A/B) 준수가 필수다.
EOF
fi
exit 0
