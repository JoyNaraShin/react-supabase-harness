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

- Launcher prefixes are unwrapped before matching — `env`, `command`, `builtin`,
  `exec`, `sudo`, `doas`, `nohup`, `time`, `nice`, `timeout`, `stdbuf`, `xargs` — and an
  absolute/relative program path is reduced to its basename (`/usr/bin/git` → `git`).
- `find <root> -delete` / `-exec rm` on an absolute/home/parent/cwd root is treated like
  `rm -rf` on that root.

Scope: this is a cooperative guard against an agent's *accidental* destructive call, not a
security boundary. A determined process can always reach git another way (a script file,
another interpreter). See README "보안 경계가 아닌 것".

Bypass: a sub-command whose first token is `CLAUDE_COMMIT_APPROVED=1` followed by
`git commit` (emitted by the `/commit` skill after user approval) bypasses the
git-commit block ONLY — every other destructive op stays blocked even with it.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellparse  # noqa: E402

ENV_PREFIX = re.compile(r"^[A-Za-z_]\w*=")
SHORT_C = re.compile(r"^-[a-z]*c$")
SEPARATOR_CHARS = set("();|&")
OPERATORS = {"&&", "||", "|", "|&", ";", ";;", ";&", "&", "\n", "(", ")"}
WRAPPERS = {"bash", "sh", "zsh", "dash", "ash"}
# raw-scan fallback (parse failure or internal error → fail closed). Patterns, not substrings:
# a substring list missed `git commit` and `push -f` entirely, and one `--force-with-lease`
# anywhere used to excuse the whole command.
RISKY_RAW = [re.compile(p) for p in (
    r"\bgit\b[^;&|\n]*\bcommit\b",
    r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*(?:\s-[a-z]*f\b|--force(?!-with-lease)|--delete|--mirror|--prune|--no-verify|\s\+\S|\s:\S)",
    r"--hard\b",
    r"\brm\s+(?:\S+\s+)*-[a-zA-Z]*[rR]",
    r"\bfind\b[^;&|\n]*(?:-delete|-exec\s+rm)",
    r"\bgit\b[^;&|\n]*\b(?:clean|restore|checkout|switch|filter-branch|filter-repo|reflog|update-ref|worktree)\b",
    r"\bbranch\s+(?:\S+\s+)*-[a-zA-Z]*D",
    r"\bstash\s+(?:clear|drop)\b",
)]
APPROVED_COMMIT = "CLAUDE_COMMIT_APPROVED=1 git commit"


def raw_risky(command: str) -> bool:
    """Unparseable command: block on any destructive pattern. An approved commit at the very
    start is exempt from the commit pattern only — whatever is chained after it is still scanned."""
    if command.lstrip().startswith(APPROVED_COMMIT):
        command = command.lstrip()[len(APPROVED_COMMIT):]
    return any(rx.search(command) for rx in RISKY_RAW)


# 막힌 조작별 비파괴 대안. 이 훅에는 커밋 말고 승인 통로가 없으므로 "사용자 승인 필요"만으로는
# 에이전트가 할 수 있는 일이 없다 — 대안이나 사용자 직접 실행을 가리켜야 한다.
ALTERNATIVES = [
    ("git commit", "`/commit` 스킬로 커밋 단위를 제안하고 사용자 승인을 받는다."),
    ("verify FAIL", "고친 뒤 `/verify` 를 다시 돌려 PASS 기록을 받고 커밋한다."),
    ("--no-verify", "pre-push 훅이 막은 이유(기본 브랜치 강제 갱신·원격 삭제)를 사용자에게 보고한다."),
    ("--force", "`git push --force-with-lease` 를 쓴다(원격이 내가 본 상태일 때만 덮어쓴다)."),
    ("reset --hard", "`git stash` 로 작업을 보관한 뒤 이동한다."),
    ("checkout", "`git stash -u` 로 작업을 보관한다. 브랜치 이동이면 `git switch <브랜치>`."),
    ("switch", "`git stash -u` 로 작업을 보관한 뒤 전환한다."),
    ("restore", "`git stash -u` 로 작업을 보관한다(`git restore --staged` 언스테이지는 허용)."),
    ("clean", "지울 파일을 `git clean -n` 으로 확인해 사용자에게 보여 준다."),
    ("branch", "머지된 브랜치면 `git branch -d` 로 지운다."),
    ("rm -r", "지울 대상을 더 깊은 구체 경로로 좁힌다(예: `rm -rf ./dist`)."),
    ("find", "`-name` 등 필터로 대상을 좁히거나 구체 경로를 루트로 준다."),
]


