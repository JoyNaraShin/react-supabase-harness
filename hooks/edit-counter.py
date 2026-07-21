#!/usr/bin/env python3
"""PostToolUse hook — src/** 편집 카운터. N회 누적 시 `/check` 권장 힌트 주입.

Edit/Write tool 이 `src/` 하위 파일을 건드리면 카운터를 올리고, 임계치에
도달하면 `additionalContext` 로 `/check` 권장을 주입한 뒤 카운터를 리셋한다.

상태 파일: `<project>/.claude/state/edit-counter.json` (프로젝트별 — gitignore 권장).
"""
import json
import os
import sys
from pathlib import Path

THRESHOLD = 5


def state_path() -> Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    return Path(base) / ".claude" / "state" / "edit-counter.json"


def load_state(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"count": 0}


def save_state(p: Path, state: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name", "")
    if tool not in ("Edit", "Write"):
        sys.exit(0)

    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path:
        sys.exit(0)

    # 프로젝트 src/ 편집만 카운트 (절대·상대 경로 모두).
    is_src = path.startswith("src/") or "/src/" in path
    if not is_src:
        sys.exit(0)

    p = state_path()
    state = load_state(p)
    state["count"] = int(state.get("count", 0)) + 1

    if state["count"] >= THRESHOLD:
        msg = (
            f"⚠️  `src/` 편집 {state['count']}회 누적 — 검증 주기. "
            "① 기계 검증: 메인 세션이 `verifier` 에이전트를 직접 호출(typecheck+biome)하거나, "
            "사용자에게 `/check`·`/verify` 실행을 안내하라. "
            "(`/check`·`/verify` 는 disable-model-invocation 이라 모델이 직접 못 켠다.) "
            "② 설계 검증: 기계 검증은 '컴파일되는가'만 본다 — 모듈 경계·계층 분리·타입 배치는 "
            "잡지 못한다(2026-07-20 실측: typecheck·test 전부 통과했으나 표시 문구가 엔진 코어에 "
            "혼재하고 타입이 산재해 사용자가 육안 적발). 슬라이스가 구조를 건드렸으면 "
            "변경 성격 매칭 렌즈 1개를 **적대 리뷰로 위임**하라 — 구조·경계·의존 방향은 "
            "`structure-fitness-reviewer`, 보안·RLS·DB 는 `stability-reviewer`, "
            "UI·성능·a11y 는 `craft-reviewer`."
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }}))
        state["count"] = 0  # recommendation 후 리셋

    save_state(p, state)
    sys.exit(0)


if __name__ == "__main__":
    main()
