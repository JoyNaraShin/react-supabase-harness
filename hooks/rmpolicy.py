"""rm -r 정책 — 정적 가드(hooks/block-destructive-git.py)와 실행 시점 가드(bin/rm)가 함께 쓴다.

두 층은 보는 것이 다르다. 정적 가드는 명령 글자(`~`·`$X`·상대 경로)를, 실행 시점 가드는 bash 가 해석을 끝낸
실제 인자를 본다. 그런데 "무엇을 지키는가"는 같아야 한다 — 전에는 bin/rm 이 정적 가드 파일 전체를 불러와
일부만 쓰고 나머지(프로젝트·.git 보호)는 따로 구현해, 같은 대상을 두 층이 다르게 판정했다.

  dangerous_root(t)          글자 수준 판정(정적 층의 `~`·`$X`·`..` 표기 포함). 실제 경로에도 쓴다.
  why(path, home, project)   절대 경로 판정 → (이유 코드, 문구) 또는 None.
  runtime(target, cwd, ...)  실행 시점 판정 — 글자 경로와 링크를 푼 경로를 둘 다 본다.

보호하는 `.git` 은 **세션 프로젝트의 저장소**뿐이다 — `git rev-parse` 의 git dir·common dir 과 작업 트리
최상위의 `.git`(워크트리에선 파일). 다른 레포의 `.git` 은 막지 않는다(스냅샷으로도 되돌릴 수 없다 — README).
"""
import os
import re

SYSTEM_TOP = {"/", "/usr", "/etc", "/bin", "/sbin", "/lib", "/opt", "/var", "/System", "/Library",
              "/Applications", "/Users", "/home", "/root", "/private", "/Volumes", "/mnt", "/srv", "/tmp"}


