#!/usr/bin/env python3
"""PreToolUse·PostToolUse(Bash) hook — 작업 트리를 바꿀 수 있는 명령 직전에 스냅샷을 남기고, 끝난 뒤
사라진 파일을 알린다(차단하지 않는다).

왜: block-destructive-git 은 표기 기반이라 재현된 우회가 남아 있다(README 「알려진 한계」). 파서를 더
조이면 과차단이 늘어난다. 이 훅은 판정 대신 복구 경로를 보장한다 — 어떤 표기로 지웠든 직전 상태가
`refs/harness/snapshots/` 에 있다(hooks/gitsnap.py).

언제 찍나: **읽기 전용으로 확인된 명령만 건너뛴다.** 모르는 명령은 찍는다 — 우회 표기(eval·인터프리터·
스크립트)가 "모르는 명령" 쪽으로 떨어지게 하려는 기본값이다. 같은 트리면 새 객체를 만들지 않는다.
비용: 파일 1,300개 레포에서 250–450ms(실측). git 레포가 아니거나 8초를 넘기면 건너뛴다(fail-open).

사후(PostToolUse): 직전 스냅샷 표식(<gitdir>/harness-last-snapshot, 같은 명령·15분 이내)이 있으면 지금
트리와 비교해 사라진 경로를 복구 명령과 함께 알린다 — `./reset.sh` 안의 `git clean` 처럼 명령 문자열로는
삭제가 안 보이는 경우를 결과로 잡는다.

한계: gitignore 대상(.env·로컬 데이터)과 레포 밖 파일은 보호하지 않는다. 원격 이력은 pre-push 훅
(scripts/install-git-hooks.sh)이 맡는다.
"""
import hashlib
import json
import os
import re
import sys
import time

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


def _marker(repo: str):
    gd = gitsnap._git(repo, "rev-parse", "--absolute-git-dir")
    return os.path.join(gd, "harness-last-snapshot") if gd else None


def _repos(cmd: str, cwd: str, project: str) -> list:
    """하위 명령마다 그 시점의 위치(`cd x && …`)로 저장소를 정한다. 프로젝트의 저장소(모노레포면 상위 루트)나
    프로젝트 안의 저장소만 — 홈 디렉터리를 관리하는 dotfiles 저장소 같은 남의 저장소에는 ref 를 쓰지 않는다."""
    home_repo = gitsnap.repo_root(project)
    home_repo = os.path.realpath(home_repo) if home_repo else None
    repos, cache = [], {}
    for seg, here in shellparse.segments(cmd, cwd):
        here = os.path.realpath(here) if os.path.isdir(here) else here
        if here not in cache:
            r = gitsnap.repo_root(here) if os.path.isdir(here) else None
            cache[here] = os.path.realpath(r) if r else None
        repo = cache[here]
        if not repo or not (repo == home_repo or repo == project or repo.startswith(project + os.sep)):
            continue
        if not _read_only(seg, here, repo) and repo not in repos:
            repos.append(repo)
    return repos


def _key(cmd: str) -> str:
    return hashlib.sha1(cmd.encode("utf-8", "replace")).hexdigest()


def pre(cmd: str, repos: list) -> None:
    shas = []
    for r in repos:
        sha = gitsnap.snapshot(r, "harness snapshot before: " + cmd[:200])
        m = _marker(r)
        if sha and m:
            shas.append(sha)
            try:
                with open(m, "w") as fh:
                    json.dump({"sha": sha, "key": _key(cmd), "ts": time.time()}, fh)
            except OSError:
                pass
    if shas and LOSSY.search(shellparse.prep(cmd)):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"하네스가 실행 직전 작업 트리를 스냅샷으로 남겼다: `{shas[0][:12]}` (refs/harness/snapshots/, "
                "미추적 파일 포함·gitignore 제외). 의도치 않게 지워졌으면 파일 단위로 "
                f"`git show {shas[0][:12]}:<경로> > <경로>` 로 되살린다."),
        }}, ensure_ascii=False))


def post(cmd: str, repos: list) -> None:
    """명령이 끝난 뒤, 직전 스냅샷에 있던 파일이 사라졌으면 알린다. 스크립트·별칭처럼 명령 문자열로는
    삭제가 드러나지 않는 경우에도 결과로 잡는다. 의도한 삭제일 수 있으니 막지 않고 복구 경로만 준다."""
    notes = []
    for r in repos:
        m = _marker(r)
        try:
            rec = json.load(open(m))
            os.remove(m)
        except Exception:
            continue
        if rec.get("key") != _key(cmd) or time.time() - rec.get("ts", 0) > 900:
            continue
        now = gitsnap.worktree_tree(r)
        before = gitsnap._git(r, "rev-parse", rec["sha"] + "^{tree}")
        if not now or not before or now == before:
            continue
        gone = gitsnap.changed_paths(r, before, now, only="D")
        if gone:
            listing = ", ".join(f"`{p}`" for p in gone[:10]) + (f" 외 {len(gone) - 10}개" if len(gone) > 10 else "")
            notes.append(f"{os.path.basename(r)}: {listing} — 복구: `git -C {r} show {rec['sha'][:12]}:<경로> > <경로>`")
    if notes:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "방금 명령 뒤에 실행 직전 스냅샷에 있던 파일이 사라졌다(하네스 스냅샷 대비):\n" + "\n".join(notes)
                + "\n의도한 삭제였더라도 사라진 파일 목록과 위 복구 명령을 답변에 적어 사용자에게 알린다(미추적 파일은"
                  " 이 스냅샷 말고는 사본이 없다). 사용자가 원하지 않은 삭제가 분명하면 바로 되살린다."),
        }}, ensure_ascii=False))


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
    repos = _repos(cmd, cwd, project)
    if data.get("hook_event_name") == "PostToolUse":
        post(cmd, repos)
    else:
        pre(cmd, repos)
    sys.exit(0)


if __name__ == "__main__":
    main()
