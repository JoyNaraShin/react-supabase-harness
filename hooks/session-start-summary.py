#!/usr/bin/env python3
"""SessionStart hook — 세션 시작 시 현재 브랜치·Phase·미커밋 상태 3줄 요약 주입.

stdout 으로 `additionalContext` JSON 을 출력하면 Claude Code 가 세션 시작
context 에 첨부한다. Phase 추정은 브랜치명 또는 `docs/plans/phase-N-*.md` 기반.
"""
import json
import re
import subprocess
import sys
from pathlib import Path


def run(cmd, timeout=1.5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout.strip()
    except Exception:
        return ""


def current_branch() -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "(no git)"


def ahead_behind(branch: str) -> str:
    """origin/main 기준 ahead/behind 커밋 수."""
    if branch in ("main", "(no git)"):
        return ""
    raw = run(["git", "rev-list", "--left-right", "--count", f"origin/main...{branch}"])
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) == 2:
        behind, ahead = parts
        if int(ahead) == 0 and int(behind) == 0:
            return "synced with origin/main"
        segs = []
        if int(ahead) > 0:
            segs.append(f"ahead {ahead}")
        if int(behind) > 0:
            segs.append(f"behind {behind}")
        return "vs origin/main: " + ", ".join(segs)
    return ""


def uncommitted_count() -> int:
    raw = run(["git", "status", "--short"])
    if not raw:
        return 0
    return len([line for line in raw.splitlines() if line.strip()])


# 브랜치명에 `phase` 리터럴이 든 경우만 phase 추정에 사용.
# 예: phase/3, feat/40-phase-3-stock — 매치 / 반례: feat/3-inventory — 매치 안 함
_BRANCH_PHASE_RE = re.compile(r"phase[/-](\d+)")


def current_phase(branch: str) -> str:
    """현재 진행 Phase 추정. 우선순위: 브랜치명 > docs/plans/ mtime."""
    if branch and branch not in ("main", "(no git)"):
        m = _BRANCH_PHASE_RE.search(branch)
        if m:
            return f"{m.group(1)} (브랜치명 기반)"

    try:
        plans_dir = Path("docs/plans")
        if not plans_dir.is_dir():
            return "?"
        files = sorted(
            plans_dir.glob("phase-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return "?"
        # Phase plan (phase-3-inventory.md) 우선, Epic plan (phase-3-epic-01-...) 차순위.
        for f in files:
            parts = f.stem.split("-")
            if len(parts) >= 2 and parts[0] == "phase" and parts[1].isdigit():
                if len(parts) > 2 and parts[2] == "epic":
                    continue
                return f"{parts[1]} (최근 수정 plan)"
        for f in files:
            parts = f.stem.split("-")
            if len(parts) >= 3 and parts[0] == "phase" and parts[1].isdigit():
                return f"{parts[1]} (Epic plan mtime)"
        return "?"
    except Exception:
        return "?"


def main() -> None:
    branch = current_branch()
    ab = ahead_behind(branch)
    phase = current_phase(branch)
    uncommitted = uncommitted_count()

    lines = [
        f"브랜치: {branch}" + (f" ({ab})" if ab else ""),
        f"Phase 추정: {phase}",
        f"미커밋: {uncommitted}개",
    ]
    print(json.dumps({"additionalContext": "\n".join(lines)}))
    sys.exit(0)


if __name__ == "__main__":
    main()
