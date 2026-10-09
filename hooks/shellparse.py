"""셸 명령에서 '실제로 쓰이는 경로'를 뽑는 공용 파서 — gate-engine(자기보호)과 block-impl-delegation(위임)이 함께 쓴다.

왜 공용인가: 두 훅이 각자 정규식으로 명령을 읽다 보니 같은 오판을 따로 냈다(4차 적대 리뷰).
  - 읽기 결과를 담은 heredoc 본문·따옴표 안의 `->` 를 명령·리다이렉트로 읽어 정상 작업을 막았다.
  - `cd src && cat > a.ts`, 디렉터리 목적지 `cp x src/`, `xargs sed -i` 처럼 흔한 쓰기 형태를 놓쳤다.

원칙: 데이터(heredoc 본문, 따옴표 안 문자열)는 명령이 아니다. 단 셸·인터프리터로 흘러가는
heredoc 과 `bash -c '…'` 의 인자는 실행되므로 명령으로 본다. 협조형 가드 — 난독화는 범위 밖.
"""
from __future__ import annotations
import os
import re

SHELLS = r"(?:bash|sh|zsh|dash|ash|eval)"
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_FEEDS_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash|python3?|node|ruby|perl)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")
LAUNCHERS = {"sudo", "doas", "env", "nohup", "time", "command", "builtin", "exec", "nice", "xargs", "stdbuf"}
DEST_LAST = {"cp", "install", "rsync", "ln"}
ALL_ARGS = {"touch", "truncate", "tee", "rm", "unlink", "mkdir", "sponge", "mv", "rmdir", "shred"}
IN_PLACE = {"sed", "perl", "awk", "gawk"}
REDIRECT = re.compile(r"(?<![0-9&<>-])>>?\|?\s*([^\s;&|<>()]+)|(?<![<>])[0-9]>>?\s*([^\s;&|<>()&]+)")


_FEEDS_POSIX_SHELL = re.compile(r"(?:^|[\s|(])(?:\S*/)?(?:bash|sh|zsh|dash|ash)\b[^|;&]*<<|\|\s*(?:\S*/)?(?:bash|sh|zsh)\b")


def strip_heredoc_bodies(cmd: str, shell_only: bool = False) -> str:
    """데이터 heredoc 본문을 지운다(헤더 줄은 남겨 `cat > f <<EOF` 의 리다이렉트는 그대로 본다).
    shell_only=True 면 셸이 읽는 본문만 남긴다 — python·node 본문은 실행되지만 셸 명령은 아니다."""
    lines, out, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC.search(line)
        i += 1
        if not m:
            continue
        tag, body = m.group(2), []
        while i < len(lines) and lines[i].strip() != tag:
            body.append(lines[i])
            i += 1
        if (_FEEDS_POSIX_SHELL if shell_only else _FEEDS_SHELL).search(line):
            out += body                                    # 셸·인터프리터가 읽는 본문은 실행된다
        if i < len(lines):
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def neutralize_quotes(cmd: str) -> str:
    """따옴표 안의 셸 메타문자를 공백으로 — 문자열 속 `a -> b`·`x | y` 는 리다이렉트·파이프가 아니다.
    `bash -c '…'`·`eval '…'` 의 인자는 실행되므로 그대로 펼친다."""
    out, i, n = [], 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            out.append(cmd[i + 1])
            i += 2
            continue
        if ch in "'\"":
            j = cmd.find(ch, i + 1)
            if j < 0:
                j = n
            inner = cmd[i + 1:j]
            before = "".join(out)
            if re.search(SHELLS + r"(?:\s+-\w+)*\s*$", before):
                out.append(inner)
            else:
                out.append(re.sub(r"[<>|;&]", " ", inner))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def prep(cmd: str, shell_only: bool = False) -> str:
    text = neutralize_quotes(strip_heredoc_bodies(cmd, shell_only))
    text = re.sub(r"(^|\s)#[^\n]*", r"\1", text)          # 주석은 실행되지 않는다
    return text


_VAR_REF = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
# 셸의 현재 위치를 뜻하는 변수 — 훅 프로세스의 값은 Bash 의 위치가 아니다(`rm -rf $PWD` 를 깊은 경로로 오판).
_SHELL_STATE = {"PWD", "OLDPWD"}
_ASSIGN = re.compile(r"(?:export\s+)?([A-Za-z_]\w*)=(\S*)")


