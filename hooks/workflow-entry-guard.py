#!/usr/bin/env python3
"""PostToolUse hook — 하네스 워크플로우 미탑승 감지 시 규정을 주입한다.

배경(실측 사고): WORKFLOW.md·RULES.md 는 컨텍스트 상주가 아니고,
docs/REVIEW-PROTOCOL.md 는 review-protocol 훅이 "리뷰해줘" 어휘에만 반응해 주입한다. 그래서
**구현 착수·신규 프로젝트 생성 경로에는 규정을 주입하는 장치가 전혀 없었다.**
메인 세션이 하네스를 한 번도 읽지 않고 자체 플로우로 진행해도 아무도 막지 않았다.

발동 조건: 프로젝트의 첫 `src/**` 코드 파일 생성(Write)인데 `docs/plans/` 에
plan 이 하나도 없을 때 = 플랜 없이 코드부터 시작한 상태. 프로젝트당 1회만 주입하고
`.claude/state/workflow-entry.json` 에 기록한다(반복 잔소리 금지).

RULES §1 의 "한 문장 diff 는 파이프라인 생략" 예외를 존중하려면 이 훅이
*차단*이 아니라 *주입*이어야 한다 — 판단은 메인 세션이 한다.
"""
import json
import os
import sys
from pathlib import Path

# 훅 출력은 치환되지 않는다 — `${CLAUDE_PLUGIN_ROOT}` 를 그대로 내면 모델이 경로를 풀 수 없다.
# 훅 프로세스에는 같은 이름의 환경변수가 export 된다(plugins-reference#where-each-variable-resolves).
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)

CODE_EXT = (".ts", ".tsx", ".js", ".jsx", ".py", ".sql", ".svelte", ".vue")


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")


def state_file() -> Path:
    return project_dir() / ".claude" / "state" / "workflow-entry.json"


def already_notified() -> bool:
    return state_file().exists()


def mark_notified() -> None:
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"notified": True}))


def has_plan() -> bool:
    plans = project_dir() / "docs" / "plans"
    if not plans.is_dir():
        return False
    return any(f.suffix == ".md" and not f.name.endswith(".template.md")
               for f in plans.iterdir())


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") != "Write":
        sys.exit(0)

    path = (data.get("tool_input") or {}).get("file_path") or ""
    is_src_code = ("/src/" in path or path.startswith("src/")) and path.endswith(CODE_EXT)
    if not is_src_code:
        sys.exit(0)

    if already_notified() or has_plan():
        sys.exit(0)

    msg = (
        "⚠️  플랜 없이 `src/` 코드 생성이 시작됐다 — 하네스 워크플로우 미탑승 신호. "
        "한 문장 diff(오타·rename 급)면 그대로 진행하고, 그 이상이면 지금 "
        f"`{PLUGIN_ROOT}/docs/WORKFLOW.md` 와 `{PLUGIN_ROOT}/docs/RULES.md` 를 Read 하라. "
        "핵심 3가지: ① 새 기능·다파일은 planner 가 `docs/plans/` 에 슬라이스 플랜을 먼저 쓴다 "
        "② 구현은 메인 세션이 직접(약한 모델 서브에이전트 위임 금지, RULES §7) "
        "③ 슬라이스 완료 시 기계 검증(typecheck/lint/test)만으로 '완료'가 아니다 — "
        "변경 성격 매칭 렌즈 1개(구조·경계→structure-fitness-reviewer / 보안·DB→stability / "
        "UI→craft)를 적대 리뷰로 돌려야 머지 *후보*가 된다. "
        "(이 안내는 프로젝트당 1회만 나온다.)"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }}, ensure_ascii=False))
    mark_notified()
    sys.exit(0)


if __name__ == "__main__":
    main()