def remediate(reason: str) -> str:
    alt = next((a for k, a in ALTERNATIVES if k in reason), "더 좁은 비파괴 방법을 찾는다.")
    return (reason.replace(" 사용자 승인 필요.", "").rstrip()
            + f"\n다음 행동: {alt} 그래도 이 명령이 필요하면 이유를 사용자에게 설명하고, "
              "사용자가 직접 `! <명령>` 으로 실행하게 하라.")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": remediate(reason),
    }}, ensure_ascii=False))
    sys.exit(0)


def normalize(command: str) -> str:
    """Make shell line structure explicit before shlex sees it.

    shlex treats a newline as plain whitespace, so a second line was never checked.
    Outside quotes: an unescaped newline becomes `;`,
    backslash-newline is a continuation, and a `#` that starts a word comments out the
    rest of the line (the shell never runs it, and an apostrophe inside a comment would
    otherwise break parsing).
    """
    out, i, n = [], 0, len(command)
    quote = None
    while i < n:
        c = command[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(c)
        elif c == "\\" and i + 1 < n:
            out.append(" " if command[i + 1] == "\n" else c + command[i + 1])
            i += 2
            continue
        elif c == "\n":
            out.append(" ; ")
        elif c == "#" and (i == 0 or command[i - 1] in " \t;&|("):
            while i < n and command[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def tokenize(command: str) -> list:
    lex = shlex.shlex(normalize(command), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""  # comments are already removed by normalize() — quote-aware
    return list(lex)


def split_subcommands(tokens: list) -> list:
    cur, out = [], []
    for t in tokens:
        # shlex 는 이어진 구두점을 한 토큰으로 붙인다(`$(echo /); rm …` → `);`). 구분 문자로만 된 토큰은 전부
        # 구분자다 — 아니면 뒤 명령이 앞 명령의 인자로 읽혀 검사를 건너뛴다(0.35.0 까지의 구멍, 차분 테스트가 찾음).
        if t in OPERATORS or (t and set(t) <= SEPARATOR_CHARS):
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


# Launchers that run their remaining argv as a command. Value = option flags that take
# a separate value argument (so the value is not mistaken for the program).
LAUNCHERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(), "builtin": set(), "exec": {"-a"}, "nohup": set(),
    "time": {"-f", "--format", "-o", "--output"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
             "-C", "--close-from", "-D", "--chdir", "-r", "--role", "-t", "--type"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-L", "-n", "-P", "-d", "-E", "-s", "-a", "--arg-file",
              "--delimiter", "--max-args", "--max-procs", "--replace"},
}
# Launchers whose first positional argument is not the program (timeout DURATION cmd).
LEADING_POSITIONAL = {"timeout": {"-s", "--signal", "-k", "--kill-after"}}


def basename(tok: str) -> str:
    return tok.rsplit("/", 1)[-1] if "/" in tok else tok


# Shell grammar words that precede a command: `while read b; do git branch -D "$b"; done`
# (the command after `do`/`then`/`{` used to go unchecked).
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "{", "}", "!", "done", "fi"}


def unwrap(tokens: list) -> list:
    """Strip env assignments and launcher prefixes until the real program is first."""
    for _ in range(16):  # bounded: `sudo env nohup git ...`
        tokens = strip_env(tokens)
        while tokens and tokens[0] in SHELL_KEYWORDS:
            tokens = tokens[1:]
        if not tokens:
            return tokens
        head = basename(tokens[0])
        if head in LAUNCHERS or head in LEADING_POSITIONAL:
            valued = LAUNCHERS[head] if head in LAUNCHERS else LEADING_POSITIONAL[head]
            i = 1
            while i < len(tokens):
                t = tokens[i]
                if t == "--":
                    i += 1
                    break
                if t in valued:
                    i += 2
                elif t.startswith("-") or (head == "env" and ENV_PREFIX.match(t)):
                    i += 1
                else:
                    break
            if head in LEADING_POSITIONAL:
                i += 1  # skip DURATION
            tokens = tokens[i:]
            continue
        return [head] + tokens[1:]
    return tokens


SYSTEM_TOP = {"/", "/usr", "/etc", "/bin", "/sbin", "/lib", "/opt", "/var", "/System", "/Library",
              "/Applications", "/Users", "/home", "/root", "/private", "/Volumes", "/mnt", "/srv", "/tmp"}


def dangerous_root(t: str) -> bool:
    """A path whose recursive deletion is never a routine cleanup.

    Deep paths (`/tmp/build-x`, `/Users/me/proj/dist`, `~/.cache/tool/x`) are routine and
    pass. What stays blocked: system tops and their direct children, a home
    directory and its top-level folders (`~/Projects`), anything outside the project
    (`../x`), the cwd itself, `.git`, and a bare variable that may expand to empty
    (`"$DIR"/`, `$PWD`, `$(pwd)`).
    """
    t = t.strip("'\"")
    if re.fullmatch(r"(\$\{?\w+\}?|\$\(\s*pwd\s*\)|`pwd`)[/*]*", t):
        return True
    # 디렉터리 스택 틸드: `~+` = cwd, `~-`·`~N` = 이전·스택 위치(알 수 없음) — 생성형 차분 테스트가 찾음
    if re.match(r"~[+-]?\d*(?:/|$)", t) and t[:2] in ("~+", "~-") or re.match(r"~\d", t):
        if t.startswith("~+"):
            t = "." + t[2:]
        else:
            return True
    # `..` 는 경로를 위로 올린다 — `/tmp/a/../..` 는 `/`, `x/..` 는 cwd 다(0.35.0 까지 통과하던 구멍).
    if ".." in t.split("/") and not t.startswith("$"):
        if t.startswith("~"):                                # `~/a/../..` — 홈 기준으로 푼다
            rest = os.path.normpath(t[2:] or ".") if t.startswith("~/") else None
            if rest is None or rest == ".." or rest.startswith("../"):
                return True                                  # 홈 위로 올라간다
            t = "~" if rest == "." else "~/" + rest
        else:
            n = os.path.normpath(t)
            if not t.startswith("/") and (n == "." or n == ".." or n.startswith("../")):
                return True
            t = n
    t = t.rstrip("/") or "/"
    if t in (".", "..", "*", "./*", ".git", "./.git", "~", "~/*", "$HOME", "${HOME}",
             "$HOME/*", "${HOME}/*") or re.fullmatch(r"(\.\./)*\.\.(/\*)?", t):
        return True
    if re.match(r"(~|\$HOME|\$\{HOME\})/[^/]+/?\*?$", t):
        return True                                  # ~/Projects — a whole top-level folder
    if t.startswith("../"):
        return True                                  # outside the project root
    if t.startswith("/"):
        parts = [p for p in t.split("/") if p and p != "*"]
        if t.rstrip("*").rstrip("/") in SYSTEM_TOP or len(parts) <= 1:
            return True
        if "/" + parts[0] in {"/Users", "/home"}:
            return len(parts) <= 3                   # /Users/me, /Users/me/Projects
        return "/" + parts[0] in SYSTEM_TOP and parts[0] != "tmp" and len(parts) <= 2  # /usr/local
    return False


# 이 하위 명령이 실행될 때 있을 수 있는 위치들 — 원래 cwd 기준 상대 경로("."), 절대 경로, `~…`, 모르면 None.
HERE = [["."]]
CMD = [""]


def _join(here: str, t: str) -> str:
    return here + "/" + t if here.startswith("~") else os.path.normpath(os.path.join(here, t))


def brace_expand(t: str, limit: int = 128) -> list:
    """bash 중괄호 확장(`a{,/..}`·`{1..3}`) — 삭제 대상 하나가 여러 경로가 된다(생성형 차분 테스트가 찾음)."""
    m = None
    depth, start = 0, -1
    for i, ch in enumerate(t):
        if ch == "{" and (i == 0 or t[i - 1] != "$"):
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                m = (start, i)
                break
    if not m:
        return [t]
    pre, body, post = t[:m[0]], t[m[0] + 1:m[1]], t[m[1] + 1:]
    items, d, cur = [], 0, ""
    for ch in body:                                          # 최상위 쉼표로 나눈다
        if ch == "," and d == 0:
            items.append(cur)
            cur = ""
            continue
        d += ch == "{"
        d -= ch == "}"
        cur += ch
    items.append(cur)
    if len(items) == 1:
        r = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", body)
        if not r:
            return [t]                                       # `{x}` 는 확장되지 않는다
        a, b = int(r.group(1)), int(r.group(2))
        items = [str(k) for k in (range(a, b + 1) if a <= b else range(a, b - 1, -1))][:limit]
    out = []
    for it in items:
        out += brace_expand(pre + it + post, limit)
        if len(out) >= limit:
            break
    return out[:limit]


def rm_candidates(t: str) -> list:
    """삭제 대상이 실제로 가리킬 수 있는 경로들. 하나라도 위험하면 막는다(None = 위치를 몰라 알 수 없음).
    - 모르는 변수는 비어 있을 수 있다: `$X/tmp` 는 `/tmp` 일 수 있다(0.35.0 까지 통과하던 구멍).
    - 앞선 `cd` 가 위치를 바꿨으면 상대 경로는 거기서 푼다: `cd / && rm -rf build` 는 `/build` 다."""
    out = [t]
    names = re.findall(r"\$\{?([A-Za-z_]\w*)", t)
    # 위치·특수 매개변수(`$@`·`$1`·`$*`)는 `set --`·`shift`·함수 인자로 바뀐다 — 값을 알 수 없다
    if re.search(r"\$\{?[0-9@*#?$!-]", t) or any(shellparse.is_tainted(n, CMD[0]) for n in names):
        return [None]                                        # 명령 안에서 바뀌는 변수 — 값을 알 수 없다
    if "$" in t and not re.fullmatch(r"(\$\{?\w+\}?|\$\(\s*pwd\s*\)|`pwd`)[/*]*", t.strip("'\"")):
        # 비어 있을 때의 값 — `${X:-/tmp}`·`${X-/tmp}` 는 기본값이 들어간다(빈 값이 아니다)
        empty = shellparse.VAR_ANY.sub(
            lambda m: (re.match(r"\$\{\w+:?-([^}$`]*)\}$", m.group(0)) or [None, ""])[1], t)
        if empty:
            out.append(empty)
    out = [b for c in out for b in brace_expand(c)]
    res = []
    for c in out:
        if c.startswith(("/", "~", "$")):
            res.append(c)
            continue
        for here in HERE[0]:
            res.append(None if here is None else (c if here == "." else _join(here, c)))
    return res


def track_cd(tokens: list) -> bool:
    """`cd`·`pushd`·`popd` 면 HERE 를 갱신하고 True. 모르는 변수만 있는 인자는 비어 있을 수 있다(`cd` = 홈)."""
    toks = unwrap(strip_env(tokens))
    if not toks or toks[0] not in ("cd", "pushd", "popd"):
        return False
    args = [a for a in toks[1:] if not a.startswith("-") or a == "-"]
    before = HERE[0]
    if toks[0] == "popd" or (toks[0] == "pushd" and (not args or re.fullmatch(r"[+-]\d*", args[0]))):
        new = [None]                                         # 스택에서 꺼낸 위치 — 알 수 없다
    else:
        d = args[0] if args else "~"
        if d == "-" or "`" in d or "$(" in d or d.startswith(("~-", "~+")):
            new = [None]
        elif "$" in d:
            new = [None] + (["~"] if not shellparse.VAR_ANY.sub("", d) else [])
        elif d.startswith(("/", "~")):
            new = [os.path.normpath(d) if d.startswith("/") else d]
        else:
            new = [None if h is None else (d if h == "." else _join(h, d)) for h in before]
    # 교체가 아니라 합집합 — `( cd / )`·`{ cd /; } &`·`cd x || …` 는 현재 셸 위치를 바꿀 수도 안 바꿀 수도 있다.
    # 가능한 위치를 넉넉히 잡으면 판정은 보수적이 될 뿐 틀리지 않는다.
    HERE[0] = list(dict.fromkeys(before + new))
    return True


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
    tokens = unwrap(tokens)
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
            return ("git commit 직접 실행 금지. `/commit` 스킬로 커밋 단위를 제안하고 "
                    "사용자 승인을 받은 뒤 그 스킬이 실행한다.")
        if sub == "push":
            # pre-push 훅(scripts/install-git-hooks.sh)이 결과로 막는 강제 갱신을 훅 건너뛰기로 피하지 못하게
            if "--no-verify" in rest or any("hookspath" in t.lower() for t in tokens[1:gi]):
                return "git push --no-verify/core.hooksPath 변경(pre-push 훅 건너뛰기) 금지. 사용자 승인 필요."
            for a in rest:
                if a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a):
                    return "git push --force/-f 금지(강제 push). 사용자 승인 필요. (`--force-with-lease` 허용)"
                if a in ("--delete", "--mirror", "--prune") or (
                        a.startswith("-") and not a.startswith("--") and "d" in a):
                    return f"git push {a}(원격 ref 삭제·덮어쓰기) 금지. 사용자 승인 필요."
                if a.startswith("+"):
                    return "git push +refspec(강제) 금지. 사용자 승인 필요."
                if len(a) > 1 and a.startswith(":"):
                    return "git push :branch(원격 브랜치 삭제) 금지. 사용자 승인 필요."
            return None
        if sub == "reset" and "--hard" in rest:
            return "git reset --hard 금지(파괴적). 사용자 승인 필요."
        if sub == "checkout":
            creates = any(a in ("-b", "-B", "--orphan") for a in rest)
            positional = [a for a in rest if not a.startswith("-")]
            if not creates and ("--" in rest or rest[:1] == ["HEAD"] or "." in rest
                                or "--force" in rest or has_short_flag(rest, "f")
                                or len(positional) >= 2):  # `checkout <tree-ish> <path>`
                return "git checkout <ref> <path>/./--/-f(작업 폐기) 금지. 사용자 승인 필요."
        if sub == "switch" and ("--discard-changes" in rest or "--force" in rest
                                or has_short_flag(rest, "f")):
            return "git switch --discard-changes/-f(작업 폐기) 금지. 사용자 승인 필요."
        if sub == "stash" and rest[:1] in (["clear"], ["drop"]):
            return f"git stash {rest[0]}(스태시 영구 삭제) 금지. 사용자 승인 필요."
        if sub == "restore" and not ("--staged" in rest and "--worktree" not in rest
                                     and not has_short_flag(rest, "W")):
            return "git restore(작업 트리 폐기) 금지. 사용자 승인 필요. (`--staged` 언스테이지는 허용)"
        if sub in ("filter-branch", "filter-repo"):
            return f"git {sub}(이력 재작성) 금지. 사용자 승인 필요."
        if sub == "reflog" and rest[:1] in (["expire"], ["delete"]):
            return "git reflog expire/delete(복구 경로 삭제) 금지. 사용자 승인 필요."
        if sub == "update-ref" and ("-d" in rest or "--delete" in rest):
            return "git update-ref -d(ref 삭제) 금지. 사용자 승인 필요."
        if sub == "worktree" and rest[:1] == ["remove"] and ("--force" in rest or has_short_flag(rest, "f")):
            return "git worktree remove --force 금지. 사용자 승인 필요."
        if sub in ("revert", "cherry-pick") and "--no-commit" not in rest and "-n" not in rest:
            return f"git {sub} 는 커밋을 만든다 — `/commit` 승인 절차를 거친다. (`--no-commit` 은 허용)"
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
        if has_r:  # -f 유무와 무관 — `rm -r ~` 도 같은 결과다
            for t in targets:
                cands = rm_candidates(t)
                if None in cands:
                    return ("rm -r 의 실제 대상을 판정할 수 없다 — 명령 안에서 값이 바뀌는 변수가 들어 있거나, "
                            "앞선 cd 의 목적지를 모른다(명령 치환·변수). "
                            "다음 행동: 리터럴 절대 경로로 지정하라(예: rm -rf /abs/path/dist).")
                if any(dangerous_root(c) for c in cands):
                    return "rm -r on system/home/cwd/parent/.git path 금지. 사용자 승인 필요."
        return None

    if head == "find":
        roots = []
        for a in tokens[1:]:
            if a.startswith("-") or a in ("(", "!"):
                break
            roots.append(a)
        destructive = "-delete" in tokens or any(
            tokens[i] in ("-exec", "-execdir", "-ok") and i + 1 < len(tokens)
            and basename(tokens[i + 1]) == "rm" for i in range(len(tokens)))
        filtered = any(t in ("-name", "-iname", "-path", "-ipath", "-regex", "-mtime",
                             "-newer", "-size", "-empty") for t in tokens)
        risky = [r for r in roots or ["."]
                 if dangerous_root(r) and not (r in (".", "./") and filtered)]
        if destructive and risky:
            return "find <시스템·홈·상위 경로, 또는 필터 없는 .> -delete/-exec rm 금지. 사용자 승인 필요."
        return None

    return None


