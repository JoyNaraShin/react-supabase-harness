#!/usr/bin/env python3
"""PostToolUse hook — 보안 민감 파일 편집 시 `/review-stability` 권장 힌트 주입.

Edit/Write 가 인증·RLS·시크릿·스토리지 경로(아래 is_security_risk)를 건드리면
`additionalContext` 로 `/review-stability` 권장을 주입한다. 같은 파일 연속 편집은
1회만 알린다(last_path 디바운스) — 새 위험 파일로 옮길 때마다 다시 알림.

review-stability 스킬은 disable-model-invocation 이라 모델이 자동 호출하지 못한다.
그래서 "위험 diff인데 보안 리뷰가 조용히 누락"되는 것을 이 훅이 넛지로 메운다.
(edit-counter 가 /check 를 넛지하는 것과 동일한 패턴.)

상태 파일: `<project>/.claude/state/security-nudge.json` (프로젝트별 — gitignore 권장).
실패해도 워크플로 차단 안 함 (exit 0).
"""
import json
import os
import sys
from pathlib import Path


def state_path() -> Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    return Path(base) / ".claude" / "state" / "security-nudge.json"


def load_state(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_state(p: Path, state: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def is_security_risk(path: str) -> bool:
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)

    # .env* (단 .env.example/.env.sample 템플릿은 시크릿 없음 → 제외)
    if base.startswith(".env") and not base.endswith((".example", ".sample")):
        return True
    # 인증 모듈 · 마이그레이션
    if "src/features/auth/" in norm or "supabase/migrations/" in norm:
        return True
    # src/lib/supabase.* · src/lib/storage.*
    if "src/lib/" in norm and (base.startswith("supabase.") or base.startswith("storage.")):
        return True
    return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name", "") not in ("Edit", "Write"):
        sys.exit(0)

    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not is_security_risk(path):
        sys.exit(0)

    p = state_path()
    state = load_state(p)
    if state.get("last_path") == path:
        sys.exit(0)  # 같은 파일 연속 편집 — 재알림 생략

    msg = (
        f"🔐 보안 민감 파일 변경: {path}. "
        "PR/머지 전 `/review-stability` (인증·RLS·시크릿·입출력 + DB 정합) 실행 권장."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }}))

    state["last_path"] = path
    save_state(p, state)
    sys.exit(0)


if __name__ == "__main__":
    main()
