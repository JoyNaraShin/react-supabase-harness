"""git 파괴 조작 정책 — 정적 가드(hooks/block-destructive-git.py)와 실행 시점 래퍼(bin/git)가 같이 쓴다.

입력은 `git` 다음의 인자 목록이다. 정적 가드는 명령 글자를 토큰으로 나눈 추측값을, 래퍼는 bash 가 해석을 끝낸
**실제 인자**를 넘긴다. 판정 규칙은 여기 한 곳에만 있다 — 두 집행 지점이 서로 다른 규칙을 갖지 않게.
"""
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellparse  # noqa: E402  — 값을 받는 git 전역 옵션 표는 shellparse 한 곳에만 둔다


# git 내장 명령(`git --list-cmds=builtins`, 2.53) — git 은 내장 명령과 같은 이름의 별칭을 무시한다.
# 그래서 이 이름은 별칭으로 펼치지 않는다(`-c alias.push=status push --force` 는 진짜 push 다).
# 대소문자를 가리지 않는 파일 시스템에서는 `git Push` 가 git-push 로 풀릴 수 있어 소문자로 맞춰 본다.
BUILTIN = frozenset("""
add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr check-ignore
check-mailmap check-ref-format checkout checkout--worker checkout-index cherry cherry-pick clean clone column commit
commit-graph commit-tree config count-objects credential credential-cache credential-store describe diagnose diff
diff-files diff-index diff-pairs diff-tree difftool fast-export fast-import fetch fetch-pack fmt-merge-msg for-each-ref
for-each-repo format-patch fsck fsck-objects gc get-tar-commit-id grep hash-object help hook index-pack init init-db
interpret-trailers last-modified log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base
merge-file merge-index merge-ours merge-recursive merge-subtree merge-tree mktag mktree multi-pack-index mv name-rev
notes pack-objects pack-redundant pack-refs patch-id pickaxe prune prune-packed pull push range-diff read-tree rebase
receive-pack reflog refs remote repack replace replay repo rerere reset restore rev-list rev-parse revert rm
send-pack shortlog show show-branch show-index show-ref sparse-checkout stage stash status stripspace switch
symbolic-ref tag unpack-file unpack-objects update-index update-ref update-server-info upload-archive upload-pack var
verify-commit verify-pack verify-tag version whatchanged worktree write-tree filter-branch filter-repo
""".split())
ALIAS_DEPTH = 10


def is_opt(a: str, name: str) -> bool:
    """긴 옵션 — git 은 모호하지 않은 접두어도 받는다(`--har` = `--hard`). 모호한 접두어는 git 이 거부하므로
    접두어면 그 옵션으로 본다."""
    k = a.split("=", 1)[0]
    return k == name or (k.startswith("--") and len(k) >= 3 and name.startswith(k))


def opt(rest: list, name: str) -> bool:
    for a in rest:
        if a == "--":
            return False
        if is_opt(a, name):
            return True
    return False


def has_short(args: list, ch: str) -> bool:
    """짧은 옵션 묶음까지 본다 — `-Df` 는 `-D` 와 `-f` 다."""
    return any(a.startswith("-") and not a.startswith("--") and len(a) > 1 and ch in a[1:] for a in args)


def split(args: list):
    """(전역 옵션, 하위 명령, 나머지, -c 로 준 별칭 {이름: 값}). 하위 명령이 없으면 sub 는 None."""
    aliases, i = {}, 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in shellparse.GIT_VALUED and i + 1 < len(args):
            if a == "-c":
                k, _, v = args[i + 1].partition("=")
                if k.lower().startswith("alias."):
                    aliases[k[6:].lower()] = v
            i += 2
            continue
        if a.startswith("-c") and len(a) > 2 and "=" in a:      # `-ckey=value`
            k, _, v = a[2:].partition("=")
            if k.lower().startswith("alias."):
                aliases[k[6:].lower()] = v
        i += 1
    if i >= len(args):
        return args[:i], None, [], aliases
    return args[:i], args[i], args[i + 1:], aliases


def inline_config(glob_opts: list) -> dict:
    """`-c key=value`·`-ckey=value`·`--config-env key=ENV` 로 준 설정 {소문자 키: 값}."""
    out, i = {}, 0
    while i < len(glob_opts):
        a = glob_opts[i]
        if a in ("-c", "--config-env") and i + 1 < len(glob_opts):
            k, _, v = glob_opts[i + 1].partition("=")
            out[k.lower()] = v
            i += 2
            continue
        if a.startswith("--config-env="):
            k, _, v = a[len("--config-env="):].partition("=")
            out[k.lower()] = v
        elif a.startswith("-c") and len(a) > 2:
            k, _, v = a[2:].partition("=")
            out[k.lower()] = v
        i += 1
    return out


def hooks_path_override(glob_opts: list) -> bool:
    """`-c core.hooksPath=…` — pre-push 훅을 건너뛰는 길."""
    return any("hookspath" in a.lower() for a in glob_opts)