HEREDOC = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
SHELL_SINK = re.compile(r"\|\s*(?:\S*/)?(?:bash|sh|zsh|dash|ash)\b|\|\s*xargs\b")


def _feeds_shell(segment: str) -> bool:
    """Does the program on this command-line segment execute its stdin as a script?"""
    # `git commit -m "$(cat <<'EOF' …)"` — the heredoc feeds `cat` inside the substitution,
    # not git or a shell (the standard approved-commit form used to be denied).
    for opener in ("$(", "`"):
        if opener in segment:
            segment = segment.rsplit(opener, 1)[1]
    try:
        head = unwrap(tokenize(re.split(r"&&|\|\||;|\|", segment)[-1]))[:1]
    except ValueError:
        return True  # unparsable → treat the body as code (fail closed)
    return bool(head) and (head[0] in WRAPPERS or head[0] == "eval")


def split_heredocs(command: str):
    """Separate heredoc bodies from the command line.

    A heredoc body is *data* for most programs (`cat > f <<EOF`, `git commit -F- <<EOF`) —
    tokenizing it as shell turns prose like "(git push --force 금지)" into a command.
    When the receiver is a shell — `bash <<EOF` or
    `cat <<EOF | sh` — the body IS a script and is still scanned.
    Returns (command_without_bodies, scripts_to_scan).
    """
    scripts = []

    def repl(m):
        line_start = command.rfind("\n", 0, m.start()) + 1
        before, after = command[line_start:m.start()], m.group(3)
        if _feeds_shell(before) or SHELL_SINK.search(after):
            scripts.append(m.group(4))
        return "<<" + m.group(2) + after + "\n"

    return HEREDOC.sub(repl, command), scripts


