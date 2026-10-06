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
- **스스로 파일을 쓰는 에이전트**(planner — plan 파일을 쓰고 한 줄만 돌려준다)와 fork(부모
  맥락을 그대로 이어받는다)는 통과.
- 그 외(리서치·general-purpose 등)는 프롬프트에 **출력 파일 경로**가 **저장 지시와 같은
  절에** 있어야 통과("save … to <경로>", "<경로> 에 저장"). 읽을 문서 경로 옆에 "report"가
  있는 것은 산출 지시가 아니다.
- 출력 파일명이 `REPORT|SUMMARY|FINDINGS|ANALYSIS*.md` 면 deny — Claude Code 가 서브에이전트의
  그런 이름의 .md 저장을 거부하므로, 지시가 있어도 결과가 파일로 남지 않는다.

우회: 프롬프트에 면제 토큰을 포함하면 통과. 사용자가 명시 승인한 예외에만 쓴다 —
메인 세션이 스스로 붙이는 것은 규정 위반이다. 토큰은 README 에만 적고 거부 메시지에는 싣지
않는다(메시지가 토큰을 알려 주면 그 자체가 자기 우회 안내가 된다).
"""
import json
import re
import sys

BYPASS = "[HARNESS: 파일산출 면제]"

# 결과가 짧거나(유실 비용이 낮음), 쓰기 도구가 없거나, 스스로 파일을 쓰는 에이전트. 이름 전체 일치만 —
# 부분 문자열로 매칭하면 `someverifier-but-actually-research` 같은 이름이 면제를 얻는다.
EXEMPT_TYPES = re.compile(
    r"^(?:(?:explore|plan|claude-code-guide|statusline-setup|fork)"
    r"|(?:react-supabase-harness:)?(?:verifier|plan-consistency-reviewer|craft-reviewer|stability-reviewer"
    r"|structure-fitness-reviewer|planner))$",
    re.IGNORECASE,
)

PATH = (r"((?:\$\{?\w+\}?|~|\.{1,2}|[A-Za-z]:)?[/\\]?(?:[\w.@+-]+[/\\])*[\w.@+-]+"
        r"\.(?:md|markdown|json|jsonl|txt|html|csv|ya?ml))\b")
# 저장 지시와 경로가 한 절 안에 있어야 한다. 영어는 동사가 앞, 한국어는 조사+동사가 뒤.
OUTPUT_TARGET = [
    re.compile(r"\b(?:save|write|dump|append|store|record|output)\b[^\n.;]{0,60}?" + PATH, re.I),
    re.compile(r"(?:output|results?|결과|산출|출력|저장)\s*(?:file|path|파일|경로|위치)?\s*(?:[:：]|->|→|=>)\s*`?"
               + PATH, re.I),
    re.compile(PATH + r"`?\s*(?:에|로|으로)\s*(?:[^\s.]+\s+){0,3}?(?:저장|기록|작성|남겨|써)", re.I),
]
REPORT_NAME = re.compile(r"(?:^|[/\\])(REPORT|SUMMARY|FINDINGS|ANALYSIS)[^/\\]*\.md$")


def output_targets(prompt: str) -> list:
    return [m.group(1) for rx in OUTPUT_TARGET for m in rx.finditer(prompt)]


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
    targets = output_targets(prompt)
    bad = [t for t in targets if REPORT_NAME.search(t)]
    if bad:
        deny(
            f"출력 파일명 `{bad[0]}` 은 저장되지 않는다 — Claude Code 는 서브에이전트가 "
            "REPORT·SUMMARY·FINDINGS·ANALYSIS 로 시작하는 .md 를 쓰는 것을 거부한다.\n"
            "다음 행동: `<주제>-ledger.md`·`<주제>-notes.md` 처럼 다른 이름으로 바꿔 다시 스폰하라."
        )
    if targets:
        sys.exit(0)

    deny(
        "파일 우선 산출 위반 — 리뷰·리서치·general-purpose 에이전트는 결과를 먼저 "
        "파일로 저장하고 메시지는 경로만 보내야 한다(사용량 한도로 중간 종료되면 "
        "메시지 보고는 유실되고 재스폰은 이중 소모 — 반복 실측).\n"
        "다음 행동: 프롬프트에 \"결과를 <scratchpad>/<주제>-ledger.md 에 섹션마다 즉시 저장하라\" "
        "같은 지시를 넣어 다시 스폰하라(파일명은 REPORT·SUMMARY·FINDINGS·ANALYSIS 로 시작하지 않게). "
        "파일 산출이 맞지 않는 작업이면 사용자에게 면제를 요청하라."
    )


if __name__ == "__main__":
    main()
