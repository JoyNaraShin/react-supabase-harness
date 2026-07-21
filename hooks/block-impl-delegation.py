#!/usr/bin/env python3
"""PreToolUse hook — RULES §55 실효화: 구현의 서브에이전트 위임을 차단한다.

배경(2026-07-20 실측 사고): 메인 세션이 확정 스펙을 근거로 코드 구현 전체를
sonnet 서브에이전트에 위임했다. 산출물은 typecheck/test 를 통과했지만 모듈 경계가
무너져(표시 문구가 엔진 코어에 혼재, 타입 산재, 함수 오배치) 사용자가 육안으로
세 차례 적발했다. RULES §7·§55 는 이미 이 패턴을 "거짓 절약이라 폐지"로 규정해
두었으나, 규정을 강제하는 장치가 없어 조용히 재발했다.

판정 규칙:
- 대상은 Agent(=Task) 스폰만. 그 외 도구는 통과.
- 리뷰어·verifier·plan-consistency 등 **읽기 전용/기계 검증** 에이전트는 통과
  (RULES §55 의 하드핀 허용 대상 + 적대 리뷰는 오히려 위임이 정답).
- 프롬프트가 **코드를 쓰라는 지시**로 읽히면 deny. 지시 동사(구현/작성/만들어라
  /implement/scaffold)와 코드 대상(파일 경로·확장자·패키지·테스트 작성)이 함께
  나타날 때만 발동해 조사·리서치 위임의 오탐을 피한다.

우회: 프롬프트에 `[HARNESS: 구현위임 승인됨]` 를 포함시키면 통과한다. 사용자가
명시 승인한 예외(예: worktree 병렬 마이그레이션)에만 쓴다 — 메인 세션이 스스로
붙이는 것은 규정 위반이다.
"""
import json
import re
import sys

BYPASS = "[HARNESS: 구현위임 승인됨]"

# 읽기 전용·기계 검증 에이전트 — 위임이 정상인 대상.
ALLOWED_TYPES = re.compile(
    r"verifier|reviewer|review-|plan-consistency|explore|plan$|^plan|research",
    re.IGNORECASE,
)

# "코드를 쓰라"는 지시 동사.
WRITE_VERB = re.compile(
    r"구현하라|구현해|작성하라|작성해|만들어라|만들어줘|짜라|짜줘|추가하라|고쳐라|리팩터|"
    r"\bimplement\b|\bscaffold\b|\bwrite\b|\brefactor\b|\bcreate\b|\bbuild\b",
    re.IGNORECASE,
)

# 코드 산출물 대상 신호.
CODE_TARGET = re.compile(
    r"\.tsx?\b|\.jsx?\b|\.py\b|\.sql\b|packages/|src/|components/|"
    r"테스트를 (작성|추가)|테스트 코드|함수를|모듈을|스키마를|컴포넌트를|"
    r"\bpackage\.json\b|\btsconfig\b|\bvitest\b|\bunit test",
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

    if data.get("tool_name") not in ("Agent", "Task"):
        sys.exit(0)

    ti = data.get("tool_input") or {}
    prompt = str(ti.get("prompt") or "")
    subagent = str(ti.get("subagent_type") or "")
    description = str(ti.get("description") or "")

    if BYPASS in prompt:
        sys.exit(0)
    if ALLOWED_TYPES.search(subagent):
        sys.exit(0)

    haystack = f"{prompt}\n{description}"
    if WRITE_VERB.search(haystack) and CODE_TARGET.search(haystack):
        deny(
            "RULES §55 위반 — 구현은 고판단 작업이라 서브에이전트에 위임하지 않는다. "
            "(하네스는 이 패턴을 '거짓 절약이라 폐지'로 이미 규정했고, 2026-07-20 "
            "재발 시 모듈 경계 붕괴가 실측됐다.) 메인 세션이 직접 구현하고, "
            "검증은 verifier·리뷰어 위임으로 분리하라. 위임이 꼭 필요한 예외면 "
            f"사용자 승인을 받아 프롬프트에 `{BYPASS}` 를 넣어라."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