def expand_vars(seg: str, known: dict) -> str:
    """`$NAME`·`${NAME}` 을 실제 셸처럼 펼친다 — 같은 명령 안의 앞선 대입이 먼저, 그다음 환경변수.
    Claude Code 의 Bash 는 호출마다 셸 상태가 새로 시작되므로 이 둘이 값의 전부다.
    모르는 변수는 그대로 둔다(값을 모르는 채 빈 문자열로 추정하면 쓰기 대상을 놓칠 수 있다)."""
    def sub(m):
        name = m.group(1) or m.group(2)
        val = known.get(name, None if name in _SHELL_STATE else os.environ.get(name))
        return m.group(0) if val is None else val
    return _VAR_REF.sub(sub, seg)


def record_assignment(seg: str, known: dict) -> bool:
    """`S=/path` · `export S=/path` 만으로 된 하위 명령이면 값을 기억하고 True.
    값에 명령 치환(`$(…)`·백틱)이 있으면 결과를 알 수 없으니 기억하지 않는다(모르는 변수로 남는다).
    `S=x cmd` 처럼 명령 앞에 붙은 대입은 그 명령의 환경일 뿐 셸 변수가 아니다."""
    m = _ASSIGN.fullmatch(seg.strip())
    if not m:
        return False
    if "$(" not in m.group(2) and "`" not in m.group(2):
        known[m.group(1)] = expand_vars(m.group(2), known)
    else:
        known.pop(m.group(1), None)
    return True


def segments(cmd: str, cwd: str, shell_only: bool = False) -> list:
    """(하위 명령, 그 시점의 cwd) 목록. `cd X` 는 뒤 하위 명령의 상대 경로 기준을 바꾼다.
    변수는 펼친 뒤에 돌려준다 — `S=/tmp/x && cp a $S/b` 의 대상은 `$S/b`(프로젝트 안 상대 경로)가 아니다."""
    out, here, known = [], cwd, {}
    for seg in re.split(r"[;&|\n]+", prep(cmd, shell_only).replace("(", " ").replace(")", " ")):
        seg = seg.strip()
        if not seg or record_assignment(seg, known):
            continue
        seg = expand_vars(seg, known)
        m = re.match(r"(?:builtin\s+)?cd\s+(\S+)\s*$", seg)
        if m:
            d = os.path.expanduser(m.group(1))
            here = os.path.normpath(d if os.path.isabs(d) else os.path.join(here, d))
            continue
        out.append((seg, here))
    return out


def _tokens(seg: str) -> list:
    toks = [t for t in re.split(r"\s+", REDIRECT.sub(" ", seg).strip()) if t]
    while toks:
        head = toks[0].rsplit("/", 1)[-1]
        if re.match(r"^[A-Za-z_]\w*=", toks[0]) or head in LAUNCHERS or toks[0].startswith("-"):
            toks = toks[1:]
        elif head == "timeout" and len(toks) > 1:
            toks = toks[2:]
        else:
            break
    return toks


def write_targets(seg: str, whole: str = "") -> list:
    """이 하위 명령이 쓰는 경로. 대상이 파이프·find 결과라 드러나지 않으면 명령 전체의 경로 인자를 대신 돌려준다."""
    out = [a or b for a, b in REDIRECT.findall(seg) if (a or b) not in ("/dev/null", "&1", "&2")]
    toks = _tokens(seg)
    if not toks:
        return out
    head = toks[0].rsplit("/", 1)[-1]
    args = [t for t in toks[1:] if not t.startswith("-")]
    if head in DEST_LAST and args:
        out.append(args[-1])
    elif head in ALL_ARGS:
        out += args
    elif head == "dd":
        out += [t[3:] for t in toks if t.startswith("of=")]
    # 줄 어디에 있든 in-place 편집기(find -exec, xargs 뒤 포함)
    for k, t in enumerate(toks):
        name = t.rsplit("/", 1)[-1]
        if name in IN_PLACE and any(x.startswith("-") and "i" in x.lstrip("-")[:3] for x in toks[k + 1:k + 4]):
            rest = [x for x in toks[k + 1:] if not x.startswith("-") and x not in ("{}", "+", "\\;", ";")]
            files = rest[1:] if name in ("sed", "awk", "gawk") or "-e" in toks[k + 1:k + 4] else rest
            if files:
                out += files
            else:                                          # xargs/find 가 대상을 공급 — 명령 전체의 경로를 본다
                out += [x for x in re.split(r"[\s;&|]+", prep(whole or seg)) if x and not x.startswith("-")
                        and ("/" in x or "." in x or x.isalpha())]
            break
    return out