SINGLE_QUOTED = re.compile(r"'[^']*'")
BACKTICK = re.compile(r"`([^`]+)`")
HERESTRING = re.compile(r"\b(?:bash|sh|zsh|dash|ash)\s+<<<\s*(['\"])(.*?)\1", re.S)
PIPED_TO_SHELL = re.compile(
    r"\b(?:echo|printf)\s+(['\"])(.*?)\1\s*\|\s*(?:\S*/)?(?:bash|sh|zsh|dash|ash)\b", re.S)


def embedded_scripts(command: str) -> list:
    """명령 문자열 안에서 셸이 실행할 다른 명령 — 백틱 치환, 셸로 가는 here-string, 셸로 파이프되는
    echo/printf 문자열. 작은따옴표 안의 백틱은 리터럴이므로 제외한다."""
    out = [m.group(1) for m in BACKTICK.finditer(SINGLE_QUOTED.sub("''", command))]
    out += [m.group(2) for m in HERESTRING.finditer(command)]
    out += [m.group(2) for m in PIPED_TO_SHELL.finditer(command)]
    return out


def scan(command: str):
    """Tokenize a command string and check each sub-command. Fail closed on error."""
    if not command:
        return None
    command, scripts = split_heredocs(command)
    scripts = scripts + embedded_scripts(command)
    for body in scripts:
        reason = scan(body)
        if reason:
            return reason
    # 같은 명령 맨 앞에서 확정된 변수만 bash 처럼 펼친다(shellparse) — `S=/tmp/x && rm -rf $S` 의 대상은 깊은
    # 경로다. 재대입·조건부·명령 치환·값을 바꾸는 builtin 이 끼면 모르는 값으로 남아 계속 막힌다(fail closed).
    command = shellparse.expand_command(command, shellparse.leading_assignments(command))
    # 따옴표 없는 `$(pwd)` 는 토크나이저가 `$`·`(`·`pwd`·`)` 로 쪼개 경로 판정을 피해 간다.
    command = re.sub(r"\$\(\s*pwd\s*\)|`\s*pwd\s*`", "$PWD", command)
    try:
        tokens = tokenize(command)
    except ValueError:
        if raw_risky(command):
            return ("파싱 불가 명령에 파괴적 패턴 포함 — 안전을 위해 차단(사용자 승인 필요). "
                    "따옴표를 단순하게 하거나 커밋 메시지는 `-F <파일>` 로 넘겨라.")
        return None
    HERE[0], CMD[0] = ["."], command
    for sub in split_subcommands(tokens):
        if track_cd(sub):
            continue
        # commit 승인 우회: 정확히 `CLAUDE_COMMIT_APPROVED=1 git commit ...` 만.
        if len(sub) >= 3 and sub[0] == "CLAUDE_COMMIT_APPROVED=1" and sub[1] == "git" and sub[2] == "commit":
            failed = verify_failed()
            if failed:
                return failed
            continue
        reason = check(strip_env(sub))
        if reason:
            return reason
    return None