def dangerous_root(t: str) -> bool:
    """A path whose recursive deletion is never a routine cleanup.

    Deep paths (`/tmp/build-x`, `/Users/me/proj/dist`, `~/.cache/tool/x`) are routine and
    pass. What stays blocked: system tops and their direct children, a home
    directory and its top-level folders (`~/Projects`), anything outside the project
    (`../x`), the cwd itself, and a bare variable that may expand to empty
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
    # `.`·`//` 도 정규화한다 — `/./x`·`~/./Projects`·`.//*` 가 깊이 판정을 속였다(문자 퍼즈가 찾음)
    if not t.startswith("$"):
        if t.startswith("~/"):
            t = "~/" + os.path.normpath(t[2:]) if os.path.normpath(t[2:]) != "." else "~"
        elif not t.startswith("~"):
            t = os.path.normpath(t) if t else t
    t = t.rstrip("/") or "/"
    if t in (".", "..", "*", "./*", "~", "~/*", "$HOME", "${HOME}",
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


# 이유 코드별 다음 행동 — 두 층의 차단 메시지가 같은 안내를 낸다
NEXT = {
    "system": "대상을 깊은 절대 경로로 다시 확인하라(예: /abs/path/dist).",
    "home": "홈 전체나 홈 바로 아래 폴더는 지우지 않는다 — 그 안의 구체적인 하위 경로를 지정하라.",
    "cwd": "현재 위치나 그 상위는 지우지 않는다 — 지울 하위 폴더를 이름으로 지정하라.",
    "project": "세션 프로젝트 폴더나 그 상위는 지우지 않는다 — 프로젝트 안의 구체적인 하위 경로를 지정하라.",
    "gitdir": "세션 프로젝트의 git 저장소(.git)는 지우지 않는다 — 이력이 함께 사라지고 스냅샷도 되돌리지 못한다.",
}
USER_RUN = "사용자가 의도한 삭제라면 사용자가 직접 `! /bin/rm -r …` 로 실행한다"

def _ident(path: str, follow: bool = True):
    """파일 신원 (st_dev, st_ino). macOS 는 대소문자를 무시하고 firmlink(/System/Volumes/Data/…)가 있어 같은 대상을
    여러 글자로 쓸 수 있다 — realpath 도 그것을 하나로 모으지 않으므로 신원으로 비교한다. 없으면 None."""
    try:
        st = os.stat(path) if follow else os.lstat(path)
        return st.st_dev, st.st_ino
    except (OSError, ValueError):
        return None


_CHAINS = {}


def _chain(anchor: str) -> set:
    """anchor 와 그 모든 상위 디렉터리의 신원 — p 의 신원이 여기 있으면 p 를 지울 때 anchor 가 사라진다.
    훅·래퍼는 호출마다 새 프로세스라 캐시가 낡지 않는다(글롭으로 대상이 많을 때 stat 을 반복하지 않게)."""
    if anchor in _CHAINS:
        return _CHAINS[anchor]
    _CHAINS[anchor] = out = set()
    d = os.path.realpath(anchor)
    while True:
        i = _ident(d)
        if i:
            out.add(i)
        parent = os.path.dirname(d)
        if parent == d:
            return out
        d = parent


# 신원으로 다시 재는 시스템 최상위 — macOS 데이터 볼륨 루트(/System/Volumes/Data)는 / 와 다른 볼륨이라
# 글자로도 신원으로도 / 와 같지 않다. 그 직계(/System/Volumes/Data/opt)는 /opt 와 같은 대상이다.
_DATA_ROOT = "/System/Volumes/Data"
_SYS = []


def _system_idents():
    """(최상위 자신, 직계까지 막는 부모) 신원 집합. /tmp 의 직계는 일상 정리라 부모 집합에서 뺀다."""
    if not _SYS:
        tops = {_ident(t) for t in SYSTEM_TOP | {_DATA_ROOT}} - {None}
        parents = (tops | {_ident("/")}) - {_ident("/tmp"), None}
        _SYS.extend([tops, parents])
    return _SYS


_GIT_DIRS = {}


def git_dirs(project: str) -> set:
    """세션 프로젝트 저장소의 보호 대상 경로. git 을 부르지 않고 파일시스템에서 찾는다 — git 이 없거나 느리거나
    환경변수(GIT_DIR)가 다른 레포를 가리켜도 같은 답이 나와야 한다(git 호출이 실패하면 보호가 열렸다, 0.39.0 검증).
    프로젝트에서 위로 올라가며 처음 만나는 `.git` 과, 그것이 파일(워크트리·separate-git-dir)이면 가리키는 git dir 과
    그 commondir. git 저장소가 아니면 빈 집합."""
    if not project:
        return set()
    if project in _GIT_DIRS:
        return _GIT_DIRS[project]
    out, d = set(), os.path.realpath(project)
    while True:
        g = os.path.join(d, ".git")
        if os.path.lexists(g):
            out.add(g)
            if os.path.isfile(g):
                try:
                    with open(g, encoding="utf-8", errors="replace") as fh:
                        line = fh.readline().strip()
                except OSError:
                    line = ""
                if line.startswith("gitdir:"):
                    gd = os.path.normpath(os.path.join(d, line[7:].strip()))
                    out.add(gd)
                    try:
                        with open(os.path.join(gd, "commondir"), encoding="utf-8", errors="replace") as fh:
                            out.add(os.path.normpath(os.path.join(gd, fh.readline().strip())))
                    except OSError:
                        pass
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    _GIT_DIRS[project] = out
    return out


def _under(p: str, anchor: str) -> bool:
    """p 를 지우면 anchor 가 사라지는가(같거나 상위) — 글자 비교."""
    return bool(anchor) and (p == anchor or anchor.startswith(p.rstrip("/") + "/"))


def why(path: str, home: str = "", project: str = "", cwd: str = ""):
    """절대 경로 path 의 재귀 삭제가 위험하면 (이유 코드, 문구), 아니면 None. cwd 를 비우면 cwd 검사를 건너뛴다.
    글자 비교 뒤에 신원 비교를 한다 — 대소문자·firmlink·링크 상위로 쓴 같은 대상도 같은 판정을 받는다.
    대상 자신이 링크면 rm 은 링크만 지우므로 대상은 lstat 으로 잰다."""
    p = os.path.normpath(path) if path else path
    if not p:
        return None
    if p == "/" or dangerous_root(p) or (p.startswith(_DATA_ROOT + "/") and dangerous_root(p[len(_DATA_ROOT):])):
        return "system", f"{p} 는 시스템·홈 최상위 경로다"           # 데이터 볼륨 표기는 원래 경로로 되돌려 다시 잰다
    if home and (_under(p, home) or os.path.dirname(p) == home):
        return "home", f"{p} 는 홈, 홈의 상위, 또는 홈 바로 아래 폴더다"
    if cwd and _under(p, cwd):
        return "cwd", f"{p} 는 현재 작업 위치이거나 그 상위다"
    real = os.path.realpath(project) if project else ""
    if project and any(_under(p, x) for x in dict.fromkeys((project, real, unprivate(real)))):
        return "project", f"{p} 는 이 세션의 프로젝트 폴더이거나 그 상위다"
    me = _ident(p, follow=False)
    if not me:
        return None                                      # 없는 대상 — rm 이 지울 것이 없다
    parent = _ident(os.path.dirname(p))
    tops, parents = _system_idents()
    if me in tops or parent in parents:
        return "system", f"{p} 는 시스템·홈 최상위 경로다(다른 표기)"
    if home and (me in _chain(home) or parent == _ident(home)):
        return "home", f"{p} 는 홈, 홈의 상위, 또는 홈 바로 아래 폴더다(다른 표기)"
    if cwd and me in _chain(cwd):
        return "cwd", f"{p} 는 현재 작업 위치이거나 그 상위다(다른 표기)"
    if project:
        if me in _chain(project):
            return "project", f"{p} 는 이 세션의 프로젝트 폴더이거나 그 상위다(다른 표기)"
        gds = git_dirs(project)
        if gds:
            mine = _chain(p) if os.path.isdir(p) and not os.path.islink(p) else {me}
            for g in gds:
                gi = _ident(g, follow=False)
                # 같거나 상위(지우면 g 가 사라진다) · 안쪽(.git/objects — 저장소가 망가진다)
                if me in _chain(g) or (gi and gi in (mine | _chain(os.path.dirname(p)))):
                    return "gitdir", f"{p} 는 이 세션 프로젝트의 git 저장소이거나 그것을 품은 경로다"
    return None


# macOS 의 /tmp·/var·/etc 는 /private/… 로의 링크다 — 실제 경로를 그대로 재면 `/tmp/x` 정리까지 막힌다
_PRIVATE = ("/private/tmp", "/private/var", "/private/etc")


def unprivate(p: str) -> str:
    for pre in _PRIVATE:
        if p == pre or p.startswith(pre + "/"):
            return p[len("/private"):]
    return p


def runtime(target: str, cwd: str, home: str, project: str = ""):
    """실행 시점 판정 — rm 이 실제로 받은 인자. 위험하면 (이유 코드, 문구), 아니면 None."""
    if not target:
        return None
    base = os.path.basename(target.rstrip("/"))
    if target.rstrip("/") and base in (".", ".."):
        return None                                      # POSIX rm 은 마지막 요소가 `.`·`..` 인 대상을 거부한다
    lexical = os.path.normpath(os.path.join(cwd, target))
    # 대상 자신이 링크면 rm 은 링크만 지운다 — 링크를 푸는 건 부모 경로까지(끝의 `/` 는 링크를 따라간다)
    if target.endswith("/"):
        resolved = os.path.realpath(lexical)
    else:
        resolved = os.path.join(os.path.realpath(os.path.dirname(lexical)), os.path.basename(lexical))
    real_cwd, real_home = os.path.realpath(cwd), os.path.realpath(home) if home else ""
    project = project or cwd                             # 세션 프로젝트를 모르면 지금 위치의 저장소라도 지킨다
    for p, c, h in ((lexical, cwd, home), (unprivate(resolved), unprivate(real_cwd), unprivate(real_home))):
        r = why(p, h, project, c)
        if r:
            return r
    return None