def expand(args: list, alias_lookup=None):
    """git 이 실제로 돌릴 인자 — 별칭을 git 과 같은 규칙으로 펼친다. (인자, 차단 사유|None).

    alias_lookup(name) -> str|None: 저장소·전역 설정의 별칭(래퍼만 넘긴다 — 정적 가드는 설정을 읽지 않는다).
    """
    for _ in range(ALIAS_DEPTH):
        glob_opts, sub, rest, aliases = split(args)
        if sub is None:
            return args, None
        if sub.lower() in BUILTIN:
            return glob_opts + [sub.lower()] + rest, None
        exp = aliases.get(sub.lower())
        if exp is None and alias_lookup:
            exp = alias_lookup(sub)
        if exp is None:
            return args, None
        exp = exp.strip()
        if exp.startswith("!"):
            # git 은 셸 별칭을 돌리기 전에 자기 exec-path 를 PATH 앞에 둔다 — 그 안의 git 은 이 가드를 지나지 않는다
            return args, f"git 셸 별칭({sub} = {exp[:60]}) 실행 금지 — 그 안의 git 은 실행 시점 가드를 지나지 않는다. 사용자 승인 필요."
        try:
            words = shlex.split(exp)                 # git 의 split_cmdline 처럼 따옴표를 벗긴다
        except ValueError:
            return args, f"git 별칭({sub}) 본문을 나눌 수 없다 — 판정 불가. 사용자 승인 필요."
        if not words:
            return args, None
        args = glob_opts + words + rest
    return args, "git 별칭 사슬이 너무 깊다 — 판정 불가. 사용자 승인 필요."