def verify_failed():
    """훅이 기록한 마지막 verifier 판정(subagent-audit → .claude/state/verdicts.jsonl)이 지금 HEAD 위의 FAIL 이면
    승인 커밋도 막는다. 기록은 에이전트가 쓸 수 없는 파일이라 대화 속 "검증 통과" 주장과 다르다.
    기록이 없거나 그 뒤 커밋이 있었으면(HEAD 가 다르면) 판단하지 않는다."""
    import subprocess
    root = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        lines = open(os.path.join(root, ".claude", "state", "verdicts.jsonl"), encoding="utf-8").read().splitlines()
    except Exception:
        return None
    last = None
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("agent_type") == "verifier":
            last = r
    if not last or last.get("verdict") != "FAIL":
        return None
    try:
        head = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except Exception:
        return None
    if head and last.get("head") == head:
        return ("verify FAIL 기록 위의 커밋 — 마지막 verifier 판정이 FAIL 이고 그 뒤 PASS 가 없다"
                "(.claude/state/verdicts.jsonl, 훅 기록). 사용자 승인 필요.")
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    try:
        reason = scan(cmd)
    except Exception:  # an internal bug must not silently open the gate
        reason = ("파괴적 명령 판정 중 훅 내부 오류 — 안전을 위해 차단(사용자 승인 필요)."
                  if raw_risky(cmd) else None)
    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
