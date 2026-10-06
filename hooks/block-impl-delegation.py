#!/usr/bin/env python3
"""PreToolUse hook — RULES §7 실효화: 구현의 서브에이전트 위임을 차단한다.

배경(실측): 메인 세션이 확정 스펙을 근거로 구현 전체를 하위 모델 서브에이전트에
위임했다. 산출물은 typecheck·test 를 통과했지만 모듈 경계가 무너져(표시 문구와 도메인
로직 혼재, 타입 산재, 함수 오배치) 사람이 육안으로 반복 적발했다. 규정은 있었지만
강제 장치가 없어 조용히 재발했다.

판정 규칙:
- 대상은 Agent(=Task) 스폰과 SendMessage 재개. 그 외 도구는 통과.
- 읽기 전용·기계 검증 에이전트(리뷰어·verifier·plan-consistency·Explore·Plan·
  claude-code-guide)와 plan 작성자(planner)는 통과 — 스코프명(`plugin:name`)도 같은 규칙.
  판정은 **에이전트 타입**으로만 한다. SendMessage 수신자 이름은 아무 문자열이나 될 수
  있어 타입 근거가 되지 못한다.
- 메인 세션에게 보내는 보고(SendMessage `to: main`)는 위임이 아니다.
- 프롬프트가 **코드를 쓰라는 지시**로 읽히면 deny. 지시 동사와 코드 대상(파일 경로·
  확장자·패키지·테스트 작성)이 함께 나타날 때만 발동한다. 판정 전에 오탐 원인을 지운다:
  툴 이름(`Write`·`Edit` 툴), 보고서·결과를 "쓰라"는 산출 지시.

우회: 프롬프트에 `[HARNESS: 구현위임 승인됨]` 를 포함시키면 통과한다. 사용자가
명시 승인한 예외(예: worktree 병렬 작업)에만 쓴다 — 협조형 장치이지 보안 경계가 아니다.
"""
import json
import re
import sys

BYPASS = "[HARNESS: 구현위임 승인됨]"

# 위임이 정상인 에이전트 타입 — 스코프명의 마지막 조각에 대해 전체 일치로 본다.
# (부분 일치였을 때 `my-research-impl` 같은 이름이 통과했다.)
ALLOWED_TYPES = re.compile(
    r"(?:[\w-]+-)?reviewer|verifier|plan-consistency(?:-reviewer)?|planner|explore|plan"
    r"|claude-code-guide|statusline-setup",
    re.IGNORECASE,
)
REPORT_RECIPIENTS = {"main", "lead", "team-lead", "parent"}

# "코드를 쓰라"는 지시 동사. 한국어는 요청형 어미까지 포함한다.
WRITE_VERB = re.compile(
    r"(?:구현|작성|생성|적용|추가|수정|완성|변경|교체)(?:하라|해라|해|하세요|하시오|해 ?줘|해 ?주|할 것|하고|바람|해야 ?(?:한다|함|돼))|"
    r"만들어(?:라|줘|주세요)|짜(?:라|줘)|고쳐(?:라|줘|주세요|놔)|리팩터|바꿔(?:라|줘)|붙여(?:라|줘)|"
    # 영어 동사는 절 첫머리(명령형)일 때만 — "verify the fix", "review the change" 의 명사는 지시가 아니다
    r"(?:^|[.\n:;!?]\s*|\b(?:and|then|please|just|also|now)\s+)"
    r"(?:implement|scaffold|write|rewrite|refactor|create|build|fix|add|update|modify|change|patch|edit|replace|generate|apply)\b",
    re.IGNORECASE | re.MULTILINE,
)

# 코드 산출물 대상 신호.
CODE_TARGET = re.compile(
    r"\.tsx?\b|\.jsx?\b|\.py\b|\.sql\b|packages/|src/|components/|"
    r"테스트를 (작성|추가)|테스트 코드|함수를|모듈을|스키마를|컴포넌트를|훅을|"
    r"\bpackage\.json\b|\btsconfig\b|\bvitest\b|\bunit tests?\b|\bfunction\b|\bcomponent\b|\bhook\b",
    re.IGNORECASE,
)

# 판정 전에 지우는 오탐 원인.
NOISE = re.compile(
    # 툴 이름: `Write`·Write 툴·Write/Edit·"Write"
    r"`(?:Write|Edit|NotebookEdit|MultiEdit)`|\b(?:Write|Edit)(?=\s*(?:/|\||툴|tool|도구))"
    r"|(?<=[/|])(?:Write|Edit)\b|\"(?:Write|Edit)\""
    # 산출 지시: 결과·보고서·리포트를 파일에 쓰라 — 리뷰·조사 위임의 정상 문구
    r"|\b(?:write|save|dump|append)\s+(?:(?:your|the|a|all|each)\s+)?(?:report|results?|findings|"
    r"output|notes|summary|ledger|sections?)\b[^.\n,;]*"
    r"|(?:결과|보고서|리포트|원장|소견|섹션)(?!\s*화면)(?:를|을|는)?(?:[^\n.]|\.(?=\S)){0,60}?(?:에|로)\s*(?:작성|저장|기록|써)\w*"
    # 금지문: "Do not modify files" · "수정하지 마라" 는 쓰라는 지시의 반대다
    r"|\b(?:do not|don't|never|must not|without)\s+(?:\w+\s+){0,2}?(?:modify|change|write|edit|create|"
    r"update|fix|add|touch)\b[^.\n,;]*|(?:수정|작성|변경|추가|구현)\w{0,3}\s*(?:금지|하지 ?마|않는다)",
    re.IGNORECASE,
)


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name")
    if tool not in ("Agent", "Task", "SendMessage"):
        sys.exit(0)

    ti = data.get("tool_input") or {}
    if tool == "SendMessage":
        if str(ti.get("to") or "").strip().lower() in REPORT_RECIPIENTS:
            sys.exit(0)
        prompt, subagent = str(ti.get("message") or ""), ""
    else:
        prompt, subagent = str(ti.get("prompt") or ""), str(ti.get("subagent_type") or "")
    description = str(ti.get("description") or "")

    if BYPASS in prompt:
        sys.exit(0)
    if subagent and ALLOWED_TYPES.fullmatch(subagent.rsplit(":", 1)[-1]):
        sys.exit(0)

    haystack = NOISE.sub(" ", f"{prompt}\n{description}")
    if WRITE_VERB.search(haystack) and CODE_TARGET.search(haystack):
        deny(
            "RULES §7 — 구현은 서브에이전트에 위임하지 않는다(산출물이 검증을 통과해도 모듈 경계가 "
            "무너지는 실패가 반복됐다). 메인 세션이 직접 구현하고, 검증은 verifier·리뷰어 위임으로 "
            "분리하라. 읽기 전용 조사·감사였다면 코드를 '쓰라'로 읽히는 표현을 빼고 다시 스폰하라. "
            "위임이 꼭 필요한 예외는 사용자에게 먼저 확인한다."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
