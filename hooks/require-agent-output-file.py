#!/usr/bin/env python3
"""PreToolUse hook — 장기 실행 서브에이전트에 "파일 우선 산출"을 강제한다.

배경(반복 실측): 리뷰·리서치
에이전트가 세션 사용량 한도로 중간에 종료되면 메시지로만 보고하던 결과는 통째로
사라지고, 메인 세션은 새 에이전트를 처음부터 다시 띄워 같은 파일을 다시 읽는다
(이중 소모). 살아남은 결과는 예외 없이 **파일에 먼저 쓰고 메시지는 포인터만**
보낸 에이전트의 것이었다. 규정으로 두면 잊히므로 스폰 시점에 장치로 막는다.

판정 규칙:
- 대상은 Agent(=Task) 스폰만.
- 결과가 짧고 기계적인 에이전트(Explore·verifier·plan-consistency·claude-code-guide·
  statusline)는 통과 — 손실 비용이 낮다.
- **파일을 쓸 수 없는 에이전트**(이 하네스의 읽기 전용 리뷰어 3종, 내장 Plan)는 통과 —
  쓰기 도구가 없는 에이전트에게 파일 산출을 요구하면 하네스가 처방한 `/review-*` 를
  하네스가 막는다.
- 그 외(리서치·general-purpose·planner·fork 등)는 프롬프트에 **출력 파일 경로**
  (`.md`/`.json`/`.txt`/`.html`로 끝나는 경로)가 **저장 지시와 함께** 있어야 통과.
  읽을 문서 경로만 있는 프롬프트는 산출 지시가 아니다. 없으면 deny.

우회: 프롬프트에 `[HARNESS: 파일산출 면제]` 를 포함하면 통과. 사용자가 명시
승인한 예외에만 쓴다 — 메인 세션이 스스로 붙이는 것은 규정 위반이다.
"""
import json
import re
import sys

BYPASS = "[HARNESS: 파일산출 면제]"

# 결과가 짧아 유실 비용이 낮은 에이전트 — 파일 강제 대상 아님.
EXEMPT_TYPES = re.compile(
    r"(^|:)(explore|plan)$|verifier|plan-consistency|claude-code-guide|statusline"
    r"|(^|:)(craft|stability|structure-fitness)-reviewer$",
    re.IGNORECASE,
)

# 출력 파일 경로 신호: 경로 구분자를 포함하고 문서 확장자로 끝나는 토큰.
OUTPUT_PATH = re.compile(
    r"(?:^|[\s`'\"(=:])((?:[A-Za-z]:)?(?:~|\.{0,2})?[/\\]?[\w.@+-]+(?:[/\\][\w.@+-]+)+"
    r"\.(?:md|json|txt|html))\b",
    re.IGNORECASE,
)
# 그 경로가 "읽을 것"이 아니라 "쓸 곳"이라는 신호 — 경로 앞뒤 가까이에 있어야 한다.
SAVE_CUE = re.compile(
    r"저장|산출|기록|남겨|써 ?(라|줘|둬)|쓰고|작성|결과(는|를)|보고서|출력|"
    r"\b(save|write|output|report|results?|dump)\b",
    re.IGNORECASE,
)


def has_output_target(prompt: str) -> bool:
    for m in OUTPUT_PATH.finditer(prompt):
        window = prompt[max(0, m.start() - 60):m.end() + 60]
        if SAVE_CUE.search(window):
            return True
    return False


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

    if data.get("tool_name") not in ("Agent", "Task"):
        sys.exit(0)

    ti = data.get("tool_input") or {}
    prompt = str(ti.get("prompt") or "")
    subagent = str(ti.get("subagent_type") or "")

    if BYPASS in prompt:
        sys.exit(0)
    if EXEMPT_TYPES.search(subagent):
        sys.exit(0)
    if has_output_target(prompt):
        sys.exit(0)

    deny(
        "파일 우선 산출 위반 — 리뷰·리서치·general-purpose 에이전트는 결과를 먼저 "
        "파일로 저장하고 메시지는 경로만 보내야 한다(사용량 한도로 중간 종료되면 "
        "메시지 보고는 유실되고 재스폰은 이중 소모 — 반복 실측). "
        "프롬프트에 출력 파일 경로(scratchpad 아래 .md/.json)를 넣고, 섹션 단위로 "
        "즉시 저장하라고 지시하라. 사용자 승인 예외면 프롬프트에 "
        f"`{BYPASS}` 를 넣어라."
    )


if __name__ == "__main__":
    main()
