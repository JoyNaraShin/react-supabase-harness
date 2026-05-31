#!/usr/bin/env python3
"""PreToolUse hook — blocks destructive git/rm unless explicitly approved.

Fires before each Bash tool call. Reads Claude Code hook JSON from stdin,
examines tool_input.command, and blocks known-destructive patterns.

Bypass: a subcommand prefixed with `CLAUDE_COMMIT_APPROVED=1 ` (set by the
`/commit` skill after user approval) bypasses the git-commit block ONLY —
push --force / reset --hard / rm -rf etc. remain blocked even with the prefix.
"""
import json
import re
import sys


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


CHECKS = [
    (
        r"(?:^|[^a-zA-Z_])git\s+commit(?:\s|$)",
        "git commit 직접 실행 금지. `/commit` 스킬로 사용자 승인 후 실행하세요. "
        "(우회: 해당 서브커맨드를 `CLAUDE_COMMIT_APPROVED=1 git commit ...` 형태로 시작)",
    ),
    (
        # `--force` 는 BLOCK, `--force-with-lease` 는 ALLOW (안전한 rebase push).
        r"git\s+push\s[^\n]*(?:--force(?!-with-lease)|(?<!\S)-f\b|(?<!\S)\+[A-Za-z0-9_./:-]+)",
        "git push --force / -f / +refspec 금지. 강제 push 는 명시적 사용자 승인 필요. `--force-with-lease` 는 허용.",
    ),
    (
        r"git\s+reset\s+--hard",
        "git reset --hard 금지. 파괴적 작업 — 사용자 승인 필요.",
    ),
    (
        r"git\s+checkout\s+--(?:\s|$)",
        "git checkout -- (작업 폐기) 금지. 사용자 승인 필요.",
    ),
    (
        r"(?:^|[^a-zA-Z_])git\s+checkout\s+HEAD\s",
        "git checkout HEAD <path> 금지. 파일 작업 폐기 — 사용자 승인 필요.",
    ),
    (
        r"(?:^|[^a-zA-Z_])git\s+restore\s",
        "git restore 금지. 파일 폐기 명령 — 사용자 승인 필요.",
    ),
    (
        r"(?:^|[^a-zA-Z_])git\s+clean\s+-[a-zA-Z]*f",
        "git clean -f* 금지. untracked 파일·디렉터리 파괴 — 사용자 승인 필요.",
    ),
    (
        r"git\s+branch\s+-D\s",
        "git branch -D (강제 삭제) 금지. 사용자 승인 필요.",
    ),
    (
        r"(?:^|[^a-zA-Z_])rm\s+-[rR]f?\s+(?:/|~/|\$HOME)",
        "rm -rf on absolute / home path 금지. 프로젝트 내부 상대경로면 문제 없음.",
    ),
    (
        # 상위 디렉터리 탈출. 중첩 ../ (예: `rm -rf ./build/../dist`) 는 허용.
        r"(?:^|[^a-zA-Z_])rm\s+-[rR]f?\s+\.\.(?:/|\s|$)",
        "rm -rf .. (상위 디렉터리 탈출) 금지. 프로젝트 밖 디렉터리 파괴 위험 — 사용자 승인 필요.",
    ),
]


def strip_quotes_and_heredocs(cmd: str) -> str:
    """커밋 메시지·문서 문자열 내부 토큰의 false positive 방지를 위해
    heredoc / 단일·이중 따옴표 내부를 제거한 skeleton 으로 축약."""
    cmd = re.sub(
        r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?[^\n]*\n.*?^\s*\1\s*$",
        "",
        cmd,
        flags=re.DOTALL | re.MULTILINE,
    )
    cmd = re.sub(r"'[^']*'", "", cmd)
    cmd = re.sub(r'"[^"]*"', "", cmd)
    return cmd


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd:
        sys.exit(0)

    skeleton = strip_quotes_and_heredocs(cmd)
    subcmds = re.split(r"(?:&&|\|\||;|\n)", skeleton)

    for sub in subcmds:
        sub = sub.strip()
        if not sub:
            continue
        # 정확히 `CLAUDE_COMMIT_APPROVED=1 git commit ...` 형태만 bypass (commit 한정).
        if re.match(r"^CLAUDE_COMMIT_APPROVED=1\s+git\s+commit(?:\s|$)", sub):
            continue
        for pattern, reason in CHECKS:
            if re.search(pattern, sub):
                block(reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
