#!/usr/bin/env python3
"""PreToolUse(Bash) hook — 작업 트리를 바꿀 수 있는 명령 직전에 스냅샷을 남긴다(차단하지 않는다).

왜: block-destructive-git 은 표기 기반이라 재현된 우회가 남아 있다(README 「알려진 한계」). 파서를 더
조이면 과차단이 늘어난다. 이 훅은 판정 대신 복구 경로를 보장한다 — 어떤 표기로 지웠든 직전 상태가
`refs/harness/snapshots/` 에 있다(hooks/gitsnap.py).

언제 찍나: **읽기 전용으로 확인된 명령만 건너뛴다.** 모르는 명령은 찍는다 — 우회 표기(eval·인터프리터·
스크립트)가 "모르는 명령" 쪽으로 떨어지게 하려는 기본값이다. 같은 트리면 새 객체를 만들지 않는다.
비용: 파일 1,300개 레포에서 250–450ms(실측). git 레포가 아니거나 8초를 넘기면 건너뛴다(fail-open).

한계: gitignore 대상(.env·로컬 데이터)과 레포 밖 파일은 보호하지 않는다. 원격 이력은 pre-push 훅
(scripts/install-git-hooks.sh)이 맡는다.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitsnap  # noqa: E402
import shellparse  # noqa: E402

READ_ONLY = {"ls", "cat", "head", "tail", "wc", "grep", "egrep", "rg", "ag", "echo", "printf", "pwd", "which",
             "type", "file", "stat", "du", "df", "tree", "diff", "less", "more", "jq", "true", "false", "test",
             "date", "whoami", "uname", "env", "printenv", "sort", "uniq", "cut", "basename", "dirname",
             "realpath", "readlink", "sleep", "column", "nl", "md5", "shasum", "sha256sum", "open", "gh"}
GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame", "describe",
            "shortlog", "cat-file", "grep", "fetch", "remote", "config", "for-each-ref", "reflog", "help",
            "merge-base", "name-rev", "version"}
FIND_WRITE = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
# 복구 안내를 붙일 명령 — 그 외에는 조용히 찍는다(컨텍스트 소모를 줄인다)
LOSSY = re.compile(r"\b(?:rm|shred|unlink|truncate|mv)\b|\bgit\b.*\b(?:reset|clean|checkout|restore|stash|switch)\b"
                   r"|\bfind\b.*-delete")


def _read_only(seg: str, here: str, repo: str) -> bool:
    toks = shellparse._tokens(seg)
    if not toks:
        return True
    head = toks[0].rsplit("/", 1)[-1]
    for t in shellparse.write_targets(seg):
        p = os.path.normpath(os.path.join(here, os.path.expanduser(t)))
        if p == repo or p.startswith(repo + os.sep):
            return False                               # 레포 안에 쓴다
    if head == "git":
        sub = next((t for t in toks[1:] if not t.startswith("-")), "")
        if sub == "branch":
            return not any(t in ("-d", "-D", "-m", "-M", "--delete", "--move", "-f", "--force") for t in toks)
        if sub == "stash":
            return toks[-1] in ("list", "show") or "list" in toks
        return sub in GIT_READ
    if head == "find":
        return not any(t in FIND_WRITE for t in toks)
    if head in ("sed", "perl", "awk"):
        return not any(t.startswith("-") and "i" in t.lstrip("-")[:3] for t in toks[1:])
    return head in READ_ONLY


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = str((data.get("tool_input") or {}).get("command") or "")
    project = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    cwd = str(data.get("cwd") or project)
    if not cmd.strip():
        sys.exit(0)
    # 하위 명령마다 그 시점의 위치(`cd x && …`)로 저장소를 정한다. 프로젝트의 저장소(모노레포면 상위 루트)나
    # 프로젝트 안의 저장소만 — 홈 디렉터리를 관리하는 dotfiles 저장소 같은 남의 저장소에는 ref 를 쓰지 않는다.
    home_repo = gitsnap.repo_root(project)
    repos, cache = [], {}
    for seg, here in shellparse.segments(cmd, cwd):
        here = os.path.realpath(here) if os.path.isdir(here) else here
        if here not in cache:
            r = gitsnap.repo_root(here) if os.path.isdir(here) else None
            cache[here] = os.path.realpath(r) if r else None
        repo = cache[here]
        if not repo or not (repo == (os.path.realpath(home_repo) if home_repo else None)
                            or repo == project or repo.startswith(project + os.sep)):
            continue
        if not _read_only(seg, here, repo) and repo not in repos:
            repos.append(repo)
    shas = [x for x in (gitsnap.snapshot(r, "harness snapshot before: " + cmd[:200]) for r in repos) if x]
    if shas and LOSSY.search(shellparse.prep(cmd)):
        sha = shas[0]
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"하네스가 실행 직전 작업 트리를 스냅샷으로 남겼다: `{sha[:12]}` (refs/harness/snapshots/, "
                "미추적 파일 포함·gitignore 제외). 의도치 않게 지워졌으면 파일 단위로 "
                f"`git show {sha[:12]}:<경로> > <경로>` 로 되살린다."),
        }}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
