#!/usr/bin/env python3
"""PreToolUse hook — blocks destructive git/rm unless explicitly approved.

Reads Claude Code hook JSON from stdin, inspects tool_input.command, and DENIES
destructive operations using the CURRENT PreToolUse contract
(`hookSpecificOutput.permissionDecision: "deny"`).

Fail-closed design:
- The command is tokenized with shlex (punctuation-aware), so quoted strings stay
  intact and operators like `a&&b` split correctly. This means a destructive token
  inside a *quoted* string of an unrelated command (e.g. `echo "rm -rf /"`) does NOT
  false-positive, while a real wrapper payload cannot hide behind quotes.
- `sh|bash|zsh -c <script>` and `eval <script>` wrappers are scanned RECURSIVELY,
  closing the previous quote-stripping bypass (`bash -c "git push --force"`).
- git is matched token-aware (skips global opts like `-C <path>`), so `git -C . commit`,
  long-form flags (`--force`, `--delete --force`), reversed flags (`rm -fr`) and remote
  branch deletion (`git push origin :main`) are all caught.
- On a shlex parse error the hook fails CLOSED: if the raw command contains a
  destructive keyword it is blocked.

Bypass: a sub-command whose first token is `CLAUDE_COMMIT_APPROVED=1` followed by
`git commit` (emitted by the `/commit` skill after user approval) bypasses the
git-commit block ONLY — every other destructive op stays blocked even with it.
"""
import json
import re
import shlex
import sys

ENV_PREFIX = re.compile(r"^[A-Za-z_]\w*=")
SHORT_C = re.compile(r"^-[a-z]*c$")
OPERATORS = {"&&", "||", "|", ";", "&", "\n", "(", ")"}
WRAPPERS = {"bash", "sh", "zsh", "dash", "ash"}
# raw-scan fallback keywords (parse failure → fail closed)
RISKY_RAW = ("--force", "--hard", "rm -r", "rm -f", "rm -fr", "git clean",
             "branch -D", "git restore", "checkout --", "push origin :")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def tokenize(command: str) -> list:
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""  # never treat '#' as a comment (could hide a payload)
    return list(lex)


def split_subcommands(tokens: list) -> list:
    cur, out = [], []
    for t in tokens:
        if t in OPERATORS:
            if cur:
                out.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def strip_env(tokens: list) -> list:
    i = 0
    while i < len(tokens) and ENV_PREFIX.match(tokens[i]):
        i += 1
    return tokens[i:]


def git_sub_index(tokens: list) -> int:
    """Index of git's subcommand token, skipping value-taking global options."""
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-C", "--git-dir", "--work-tree", "-c", "--namespace"):
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            return i
    return -1


def has_short_flag(args: list, ch: str) -> bool:
    return any(a.startswith("-") and not a.startswith("--") and ch in a for a in args)


def check(tokens: list):
    """Return a denial reason for these (single sub-command) tokens, else None."""
    if not tokens:
        return None
    head = tokens[0]

    if head == "eval":
        return scan(" ".join(tokens[1:]))

    if head in WRAPPERS:
        for i, t in enumerate(tokens[1:], 1):
            if t == "-c" or SHORT_C.match(t):
                if i + 1 < len(tokens):
                    return scan(tokens[i + 1])
                break
        return None

    if head == "git":
        gi = git_sub_index(tokens)
        if gi < 0:
            return None
        sub, rest = tokens[gi], tokens[gi + 1:]
        if sub == "commit":
            return ("git commit 직접 실행 금지. `/commit` 스킬로 사용자 승인 후 실행. "
                    "(우회: `CLAUDE_COMMIT_APPROVED=1 git commit ...`)")
        if sub == "push":
            for a in rest:
                if a in ("--force", "-f"):
                    return "git push --force/-f 금지(강제 push). 사용자 승인 필요. (`--force-with-lease` 허용)"
                if a.startswith("+"):
                    return "git push +refspec(강제) 금지. 사용자 승인 필요."
                if len(a) > 1 and a.startswith(":"):
                    return "git push :branch(원격 브랜치 삭제) 금지. 사용자 승인 필요."
            return None
        if sub == "reset" and "--hard" in rest:
            return "git reset --hard 금지(파괴적). 사용자 승인 필요."
        if sub == "checkout" and ("--" in rest or rest[:1] == ["HEAD"]):
            return "git checkout --/HEAD <path>(작업 폐기) 금지. 사용자 승인 필요."
        if sub == "restore":
            return "git restore(파일 폐기) 금지. 사용자 승인 필요."
        if sub == "clean" and ("--force" in rest or has_short_flag(rest, "f")):
            return "git clean -f/--force(untracked 파괴) 금지. 사용자 승인 필요."
        if sub == "branch":
            if "-D" in rest:
                return "git branch -D(강제 삭제) 금지. 사용자 승인 필요."
            if ("--delete" in rest or "-d" in rest) and ("--force" in rest or has_short_flag(rest, "f")):
                return "git branch --delete --force 금지. 사용자 승인 필요."
        return None

    if head == "rm":
        flags = [a for a in tokens[1:] if a.startswith("-")]
        targets = [a for a in tokens[1:] if not a.startswith("-")]
        has_r = "--recursive" in flags or has_short_flag(flags, "r") or has_short_flag(flags, "R")
        has_f = "--force" in flags or has_short_flag(flags, "f")
        if has_r and has_f:
            for t in targets:
                if t.startswith(("/", "~", "$HOME")) or t == ".." or t.startswith("../"):
                    return "rm -rf on absolute/home/parent path 금지. 사용자 승인 필요."
        return None

    return None


def scan(command: str):
    """Tokenize a command string and check each sub-command. Fail closed on error."""
    if not command:
        return None
    try:
        tokens = tokenize(command)
    except ValueError:
        if command.lstrip().startswith("CLAUDE_COMMIT_APPROVED=1 git commit"):
            return None  # approved commit; message may contain shell-confusing chars
        if any(k in command for k in RISKY_RAW) and "--force-with-lease" not in command:
            return "파싱 불가 명령에 파괴적 키워드 포함 — 안전을 위해 차단(사용자 승인 필요)."
        return None
    for sub in split_subcommands(tokens):
        # commit 승인 우회: 정확히 `CLAUDE_COMMIT_APPROVED=1 git commit ...` 만.
        if len(sub) >= 3 and sub[0] == "CLAUDE_COMMIT_APPROVED=1" and sub[1] == "git" and sub[2] == "commit":
            continue
        reason = check(strip_env(sub))
        if reason:
            return reason
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    reason = scan(cmd)
    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
