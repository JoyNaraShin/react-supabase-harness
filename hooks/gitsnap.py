"""작업 트리 스냅샷 — snapshot-guard(파괴 명령 직전)와 subagent-diff(서브에이전트 전후 비교)가 함께 쓴다.

왜 스냅샷인가: 파괴 명령을 문자열로 판정하는 파서는 표기를 바꾸면 뚫린다(인용·변수·eval). Claude Code 의
체크포인트는 Bash 로 바꾼 파일을 추적하지 않는다(공식 checkpointing 문서). 그래서 명령을 해석하는 대신
"실행 직전의 작업 트리"를 git 객체로 남긴다 — 어떻게 지웠든 복구 경로는 같다.

방식: 실제 인덱스를 임시 파일로 복사(stat 캐시를 살려 바뀐 파일만 해시)한 뒤 `git add -A` → `write-tree`.
미추적 파일은 포함하고 gitignore 대상(node_modules·.env 등)은 빠진다. HEAD·실제 인덱스·작업 트리는
건드리지 않는다.

프라이버시: 미추적 파일 중 비밀 파일 이름(SECRET_NAMES — 이름 전체 일치, 부분 일치는 코드 파일을 삼킨다)과
BIG_BYTES 를 넘는 파일은 스냅샷에 넣지 않는다. 제외한 경로는 호출자에게 돌려줘 사후 알림에 쓴다 — 제외 대상은
복구되지 않는다. 이미 추적 중인 파일은 빼지 않는다(이미 이력에 있고, 빼면 스냅샷이 삭제로 보인다). 결과는 `refs/harness/snapshots/<epoch_ms>` (브랜치가 아니라 일반 push 에 실리지 않는다).
모든 실패는 None — 훅은 fail-open 이다.
"""
from __future__ import annotations
import fnmatch
import os
import shutil
import subprocess
import time

REF_PREFIX = "refs/harness/snapshots/"
KEEP = 30           # 보존할 스냅샷 수 — 넘치면 오래된 것부터 ref 를 지운다(객체는 git gc 가 회수)
TIMEOUT = 8         # 초. 거대한 미추적 파일 등으로 느려지면 스냅샷을 포기한다(작업을 세우지 않는다)
BIG_BYTES = 5 * 1024 * 1024
SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                "credentials.json", "service-account*.json")
SECRET_OK = (".env.example", ".env.sample", ".env.template")


def _git(repo: str, *args, env=None, timeout=TIMEOUT):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           env=env, timeout=timeout)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def repo_root(cwd: str):
    return _git(cwd, "rev-parse", "--show-toplevel")


def is_secret_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name not in SECRET_OK and any(fnmatch.fnmatchcase(name, g) for g in SECRET_NAMES)


def excluded_untracked(repo: str) -> list:
    """스냅샷에서 뺄 미추적 파일 [(경로, 바이트)] — 비밀 파일 이름이거나 BIG_BYTES 초과."""
    out = _git(repo, "ls-files", "-o", "--exclude-standard", "-z")
    if out is None:
        return []
    hits = []
    for p in out.split("\0"):
        if not p:
            continue
        try:
            size = os.lstat(os.path.join(repo, p)).st_size
        except OSError:
            continue
        if is_secret_name(p) or size > BIG_BYTES:
            hits.append((p, size))
    return hits


def worktree_tree(repo: str, excluded=None):
    """지금 작업 트리(미추적 포함, ignore 제외, 비밀·대용량 미추적 제외)의 tree 객체 sha.
    excluded 에 리스트를 넘기면 뺀 [(경로, 바이트)] 를 채운다."""
    gd = _git(repo, "rev-parse", "--absolute-git-dir")
    if not gd:
        return None
    tmp = os.path.join(gd, f"harness-snap-index-{os.getpid()}")
    try:
        real = os.path.join(gd, "index")
        if os.path.isfile(real):
            shutil.copyfile(real, tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        env.pop("GIT_DIR", None)
        if not os.path.isfile(tmp) and _git(repo, "rev-parse", "-q", "--verify", "HEAD"):
            _git(repo, "read-tree", "HEAD", env=env)
        skip = excluded_untracked(repo)
        if excluded is not None:
            excluded.extend(skip)
        spec = ["--", "."] + [f":(exclude,literal){p}" for p, _ in skip] if skip else []
        if _git(repo, "add", "-A", *spec, env=env) is None:
            return None
        return _git(repo, "write-tree", env=env)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def latest(repo: str):
    """가장 최근 스냅샷 (commit sha, tree sha) 또는 None."""
    out = _git(repo, "for-each-ref", "--sort=-refname", "--count=1", "--format=%(objectname)", REF_PREFIX)
    if not out:
        return None
    return out, _git(repo, "rev-parse", out + "^{tree}")


def snapshot(repo: str, message: str = "harness snapshot", excluded=None):
    """스냅샷 commit sha. 트리가 직전 스냅샷과 같으면 새로 만들지 않고 직전 것을 돌려준다.
    excluded 는 worktree_tree 와 같다(제외한 미추적 파일)."""
    tree = worktree_tree(repo, excluded)
    if not tree:
        return None
    prev = latest(repo)
    if prev and prev[1] == tree:
        return prev[0]
    head = _git(repo, "rev-parse", "-q", "--verify", "HEAD")
    # 스냅샷은 사용자 커밋이 아니다 — git 사용자 설정(user.name/email)이 없는 환경(CI·새 머신)에서도
    # commit-tree 가 실패하지 않도록 고정 신원을 쓴다(실측: GitHub Actions 에서 스냅샷 전부 누락).
    ident = {f"GIT_{r}_{k}": v for r in ("AUTHOR", "COMMITTER")
             for k, v in (("NAME", "harness snapshot"), ("EMAIL", "harness-snapshot@localhost"))}
    commit = _git(repo, "commit-tree", tree, "-m", message, *(["-p", head] if head else []),
                  env=dict(os.environ, **ident))
    if not commit:
        return None
    _git(repo, "update-ref", f"{REF_PREFIX}{int(time.time() * 1000)}", commit)
    _prune(repo)
    return commit


def _prune(repo: str) -> None:
    refs = (_git(repo, "for-each-ref", "--sort=-refname", "--format=%(refname)", REF_PREFIX) or "").split()
    for ref in refs[KEEP:]:
        _git(repo, "update-ref", "-d", ref)


def changed_paths(repo: str, tree_a: str, tree_b: str, only: str = "") -> list:
    """두 트리 사이에 바뀐 경로. only="D" 면 tree_a 에 있고 tree_b 에 없는(사라진) 경로만."""
    args = ["diff-tree", "-r", "--name-only", "--no-renames"] + ([f"--diff-filter={only}"] if only else [])
    out = _git(repo, *args, tree_a, tree_b)
    return [p for p in (out or "").splitlines() if p]
