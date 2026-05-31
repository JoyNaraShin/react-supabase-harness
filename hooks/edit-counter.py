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
            f"⚠️  `src/` 편집 {state['count']}회 누적. "
            "변경 검증 주기 — `/check` (typecheck + biome) 또는 `/verify` (full) 실행 권장."
        )
        print(json.dumps({"additionalContext": msg}))
        state["count"] = 0  # recommendation 후 리셋

    save_state(p, state)
    sys.exit(0)


if __name__ == "__main__":
    main()