def check(args: list, alias_lookup=None, config_lookup=None):
    """파괴 조작이면 차단 사유, 아니면 None. commit 은 사유를 돌려준다(승인 판단은 호출한 쪽).

    config_lookup(key) -> str|None: 저장소·전역 설정값(래퍼만 넘긴다). 설정이 동작을 바꾸는 하위 명령에만 묻는다.
    """
    args, why = expand(args, alias_lookup)
    if why:
        return why
    glob_opts, sub, rest, _ = split(args)
    if sub is None:
        return None
    conf = inline_config(glob_opts)

    if sub == "config":
        # 위 규칙을 나중의 평범한 명령으로 우회하는 설정 쓰기 — 셸 별칭·push refspec/mirror·훅 경로·clean 강제 해제
        if rest[:1] in (["get"], ["list"]) or any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l")
                                                  for a in rest):
            return None
        for a in rest:
            k = a.lower()
            if (k.startswith("alias.") and any(r.lstrip().startswith("!") for r in rest[rest.index(a) + 1:][:1])) \
                    or re.match(r"remote\..+\.(push|mirror)$", k) or k.startswith("push.") \
                    or k in ("core.hookspath", "clean.requireforce"):
                return f"git config {a}(이후 git 명령의 파괴 동작을 바꾸는 설정) 금지. 사용자 승인 필요."
        return None
    if sub == "commit":
        return ("git commit 직접 실행 금지. `/commit` 스킬로 커밋 단위를 제안하고 "
                "사용자 승인을 받은 뒤 그 스킬이 실행한다.")
    if sub == "push":
        if opt(rest, "--no-verify") or hooks_path_override(glob_opts):
            return "git push --no-verify/core.hooksPath 변경(pre-push 훅 건너뛰기) 금지. 사용자 승인 필요."
        if any(k.startswith(("remote.", "push.")) for k in conf):
            # `-c remote.origin.push=+HEAD:main` 처럼 설정으로 준 refspec·mirror 도 강제 push 다
            return "git push 에 -c remote.*/push.* (설정으로 refspec·mirror 변경) 금지. 사용자 승인 필요."
        for a in rest:
            if a == "--":
                break
            if is_opt(a, "--force") or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]):
                return "git push --force/-f 금지(강제 push). 사용자 승인 필요. (`--force-with-lease` 허용)"
            if any(is_opt(a, o) for o in ("--delete", "--mirror", "--prune")) or (
                    a.startswith("-") and not a.startswith("--") and "d" in a[1:]):
                return f"git push {a}(원격 ref 삭제·덮어쓰기) 금지. 사용자 승인 필요."
        for a in rest:
            if a.startswith("+"):
                return "git push +refspec(강제) 금지. 사용자 승인 필요."
            if len(a) > 1 and a.startswith(":"):
                return "git push :branch(원격 브랜치 삭제) 금지. 사용자 승인 필요."
        if config_lookup:
            # 실제로 적용될 설정(저장소·전역·include·GIT_CONFIG_* 환경변수) — 강제 refspec·mirror 면 평범한 push 도 강제다
            for ln in (config_lookup(r"^remote\..*\.(push|mirror)$", True) or "").splitlines():
                k, _, v = ln.partition(" ")
                v = v.strip().lower()
                if (k.endswith(".mirror") and v in ("true", "yes", "on", "1", "")) or (k.endswith(".push") and v.startswith("+")):
                    return f"git push — 설정 {k}={v}(강제 refspec·mirror)이 적용된다. 사용자 승인 필요."
        return None
    if sub == "reset" and opt(rest, "--hard"):
        return "git reset --hard 금지(파괴적). 사용자 승인 필요."
    if sub == "checkout":
        creates = any(a in ("-b", "-B") or is_opt(a, "--orphan") for a in rest)
        positional = [a for a in rest if not a.startswith("-")]
        if not creates and ("--" in rest or rest[:1] == ["HEAD"] or "." in rest
                            or opt(rest, "--force") or has_short(rest, "f")
                            or len(positional) >= 2):  # `checkout <tree-ish> <path>`
            return "git checkout <ref> <path>/./--/-f(작업 폐기) 금지. 사용자 승인 필요."
    if sub == "switch" and (opt(rest, "--discard-changes") or opt(rest, "--force") or has_short(rest, "f")):
        return "git switch --discard-changes/-f(작업 폐기) 금지. 사용자 승인 필요."
    if sub == "stash" and rest[:1] in (["clear"], ["drop"]):
        return f"git stash {rest[0]}(스태시 영구 삭제) 금지. 사용자 승인 필요."
    if sub == "restore" and not (opt(rest, "--staged") and not opt(rest, "--worktree") and not has_short(rest, "W")):
        return "git restore(작업 트리 폐기) 금지. 사용자 승인 필요. (`--staged` 언스테이지는 허용)"
    if sub in ("filter-branch", "filter-repo"):
        return f"git {sub}(이력 재작성) 금지. 사용자 승인 필요."
    if sub == "reflog" and rest[:1] in (["expire"], ["delete"]):
        return "git reflog expire/delete(복구 경로 삭제) 금지. 사용자 승인 필요."
    if sub == "update-ref" and ("-d" in rest or opt(rest, "--delete")):
        return "git update-ref -d(ref 삭제) 금지. 사용자 승인 필요."
    if sub == "worktree" and rest[:1] == ["remove"] and (opt(rest, "--force") or has_short(rest, "f")):
        return "git worktree remove --force 금지. 사용자 승인 필요."
    if sub in ("revert", "cherry-pick") and not opt(rest, "--no-commit") and "-n" not in rest:
        return f"git {sub} 는 커밋을 만든다 — `/commit` 승인 절차를 거친다. (`--no-commit` 은 허용)"
    if sub == "clean":
        if opt(rest, "--force") or has_short(rest, "f"):
            return "git clean -f/--force(untracked 파괴) 금지. 사용자 승인 필요."
        dry = opt(rest, "--dry-run") or has_short(rest, "n") or opt(rest, "--interactive") or has_short(rest, "i")
        req = conf.get("clean.requireforce")
        if req is None and config_lookup:
            req = config_lookup("clean.requireForce")
        if not dry and req is not None and req.strip().lower() in ("false", "no", "off", "0", ""):
            return "git clean (clean.requireForce=false — -f 없이도 untracked 파괴) 금지. 사용자 승인 필요."
    if sub == "branch":
        if has_short(rest, "M") or has_short(rest, "C") or (
                (opt(rest, "--move") or opt(rest, "--copy") or has_short(rest, "m") or has_short(rest, "c"))
                and (opt(rest, "--force") or has_short(rest, "f"))):
            return "git branch -M/-C(기존 브랜치 덮어쓰기) 금지. 사용자 승인 필요."
        if has_short(rest, "D"):
            return "git branch -D(강제 삭제) 금지. 사용자 승인 필요."
        if (opt(rest, "--delete") or has_short(rest, "d")) and (opt(rest, "--force") or has_short(rest, "f")):
            return "git branch --delete --force 금지. 사용자 승인 필요."
    return None


def is_commit(args: list, alias_lookup=None) -> bool:
    """펼친 뒤 실제로 commit 인가 — 승인 표식은 commit 에만 쓰인다(별칭 이름으로 다른 명령을 승인시키지 못하게)."""
    args, why = expand(args, alias_lookup)
    return not why and split(args)[1] == "commit"


def verify_failed(root: str, git: str = "git"):
    """훅이 기록한 마지막 verifier 판정(subagent-audit → .claude/state/verdicts.jsonl)이 지금 HEAD 위의 FAIL 이면
    승인 커밋도 막는다. 기록은 에이전트가 쓸 수 없는 파일이라 대화 속 "검증 통과" 주장과 다르다.
    기록이 없거나 그 뒤 커밋이 있었으면(HEAD 가 다르면) 판단하지 않는다."""
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
        head = subprocess.run([git, "-C", root, "rev-parse", "-q", "--verify", "HEAD"], capture_output=True,
                              text=True, timeout=3, env=dict(os.environ, HARNESS_GIT_WRAPPED="1")).stdout.strip()
    except Exception:
        return None
    if head and last.get("head") == head:
        return ("verify FAIL 기록 위의 커밋 — 마지막 verifier 판정이 FAIL 이고 그 뒤 PASS 가 없다"
                "(.claude/state/verdicts.jsonl, 훅 기록). 사용자 승인 필요.")
    return None
